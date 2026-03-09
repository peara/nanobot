from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import (
    EMPTY_REPLY_FALLBACK,
    SCRATCHPAD_PROTOCOL_ABORT_REPLY,
    BotCore,
)
from nanobot.core_scratchpad import (
    MAX_CONTEXT_CHARS,
    MAX_FIELD_CHARS,
    MAX_KNOWN_FACTS,
    MAX_TOOL_JOURNAL,
    SCRATCHPAD_TOOL_NAME,
)


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


class _RecordingFakeLlm(_FakeLlm):
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        super().__init__(replies)
        self.calls_messages: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> dict:
        self.calls_messages.append(messages)
        return await super().chat(messages, tools, response_format)


class _FakeMcp:
    def list_openai_tools(self) -> list[dict]:
        return []

    async def call_tool(self, fn_name: str, args: dict) -> str:
        del fn_name, args
        raise RuntimeError("No tools expected in this test")


def _build_config(tmp_path) -> AppConfig:
    db_path = str(tmp_path / "nanobot.db")
    scheduler_db_path = str(tmp_path / "scheduler.db")
    return AppConfig(
        assistant_name="Nano",
        database_path=db_path,
        scheduler_db_path=scheduler_db_path,
        poll_interval_seconds=20,
        system_prompt_template="You are {assistant_name}.",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost:11434/v1", api_key="dummy", model="dummy-model"),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
    )


def test_plan_command_creates_plan_run_scope_and_reports_result(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(
        Any,
        _FakeLlm(
            replies=[
                {
                    "content": (
                        '{"goal":"Book flight","constraints":["budget"],'
                        '"required_inputs":["date"],"risk_flags":[],"notes":"ok"}'
                    ),
                    "tool_calls": None,
                },
                {"content": "Plan completed: please share your travel date.", "tool_calls": None},
            ]
        ),
    )
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/plan book me a flight")
    asyncio.run(bot.on_incoming(message))

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "42"
    assert "Plan completed" in channel.sent[0][1]

    chat_scope = "telegram:42"
    last_run = bot.contexts.get("chat", chat_scope, "last_plan_run_id")
    assert isinstance(last_run, dict)
    run_id = str(last_run["run_id"])

    assert bot.contexts.get("plan_run", run_id, "status") == {"value": "completed"}
    result = bot.contexts.get("plan_run", run_id, "result")
    assert isinstance(result, dict)
    assert "Plan completed" in str(result["text"])


def test_plan_command_recovers_from_garbled_output(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    garbled = "Brock" + ("?" * 300)
    bot.llm = cast(
        Any,
        _FakeLlm(
            replies=[
                {"content": garbled, "tool_calls": None},
                {"content": garbled, "tool_calls": None},
                {"content": "Recovered: please provide max budget in USD.", "tool_calls": None},
            ]
        ),
    )
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/plan find me a camera")
    asyncio.run(bot.on_incoming(message))

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "42"
    assert "Recovered:" in channel.sent[0][1]

    chat_scope = "telegram:42"
    last_run = bot.contexts.get("chat", chat_scope, "last_plan_run_id")
    assert isinstance(last_run, dict)
    run_id = str(last_run["run_id"])
    result = bot.contexts.get("plan_run", run_id, "result")
    assert isinstance(result, dict)
    assert "Recovered:" in str(result["text"])


def test_scratchpad_tool_is_persisted_and_injected(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(
        Any,
        _FakeLlm(
            replies=[
                {
                    "content": "Noted. I will keep track carefully.",
                    "tool_calls": None,
                },
            ]
        ),
    )
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="remember this context")
    asyncio.run(bot.on_incoming(message))
    assert len(channel.sent) == 1
    assert "Noted." in channel.sent[0][1]

    ctx_msg = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/ctxfull")
    asyncio.run(bot.on_incoming(ctx_msg))
    assert len(channel.sent) == 2
    assert "Execution scratchpad (private state" in channel.sent[1][1]


def test_tool_results_are_persisted_in_context(tmp_path) -> None:
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
                            "function": {
                                "name": "timer__time_now",
                                "arguments": '{"timezone_name":"UTC"}',
                            },
                        }
                    ],
                },
                {"content": "Done.", "tool_calls": None},
            ]
        ),
    )

    class _ToolMcp(_FakeMcp):
        def list_openai_tools(self) -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "timer__time_now",
                        "description": "Get current date-time in ISO format for a timezone.",
                        "parameters": {
                            "type": "object",
                            "properties": {"timezone_name": {"type": "string"}},
                        },
                    },
                }
            ]

        async def call_tool(self, fn_name: str, args: dict) -> str:
            del fn_name, args
            return "tool output: 2026-03-08T10:00:00Z"

    bot.mcp = cast(Any, _ToolMcp())
    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    asyncio.run(bot.on_incoming(message))

    stored = bot.contexts.get("chat", "telegram:42", "tool_results")
    assert isinstance(stored, dict)
    events = stored.get("events")
    assert isinstance(events, list)
    assert len(events) == 1
    assert events[0]["tool"] == "timer__time_now"
    assert "tool output" in str(events[0]["result_preview"])


