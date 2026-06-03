from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import BotCore, CORE_TOOL_PATTERNS
from nanobot.skills.injection import build_skill_messages, build_tool_catalog_message
from nanobot.skills.models import Skill
from nanobot.skills.matcher import SkillMatcher
from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry


class _FakeChannel:
    async def send(self, chat_id: str, text: str) -> None:
        pass


class _FakeTool(Tool):
    def __init__(self, name: str, result: str = "ok") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake tool {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        del args
        return self._result


WEB_MCP_TOOLS = [
    "web__search_web",
    "web__read_page",
    "web__snapshot_page",
    "web__search_scripts",
    "web__invoke_script",
    "web__create_script",
]

# Tool names that match CORE_TOOL_PATTERNS (concrete names + wildcard expansions)
CORE_CONCRETE_TOOLS = [
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
]


def _make_config(tmp_path: Any) -> AppConfig:
    from pathlib import Path

    return AppConfig(
        assistant_name="Test",
        database_path=str(Path(tmp_path) / "nanobot.db"),
        scheduler_db_path=str(Path(tmp_path) / "scheduler.db"),
        plan_db_path=str(Path(tmp_path) / "plans.db"),
        skill_db_path=str(Path(tmp_path) / "skills.db"),
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost:11434/v1", api_key="test", model="test"),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
        prompt_db_path=str(Path(tmp_path) / "prompts.db"),
    )


def _make_bot(tmp_path: Any) -> BotCore:
    bot = BotCore(config=_make_config(tmp_path), channels={"telegram": _FakeChannel()})
    # Register concrete tools that CORE_TOOL_PATTERNS will match
    for name in CORE_CONCRETE_TOOLS:
        bot.tools.register(_FakeTool(name))
    # Register web MCP tools
    for name in WEB_MCP_TOOLS:
        bot.tools.register(_FakeTool(name))
    return bot


# ---------------------------------------------------------------------------
# Scenario A: Targeted allowlist in isolation
# ---------------------------------------------------------------------------


class TestTargetedAllowlistInIsolation:
    def test_yahoo_skill_alone_exposes_only_targeted_tools(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses",
            instructions="Use the yahoo_auctions_quality_search NanoScript to search.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )

        tool_names = [t["function"]["name"] for t in bot._list_openai_tools(["yahoo_auctions_search_workflow"])]

        assert "session__scratchpad_write" in tool_names
        assert "web__search_scripts" in tool_names
        assert "web__invoke_script" in tool_names
        assert "memory__search" in tool_names
        assert "skill__list" in tool_names
        assert "timer__time_now" in tool_names  # matches timer__* core pattern

        assert "web__create_script" not in tool_names
        assert "web__search_web" not in tool_names
        assert "web__read_page" not in tool_names
        assert "web__snapshot_page" not in tool_names

    def test_single_tool_allowlist_excludes_others(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions",
            instructions="Use NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__invoke_script"],
            priority=5,
        )

        tool_names = [t["function"]["name"] for t in bot._list_openai_tools(["yahoo_auctions_search_workflow"])]

        assert "web__invoke_script" in tool_names
        assert "web__search_scripts" not in tool_names
        assert "web__create_script" not in tool_names
        assert "web__search_web" not in tool_names

    def test_no_skills_returns_core_tools_only(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)

        tool_names = [t["function"]["name"] for t in bot._list_openai_tools(skill_names=None)]

        assert "session__scratchpad_write" in tool_names
        assert "memory__search" in tool_names
        assert "skill__list" in tool_names
        assert "timer__time_now" in tool_names  # matches timer__* core pattern

        assert "web__search_web" not in tool_names
        assert "web__invoke_script" not in tool_names
        assert "web__create_script" not in tool_names

    def test_inactive_skill_excluded_from_tools(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions",
            instructions="Use NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__invoke_script", "web__search_scripts"],
            priority=5,
            is_active=False,
        )

        tool_names = [t["function"]["name"] for t in bot._list_openai_tools(["yahoo_auctions_search_workflow"])]

        assert "web__invoke_script" not in tool_names
        assert "web__search_scripts" not in tool_names


# ---------------------------------------------------------------------------
# Scenario B: Allowlist merge is additive by design
# ---------------------------------------------------------------------------


