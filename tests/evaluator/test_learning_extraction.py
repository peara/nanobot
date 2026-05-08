from __future__ import annotations

import json
from typing import Any

import pytest

from nanobot.evaluator.store import (
    LEARNING_EXTRACTION_SCHEMA,
    LearningExtraction,
    LearningItem,
    parse_learning_extraction,
    parse_learning_from_json,
    parse_learning_item,
)


class TestLearningItem:
    def test_creation(self) -> None:
        item = LearningItem(
            category="user_preference",
            observation="User prefers TypeScript",
            direction="create_skill",
            evidence="User said 'Use TypeScript instead'",
            confidence="high",
        )
        assert item.category == "user_preference"
        assert item.direction == "create_skill"
        assert item.confidence == "high"

    def test_frozen(self) -> None:
        item = LearningItem(
            category="constraint",
            observation="Must use UTC",
            direction="update_skill",
            evidence="User specified timezone handling",
            confidence="medium",
        )
        with pytest.raises(AttributeError):
            item.category = "workflow_pattern"  # type: ignore[misc]


class TestLearningExtraction:
    def test_empty_learnings(self) -> None:
        extraction = LearningExtraction(learnings=[])
        assert len(extraction.learnings) == 0

    def test_with_learnings(self) -> None:
        items = [
            LearningItem(
                category="user_preference",
                observation="Prefers dark mode",
                direction="create_skill",
                evidence="Said 'dark mode please'",
                confidence="high",
            ),
        ]
        extraction = LearningExtraction(learnings=items)
        assert len(extraction.learnings) == 1


class TestParseLearningItem:
    def test_parse_valid_item(self) -> None:
        data = {
            "category": "workflow_pattern",
            "observation": "User likes step-by-step output",
            "direction": "create_skill",
            "evidence": "User asked for numbered steps",
            "confidence": "medium",
        }
        item = parse_learning_item(data)
        assert item.category == "workflow_pattern"
        assert item.direction == "create_skill"
        assert item.confidence == "medium"

    def test_parse_invalid_category(self) -> None:
        data = {
            "category": "invalid",
            "observation": "test",
            "direction": "create_skill",
            "evidence": "test",
            "confidence": "high",
        }
        with pytest.raises(ValueError, match="invalid category"):
            parse_learning_item(data)

    def test_parse_invalid_direction(self) -> None:
        data = {
            "category": "user_preference",
            "observation": "test",
            "direction": "invalid",
            "evidence": "test",
            "confidence": "high",
        }
        with pytest.raises(ValueError, match="invalid direction"):
            parse_learning_item(data)

    def test_parse_invalid_confidence(self) -> None:
        data = {
            "category": "user_preference",
            "observation": "test",
            "direction": "create_skill",
            "evidence": "test",
            "confidence": "invalid",
        }
        with pytest.raises(ValueError, match="invalid confidence"):
            parse_learning_item(data)


class TestParseLearningExtraction:
    def test_parse_from_dict(self) -> None:
        data = {
            "learnings": [
                {
                    "category": "constraint",
                    "observation": "Must use Python 3.11+",
                    "direction": "create_skill",
                    "evidence": "User specified Python version",
                    "confidence": "high",
                },
            ],
        }
        extraction = parse_learning_extraction(data)
        assert len(extraction.learnings) == 1
        assert extraction.learnings[0].category == "constraint"

    def test_parse_empty_learnings(self) -> None:
        data: dict[str, Any] = {"learnings": []}
        extraction = parse_learning_extraction(data)
        assert len(extraction.learnings) == 0

    def test_parse_missing_learnings_key(self) -> None:
        data: dict[str, Any] = {}
        extraction = parse_learning_extraction(data)
        assert len(extraction.learnings) == 0


class TestParseLearningFromJson:
    def test_parse_json_string(self) -> None:
        json_str = json.dumps(
            {
                "learnings": [
                    {
                        "category": "user_preference",
                        "observation": "Prefers concise answers",
                        "direction": "create_skill",
                        "evidence": "Said 'keep it brief'",
                        "confidence": "high",
                    },
                ],
            }
        )
        extraction = parse_learning_from_json(json_str)
        assert len(extraction.learnings) == 1
        assert extraction.learnings[0].observation == "Prefers concise answers"

    def test_parse_invalid_json_returns_empty_extraction(self) -> None:
        extraction = parse_learning_from_json("not-json")
        assert extraction.learnings == []


class TestLearningExtractionSchema:
    def test_schema_structure(self) -> None:
        schema = LEARNING_EXTRACTION_SCHEMA
        assert schema["type"] == "json_schema"
        assert schema["json_schema"]["name"] == "learning_extraction"
        assert schema["json_schema"]["strict"] is True

        props = schema["json_schema"]["schema"]["properties"]
        assert "learnings" in props
        assert props["learnings"]["type"] == "array"

    def test_learning_item_schema(self) -> None:
        item_schema = LEARNING_EXTRACTION_SCHEMA["json_schema"]["schema"]["properties"]["learnings"]["items"]
        assert item_schema["type"] == "object"

        item_props = item_schema["properties"]
        assert "category" in item_props
        assert "observation" in item_props
        assert "direction" in item_props
        assert "evidence" in item_props
        assert "confidence" in item_props

        assert item_props["category"]["enum"] == ["user_preference", "workflow_pattern", "constraint"]
        assert item_props["direction"]["enum"] == ["create_skill", "update_skill", "deprecate_skill"]
        assert item_props["confidence"]["enum"] == ["high", "medium", "low"]

    def test_strict_schema(self) -> None:
        schema_obj = LEARNING_EXTRACTION_SCHEMA["json_schema"]["schema"]
        assert schema_obj["additionalProperties"] is False

        item_schema = LEARNING_EXTRACTION_SCHEMA["json_schema"]["schema"]["properties"]["learnings"]["items"]
        assert item_schema["additionalProperties"] is False
