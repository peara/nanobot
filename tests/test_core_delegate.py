"""Tests for BotCore changes related to delegate_task (issue #43).

Verifies:
- `delegate_task` is in CORE_TOOL_PATTERNS.
- BotCore registers the DelegateTaskTool in its ToolRegistry.
- BotCore._current_run_depth() returns -1 when no run is set.
- BotCore._current_run_depth() returns the right depth for real runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import CORE_TOOL_PATTERNS, BotCore
from nanobot.subagents.delegate_tool import DelegateTaskTool


def _build_config(tmp_path: Path) -> AppConfig:
    """Build a minimal AppConfig for instantiating BotCore in tests."""
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


class _FakeLlm:
    """Minimal LLM stub that returns the next pre-canned reply each call.

    Defined locally because tests/ is not a Python package and cross-test
    imports would need sys.path hacks. Kept tiny since these tests don't
    actually invoke the LLM.
    """

    def __init__(self, replies: list[dict]) -> None:
        self._replies = replies
        self._idx = 0

    async def chat(self, messages, tools, response_format=None, *, scope=None, cancel_token=None):
        del messages, tools, response_format, scope, cancel_token
        if self._idx >= len(self._replies):
            raise RuntimeError("No fake LLM reply left")
        reply = self._replies[self._idx]
        self._idx += 1
        return reply


def _make_bot(tmp_path: Path) -> BotCore:
    """Build a BotCore for tests; uses a no-op fake LLM."""
    config = _build_config(tmp_path)
    channel = cast(Any, object())  # unused in these tests
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, _FakeLlm(replies=[]))
    return bot


class TestCoreToolPatterns:
    def test_delegate_task_in_core_patterns(self) -> None:
        """delegate_task must be in CORE_TOOL_PATTERNS so the orchestrator can see it."""
        assert "delegate_task" in CORE_TOOL_PATTERNS


class TestToolRegistration:
    def test_delegate_task_tool_is_registered(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        tool = bot.tools.get("delegate_task")
        assert tool is not None
        assert isinstance(tool, DelegateTaskTool)

    def test_delegate_task_appears_in_core_tool_list(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        specs = bot._list_openai_tools(skill_names=None)
        names = {s["function"]["name"] for s in specs if "function" in s}
        assert "delegate_task" in names


class TestCurrentRunDepth:
    def test_returns_minus_one_when_no_run_id_set(self, tmp_path: Path) -> None:
        """If _current_run_id is not set, depth is unknown (-1)."""
        bot = _make_bot(tmp_path)
        assert bot._current_run_depth() == -1

    def test_returns_zero_for_root_run(self, tmp_path: Path) -> None:
        """A run with no parent (orchestrator or scheduled) reports depth 0."""
        bot = _make_bot(tmp_path)
        run = bot.subagent_manager.spawn(scope="telegram:depth0", goal="root")
        bot._current_run_id = run.id
        assert bot._current_run_depth() == 0

    def test_returns_one_for_first_child(self, tmp_path: Path) -> None:
        """A run with an orchestrator as parent reports depth 1 (the child is a delegate_task result)."""
        bot = _make_bot(tmp_path)
        orchestrator = bot.subagent_manager.spawn(scope="telegram:depth0", goal="root")
        child = bot.subagent_manager.spawn(scope="telegram:depth0", parent_run_id=orchestrator.id, goal="child")
        bot._current_run_id = child.id
        assert bot._current_run_depth() == 1

    def test_returns_minus_one_for_unknown_run_id(self, tmp_path: Path) -> None:
        """If _current_run_id is set but the row does not exist, depth is -1 (defensive)."""
        bot = _make_bot(tmp_path)
        bot._current_run_id = "run-does-not-exist"
        assert bot._current_run_depth() == -1
