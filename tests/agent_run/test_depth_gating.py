"""Tests for the AgentRun changes that gate delegate_task by depth.

See issue #43.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.agent_run import AgentRun
from nanobot.core_scratchpad import SCRATCHPAD_TOOL_NAME
from nanobot.tools import ToolRegistry
from nanobot.tools.base import Tool


class _FakeLlm:
    """Records all calls to chat() so we can inspect the tool list that was sent."""

    def __init__(self, replies: list[dict]) -> None:
        self._replies = replies
        self._idx = 0
        self.calls_tools: list[list[dict]] = []

    async def chat(self, messages, tools, response_format=None, *, scope=None, cancel_token=None):
        del messages, response_format, scope, cancel_token
        self.calls_tools.append(list(tools))
        if self._idx >= len(self._replies):
            raise RuntimeError("No fake LLM reply left")
        reply = self._replies[self._idx]
        self._idx += 1
        return reply


class _FakeTool(Tool):
    def __init__(self, name: str, result: str = "ok") -> None:
        self._name = name
        self._result = result

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


class _FakeContexts:
    def get(self, *args, **kwargs):
        return None

    def put(self, *args, **kwargs):
        pass


class _FakeHost:
    """Minimal host that lets us inject _current_run_depth() per test."""

    def __init__(self, llm: _FakeLlm, depth: int) -> None:
        import tempfile

        from nanobot.prompts import PromptStore

        self.config = SimpleNamespace(working_timezone="UTC")
        self.llm = llm
        self.contexts = _FakeContexts()
        self.tools = ToolRegistry()
        self.active_requests: dict[str, Any] = {}
        self.tool_hooks: list[Any] = []
        self.tool_guards: list[Any] = []
        self.events: list[Any] = []
        self._temp_dir = tempfile.mkdtemp()
        self.prompts = PromptStore(f"{self._temp_dir}/prompts.db", seed_defaults=True)
        self._current_run_id: str | None = None
        self._depth = depth

    def _current_run_depth(self) -> int:
        return self._depth

    async def _dispatch_after_tool_call(self, event: Any) -> None:
        self.events.append(event)


def _delegate_task_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Spawn a subagent",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _other_tool_spec(name: str = "web__search_web") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Fake {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _scratchpad_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": SCRATCHPAD_TOOL_NAME,
            "description": "Scratchpad",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _no_tool_call_reply(content: str = "ok") -> dict:
    return {"content": content, "tool_calls": None}


def _names(specs: list[dict]) -> set[str]:
    return {s.get("function", {}).get("name") for s in specs if "function" in s}


class TestToolsForChat:
    def test_delegate_task_present_at_depth_0(self) -> None:
        """At depth 0 (orchestrator), delegate_task is in the spec list."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth=0)
        run = AgentRun(host)
        result = run._tools_for_chat([_delegate_task_spec(), _other_tool_spec()], allow_scratchpad=True)
        assert "delegate_task" in _names(result)

    def test_delegate_task_stripped_at_depth_1(self) -> None:
        """At depth 1 (first child of orchestrator), delegate_task is hidden."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth=1)
        run = AgentRun(host)
        result = run._tools_for_chat([_delegate_task_spec(), _other_tool_spec()], allow_scratchpad=True)
        assert "delegate_task" not in _names(result)
        assert "web__search_web" in _names(result)

    def test_delegate_task_stripped_at_deeper_depths(self) -> None:
        """At depth 2+, delegate_task is also hidden (defense in depth)."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth=2)
        run = AgentRun(host)
        result = run._tools_for_chat([_delegate_task_spec()], allow_scratchpad=True)
        assert "delegate_task" not in _names(result)

    def test_scratchpad_still_filtered_when_disallowed(self) -> None:
        """The pre-existing scratchpad-filter still works alongside the depth strip."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth=1)
        run = AgentRun(host)
        result = run._tools_for_chat(
            [_delegate_task_spec(), _scratchpad_spec(), _other_tool_spec()],
            allow_scratchpad=False,
        )
        names = _names(result)
        assert SCRATCHPAD_TOOL_NAME not in names
        assert "delegate_task" not in names
        assert "web__search_web" in names

    def test_falls_back_to_minus_one_when_host_lacks_method(self) -> None:
        """If _current_run_depth is missing on the host, default to -1 (no strip)."""
        llm = _FakeLlm([])
        host = SimpleNamespace(
            config=SimpleNamespace(working_timezone="UTC"),
            llm=llm,
            contexts=_FakeContexts(),
            tools=ToolRegistry(),
            active_requests={},
            tool_hooks=[],
            tool_guards=[],
            events=[],
            _current_run_id=None,
        )
        run = AgentRun(host)  # type: ignore[arg-type]
        result = run._tools_for_chat([_delegate_task_spec()], allow_scratchpad=True)
        assert "delegate_task" in _names(result)


class TestRunPublishesCurrentRunId:
    @pytest.mark.asyncio
    async def test_run_sets_current_run_id_on_host(self) -> None:
        """AgentRun.run() should publish _current_run_id on the host for the duration of the call."""
        llm = _FakeLlm([_no_tool_call_reply("ok")])
        host = _FakeHost(llm, depth=0)
        run = AgentRun(host)

        observed: dict[str, str | None] = {}

        original_chat = llm.chat

        async def chat_spy(messages, tools, response_format=None, *, scope=None, cancel_token=None):
            observed["during"] = host._current_run_id
            return await original_chat(messages, tools, response_format, scope=scope, cancel_token=cancel_token)

        llm.chat = chat_spy  # type: ignore[method-assign]
        await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            run_id="run-abc",
        )
        assert observed["during"] == "run-abc"
        assert host._current_run_id is None

    @pytest.mark.asyncio
    async def test_run_clears_current_run_id_on_exception(self) -> None:
        """Even if an exception is raised mid-run, the finally clears _current_run_id."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth=0)
        run = AgentRun(host)

        async def boom(messages, tools, response_format=None, *, scope=None, cancel_token=None):
            raise RuntimeError("LLM error")

        llm.chat = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="LLM error"):
            await run.run(
                scope_for_tools="telegram:1",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                run_id="run-xyz",
            )
        assert host._current_run_id is None
