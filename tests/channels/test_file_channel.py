from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.channels.file import FileChannel, FileTraceHook
from nanobot.hooks import ToolCallEvent


class TestFileChannelInit:
    def test_auto_session_id(self, tmp_path: Path) -> None:
        ch = FileChannel(sessions_dir=str(tmp_path / "sessions"))
        assert ch.session_id.startswith("session_")
        assert len(ch.session_id) > len("session_YYYYMMDD_HHMMSS_")

    def test_custom_session_id(self, tmp_path: Path) -> None:
        ch = FileChannel(sessions_dir=str(tmp_path / "sessions"), session_id="my_session")
        assert ch.session_id == "my_session"

    def test_default_params(self, tmp_path: Path) -> None:
        ch = FileChannel(sessions_dir=str(tmp_path / "sessions"))
        assert ch.capture_tool_calls is False
        assert ch.poll_interval == 0.5
        assert ch.user_id == "file_user"

    def test_custom_params(self, tmp_path: Path) -> None:
        ch = FileChannel(
            sessions_dir=str(tmp_path / "sessions"),
            session_id="test42",
            capture_tool_calls=True,
            poll_interval=1.0,
            user_id="sisyphus",
        )
        assert ch.capture_tool_calls is True
        assert ch.poll_interval == 1.0
        assert ch.user_id == "sisyphus"

    def test_in_out_dirs(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="test_dirs")
        assert ch._in_dir == Path(sessions) / "in"
        assert ch._out_dir == Path(sessions) / "out"

    def test_in_out_files(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="test_files")
        assert ch._in_file == Path(sessions) / "in" / "test_files.jsonl"
        assert ch._out_file == Path(sessions) / "out" / "test_files.jsonl"


class TestFileChannelStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_dirs_and_files(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="start_test")
        ch.set_handler(AsyncMock())

        await ch.start()

        assert ch._in_dir.exists()
        assert ch._out_dir.exists()
        assert ch._in_file.exists()

        lines = ch._out_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        event = json.loads(lines[0])
        assert event["type"] == "session_start"
        assert event["session_id"] == "start_test"
        assert "timestamp" in event

        await ch.stop()

    @pytest.mark.asyncio
    async def test_stop_writes_session_end(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="stop_test")
        ch.set_handler(AsyncMock())

        await ch.start()
        await ch.stop()

        lines = ch._out_file.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in lines]
        types = [e["type"] for e in events]
        assert "session_start" in types
        assert "session_end" in types

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="lifecycle")
        ch.set_handler(AsyncMock())

        await ch.start()
        assert ch._poll_task is not None
        assert not ch._stop_event.is_set()

        await ch.stop()
        assert ch._stop_event.is_set()
        assert ch._poll_task is None or ch._poll_task.done()


class TestFileChannelSend:
    @pytest.mark.asyncio
    async def test_send_writes_assistant_message(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="send_test")
        ch.set_handler(AsyncMock())
        await ch.start()

        await ch.send("test_chat_id", "Hello from bot!")

        lines = ch._out_file.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in lines]

        assistant_msgs = [e for e in events if e["type"] == "assistant_message"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["text"] == "Hello from bot!"

        turn_complete = [e for e in events if e["type"] == "turn_complete"]
        assert len(turn_complete) == 1

        await ch.stop()

    @pytest.mark.asyncio
    async def test_send_writes_turn_complete_with_reply_to(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="send_reply")
        ch.set_handler(AsyncMock())
        await ch.start()

        ch._last_user_msg_timestamp = "2025-04-18T14:30:55Z"
        await ch.send("test_chat_id", "Response text")

        lines = ch._out_file.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in lines]
        turn_complete = [e for e in events if e["type"] == "turn_complete"]
        assert turn_complete[0]["reply_to"] == "2025-04-18T14:30:55Z"

        await ch.stop()


class TestFileChannelInject:
    @pytest.mark.asyncio
    async def test_inject_writes_to_in_file(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="inject_test")
        handler = AsyncMock()
        ch.set_handler(handler)
        await ch.start()

        await ch.inject("What is the weather?", user_id="tester")

        in_lines = ch._in_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(in_lines) >= 1
        event = json.loads(in_lines[-1])
        assert event["type"] == "user_message"
        assert event["text"] == "What is the weather?"
        assert event["user_id"] == "tester"

        handler.assert_called_once()
        msg = handler.call_args[0][0]
        assert msg.channel == "file"
        assert msg.text == "What is the weather?"
        assert msg.user_id == "tester"

        await ch.stop()

    @pytest.mark.asyncio
    async def test_inject_updates_last_user_msg_timestamp(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="inject_ts")
        ch.set_handler(AsyncMock())
        await ch.start()

        await ch.inject("Hello", user_id="tester")
        assert ch._last_user_msg_timestamp != ""

        await ch.stop()


