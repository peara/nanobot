from __future__ import annotations

import json
from typing import Any

from nanobot.core import CORE_TOOL_PATTERNS
from nanobot.skills.tools import SkillCreateTool, SkillUpdateTool
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


def _make_registry_with_tools(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_FakeTool(name))
    return registry


class TestToolRegistryPatterns:
    def test_list_tools_no_patterns_returns_all(self) -> None:
        registry = _make_registry_with_tools("memory__search", "memory__save", "plan__get")
        assert len(registry.list_tools(patterns=None)) == 3

    def test_list_tools_with_exact_match(self) -> None:
        registry = _make_registry_with_tools("memory__search", "memory__save", "plan__get")
        tools = registry.list_tools(patterns=["memory__search"])
        assert [t.name for t in tools] == ["memory__search"]

    def test_list_tools_with_wildcard_pattern(self) -> None:
        registry = _make_registry_with_tools("memory__search", "memory__save", "plan__get")
        tools = registry.list_tools(patterns=["memory__*"])
        assert [t.name for t in tools] == ["memory__search", "memory__save"]

    def test_list_tools_with_multiple_patterns(self) -> None:
        registry = _make_registry_with_tools("memory__search", "plan__get", "skill__list")
        tools = registry.list_tools(patterns=["memory__*", "plan__*"])
        assert set(t.name for t in tools) == {"memory__search", "plan__get"}

    def test_list_tools_patterns_are_union(self) -> None:
        registry = _make_registry_with_tools("memory__search", "memory__save", "timer__time_now")
        tools = registry.list_tools(patterns=["memory__search", "timer__*"])
        assert set(t.name for t in tools) == {"memory__search", "timer__time_now"}

    def test_list_openai_specs_no_patterns(self) -> None:
        registry = _make_registry_with_tools("memory__search", "plan__get")
        specs = registry.list_openai_specs(patterns=None)
        assert len(specs) == 2

    def test_list_openai_specs_with_patterns(self) -> None:
        registry = _make_registry_with_tools("memory__search", "memory__save", "plan__get")
        specs = registry.list_openai_specs(patterns=["memory__*"])
        assert len(specs) == 2
        names = [s["function"]["name"] for s in specs]
        assert set(names) == {"memory__search", "memory__save"}

    def test_pattern_matches_partial_name(self) -> None:
        registry = _make_registry_with_tools("scheduler__schedule_task", "scheduler__list_tasks")
        tools = registry.list_tools(patterns=["scheduler__*"])
        assert len(tools) == 2


class TestCoreToolPatterns:
    def test_core_patterns_is_a_list_of_strings(self) -> None:
        assert isinstance(CORE_TOOL_PATTERNS, list)
        assert all(isinstance(p, str) for p in CORE_TOOL_PATTERNS)

    def test_core_patterns_contains_essential_tools(self) -> None:
        assert "memory__search" in CORE_TOOL_PATTERNS
        assert "memory__save" in CORE_TOOL_PATTERNS
        assert "skill__list" in CORE_TOOL_PATTERNS
        assert "skill__get" in CORE_TOOL_PATTERNS
        assert "plan__get" in CORE_TOOL_PATTERNS
        assert "plan__list" in CORE_TOOL_PATTERNS
        assert "timer__*" in CORE_TOOL_PATTERNS
        assert "scheduler__*" in CORE_TOOL_PATTERNS

    def test_core_patterns_does_not_include_scratchpad(self) -> None:
        assert "session__scratchpad_write" not in CORE_TOOL_PATTERNS

    def test_core_patterns_count_under_20(self) -> None:
        registry = _make_registry_with_tools(
            "memory__search",
            "memory__save",
            "memory__save_turn",
            "skill__list",
            "skill__get",
            "plan__get",
            "plan__list",
            "timer__time_now",
            "timer__time_epoch",
            "scheduler__schedule_task",
            "scheduler__list_tasks",
            "scheduler__delete_task",
            "scheduler__pause_task",
            "scheduler__resume_task",
            "scheduler__cron_list",
            "scheduler__cron_add",
            "scheduler__cron_remove",
            "memory__delete",
            "memory__update",
            "memory__health",
            "skill__create",
            "skill__update",
            "skill__activate",
            "skill__delete",
            "plan__update",
            "plan__add_step",
            "plan__edit_step",
        )
        tools = registry.list_tools(patterns=CORE_TOOL_PATTERNS)
        assert len(tools) <= 20