def test_scratchpad_command_can_force_write_and_read(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, _FakeLlm(replies=[{"content": "ok", "tool_calls": None}]))
    bot.mcp = cast(Any, _FakeMcp())

    set_msg = IncomingMessage(
        channel="telegram",
        chat_id="42",
        user_id="u1",
        text="/scratchpad clear",
    )
    asyncio.run(bot.on_incoming(set_msg))
    assert "Scratchpad cleared" in channel.sent[-1][1]

    show_msg = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/scratchpad show")
    asyncio.run(bot.on_incoming(show_msg))
    assert "Structured scratchpad" in channel.sent[-1][1]


def test_session_scratchpad_write_tool_persists_state(tmp_path) -> None:
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
                            "id": "call_sp_1",
                            "type": "function",
                            "function": {
                                "name": SCRATCHPAD_TOOL_NAME,
                                "arguments": json.dumps(
                                    {
                                        "mode": "init",
                                        "goal": "Find best laptop",
                                        "context": "Budget under 1500 USD",
                                        "known_facts": ["User prefers 14 inch", "Needs 16GB RAM"],
                                        "current_step": "Collect candidate models",
                                        "next_step": "Compare prices",
                                        "tool_journal": ["Initialized scratchpad"],
                                    }
                                ),
                            },
                        }
                    ],
                },
                {"content": "Working on it.", "tool_calls": None},
            ]
        ),
    )
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="help me buy laptop")
    asyncio.run(bot.on_incoming(message))

    assert "Working on it." in channel.sent[-1][1]
    state = bot.contexts.get("chat", "telegram:42", "scratchpad")
    assert isinstance(state, dict)
    assert state["goal"] == "Find best laptop"
    assert state["context"] == "Budget under 1500 USD"
    assert state["current_step"] == "Collect candidate models"
    assert state["next_step"] == "Compare prices"
    assert state["known_facts"] == ["User prefers 14 inch", "Needs 16GB RAM"]
    assert state["tool_journal"] == ["Initialized scratchpad"]
    assert isinstance(state["updated_at"], str) and state["updated_at"]


def test_session_scratchpad_write_clips_long_fields(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    long_field = "g" * (MAX_FIELD_CHARS + 50)
    long_context = "c" * (MAX_CONTEXT_CHARS + 80)
    known_facts = [f"fact-{idx}-" + ("k" * (MAX_FIELD_CHARS + 10)) for idx in range(MAX_KNOWN_FACTS + 5)]
    tool_journal = [f"journal-{idx}-" + ("j" * (MAX_FIELD_CHARS + 10)) for idx in range(MAX_TOOL_JOURNAL + 7)]
    bot.llm = cast(
        Any,
        _FakeLlm(
            replies=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_sp_2",
                            "type": "function",
                            "function": {
                                "name": SCRATCHPAD_TOOL_NAME,
                                "arguments": json.dumps(
                                    {
                                        "mode": "init",
                                        "goal": long_field,
                                        "context": long_context,
                                        "known_facts": known_facts,
                                        "current_step": long_field,
                                        "next_step": long_field,
                                        "tool_journal": tool_journal,
                                    }
                                ),
                            },
                        }
                    ],
                },
                {"content": "Done.", "tool_calls": None},
            ]
        ),
    )
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="start")
    asyncio.run(bot.on_incoming(message))

    state = bot.contexts.get("chat", "telegram:42", "scratchpad")
    assert isinstance(state, dict)
    assert len(state["goal"]) == MAX_FIELD_CHARS
    assert len(state["context"]) == MAX_CONTEXT_CHARS
    assert len(state["current_step"]) == MAX_FIELD_CHARS
    assert len(state["next_step"]) == MAX_FIELD_CHARS
    assert len(state["known_facts"]) == MAX_KNOWN_FACTS
    assert len(state["tool_journal"]) == MAX_TOOL_JOURNAL
    assert all(len(item) <= MAX_FIELD_CHARS for item in state["known_facts"])
    assert all(len(item) <= MAX_FIELD_CHARS for item in state["tool_journal"])


