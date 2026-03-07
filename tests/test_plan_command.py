from __future__ import annotations

import asyncio
from typing import Any, cast

from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import BotCore


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class _FakeLlm:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = replies
        self._idx = 0

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        del messages, tools
        if self._idx >= len(self._replies):
            raise RuntimeError("No fake LLM reply left")
        reply = self._replies[self._idx]
        self._idx += 1
        return reply


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
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "session__scratchpad_write",
                                "arguments": '{"mode":"replace","content":"User wants links for ebay X-500 search"}',
                            },
                        }
                    ],
                },
                {"content": "Noted. I will keep track carefully.", "tool_calls": None},
            ]
        ),
    )
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="remember this context")
    asyncio.run(bot.on_incoming(message))
    assert len(channel.sent) == 1
    assert "Noted." in channel.sent[0][1]

    scratchpad = bot.contexts.get("chat", "telegram:42", "scratchpad")
    assert isinstance(scratchpad, dict)
    assert "ebay X-500 search" in str(scratchpad.get("text", ""))

    ctx_msg = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/ctxfull")
    asyncio.run(bot.on_incoming(ctx_msg))
    assert len(channel.sent) == 2
    assert "Session scratchpad (private notes" in channel.sent[1][1]
