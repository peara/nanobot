from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.config import AppConfig, ModelConfig
from nanobot.core import BotCore
from nanobot.evaluator.store import EvaluationResult, QualityAssessment, SkillOperation
from nanobot.subagents.manager import SubagentRunResult


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _make_config(*, enable_evaluator: bool = False) -> Any:
    tmp = tempfile.mkdtemp()
    return AppConfig(
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
        enable_evaluator=enable_evaluator,
    )


class TestEvaluatorIntegration:
    def test_evaluator_none_when_disabled(self) -> None:
        config = _make_config(enable_evaluator=False)
        bot = BotCore(config, {"telegram": _FakeChannel()})
        assert bot.evaluator is None

    def test_evaluator_created_when_enabled(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})
        assert bot.evaluator is not None

    @pytest.mark.asyncio
    async def test_evaluate_turn_noop_when_disabled(self) -> None:
        config = _make_config(enable_evaluator=False)
        bot = BotCore(config, {"telegram": _FakeChannel()})
        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )
        await bot._evaluate_turn("telegram:123", "hello", result)

    @pytest.mark.asyncio
    async def test_evaluate_turn_calls_evaluator(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Good answer",
            has_learnings=False,
            confidence="high",
        )
        eval_result = EvaluationResult(quality=quality)
        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result) as mock_eval:
            await bot._evaluate_turn("telegram:123", "hello", result)
            call_kwargs = mock_eval.call_args
            assert call_kwargs[0][0] == "telegram:123"
            assert call_kwargs[0][1] == "hello"
            assert call_kwargs[0][2] == result
            assert call_kwargs[1].get("scratchpad") is None
            assert isinstance(call_kwargs[1].get("active_skills"), list)

    @pytest.mark.asyncio
    async def test_evaluate_turn_executes_skill_decisions(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Learned preference",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="create",
                name="test_skill_pref",
                description="User prefers X",
                instructions="Use X by default",
                trigger_mode="intelligent",
                source_confidence="high",
                reason="User explicitly requested X",
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)
            created_skill = bot.skills.get_by_name("test_skill_pref")
            assert created_skill is not None
            assert created_skill.description == "User prefers X"
            assert created_skill.trigger_mode == "intelligent"

    @pytest.mark.asyncio
    async def test_evaluate_turn_skips_existing_skill(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Learned preference",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="create",
                name="duplicate_skill",
                description="Should not be created",
                instructions="Should not happen",
                trigger_mode="intelligent",
                source_confidence="high",
                reason="Test duplicate",
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        bot.skills.create(
            name="duplicate_skill",
            description="Already exists",
            instructions="Original",
            trigger_mode="pattern",
        )

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)
            existing = bot.skills.get_by_name("duplicate_skill")
            assert existing is not None
            assert existing.description == "Already exists"

    @pytest.mark.asyncio
    async def test_evaluate_turn_logs_warning_for_missing_update(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Learned preference",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="update",
                name="nonexistent_skill",
                description="Cannot update",
                instructions="Does not exist",
                trigger_mode="intelligent",
                source_confidence="high",
                reason="Test missing update",
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)

    @pytest.mark.asyncio
    async def test_evaluate_turn_swallows_exceptions(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await bot._evaluate_turn("telegram:123", "hello", result)

    @pytest.mark.asyncio
    async def test_evaluate_turn_deprecates_skill(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        bot.skills.create(
            name="old_skill",
            description="An old skill",
            instructions="Do something obsolete",
            trigger_mode="pattern",
            trigger_patterns=["obsolete"],
            is_active=True,
        )

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Learned this is obsolete",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="deprecate",
                name="old_skill",
                description="No longer needed",
                instructions="Deprecated",
                trigger_mode="pattern",
                source_confidence="high",
                reason="Skill is obsolete",
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)
            deprecated = bot.skills.get_by_name("old_skill")
            assert deprecated is not None
            assert deprecated.is_active is False

    @pytest.mark.asyncio
    async def test_evaluate_turn_deprecate_skips_nonexistent(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Should not crash",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="deprecate",
                name="phantom_skill",
                description="Does not exist",
                instructions="Should not crash",
                trigger_mode="intelligent",
                source_confidence="high",
                reason="Test nonexistent deprecate",
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)

    @pytest.mark.asyncio
    async def test_evaluate_turn_deprecate_skips_already_inactive(self) -> None:
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        bot.skills.create(
            name="inactive_skill",
            description="Already inactive",
            instructions="Do nothing",
            trigger_mode="pattern",
            is_active=False,
        )

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Should be idempotent",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="deprecate",
                name="inactive_skill",
                description="Already inactive",
                instructions="No-op",
                trigger_mode="pattern",
                source_confidence="high",
                reason="Test idempotent deprecate",
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)
            skill = bot.skills.get_by_name("inactive_skill")
            assert skill is not None
            assert skill.is_active is False

    @pytest.mark.asyncio
    async def test_evaluate_update_preserves_tools_allowlist_on_empty(self) -> None:
        """Evaluator update with empty tools_allowlist should NOT wipe existing allowlist.

        The LLM often outputs tools_allowlist=[] meaning "no opinion on tools" rather than
        "explicitly set to core-only". We preserve existing allowlists on update when the
        evaluator provides an empty list.
        """
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        bot.skills.create(
            name="yahoo_search",
            description="Search Yahoo Auctions",
            instructions="Use web tools to search",
            trigger_mode="intelligent",
            tools_allowlist=["web__*"],
        )

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Learned keyword improvement",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="update",
                name="yahoo_search",
                description="Updated description",
                instructions="Use better keywords",
                trigger_mode="intelligent",
                source_confidence="high",
                reason="Keyword improvement",
                tools_allowlist=[],
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)
            updated = bot.skills.get_by_name("yahoo_search")
            assert updated is not None
            assert updated.description == "Updated description"
            assert updated.instructions == "Use better keywords"
            assert updated.tools_allowlist == ["web__*"]

    @pytest.mark.asyncio
    async def test_evaluate_update_applies_explicit_tools_allowlist(self) -> None:
        """Evaluator update with explicit tools_allowlist should overwrite the existing one.

        When the evaluator outputs explicit tool patterns (not empty), those should be
        applied as the new allowlist.
        """
        config = _make_config(enable_evaluator=True)
        bot = BotCore(config, {"telegram": _FakeChannel()})

        bot.skills.create(
            name="reddit_scan",
            description="Scan Reddit",
            instructions="Use Reddit tools",
            trigger_mode="intelligent",
            tools_allowlist=["reddit__*"],
        )

        quality = QualityAssessment(
            quality_score=4,
            quality_reason="Switched to broader tools",
            has_learnings=True,
            confidence="high",
        )
        decisions = [
            SkillOperation(
                action="update",
                name="reddit_scan",
                description="Updated description",
                instructions="Use broader tools",
                trigger_mode="intelligent",
                source_confidence="high",
                reason="Needs web access too",
                tools_allowlist=["web__*", "reddit__*"],
            ),
        ]
        eval_result = EvaluationResult(quality=quality, decisions=decisions)

        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result):
            await bot._evaluate_turn("telegram:123", "hello", result)
            updated = bot.skills.get_by_name("reddit_scan")
            assert updated is not None
            assert updated.tools_allowlist == ["web__*", "reddit__*"]
