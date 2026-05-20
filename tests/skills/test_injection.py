from __future__ import annotations

from typing import Any

from nanobot.skills.injection import build_skill_catalog_message, build_skill_messages, build_tool_catalog_message
from nanobot.skills.models import Skill
from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry


class _FakeTool(Tool):
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


def _make_registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_FakeTool(name))
    return registry


class TestBuildToolCatalogMessageGroupsByNamespace:
    def test_groups_by_double_underscore_prefix(self) -> None:
        registry = _make_registry("memory__search", "memory__save", "plan__get")
        result = build_tool_catalog_message(registry)
        assert result is not None
        content = result["content"]
        lines = content.split("\n")
        assert lines[0] == "Available tools:"
        # Find memory line and plan line
        memory_line = next(line for line in lines if line.startswith("- memory:"))
        plan_line = next(line for line in lines if line.startswith("- plan:"))
        assert "memory__search" in memory_line
        assert "memory__save" in memory_line
        assert "plan__get" in plan_line

    def test_tools_without_namespace_go_ungrouped(self) -> None:
        registry = _make_registry("status")
        result = build_tool_catalog_message(registry)
        assert result is not None
        content = result["content"]
        assert "- status: status" in content


class TestBuildToolCatalogMessageReturnsNoneForEmpty:
    def test_empty_registry_returns_none(self) -> None:
        registry = ToolRegistry()
        assert build_tool_catalog_message(registry) is None


class TestBuildToolCatalogMessageSortsGroupsAndTools:
    def test_groups_sorted_alphabetically(self) -> None:
        registry = _make_registry("web__search", "memory__save", "plan__get")
        result = build_tool_catalog_message(registry)
        assert result is not None
        lines = result["content"].split("\n")[1:]  # skip header
        prefixes = [line.split(":")[0].strip("- ") for line in lines]
        assert prefixes == sorted(prefixes)

    def test_tools_within_group_sorted_alphabetically(self) -> None:
        registry = _make_registry("memory__save", "memory__delete", "memory__health", "memory__search")
        result = build_tool_catalog_message(registry)
        assert result is not None
        lines = result["content"].split("\n")
        memory_line = next(line for line in lines if line.startswith("- memory:"))
        tools = [t.strip() for t in memory_line.split(": ", 1)[1].split(",")]
        assert tools == sorted(tools)
        assert tools == ["memory__delete", "memory__health", "memory__save", "memory__search"]


class TestBuildToolCatalogMessageSystemMessageFormat:
    def test_role_is_system(self) -> None:
        registry = _make_registry("memory__search")
        result = build_tool_catalog_message(registry)
        assert result is not None
        assert result["role"] == "system"

    def test_content_starts_with_header(self) -> None:
        registry = _make_registry("memory__search")
        result = build_tool_catalog_message(registry)
        assert result is not None
        assert result["content"].startswith("Available tools:")

    def test_format_matches_expected_pattern(self) -> None:
        registry = _make_registry(
            "core__session_scratchpad",
            "memory__search",
            "memory__save",
            "timer__now",
        )
        result = build_tool_catalog_message(registry)
        assert result is not None
        content = result["content"]
        assert "- core: core__session_scratchpad" in content
        assert "- memory: memory__save, memory__search" in content
        assert "- timer: timer__now" in content


class TestBuildSkillCatalogMessageExistingCode:
    def test_existing_skill_catalog_still_works(self) -> None:
        skills = [
            Skill(id=1, name="test-skill", description="A test skill", instructions="Do things", trigger_mode="always"),
        ]
        result = build_skill_catalog_message(skills)
        assert result is not None
        assert result["role"] == "system"
        assert "test-skill" in result["content"]

    def test_skill_catalog_returns_none_for_empty(self) -> None:
        assert build_skill_catalog_message([]) is None


class TestBuildSkillMessagesExistingCode:
    def test_existing_skill_messages_still_works(self) -> None:
        from unittest.mock import MagicMock

        skills = [
            Skill(id=1, name="test-skill", description="A test skill", instructions="Do things", trigger_mode="always"),
        ]
        prompts = MagicMock()
        prompts.render.return_value = "[Skill: test-skill]\nA test skill\n\nDo things"
        result = build_skill_messages(skills, prompts)
        assert len(result) == 1
        assert result[0]["role"] == "system"

    def test_skill_messages_returns_empty_for_no_skills(self) -> None:
        from unittest.mock import MagicMock

        result = build_skill_messages([], MagicMock())
        assert result == []
