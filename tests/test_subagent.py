from __future__ import annotations

import asyncio
from typing import Any, cast

from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import BotCore
from nanobot.messages import SubagentResultMessage
from nanobot.subagent import SubagentRunner
from nanobot.tools.base import Tool


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


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


class _TrackingTool(Tool):
    def __init__(self, name: str, call_log: list[tuple[str, dict]]) -> None:
        self._name = name
        self._call_log = call_log

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Tracking tool {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        self._call_log.append((self._name, args))
        return "ok"


def _build_config(tmp_path) -> AppConfig:
    from pathlib import Path

    db_path = str(Path(tmp_path) / "nanobot.db")
    scheduler_db_path = str(Path(tmp_path) / "scheduler.db")
    return AppConfig(
        assistant_name="Nano",
        database_path=db_path,
        scheduler_db_path=scheduler_db_path,
        poll_interval_seconds=20,
        system_prompt_template="You are {assistant_name}.",
        subagent_system_prompt="You are an autonomous agent. Complete the task.",
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost:11434/v1", api_key="dummy", model="dummy-model"),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
    )


def test_subagent_runner_returns_result_on_success(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, _FakeLlm(replies=[{"content": "Task completed", "tool_calls": None}]))

    runner = SubagentRunner(bot)

    async def _run() -> SubagentResultMessage:
        return await runner.run(
            goal="Check the weather",
            parent_scope="telegram:123",
            system_prompt="You are an autonomous agent.",
        )

    result = asyncio.run(_run())

    assert result.success is True
    assert result.summary == "Task completed"
    assert result.parent_scope == "telegram:123"
    assert result.run_id.startswith("subagent-")
    assert len(result.tool_trace) == 0


def test_subagent_runner_stores_context_on_success(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, _FakeLlm(replies=[{"content": "Done", "tool_calls": None}]))

    runner = SubagentRunner(bot)

    async def _run() -> SubagentResultMessage:
        return await runner.run(
            goal="Test task",
            parent_scope="telegram:456",
            system_prompt="Do it.",
        )

    result = asyncio.run(_run())

    stored = bot.contexts.get("subagent_run", result.run_id, "result")
    assert stored is not None
    assert stored["success"] is True
    assert stored["summary"] == "Done"

    status = bot.contexts.get("subagent_run", result.run_id, "status")
    assert status == {"value": "completed"}


def test_subagent_runner_handles_failure(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})

    class _FailingLlm:
        async def chat(self, messages, tools, response_format=None):
            raise RuntimeError("LLM connection failed")

    bot.llm = cast(Any, _FailingLlm())

    runner = SubagentRunner(bot)

    async def _run() -> SubagentResultMessage:
        return await runner.run(
            goal="Failing task",
            parent_scope="telegram:789",
            system_prompt="Do it.",
        )

    result = asyncio.run(_run())

    assert result.success is False
    assert "Error:" in result.summary
    assert result.metadata is not None
    assert "error" in result.metadata

    status = bot.contexts.get("subagent_run", result.run_id, "status")
    assert status == {"value": "failed"}


def test_subagent_runner_with_tool_calls(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(
        Any,
        _FakeLlm(
            replies=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "timer__time_now", "arguments": "{}"},
                        }
                    ],
                },
                {"content": "Time checked", "tool_calls": None},
            ]
        ),
    )

    bot.tools.register(_FakeTool("timer__time_now", result="2025-01-01T12:00:00Z"))

    runner = SubagentRunner(bot)

    async def _run() -> SubagentResultMessage:
        return await runner.run(
            goal="Check time",
            parent_scope="telegram:999",
            system_prompt="Get the current time.",
        )

    result = asyncio.run(_run())

    assert result.success is True
    assert result.summary == "Time checked"
    assert len(result.tool_trace) == 1
    assert result.tool_trace[0]["name"] == "timer__time_now"


def test_subagent_runner_uses_parent_scope_for_tools(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})

    call_log: list[tuple[str, dict]] = []
    bot.tools.register(_TrackingTool("test_tool", call_log))

    bot.llm = cast(
        Any,
        _FakeLlm(
            replies=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "test_tool", "arguments": '{"chat_id": "current"}'},
                        }
                    ],
                },
                {"content": "Done", "tool_calls": None},
            ]
        ),
    )

    runner = SubagentRunner(bot)

    async def _run() -> SubagentResultMessage:
        return await runner.run(
            goal="Test scope",
            parent_scope="telegram:parent123",
            system_prompt="Do it.",
        )

    asyncio.run(_run())

    assert len(call_log) == 1
