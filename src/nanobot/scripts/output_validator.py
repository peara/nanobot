from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from nanobot.scripts.schemas import validate_data_against_schema


@dataclass
class OutputValidationResult:
    status: str
    confidence: float
    warnings: list[str]
    schema_errors: list[str]


def validate_output(
    output: dict[str, Any],
    output_schema: dict[str, Any],
    validation_rules: list[dict[str, Any]] | None,
    *,
    used_primary_selectors: bool,
    historical_success_rate: float,
    recent_failure_rate: float,
) -> OutputValidationResult:
    warnings: list[str] = []
    schema_errors = validate_data_against_schema(output, output_schema)

    confidence = 0.5
    if not schema_errors:
        confidence += 0.2
    else:
        confidence -= 0.3

    total_items = _estimate_item_count(output)
    if total_items > 0:
        confidence += 0.1
    else:
        warnings.append("empty_output")
        confidence -= 0.3

    if used_primary_selectors:
        confidence += 0.1
    else:
        confidence -= 0.1

    if historical_success_rate >= 0.7:
        confidence += 0.1

    semantic_passed, semantic_warnings = _run_semantic_checks(output, validation_rules or [])
    warnings.extend(semantic_warnings)
    if semantic_passed:
        confidence += 0.1
    else:
        confidence -= 0.1

    duplicate_ratio = _duplicate_ratio(output)
    if duplicate_ratio >= 0.5:
        warnings.append("duplicate_heavy_output")
        confidence -= 0.1

    if recent_failure_rate >= 0.4:
        confidence -= 0.1

    confidence = max(0.0, min(1.0, confidence))

    if schema_errors:
        status = "failed"
    elif confidence < 0.6 or warnings:
        status = "suspicious"
    else:
        status = "success"

    return OutputValidationResult(
        status=status,
        confidence=confidence,
        warnings=warnings,
        schema_errors=schema_errors,
    )


def _estimate_item_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        total = 0
        for value in payload.values():
            total += _estimate_item_count(value)
        return total
    return 0


def _duplicate_ratio(payload: dict[str, Any]) -> float:
    arrays = [value for value in payload.values() if isinstance(value, list)]
    if not arrays:
        return 0.0

    highest = 0.0
    for items in arrays:
        if not items:
            continue
        serialized = [json.dumps(item, sort_keys=True, ensure_ascii=True) for item in items]
        unique = len(set(serialized))
        ratio = 1.0 - (unique / len(serialized))
        highest = max(highest, ratio)
    return highest


def _run_semantic_checks(output: dict[str, Any], rules: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    passed = True

    # Generic URL field validation
    for key, value in _flatten(output):
        if "url" in key.lower() and isinstance(value, str):
            if not _looks_like_url(value):
                passed = False
                warnings.append(f"invalid_url:{key}")

    for rule in rules:
        if rule.get("type") == "require_non_empty":
            field = str(rule.get("field", ""))
            value = output.get(field)
            if isinstance(value, list) and not value:
                passed = False
                warnings.append(f"required_non_empty:{field}")

    return passed, warnings


def _flatten(payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            next_key = f"{prefix}.{key}" if prefix else key
            items.extend(_flatten(value, next_key))
    elif isinstance(payload, list):
        for idx, value in enumerate(payload):
            next_key = f"{prefix}[{idx}]"
            items.extend(_flatten(value, next_key))
    else:
        items.append((prefix, payload))
    return items


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)
