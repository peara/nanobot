from __future__ import annotations

import json
from typing import Any

from nanobot.core_utils import command_body, human_now

SCRATCHPAD_TOOL_NAME = "session__scratchpad_write"
MAX_FIELD_CHARS = 600
MAX_CONTEXT_CHARS = 1200
MAX_KNOWN_FACTS = 30
MAX_TOOL_JOURNAL = 30
VALID_MODES = {"init", "append", "finalize"}


def empty_scratchpad_state() -> dict[str, Any]:
    return {
        "goal": "",
        "context": "",
        "known_facts": [],
        "current_step": "",
        "next_step": "",
        "tool_journal": [],
        "updated_at": human_now(),
    }


def _clip_text(value: Any, *, limit: int = MAX_FIELD_CHARS) -> str:
    return str(value or "").strip()[:limit]


def _to_text_list(value: Any, *, limit_items: int, limit_chars: int = MAX_FIELD_CHARS) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = _clip_text(item, limit=limit_chars)
        if text:
            cleaned.append(text)
    return cleaned[-limit_items:]


def _coerce_state(payload: Any) -> dict[str, Any]:
    state = empty_scratchpad_state()
    if not isinstance(payload, dict):
        return state
    state["goal"] = _clip_text(payload.get("goal"))
    state["context"] = _clip_text(payload.get("context"), limit=MAX_CONTEXT_CHARS)
    state["known_facts"] = _to_text_list(payload.get("known_facts"), limit_items=MAX_KNOWN_FACTS)
    state["current_step"] = _clip_text(payload.get("current_step"))
    state["next_step"] = _clip_text(payload.get("next_step"))
    state["tool_journal"] = _to_text_list(payload.get("tool_journal"), limit_items=MAX_TOOL_JOURNAL)
    state["updated_at"] = _clip_text(payload.get("updated_at")) or human_now()
    return state


def get_scratchpad_state(bot: Any, scope: str) -> dict[str, Any]:
    payload = bot.contexts.get("chat", scope, "scratchpad")
    return _coerce_state(payload)


def clear_scratchpad(bot: Any, scope: str) -> None:
    bot.contexts.put("chat", scope, "scratchpad", empty_scratchpad_state())


def scratchpad_tool_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": SCRATCHPAD_TOOL_NAME,
            "description": (
                "Update private execution scratchpad state. "
                "Use mode init at start of work, append after each tool result, finalize before final answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["init", "append", "finalize"],
                    },
                    "goal": {"type": "string"},
                    "context": {"type": "string"},
                    "known_facts": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "current_step": {"type": "string"},
                    "next_step": {"type": "string"},
                    "tool_journal": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
    }


def apply_scratchpad_tool_call(bot: Any, scope: str, args: dict[str, Any]) -> dict[str, Any]:
    mode = _clip_text(args.get("mode")).lower()
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Expected one of: {', '.join(sorted(VALID_MODES))}")

    state = get_scratchpad_state(bot, scope)
    if mode == "init":
        state = empty_scratchpad_state()

    goal = _clip_text(args.get("goal"))
    if goal:
        state["goal"] = goal
    context = _clip_text(args.get("context"), limit=MAX_CONTEXT_CHARS)
    if context:
        state["context"] = context
    current_step = _clip_text(args.get("current_step"))
    if current_step:
        state["current_step"] = current_step
    next_step = _clip_text(args.get("next_step"))
    if next_step:
        state["next_step"] = next_step

    known_facts = _to_text_list(args.get("known_facts"), limit_items=MAX_KNOWN_FACTS)
    if known_facts:
        if mode == "init":
            state["known_facts"] = known_facts
        else:
            combined = [*state.get("known_facts", []), *known_facts]
            state["known_facts"] = _to_text_list(combined, limit_items=MAX_KNOWN_FACTS)

    tool_journal = _to_text_list(args.get("tool_journal"), limit_items=MAX_TOOL_JOURNAL)
    if tool_journal:
        if mode == "init":
            state["tool_journal"] = tool_journal
        else:
            combined = [*state.get("tool_journal", []), *tool_journal]
            state["tool_journal"] = _to_text_list(combined, limit_items=MAX_TOOL_JOURNAL)

    state["updated_at"] = human_now()
    normalized = _coerce_state(state)
    bot.contexts.put("chat", scope, "scratchpad", normalized)
    return normalized


async def scratchpad_command(bot: Any, scope: str, raw_text: str) -> None:
    body = command_body(raw_text)
    if not body:
        state = get_scratchpad_state(bot, scope)
        text = json.dumps(state, ensure_ascii=True, indent=2)
        await bot._send(scope, f"Structured scratchpad:\n{text}")
        return

    parts = body.split(maxsplit=1)
    action = parts[0].strip().lower()
    if action == "show":
        state = get_scratchpad_state(bot, scope)
        text = json.dumps(state, ensure_ascii=True, indent=2)
        await bot._send(scope, f"Structured scratchpad:\n{text}")
        return
    if action == "clear":
        clear_scratchpad(bot, scope)
        await bot._send(scope, "Scratchpad cleared.")
        return
    await bot._send(scope, "Usage: /scratchpad [show|clear]")


def scratchpad_system_message(bot: Any, scope: str) -> dict[str, str] | None:
    state = get_scratchpad_state(bot, scope)
    body = json.dumps(state, ensure_ascii=True, indent=2)
    return {
        "role": "system",
        "content": (
            f"Execution scratchpad (private state, never reveal verbatim). Keep it updated every turn.\n{body}"
        ),
    }