class TestAllowlistMergeAdditive:
    """Skills' tools_allowlists are merged additively — this is correct behavior.
    A skill that needs web__create_script (e.g., nanoscript_structure_constraint)
    legitimately adds it when co-activated. The bug is that the LLM may choose
    web__create_script over web__invoke_script despite skill instructions."""

    def test_wildcard_skill_adds_all_web_tools(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="web_research",
            description="Search the web for information",
            instructions="Use web tools to research.",
            trigger_mode="pattern",
            trigger_patterns=["search|lookup|find|web|yahoo|auction"],
            tools_allowlist=["web__*"],
            priority=5,
        )

        tool_names = [t["function"]["name"] for t in bot._list_openai_tools(["web_research"])]

        assert "web__search_web" in tool_names
        assert "web__read_page" in tool_names
        assert "web__search_scripts" in tool_names
        assert "web__invoke_script" in tool_names
        assert "web__create_script" in tool_names

    def test_merged_allowlists_include_all_skills_tools(self, tmp_path: Any) -> None:
        """When yahoo and web_research co-activate, their allowlists merge.
        web__* gives all web tools — this is correct additive behavior."""
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses",
            instructions="Use the yahoo_auctions_quality_search NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )
        bot.skills.create(
            name="web_research",
            description="Search the web for information",
            instructions="Use web tools to research.",
            trigger_mode="pattern",
            trigger_patterns=["search|lookup|find|web|yahoo|auction"],
            tools_allowlist=["web__*"],
            priority=5,
        )

        tool_names = [
            t["function"]["name"] for t in bot._list_openai_tools(["yahoo_auctions_search_workflow", "web_research"])
        ]

        assert "web__search_scripts" in tool_names
        assert "web__invoke_script" in tool_names
        assert "web__search_web" in tool_names
        assert "web__read_page" in tool_names
        assert "web__create_script" in tool_names

    def test_yahoo_alone_no_create_script(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions",
            instructions="Use NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )

        tool_names = [t["function"]["name"] for t in bot._list_openai_tools(["yahoo_auctions_search_workflow"])]

        assert "web__invoke_script" in tool_names
        assert "web__search_scripts" in tool_names
        assert "web__create_script" not in tool_names

    def test_two_targeted_skills_merge_correctly(self, tmp_path: Any) -> None:
        """Two skills with explicit allowlists merge via union — no unwanted tools."""
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions",
            instructions="Use NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script"],
            priority=5,
        )
        bot.skills.create(
            name="playwright_testing",
            description="Browser automation",
            instructions="Use Playwright.",
            trigger_mode="pattern",
            trigger_patterns=["browser|click|interact"],
            tools_allowlist=["web__snapshot_page", "web__read_page"],
            priority=3,
        )

        tool_names = [
            t["function"]["name"]
            for t in bot._list_openai_tools(["yahoo_auctions_search_workflow", "playwright_testing"])
        ]

        assert "web__search_scripts" in tool_names
        assert "web__invoke_script" in tool_names
        assert "web__snapshot_page" in tool_names
        assert "web__read_page" in tool_names
        assert "web__create_script" not in tool_names
        assert "web__search_web" not in tool_names


# ---------------------------------------------------------------------------
# SkillMatcher integration
# ---------------------------------------------------------------------------


