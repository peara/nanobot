from __future__ import annotations

import asyncio
import json
import logging
import random
import string
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanobot.channels.base import Channel, IncomingMessage
from nanobot.core_utils import scoped_chat_id
from nanobot.hooks import ToolCallEvent

logger = logging.getLogger(__name__)


class FileChannel(Channel):
    def __init__(
        self,
        sessions_dir: str,
        session_id: str | None = None,
        capture_tool_calls: bool = False,
        poll_interval: float = 0.5,
        user_id: str = "file_user",
    ) -> None:
        super().__init__()
        if session_id is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = "".join(random.choices(string.ascii_lowercase, k=4))
            session_id = f"session_{timestamp}_{suffix}"
        self.session_id = session_id
        self.sessions_dir = sessions_dir
        self.capture_tool_calls = capture_tool_calls
        self.poll_interval = poll_interval
        self.user_id = user_id

        self._in_dir = Path(sessions_dir) / "in"
        self._out_dir = Path(sessions_dir) / "out"
        self._poll_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._in_offset: int = 0
        self._last_user_msg_timestamp: str = ""

    @property
    def _in_file(self) -> Path:
        return self._in_dir / f"{self.session_id}.jsonl"

    @property
    def _out_file(self) -> Path:
        return self._out_dir / f"{self.session_id}.jsonl"

    async def start(self) -> None:
        self._in_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._in_file.touch()
        self._write_event({"type": "session_start", "session_id": self.session_id})
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_input())
        logger.info("FileChannel started session=%s", self.session_id)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._write_event({"type": "session_end"})
        logger.info("FileChannel stopped session=%s", self.session_id)

    async def send(self, chat_id: str, text: str) -> None:
        self._write_event({"type": "assistant_message", "text": text})
        self._write_event({"type": "turn_complete", "reply_to": self._last_user_msg_timestamp})
        logger.info("FileChannel send session=%s chars=%d", self.session_id, len(text))

    async def _poll_input(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._read_new_lines()
            except Exception:
                logger.exception("Error reading input file in FileChannel")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval,
                )
            except asyncio.TimeoutError:
                continue

    async def _read_new_lines(self) -> None:
        if not self._in_file.exists():
            return

        with self._in_file.open("r", encoding="utf-8") as f:
            f.seek(self._in_offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON line in input file: %s", line[:100])
                    continue

                text = msg.get("text", "")
                if not text:
                    continue

                user_id = msg.get("user_id", self.user_id)
                self._last_user_msg_timestamp = msg.get("timestamp", "")
                await self.emit(
                    IncomingMessage(
                        channel="file",
                        chat_id=scoped_chat_id("file", self.session_id),
                        user_id=user_id,
                        text=text,
                    )
                )

            self._in_offset = f.tell()

    def _write_event(self, event: dict) -> None:
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(UTC).isoformat()
        with self._out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def inject(self, text: str, user_id: str | None = None) -> None:
        event = {
            "type": "user_message",
            "text": text,
            "user_id": user_id or self.user_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        with self._in_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        self._last_user_msg_timestamp = event["timestamp"]
        await self.emit(
            IncomingMessage(
                channel="file",
                chat_id=scoped_chat_id("file", self.session_id),
                user_id=user_id or self.user_id,
                text=text,
            )
        )

    async def wait_for_response(self, timeout: float = 30) -> str:
        start_time = asyncio.get_event_loop().time()
        out_offset = 0
        accumulated_lines: list[dict] = []

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                raise TimeoutError(f"No response within {timeout}s")

            if self._out_file.exists():
                with self._out_file.open("r", encoding="utf-8") as f:
                    f.seek(out_offset)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            accumulated_lines.append(event)
                            if event.get("type") == "turn_complete":
                                for evt in reversed(accumulated_lines):
                                    if evt.get("type") == "assistant_message":
                                        return evt.get("text", "")
                                raise RuntimeError("turn_complete found but no assistant_message")
                        except json.JSONDecodeError:
                            continue
                    out_offset = f.tell()

            await asyncio.sleep(0.2)


class FileTraceHook:
    def __init__(self, out_file: Path) -> None:
        self._out_file = out_file

    async def after_tool_call(self, event: ToolCallEvent, bot: Any) -> None:
        if not event.scope.startswith("file:"):
            return

        tool_call_event = {
            "type": "tool_call",
            "timestamp": event.at,
            "tool": event.tool_name,
            "args": event.args,
            "call_id": event.call_id,
        }
        with self._out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(tool_call_event, ensure_ascii=False) + "\n")

        tool_result_event = {
            "type": "tool_result",
            "timestamp": event.at,
            "tool": event.tool_name,
            "result_preview": event.result_preview,
            "ok": event.ok,
            "error": event.error or "",
        }
        with self._out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(tool_result_event, ensure_ascii=False) + "\n")
