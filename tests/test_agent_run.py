from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from nanobot.agent_run import AgentRun, prepare_messages_for_chat
from nanobot.core_scratchpad import SCRATCHPAD_TOOL_NAME
from nanobot.hooks import ToolCallEvent
from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry


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


class _RecordingFakeLlm(_FakeLlm):
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        super().__init__(replies)
        self.calls_messages: list[list[dict[str, Any]]] = []
        self.calls_tools: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> dict:
        self.calls_messages.append(messages)
        self.calls_tools.append(tools)
        return await super().chat(messages, tools, response_format)


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


class _RecordingTool(Tool):
    def __init__(self, name: str, call_log: list[tuple[str, dict]]) -> None:
        self._name = name
        self._call_log = call_log

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Recording tool {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        self._call_log.append((self._name, dict(args)))
        return "ok"


class _FakeHost:
    def __init__(self, llm: _FakeLlm) -> None:
        self.config = SimpleNamespace(working_timezone="UTC")
        self.llm = llm
        self.contexts = _FakeContexts()
        self.tools = ToolRegistry()
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


def test_agent_run_does_not_repeat_finalize_scratchpad_calls() -> None:
    llm = _RecordingFakeLlm(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "scheduler__schedule_task",
                            "arguments": json.dumps(
                                {
                                    "chat_id": "telegram:1",
                                    "cron_expr": "58 10 * * 5",
                                    "prompt": "test msg",
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": SCRATCHPAD_TOOL_NAME,
                            "arguments": json.dumps(
                                {
                                    "mode": "finalize",
                                    "current_step": "Scheduled",
                                    "next_step": "Reply",
                                    "tool_journal": ["scheduler ok"],
                                }
                            ),
                        },
                    }
                ],
            },
            {"content": "Scheduled successfully.", "tool_calls": None},
        ]
    )
    host = _FakeHost(llm)
    host.tools.register(_FakeTool("scheduler__schedule_task"))
    run = AgentRun(host)

    async def _go() -> None:
        text, trace = await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "set a reminder"}],
            tools=[
                {"type": "function", "function": {"name": SCRATCHPAD_TOOL_NAME}},
                {"type": "function", "function": {"name": "scheduler__schedule_task"}},
            ],
        )
        assert text == "Scheduled successfully."
        assert [item["name"] for item in trace] == ["scheduler__schedule_task", SCRATCHPAD_TOOL_NAME]

    asyncio.run(_go())

    assert len(llm.calls_messages) == 3
    assert not any("Internal scratchpad state" in str(message.get("content", "")) for message in llm.calls_messages[-1])
    assert [str(tool.get("function", {}).get("name", "")) for tool in llm.calls_tools[-1]] == [
        "scheduler__schedule_task"
    ]


def test_agent_run_normalizes_numeric_schedule_chat_id_to_current_scope() -> None:
    llm = _FakeLlm(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "scheduler__schedule_task",
                            "arguments": json.dumps(
                                {
                                    "chat_id": "123456789",
                                    "cron_expr": "18 11 * * *",
                                    "prompt": "nau com",
                                }
                            ),
                        },
                    }
                ],
            },
            {"content": "Scheduled successfully.", "tool_calls": None},
        ]
    )
    host = _FakeHost(llm)
    recorded_calls: list[tuple[str, dict[str, Any]]] = []
    host.tools.register(_RecordingTool("scheduler__schedule_task", recorded_calls))
    run = AgentRun(host)

    async def _go() -> None:
        text, trace = await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "set a reminder"}],
            tools=[{"type": "function", "function": {"name": "scheduler__schedule_task"}}],
        )
        assert text == "Scheduled successfully."
        assert [item["name"] for item in trace] == ["scheduler__schedule_task"]

    asyncio.run(_go())

    assert recorded_calls == [
        (
            "scheduler__schedule_task",
            {"chat_id": "telegram:1", "cron_expr": "18 11 * * *", "prompt": "nau com"},
        )
    ]
