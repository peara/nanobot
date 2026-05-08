from __future__ import annotations

from nanobot.scripts.output_validator import validate_output

SCHEMA = {
    "type": "object",
    "required": ["issues"],
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "url"],
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                },
            },
        }
    },
}


def test_schema_valid_output_success() -> None:
    output = {"issues": [{"title": "Issue 1", "url": "https://github.com/org/repo/issues/1"}]}
    result = validate_output(
        output,
        SCHEMA,
        [],
        used_primary_selectors=True,
        historical_success_rate=0.9,
        recent_failure_rate=0.0,
    )
    assert result.status == "success"
    assert result.confidence >= 0.6


def test_schema_invalid_output_failed() -> None:
    output = {"issues": [{"title": "Issue 1"}]}
    result = validate_output(
        output,
        SCHEMA,
        [],
        used_primary_selectors=True,
        historical_success_rate=0.9,
        recent_failure_rate=0.0,
    )
    assert result.status == "failed"
    assert result.schema_errors


def test_empty_output_is_suspicious() -> None:
    output = {"issues": []}
    result = validate_output(
        output,
        SCHEMA,
        [],
        used_primary_selectors=True,
        historical_success_rate=0.9,
        recent_failure_rate=0.0,
    )
    assert result.status == "suspicious"


def test_duplicate_heavy_output_is_suspicious() -> None:
    output = {
        "issues": [
            {"title": "Issue 1", "url": "https://github.com/org/repo/issues/1"},
            {"title": "Issue 1", "url": "https://github.com/org/repo/issues/1"},
            {"title": "Issue 1", "url": "https://github.com/org/repo/issues/1"},
        ]
    }
    result = validate_output(
        output,
        SCHEMA,
        [],
        used_primary_selectors=False,
        historical_success_rate=0.2,
        recent_failure_rate=0.5,
    )
    assert result.status == "suspicious"
    assert "duplicate_heavy_output" in result.warnings
