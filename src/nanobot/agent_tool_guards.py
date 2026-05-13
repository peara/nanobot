from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from nanobot.core_scratchpad import SCRATCHPAD_TOOL_NAME

WEB_CREATE_SCRIPT_TOOL = "web__create_script"
WEB_INVOKE_SCRIPT_TOOL = "web__invoke_script"
WEB_READ_PAGE_TOOL = "web__read_page"
MEMORY_SAVE_TOOL = "memory__save"

SCRIPT_SAVED_PATTERNS = (
    r"\bscript saved\b",
    r"\bsaved as\b",
    r"\buse\s+[a-z0-9_-]+\b",
)
JAVASCRIPT_SCRIPT_MARKERS = ("const ", "let ", "=>", "document.queryselector", "?.", "array.from(")
DATA_LOSS_REPLY_PATTERNS = (
    "didn't survive",
    "did not survive",
    "need to re-run",
    "need to rerun",
    "would need to re-run",
    "would need to rerun",
)
BLOCKED_WHEN_ITEMS_EXIST = (
    "would you like me to fetch",
    "i don't have the actual",
    "no actual web work done yet",
)
DSML_PARAM_PATTERN = re.compile(r"<\|DSML\|([^>]+)>(.*?)</\|DSML\|\1>", re.DOTALL)


@dataclass
class ToolCallContext:
    """State shared across one agent turn."""

    scope: str
    scratchpad_calls: int = 0
    total_calls: int = 0
    current_tool_name: str = ""
    _guard_state: dict[str, dict[str, Any]] = field(default_factory=dict)

    def guard_state(self, name: str) -> dict[str, Any]:
        return self._guard_state.setdefault(name, {})


@dataclass
class PreCallResult:
    block: bool = False
    block_error: str | None = None
    block_payload: dict[str, Any] | None = None
    normalized_args: dict[str, Any] | None = None


@dataclass
class PostResultAction:
    force_finalize: bool = False
    finalize_reply: str | None = None


class ToolGuard:
    """Base hook for tool-call argument/result handling."""

    def pre_call(self, fn_name: str, args: dict[str, Any], ctx: ToolCallContext) -> PreCallResult | None:
        del fn_name, args, ctx
        return None

    def post_result(
        self,
        fn_name: str,
        args: dict[str, Any],
        result: dict[str, Any] | None,
        ctx: ToolCallContext,
    ) -> PostResultAction | None:
        del fn_name, args, result, ctx
        return None

    def rewrite_finalize_reply(self, reply: str, ctx: ToolCallContext) -> str | None:
        del reply, ctx
        return None


def parse_tool_result_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def contains_script_saved_claim(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in SCRIPT_SAVED_PATTERNS)


def normalize_memory_user_id(args: dict[str, Any], scope_for_tools: str) -> None:
    if not str(args.get("user_id", "")).strip():
        args["user_id"] = scope_for_tools
        return
    user_id = str(args.get("user_id", "")).strip()
    if ":" in user_id:
        return
    if ":" not in scope_for_tools:
        return
    channel = scope_for_tools.split(":", 1)[0]
    args["user_id"] = f"{channel}:{user_id}"


