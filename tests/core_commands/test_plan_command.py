from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import EMPTY_REPLY_FALLBACK, BotCore
from nanobot.core_scratchpad import (
    MAX_CONTEXT_CHARS,
    MAX_FIELD_CHARS,
    MAX_KNOWN_FACTS,
    MAX_TOOL_JOURNAL,
    SCRATCHPAD_TOOL_NAME,
    get_scratchpad_state,
)
from nanobot.tools.base import Tool


def _last_run_id_for_scope(bot: BotCore, scope: str) -> str | None:
    runs = bot.subagent_manager.list_by_scope(scope, status="completed", limit=1)
    if not runs:
        runs = bot.subagent_manager.list_by_scope(scope, limit=1)
    return runs[0].id if runs else None


def _await_process(bot: BotCore, message: IncomingMessage) -> None:
    asyncio.run(bot.on_incoming(message))
    asyncio.run(bot._process_one_message())


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
        *,
        scope: str | None = None,
        cancel_token: Any | None = None,
    ) -> dict:
        del messages, tools, response_format, scope, cancel_token
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
        *,
        scope: str | None = None,
        cancel_token: Any | None = None,
    ) -> dict:
        self.calls_messages.append(messages)
        return await super().chat(messages, tools, response_format, scope=scope, cancel_token=cancel_token)


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


class _CountingFakeTool(Tool):
    def __init__(self, name: str, result: str = "tool output") -> None:
        self._name = name
        self._result = result
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Counting fake tool {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        del args
        self.calls.append(self._name)
        return self._result


def _build_config(tmp_path) -> AppConfig:
    db_path = str(tmp_path / "nanobot.db")
    scheduler_db_path = str(tmp_path / "scheduler.db")
    plan_db_path = str(tmp_path / "plans.db")
    prompt_db_path = str(tmp_path / "prompts.db")
    skill_db_path = str(tmp_path / "skills.db")
    return AppConfig(
        assistant_name="Nano",
        database_path=db_path,
        scheduler_db_path=scheduler_db_path,
        plan_db_path=plan_db_path,
        skill_db_path=skill_db_path,
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost:11434/v1", api_key="dummy", model="dummy-model"),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
        prompt_db_path=prompt_db_path,
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
    # bot.mcp removed - tools registry in use
    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/plan book me a flight")
    _await_process(bot, message)

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

    # Verify plan was persisted
    plans = bot.plan_store.list_plans(source_type="plan_command", limit=10)
    assert len(plans) == 1
    assert plans[0].goal == "Book flight"
    assert plans[0].source_scope == "telegram:42"


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
    # bot.mcp removed - tools registry in use

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/plan find me a camera")
    _await_process(bot, message)

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
    # bot.mcp removed - tools registry in use

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="remember this context")
    _await_process(bot, message)
    assert len(channel.sent) == 1
    assert "Noted." in channel.sent[0][1]

    ctx_msg = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/ctxfull")
    _await_process(bot, ctx_msg)
    assert len(channel.sent) == 2
    assert "model" in channel.sent[1][1].lower()


def test_new_turn_gets_fresh_scratchpad_per_run(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    llm = _RecordingFakeLlm(replies=[{"content": "ok", "tool_calls": None}])
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, llm)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="set a reminder")
    _await_process(bot, message)

    scratchpad_messages = [
        item for item in llm.calls_messages[0] if "[Internal scratchpad state" in str(item.get("content", ""))
    ]
    assert len(scratchpad_messages) == 1
    scratchpad_text = str(scratchpad_messages[0]["content"])
    assert '"goal": ""' in scratchpad_text


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

    bot.tools.register(_FakeTool("timer__time_now", result="tool output: 2026-03-08T10:00:00Z"))

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    _await_process(bot, message)

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
    # bot.mcp removed - tools registry in use

    set_msg = IncomingMessage(
        channel="telegram",
        chat_id="42",
        user_id="u1",
        text="/scratchpad clear",
    )
    _await_process(bot, set_msg)
    assert "Scratchpad cleared" in channel.sent[-1][1]

    show_msg = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/scratchpad show")
    _await_process(bot, show_msg)
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
                # Text-only stop after init triggers the protocol nudge.
                {"content": "Working on it.", "tool_calls": None},
                # After nudge the model self-corrects with finalize; the
                # next call (no tools) returns the user-facing answer.
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_sp_2",
                            "type": "function",
                            "function": {
                                "name": SCRATCHPAD_TOOL_NAME,
                                "arguments": json.dumps({"mode": "finalize"}),
                            },
                        }
                    ],
                },
                {"content": "Initialized: searching for laptops under 1500 USD.", "tool_calls": None},
            ]
        ),
    )
    # bot.mcp removed - tools registry in use

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="help me buy laptop")
    _await_process(bot, message)

    assert "Initialized: searching for laptops" in channel.sent[-1][1]
    run_id = _last_run_id_for_scope(bot, "telegram:42")
    assert run_id is not None
    state = get_scratchpad_state(bot, "telegram:42", run_id=run_id)
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
    # bot.mcp removed - tools registry in use

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="start")
    _await_process(bot, message)

    run_id = _last_run_id_for_scope(bot, "telegram:42")
    assert run_id is not None
    state = get_scratchpad_state(bot, "telegram:42", run_id=run_id)
    assert isinstance(state, dict)
    assert len(state["goal"]) == MAX_FIELD_CHARS
    assert len(state["context"]) == MAX_CONTEXT_CHARS
    assert len(state["current_step"]) == MAX_FIELD_CHARS
    assert len(state["next_step"]) == MAX_FIELD_CHARS
    assert len(state["known_facts"]) == MAX_KNOWN_FACTS
    assert len(state["tool_journal"]) == MAX_TOOL_JOURNAL
    assert all(len(item) <= MAX_FIELD_CHARS for item in state["known_facts"])
    assert all(len(item) <= MAX_FIELD_CHARS for item in state["tool_journal"])


