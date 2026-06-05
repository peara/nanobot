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
            "category": "workflow_pattern",
            "observation": "test",
            "direction": "invalid",
            "evidence": "test",
            "confidence": "high",
        }
        with pytest.raises(ValueError, match="invalid direction"):
            parse_learning_item(data)

    def test_parse_invalid_confidence(self) -> None:
        data = {
            "category": "workflow_pattern",
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
                        "category": "workflow_pattern",
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

    def test_parse_markdown_wrapped_json(self) -> None:
        # Regression: gemma4:31b-cloud (Ollama Cloud) wraps structured output in
        # ```json fences despite response_format: json_schema strict mode.
        fenced = (
            "```json\n"
            + json.dumps(
                {
                    "learnings": [
                        {
                            "category": "constraint",
                            "observation": "Always include traceback in error responses",
                            "direction": "create_skill",
                            "evidence": "User got a generic error and asked for more detail",
                            "confidence": "high",
                        }
                    ],
                }
            )
            + "\n```"
        )
        extraction = parse_learning_from_json(fenced)
        assert len(extraction.learnings) == 1
        assert extraction.learnings[0].observation.startswith("Always include traceback")

    def test_parse_bare_markdown_fence(self) -> None:
        # Some LLMs use bare ``` without language tag.
        fenced = "```\n" + json.dumps({"learnings": []}) + "\n```"
        extraction = parse_learning_from_json(fenced)
        assert len(extraction.learnings) == 0

    def test_parse_bare_list_root(self) -> None:
        # Regression: gemma4:31b-cloud (Ollama Cloud) returns a bare JSON array
        # at the root despite response_format: json_schema strict. Parser must
        # return empty extraction instead of crashing with AttributeError.
        content = json.dumps(
            [
                {
                    "category": "workflow_pattern",
                    "observation": "X",
                    "direction": "create_skill",
                    "evidence": "Y",
                    "confidence": "high",
                },
            ]
        )
        extraction = parse_learning_from_json(content)
        assert len(extraction.learnings) == 0

    def test_parse_markdown_fenced_bare_list(self) -> None:
        # Regression: bare list wrapped in ```json fences — the actual shape
        # seen in production log data/nanobot.log on 2026-06-03.
        content = (
            "```json\n"
            + json.dumps(
                [
                    {
                        "category": "user_preference",
                        "observation": "X",
                        "direction": "create_skill",
                        "evidence": "Y",
                        "confidence": "high",
                    },
                ]
            )
            + "\n```"
        )
        extraction = parse_learning_from_json(content)
        assert len(extraction.learnings) == 0

    def test_parse_object_wrapped_empty_learnings(self) -> None:
        # The schema-correct empty form: {"learnings": []}.
        content = json.dumps({"learnings": []})
        extraction = parse_learning_from_json(content)
        assert len(extraction.learnings) == 0


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

        assert item_props["category"]["enum"] == ["workflow_pattern", "constraint"]
        assert item_props["direction"]["enum"] == ["create_skill", "update_skill", "deprecate_skill"]
        assert item_props["confidence"]["enum"] == ["high", "medium", "low"]

    def test_strict_schema(self) -> None:
        schema_obj = LEARNING_EXTRACTION_SCHEMA["json_schema"]["schema"]
        assert schema_obj["additionalProperties"] is False

        item_schema = LEARNING_EXTRACTION_SCHEMA["json_schema"]["schema"]["properties"]["learnings"]["items"]
        assert item_schema["additionalProperties"] is False


class TestInvalidCategoryRejected:
    """The schema, parser, and extraction all reject categories outside the enum.

    Tests use a concrete non-enum string as the invalid input so the
    assertions are reproducible. The behavior under test is generic — any
    category not in the schema enum is rejected.
    """

    INVALID_CATEGORY = "user_preference"

    def test_schema_enum_excludes_invalid_category(self) -> None:
        schema = LEARNING_EXTRACTION_SCHEMA["json_schema"]["schema"]
        item_props = schema["properties"]["learnings"]["items"]["properties"]
        assert self.INVALID_CATEGORY not in item_props["category"]["enum"]

    def test_parser_rejects_invalid_category(self) -> None:
        data = {
            "category": self.INVALID_CATEGORY,
            "observation": "User likes TypeScript",
            "direction": "create_skill",
            "evidence": "User said so",
            "confidence": "high",
        }
        with pytest.raises(ValueError, match="invalid category"):
            parse_learning_item(data)

    def test_extraction_drops_invalid_items_but_keeps_others(self) -> None:
        # The LLM might emit one bad item alongside good ones (prompt drift).
        # The extractor should drop the bad one and keep the good ones.
        data = {
            "learnings": [
                {
                    "category": self.INVALID_CATEGORY,
                    "observation": "User likes dark mode",
                    "direction": "create_skill",
                    "evidence": "User said so",
                    "confidence": "high",
                },
                {
                    "category": "workflow_pattern",
                    "observation": "Search selector X then click Y on site Z",
                    "direction": "create_skill",
                    "evidence": "Discovered mid-run",
                    "confidence": "high",
                },
            ],
        }
        extraction = parse_learning_extraction(data)
        assert len(extraction.learnings) == 1
        assert extraction.learnings[0].category == "workflow_pattern"

    def test_from_json_drops_invalid_items_but_keeps_others(self) -> None:
        json_str = json.dumps(
            {
                "learnings": [
                    {
                        "category": self.INVALID_CATEGORY,
                        "observation": "User likes dark mode",
                        "direction": "create_skill",
                        "evidence": "User said so",
                        "confidence": "high",
                    },
                    {
                        "category": "constraint",
                        "observation": "Must use Python 3.11+",
                        "direction": "create_skill",
                        "evidence": "User specified",
                        "confidence": "high",
                    },
                ],
            }
        )
        extraction = parse_learning_from_json(json_str)
        assert len(extraction.learnings) == 1
        assert extraction.learnings[0].category == "constraint"
