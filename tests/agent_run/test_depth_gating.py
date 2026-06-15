"""Tests for the AgentRun changes that gate delegate_task by depth.

Covers the spec-list strip in _tools_for_chat, parameterized by the
run_id passed in from the LLM loop's local state. The host's
_compute_run_depth(run_id) is the depth oracle; the LLM loop no longer
publishes/clears shared state on the host.

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
    """Minimal host with a configurable _compute_run_depth map."""

    def __init__(self, llm: _FakeLlm, depth_by_run_id: dict[str | None, int]) -> None:
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
        self._depth_by_run_id = depth_by_run_id

    def _compute_run_depth(self, run_id: str | None) -> int:
        return self._depth_by_run_id.get(run_id, -1)

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
        host = _FakeHost(llm, depth_by_run_id={"run-orch": 0})
        run = AgentRun(host)
        result = run._tools_for_chat(
            [_delegate_task_spec(), _other_tool_spec()],
            allow_scratchpad=True,
            run_id="run-orch",
        )
        assert "delegate_task" in _names(result)

    def test_delegate_task_stripped_at_depth_1(self) -> None:
        """At depth 1 (first child of orchestrator), delegate_task is hidden."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth_by_run_id={"run-child": 1})
        run = AgentRun(host)
        result = run._tools_for_chat(
            [_delegate_task_spec(), _other_tool_spec()],
            allow_scratchpad=True,
            run_id="run-child",
        )
        assert "delegate_task" not in _names(result)
        assert "web__search_web" in _names(result)

    def test_delegate_task_stripped_at_deeper_depths(self) -> None:
        """At depth 2+, delegate_task is also hidden (defense in depth)."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth_by_run_id={"run-grand": 2})
        run = AgentRun(host)
        result = run._tools_for_chat(
            [_delegate_task_spec()],
            allow_scratchpad=True,
            run_id="run-grand",
        )
        assert "delegate_task" not in _names(result)

    def test_scratchpad_still_filtered_when_disallowed(self) -> None:
        """The pre-existing scratchpad-filter still works alongside the depth strip."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth_by_run_id={"run-child": 1})
        run = AgentRun(host)
        result = run._tools_for_chat(
            [_delegate_task_spec(), _scratchpad_spec(), _other_tool_spec()],
            allow_scratchpad=False,
            run_id="run-child",
        )
        names = _names(result)
        assert SCRATCHPAD_TOOL_NAME not in names
        assert "delegate_task" not in names
        assert "web__search_web" in names

    def test_falls_back_to_minus_one_when_host_lacks_method(self) -> None:
        """If _compute_run_depth is missing on the host, default to -1 (no strip)."""
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
        )
        run = AgentRun(host)  # type: ignore[arg-type]
        result = run._tools_for_chat(
            [_delegate_task_spec()],
            allow_scratchpad=True,
            run_id="run-anyone",
        )
        assert "delegate_task" in _names(result)

    def test_no_shared_state_read_in_strip(self) -> None:
        """The strip reads only the run_id argument, not anything from the host."""
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth_by_run_id={"run-a": 0, "run-b": 1})
        run = AgentRun(host)
        a = run._tools_for_chat([_delegate_task_spec()], allow_scratchpad=True, run_id="run-a")
        b = run._tools_for_chat([_delegate_task_spec()], allow_scratchpad=True, run_id="run-b")
        assert "delegate_task" in _names(a)
        assert "delegate_task" not in _names(b)


class TestRunDoesNotMutateHost:
    """AgentRun.run() no longer publishes/clears shared state on the host.

    The delegate_task tool now receives the run context explicitly from
    the LLM loop's local state, so the host attribute lifecycle is gone.
    """

    @pytest.mark.asyncio
    async def test_run_does_not_set_attributes_on_host(self) -> None:
        llm = _FakeLlm([_no_tool_call_reply("ok")])
        host = _FakeHost(llm, depth_by_run_id={"run-abc": 0})
        run = AgentRun(host)

        await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            run_id="run-abc",
        )
        for attr in ("_current_run_id", "_current_scope", "_current_cancel_token"):
            assert not hasattr(host, attr), f"host should not have {attr}"

    @pytest.mark.asyncio
    async def test_run_does_not_set_attributes_on_exception(self) -> None:
        llm = _FakeLlm([])
        host = _FakeHost(llm, depth_by_run_id={"run-xyz": 0})
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
        for attr in ("_current_run_id", "_current_scope", "_current_cancel_token"):
            assert not hasattr(host, attr), f"host should not have {attr}"
