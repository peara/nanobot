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
from nanobot.core_scratchpad import handle_scratchpad_tool, scratchpad_command, scratchpad_system_message
from nanobot.core_utils import (
    SCHEDULED_SYSTEM_MARKER,
    SCRATCHPAD_TOOL_NAME,
    attach_human_timestamps,
    clip,
    command_name,
    extract_playwright_field,
    help_text,
    human_now,
    scoped_chat_id,
    scratchpad_tool_spec,
    tool_result_preview,
    trim_history_by_chars,
    unscoped_chat_id,
)
from nanobot.llm import LlmClient
from nanobot.mcp_hub import McpHub
from nanobot.memory import ConversationStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore

logger = logging.getLogger(__name__)


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
            self.contexts.put("chat", scope, "scratchpad", {"text": ""})
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
        scratchpad_message = self._scratchpad_system_message(scope)
        if scratchpad_message is not None:
            messages.append(scratchpad_message)
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
        if persist_assistant:
            self.memory.add_message(scope, "assistant", reply)
        self.contexts.put("chat", scope, "last_assistant_message", {"text": reply})
        await self._send(scope, reply)

    async def _run_agent_loop(
        self,
        scope_for_tools: str,
        messages: list[dict],
        tools: list[dict],
    ) -> tuple[str, list[dict[str, Any]]]:
        assistant_message = await self.llm.chat(messages=messages, tools=tools)
        tool_trace: list[dict[str, Any]] = []
        while assistant_message.get("tool_calls"):
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_message.get("content") or "",
                    "tool_calls": assistant_message["tool_calls"],
                }
            )
            for tool_call in assistant_message["tool_calls"]:
                fn_name = tool_call["function"]["name"]
                raw_args = tool_call["function"].get("arguments") or "{}"
                args = json.loads(raw_args)
                if fn_name == SCRATCHPAD_TOOL_NAME:
                    result = self._handle_scratchpad_tool(scope_for_tools, args)
                else:
                    if fn_name.endswith("__schedule_task"):
                        chat_id = str(args.get("chat_id", "")).strip()
                        # LLMs often pass placeholders like "current_chat"; map to the real scoped chat id.
                        if not chat_id or chat_id in {"current_chat", "this_chat", "current", "here"}:
                            args["chat_id"] = scope_for_tools
                    try:
                        logger.info("Calling tool=%s args=%s", fn_name, args)
                        result = await self.mcp.call_tool(fn_name, args)
                        logger.info("Tool succeeded tool=%s", fn_name)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Tool failed tool=%s", fn_name)
                        result = f"Tool call failed: {exc}"
                logger.info(
                    "Tool result tool=%s chars=%d preview=%s",
                    fn_name,
                    len(result),
                    tool_result_preview(result),
                )
                if fn_name.startswith("playwright__"):
                    self._record_browse_event(scope_for_tools, fn_name, args, result)
                tool_trace.append(
                    {
                        "name": fn_name,
                        "args": args,
                        "result_preview": tool_result_preview(result, limit=300),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": fn_name,
                        "content": result,
                    }
                )
            assistant_message = await self.llm.chat(messages=messages, tools=tools)
        reply = assistant_message.get("content") or "I could not generate a response."
        return reply, tool_trace

    def _list_openai_tools(self) -> list[dict]:
        return [*self.mcp.list_openai_tools(), scratchpad_tool_spec()]

    async def _scratchpad_command(self, scope: str, raw_text: str) -> None:
        await scratchpad_command(self, scope, raw_text)

    def _record_browse_event(self, scope: str, tool_name: str, args: dict[str, Any], result: str) -> None:
        page_url = extract_playwright_field(result, "Page URL")
        page_title = extract_playwright_field(result, "Page Title")
        blocked = False
        if page_title and "pardon our interruption" in page_title.lower():
            blocked = True
        if page_url and "/splashui/challenge" in page_url:
            blocked = True

        existing = self.contexts.get("chat", scope, "browse_history")
        events: list[dict[str, Any]]
        if isinstance(existing, dict):
            payload_events = existing.get("events")
            events = payload_events if isinstance(payload_events, list) else []
        elif isinstance(existing, list):
            events = existing
        else:
            events = []

        events.append(
            {
                "at": human_now(),
                "tool": tool_name,
                "args": args,
                "page_url": page_url or "",
                "page_title": page_title or "",
                "blocked": blocked,
                "result_preview": tool_result_preview(result, limit=400),
            }
        )
        events = events[-40:]
        self.contexts.put("chat", scope, "browse_history", {"events": events})

    def _scratchpad_system_message(self, scope: str) -> dict[str, str] | None:
        return scratchpad_system_message(self, scope)

    def _handle_scratchpad_tool(self, scope: str, args: dict[str, Any]) -> str:
        return handle_scratchpad_tool(self, scope, args)

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
