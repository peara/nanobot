"""Tests for BotCore changes related to delegate_task (issue #43).

Verifies:
- `delegate_task` is NOT in CORE_TOOL_PATTERNS (its spec is prepended
  by _list_openai_tools instead, like session__scratchpad_write).
- BotCore does NOT register a DelegateTaskTool in its ToolRegistry
  (the tool is dispatched as a control-plane operation by the LLM loop).
- BotCore._list_openai_tools() prepends the delegate_task spec.
- BotCore._compute_run_depth(run_id) returns -1 for unknown / None,
  0 for root, 1 for first child.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import CORE_TOOL_PATTERNS, BotCore
from nanobot.subagents.delegate_tool import DELEGATE_TASK_NAME, delegate_task_spec


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


class _FakeLlm:
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
    config = _build_config(tmp_path)
    channel = cast(Any, object())
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, _FakeLlm(replies=[]))
    return bot


class TestCoreToolPatterns:
    def test_delegate_task_absent_from_core_patterns(self) -> None:
        """delegate_task must NOT be in CORE_TOOL_PATTERNS — its spec is prepended directly."""
        assert DELEGATE_TASK_NAME not in CORE_TOOL_PATTERNS


class TestToolRegistration:
    def test_delegate_task_tool_not_in_registry(self, tmp_path: Path) -> None:
        """BotCore does not register delegate_task in the ToolRegistry (control-plane dispatch)."""
        bot = _make_bot(tmp_path)
        assert bot.tools.get(DELEGATE_TASK_NAME) is None

    def test_delegate_task_appears_in_tool_list(self, tmp_path: Path) -> None:
        """BotCore._list_openai_tools() prepends the delegate_task spec (scratchpad pattern)."""
        bot = _make_bot(tmp_path)
        specs = bot._list_openai_tools(skill_names=None)
        names = {s["function"]["name"] for s in specs if "function" in s}
        assert DELEGATE_TASK_NAME in names

    def test_delegate_task_spec_matches_prepended(self, tmp_path: Path) -> None:
        """The prepended spec is the one returned by delegate_task_spec()."""
        bot = _make_bot(tmp_path)
        specs = bot._list_openai_tools(skill_names=None)
        delegate_specs = [s for s in specs if s.get("function", {}).get("name") == DELEGATE_TASK_NAME]
        assert len(delegate_specs) == 1
        assert delegate_specs[0] == delegate_task_spec()


class TestComputeRunDepth:
    def test_returns_minus_one_for_none(self, tmp_path: Path) -> None:
        """If run_id is None, depth is unknown (-1)."""
        bot = _make_bot(tmp_path)
        assert bot._compute_run_depth(None) == -1

    def test_returns_zero_for_root_run(self, tmp_path: Path) -> None:
        """A run with no parent (orchestrator or scheduled) reports depth 0."""
        bot = _make_bot(tmp_path)
        run = bot.subagent_manager.spawn(scope="telegram:depth0", goal="root")
        assert bot._compute_run_depth(run.id) == 0

    def test_returns_one_for_first_child(self, tmp_path: Path) -> None:
        """A run with an orchestrator as parent reports depth 1 (the child is a delegate_task result)."""
        bot = _make_bot(tmp_path)
        orchestrator = bot.subagent_manager.spawn(scope="telegram:depth0", goal="root")
        child = bot.subagent_manager.spawn(scope="telegram:depth0", parent_run_id=orchestrator.id, goal="child")
        assert bot._compute_run_depth(child.id) == 1

    def test_returns_minus_one_for_unknown_run_id(self, tmp_path: Path) -> None:
        """If run_id is set but the row does not exist, depth is -1 (defensive)."""
        bot = _make_bot(tmp_path)
        assert bot._compute_run_depth("run-does-not-exist") == -1


class TestNoSharedStateAttributes:
    """The host must not have the _current_* attributes anymore."""

    def test_host_has_no_current_run_id(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        assert not hasattr(bot, "_current_run_id")

    def test_host_has_no_current_scope(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        assert not hasattr(bot, "_current_scope")

    def test_host_has_no_current_cancel_token(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path)
        assert not hasattr(bot, "_current_cancel_token")