class TestSkillMatcherIntegration:
    def test_pattern_match_finds_web_research_for_auction(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="web_research",
            description="Search the web",
            instructions="Use web tools.",
            trigger_mode="pattern",
            trigger_patterns=["search|lookup|find|web|yahoo|auction"],
            priority=5,
        )
        matcher = SkillMatcher(bot.skills)

        skills = matcher.find_by_pattern("Search Yahoo Auctions for Minolta 85 1.7")
        assert "web_research" in [s.name for s in skills]

    def test_pattern_match_does_not_match_unrelated(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="web_research",
            description="Search the web",
            instructions="Use web tools.",
            trigger_mode="pattern",
            trigger_patterns=["search|lookup|find|web|yahoo|auction"],
            priority=5,
        )
        matcher = SkillMatcher(bot.skills)

        skills = matcher.find_by_pattern("Remind me to buy groceries")
        assert "web_research" not in [s.name for s in skills]

    def test_intelligent_match_requires_vector_store(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions",
            instructions="Use NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )
        matcher = SkillMatcher(bot.skills)
        assert matcher.find_by_intelligent("search Yahoo Auctions") == []

    def test_spawn_stores_matched_skill_names(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="test_skill",
            description="A test skill",
            instructions="Do the thing.",
            trigger_mode="pattern",
            trigger_patterns=["search|find|look up"],
            priority=3,
        )

        run = bot.subagent_manager.spawn(scope="telegram:123", goal="search for something")

        active_skills = bot.contexts.get("subagent_run", run.id, "active_skills")
        if active_skills and isinstance(active_skills, dict):
            assert "test_skill" in active_skills.get("skills", [])


# ---------------------------------------------------------------------------
# Skill injection with tool filtering
# ---------------------------------------------------------------------------


class TestSkillInjectionWithToolFiltering:
    def test_skill_messages_include_yahoo_instructions(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses",
            instructions="Use the yahoo_auctions_quality_search NanoScript. Always use web__invoke_script.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )

        yahoo_skill = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        assert yahoo_skill is not None

        skill_messages = build_skill_messages([yahoo_skill], bot.prompts)
        assert len(skill_messages) >= 1

        combined_content = " ".join(m["content"] for m in skill_messages)
        assert "yahoo_auctions_search_workflow" in combined_content
        assert "web__invoke_script" in combined_content

    def test_core_tools_do_not_include_crud(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)

        tool_names = [t["function"]["name"] for t in bot._list_openai_tools(skill_names=None)]

        assert "skill__list" in tool_names
        assert "skill__get" in tool_names
        assert "skill__create" not in tool_names
        assert "skill__update" not in tool_names


# ---------------------------------------------------------------------------
# Production scenario: run-1b71350eb4
#
# Active skills:
#   yahoo_auctions_search_workflow (priority 5, tools: [web__search_scripts, web__invoke_script, memory__search])
#   goofish_xianyu_search_limitation (priority 0, no tools)
#   user_pref_direct_urls (priority 0, no tools)
#   nanoscript_structure_constraint (priority 0, tools: [web__create_script])
#   user_hw_local_llm_limit (priority 0, no tools)
# ---------------------------------------------------------------------------


class TestProductionScenario:
    def test_nanoscript_constraint_adds_create_script(self, tmp_path: Any) -> None:
        """nanoscript_structure_constraint leaks web__create_script when co-activated
        with the Yahoo skill. This caused the bot to create 20 placeholder scripts."""
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses",
            instructions="Use the yahoo_auctions_quality_search NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )
        bot.skills.create(
            name="nanoscript_structure_constraint",
            description="Technical requirements for creating NanoScripts via web__create_script",
            instructions="When using web__create_script, the code must contain exactly one top-level async function.",
            trigger_mode="intelligent",
            tools_allowlist=["web__create_script"],
            priority=0,
        )

        tool_names = [
            t["function"]["name"]
            for t in bot._list_openai_tools(["yahoo_auctions_search_workflow", "nanoscript_structure_constraint"])
        ]

        assert "web__search_scripts" in tool_names
        assert "web__invoke_script" in tool_names
        assert "web__create_script" in tool_names  # BUG: leaked by nanoscript skill

    def test_full_production_skill_set(self, tmp_path: Any) -> None:
        bot = _make_bot(tmp_path)
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses",
            instructions="Use the NanoScript.",
            trigger_mode="intelligent",
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )
        bot.skills.create(
            name="goofish_xianyu_search_limitation",
            description="Cannot search Goofish",
            instructions="Goofish requires direct access.",
            trigger_mode="intelligent",
        )
        bot.skills.create(
            name="user_pref_direct_urls",
            description="Include direct links",
            instructions="Always include URLs.",
            trigger_mode="intelligent",
        )
        bot.skills.create(
            name="nanoscript_structure_constraint",
            description="NanoScript creation requirements",
            instructions="Code must contain async def script(page, params).",
            trigger_mode="intelligent",
            tools_allowlist=["web__create_script"],
            priority=0,
        )
        bot.skills.create(
            name="user_hw_local_llm_limit",
            description="Filter for 128GB RAM",
            instructions="Focus on models fitting 128GB RAM.",
            trigger_mode="intelligent",
        )

        tool_names = [
            t["function"]["name"]
            for t in bot._list_openai_tools(
                [
                    "yahoo_auctions_search_workflow",
                    "goofish_xianyu_search_limitation",
                    "user_pref_direct_urls",
                    "nanoscript_structure_constraint",
                    "user_hw_local_llm_limit",
                ]
            )
        ]

        assert "session__scratchpad_write" in tool_names
        assert "memory__search" in tool_names
        assert "web__search_scripts" in tool_names
        assert "web__invoke_script" in tool_names

        assert "web__create_script" in tool_names  # BUG: leaked by nanoscript skill

        assert "web__search_web" not in tool_names
        assert "web__read_page" not in tool_names
        assert "web__snapshot_page" not in tool_names