class TestListOpenaiToolsWithSkills:
    def _make_bot_core(self, tmp_path, registry_with_tools: list[str] | None = None) -> Any:
        from pathlib import Path

        from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
        from nanobot.core import BotCore

        db_path = str(Path(tmp_path) / "nanobot.db")
        scheduler_db_path = str(Path(tmp_path) / "scheduler.db")
        plan_db_path = str(Path(tmp_path) / "plans.db")
        prompt_db_path = str(Path(tmp_path) / "prompts.db")
        skill_db_path = str(Path(tmp_path) / "skills.db")

        config = AppConfig(
            assistant_name="Test",
            database_path=db_path,
            scheduler_db_path=scheduler_db_path,
            plan_db_path=plan_db_path,
            skill_db_path=skill_db_path,
            poll_interval_seconds=20,
            working_timezone="UTC",
            history_message_limit=24,
            history_char_limit=12000,
            model=ModelConfig(base_url="http://localhost:11434/v1", api_key="test", model="test"),
            channels=[ChannelConfig(type="telegram")],
            mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
            prompt_db_path=prompt_db_path,
        )

        class _FakeChannel:
            async def send(self, chat_id: str, text: str) -> None:
                pass

        bot = BotCore(config=config, channels={"telegram": _FakeChannel()})
        if registry_with_tools:
            for name in registry_with_tools:
                bot.tools.register(_FakeTool(name))
        return bot

    def test_no_skills_returns_core_tools_plus_scratchpad(self, tmp_path: Any) -> None:
        bot = self._make_bot_core(
            tmp_path,
            [
                "memory__search",
                "memory__save",
                "plan__get",
                "skill__list",
                "skill__get",
                "timer__time_now",
                "web__search_web",
                "web__read_page",
            ],
        )
        tools = bot._list_openai_tools(skill_names=None)
        tool_names = [t["function"]["name"] for t in tools]
        assert "session__scratchpad_write" in tool_names
        assert "memory__search" in tool_names
        assert "memory__save" in tool_names
        assert "plan__get" in tool_names
        assert "timer__time_now" in tool_names
        assert "web__search_web" not in tool_names
        assert "web__read_page" not in tool_names

    def test_skill_with_allowlist_adds_tools(self, tmp_path: Any) -> None:
        bot = self._make_bot_core(
            tmp_path,
            [
                "memory__search",
                "memory__save",
                "plan__get",
                "web__search_web",
                "web__read_page",
            ],
        )
        bot.skills.create(
            name="web-browsing",
            description="Browse the web",
            instructions="Use web tools",
            trigger_mode="pattern",
            tools_allowlist=["web__*"],
        )
        tools = bot._list_openai_tools(skill_names=["web-browsing"])
        tool_names = [t["function"]["name"] for t in tools]
        assert "memory__search" in tool_names
        assert "web__search_web" in tool_names
        assert "web__read_page" in tool_names

    def test_skill_with_no_allowlist_adds_no_extra_tools(self, tmp_path: Any) -> None:
        bot = self._make_bot_core(
            tmp_path,
            [
                "memory__search",
                "web__search_web",
            ],
        )
        bot.skills.create(
            name="some-skill",
            description="A skill",
            instructions="Do things",
            trigger_mode="pattern",
        )
        tools = bot._list_openai_tools(skill_names=["some-skill"])
        tool_names = [t["function"]["name"] for t in tools]
        assert "memory__search" in tool_names
        assert "web__search_web" not in tool_names

    def test_inactive_skill_ignored(self, tmp_path: Any) -> None:
        bot = self._make_bot_core(
            tmp_path,
            [
                "memory__search",
                "web__search_web",
            ],
        )
        bot.skills.create(
            name="web-browsing",
            description="Browse the web",
            instructions="Use web tools",
            trigger_mode="pattern",
            tools_allowlist=["web__*"],
            is_active=False,
        )
        tools = bot._list_openai_tools(skill_names=["web-browsing"])
        tool_names = [t["function"]["name"] for t in tools]
        assert "web__search_web" not in tool_names

    def test_multiple_skills_merge_allowlists(self, tmp_path: Any) -> None:
        bot = self._make_bot_core(
            tmp_path,
            [
                "memory__search",
                "web__search_web",
                "playwright__click",
            ],
        )
        bot.skills.create(
            name="web-browsing",
            description="Browse the web",
            instructions="Use web tools",
            trigger_mode="pattern",
            tools_allowlist=["web__*"],
        )
        bot.skills.create(
            name="browser-testing",
            description="Test with browser",
            instructions="Use playwright",
            trigger_mode="pattern",
            tools_allowlist=["playwright__*"],
        )
        tools = bot._list_openai_tools(skill_names=["web-browsing", "browser-testing"])
        tool_names = [t["function"]["name"] for t in tools]
        assert "memory__search" in tool_names
        assert "web__search_web" in tool_names
        assert "playwright__click" in tool_names

    def test_nonexistent_skill_ignored(self, tmp_path: Any) -> None:
        bot = self._make_bot_core(tmp_path, ["memory__search", "web__search_web"])
        tools = bot._list_openai_tools(skill_names=["nonexistent-skill"])
        tool_names = [t["function"]["name"] for t in tools]
        assert "session__scratchpad_write" in tool_names
        assert "memory__search" in tool_names
        assert "web__search_web" not in tool_names

    def test_scratchpad_always_present(self, tmp_path: Any) -> None:
        bot = self._make_bot_core(tmp_path, ["memory__search"])
        tools = bot._list_openai_tools(skill_names=None)
        tool_names = [t["function"]["name"] for t in tools]
        assert tool_names[0] == "session__scratchpad_write"


