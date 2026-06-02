from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEDULED_SYSTEM_MARKER = (
    "This is an automated scheduler trigger, not a user message. Do not assume a human is currently chatting."
)


def scoped_chat_id(channel: str, chat_id: str) -> str:
    return f"{channel}:{chat_id}"


def unscoped_chat_id(scoped: str) -> tuple[str, str]:
    channel, _, chat = scoped.partition(":")
    return channel, chat


def trim_history_by_chars(messages: list[dict], char_limit: int) -> list[dict]:
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


def tool_result_preview(text: str, limit: int = 1200) -> str:
    compact = text.replace("\n", "\\n")
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}...(truncated)"


def clip(text: str, limit: int = 100) -> str:
    stripped = text.strip().replace("\n", " ")
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit]}..."


def clip_long(text: str, limit: int = 3500) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...(truncated)"


def _resolve_zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def format_timestamp_for_prompt(raw_value: str, timezone_name: str = "UTC") -> str | None:
    try:
        parsed = datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    local = parsed.astimezone(_resolve_zoneinfo(timezone_name))
    return local.strftime("%A, %d %B %Y, %I:%M %p")


def attach_human_timestamps(messages: list[dict], timezone_name: str = "UTC") -> list[dict]:
    result: list[dict] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        created_at = message.get("created_at")
        if isinstance(created_at, str):
            formatted = format_timestamp_for_prompt(created_at, timezone_name)
            if formatted:
                content = f"[{formatted}]\n{content}"
        result.append({"role": role, "content": content})
    return result


def extract_playwright_field(result_text: str, field: str) -> str | None:
    prefix = f"- {field}: "
    for line in result_text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def human_now(timezone_name: str = "UTC") -> str:
    return datetime.now(_resolve_zoneinfo(timezone_name)).strftime("%A, %d %B %Y, %I:%M %p")


def looks_garbled_text(text: str) -> bool:
    if not text:
        return False
    if len(text) < 80:
        return False
    q_count = text.count("?")
    if q_count < 20:
        return False
    ratio = q_count / max(1, len(text))
    return ratio >= 0.2


def help_text() -> str:
    return "\n".join(
        [
            "Available commands",
            "/help - show this help",
            "/plan <request> - run inline planner flow in a new plan_run scope",
            "/ctx - compact context diagnostics for this chat",
            "/ctxfull - full pre-LLM payload JSON (truncated)",
            "/reset - clear local conversation history for this chat scope",
            "/scratchpad [show|clear] - inspect or clear structured scratchpad",
            "/stop - cancel all in-flight requests",
        ]
    )


def command_name(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split()[0]
    token = token.split("@", 1)[0]
    return token.lower()


def command_body(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return stripped
    parts = stripped.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
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
