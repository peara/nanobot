from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig
from nanobot.context_store import ContextStore
from nanobot.core_plan import process_plan
from nanobot.core_reports import build_context_report, build_full_context_report
from nanobot.core_scratchpad import (
    SCRATCHPAD_TOOL_NAME,
    apply_scratchpad_tool_call,
    clear_scratchpad,
    scratchpad_assistant_message,
    scratchpad_command,
    scratchpad_tool_spec,
)
from nanobot.core_utils import (
    SCHEDULED_SYSTEM_MARKER,
    attach_human_timestamps,
    clip,
    command_name,
    help_text,
    human_now,
    scoped_chat_id,
    tool_result_preview,
    trim_history_by_chars,
    unscoped_chat_id,
)
from nanobot.hooks import ToolCallEvent, ToolHook, build_default_tool_hooks
from nanobot.llm import LlmClient
from nanobot.mcp_hub import McpHub
from nanobot.memory import ConversationStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore

logger = logging.getLogger(__name__)

SCRATCHPAD_PROTOCOL_CORRECTION = (
    "Protocol violation: after any external tool result, call session__scratchpad_write first "
    "(mode='append' or mode='finalize') before requesting another external tool."
)
MAX_SCRATCHPAD_PROTOCOL_RETRIES = 2
SCRATCHPAD_PROTOCOL_ABORT_REPLY = "I got stuck enforcing scratchpad updates in this turn. Please try again."
EMPTY_REPLY_FALLBACK = "I'm sorry, I hit an empty model response. Please try again."


