from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from nanobot.agent_run import AgentRun, prepare_messages_for_chat
from nanobot.hooks import ToolCallEvent


class _FakeContexts:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], Any] = {}

    def get(self, kind: str, scope: str, key: str) -> Any:
        return self._data.get((kind, scope, key))

    def put(self, kind: str, scope: str, key: str, value: Any) -> None:
        self._data[(kind, scope, key)] = value


class _FakeLlm:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = replies
        self._idx = 0

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> dict:
        del messages, tools, response_format
        if self._idx >= len(self._replies):
            raise RuntimeError("No fake LLM reply left")
        reply = self._replies[self._idx]
        self._idx += 1
        return reply


class _FakeMcp:
    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        del name, args
        return "ok"


class _FakeHost:
    def __init__(self, llm: _FakeLlm) -> None:
        self.config = SimpleNamespace(working_timezone="UTC")
        self.llm = llm
        self.mcp = _FakeMcp()
        self.contexts = _FakeContexts()
        self.active_requests: dict[str, Any] = {}
        self.tool_hooks: list[Any] = []
        self.events: list[ToolCallEvent] = []

    async def _dispatch_after_tool_call(self, event: ToolCallEvent) -> None:
        self.events.append(event)


def test_prepare_messages_for_chat_merges_system_roles() -> None:
    merged = prepare_messages_for_chat(
        [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert merged[0]["role"] == "system"
    assert merged[0]["content"] == "A\n\nB"
    assert merged[1] == {"role": "user", "content": "hi"}


def test_agent_run_without_tools_returns_llm_content() -> None:
    llm = _FakeLlm([{"content": "final answer", "tool_calls": None}])
    host = _FakeHost(llm)
    run = AgentRun(host)

    async def _go() -> None:
        text, trace = await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "ping"}],
            tools=[],
        )
        assert text == "final answer"
        assert trace == []

    asyncio.run(_go())
