from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
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
SCRATCHPAD_TOOL_NAME = "session__scratchpad_write"
SCRATCHPAD_MAX_CHARS = 6000


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


def _format_timestamp_for_prompt(raw_value: str) -> str | None:
    try:
        parsed = datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.strftime("%A, %d %B %Y, %I:%M %p")


def _attach_human_timestamps(messages: list[dict]) -> list[dict]:
    result: list[dict] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        created_at = message.get("created_at")
        if isinstance(created_at, str):
            formatted = _format_timestamp_for_prompt(created_at)
            if formatted:
                content = f"[{formatted}]\n{content}"
        result.append({"role": role, "content": content})
    return result


def _extract_playwright_field(result_text: str, field: str) -> str | None:
    prefix = f"- {field}: "
    for line in result_text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _human_now() -> str:
    return datetime.now().strftime("%A, %d %B %Y, %I:%M %p")


def _looks_garbled_text(text: str) -> bool:
    if not text:
        return False
    if len(text) < 80:
        return False
    q_count = text.count("?")
    if q_count < 20:
        return False
    ratio = q_count / max(1, len(text))
    return ratio >= 0.2


def _help_text() -> str:
    return "\n".join(
        [
            "Available commands",
            "/help - show this help",
            "/plan <request> - run inline planner flow in a new plan_run scope",
            "/ctx - compact context diagnostics for this chat",
            "/ctxfull - full pre-LLM payload JSON (truncated)",
            "/reset - clear local conversation history for this chat scope",
            "/scratchpad [show|set|append|clear] - inspect or force scratchpad updates",
        ]
    )


def _scratchpad_tool_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": SCRATCHPAD_TOOL_NAME,
            "description": (
                "Write private notes to a session scratchpad for this chat. "
                "Scratchpad content is hidden from the user and injected into later prompts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["append", "replace", "clear"],
                        "description": "How to update scratchpad text.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Scratchpad text to append or replace.",
                    },
                },
                "required": ["mode"],
            },
        },
    }


