from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nanobot.mcp_servers.scheduler import server
from nanobot.scheduler_store import SchedulerStore


def _make_store(db_path: str) -> SchedulerStore:
    return SchedulerStore(db_path, timezone_name="UTC")


def _make_stale_paused_task(store: SchedulerStore) -> int:
    created = store.add_task(chat_id="telegram:42", prompt="daily report", cron_expr="0 7 * * *")
    task_id = int(created["id"])
    store.set_enabled(task_id, enabled=False)
    # Pause spanned several occurrences: next_run_at is stale (in the past).
    store.mark_ran(task_id, "0 7 * * *", ran_at=datetime.now(timezone.utc) - timedelta(days=3))
    return task_id


def test_resume_task_refreshes_stale_next_run_and_returns_it(tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "scheduler.db")
    monkeypatch.setenv("SCHEDULER_DB_PATH", db_path)
    monkeypatch.setenv("SCHEDULER_TIMEZONE", "UTC")
    store = _make_store(db_path)
    task_id = _make_stale_paused_task(store)
    task = store.list_tasks()[0]
    # stale slot: re-enabling without a refresh would fire it on the next poll
    assert datetime.fromisoformat(task["next_run_at"]) < datetime.now(timezone.utc)
    assert task["enabled"] == 0

    result = server.resume_task(task_id)

    assert result["resumed"] is True
    assert result["task_id"] == task_id
    refreshed = datetime.fromisoformat(result["next_run_at"])
    assert refreshed > datetime.now(timezone.utc)  # next future slot, not the stale one
    task = store.list_tasks()[0]
    assert task["enabled"] == 1
    assert task["next_run_at"] == result["next_run_at"]
    assert store.due_tasks() == []


def test_resume_task_missing_task_reports_not_resumed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCHEDULER_DB_PATH", str(tmp_path / "scheduler.db"))
    monkeypatch.setenv("SCHEDULER_TIMEZONE", "UTC")

    result = server.resume_task(999)

    assert result["resumed"] is False
    assert result["task_id"] == 999
    assert result["next_run_at"] is None