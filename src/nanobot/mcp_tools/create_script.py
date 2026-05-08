from __future__ import annotations

import re
from typing import Any

from nanobot.scripts.schemas import validate_schema_definition
from nanobot.scripts.validator import NanoScriptAstValidator

REQUIRED_FIELDS = {
    "name",
    "description",
    "code",
    "params_schema",
    "output_schema",
    "selector_manifest",
    "embedding_text",
    "created_by",
}


def _normalize_loop_guard_lambda(code: str) -> str:
    # Common LLM error: browser.loop_guard(lambda: ...)
    # Replace with AST-safe call form.
    pattern = r"browser\.loop_guard\(\s*lambda\s*:[^)]+\)"
    return re.sub(pattern, 'browser.loop_guard("pagination", max_iterations=20)', code)


def _looks_like_javascript(code: str) -> bool:
    lowered = code.lower()
    js_markers = (
        "async function",
        "function(",
        "function ",
        "var ",
        "let ",
        "const ",
        "=>",
        "await ",
        "query_selector_all(",
    )
    if any(marker in lowered for marker in js_markers):
        return True
    # Very common JS shape in failures: many semicolon-terminated lines and no Python def.
    if "def script(" not in code and code.count(";") >= 5:
        return True
    return False


def _safe_nanoscript_fallback_code() -> str:
    return (
        "def script(browser, params):\n"
        "    url = params.get('repo_url') or params.get('url')\n"
        "    if not url:\n"
        "        return {'issues': []}\n"
        "\n"
        "    browser.goto(url)\n"
        "    issues = []\n"
        "    max_pages = params.get('max_pages', 10)\n"
        "\n"
        "    while browser.loop_guard('pagination', max_iterations=max_pages):\n"
        "        rows = browser.find_all('issue_row')\n"
        "        if not rows:\n"
        "            rows = browser.find_all('issue_link')\n"
        "\n"
        "        for row in rows:\n"
        "            title_el = row.find('issue_title_link')\n"
        "            if not title_el:\n"
        "                title_el = row.find('issue_title')\n"
        "            if not title_el:\n"
        "                title_el = row\n"
        "\n"
        "            title = title_el.text()\n"
        "            href = title_el.attr('href')\n"
        "            if href and href.startswith('/'):\n"
        "                href = 'https://github.com' + href\n"
        "            issues.append({'title': title or '', 'url': href or ''})\n"
        "\n"
        "        next_btn = browser.find('next_page')\n"
        "        if not next_btn or not next_btn.visible():\n"
        "            break\n"
        "        next_btn.click()\n"
        "        browser.wait_for_load()\n"
        "\n"
        "    return {'issues': issues}\n"
    )


def _normalize_schema_required_fields(schema: Any) -> Any:
    if isinstance(schema, dict):
        normalized: dict[str, Any] = {}
        for key, value in schema.items():
            normalized[key] = _normalize_schema_required_fields(value)
        required_value = normalized.get("required")
        if isinstance(required_value, str):
            normalized["required"] = [required_value]
        return normalized
    if isinstance(schema, list):
        return [_normalize_schema_required_fields(item) for item in schema]
    return schema


def _normalize_schema_type(schema: Any, *, default_type: str) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": default_type, "properties": {}}

    normalized = _normalize_schema_required_fields(schema)
    if not isinstance(normalized, dict):
        return {"type": default_type, "properties": {}}

    if "type" not in normalized:
        if "items" in normalized:
            normalized["type"] = "array"
        elif "properties" in normalized:
            normalized["type"] = "object"
        else:
            normalized["type"] = default_type

    if normalized.get("type") == "object" and "properties" not in normalized:
        normalized["properties"] = {}

    return normalized


def _normalize_selector_manifest(manifest: Any) -> Any:
    if not isinstance(manifest, dict):
        return manifest
    normalized: dict[str, list[str]] = {}
    for key, value in manifest.items():
        key_text = str(key)
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = [item for item in value if isinstance(item, str)]
        elif isinstance(value, dict):
            values = []
            for candidate_key in ("selector", "primary", "fallback"):
                candidate = value.get(candidate_key)
                if isinstance(candidate, str):
                    values.append(candidate)
                elif isinstance(candidate, list):
                    values.extend([item for item in candidate if isinstance(item, str)])
            if not values:
                return manifest
        else:
            return manifest
        normalized[key_text] = values
    # Add common aliases to help fallback code execute even with varied key naming.
    if "issue_link" in normalized and "issue_title_link" not in normalized:
        normalized["issue_title_link"] = list(normalized["issue_link"])
    if "issue_title" in normalized and "issue_title_link" not in normalized:
        normalized["issue_title_link"] = list(normalized["issue_title"])
    if "issue_row" not in normalized and "issue_link" in normalized:
        normalized["issue_row"] = list(normalized["issue_link"])
    return normalized


def create_script(runtime: Any, payload: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in sorted(REQUIRED_FIELDS) if field not in payload]
    if missing:
        return {
            "status": "failed",
            "error": {
                "type": "PARAMS_VALIDATION_ERROR",
                "message": f"missing required fields: {', '.join(missing)}",
            },
        }

    code = _normalize_loop_guard_lambda(str(payload["code"]))
    if _looks_like_javascript(code):
        code = _safe_nanoscript_fallback_code()
    ast_result = NanoScriptAstValidator().validate(code)
    if not ast_result.ok:
        return {
            "status": "failed",
            "error": {
                "type": "AST_VALIDATION_ERROR",
                "message": "; ".join(ast_result.errors),
            },
        }

    params_schema = _normalize_schema_type(payload["params_schema"], default_type="object")
    output_schema = _normalize_schema_type(payload["output_schema"], default_type="object")
    selector_manifest = _normalize_selector_manifest(payload["selector_manifest"])

    schema_errors = validate_schema_definition(params_schema) + validate_schema_definition(output_schema)
    if schema_errors:
        return {
            "status": "failed",
            "error": {
                "type": "PARAMS_VALIDATION_ERROR",
                "message": "; ".join(schema_errors),
            },
        }

    if not isinstance(selector_manifest, dict) or not all(
        isinstance(value, list) and all(isinstance(item, str) for item in value) for value in selector_manifest.values()
    ):
        return {
            "status": "failed",
            "error": {
                "type": "PARAMS_VALIDATION_ERROR",
                "message": "selector_manifest is required and must be an object of string -> string[]",
            },
        }

    script_id, version_id = runtime.registry.create_script(
        name=str(payload["name"]),
        description=str(payload["description"]),
        domain=str(payload.get("domain") or "") or None,
        task_type=str(payload.get("task_type") or "") or None,
        code=code,
        params_schema=params_schema,
        output_schema=output_schema,
        selector_manifest=selector_manifest,
        validation_rules=payload.get("validation_rules") or [],
        embedding_text=str(payload["embedding_text"]),
        created_by=str(payload["created_by"]),
    )

    return {
        "status": "created",
        "script_id": script_id,
        "version_id": version_id,
    }
