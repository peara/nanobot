from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from nanobot.core_utils import extract_playwright_field, tool_result_preview

TOOL_RESULTS_CONTEXT_KEY = "tool_results"


@dataclass(frozen=True)
class ToolCallEvent:
    scope: str
    call_id: str
    tool_name: str
    args: dict[str, Any]
    result: str
    result_preview: str
    ok: bool
    error: str | None
    at: str


class ToolHook(Protocol):
    async def after_tool_call(self, event: ToolCallEvent, bot: Any) -> None: ...


def _load_events_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        events = payload.get("events")
        return events if isinstance(events, list) else []
    if isinstance(payload, list):
        return payload
    return []


def load_tool_result_events(payload: Any) -> list[dict[str, Any]]:
    events = _load_events_payload(payload)
    return [item for item in events if isinstance(item, dict)]


class ToolResultRecorderHook:
    async def after_tool_call(self, event: ToolCallEvent, bot: Any) -> None:
        existing = bot.contexts.get("chat", event.scope, TOOL_RESULTS_CONTEXT_KEY)
        events = load_tool_result_events(existing)
        events.append(
            {
                "at": event.at,
                "tool": event.tool_name,
                "args": event.args,
                "ok": event.ok,
                "error": event.error or "",
                "result_chars": len(event.result),
                "result_preview": event.result_preview,
            }
        )
        bot.contexts.put("chat", event.scope, TOOL_RESULTS_CONTEXT_KEY, {"events": events[-60:]})


class BrowseEventRecorderHook:
    async def after_tool_call(self, event: ToolCallEvent, bot: Any) -> None:
        if not event.tool_name.startswith("playwright__"):
            return
        page_url = extract_playwright_field(event.result, "Page URL")
        page_title = extract_playwright_field(event.result, "Page Title")
        blocked = False
        if page_title and "pardon our interruption" in page_title.lower():
            blocked = True
        if page_url and "/splashui/challenge" in page_url:
            blocked = True
        existing = bot.contexts.get("chat", event.scope, "browse_history")
        events = _load_events_payload(existing)
        events.append(
            {
                "at": event.at,
                "tool": event.tool_name,
                "args": event.args,
                "page_url": page_url or "",
                "page_title": page_title or "",
                "blocked": blocked,
                "ok": event.ok,
                "error": event.error or "",
                "result_preview": tool_result_preview(event.result, limit=400),
            }
        )
        bot.contexts.put("chat", event.scope, "browse_history", {"events": events[-40:]})
