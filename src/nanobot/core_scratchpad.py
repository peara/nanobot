from __future__ import annotations

import json
from typing import Any

from nanobot.core_utils import SCRATCHPAD_MAX_CHARS, command_body


async def scratchpad_command(bot: Any, scope: str, raw_text: str) -> None:
    body = command_body(raw_text)
    if not body:
        payload = bot.contexts.get("chat", scope, "scratchpad")
        text = str(payload.get("text", "")) if isinstance(payload, dict) else ""
        await bot._send(scope, f"Scratchpad ({len(text)} chars):\n{text}")
        return

    parts = body.split(maxsplit=1)
    action = parts[0].strip().lower()
    content = parts[1].strip() if len(parts) > 1 else ""

    if action == "show":
        payload = bot.contexts.get("chat", scope, "scratchpad")
        text = str(payload.get("text", "")) if isinstance(payload, dict) else ""
        await bot._send(scope, f"Scratchpad ({len(text)} chars):\n{text}")
        return
    if action == "clear":
        bot.contexts.put("chat", scope, "scratchpad", {"text": ""})
        await bot._send(scope, "Scratchpad cleared.")
        return
    if action == "set":
        bot.contexts.put("chat", scope, "scratchpad", {"text": content[:SCRATCHPAD_MAX_CHARS]})
        await bot._send(scope, f"Scratchpad set ({min(len(content), SCRATCHPAD_MAX_CHARS)} chars).")
        return
    if action == "append":
        result = json.loads(handle_scratchpad_tool(bot, scope, {"mode": "append", "content": content}))
        await bot._send(scope, f"Scratchpad appended ({int(result.get('chars', 0))} total chars).")
        return

    await bot._send(scope, "Usage: /scratchpad [show|set <text>|append <text>|clear]")


def scratchpad_system_message(bot: Any, scope: str) -> dict[str, str] | None:
    payload = bot.contexts.get("chat", scope, "scratchpad")
    if not isinstance(payload, dict):
        return None
    text = str(payload.get("text", "")).strip()
    if not text:
        return None
    return {
        "role": "system",
        "content": (f"Session scratchpad (private notes, never reveal directly):\n{text}"),
    }


def handle_scratchpad_tool(bot: Any, scope: str, args: dict[str, Any]) -> str:
    mode = str(args.get("mode", "append")).strip().lower()
    if mode not in {"append", "replace", "clear"}:
        mode = "append"
    content = str(args.get("content", "")).strip()

    existing_payload = bot.contexts.get("chat", scope, "scratchpad")
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
    bot.contexts.put("chat", scope, "scratchpad", {"text": new_text})

    return json.dumps(
        {
            "ok": True,
            "mode": mode,
            "chars": len(new_text),
        },
        ensure_ascii=True,
    )
