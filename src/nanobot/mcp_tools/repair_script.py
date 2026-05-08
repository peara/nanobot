from __future__ import annotations

from typing import Any

from nanobot.mcp_tools.create_script import (
    _looks_like_javascript,
    _normalize_loop_guard_lambda,
    _normalize_selector_manifest,
    _safe_nanoscript_fallback_code,
)


async def repair_script(runtime: Any, payload: dict[str, Any]) -> dict[str, Any]:
    script_id = str(payload.get("script_id") or "").strip()
    failed_execution_id = str(payload.get("failed_execution_id") or "").strip()
    patched_code = str(payload.get("patched_code") or "").strip()
    if not script_id or not failed_execution_id or not patched_code:
        return {
            "status": "failed",
            "new_version_id": None,
            "promoted": False,
            "error": {
                "type": "PARAMS_VALIDATION_ERROR",
                "message": "script_id, failed_execution_id and patched_code are required",
            },
        }

    patched_code = _normalize_loop_guard_lambda(patched_code)
    if _looks_like_javascript(patched_code):
        patched_code = _safe_nanoscript_fallback_code()

    selector_manifest = _normalize_selector_manifest(payload.get("patched_selector_manifest"))
    if selector_manifest is not None and not isinstance(selector_manifest, dict):
        return {
            "status": "failed",
            "new_version_id": None,
            "promoted": False,
            "error": {
                "type": "PARAMS_VALIDATION_ERROR",
                "message": "patched_selector_manifest must be an object",
            },
        }
    if selector_manifest is not None and not all(
        isinstance(value, list) and all(isinstance(item, str) for item in value) for value in selector_manifest.values()
    ):
        return {
            "status": "failed",
            "new_version_id": None,
            "promoted": False,
            "error": {
                "type": "PARAMS_VALIDATION_ERROR",
                "message": "patched_selector_manifest must be an object of string -> string[]",
            },
        }

    test_cases = payload.get("test_cases")
    if test_cases is not None and not isinstance(test_cases, list):
        return {
            "status": "failed",
            "new_version_id": None,
            "promoted": False,
            "error": {"type": "PARAMS_VALIDATION_ERROR", "message": "test_cases must be an array"},
        }

    return await runtime.repair.repair(
        script_id=script_id,
        failed_execution_id=failed_execution_id,
        patched_code=patched_code,
        patched_selector_manifest=selector_manifest,
        changelog=str(payload.get("changelog") or "repair candidate"),
        test_cases=test_cases,
    )
