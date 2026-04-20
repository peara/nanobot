from __future__ import annotations

import pytest

from nanobot.evaluator.store import (
    QUALITY_ASSESSMENT_SCHEMA,
    QualityAssessment,
    parse_quality_assessment,
    parse_quality_from_json,
)


class TestQualityAssessment:
    def test_creation(self) -> None:
        qa = QualityAssessment(
            quality_score=4,
            quality_reason="Good response with minor gaps",
            has_learnings=True,
            confidence="high",
        )
        assert qa.quality_score == 4
        assert qa.has_learnings is True
        assert qa.confidence == "high"

    def test_frozen(self) -> None:
        qa = QualityAssessment(
            quality_score=3,
            quality_reason="Acceptable",
            has_learnings=False,
            confidence="medium",
        )
        with pytest.raises(AttributeError):
            qa.quality_score = 5  # type: ignore[misc]


class TestParseQualityAssessment:
    def test_parse_from_dict(self) -> None:
        data = {
            "quality_score": 5,
            "quality_reason": "Excellent answer",
            "has_learnings": False,
            "confidence": "high",
        }
        qa = parse_quality_assessment(data)
        assert qa.quality_score == 5
        assert qa.has_learnings is False

    def test_parse_from_json_string(self) -> None:
        json_str = '{"quality_score": 2, "quality_reason": "Poor", "has_learnings": true, "confidence": "low"}'
        qa = parse_quality_from_json(json_str)
        assert qa.quality_score == 2
        assert qa.has_learnings is True
        assert qa.confidence == "low"

    def test_parse_invalid_score(self) -> None:
        data = {
            "quality_score": 6,
            "quality_reason": "Invalid",
            "has_learnings": False,
            "confidence": "high",
        }
        with pytest.raises(ValueError, match="must be 1-5"):
            parse_quality_assessment(data)

    def test_parse_invalid_confidence(self) -> None:
        data = {
            "quality_score": 3,
            "quality_reason": "Test",
            "has_learnings": False,
            "confidence": "invalid",
        }
        with pytest.raises(ValueError, match="must be high/medium/low"):
            parse_quality_assessment(data)


class TestQualityAssessmentSchema:
    def test_schema_structure(self) -> None:
        schema = QUALITY_ASSESSMENT_SCHEMA
        assert schema["type"] == "json_schema"
        assert schema["json_schema"]["name"] == "quality_assessment"
        assert schema["json_schema"]["strict"] is True

        props = schema["json_schema"]["schema"]["properties"]
        assert "quality_score" in props
        assert "quality_reason" in props
        assert "has_learnings" in props
        assert "confidence" in props

    def test_score_bounds(self) -> None:
        props = QUALITY_ASSESSMENT_SCHEMA["json_schema"]["schema"]["properties"]
        assert props["quality_score"]["minimum"] == 1
        assert props["quality_score"]["maximum"] == 5

    def test_confidence_enum(self) -> None:
        props = QUALITY_ASSESSMENT_SCHEMA["json_schema"]["schema"]["properties"]
        assert props["confidence"]["enum"] == ["high", "medium", "low"]

    def test_additional_properties_false(self) -> None:
        schema = QUALITY_ASSESSMENT_SCHEMA["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
