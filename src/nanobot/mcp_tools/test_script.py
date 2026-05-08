from __future__ import annotations

from typing import Any


async def test_script(runtime: Any, payload: dict[str, Any]) -> dict[str, Any]:
    script_id = str(payload.get("script_id") or "").strip()
    version_id = str(payload.get("version_id") or "").strip()
    if not script_id or not version_id:
        return {
            "status": "failed",
            "error": {"type": "PARAMS_VALIDATION_ERROR", "message": "script_id and version_id are required"},
        }

    cases = payload.get("test_cases")
    if not isinstance(cases, list) or not cases:
        return {
            "status": "failed",
            "error": {"type": "PARAMS_VALIDATION_ERROR", "message": "test_cases must be a non-empty array"},
        }

    case_results: list[dict[str, Any]] = []
    for case in cases:
        params = case.get("params") if isinstance(case, dict) else {}
        if not isinstance(params, dict):
            params = {}
        result = await runtime.executor.invoke(script_id, params, version_id=version_id)
        case_results.append(
            {
                "status": result.get("status"),
                "confidence": float(result.get("confidence", 0.0)),
                "error": result.get("error"),
                "execution_id": result.get("execution_id"),
            }
        )

    passed = all(case["status"] != "failed" for case in case_results)
    should_promote = bool(payload.get("promote_on_pass", False))
    promoted = False
    if passed and should_promote:
        runtime.registry.promote_version(script_id, version_id)
        promoted = True

    return {
        "status": "passed" if passed else "failed",
        "cases": case_results,
        "promoted": promoted,
    }
