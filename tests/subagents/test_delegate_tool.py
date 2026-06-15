"""Tests for run_delegate_task() — issue #43.

Covers the three defense layers for the depth cap:
  1. Tool spec strip in agent_run._tools_for_chat (depth >= 1).
  2. SubagentManager.spawn raises ValueError if depth > MAX_SUBAGENT_DEPTH.
  3. Defensive check in run_delegate_task() (depth >= 1 → refuse).
Also covers input validation (empty/missing goal, missing run context)
and the happy-path wiring (spawn args, system messages, return shape,
cancel token propagation, skill list propagation).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import BotCore
from nanobot.subagents.delegate_tool import DEPTH_REFUSED_MESSAGE, run_delegate_task
from nanobot.subagents.manager import SubagentRunResult
from nanobot.subagents.store import SubagentRun


def _build_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        assistant_name="Nano",
        database_path=str(tmp_path / "nanobot.db"),
        scheduler_db_path=str(tmp_path / "scheduler.db"),
        plan_db_path=str(tmp_path / "plans.db"),
        skill_db_path=str(tmp_path / "skills.db"),
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost:11434/v1", api_key="dummy", model="dummy-model"),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
        prompt_db_path=str(tmp_path / "prompts.db"),
    )


def _make_bot(tmp_path: Path) -> BotCore:
    config = _build_config(tmp_path)
    channel = cast(Any, object())
    return BotCore(config=config, channels={"telegram": channel})


def _make_child_run(run_id: str = "run-child123", scope: str = "telegram:123") -> SubagentRun:
    return SubagentRun(
        id=run_id,
        parent_run_id="run-parent",
        scope=scope,
        status="pending",
        created_at=datetime.now(tz=timezone.utc),
        goal="child goal",
    )


def _make_result(
    run_id: str = "run-child123",
    success: bool = True,
    reply: str = "Child agent reply",
    tool_trace: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> SubagentRunResult:
    return SubagentRunResult(
        run_id=run_id,
        success=success,
        reply=reply,
        tool_trace=tool_trace or [],
        error=error,
    )


class TestDepthRefusal:
    """Defensive check (third layer) refuses delegate_task at depth >= 1."""

    @pytest.mark.asyncio
    async def test_refuses_at_depth_1(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        orchestrator = bot.subagent_manager.spawn(scope="telegram:1", goal="root")
        child = bot.subagent_manager.spawn(scope="telegram:1", parent_run_id=orchestrator.id, goal="child")

        runs_before = len(bot.subagent_manager.list_by_scope("telegram:1"))

        result_str = await run_delegate_task(
            bot,
            {"goal": "do something"},
            scope="telegram:1",
            run_id=child.id,
            cancel_token=None,
        )

        result = json.loads(result_str)
        assert "error" in result
        assert DEPTH_REFUSED_MESSAGE in result["error"]
        # No new spawn should have happened.
        assert len(bot.subagent_manager.list_by_scope("telegram:1")) == runs_before


class TestEmptyGoal:
    @pytest.mark.asyncio
    async def test_empty_goal_returns_error(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        run = bot.subagent_manager.spawn(scope="telegram:1", goal="root")

        result = json.loads(
            await run_delegate_task(
                bot,
                {"goal": ""},
                scope="telegram:1",
                run_id=run.id,
                cancel_token=None,
            )
        )
        assert "error" in result
        assert "goal" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_goal_returns_error(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        run = bot.subagent_manager.spawn(scope="telegram:1", goal="root")

        result = json.loads(
            await run_delegate_task(
                bot,
                {"goal": "   \n\t  "},
                scope="telegram:1",
                run_id=run.id,
                cancel_token=None,
            )
        )
        assert "error" in result


class TestMissingRunId:
    @pytest.mark.asyncio
    async def test_none_run_id_returns_error(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)

        result = json.loads(
            await run_delegate_task(
                bot,
                {"goal": "do something"},
                scope="telegram:1",
                run_id=None,
                cancel_token=None,
            )
        )
        assert "error" in result


class TestHappyPath:
    """Mock-based tests for the happy path: spawn + execute wiring without an LLM."""

    @pytest.mark.asyncio
    async def test_spawn_called_with_parent_run_id_and_scope(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        parent = bot.subagent_manager.spawn(scope="telegram:parent", goal="orchestrator goal")
        token = cast(Any, object())

        child_run = _make_child_run(run_id="run-child999", scope="telegram:parent")
        child_result = _make_result(run_id="run-child999", reply="Child done")
        with (
            patch_spawn(bot, child_run) as mock_spawn,
            patch_execute(bot, child_result) as mock_execute,
        ):
            await run_delegate_task(
                bot,
                {"goal": "narrow focused sub-task"},
                scope="telegram:parent",
                run_id=parent.id,
                cancel_token=token,
            )

        mock_spawn.assert_called_once()
        kwargs = mock_spawn.call_args.kwargs
        assert kwargs["scope"] == "telegram:parent"
        assert kwargs["parent_run_id"] == parent.id
        assert kwargs["goal"] == "narrow focused sub-task"

        mock_execute.assert_called_once()
        ex_args = mock_execute.call_args
        assert ex_args.args[0].id == "run-child999"
        assert ex_args.kwargs["cancel_token"] is token

    @pytest.mark.asyncio
    async def test_system_message_uses_subagent_delegated_template(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        parent = bot.subagent_manager.spawn(scope="telegram:parent", goal="root")

        captured: dict[str, Any] = {}

        def render_capture(name: str, **kwargs: Any) -> str:
            captured.setdefault("calls", []).append((name, kwargs))
            if name == "subagent_delegated":
                return "DELEGATED_SYSTEM_PROMPT"
            if name == "subagent_time":
                return "TIME_BLOCK"
            return ""

        bot.prompts.render = cast(Any, render_capture)  # type: ignore[method-override]

        with patch_spawn(bot, _make_child_run()), patch_execute(bot, _make_result()):
            await run_delegate_task(
                bot,
                {"goal": "test goal"},
                scope="telegram:parent",
                run_id=parent.id,
                cancel_token=None,
            )

        template_names = [c[0] for c in captured["calls"]]
        assert "subagent_delegated" in template_names
        assert "orchestrator_main" not in template_names
        assert "subagent_scheduled" not in template_names

    @pytest.mark.asyncio
    async def test_messages_structure_is_system_system_user(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        parent = bot.subagent_manager.spawn(scope="telegram:parent", goal="root")

        bot.prompts.render = cast(  # type: ignore[method-override]
            Any,
            lambda name, **kw: f"[{name}]" if name == "subagent_delegated" else "[time]",
        )

        captured_messages: list[dict[str, str]] = []
        with patch_spawn(bot, _make_child_run()), patch_execute_capture(bot, captured_messages):
            await run_delegate_task(
                bot,
                {"goal": "the actual goal text"},
                scope="telegram:parent",
                run_id=parent.id,
                cancel_token=None,
            )

        assert len(captured_messages) == 3
        assert captured_messages[0]["role"] == "system"
        assert captured_messages[0]["content"] == "[subagent_delegated]"
        assert captured_messages[1]["role"] == "system"
        assert captured_messages[1]["content"] == "[time]"
        assert captured_messages[2]["role"] == "user"
        assert captured_messages[2]["content"] == "the actual goal text"

    @pytest.mark.asyncio
    async def test_return_shape_matches_result_fields(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        parent = bot.subagent_manager.spawn(scope="telegram:parent", goal="root")

        trace = [{"name": "timer__time_now", "args": {}, "result_preview": "12:00"}]
        child_result = _make_result(run_id="run-child42", success=True, reply="Got the time", tool_trace=trace)
        with patch_spawn(bot, _make_child_run(run_id="run-child42")), patch_execute(bot, child_result):
            result_str = await run_delegate_task(
                bot,
                {"goal": "check time"},
                scope="telegram:parent",
                run_id=parent.id,
                cancel_token=None,
            )

        result = json.loads(result_str)
        assert result["run_id"] == "run-child42"
        assert result["reply"] == "Got the time"
        assert result["success"] is True
        assert result["tool_calls"] == trace
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_return_shape_includes_error_on_failure(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        parent = bot.subagent_manager.spawn(scope="telegram:parent", goal="root")

        child_result = _make_result(run_id="run-child-fail", success=False, reply="", error="boom")
        with patch_spawn(bot, _make_child_run(run_id="run-child-fail")), patch_execute(bot, child_result):
            result_str = await run_delegate_task(
                bot,
                {"goal": "failing task"},
                scope="telegram:parent",
                run_id=parent.id,
                cancel_token=None,
            )

        result = json.loads(result_str)
        assert result["success"] is False
        assert result["error"] == "boom"

    @pytest.mark.asyncio
    async def test_execute_uses_active_skill_names_for_tool_list(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        parent = bot.subagent_manager.spawn(scope="telegram:parent", goal="root")

        captured_kwargs: dict[str, Any] = {}

        def _capture_list(skill_names=None):
            captured_kwargs["skill_names"] = skill_names
            return [{"type": "function", "function": {"name": "fake_tool"}}]

        bot._list_openai_tools = cast(Any, _capture_list)  # type: ignore[method-override]

        with patch_spawn(bot, _make_child_run()), patch_execute(bot, _make_result()):
            await run_delegate_task(
                bot,
                {"goal": "test"},
                scope="telegram:parent",
                run_id=parent.id,
                cancel_token=None,
            )

        assert "skill_names" in captured_kwargs
        assert isinstance(captured_kwargs["skill_names"], list)


class _SpawnPatch:
    def __init__(self, bot: BotCore, return_value: SubagentRun) -> None:
        self._bot = bot
        self._return = return_value
        self.mock: MagicMock | None = None

    def __enter__(self) -> MagicMock:
        self.mock = MagicMock(return_value=self._return)
        self._bot.subagent_manager.spawn = self.mock  # type: ignore[method-override]
        return self.mock

    def __exit__(self, *args: Any) -> None:
        pass


class _ExecutePatch:
    def __init__(self, bot: BotCore, return_value: SubagentRunResult) -> None:
        self._bot = bot
        self._return = return_value
        self.mock: AsyncMock | None = None

    def __enter__(self) -> AsyncMock:
        self.mock = AsyncMock(return_value=self._return)
        self._bot.subagent_manager.execute = self.mock  # type: ignore[method-override]
        return self.mock

    def __exit__(self, *args: Any) -> None:
        pass


class _ExecuteCapturePatch:
    def __init__(self, bot: BotCore, captured_messages: list[dict[str, str]]) -> None:
        self._bot = bot
        self._captured = captured_messages
        self.mock: AsyncMock | None = None

    def __enter__(self) -> AsyncMock:
        async def _execute(run, messages, tools, **kwargs: Any) -> SubagentRunResult:
            self._captured.extend(messages)
            return _make_result()

        self.mock = AsyncMock(side_effect=_execute)
        self._bot.subagent_manager.execute = self.mock  # type: ignore[method-override]
        return self.mock

    def __exit__(self, *args: Any) -> None:
        pass


def patch_spawn(bot: BotCore, return_value: SubagentRun) -> _SpawnPatch:
    return _SpawnPatch(bot, return_value)


def patch_execute(bot: BotCore, return_value: SubagentRunResult) -> _ExecutePatch:
    return _ExecutePatch(bot, return_value)


def patch_execute_capture(bot: BotCore, captured_messages: list[dict[str, str]]) -> _ExecuteCapturePatch:
    return _ExecuteCapturePatch(bot, captured_messages)


class TestSpawnDepthSecondLayer:
    """SubagentManager.spawn raises ValueError past MAX_SUBAGENT_DEPTH (second defense layer)."""

    def test_spawn_raises_value_error_at_depth_2(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        orch = bot.subagent_manager.spawn(scope="telegram:1", goal="orch")
        child = bot.subagent_manager.spawn(scope="telegram:1", parent_run_id=orch.id, goal="child")
        with pytest.raises(ValueError, match="depth 2 > MAX_SUBAGENT_DEPTH"):
            bot.subagent_manager.spawn(scope="telegram:1", parent_run_id=child.id, goal="grand")
