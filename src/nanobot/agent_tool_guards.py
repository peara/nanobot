from __future__ import annotations

import json
import re
from typing import Any

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
    if tool_name not in {"web__read_page", "web__invoke_script"}:
        return False
    if payload.get("ok") is not True:
        return False
    if tool_name == "web__invoke_script":
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
    if tool_name == "web__invoke_script":
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
    return tool_name == "web__create_script" and payload.get("ok") is True
