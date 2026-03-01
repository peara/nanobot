from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging

from nanobot.scheduler_store import SchedulerStore

logger = logging.getLogger(__name__)


TaskHandler = Callable[[str, str], Awaitable[None]]


class SchedulerRunner:
    def __init__(self, store: SchedulerStore, on_due_task: TaskHandler, poll_interval_seconds: int = 20) -> None:
        self.store = store
        self.on_due_task = on_due_task
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                due = self.store.due_tasks()
            except Exception:  # pylint: disable=broad-except
                logger.exception("Failed to fetch due tasks")
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            for task in due:
                try:
                    logger.info("Executing scheduled task id=%s chat_id=%s", task["id"], task["chat_id"])
                    await self.on_due_task(task["chat_id"], task["prompt"])
                    self.store.mark_ran(task["id"], task["cron_expr"])
                except Exception:  # pylint: disable=broad-except
                    logger.exception("Scheduled task failed id=%s", task.get("id"))
            await asyncio.sleep(self.poll_interval_seconds)
