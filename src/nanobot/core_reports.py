from __future__ import annotations

import json
from typing import Any

from nanobot.core_utils import attach_human_timestamps, clip, clip_long, trim_history_by_chars


def build_context_report(bot: Any, scope: str) -> str:
    total = bot.memory.count_messages(scope)
    recent = bot.memory.get_recent_messages(scope, limit=bot.config.history_message_limit)
    recent = attach_human_timestamps(recent, timezone_name=bot.config.working_timezone)
    trimmed = trim_history_by_chars(recent, bot.config.history_char_limit)
    recent_chars = sum(len(str(m.get("content", ""))) for m in recent)
    trimmed_chars = sum(len(str(m.get("content", ""))) for m in trimmed)
    lines = [
        "Context report",
        f"scope: {scope}",
        f"total_messages_in_db: {total}",
        f"recent_window_limit: {bot.config.history_message_limit}",
        f"char_limit: {bot.config.history_char_limit}",
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
            content = clip(str(msg.get("content", "")))
            lines.append(f"- {role}: {content}")
    return "\n".join(lines)


def build_full_context_report(bot: Any, scope: str) -> str:
    history = bot.memory.get_recent_messages(scope, limit=bot.config.history_message_limit)
    history = attach_human_timestamps(history, timezone_name=bot.config.working_timezone)
    trimmed = trim_history_by_chars(history, bot.config.history_char_limit)
    messages = [bot._base_system_message()]
    messages.extend(trimmed)
    scratchpad_message = bot._scratchpad_assistant_message(scope)
    if scratchpad_message is not None:
        messages.append(scratchpad_message)
    payload = {
        "model": bot.config.model.model,
        "temperature": bot.config.model.temperature,
        "max_tokens": bot.config.model.max_tokens,
        "tools_count": len(bot._list_openai_tools()),
        "messages": messages,
    }
    body = json.dumps(payload, ensure_ascii=True, indent=2)
    return clip_long(body)
