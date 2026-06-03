from __future__ import annotations

import json
from typing import Any, cast

import pytest

from nanobot.evaluator import LearningEvaluator, LearningItem
from nanobot.evaluator.store import (
    SKILL_LIFECYCLE_SCHEMA,
    EvaluationResult,
    QualityAssessment,
    SkillOperation,
    parse_lifecycle_from_json,
    parse_skill_operation,
)
from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry


class TestSkillOperation:
    def test_creation_with_tools_allowlist(self) -> None:
        op = SkillOperation(
            action="create",
            name="web_research",
            description="Research web topics",
            instructions="Use web tools for research",
            trigger_mode="pattern",
            tools_allowlist=["web__*", "playwright__*"],
            source_confidence="high",
            reason="User needs web research",
        )
        assert op.tools_allowlist == ["web__*", "playwright__*"]

    def test_creation_without_tools_allowlist(self) -> None:
        op = SkillOperation(
            action="create",
            name="pref_concise",
            description="User prefers concise",
            instructions="Be brief",
            trigger_mode="intelligent",
            source_confidence="high",
            reason="User preference",
        )
        assert op.tools_allowlist is None

    def test_frozen(self) -> None:
        op = SkillOperation(
            action="skip",
            name="test_skill",
            description="Test",
            instructions="Test",
            trigger_mode="pattern",
            source_confidence="low",
            reason="Testing",
        )
        with pytest.raises(AttributeError):
            op.action = "create"  # type: ignore[misc]


class TestEvaluationResult:
    def test_creation_with_decisions(self) -> None:
        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Good response",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="create",
                name="pref_concise",
                description="User prefers concise answers",
                instructions="Keep responses brief",
                trigger_mode="intelligent",
                source_confidence="high",
                reason="User said 'keep it brief'",
            ),
        ]
        result = EvaluationResult(quality=quality, decisions=decisions)
        assert result.quality.quality_score == 4
        assert len(result.decisions) == 1
        assert result.decisions[0].name == "pref_concise"
        assert result.decisions[0].tools_allowlist is None

    def test_creation_without_decisions(self) -> None:
        quality = QualityAssessment(
            quality_score=3,
            quality_reason="Acceptable",
            has_learnings=False,
            confidence="medium",
        )
        result = EvaluationResult(quality=quality)
        assert result.quality.has_learnings is False
        assert len(result.decisions) == 0


class TestParseSkillOperation:
    def test_parse_valid_operation_with_tools_allowlist(self) -> None:
        data = {
            "action": "create",
            "name": "web_research",
            "description": "Research web topics",
            "instructions": "Use web tools",
            "trigger_mode": "pattern",
            "tools_allowlist": ["web__*", "playwright__*"],
            "source_confidence": "high",
            "reason": "User needs web research",
        }
        op = parse_skill_operation(data)
        assert op.action == "create"
        assert op.name == "web_research"
        assert op.trigger_mode == "pattern"
        assert op.source_confidence == "high"
        assert op.tools_allowlist == ["web__*", "playwright__*"]

    def test_parse_operation_without_tools_allowlist(self) -> None:
        data = {
            "action": "create",
            "name": "test_skill",
            "description": "Test",
            "instructions": "Test",
            "trigger_mode": "intelligent",
            "tools_allowlist": None,
            "source_confidence": "high",
            "reason": "Test",
        }
        op = parse_skill_operation(data)
        assert op.tools_allowlist is None

    def test_parse_operation_with_empty_tools_allowlist(self) -> None:
        data = {
            "action": "create",
            "name": "test_skill",
            "description": "Test",
            "instructions": "Test",
            "trigger_mode": "intelligent",
            "tools_allowlist": [],
            "source_confidence": "high",
            "reason": "Test",
        }
        op = parse_skill_operation(data)
        # Empty list is normalized to None ("no opinion") to prevent
        # wiping existing allowlists on update.
        assert op.tools_allowlist is None

    def test_parse_deprecate_action(self) -> None:
        data = {
            "action": "deprecate",
            "name": "old_skill",
            "description": "No longer needed",
            "instructions": "Deprecated",
            "trigger_mode": "pattern",
            "tools_allowlist": None,
            "source_confidence": "high",
            "reason": "Skill is obsolete",
        }
        op = parse_skill_operation(data)
        assert op.action == "deprecate"
        assert op.name == "old_skill"

    def test_parse_invalid_action(self) -> None:
        data = {
            "action": "invalid",
            "name": "test_skill",
            "description": "Test",
            "instructions": "Test",
            "trigger_mode": "intelligent",
            "source_confidence": "high",
            "reason": "Test",
        }
        with pytest.raises(ValueError, match="invalid action"):
            parse_skill_operation(data)

    def test_parse_invalid_trigger_mode(self) -> None:
        data = {
            "action": "create",
            "name": "test_skill",
            "description": "Test",
            "instructions": "Test",
            "trigger_mode": "invalid",
            "source_confidence": "high",
            "reason": "Test",
        }
        with pytest.raises(ValueError, match="invalid trigger_mode"):
            parse_skill_operation(data)

    def test_parse_invalid_source_confidence(self) -> None:
        data = {
            "action": "update",
            "name": "test_skill",
            "description": "Test",
            "instructions": "Test",
            "trigger_mode": "intelligent",
            "source_confidence": "invalid",
            "reason": "Test",
        }
        with pytest.raises(ValueError, match="invalid source_confidence"):
            parse_skill_operation(data)


