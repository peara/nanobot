from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from nanobot.config import AppConfig, ModelConfig
from nanobot.core import BotCore


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.started = False
        self.send_calls = 0

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def send(self, chat_id: str, text: str) -> None:
        self.send_calls += 1
        if not self.started:
            raise RuntimeError("Telegram channel not started.")
        self.sent.append((chat_id, text))


def _make_config(owner_chat_id: int = 42) -> Any:
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
        owner_chat_id=owner_chat_id,
    )


def _make_bot(owner_chat_id: int = 42, channel: _FakeChannel | None = None) -> BotCore:
    channel = channel if channel is not None else _FakeChannel()
    return BotCore(_make_config(owner_chat_id=owner_chat_id), {"telegram": channel})


def _add_stale_task(bot: BotCore, prompt: str) -> int:
    """Add a task whose next_run_at is stuck in the past (missed while down)."""
    created = bot.scheduler_store.add_task("telegram:42", prompt, "0 * * * *")
    task_id = int(created["id"])
    bot.scheduler_store.mark_ran(task_id, "0 * * * *", ran_at=datetime.now(timezone.utc) - timedelta(days=4))
    assert bot.scheduler_store.due_tasks()  # precondition: past-due
    return task_id


async def test_startup_pre_pass_advances_past_due_task_without_executing() -> None:
    channel = _FakeChannel()
    channel.started = True
    bot = _make_bot(channel=channel)
    task_id = _add_stale_task(bot, "stale prompt")
    stale_last_run = bot.scheduler_store.list_tasks()[0]["last_run_at"]

    await bot.start()
    try:
        await asyncio.sleep(0.05)  # let the scheduler's first poll and the notice task run
        task = bot.scheduler_store.list_tasks()[0]
        assert datetime.fromisoformat(task["next_run_at"]) > datetime.now(timezone.utc)
        assert task["last_run_at"] == stale_last_run  # a skipped run never happened
        assert bot._message_queue.qsize() == 0  # nothing was executed
        assert len(channel.sent) == 1  # exactly one summary notice
        chat_id, text = channel.sent[0]
        assert chat_id == "42"
        assert "1 missed run(s) across 1 task(s)" in text
        assert f"task {task_id}" in text
        assert "overdue since" in text
    finally:
        await bot.stop()


async def test_startup_pre_pass_leaves_fresh_tasks_untouched() -> None:
    channel = _FakeChannel()
    channel.started = True
    bot = _make_bot(channel=channel)
    created = bot.scheduler_store.add_task("telegram:42", "fresh prompt", "0 7 * * *")

    await bot.start()
    try:
        await asyncio.sleep(0.05)
        task = bot.scheduler_store.list_tasks()[0]
        assert task["next_run_at"] == created["next_run_at"]
        assert channel.sent == []  # no downtime, no notice
        assert bot._message_queue.qsize() == 0
    finally:
        await bot.stop()


async def test_startup_notice_sent_once_for_multiple_stale_tasks() -> None:
    channel = _FakeChannel()
    channel.started = True
    bot = _make_bot(channel=channel)
    task_ids = [_add_stale_task(bot, f"stale prompt {i}") for i in range(3)]

    await bot.start()
    try:
        await asyncio.sleep(0.05)
        assert len(channel.sent) == 1  # one message, not one per task
        text = channel.sent[0][1]
        assert "3 missed run(s) across 3 task(s)" in text
        for task_id in task_ids:
            assert f"- task {task_id} " in text
        for task in bot.scheduler_store.list_tasks():
            assert datetime.fromisoformat(task["next_run_at"]) > datetime.now(timezone.utc)
    finally:
        await bot.stop()


async def test_startup_notice_deferred_until_channels_started() -> None:
    channel = _FakeChannel()  # not started: mirrors main.py starting channels after core.start()
    bot = _make_bot(channel=channel)
    _add_stale_task(bot, "stale prompt")

    with patch("nanobot.core.MISSED_RUN_NOTICE_RETRY_DELAY_SECONDS", 0.01):
        await bot.start()  # must return promptly even though the channel is down
        try:
            await asyncio.sleep(0.05)
            assert channel.sent == []  # notice not delivered yet
            assert bot._message_queue.qsize() == 0  # but the run itself was still skipped
            task = bot.scheduler_store.list_tasks()[0]
            assert datetime.fromisoformat(task["next_run_at"]) > datetime.now(timezone.utc)

            channel.started = True  # main.py starts channels
            await asyncio.sleep(0.05)
        finally:
            await bot.stop()
    assert len(channel.sent) == 1  # retried until deliverable, then sent once


async def test_startup_notice_gives_up_after_bounded_attempts() -> None:
    channel = _FakeChannel()  # never starts
    bot = _make_bot(channel=channel)
    _add_stale_task(bot, "stale prompt")

    with (
        patch("nanobot.core.MISSED_RUN_NOTICE_MAX_ATTEMPTS", 3),
        patch("nanobot.core.MISSED_RUN_NOTICE_RETRY_DELAY_SECONDS", 0.01),
    ):
        await bot.start()  # undeliverable notice must not crash or delay boot
        try:
            await asyncio.sleep(0.1)
        finally:
            await bot.stop()
    assert channel.send_calls == 3
    assert channel.sent == []
    task = bot.scheduler_store.list_tasks()[0]
    assert datetime.fromisoformat(task["next_run_at"]) > datetime.now(timezone.utc)  # skip itself unaffected


async def test_startup_notice_skipped_when_no_owner_configured() -> None:
    channel = _FakeChannel()
    channel.started = True
    bot = _make_bot(owner_chat_id=0, channel=channel)
    _add_stale_task(bot, "stale prompt")

    await bot.start()
    try:
        await asyncio.sleep(0.05)
        assert channel.sent == []  # log-only: no owner chat, no crash, no empty send
        task = bot.scheduler_store.list_tasks()[0]
        assert datetime.fromisoformat(task["next_run_at"]) > datetime.now(timezone.utc)
    finally:
        await bot.stop()


async def test_pre_pass_notifies_once_across_reload_style_restarts() -> None:
    channel = _FakeChannel()
    channel.started = True
    bot = _make_bot(channel=channel)
    _add_stale_task(bot, "stale prompt")

    # /reload paths re-run core.start(), which re-runs the pre-pass; the
    # already-advanced task is no longer due, so no second notice.
    bot._skip_missed_scheduled_runs()
    bot._skip_missed_scheduled_runs()
    await asyncio.sleep(0.05)

    assert len(channel.sent) == 1
    task = bot.scheduler_store.list_tasks()[0]
    assert datetime.fromisoformat(task["next_run_at"]) > datetime.now(timezone.utc)