class TestSkillToolsAllowlist:
    def test_create_skill_with_tools_allowlist(self, tmp_path: Any) -> None:
        from nanobot.skills.store import SkillStore

        store = SkillStore(str(tmp_path / "skills.db"))
        tool = SkillCreateTool(store)
        result = json.loads(
            asyncio_run(
                tool.call(
                    {
                        "name": "web-browsing",
                        "description": "Browse the web",
                        "instructions": "Use web tools",
                        "tools_allowlist": ["web__*", "playwright__*"],
                    }
                )
            )
        )
        assert result["ok"] is True
        assert result["skill"]["tools_allowlist"] == ["web__*", "playwright__*"]

    def test_update_skill_with_tools_allowlist(self, tmp_path: Any) -> None:
        from nanobot.skills.store import SkillStore

        store = SkillStore(str(tmp_path / "skills.db"))
        store.create(
            name="web-browsing",
            description="Browse the web",
            instructions="Use web tools",
        )
        tool = SkillUpdateTool(store)
        result = json.loads(
            asyncio_run(
                tool.call(
                    {
                        "name": "web-browsing",
                        "tools_allowlist": ["web__search_web", "web__read_page"],
                    }
                )
            )
        )
        assert result["ok"] is True
        assert result["skill"]["tools_allowlist"] == ["web__search_web", "web__read_page"]

    def test_create_skill_without_tools_allowlist(self, tmp_path: Any) -> None:
        from nanobot.skills.store import SkillStore

        store = SkillStore(str(tmp_path / "skills.db"))
        tool = SkillCreateTool(store)
        result = json.loads(
            asyncio_run(
                tool.call(
                    {
                        "name": "basic-skill",
                        "description": "A basic skill",
                        "instructions": "Do things",
                    }
                )
            )
        )
        assert result["ok"] is True
        assert result["skill"]["tools_allowlist"] is None


def asyncio_run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
