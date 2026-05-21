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
    import tempfile

    from nanobot.config import AppConfig, ModelConfig

    tmp = tempfile.mkdtemp()
    return AppConfig(
        assistant_name="TestBot",
        database_path=f"{tmp}/nanobot.db",
        scheduler_db_path=f"{tmp}/scheduler.db",
        plan_db_path=f"{tmp}/plans.db",
        skill_db_path=f"{tmp}/skills.db",
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost", api_key="test", model="test"),
        channels=[],
        mcp_servers=[],
        prompt_db_path=f"{tmp}/prompts.db",
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


def test_should_notify_user_returns_true_for_unsuccessful(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Failed",
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is True


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


def test_format_failure_summary_context_overflow(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Error: context overflow",
        tool_trace=[],
        metadata={"error": "Error code: 400 - request (43792 tokens) exceeds the available context size (32768 tokens)"},
    )
    summary = bot._format_failure_summary(msg)
    assert "context window" in summary
    assert "retry" in summary


def test_format_failure_summary_exceed_context_size_error(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Error",
        tool_trace=[],
        metadata={"error": "exceed_context_size_error: token limit reached"},
    )
    summary = bot._format_failure_summary(msg)
    assert "context window" in summary


def test_format_failure_summary_generic_error(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Error",
        tool_trace=[],
        metadata={"error": "Connection timed out"},
    )
    summary = bot._format_failure_summary(msg)
    assert "Connection timed out" in summary
    assert "Scheduled task failed" in summary


def test_format_failure_summary_no_metadata(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Error",
        tool_trace=[],
    )
    summary = bot._format_failure_summary(msg)
    assert "unexpected error" in summary


def test_format_failure_summary_truncates_long_error(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Error",
        tool_trace=[],
        metadata={"error": "x" * 500},
    )
    summary = bot._format_failure_summary(msg)
    assert len(summary) < 400


@pytest.mark.asyncio
async def test_handle_subagent_result_notifies_when_should(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="subagent-test",
        parent_scope="telegram:123",
        success=True,
        summary="I completed the task.",
        tool_trace=[{"name": "timer__time_now", "args": {}, "result_preview": "12:00"}],
    )

    with patch.object(bot.memory, "add_message") as mock_add:
        with patch.object(bot, "_send", new_callable=AsyncMock) as mock_send:
            await bot._handle_subagent_result(msg)

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
        with patch.object(bot, "_send", new_callable=AsyncMock) as mock_send:
            await bot._handle_subagent_result(msg)

            mock_add.assert_not_called()
            mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_handle_subagent_result_notifies_on_failure_with_context_overflow(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="subagent-test",
        parent_scope="telegram:123",
        success=False,
        summary="Error: BadRequestError",
        tool_trace=[],
        metadata={"error": "request (43792 tokens) exceeds the available context size (32768 tokens)"},
    )

    with patch.object(bot.memory, "add_message") as mock_add:
        with patch.object(bot, "_send", new_callable=AsyncMock) as mock_send:
            await bot._handle_subagent_result(msg)

            assert mock_add.call_count == 1
            assert mock_send.call_count == 1
            sent_text = mock_send.call_args[0][1]
            assert "context window" in sent_text
