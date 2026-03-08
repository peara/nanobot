from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from nanobot.core_utils import extract_playwright_field, tool_result_preview

TOOL_RESULTS_CONTEXT_KEY = "tool_results"
BROWSE_HISTORY_CONTEXT_KEY = "browse_history"


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


@dataclass(frozen=True)
class HookDebugCommand:
    name: str
    help: str
    add_arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace, str], None]


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


def _connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _latest_scope(db_path: str) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT chat_id FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    return str(row[0]) if row else None


def _resolve_scope(args: argparse.Namespace, db_path: str) -> str:
    scope = getattr(args, "scope", None)
    if getattr(args, "latest", False):
        scope = _latest_scope(db_path)
    if not scope:
        raise SystemExit("Scope is required. Pass --scope or --latest.")
    return str(scope)


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

    @staticmethod
    def debug_commands() -> list[HookDebugCommand]:
        def add_arguments(parser: argparse.ArgumentParser) -> None:
            parser.add_argument("--scope", help="Scoped chat id, e.g. telegram:500506690")
            parser.add_argument("--latest", action="store_true", help="Use latest scope from DB")
            parser.add_argument("--limit", type=int, default=20, help="Number of latest tool events to show")
            parser.add_argument("--full", action="store_true", help="Show args and result preview")

        def run(args: argparse.Namespace, database_path: str) -> None:
            scope = _resolve_scope(args, database_path)
            with _connect(database_path) as conn:
                row = conn.execute(
                    """
                    SELECT value_json
                    FROM contexts
                    WHERE scope_type = 'chat' AND scope_id = ? AND key = ?
                    LIMIT 1
                    """,
                    (scope, TOOL_RESULTS_CONTEXT_KEY),
                ).fetchone()
            if not row:
                print(f"No tool result history found for scope: {scope}")
                return
            payload = json.loads(str(row[0]))
            events = load_tool_result_events(payload)
            if not events:
                print(f"No tool result events found for scope: {scope}")
                return
            selected = events[-max(1, int(args.limit)) :]
            print(f"Tool results for {scope} (showing {len(selected)} of {len(events)} events)")
            for idx, event in enumerate(selected, start=1):
                print(f"\n[{idx}] {event.get('at', '')}")
                print(f"tool={event.get('tool', '')}")
                print(f"ok={event.get('ok', True)}")
                result_chars_raw = event.get("result_chars")
                result_chars: int | None = None
                if isinstance(result_chars_raw, int):
                    result_chars = result_chars_raw
                elif isinstance(result_chars_raw, str) and result_chars_raw.isdigit():
                    result_chars = int(result_chars_raw)
                if result_chars is not None:
                    print(f"result_chars={result_chars}")
                error = str(event.get("error", "")).strip()
                if error:
                    print(f"error={error}")
                if args.full:
                    print("args:")
                    print(json.dumps(event.get("args", {}), ensure_ascii=True, indent=2))
                print(f"preview={event.get('result_preview', '')}")

        return [
            HookDebugCommand(
                name="tools",
                help="Inspect stored tool result history",
                add_arguments=add_arguments,
                run=run,
            )
        ]


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
        existing = bot.contexts.get("chat", event.scope, BROWSE_HISTORY_CONTEXT_KEY)
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
        bot.contexts.put("chat", event.scope, BROWSE_HISTORY_CONTEXT_KEY, {"events": events[-40:]})

    @staticmethod
    def debug_commands() -> list[HookDebugCommand]:
        def add_arguments(parser: argparse.ArgumentParser) -> None:
            parser.add_argument("--scope", help="Scoped chat id, e.g. telegram:500506690")
            parser.add_argument("--latest", action="store_true", help="Use latest scope from DB")
            parser.add_argument("--limit", type=int, default=12, help="Number of latest browse events to show")
            parser.add_argument("--full", action="store_true", help="Show args and result preview")

        def run(args: argparse.Namespace, database_path: str) -> None:
            scope = _resolve_scope(args, database_path)
            with _connect(database_path) as conn:
                row = conn.execute(
                    """
                    SELECT value_json
                    FROM contexts
                    WHERE scope_type = 'chat' AND scope_id = ? AND key = ?
                    LIMIT 1
                    """,
                    (scope, BROWSE_HISTORY_CONTEXT_KEY),
                ).fetchone()
            if not row:
                print(f"No browse history found for scope: {scope}")
                return
            payload = json.loads(str(row[0]))
            events = _load_events_payload(payload)
            events = [item for item in events if isinstance(item, dict)]
            if not events:
                print(f"No browse events found for scope: {scope}")
                return
            selected = events[-max(1, int(args.limit)) :]
            print(f"Browse history for {scope} (showing {len(selected)} of {len(events)} events)")
            for idx, event in enumerate(selected, start=1):
                print(f"\n[{idx}] {event.get('at', '')}")
                print(f"tool={event.get('tool', '')}")
                print(f"blocked={event.get('blocked', False)}")
                print(f"url={event.get('page_url', '')}")
                print(f"title={event.get('page_title', '')}")
                if args.full:
                    print("args:")
                    print(json.dumps(event.get("args", {}), ensure_ascii=True, indent=2))
                    print(f"preview={event.get('result_preview', '')}")

        return [
            HookDebugCommand(
                name="browse",
                help="Inspect stored Playwright browse history",
                add_arguments=add_arguments,
                run=run,
            )
        ]


def build_default_tool_hooks() -> list[ToolHook]:
    return [
        ToolResultRecorderHook(),
        BrowseEventRecorderHook(),
    ]
