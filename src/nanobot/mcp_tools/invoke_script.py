from __future__ import annotations

from typing import Any


def _failed_payload(error_type: str, message: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "confidence": 0.0,
        "result": None,
        "execution_id": None,
        "error": {"type": error_type, "message": message},
    }


async def invoke_script(runtime: Any, payload: dict[str, Any]) -> dict[str, Any]:
    script_id = str(payload.get("script_id") or "").strip()
    if not script_id:
        return _failed_payload("PARAMS_VALIDATION_ERROR", "script_id is required")

    params = payload.get("params")
    if not isinstance(params, dict):
        return _failed_payload("PARAMS_VALIDATION_ERROR", "params must be an object")

    version_id_raw = payload.get("version_id")
    version_id = str(version_id_raw) if isinstance(version_id_raw, str) and version_id_raw.strip() else None

    result = await runtime.executor.invoke(script_id, params, version_id=version_id)
    repair_on_failure = bool(payload.get("repair_on_failure", False))
    if result.get("status") != "failed" or not repair_on_failure:
        return result

    patched_code = payload.get("patched_code")
    if isinstance(patched_code, str) and patched_code.strip():
        repair_result = await runtime.repair.repair(
            script_id=script_id,
            failed_execution_id=str(result.get("execution_id")),
            patched_code=patched_code,
            patched_selector_manifest=payload.get("patched_selector_manifest"),
            changelog=str(payload.get("changelog") or "auto-repair candidate"),
            test_cases=[{"params": params}],
        )
        result["repair"] = repair_result
        return result

    result["repair"] = {
        "status": "candidate_created",
        "promoted": False,
        "message": "repair_on_failure requested; provide patched_code via web__repair_script",
        "failed_execution_id": result.get("execution_id"),
    }
    return result