def test_phase2_logs_violation_but_does_not_block(tmp_path) -> None:
    """With relaxed protocol we only log violation and continue executing tools."""
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
                            "id": "call_ext_2",
                            "type": "function",
                            "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC"}'},
                        }
                    ],
                },
                {"content": "Stopped for protocol correction.", "tool_calls": None},
            ]
        ),
    )

    counting_tool = _CountingFakeTool("timer__time_now")
    bot.tools.register(counting_tool)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    _await_process(bot, message)

    assert counting_tool.calls == ["timer__time_now", "timer__time_now"]
    assert "Stopped for protocol correction." in channel.sent[-1][1]


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

    counting_tool = _CountingFakeTool("timer__time_now")
    bot.tools.register(counting_tool)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    _await_process(bot, message)

    assert channel.sent[-1][1] == "Done."
    assert counting_tool.calls == ["timer__time_now", "timer__time_now"]
    run_id = _last_run_id_for_scope(bot, "telegram:42")
    assert run_id is not None
    state = get_scratchpad_state(bot, "telegram:42", run_id=run_id)
    assert isinstance(state, dict)
    assert state["current_step"] == "Captured current time"
    assert state["next_step"] == "Check again"
    assert "timer__time_now returned UTC time" in state["tool_journal"]


def test_phase2_relaxed_no_abort_continues_executing(tmp_path) -> None:
    """Test relaxed protocol: all tools run. Use different args to avoid repeated call guard."""
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
                            "id": "call_ext_2",
                            "type": "function",
                            "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC+1"}'},
                        }
                    ],
                },
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_ext_3",
                            "type": "function",
                            "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC+2"}'},
                        }
                    ],
                },
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_ext_4",
                            "type": "function",
                            "function": {"name": "timer__time_now", "arguments": '{"timezone_name":"UTC+3"}'},
                        }
                    ],
                },
                {"content": "Done after four calls.", "tool_calls": None},
            ]
        ),
    )

    counting_tool = _CountingFakeTool("timer__time_now")
    bot.tools.register(counting_tool)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    _await_process(bot, message)

    assert counting_tool.calls == ["timer__time_now", "timer__time_now", "timer__time_now", "timer__time_now"]
    assert channel.sent[-1][1] == "Done after four calls."


def test_empty_final_reply_uses_fallback(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = cast(Any, _FakeLlm(replies=[{"content": "\n\n\n", "tool_calls": None}]))
    # bot.mcp removed - tools registry in use

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    _await_process(bot, message)

    assert channel.sent[-1][1] == EMPTY_REPLY_FALLBACK