class TestFileChannelWaitForResponse:
    @pytest.mark.asyncio
    async def test_wait_for_response_returns_assistant_text(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="wait_test")
        ch.set_handler(AsyncMock())
        await ch.start()

        async def write_response() -> None:
            await asyncio.sleep(0.1)
            ch._write_event({"type": "assistant_message", "text": "The weather is sunny."})
            ch._write_event({"type": "turn_complete", "reply_to": ""})

        asyncio.create_task(write_response())

        result = await ch.wait_for_response(timeout=5)
        assert result == "The weather is sunny."

        await ch.stop()

    @pytest.mark.asyncio
    async def test_wait_for_response_timeout(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="wait_timeout")
        ch.set_handler(AsyncMock())
        await ch.start()

        with pytest.raises(TimeoutError, match="No response within"):
            await ch.wait_for_response(timeout=0.3)

        await ch.stop()


class TestFileChannelPolling:
    @pytest.mark.asyncio
    async def test_poll_reads_user_messages(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="poll_test", poll_interval=0.1)
        handler = AsyncMock()
        ch.set_handler(handler)
        await ch.start()

        event = json.dumps(
            {
                "type": "user_message",
                "text": "Hello from polling",
                "user_id": "poller",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        ch._in_file.write_text(event + "\n", encoding="utf-8")

        await asyncio.sleep(0.5)

        handler.assert_called()
        msg = handler.call_args[0][0]
        assert msg.channel == "file"
        assert msg.text == "Hello from polling"
        assert msg.user_id == "poller"

        await ch.stop()

    @pytest.mark.asyncio
    async def test_poll_handles_invalid_json(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="poll_invalid", poll_interval=0.1)
        handler = AsyncMock()
        ch.set_handler(handler)
        await ch.start()

        ch._in_file.write_text("not json\n", encoding="utf-8")

        await asyncio.sleep(0.5)

        for call in handler.call_args_list:
            msg = call[0][0]
            assert msg.text != "not json"

        await ch.stop()

    @pytest.mark.asyncio
    async def test_poll_skips_empty_text(self, tmp_path: Path) -> None:
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="poll_empty", poll_interval=0.1)
        handler = AsyncMock()
        ch.set_handler(handler)
        await ch.start()

        event = json.dumps({"type": "user_message", "text": "", "user_id": "empty"})
        ch._in_file.write_text(event + "\n", encoding="utf-8")

        await asyncio.sleep(0.5)

        handler.assert_not_called()

        await ch.stop()

    @pytest.mark.asyncio
    async def test_poll_incremental_reads(self, tmp_path: Path) -> None:
        """Verify that polling only reads new lines, not already-processed ones."""
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="poll_incr", poll_interval=0.1)
        handler = AsyncMock()
        ch.set_handler(handler)
        await ch.start()

        event1 = json.dumps({"type": "user_message", "text": "First", "user_id": "u1"})
        ch._in_file.write_text(event1 + "\n", encoding="utf-8")

        await asyncio.sleep(0.5)

        event2 = json.dumps({"type": "user_message", "text": "Second", "user_id": "u2"})
        with ch._in_file.open("a", encoding="utf-8") as f:
            f.write(event2 + "\n")

        await asyncio.sleep(0.5)

        assert handler.call_count == 2
        texts = [call[0][0].text for call in handler.call_args_list]
        assert "First" in texts
        assert "Second" in texts

        await ch.stop()


class TestFileChannelEmit:
    @pytest.mark.asyncio
    async def test_emit_through_handler(self, tmp_path: Path) -> None:
        """Test that inject calls emit which routes through handler."""
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="emit_test")
        received_messages: list[Any] = []
        handler = AsyncMock(side_effect=lambda msg: received_messages.append(msg))
        ch.set_handler(handler)

        await ch.start()
        await ch.inject("test message")

        assert len(received_messages) == 1
        assert received_messages[0].channel == "file"
        assert received_messages[0].text == "test message"

        await ch.stop()


