from __future__ import annotations

import json
import logging
from typing import Any

from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig
from nanobot.context_store import ContextStore
from nanobot.llm import LlmClient
from nanobot.mcp_hub import McpHub
from nanobot.memory import ConversationStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore

logger = logging.getLogger(__name__)
SCHEDULED_SYSTEM_MARKER = (
    "This is an automated scheduler trigger, not a user message. Do not assume a human is currently chatting."
)


def scoped_chat_id(channel: str, chat_id: str) -> str:
    return f"{channel}:{chat_id}"


def unscoped_chat_id(scoped: str) -> tuple[str, str]:
    channel, _, chat = scoped.partition(":")
    return channel, chat


def _trim_history_by_chars(messages: list[dict], char_limit: int) -> list[dict]:
    if char_limit <= 0:
        return messages
    kept_reversed: list[dict] = []
    total = 0
    for msg in reversed(messages):
        content = str(msg.get("content", ""))
        msg_len = len(content)
        if kept_reversed and total + msg_len > char_limit:
            break
        kept_reversed.append(msg)
        total += msg_len
    kept_reversed.reverse()
    return kept_reversed


def _tool_result_preview(text: str, limit: int = 1200) -> str:
    compact = text.replace("\n", "\\n")
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}...(truncated)"


def _clip(text: str, limit: int = 100) -> str:
    stripped = text.strip().replace("\n", " ")
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit]}..."


def _clip_long(text: str, limit: int = 3500) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...(truncated)"


def _help_text() -> str:
    return "\n".join(
        [
            "Available commands",
            "/help - show this help",
            "/ctx - compact context diagnostics for this chat",
            "/ctxfull - full pre-LLM payload JSON (truncated)",
            "/reset - clear local conversation history for this chat scope",
        ]
    )


def _command_name(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split()[0]
    token = token.split("@", 1)[0]
    return token.lower()


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
        await self.mcp.start()
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.mcp.stop()

    async def on_incoming(self, message: IncomingMessage) -> None:
        scope = scoped_chat_id(message.channel, message.chat_id)
        cmd = _command_name(message.text)
        if cmd in {"/help", "/commands", "/start"}:
            await self._send(scope, _help_text())
            return
        if cmd == "/ctx":
            await self._send(scope, self._build_context_report(scope))
            return
        if cmd == "/ctxfull":
            await self._send(scope, self._build_full_context_report(scope))
            return
        if cmd == "/reset":
            deleted = self.memory.clear_chat(scope)
            await self._send(
                scope,
                f"Context reset complete.\nscope: {scope}\ndeleted_messages: {deleted}",
            )
            return
        await self._process(scope, message.text)

    async def _handle_scheduled_task(self, scoped_id: str, prompt: str) -> None:
        await self._process_scheduled(scoped_id, prompt)

    async def _process(self, scope: str, user_text: str) -> None:
        logger.info("Processing message for scope=%s", scope)
        self.memory.add_message(scope, "user", user_text)
        self.contexts.put("chat", scope, "last_user_message", {"text": user_text})
        history = self.memory.get_recent_messages(scope, limit=self.config.history_message_limit)
        history = _trim_history_by_chars(history, self.config.history_char_limit)
        messages = [self._base_system_message(), *history]
        await self._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)

    async def _process_scheduled(self, scope: str, prompt: str) -> None:
        logger.info("Processing scheduled task for scope=%s prompt=%s", scope, _clip(prompt, limit=200))
        messages = [
            self._base_system_message(),
            {"role": "system", "content": SCHEDULED_SYSTEM_MARKER},
            {"role": "user", "content": prompt},
        ]
        await self._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)

    def _base_system_message(self) -> dict[str, str]:
        return {
            "role": "system",
            "content": self.config.system_prompt_template.format(assistant_name=self.config.assistant_name),
        }

    async def _run_agent_turn(self, scope: str, messages: list[dict], persist_assistant: bool) -> None:
        tools = self.mcp.list_openai_tools()

        assistant_message = await self.llm.chat(messages=messages, tools=tools)

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
                if fn_name.endswith("__schedule_task"):
                    chat_id = str(args.get("chat_id", "")).strip()
                    # LLMs often pass placeholders like "current_chat"; map to the real scoped chat id.
                    if not chat_id or chat_id in {"current_chat", "this_chat", "current", "here"}:
                        args["chat_id"] = scope
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
                    _tool_result_preview(result),
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
        if persist_assistant:
            self.memory.add_message(scope, "assistant", reply)
        self.contexts.put("chat", scope, "last_assistant_message", {"text": reply})
        await self._send(scope, reply)

    async def _send(self, scope: str, text: str) -> None:
        channel_name, raw_chat_id = unscoped_chat_id(scope)
        channel = self.channels.get(channel_name)
        if channel is None:
            logger.error("No channel configured for scope=%s channel=%s", scope, channel_name)
            raise KeyError(f"No channel configured for '{channel_name}'")
        logger.info("Sending message via channel=%s chat_id=%s", channel_name, raw_chat_id)
        await channel.send(raw_chat_id, text)

    def _build_context_report(self, scope: str) -> str:
        total = self.memory.count_messages(scope)
        recent = self.memory.get_recent_messages(scope, limit=self.config.history_message_limit)
        trimmed = _trim_history_by_chars(recent, self.config.history_char_limit)
        recent_chars = sum(len(str(m.get("content", ""))) for m in recent)
        trimmed_chars = sum(len(str(m.get("content", ""))) for m in trimmed)
        lines = [
            "Context report",
            f"scope: {scope}",
            f"total_messages_in_db: {total}",
            f"recent_window_limit: {self.config.history_message_limit}",
            f"char_limit: {self.config.history_char_limit}",
            f"messages_after_limit: {len(recent)} ({recent_chars} chars)",
            f"messages_after_trim: {len(trimmed)} ({trimmed_chars} chars)",
            "included_tail:",
        ]
        tail = trimmed[-8:]
        if not tail:
            lines.append("- (empty)")
        else:
            for msg in tail:
                role = str(msg.get("role", "unknown"))
                content = _clip(str(msg.get("content", "")))
                lines.append(f"- {role}: {content}")
        return "\n".join(lines)

    def _build_full_context_report(self, scope: str) -> str:
        history = self.memory.get_recent_messages(scope, limit=self.config.history_message_limit)
        trimmed = _trim_history_by_chars(history, self.config.history_char_limit)
        messages = [self._base_system_message(), *trimmed]
        payload = {
            "model": self.config.model.model,
            "temperature": self.config.model.temperature,
            "max_tokens": self.config.model.max_tokens,
            "tools_count": len(self.mcp.list_openai_tools()),
            "messages": messages,
        }
        body = json.dumps(payload, ensure_ascii=True, indent=2)
        return _clip_long(body)
