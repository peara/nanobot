from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.channels.base import IncomingMessage
from nanobot.core import BotCore
from nanobot.messages import SubagentResultMessage, UserMessage


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _make_config() -> Any:
    from nanobot.config import AppConfig, ModelConfig

    return AppConfig(
        assistant_name="TestBot",
        database_path=":memory:",
        scheduler_db_path=":memory:",
        poll_interval_seconds=20,
        system_prompt_template="You are {assistant_name}.",
        subagent_system_prompt="You are an autonomous agent.",
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost", api_key="test", model="test"),
        channels=[],
        mcp_servers=[],
    )


@pytest.fixture
def bot() -> BotCore:
    config = _make_config()
    channels: dict[str, Any] = {"telegram": _FakeChannel()}
    return BotCore(config, channels)


def test_user_message_scope() -> None:
    msg = UserMessage(channel="telegram", chat_id="123", text="hello")
    assert msg.scope == "telegram:123"


@pytest.mark.asyncio
async def test_on_incoming_enqueues_user_message(bot: BotCore) -> None:
    incoming = IncomingMessage(
        channel="telegram",
        chat_id="123",
        user_id="user1",
        text="hello world",
    )

    await bot.on_incoming(incoming)

    assert bot._message_queue.qsize() == 1
    msg = bot._message_queue.get_nowait()
    assert isinstance(msg, UserMessage)
    assert msg.channel == "telegram"
    assert msg.chat_id == "123"
    assert msg.text == "hello world"


@pytest.mark.asyncio
async def test_on_subagent_result_enqueues_message(bot: BotCore) -> None:
    result = SubagentResultMessage(
        run_id="subagent-test123",
        parent_scope="telegram:123",
        success=True,
        summary="Task done",
        tool_trace=[],
    )

    await bot.on_subagent_result(result)

    assert bot._message_queue.qsize() == 1
    msg = bot._message_queue.get_nowait()
    assert isinstance(msg, SubagentResultMessage)
    assert msg.run_id == "subagent-test123"


def test_should_notify_user_returns_false_for_unsuccessful(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Failed",
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_false_for_empty_summary(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="   ",
        tool_trace=[{"name": "tool", "args": {}, "result_preview": "ok"}],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_false_for_no_action_needed(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="NO_ACTION_NEEDED",
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_false_for_no_tools_and_short_summary(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="OK",  # Less than 50 chars
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_true_for_no_tools_but_long_summary(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="This is a substantial message that exceeds fifty characters for length.",
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is True


def test_should_notify_user_returns_true_for_tools_used(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="Done checking weather.",
        tool_trace=[{"name": "timer__time_now", "args": {}, "result_preview": "12:00"}],
    )
    assert bot._should_notify_user(msg) is True


@pytest.mark.asyncio
async def test_handle_subagent_result_stores_and_notifies(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="subagent-test",
        parent_scope="telegram:123",
        success=True,
        summary="I completed the task.",
        tool_trace=[{"name": "timer__time_now", "args": {}, "result_preview": "12:00"}],
    )

    with patch.object(bot.memory, "add_message") as mock_add:
        with patch.object(bot.contexts, "put") as mock_put:
            with patch.object(bot, "_send", new_callable=AsyncMock) as mock_send:
                await bot._handle_subagent_result(msg)

                mock_put.assert_called_once()
                args = mock_put.call_args[0]
                assert args[0] == "subagent_run"
                assert args[1] == "subagent-test"
                assert args[2] == "result"

                mock_add.assert_called_once_with("telegram:123", "assistant", "I completed the task.")
                mock_send.assert_called_once_with("telegram:123", "I completed the task.")


@pytest.mark.asyncio
async def test_handle_subagent_result_does_not_notify_when_should_not(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="subagent-test",
        parent_scope="telegram:123",
        success=True,
        summary="NO_ACTION_NEEDED",
        tool_trace=[],
    )

    with patch.object(bot.memory, "add_message") as mock_add:
        with patch.object(bot.contexts, "put"):
            with patch.object(bot, "_send", new_callable=AsyncMock) as mock_send:
                await bot._handle_subagent_result(msg)

                mock_add.assert_not_called()
                mock_send.assert_not_called()
