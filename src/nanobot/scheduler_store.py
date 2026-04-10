from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc(dt: str | None) -> datetime | None:
    if not dt:
        return None
    return datetime.fromisoformat(dt).astimezone(timezone.utc)


@dataclass
class ScheduledTask:
    id: int
    chat_id: str
    prompt: str
    cron_expr: str
    enabled: int
    last_run_at: str | None
    next_run_at: str

    def as_dict(self) -> dict:
        return asdict(self)


class SchedulerStore:
    def __init__(self, db_path: str, timezone_name: str = "UTC") -> None:
        self.db_path = db_path
        self.timezone_name = timezone_name or "UTC"
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    cron_expr TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT,
                    next_run_at TEXT NOT NULL
                )
                """
            )

    def _next_run(self, cron_expr: str, now: datetime | None = None) -> datetime:
        base_utc = now or utc_now()
        base_local = base_utc.astimezone(self._timezone())
        next_local = croniter(cron_expr, base_local).get_next(datetime)
        return next_local.astimezone(timezone.utc)

    def add_task(self, chat_id: str, prompt: str, cron_expr: str) -> dict:
        now = utc_now()
        next_run = self._next_run(cron_expr, now)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scheduled_tasks(chat_id, prompt, cron_expr, enabled, next_run_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (chat_id, prompt, cron_expr, iso(next_run)),
            )
            task_id = cur.lastrowid
        return {
            "id": task_id,
            "chat_id": chat_id,
            "prompt": prompt,
            "cron_expr": cron_expr,
            "next_run_at": iso(next_run),
        }

    def list_tasks(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, chat_id, prompt, cron_expr, enabled, last_run_at, next_run_at
                FROM scheduled_tasks
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            ScheduledTask(
                id=r[0], chat_id=r[1], prompt=r[2], cron_expr=r[3], enabled=r[4], last_run_at=r[5], next_run_at=r[6]
            ).as_dict()
            for r in rows
        ]

    def delete_task(self, task_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            return cur.rowcount > 0

    def set_enabled(self, task_id: int, enabled: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE scheduled_tasks SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, task_id),
            )
            return cur.rowcount > 0

    def due_tasks(self, now: datetime | None = None) -> list[dict]:
        ts = iso(now or utc_now())
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, chat_id, prompt, cron_expr, enabled, last_run_at, next_run_at
                FROM scheduled_tasks
                WHERE enabled = 1 AND next_run_at <= ?
                ORDER BY next_run_at ASC
                """,
                (ts,),
            ).fetchall()
        return [
            ScheduledTask(
                id=r[0], chat_id=r[1], prompt=r[2], cron_expr=r[3], enabled=r[4], last_run_at=r[5], next_run_at=r[6]
            ).as_dict()
            for r in rows
        ]

    def mark_ran(self, task_id: int, cron_expr: str, ran_at: datetime | None = None) -> None:
        when = ran_at or utc_now()
        next_run = self._next_run(cron_expr, when)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET last_run_at = ?, next_run_at = ?
                WHERE id = ?
                """,
                (iso(when), iso(next_run), task_id),
            )
