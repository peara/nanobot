from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QualityAssessment:
    """Phase 1 output: quality score and learning gate."""

    quality_score: int
    quality_reason: str
    has_learnings: bool
    confidence: str


# JSON Schema for structured output (OpenAI-compatible)
QUALITY_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "quality_assessment",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "quality_score": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Quality score from 1 (failed) to 5 (excellent)",
                },
                "quality_reason": {
                    "type": "string",
                    "description": "Brief explanation of the quality score (1-2 sentences)",
                },
                "has_learnings": {
                    "type": "boolean",
                    "description": "True if there are learnings worth extracting for skill creation",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Confidence in the assessment",
                },
            },
            "required": ["quality_score", "quality_reason", "has_learnings", "confidence"],
            "additionalProperties": False,
        },
    },
}


def parse_quality_assessment(response: dict[str, Any]) -> QualityAssessment:
    """Parse LLM response dict into QualityAssessment."""
    score = int(response["quality_score"])
    if not 1 <= score <= 5:
        raise ValueError(f"quality_score must be 1-5, got {score}")

    confidence = str(response["confidence"])
    if confidence not in ("high", "medium", "low"):
        raise ValueError(f"confidence must be high/medium/low, got {confidence}")

    return QualityAssessment(
        quality_score=score,
        quality_reason=str(response["quality_reason"]),
        has_learnings=bool(response["has_learnings"]),
        confidence=confidence,
    )


def parse_quality_from_json(content: str) -> QualityAssessment:
    """Parse JSON string from LLM response into QualityAssessment."""
    data = json.loads(content)
    return parse_quality_assessment(data)
