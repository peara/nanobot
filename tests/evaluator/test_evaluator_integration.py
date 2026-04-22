from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.config import AppConfig, ModelConfig
from nanobot.core import BotCore
from nanobot.evaluator.store import QualityAssessment
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
        result = SubagentRunResult(
            run_id="test-run",
            success=True,
            reply="Done",
            tool_trace=[],
        )

        with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=quality) as mock_eval:
            await bot._evaluate_turn("telegram:123", "hello", result)
            call_kwargs = mock_eval.call_args
            assert call_kwargs[0][0] == "telegram:123"
            assert call_kwargs[0][1] == "hello"
            assert call_kwargs[0][2] == result
            assert call_kwargs[1].get("scratchpad") is None
            assert isinstance(call_kwargs[1].get("active_skills"), list)

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