def test_phase2_blocks_external_tool_when_scratchpad_update_missing(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    llm = _RecordingFakeLlm(
        replies=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_ext_1",
                        "type": "function",
                        "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                    }
                ],
            },
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_ext_2",
                        "type": "function",
                        "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                    }
                ],
            },
            {"content": "Stopped for protocol correction.", "tool_calls": None},
        ]
    )
    bot.llm = cast(Any, llm)

    class _CountingMcp(_FakeMcp):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call_tool(self, fn_name: str, args: dict) -> str:
            del args
            self.calls.append(fn_name)
            return "tool output"

    mcp = _CountingMcp()
    bot.mcp = cast(Any, mcp)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    asyncio.run(bot.on_incoming(message))

    assert len(mcp.calls) == 1
    assert mcp.calls == ["timer__time_now"]
    assert "Stopped for protocol correction." in channel.sent[-1][1]
    merged_prompt_text = "\n".join(str(msg.get("content", "")) for msg in llm.calls_messages[-1])
    assert "Protocol violation:" in merged_prompt_text
    assert SCRATCHPAD_TOOL_NAME in merged_prompt_text


def test_phase2_recovers_after_scratchpad_update_then_external_tool(tmp_path) -> None:
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
                            "id": "call_ext_1",
                            "type": "function",
                            "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                        }
                    ],
                },
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_sp_append",
                            "type": "function",
                            "function": {
                                "name": SCRATCHPAD_TOOL_NAME,
                                "arguments": json.dumps(
                                    {
                                        "mode": "append",
                                        "current_step": "Captured current time",
                                        "next_step": "Check again",
                                        "tool_journal": ["timer__time_now returned UTC time"],
                                    }
                                ),
                            },
                        },
                        {
                            "id": "call_ext_2",
                            "type": "function",
                            "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                        },
                    ],
                },
                {"content": "Done.", "tool_calls": None},
            ]
        ),
    )

    class _CountingMcp(_FakeMcp):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call_tool(self, fn_name: str, args: dict) -> str:
            del args
            self.calls.append(fn_name)
            return "tool output"

    mcp = _CountingMcp()
    bot.mcp = cast(Any, mcp)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    asyncio.run(bot.on_incoming(message))

    assert channel.sent[-1][1] == "Done."
    assert mcp.calls == ["timer__time_now", "timer__time_now"]
    state = bot.contexts.get("chat", "telegram:42", "scratchpad")
    assert isinstance(state, dict)
    assert state["current_step"] == "Captured current time"
    assert state["next_step"] == "Check again"
    assert "timer__time_now returned UTC time" in state["tool_journal"]


def test_phase2_aborts_after_two_protocol_retries(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    llm = _RecordingFakeLlm(
        replies=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_ext_1",
                        "type": "function",
                        "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                    }
                ],
            },
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_ext_2",
                        "type": "function",
                        "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                    }
                ],
            },
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_ext_3",
                        "type": "function",
                        "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                    }
                ],
            },
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_ext_4",
                        "type": "function",
                        "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                    }
                ],
            },
        ]
    )
    bot.llm = cast(Any, llm)

    class _CountingMcp(_FakeMcp):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call_tool(self, fn_name: str, args: dict) -> str:
            del args
            self.calls.append(fn_name)
            return "tool output"

    mcp = _CountingMcp()
    bot.mcp = cast(Any, mcp)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    asyncio.run(bot.on_incoming(message))

    assert mcp.calls == ["timer__time_now"]
    assert channel.sent[-1][1] == SCRATCHPAD_PROTOCOL_ABORT_REPLY
    last_messages = llm.calls_messages[-1]
    assistant_tool_messages = [
        msg for msg in last_messages if str(msg.get("role")) == "assistant" and msg.get("tool_calls")
    ]
    assert len(assistant_tool_messages) == 1


def test_empty_final_reply_uses_fallback(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, _FakeLlm(replies=[{"content": "\n\n\n", "tool_calls": None}]))
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    asyncio.run(bot.on_incoming(message))

    assert channel.sent[-1][1] == EMPTY_REPLY_FALLBACK
