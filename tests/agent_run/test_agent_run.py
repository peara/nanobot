from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from nanobot.agent_run import (
    REPEATED_TOOL_CALL_ABORT_REPLY,
    TOOL_CALL_LIMIT_ABORT_REPLY,
    AgentRun,
    _normalize_roles,
    prepare_messages_for_chat,
)
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
        import tempfile

        from nanobot.prompts import PromptStore

        self.config = SimpleNamespace(working_timezone="UTC")
        self.llm = llm
        self.contexts = _FakeContexts()
        self.tools = ToolRegistry()
        self.active_requests: dict[str, Any] = {}
        self.tool_hooks: list[Any] = []
        self.events: list[ToolCallEvent] = []
        self._temp_dir = tempfile.mkdtemp()
        self.prompts = PromptStore(f"{self._temp_dir}/prompts.db", seed_defaults=True)

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


def test_normalize_roles_merges_consecutive_user_messages() -> None:
    result = _normalize_roles(
        [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
        ]
    )
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "hello\n\nworld"


def test_normalize_roles_merges_consecutive_assistant_messages() -> None:
    result = _normalize_roles(
        [
            {"role": "assistant", "content": "part 1"},
            {"role": "assistant", "content": "part 2"},
        ]
    )
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "part 1\n\npart 2"


def test_normalize_roles_does_not_merge_tool_messages() -> None:
    result = _normalize_roles(
        [
            {"role": "tool", "tool_call_id": "c1", "content": "result1"},
            {"role": "tool", "tool_call_id": "c2", "content": "result2"},
        ]
    )
    assert len(result) == 2


def test_normalize_roles_does_not_merge_across_alternation() -> None:
    result = _normalize_roles(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ]
    )
    assert len(result) == 3


def test_normalize_roles_preserves_tool_calls_on_assistant_merge() -> None:
    result = _normalize_roles(
        [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "assistant", "content": "done"},
        ]
    )
    assert len(result) == 1
    assert result[0]["tool_calls"] == [{"id": "c1"}]
    assert result[0]["content"] == "done"


def test_normalize_roles_empty_and_none_content_merged() -> None:
    result = _normalize_roles(
        [
            {"role": "user", "content": ""},
            {"role": "user", "content": "actual"},
            {"role": "user", "content": None},
            {"role": "user", "content": "more"},
        ]
    )
    assert len(result) == 1
    assert result[0]["content"] == "actual\n\nmore"


def test_prepare_messages_for_chat_normalizes_non_system_roles() -> None:
    merged = prepare_messages_for_chat(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "msg1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "reply"},
        ]
    )
    assert len(merged) == 3
    assert merged[0]["role"] == "system"
    assert merged[1] == {"role": "user", "content": "msg1\n\nmsg2"}
    assert merged[2] == {"role": "assistant", "content": "reply"}


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
    final_tools = llm.calls_tools[-1]
    assert final_tools == []
    final_messages = llm.calls_messages[-1]
    assert any("completed your research" in str(m.get("content", "")) for m in final_messages)


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


def test_agent_run_aborts_on_repeated_identical_tool_calls() -> None:
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "web__read_page",
            "arguments": json.dumps({"url": "https://example.com"}),
        },
    }
    llm = _FakeLlm(
        [
            {"content": "", "tool_calls": [tool_call]},
            {"content": "", "tool_calls": [tool_call]},
            {"content": "", "tool_calls": [tool_call]},
        ]
    )
    host = _FakeHost(llm)
    run = AgentRun(host)

    async def _go() -> None:
        text, trace = await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "read this page"}],
            tools=[{"type": "function", "function": {"name": "web__read_page"}}],
        )
        assert text == REPEATED_TOOL_CALL_ABORT_REPLY
        assert [item["name"] for item in trace] == ["web__read_page", "web__read_page"]

    asyncio.run(_go())


def test_agent_run_aborts_after_tool_call_limit() -> None:
    replies = []
    for idx in range(35):
        replies.append(
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{idx}",
                        "type": "function",
                        "function": {
                            "name": "timer__time_now",
                            "arguments": json.dumps({"timezone_name": f"UTC+{idx}"}),
                        },
                    }
                ],
            }
        )
    llm = _FakeLlm(replies)
    host = _FakeHost(llm)
    host.tools.register(_FakeTool("timer__time_now"))
    run = AgentRun(host)

    async def _go() -> None:
        text, trace = await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "loop tools"}],
            tools=[{"type": "function", "function": {"name": "timer__time_now"}}],
        )
        assert text == TOOL_CALL_LIMIT_ABORT_REPLY
        assert len(trace) == 30

    asyncio.run(_go())


def test_agent_run_finalize_makes_explicit_no_tools_call() -> None:
    llm = _RecordingFakeLlm(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": SCRATCHPAD_TOOL_NAME,
                            "arguments": json.dumps(
                                {
                                    "mode": "finalize",
                                    "goal": "Find info",
                                    "current_step": "Done",
                                    "known_facts": ["fact1", "fact2"],
                                    "tool_journal": ["searched web"],
                                }
                            ),
                        },
                    }
                ],
            },
            {"content": "Here is the info you requested.", "tool_calls": None},
        ]
    )
    host = _FakeHost(llm)
    run = AgentRun(host)

    async def _go() -> None:
        text, trace = await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "find info"}],
            tools=[{"type": "function", "function": {"name": SCRATCHPAD_TOOL_NAME}}],
        )
        assert text == "Here is the info you requested."
        assert [item["name"] for item in trace] == [SCRATCHPAD_TOOL_NAME]

    asyncio.run(_go())

    assert len(llm.calls_messages) == 2
    final_tools = llm.calls_tools[-1]
    assert final_tools == []
    final_messages = llm.calls_messages[-1]
    assert any("completed your research" in str(m.get("content", "")) for m in final_messages)