class TestParseLifecycleFromJson:
    def test_parse_valid_json(self) -> None:
        json_str = json.dumps(
            {
                "operations": [
                    {
                        "action": "create",
                        "name": "user_pref_dark_mode",
                        "description": "User prefers dark mode",
                        "instructions": "Use dark theme in examples",
                        "trigger_mode": "intelligent",
                        "tools_allowlist": None,
                        "source_confidence": "high",
                        "reason": "User explicitly stated preference",
                    },
                    {
                        "action": "skip",
                        "name": "temporary_context",
                        "description": "Temporary context",
                        "instructions": "Skip this",
                        "trigger_mode": "intelligent",
                        "tools_allowlist": [],
                        "source_confidence": "low",
                        "reason": "Not a persistent preference",
                    },
                ],
            }
        )
        operations = parse_lifecycle_from_json(json_str)
        assert len(operations) == 2
        assert operations[0].action == "create"
        assert operations[0].tools_allowlist is None
        assert operations[1].action == "skip"
        assert operations[1].tools_allowlist is None

    def test_parse_lifecycle_with_deprecate(self) -> None:
        json_str = json.dumps(
            {
                "operations": [
                    {
                        "action": "deprecate",
                        "name": "old_skill",
                        "description": "No longer needed",
                        "instructions": "Deprecated",
                        "trigger_mode": "pattern",
                        "tools_allowlist": None,
                        "source_confidence": "high",
                        "reason": "Skill is obsolete",
                    },
                ],
            }
        )
        operations = parse_lifecycle_from_json(json_str)
        assert len(operations) == 1
        assert operations[0].action == "deprecate"
        assert operations[0].name == "old_skill"

    def test_parse_empty_operations(self) -> None:
        json_str = '{"operations": []}'
        operations = parse_lifecycle_from_json(json_str)
        assert len(operations) == 0

    def test_parse_missing_operations_key(self) -> None:
        json_str = "{}"
        operations = parse_lifecycle_from_json(json_str)
        assert len(operations) == 0

    def test_parse_empty_string_returns_empty(self) -> None:
        operations = parse_lifecycle_from_json("")
        assert operations == []

    def test_parse_invalid_json_returns_empty(self) -> None:
        operations = parse_lifecycle_from_json("not-json")
        assert operations == []

    def test_parse_markdown_wrapped_json(self) -> None:
        json_str = """
```json
{"operations": []}
```
""".strip()
        operations = parse_lifecycle_from_json(json_str)
        assert operations == []


class TestSkillLifecycleSchema:
    def test_schema_structure(self) -> None:
        schema = SKILL_LIFECYCLE_SCHEMA
        assert schema["type"] == "json_schema"
        assert schema["json_schema"]["name"] == "skill_lifecycle"
        assert schema["json_schema"]["strict"] is True

        props = schema["json_schema"]["schema"]["properties"]
        assert "operations" in props
        assert props["operations"]["type"] == "array"

    def test_operation_item_schema(self) -> None:
        item_schema = SKILL_LIFECYCLE_SCHEMA["json_schema"]["schema"]["properties"]["operations"]["items"]
        assert item_schema["type"] == "object"

        item_props = item_schema["properties"]
        assert "action" in item_props
        assert "name" in item_props
        assert "description" in item_props
        assert "instructions" in item_props
        assert "trigger_mode" in item_props
        assert "source_confidence" in item_props
        assert "reason" in item_props

        assert item_props["action"]["enum"] == ["create", "update", "deprecate", "skip"]
        assert item_props["trigger_mode"]["enum"] == ["always", "pattern", "intelligent"]
        assert item_props["source_confidence"]["enum"] == ["high", "medium", "low"]

    def test_operation_item_schema_has_tools_allowlist(self) -> None:
        item_schema = SKILL_LIFECYCLE_SCHEMA["json_schema"]["schema"]["properties"]["operations"]["items"]
        assert "tools_allowlist" in item_schema["properties"]
        assert item_schema["properties"]["tools_allowlist"]["type"] == "array"

    def test_tools_allowlist_in_required(self) -> None:
        item_schema = SKILL_LIFECYCLE_SCHEMA["json_schema"]["schema"]["properties"]["operations"]["items"]
        assert "tools_allowlist" in item_schema["required"]

    def test_strict_schema(self) -> None:
        schema_obj = SKILL_LIFECYCLE_SCHEMA["json_schema"]["schema"]
        assert schema_obj["additionalProperties"] is False

        item_schema = SKILL_LIFECYCLE_SCHEMA["json_schema"]["schema"]["properties"]["operations"]["items"]
        assert item_schema["additionalProperties"] is False


