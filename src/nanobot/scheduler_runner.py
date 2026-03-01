from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from nanobot.scheduler_store import SchedulerStore


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
            due = self.store.due_tasks()
            for task in due:
                await self.on_due_task(task["chat_id"], task["prompt"])
                self.store.mark_ran(task["id"], task["cron_expr"])
            await asyncio.sleep(self.poll_interval_seconds)