class TestFileTraceHook:
    def _make_event(
        self,
        scope: str = "file:test_session",
        tool_name: str = "web__search_web",
        ok: bool = True,
        error: str | None = None,
    ) -> ToolCallEvent:
        return ToolCallEvent(
            scope=scope,
            call_id="call_123",
            tool_name=tool_name,
            args={"query": "weather Bangkok"},
            result="sunny, 32C",
            result_preview="sunny, 32C",
            ok=ok,
            error=error,
            at="2025-04-18T14:30:56Z",
        )

    @pytest.mark.asyncio
    async def test_hook_writes_tool_call_and_result(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out" / "test_trace.jsonl"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        hook = FileTraceHook(out_file=out_file)

        event = self._make_event()
        bot_mock = MagicMock()
        await hook.after_tool_call(event, bot_mock)

        lines = out_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

        call_event = json.loads(lines[0])
        assert call_event["type"] == "tool_call"
        assert call_event["tool"] == "web__search_web"
        assert call_event["args"] == {"query": "weather Bangkok"}
        assert call_event["call_id"] == "call_123"
        assert "timestamp" in call_event

        result_event = json.loads(lines[1])
        assert result_event["type"] == "tool_result"
        assert result_event["tool"] == "web__search_web"
        assert result_event["ok"] is True
        assert result_event["error"] == ""

    @pytest.mark.asyncio
    async def test_hook_writes_error(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out" / "test_error.jsonl"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        hook = FileTraceHook(out_file=out_file)

        event = self._make_event(ok=False, error="connection timeout")
        bot_mock = MagicMock()
        await hook.after_tool_call(event, bot_mock)

        lines = out_file.read_text(encoding="utf-8").strip().splitlines()
        result_event = json.loads(lines[1])
        assert result_event["ok"] is False
        assert result_event["error"] == "connection timeout"

    @pytest.mark.asyncio
    async def test_hook_skips_non_file_scopes(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out" / "test_skip.jsonl"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        hook = FileTraceHook(out_file=out_file)

        event = self._make_event(scope="telegram:123456")
        bot_mock = MagicMock()
        await hook.after_tool_call(event, bot_mock)

        assert not out_file.exists()

    @pytest.mark.asyncio
    async def test_hook_skips_github_scope(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out" / "test_skip_github.jsonl"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        hook = FileTraceHook(out_file=out_file)

        event = self._make_event(scope="github:owner/repo#42")
        bot_mock = MagicMock()
        await hook.after_tool_call(event, bot_mock)

        assert not out_file.exists()

    @pytest.mark.asyncio
    async def test_hook_multiple_events(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out" / "test_multi.jsonl"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        hook = FileTraceHook(out_file=out_file)

        event1 = self._make_event(tool_name="timer__time_now")
        event2 = self._make_event(tool_name="web__search_web")
        bot_mock = MagicMock()

        await hook.after_tool_call(event1, bot_mock)
        await hook.after_tool_call(event2, bot_mock)

        lines = out_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 4

        call_events = [json.loads(line) for line in lines if json.loads(line)["type"] == "tool_call"]
        assert len(call_events) == 2
        assert call_events[0]["tool"] == "timer__time_now"
        assert call_events[1]["tool"] == "web__search_web"


class TestFileChannelIntegration:
    @pytest.mark.asyncio
    async def test_full_session_lifecycle(self, tmp_path: Path) -> None:
        """Test a complete session: start → inject → send → stop with trace hook."""
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(
            sessions_dir=sessions,
            session_id="full_lifecycle",
            capture_tool_calls=True,
            poll_interval=0.1,
        )
        received: list[Any] = []
        handler = AsyncMock(side_effect=lambda msg: received.append(msg))
        ch.set_handler(handler)

        out_file = ch._out_file
        hook = FileTraceHook(out_file=out_file)

        await ch.start()

        await ch.inject("Hello bot", user_id="tester")
        assert len(received) == 1
        assert received[0].text == "Hello bot"

        # Simulate a tool call via hook
        event = ToolCallEvent(
            scope="file:full_lifecycle",
            call_id="call_1",
            tool_name="timer__time_now",
            args={},
            result="12:00 PM UTC",
            result_preview="12:00 PM UTC",
            ok=True,
            error=None,
            at=datetime.now(UTC).isoformat(),
        )
        await hook.after_tool_call(event, MagicMock())

        await ch.send("full_lifecycle", "Hello! The time is 12:00 PM UTC.")

        result = await ch.wait_for_response(timeout=5)
        assert result == "Hello! The time is 12:00 PM UTC."

        await ch.stop()

        out_lines = out_file.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in out_lines]
        types = [e["type"] for e in events]

        assert "session_start" in types
        assert "tool_call" in types
        assert "tool_result" in types
        assert "assistant_message" in types
        assert "turn_complete" in types
        assert "session_end" in types

    @pytest.mark.asyncio
    async def test_session_start_and_end_order(self, tmp_path: Path) -> None:
        """Verify session_start is first, session_end is last in out file."""
        sessions = str(tmp_path / "sessions")
        ch = FileChannel(sessions_dir=sessions, session_id="order_test")
        ch.set_handler(AsyncMock())

        await ch.start()
        await ch.send("order_test", "Hello")
        await ch.stop()

        lines = ch._out_file.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in lines]

        assert events[0]["type"] == "session_start"
        assert events[-1]["type"] == "session_end"
