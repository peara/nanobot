from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import BotCore


def _await_process(bot: BotCore, message: IncomingMessage) -> None:
    asyncio.run(bot.on_incoming(message))
    asyncio.run(bot._process_one_message())


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class _FakeLlm:
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
        return {"content": "ok", "tool_calls": None}


class _FakeMcp:
    def list_openai_tools(self) -> list[dict]:
        return []

    async def call_tool(self, fn_name: str, args: dict) -> str:
        del fn_name, args
        raise RuntimeError("No tools expected")


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


def test_status_command_shows_free_when_no_active_requests(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = _FakeLlm()  # type: ignore
    bot.mcp = _FakeMcp()  # type: ignore

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/status")
    _await_process(bot, message)

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "42"
    assert "🟢 Free" in channel.sent[0][1]


def test_status_command_shows_busy_with_elapsed_time(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = _FakeLlm()  # type: ignore
    bot.mcp = _FakeMcp()  # type: ignore

    scope = "telegram:42"
    from nanobot.core import ActiveRequest

    now = datetime.now()
    bot.active_requests[scope] = ActiveRequest(chat_id="42", started_at=now, current_step="processing")

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/status")
    _await_process(bot, message)

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "42"
    assert "🔴 Busy" in channel.sent[0][1]
    assert "processing" in channel.sent[0][1]


def test_status_command_shows_minutes_in_elapsed_time(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = _FakeLlm()  # type: ignore
    bot.mcp = _FakeMcp()  # type: ignore

    scope = "telegram:42"
    from nanobot.core import ActiveRequest

    now = datetime.now() - timedelta(minutes=5, seconds=10)
    bot.active_requests[scope] = ActiveRequest(chat_id="42", started_at=now, current_step="heavy computation")

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/status")
    _await_process(bot, message)

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "42"
    assert "5m" in channel.sent[0][1]
    assert "heavy computation" in channel.sent[0][1]


def test_status_command_shows_last_activity_when_free(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = _FakeLlm()  # type: ignore
    bot.mcp = _FakeMcp()  # type: ignore

    scope = "telegram:42"
    last_activity = datetime.now() - timedelta(seconds=30)
    bot.contexts.put("chat", scope, "last_assistant_message", {"text": "hello", "timestamp": last_activity.isoformat()})

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/status")
    with patch.object(bot.contexts, "get", return_value={"text": "hello", "timestamp": last_activity.isoformat()}):
        _await_process(bot, message)

        assert len(channel.sent) == 1
        assert channel.sent[0][0] == "42"
        assert "🟢 Free" in channel.sent[0][1]
        assert "ago" in channel.sent[0][1]


def test_status_command_shows_free_without_time_when_no_activity(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = _FakeLlm()  # type: ignore
    bot.mcp = _FakeMcp()  # type: ignore

    scope = "telegram:42"
    bot.contexts.put("chat", scope, "last_assistant_message", None)

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/status")
    _await_process(bot, message)

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "42"
    assert channel.sent[0][1] == "🟢 Free"


def test_status_command_with_seconds_only_elapsed(tmp_path) -> None:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    bot.llm = _FakeLlm()  # type: ignore
    bot.mcp = _FakeMcp()  # type: ignore

    scope = "telegram:42"
    from nanobot.core import ActiveRequest

    now = datetime.now() - timedelta(seconds=15)
    bot.active_requests[scope] = ActiveRequest(chat_id="42", started_at=now, current_step="quick task")

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/status")
    _await_process(bot, message)

    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "42"
    assert "🔴 Busy" in channel.sent[0][1]
    assert "15s" in channel.sent[0][1]
    assert "quick task" in channel.sent[0][1]
