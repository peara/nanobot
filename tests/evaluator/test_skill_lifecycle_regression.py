from __future__ import annotations

import json
from typing import Any, cast

import pytest
from unittest.mock import AsyncMock, patch

from nanobot.evaluator import LearningEvaluator, LearningItem
from nanobot.evaluator.store import EvaluationResult, QualityAssessment, SkillOperation
from nanobot.prompts import PromptStore
from nanobot.prompts.defaults import SKILL_LIFECYCLE_PROMPT
from nanobot.skills.models import Skill
from nanobot.subagents.manager import SubagentRunResult
from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry


class _FakePromptStore:
    """Uses the real PromptStore seeded with defaults, for prompt rendering tests."""

    def __init__(self) -> None:
        import tempfile

        self._real = PromptStore(tempfile.mkdtemp() + "/prompts.db", seed_defaults=True)

    def render(self, key: str, **variables: str) -> str:
        return self._real.render(key, **variables)


class _FakeLlm:
    """Fake LLM that returns a pre-canned response."""

    def __init__(self, response_content: str) -> None:
        self._response_content = response_content

    async def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[Any],
        response_format: dict[str, Any],
        *,
        scope: str | None = None,
    ) -> dict[str, str]:
        return {"content": self._response_content}


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


def _yahoo_skill() -> Skill:
    """The yahoo_auctions_search_workflow skill as it existed before the 58mm failure run."""
    return Skill(
        id=1,
        name="yahoo_auctions_search_workflow",
        description="Optimized search, quality filtering, and delta-notification workflow for Yahoo Auctions.",
        instructions=(
            "When searching for high-quality vintage lenses on Yahoo Auctions, incorporate the "
            "Japanese keywords 'カビなし' (no mold) and 'くもりなし' (no haze) as primary indicators "
            "and filters for quality to ensure high-grade results."
        ),
        trigger_mode="intelligent",
        trigger_patterns=[],
        tools_allowlist=["web__*"],
        priority=0,
        is_active=True,
        created_at="2026-05-07T16:20:33.775840+00:00",
        updated_at="2026-05-26T07:10:15.724000+00:00",
    )


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_FakeToolForCatalog("web__create_script"))
    registry.register(_FakeToolForCatalog("web__search_scripts"))
    registry.register(_FakeToolForCatalog("web__invoke_script"))
    registry.register(_FakeToolForCatalog("web__search_web"))
    registry.register(_FakeToolForCatalog("web__read_page"))
    registry.register(_FakeToolForCatalog("web__snapshot_page"))
    registry.register(_FakeToolForCatalog("web__interact_page"))
    registry.register(_FakeToolForCatalog("memory__search"))
    registry.register(_FakeToolForCatalog("memory__save"))
    registry.register(_FakeToolForCatalog("skill__list"))
    registry.register(_FakeToolForCatalog("skill__get"))
    return registry