class BotCore:
    def __init__(self, config: AppConfig, channels: dict[str, Any]) -> None:
        self.config = config
        self.channels = channels
        self.llm = LlmClient(config.model)
        self.memory = ConversationStore(config.database_path)
        self.contexts = ContextStore(config.database_path)
        for server in config.mcp_servers:
            if server.name == "scheduler":
                server.env = dict(server.env)
                server.env.setdefault("SCHEDULER_DB_PATH", config.scheduler_db_path)
        self.mcp = McpHub(config.mcp_servers)
        self.scheduler_store = SchedulerStore(config.scheduler_db_path)
        self.tool_hooks: list[ToolHook] = build_default_tool_hooks()
        self.scheduler = SchedulerRunner(
            store=self.scheduler_store,
            on_due_task=self._handle_scheduled_task,
            poll_interval_seconds=config.poll_interval_seconds,
        )

    async def start(self) -> None:
        logger.info(
            "Runtime paths cwd=%s database=%s scheduler_db=%s",
            os.getcwd(),
            str(Path(self.config.database_path).resolve()),
            str(Path(self.config.scheduler_db_path).resolve()),
        )
        await self.mcp.start()
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.mcp.stop()

    async def on_incoming(self, message: IncomingMessage) -> None:
        scope = scoped_chat_id(message.channel, message.chat_id)
        cmd = command_name(message.text)
        if cmd in {"/help", "/commands", "/start"}:
            await self._send(scope, help_text())
            return
        if cmd == "/ctx":
            await self._send(scope, self._build_context_report(scope))
            return
        if cmd == "/ctxfull":
            await self._send(scope, self._build_full_context_report(scope))
            return
        if cmd == "/reset":
            deleted = self.memory.clear_chat(scope)
            clear_scratchpad(self, scope)
            await self._send(
                scope,
                f"Context reset complete.\nscope: {scope}\ndeleted_messages: {deleted}",
            )
            return
        if cmd == "/plan":
            await self._process_plan(scope, message.text)
            return
        if cmd == "/scratchpad":
            await self._scratchpad_command(scope, message.text)
            return
        await self._process(scope, message.text)

    async def _handle_scheduled_task(self, scoped_id: str, prompt: str) -> None:
        await self._process_scheduled(scoped_id, prompt)

    async def _process(self, scope: str, user_text: str) -> None:
        logger.info("Processing message for scope=%s", scope)
        self.memory.add_message(scope, "user", user_text)
        self.contexts.put("chat", scope, "last_user_message", {"text": user_text})
        history = self.memory.get_recent_messages(scope, limit=self.config.history_message_limit)
        history = attach_human_timestamps(history)
        history = trim_history_by_chars(history, self.config.history_char_limit)
        messages = [self._base_system_message()]
        messages.extend(history)
        await self._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)

    async def _process_scheduled(self, scope: str, prompt: str) -> None:
        logger.info("Processing scheduled task for scope=%s prompt=%s", scope, clip(prompt, limit=200))
        messages = [
            self._base_system_message(),
            {"role": "system", "content": SCHEDULED_SYSTEM_MARKER},
            {"role": "user", "content": prompt},
        ]
        await self._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)

    async def _process_plan(self, chat_scope: str, raw_text: str) -> None:
        await process_plan(self, chat_scope, raw_text)

    def _base_system_message(self) -> dict[str, str]:
        return {
            "role": "system",
            "content": self.config.system_prompt_template.format(assistant_name=self.config.assistant_name),
        }

    async def _run_agent_turn(self, scope: str, messages: list[dict], persist_assistant: bool) -> None:
        reply, _ = await self._run_agent_loop(
            scope_for_tools=scope,
            messages=messages,
            tools=self._list_openai_tools(),
        )
        final_reply = str(reply or "")
        if not final_reply.strip():
            logger.warning(
                "Assistant produced empty/whitespace reply scope=%s chars=%d; using fallback",
                scope,
                len(final_reply),
            )
            final_reply = EMPTY_REPLY_FALLBACK
        if persist_assistant:
            self.memory.add_message(scope, "assistant", final_reply)
        self.contexts.put("chat", scope, "last_assistant_message", {"text": final_reply})
        await self._send(scope, final_reply)

    async def _run_agent_loop(
        self,
        scope_for_tools: str,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        scratchpad_msg = scratchpad_assistant_message(self, scope_for_tools)
        to_send = messages + ([scratchpad_msg] if scratchpad_msg else [])
        prepared_messages = self._prepare_messages_for_chat(to_send)
        assistant_message = await self.llm.chat(
            messages=prepared_messages,
            tools=tools,
            response_format=response_format,
        )
        tool_trace: list[dict[str, Any]] = []
        needs_scratchpad_update = False
        protocol_retry_count = 0
        while assistant_message.get("tool_calls"):
            requested_calls = assistant_message["tool_calls"]
            if needs_scratchpad_update:
                scratchpad_seen = False
                protocol_violation = False
                for tool_call in requested_calls:
                    name = str(tool_call.get("function", {}).get("name", ""))
                    if name == SCRATCHPAD_TOOL_NAME:
                        scratchpad_seen = True
                        continue
                    if not scratchpad_seen:
                        protocol_violation = True
                        break
                if protocol_violation:
                    protocol_retry_count += 1
                    proposed_tools = [str(call.get("function", {}).get("name", "")) for call in requested_calls]
                    logger.warning(
                        "Scratchpad protocol violation scope=%s retry=%d/%d proposed_tools=%s",
                        scope_for_tools,
                        protocol_retry_count,
                        MAX_SCRATCHPAD_PROTOCOL_RETRIES,
                        proposed_tools,
                    )
                    if protocol_retry_count > MAX_SCRATCHPAD_PROTOCOL_RETRIES:
                        logger.error(
                            "Scratchpad protocol retries exceeded scope=%s; aborting turn",
                            scope_for_tools,
                        )
                        return SCRATCHPAD_PROTOCOL_ABORT_REPLY, tool_trace
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                f"{SCRATCHPAD_PROTOCOL_CORRECTION} "
                                f"Retry {protocol_retry_count}/{MAX_SCRATCHPAD_PROTOCOL_RETRIES}."
                            ),
                        }
                    )
                    trimmed = self._trim_to_last_tool_round(messages)
                    scratchpad_msg = scratchpad_assistant_message(self, scope_for_tools)
                    to_send = trimmed + ([scratchpad_msg] if scratchpad_msg else [])
                    prepared_messages = self._prepare_messages_for_chat(to_send)
                    assistant_message = await self.llm.chat(
                        messages=prepared_messages,
                        tools=tools,
                        response_format=response_format,
                    )
                    continue
            protocol_retry_count = 0
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_message.get("content") or "",
                    "tool_calls": requested_calls,
                }
            )
            for tool_call in requested_calls:
                fn_name = tool_call["function"]["name"]
                raw_args = tool_call["function"].get("arguments") or "{}"
                args = json.loads(raw_args)
                if fn_name.endswith("__schedule_task"):
                    chat_id = str(args.get("chat_id", "")).strip()
                    # LLMs often pass placeholders like "current_chat"; map to the real scoped chat id.
                    if not chat_id or chat_id in {"current_chat", "this_chat", "current", "here"}:
                        args["chat_id"] = scope_for_tools
                ok = True
                error: str | None = None
                try:
                    logger.info("Calling tool=%s args=%s", fn_name, args)
                    if fn_name == SCRATCHPAD_TOOL_NAME:
                        scratchpad = apply_scratchpad_tool_call(self, scope_for_tools, args)
                        result = json.dumps({"ok": True, "scratchpad": scratchpad}, ensure_ascii=True)
                        needs_scratchpad_update = False
                    else:
                        result = await self.mcp.call_tool(fn_name, args)
                        needs_scratchpad_update = True
                    logger.info("Tool succeeded tool=%s", fn_name)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception("Tool failed tool=%s", fn_name)
                    ok = False
                    error = str(exc)
                    result = f"Tool call failed: {exc}"
                    if fn_name != SCRATCHPAD_TOOL_NAME:
                        needs_scratchpad_update = True
                result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=True)
                logger.info(
                    "Tool result tool=%s chars=%d preview=%s",
                    fn_name,
                    len(result_text),
                    tool_result_preview(result_text),
                )
                if fn_name != SCRATCHPAD_TOOL_NAME:
                    await self._dispatch_after_tool_call(
                        ToolCallEvent(
                            scope=scope_for_tools,
                            call_id=str(tool_call.get("id", "")),
                            tool_name=fn_name,
                            args=args,
                            result=result_text,
                            result_preview=tool_result_preview(result_text, limit=1200),
                            ok=ok,
                            error=error,
                            at=human_now(),
                        )
                    )
                tool_trace.append(
                    {
                        "name": fn_name,
                        "args": args,
                        "result_preview": tool_result_preview(result_text, limit=300),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": fn_name,
                        "content": result_text,
                    }
                )
            trimmed = self._trim_to_last_tool_round(messages)
            scratchpad_msg = scratchpad_assistant_message(self, scope_for_tools)
            to_send = trimmed + ([scratchpad_msg] if scratchpad_msg else [])
            prepared_messages = self._prepare_messages_for_chat(to_send)
            assistant_message = await self.llm.chat(
                messages=prepared_messages,
                tools=tools,
                response_format=response_format,
            )
        reply = assistant_message.get("content") or "I could not generate a response."
        return reply, tool_trace

    def _list_openai_tools(self) -> list[dict]:
        return [scratchpad_tool_spec(), *self.mcp.list_openai_tools()]

    async def _scratchpad_command(self, scope: str, raw_text: str) -> None:
        await scratchpad_command(self, scope, raw_text)

    def _scratchpad_assistant_message(self, scope: str) -> dict[str, str] | None:
        return scratchpad_assistant_message(self, scope)

    async def _dispatch_after_tool_call(self, event: ToolCallEvent) -> None:
        for hook in self.tool_hooks:
            try:
                await hook.after_tool_call(event, self)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "after_tool_call hook failed hook=%s tool=%s", hook.__class__.__name__, event.tool_name
                )

    @staticmethod
    def _trim_to_last_tool_round(messages: list[dict]) -> list[dict]:
        """Keep only the last round of tool use (assistant with tool_calls + its tool results)."""
        prefix_end = 0
        for idx, m in enumerate(messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                prefix_end = idx
                break
        last_assistant_idx: int | None = None
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "assistant" and messages[idx].get("tool_calls"):
                last_assistant_idx = idx
                break
        if last_assistant_idx is None:
            return list(messages)
        return [*messages[:prefix_end], *messages[last_assistant_idx:]]

    @staticmethod
    def _prepare_messages_for_chat(messages: list[dict]) -> list[dict]:
        system_contents: list[str] = []
        non_system: list[dict] = []
        for message in messages:
            role = str(message.get("role", ""))
            if role == "system":
                content = message.get("content")
                if content is None:
                    continue
                text = str(content).strip()
                if text:
                    system_contents.append(text)
                continue
            non_system.append(message)
        if not system_contents:
            return non_system
        merged_system = {"role": "system", "content": "\n\n".join(system_contents)}
        return [merged_system, *non_system]

    async def _send(self, scope: str, text: str) -> None:
        channel_name, raw_chat_id = unscoped_chat_id(scope)
        channel = self.channels.get(channel_name)
        if channel is None:
            logger.error("No channel configured for scope=%s channel=%s", scope, channel_name)
            raise KeyError(f"No channel configured for '{channel_name}'")
        logger.info("Sending message via channel=%s chat_id=%s", channel_name, raw_chat_id)
        await channel.send(raw_chat_id, text)

    def _build_context_report(self, scope: str) -> str:
        return build_context_report(self, scope)

    def _build_full_context_report(self, scope: str) -> str:
        return build_full_context_report(self, scope)
