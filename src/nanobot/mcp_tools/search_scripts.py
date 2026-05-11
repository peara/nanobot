from __future__ import annotations

from typing import Any


def _required_params(params_schema: dict[str, Any]) -> list[str]:
    required = params_schema.get("required", [])
    if not isinstance(required, list):
        return []
    return [str(item) for item in required if isinstance(item, str)]


def _missing_params(required: list[str], params: dict[str, Any]) -> list[str]:
    return [name for name in required if name not in params]


def _invoke_example(
    script_id: str,
    version_id: str,
    params_schema: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    example_params = dict(params)
    properties = params_schema.get("properties", {}) if isinstance(params_schema, dict) else {}
    for name in _required_params(params_schema):
        if name in example_params:
            continue
        prop = properties.get(name, {}) if isinstance(properties, dict) else {}
        if name == "url" or prop.get("format") == "uri":
            example_params[name] = "https://example.com"
        elif prop.get("type") == "integer":
            example_params[name] = 10
        elif prop.get("type") == "number":
            example_params[name] = 1
        elif prop.get("type") == "boolean":
            example_params[name] = True
        elif prop.get("type") == "array":
            example_params[name] = []
        elif prop.get("type") == "object":
            example_params[name] = {}
        else:
            example_params[name] = f"<{name}>"
    return {
        "tool": "web__invoke_script",
        "arguments": {
            "script_id": script_id,
            "version_id": version_id,
            "params": example_params,
        },
    }


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
            _candidate_payload(runtime, candidate, params)
            for candidate in candidates
        ]
    }


def _candidate_payload(runtime: Any, candidate: Any, params: dict[str, Any]) -> dict[str, Any]:
    record = runtime.registry.get_script_version(candidate.script_id, version_id=candidate.version_id)
    if record is None:
        return {
            "script_id": candidate.script_id,
            "version_id": candidate.version_id,
            "score": round(candidate.score, 4),
            "reason": candidate.reason,
        }

    required = _required_params(record.params_schema)
    return {
        "script_id": candidate.script_id,
        "version_id": candidate.version_id,
        "name": record.script_name,
        "description": record.description,
        "domain": record.domain,
        "task_type": record.task_type,
        "score": round(candidate.score, 4),
        "reason": candidate.reason,
        "params_schema": record.params_schema,
        "output_schema": record.output_schema,
        "required_params": required,
        "missing_params": _missing_params(required, params),
        "invoke_example": _invoke_example(candidate.script_id, candidate.version_id, record.params_schema, params),
    }