class TestLifecyclePromptPreservesInstructions:
    """Tests that the lifecycle prompt's 'Update Semantics — CRITICAL' section
    causes the LLM to preserve existing skill instructions when updating.

    These tests use real prompt rendering (not mocked) to ensure the prompt
    content is correct, and FakeLlm with canned JSON to verify the code paths
    that process lifecycle decisions.
    """

    def test_lifecycle_prompt_contains_update_semantics(self) -> None:
        """The SKILL_LIFECYCLE_PROMPT must contain the Update Semantics section."""
        assert "## Update Semantics" in SKILL_LIFECYCLE_PROMPT
        assert "COMPLETELY REPLACES" in SKILL_LIFECYCLE_PROMPT
        assert "preserve existing" in SKILL_LIFECYCLE_PROMPT.lower() or "ALL instructions" in SKILL_LIFECYCLE_PROMPT

    def test_lifecycle_prompt_contains_inefficient_runs_section(self) -> None:
        """The SKILL_LIFECYCLE_PROMPT must contain the Learning from Inefficient Runs section."""
        assert "## Learning from Inefficient Runs" in SKILL_LIFECYCLE_PROMPT
        assert "inefficient" in SKILL_LIFECYCLE_PROMPT.lower()
        assert "script" in SKILL_LIFECYCLE_PROMPT.lower() or "structured workflow" in SKILL_LIFECYCLE_PROMPT.lower()

    def test_lifecycle_prompt_rendered_contains_update_sections(self) -> None:
        """Verify the PromptStore renders the lifecycle prompt with update semantics."""
        store = _FakePromptStore()
        rendered = store.render("skill_lifecycle")
        assert "## Update Semantics" in rendered
        assert "## Learning from Inefficient Runs" in rendered

    @pytest.mark.asyncio
    async def test_lifecycle_input_includes_existing_skill_instructions(self) -> None:
        """Verify that _build_lifecycle_input includes existing skill descriptions
        so the LLM can see what instructions to preserve."""
        registry = _make_registry()
        learnings = [
            LearningItem(
                category="workflow_pattern",
                direction="update_skill",
                observation="On Yahoo Auctions, `web__snapshot_page` is more reliable than `web__read_page`",
                evidence="The `web__read_page` tool failed to extract descriptions",
                confidence="high",
            ),
        ]
        active_skills = [_yahoo_skill()]

        result = LearningEvaluator._build_lifecycle_input(learnings, active_skills, tool_registry=registry)

        # The input must include the existing skill instructions so the LLM can preserve them
        assert "yahoo_auctions_search_workflow" in result
        # The existing instructions about quality keywords must be visible to the LLM
        assert "カビなし" in result

    @pytest.mark.asyncio
    async def test_update_preserves_existing_instructions_in_code(self) -> None:
        """After an update decision, _execute_skill_decisions correctly replaces
        instructions entirely (as the code does). This test verifies that when
        the lifecycle decides to update, the code path replaces the instructions —
        making it critical that the LLM includes ALL instructions in its update."""
        import tempfile

        from nanobot.config import AppConfig, ModelConfig
        from nanobot.core import BotCore

        class _FakeChannel:
            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            async def send(self, chat_id: str, text: str) -> None:
                pass

        tmp = tempfile.mkdtemp()
        config = AppConfig(
            assistant_name="TestBot",
            database_path=f"{tmp}/nanobot.db",
            scheduler_db_path=f"{tmp}/scheduler.db",
            plan_db_path=f"{tmp}/plans.db",
            skill_db_path=f"{tmp}/skills.db",
            poll_interval_seconds=20,
            working_timezone="UTC",
            history_message_limit=24,
            history_char_limit=12000,
            model=ModelConfig(base_url="http://localhost", api_key="test", model="test"),
            channels=[],
            mcp_servers=[],
            prompt_db_path=f"{tmp}/prompts.db",
            enable_evaluator=True,
        )
        bot = BotCore(config, {"telegram": _FakeChannel()})

        # Create a skill with existing instructions that should be preserved
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Optimized search, quality filtering, and delta-notification workflow for Yahoo Auctions.",
            instructions=(
                "When searching for high-quality vintage lenses on Yahoo Auctions, incorporate the "
                "Japanese keywords 'カビなし' (no mold) and 'くもりなし' (no haze) as primary indicators "
                "and filters for quality to ensure high-grade results."
            ),
            trigger_mode="intelligent",
            tools_allowlist=["web__*"],
        )

        original = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        assert original is not None
        assert "カビなし" in original.instructions

        # Simulate an evaluator decision that includes the original instructions
        # This is what the updated prompt SHOULD produce: preserving original + adding new
        decisions = [
            SkillOperation(
                action="update",
                name="yahoo_auctions_search_workflow",
                description="Optimized search, quality filtering, and delta-notification workflow for Yahoo Auctions.",
                instructions=(
                    "1. When interacting with Yahoo Auctions, prioritize using available extraction "
                    "scripts (search via `web__search_scripts` then invoke via `web__invoke_script`) "
                    "over manual browsing with `web__search_web` or `web__read_page`.\n"
                    "2. When searching for high-quality vintage lenses on Yahoo Auctions, incorporate the "
                    "Japanese keywords 'カビなし' (no mold) and 'くもりなし' (no haze) as primary indicators "
                    "and filters for quality to ensure high-grade results.\n"
                    "3. `web__snapshot_page` is more reliable than `web__read_page` for extracting "
                    "item descriptions when manual browsing is necessary."
                ),
                trigger_mode="intelligent",
                tools_allowlist=["web__*"],
                source_confidence="high",
                reason="Integrating web script priority with existing quality keywords and snapshot reliability.",
            ),
        ]

        quality = QualityAssessment(
            quality_score=2,
            quality_reason="Hit tool call limit but found useful patterns",
            has_learnings=True,
            confidence="high",
        )
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(run_id="test-run", success=False, reply="", tool_trace=[])

        with patch("nanobot.core.LearningEvaluator") as mock_eval_cls:
            mock_eval = mock_eval_cls.return_value
            mock_eval.evaluate = AsyncMock(return_value=eval_result)
            bot.evaluator = mock_eval

            await bot._evaluate_turn("telegram:123", "search yahoo", result)

        updated = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        assert updated is not None
        # Original instructions preserved
        assert "カビなし" in updated.instructions
        # New instructions added
        assert "web__search_scripts" in updated.instructions
        assert "web__invoke_script" in updated.instructions

    @pytest.mark.asyncio
    async def test_update_wipes_instructions_if_not_preserved(self) -> None:
        """Demonstrates the critical bug: if the LLM forgets to include existing
        instructions in its update, they are lost. This is why the prompt must
        explicitly tell the LLM to preserve them."""
        import tempfile

        from nanobot.config import AppConfig, ModelConfig
        from nanobot.core import BotCore

        class _FakeChannel:
            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            async def send(self, chat_id: str, text: str) -> None:
                pass

        tmp = tempfile.mkdtemp()
        config = AppConfig(
            assistant_name="TestBot",
            database_path=f"{tmp}/nanobot.db",
            scheduler_db_path=f"{tmp}/scheduler.db",
            plan_db_path=f"{tmp}/plans.db",
            skill_db_path=f"{tmp}/skills.db",
            poll_interval_seconds=20,
            working_timezone="UTC",
            history_message_limit=24,
            history_char_limit=12000,
            model=ModelConfig(base_url="http://localhost", api_key="test", model="test"),
            channels=[],
            mcp_servers=[],
            prompt_db_path=f"{tmp}/prompts.db",
            enable_evaluator=True,
        )
        bot = BotCore(config, {"telegram": _FakeChannel()})

        # Create a skill with script-first instructions
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Optimized search, quality filtering, and delta-notification workflow for Yahoo Auctions.",
            instructions=(
                "Prioritize the use of the `yahoo_auctions_quality_search` script over standard "
                "`web__search_web` queries for better quality filtering."
            ),
            trigger_mode="intelligent",
            tools_allowlist=["web__*"],
        )

        # Simulate the BUGGY behavior: update that REPLACES instructions without preserving,
        # which is exactly what the 58mm run's evaluator did
        decisions = [
            SkillOperation(
                action="update",
                name="yahoo_auctions_search_workflow",
                description="Optimized search, quality filtering, and delta-notification workflow for Yahoo Auctions.",
                instructions=(
                    "When interacting with Yahoo Auctions:\n"
                    "1. Tooling: Prefer `web__snapshot_page` over `web__read_page` for extracting "
                    "item descriptions and pricing.\n"
                    "2. Search Strategy: Prioritize direct platform searching over external search engines.\n"
                    "3. Quality Filtering: Supplement specific quality keywords with broader condition terms."
                ),
                trigger_mode="intelligent",
                tools_allowlist=["web__*"],
                source_confidence="high",
                reason="Integrating learnings from failed run.",
            ),
        ]

        quality = QualityAssessment(
            quality_score=2,
            quality_reason="Incomplete but found patterns",
            has_learnings=True,
            confidence="high",
        )
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(run_id="test-run", success=False, reply="", tool_trace=[])

        with patch("nanobot.core.LearningEvaluator") as mock_eval_cls:
            mock_eval = mock_eval_cls.return_value
            mock_eval.evaluate = AsyncMock(return_value=eval_result)
            bot.evaluator = mock_eval

            await bot._evaluate_turn("telegram:123", "search yahoo", result)

        updated = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        assert updated is not None
        # BUG: original script-first instructions are LOST
        # The update replaced them entirely with manual browsing instructions
        assert "yahoo_auctions_quality_search" not in updated.instructions
        # This test documents the problem that the prompt update is designed to prevent

    @pytest.mark.asyncio
    async def test_lifecycle_input_for_inefficient_run(self) -> None:
        """Test that the lifecycle input for a run that hit the tool limit
        and used manual browsing instead of a script includes enough context
        for the LLM to produce efficiency-oriented learnings."""
        registry = _make_registry()

        # These are the learnings the 58mm run's evaluator extracted (the BAD ones)
        learnings = [
            LearningItem(
                category="workflow_pattern",
                direction="update_skill",
                observation="On Yahoo Auctions, `web__snapshot_page` is more reliable than `web__read_page`",
                evidence="The `web__read_page` tool failed to extract the item descriptions",
                confidence="high",
            ),
            LearningItem(
                category="workflow_pattern",
                direction="update_skill",
                observation="External search engine results are frequently outdated",
                evidence="Yahoo Auctions listings change rapidly and often return 404s",
                confidence="high",
            ),
        ]

        active_skills = [_yahoo_skill()]

        result = LearningEvaluator._build_lifecycle_input(learnings, active_skills, tool_registry=registry)

        # The input must show the existing skill so the LLM knows to preserve it
        assert "yahoo_auctions_search_workflow" in result
        assert "カビなし" in result
        # The input must show available tools including scripts
        assert "web__invoke_script" in result
        assert "web__search_scripts" in result