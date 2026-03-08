from __future__ import annotations

import json
from typing import Any

from nanobot.core_utils import command_body, extract_json_object, human_now

MAX_TEXT_CHARS = 600
MAX_STEP_RESULTS = 20
VALID_ACTIONS = {"set_goal", "set_plan", "add_step_result", "reset"}


def empty_scratchpad_state() -> dict[str, Any]:
    return {
        "goal": "",
        "plan": "",
        "step_results": [],
        "updated_at": human_now(),
    }


def _coerce_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return empty_scratchpad_state()
    state = empty_scratchpad_state()
    state["goal"] = str(payload.get("goal", "")).strip()[:MAX_TEXT_CHARS]
    state["plan"] = str(payload.get("plan", "")).strip()[:MAX_TEXT_CHARS]
    raw_steps = payload.get("step_results")
    if isinstance(raw_steps, list):
        cleaned: list[dict[str, str]] = []
        for item in raw_steps[-MAX_STEP_RESULTS:]:
            if not isinstance(item, dict):
                continue
            cleaned.append(
                {
                    "at": str(item.get("at", "")).strip() or human_now(),
                    "summary": str(item.get("summary", "")).strip()[:MAX_TEXT_CHARS],
                }
            )
        state["step_results"] = cleaned
    state["updated_at"] = str(payload.get("updated_at", "")).strip() or human_now()
    return state


def get_scratchpad_state(bot: Any, scope: str) -> dict[str, Any]:
    payload = bot.contexts.get("chat", scope, "scratchpad")
    return _coerce_state(payload)


def clear_scratchpad(bot: Any, scope: str) -> None:
    bot.contexts.put("chat", scope, "scratchpad", empty_scratchpad_state())


def apply_structured_update(bot: Any, scope: str, update: dict[str, Any]) -> dict[str, Any]:
    state = get_scratchpad_state(bot, scope)
    action = str(update.get("action", "")).strip().lower()
    content = str(update.get("content", "")).strip()[:MAX_TEXT_CHARS]

    if action == "reset":
        state = empty_scratchpad_state()
    elif action == "set_goal":
        state["goal"] = content
    elif action == "set_plan":
        state["plan"] = content
    elif action == "add_step_result":
        if content:
            step_results = state.get("step_results")
            if not isinstance(step_results, list):
                step_results = []
            step_results.append({"at": human_now(), "summary": content})
            state["step_results"] = step_results[-MAX_STEP_RESULTS:]

    state["updated_at"] = human_now()
    bot.contexts.put("chat", scope, "scratchpad", state)
    return state


def parse_structured_turn(raw_text: str) -> tuple[dict[str, str], str] | None:
    payload = extract_json_object(raw_text)
    if not isinstance(payload, dict):
        return None
    update = payload.get("scratchpad_update")
    assistant_reply = payload.get("assistant_reply")
    if not isinstance(update, dict) or not isinstance(assistant_reply, str):
        return None
    action = str(update.get("action", "")).strip().lower()
    content = str(update.get("content", "")).strip()
    if action not in VALID_ACTIONS:
        return None
    if action != "reset" and not content:
        return None
    return {"action": action, "content": content}, assistant_reply.strip()


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
