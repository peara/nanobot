from __future__ import annotations

from datetime import datetime, timezone

from nanobot.scheduler_store import SchedulerStore


def test_scheduler_store_uses_working_timezone_for_next_run(tmp_path) -> None:
    store = SchedulerStore(
        str(tmp_path / "scheduler.db"),
        timezone_name="Asia/Ho_Chi_Minh",
    )

    base_utc = datetime(2026, 4, 10, 4, 16, 36, tzinfo=timezone.utc)
    next_run = store._next_run("18 11 * * *", now=base_utc)

    assert next_run == datetime(2026, 4, 10, 4, 18, 0, tzinfo=timezone.utc)


def test_scheduler_store_mark_ran_recomputes_using_working_timezone(tmp_path) -> None:
    store = SchedulerStore(
        str(tmp_path / "scheduler.db"),
        timezone_name="Asia/Ho_Chi_Minh",
    )

    created = store.add_task(chat_id="telegram:42", prompt="nau com", cron_expr="18 11 * * *")
    ran_at_utc = datetime(2026, 4, 10, 4, 18, 5, tzinfo=timezone.utc)
    store.mark_ran(int(created["id"]), "18 11 * * *", ran_at=ran_at_utc)

    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["next_run_at"] == "2026-04-11T04:18:00+00:00"
