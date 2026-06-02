from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from nanobot.cancel_token import CancellationToken
from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import ActiveRequest, BotCore


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


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _make_bot(tmp_path) -> BotCore:
    config = _build_config(tmp_path)
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    return bot


class TestStopCommand:
    def test_stop_cancels_active_requests(self, tmp_path) -> None:
        bot = _make_bot(tmp_path)
        scope = "telegram:42"
        token = CancellationToken()
        bot._cancel_tokens[scope] = token
        bot.active_requests[scope] = ActiveRequest(chat_id="42", started_at=datetime.now(), current_step="processing")

        handler = bot.command_manager._commands["/stop"](core=bot)
        asyncio.run(handler.handle_with_error_handling("/stop", scope))

        assert token.is_cancelled is True
        assert len(bot.channels["telegram"].sent) == 1
        assert "Cancelled 1 active request" in bot.channels["telegram"].sent[0][1]

    def test_stop_no_active_requests(self, tmp_path) -> None:
        bot = _make_bot(tmp_path)

        handler = bot.command_manager._commands["/stop"](core=bot)
        asyncio.run(handler.handle_with_error_handling("/stop", "telegram:42"))

        assert len(bot.channels["telegram"].sent) == 1
        assert "No active requests" in bot.channels["telegram"].sent[0][1]

    def test_stop_cancels_multiple_requests(self, tmp_path) -> None:
        bot = _make_bot(tmp_path)

        scopes = ["telegram:1", "telegram:2", "scheduler:task_5"]
        tokens = []
        for scope in scopes:
            t = CancellationToken()
            tokens.append(t)
            bot._cancel_tokens[scope] = t
            bot.active_requests[scope] = ActiveRequest(chat_id=scope, started_at=datetime.now(), current_step="working")

        handler = bot.command_manager._commands["/stop"](core=bot)
        asyncio.run(handler.handle_with_error_handling("/stop", "telegram:42"))

        assert len(bot.channels["telegram"].sent) == 1
        assert "Cancelled 3 active request" in bot.channels["telegram"].sent[0][1]
        for t in tokens:
            assert t.is_cancelled is True

    def test_cancel_request_returns_true(self, tmp_path) -> None:
        bot = _make_bot(tmp_path)

        scope = "telegram:99"
        token = CancellationToken()
        bot._cancel_tokens[scope] = token

        result = bot.cancel_request(scope)
        assert result is True
        assert token.is_cancelled is True

    def test_cancel_request_returns_false_for_unknown_scope(self, tmp_path) -> None:
        bot = _make_bot(tmp_path)
        result = bot.cancel_request("nonexistent:scope")
        assert result is False


class TestCommandPreDispatch:
    @pytest.mark.asyncio
    async def test_command_not_enqueued(self, tmp_path) -> None:
        """Commands should be dispatched immediately via asyncio.create_task,
        not enqueued in the message queue."""
        bot = _make_bot(tmp_path)
        message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/status")

        await bot.on_incoming(message)

        assert bot._message_queue.empty()

    @pytest.mark.asyncio
    async def test_non_command_enqueued(self, tmp_path) -> None:
        """Non-command messages should be enqueued as before."""
        bot = _make_bot(tmp_path)
        message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello world")

        await bot.on_incoming(message)

        assert bot._message_queue.qsize() == 1
        msg = bot._message_queue.get_nowait()
        assert msg.text == "hello world"

    @pytest.mark.asyncio
    async def test_stop_command_cancels_via_dispatch(self, tmp_path) -> None:
        """A /stop command dispatched via on_incoming cancels the token."""
        bot = _make_bot(tmp_path)
        scope = "telegram:42"

        token = CancellationToken()
        bot._cancel_tokens[scope] = token
        bot.active_requests[scope] = ActiveRequest(chat_id="42", started_at=datetime.now(), current_step="processing")

        message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="/stop")
        await bot.on_incoming(message)

        assert bot._message_queue.empty()
        await asyncio.sleep(0.05)

        assert token.is_cancelled is True