def coerce_scalar_param(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def normalize_invoke_script_params(args: dict[str, Any]) -> None:
    params = args.get("params")
    if params is None:
        args["params"] = {}
        return
    if isinstance(params, dict):
        return
    if isinstance(params, str):
        text = params.strip()
        if not text:
            args["params"] = {}
            return
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            dsml_matches = DSML_PARAM_PATTERN.findall(text)
            if dsml_matches:
                args["params"] = {key.strip(): coerce_scalar_param(value) for key, value in dsml_matches}
                return
            args["params"] = {"value": text}
            return
        if isinstance(parsed, dict):
            args["params"] = parsed
            return
    args["params"] = {"value": params}


def looks_like_javascript_script(code: str) -> bool:
    lowered = code.lower()
    return any(marker in lowered for marker in JAVASCRIPT_SCRIPT_MARKERS)


def has_usable_web_data(tool_name: str, payload: dict[str, Any]) -> bool:
    if tool_name not in {WEB_READ_PAGE_TOOL, WEB_INVOKE_SCRIPT_TOOL}:
        return False
    if payload.get("ok") is not True:
        return False
    if tool_name == WEB_INVOKE_SCRIPT_TOOL:
        data = payload.get("data")
        if not isinstance(data, dict):
            return False
        items = data.get("items")
        if isinstance(items, list) and items:
            return True
        return bool(str(data.get("content", "")).strip())
    items = payload.get("items")
    if isinstance(items, list) and items:
        return True
    return bool(str(payload.get("content", "")).strip())


def reply_claims_data_lost(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in (*DATA_LOSS_REPLY_PATTERNS, *BLOCKED_WHEN_ITEMS_EXIST))


def rewrite_data_loss_reply(text: str) -> str:
    if not reply_claims_data_lost(text):
        return text
    return (
        "I already fetched and parsed the page in this turn. "
        "Here are the extracted results now, and the reusable script status is reported separately."
    )


def extract_web_items(tool_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if tool_name == WEB_INVOKE_SCRIPT_TOOL:
        data = payload.get("data")
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def synthesize_web_data_reply(items: list[dict[str, Any]], fallback: str) -> str:
    if not items:
        return rewrite_data_loss_reply(fallback)
    lines = ["I already extracted the results in this turn. Here are the top stories:"]
    for idx, item in enumerate(items[:10], start=1):
        title = str(item.get("title", "")).strip() or "Untitled"
        url = str(item.get("url", "")).strip()
        if url:
            lines.append(f"{idx}. {title} - {url}")
        else:
            lines.append(f"{idx}. {title}")
    return "\n".join(lines)


def looks_like_successful_script_create(tool_name: str, payload: dict[str, Any]) -> bool:
    return tool_name == WEB_CREATE_SCRIPT_TOOL and payload.get("ok") is True


class WebScriptGuard(ToolGuard):
    """NanoScript-specific interception and turn state."""

    _state_name = "web_script"

    def pre_call(self, fn_name: str, args: dict[str, Any], ctx: ToolCallContext) -> PreCallResult | None:
        state = ctx.guard_state(self._state_name)

        if fn_name == WEB_CREATE_SCRIPT_TOOL and looks_like_javascript_script(str(args.get("code", ""))):
            return PreCallResult(
                block=True,
                block_error="invalid_script_language",
                block_payload={
                    "ok": False,
                    "error": "invalid_script",
                    "message": (
                        "web__create_script expects Python NanoScript only. "
                        "JavaScript syntax detected. Use: async def script(page, params) -> dict."
                    ),
                },
            )

        if fn_name == WEB_INVOKE_SCRIPT_TOOL:
            normalize_invoke_script_params(args)
            return PreCallResult(normalized_args=args)

        if fn_name == MEMORY_SAVE_TOOL:
            normalize_memory_user_id(args, ctx.scope)
            if state.get("last_create_ok") is False and contains_script_saved_claim(str(args.get("text", ""))):
                return PreCallResult(
                    block=True,
                    block_error="blocked_false_memory",
                    block_payload={
                        "ok": False,
                        "error": "blocked_false_memory",
                        "message": (
                            "Blocked memory__save: previous web__create_script failed, "
                            "so script-saved claims are not allowed."
                        ),
                    },
                )
            return PreCallResult(normalized_args=args)

        return None

    def post_result(
        self,
        fn_name: str,
        args: dict[str, Any],
        result: dict[str, Any] | None,
        ctx: ToolCallContext,
    ) -> PostResultAction | None:
        del args
        if result is None:
            return None
        state = ctx.guard_state(self._state_name)

        if fn_name == WEB_CREATE_SCRIPT_TOOL and isinstance(result.get("ok"), bool):
            state["last_create_ok"] = bool(result["ok"])

        if has_usable_web_data(fn_name, result):
            state["had_usable_web_data"] = True
            extracted_items = extract_web_items(fn_name, result)
            if extracted_items:
                state["latest_web_items"] = extracted_items

        if looks_like_successful_script_create(fn_name, result):
            script = result.get("script")
            if isinstance(script, dict):
                name = str(script.get("name", "")).strip()
                if name:
                    state["latest_script_status"] = f"{name} saved"

        if state.get("had_usable_web_data") and state.get("latest_web_items") and state.get("last_create_ok") is True:
            reply = synthesize_web_data_reply(state["latest_web_items"], "")
            if state.get("latest_script_status"):
                reply += f"\nReusable script: {state['latest_script_status']}."
            return PostResultAction(force_finalize=True, finalize_reply=reply)

        if (
            ctx.current_tool_name == SCRATCHPAD_TOOL_NAME
            and ctx.scratchpad_calls >= 3
            and state.get("had_usable_web_data")
        ):
            reply = synthesize_web_data_reply(state.get("latest_web_items", []), "")
            return PostResultAction(force_finalize=True, finalize_reply=reply)

        return None

    def rewrite_finalize_reply(self, reply: str, ctx: ToolCallContext) -> str | None:
        state = ctx.guard_state(self._state_name)
        if not state.get("had_usable_web_data"):
            return None
        if reply_claims_data_lost(reply):
            return synthesize_web_data_reply(state.get("latest_web_items", []), reply)
        return rewrite_data_loss_reply(reply)