def _command_name(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split()[0]
    token = token.split("@", 1)[0]
    return token.lower()


def _command_body(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return stripped
    parts = stripped.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


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
        history = _attach_human_timestamps(history)
        history = _trim_history_by_chars(history, self.config.history_char_limit)
        messages = [self._base_system_message()]
        scratchpad_message = self._scratchpad_system_message(scope)
        if scratchpad_message is not None:
            messages.append(scratchpad_message)
        messages.extend(history)
        await self._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)

    async def _process_scheduled(self, scope: str, prompt: str) -> None:
        logger.info("Processing scheduled task for scope=%s prompt=%s", scope, _clip(prompt, limit=200))
        messages = [
            self._base_system_message(),
            {"role": "system", "content": SCHEDULED_SYSTEM_MARKER},
            {"role": "user", "content": prompt},
        ]
        await self._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)

    async def _process_plan(self, chat_scope: str, raw_text: str) -> None:
        request_text = _command_body(raw_text)
        if not request_text:
            await self._send(chat_scope, "Usage: /plan <request>")
            return

        run_id = f"run-{uuid.uuid4().hex[:10]}"
        logger.info("Starting plan run run_id=%s chat_scope=%s", run_id, chat_scope)
        self.memory.add_message(chat_scope, "user", raw_text)
        self.contexts.put("chat", chat_scope, "last_plan_run_id", {"run_id": run_id})
        self.contexts.put("plan_run", run_id, "chat_scope", {"value": chat_scope})
        self.contexts.put("plan_run", run_id, "request_text", {"text": request_text})
        self.contexts.put("plan_run", run_id, "status", {"value": "created"})

        # Pass 1: extract plan brief in chat-facing intake mode.
        intake_messages = [
            self._base_system_message(),
            {
                "role": "system",
                "content": (
                    "Extract a concise planning brief as strict JSON object only. "
                    "Include keys: goal (string), constraints (array of strings), "
                    "required_inputs (array of strings), risk_flags (array of strings), "
                    "notes (string)."
                ),
            },
            {"role": "user", "content": request_text},
        ]
        intake_reply, _ = await self._run_agent_loop(scope_for_tools=chat_scope, messages=intake_messages, tools=[])
        self.contexts.put("plan_run", run_id, "intake_raw", {"text": intake_reply})
        plan_brief = _extract_json_object(intake_reply) or {
            "goal": request_text,
            "constraints": [],
            "required_inputs": [],
            "risk_flags": [],
            "notes": "" if _looks_garbled_text(intake_reply) else intake_reply.strip(),
        }
        self.contexts.put("plan_run", run_id, "plan_brief", plan_brief)
        self.contexts.put("plan_run", run_id, "status", {"value": "planning"})

        # Pass 2: run execution mode using only plan-run context payload.
        run_payload = {
            "run_id": run_id,
            "request_text": request_text,
            "plan_brief": plan_brief,
        }
        run_messages = [
            {
                "role": "system",
                "content": (
                    "You are an execution agent operating in a dedicated plan_run scope. "
                    "Use only the provided run payload as context, execute the task, and provide "
                    "a practical final answer. If important inputs are missing, clearly ask for them."
                ),
            },
            {"role": "system", "content": json.dumps(run_payload, ensure_ascii=True)},
            {"role": "user", "content": "Execute this plan request and return the final result."},
        ]
        self.contexts.put("plan_run", run_id, "status", {"value": "running"})
        try:
            final_reply, tool_trace = await self._run_agent_loop(
                scope_for_tools=chat_scope,
                messages=run_messages,
                tools=self.mcp.list_openai_tools(),
            )
            self.contexts.put("plan_run", run_id, "execution_raw", {"text": final_reply})
            if _looks_garbled_text(final_reply):
                logger.warning("Detected garbled /plan output run_id=%s, attempting recovery pass", run_id)
                recovery_payload = {
                    "request_text": request_text,
                    "plan_brief": plan_brief,
                    "tool_trace_preview": tool_trace[:8],
                }
                recovery_messages = [
                    self._base_system_message(),
                    {
                        "role": "system",
                        "content": (
                            "Rewrite a clear, concise plain-text answer in English. "
                            "Do not output long runs of '?' characters. "
                            "If data is incomplete, state what is missing."
                        ),
                    },
                    {"role": "user", "content": json.dumps(recovery_payload, ensure_ascii=True)},
                ]
                recovered_reply, _ = await self._run_agent_loop(
                    scope_for_tools=chat_scope,
                    messages=recovery_messages,
                    tools=[],
                )
                self.contexts.put("plan_run", run_id, "recovery_raw", {"text": recovered_reply})
                if _looks_garbled_text(recovered_reply):
                    final_reply = (
                        "I could not produce a readable plan result for this request. "
                        "Please retry with a more specific query."
                    )
                else:
                    final_reply = recovered_reply
            self.contexts.put("plan_run", run_id, "tool_trace", tool_trace)
            self.contexts.put("plan_run", run_id, "result", {"text": final_reply})
            self.contexts.put("plan_run", run_id, "status", {"value": "completed"})
            self.memory.add_message(chat_scope, "assistant", final_reply)
            self.contexts.put("chat", chat_scope, "last_assistant_message", {"text": final_reply})
            await self._send(chat_scope, final_reply)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Plan run failed run_id=%s", run_id)
            self.contexts.put("plan_run", run_id, "error", {"message": str(exc)})
            self.contexts.put("plan_run", run_id, "status", {"value": "failed"})
            await self._send(chat_scope, f"Plan run failed ({run_id}): {exc}")

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
                    _tool_result_preview(result),
                )
                if fn_name.startswith("playwright__"):
                    self._record_browse_event(scope_for_tools, fn_name, args, result)
                tool_trace.append(
                    {
                        "name": fn_name,
                        "args": args,
                        "result_preview": _tool_result_preview(result, limit=300),
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
        return [*self.mcp.list_openai_tools(), _scratchpad_tool_spec()]

    async def _scratchpad_command(self, scope: str, raw_text: str) -> None:
        body = _command_body(raw_text)
        if not body:
            payload = self.contexts.get("chat", scope, "scratchpad")
            text = str(payload.get("text", "")) if isinstance(payload, dict) else ""
            await self._send(scope, f"Scratchpad ({len(text)} chars):\n{text}")
            return

        parts = body.split(maxsplit=1)
        action = parts[0].strip().lower()
        content = parts[1].strip() if len(parts) > 1 else ""

        if action == "show":
            payload = self.contexts.get("chat", scope, "scratchpad")
            text = str(payload.get("text", "")) if isinstance(payload, dict) else ""
            await self._send(scope, f"Scratchpad ({len(text)} chars):\n{text}")
            return
        if action == "clear":
            self.contexts.put("chat", scope, "scratchpad", {"text": ""})
            await self._send(scope, "Scratchpad cleared.")
            return
        if action == "set":
            self.contexts.put("chat", scope, "scratchpad", {"text": content[:SCRATCHPAD_MAX_CHARS]})
            await self._send(scope, f"Scratchpad set ({min(len(content), SCRATCHPAD_MAX_CHARS)} chars).")
            return
        if action == "append":
            result = json.loads(self._handle_scratchpad_tool(scope, {"mode": "append", "content": content}))
            await self._send(scope, f"Scratchpad appended ({int(result.get('chars', 0))} total chars).")
            return

        await self._send(scope, "Usage: /scratchpad [show|set <text>|append <text>|clear]")

    def _record_browse_event(self, scope: str, tool_name: str, args: dict[str, Any], result: str) -> None:
        page_url = _extract_playwright_field(result, "Page URL")
        page_title = _extract_playwright_field(result, "Page Title")
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
                "at": _human_now(),
                "tool": tool_name,
                "args": args,
                "page_url": page_url or "",
                "page_title": page_title or "",
                "blocked": blocked,
                "result_preview": _tool_result_preview(result, limit=400),
            }
        )
        events = events[-40:]
        self.contexts.put("chat", scope, "browse_history", {"events": events})

    def _scratchpad_system_message(self, scope: str) -> dict[str, str] | None:
        payload = self.contexts.get("chat", scope, "scratchpad")
        if not isinstance(payload, dict):
            return None
        text = str(payload.get("text", "")).strip()
        if not text:
            return None
        return {
            "role": "system",
            "content": (f"Session scratchpad (private notes, never reveal directly):\n{text}"),
        }

    def _handle_scratchpad_tool(self, scope: str, args: dict[str, Any]) -> str:
        mode = str(args.get("mode", "append")).strip().lower()
        if mode not in {"append", "replace", "clear"}:
            mode = "append"
        content = str(args.get("content", "")).strip()

        existing_payload = self.contexts.get("chat", scope, "scratchpad")
        existing_text = ""
        if isinstance(existing_payload, dict):
            existing_text = str(existing_payload.get("text", ""))

        if mode == "clear":
            new_text = ""
        elif mode == "replace":
            new_text = content
        elif not existing_text:
            new_text = content
        elif not content:
            new_text = existing_text
        else:
            new_text = f"{existing_text}\n{content}"

        if len(new_text) > SCRATCHPAD_MAX_CHARS:
            new_text = new_text[-SCRATCHPAD_MAX_CHARS:]
        self.contexts.put("chat", scope, "scratchpad", {"text": new_text})

        return json.dumps(
            {
                "ok": True,
                "mode": mode,
                "chars": len(new_text),
            },
            ensure_ascii=True,
        )

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
        recent = _attach_human_timestamps(recent)
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
        history = _attach_human_timestamps(history)
        trimmed = _trim_history_by_chars(history, self.config.history_char_limit)
        messages = [self._base_system_message()]
        scratchpad_message = self._scratchpad_system_message(scope)
        if scratchpad_message is not None:
            messages.append(scratchpad_message)
        messages.extend(trimmed)
        payload = {
            "model": self.config.model.model,
            "temperature": self.config.model.temperature,
            "max_tokens": self.config.model.max_tokens,
            "tools_count": len(self._list_openai_tools()),
            "messages": messages,
        }
        body = json.dumps(payload, ensure_ascii=True, indent=2)
        return _clip_long(body)