class TestDecideLifecycle:
    @pytest.mark.asyncio
    async def test_decide_lifecycle_returns_operations(self) -> None:
        class _FakePromptStore:
            def render(self, key: str) -> str:
                raise KeyError(key)

        class _FakeLlm:
            def __init__(self, response_content: str) -> None:
                self._response_content = response_content

            async def chat(
                self,
                messages: list[dict[str, str]],
                tools: list[Any],
                response_format: dict[str, Any],
                *,
                scope: str | None = None,
                cancel_token: Any | None = None,
            ) -> dict[str, str]:
                return {"content": self._response_content}

        learnings = [
            LearningItem(
                category="user_preference",
                observation="User prefers Python over JavaScript",
                direction="create_skill",
                evidence="User said 'Use Python for all code'",
                confidence="high",
            ),
        ]

        json_response = json.dumps(
            {
                "operations": [
                    {
                        "action": "create",
                        "name": "user_pref_python",
                        "description": "User prefers Python for code",
                        "instructions": "Use Python for all code examples unless specified",
                        "trigger_mode": "intelligent",
                        "tools_allowlist": None,
                        "source_confidence": "high",
                        "reason": "User explicitly stated preference",
                    },
                ],
            }
        )

        fake_llm = _FakeLlm(json_response)
        fake_prompts = _FakePromptStore()
        evaluator = LearningEvaluator(llm=cast(Any, fake_llm), prompts=cast(Any, fake_prompts))

        operations = await evaluator._decide_lifecycle(
            scope="test:scope",
            learnings=learnings,
            active_skills=[],
        )

        assert len(operations) == 1
        assert operations[0].action == "create"
        assert operations[0].name == "user_pref_python"
        assert operations[0].trigger_mode == "intelligent"

    @pytest.mark.asyncio
    async def test_decide_lifecycle_returns_empty_on_empty_response(self) -> None:
        class _FakePromptStore:
            def render(self, key: str) -> str:
                raise KeyError(key)

        class _FakeLlm:
            async def chat(
                self,
                messages: list[dict[str, str]],
                tools: list[Any],
                response_format: dict[str, Any],
                *,
                scope: str | None = None,
                cancel_token: Any | None = None,
            ) -> dict[str, str]:
                return {"content": '{"operations": []}'}

        learnings = [
            LearningItem(
                category="constraint",
                observation="Must use UTF-8 encoding",
                direction="create_skill",
                evidence="User specified encoding",
                confidence="medium",
            ),
        ]

        fake_llm = _FakeLlm()
        fake_prompts = _FakePromptStore()
        evaluator = LearningEvaluator(llm=cast(Any, fake_llm), prompts=cast(Any, fake_prompts))

        operations = await evaluator._decide_lifecycle(
            scope="test:scope",
            learnings=learnings,
            active_skills=[],
        )

        assert len(operations) == 0


class _FakeToolForCatalog(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        return "ok"


class TestBuildLifecycleInputWithToolCatalog:
    def test_lifecycle_input_includes_tool_catalog_when_registry_provided(self) -> None:
        registry = ToolRegistry()
        registry.register(_FakeToolForCatalog("memory__search"))
        registry.register(_FakeToolForCatalog("web__search_web"))
        registry.register(_FakeToolForCatalog("web__read_page"))

        learnings = [
            LearningItem(
                category="workflow_pattern",
                observation="User searches web for restaurant recommendations",
                direction="create_skill",
                evidence="User asked to search for restaurants",
                confidence="high",
            ),
        ]

        result = LearningEvaluator._build_lifecycle_input(learnings, active_skills=[], tool_registry=registry)

        assert "Available tools:" in result
        assert "memory: memory__search" in result
        assert "web: web__read_page, web__search_web" in result

    def test_lifecycle_input_excludes_tool_catalog_when_no_registry(self) -> None:
        learnings = [
            LearningItem(
                category="user_preference",
                observation="User prefers concise answers",
                direction="create_skill",
                evidence="User said 'keep it brief'",
                confidence="high",
            ),
        ]

        result = LearningEvaluator._build_lifecycle_input(learnings, active_skills=[], tool_registry=None)

        assert "Available tools:" not in result
        assert "Extracted learnings:" in result

    def test_lifecycle_input_excludes_tool_catalog_for_empty_registry(self) -> None:
        registry = ToolRegistry()

        learnings = [
            LearningItem(
                category="user_preference",
                observation="User prefers concise answers",
                direction="create_skill",
                evidence="User said 'keep it brief'",
                confidence="high",
            ),
        ]

        result = LearningEvaluator._build_lifecycle_input(learnings, active_skills=[], tool_registry=registry)

        assert "Available tools:" not in result
