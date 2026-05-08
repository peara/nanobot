from __future__ import annotations

from typing import Any


def search_scripts(runtime: Any, payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    if not query:
        return {
            "status": "failed",
            "error": {"type": "PARAMS_VALIDATION_ERROR", "message": "query is required"},
        }

    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    limit = int(payload.get("limit", 5))

    candidates = runtime.registry.search_scripts(query, params, limit=limit)
    return {
        "candidates": [
            {
                "script_id": candidate.script_id,
                "version_id": candidate.version_id,
                "score": round(candidate.score, 4),
                "reason": candidate.reason,
            }
            for candidate in candidates
        ]
    }
