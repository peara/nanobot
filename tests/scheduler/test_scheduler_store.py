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


def test_skip_to_next_advances_past_due_slot_without_touching_last_run(tmp_path) -> None:
    store = SchedulerStore(
        str(tmp_path / "scheduler.db"),
        timezone_name="UTC",
    )
    created = store.add_task(chat_id="telegram:42", prompt="report", cron_expr="0 * * * *")
    task_id = int(created["id"])
    # Simulate downtime: last execution long ago, next_run_at stuck in the past.
    store.mark_ran(task_id, "0 * * * *", ran_at=datetime(2026, 6, 24, 7, 0, 0, tzinfo=timezone.utc))
    assert store.due_tasks()  # precondition: past-due

    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
    new_next = store.skip_to_next(task_id, "0 * * * *", now)

    assert new_next == "2026-10-30T13:00:00+00:00"
    task = store.list_tasks()[0]
    assert task["next_run_at"] == "2026-10-30T13:00:00+00:00"
    assert task["last_run_at"] == "2026-06-24T07:00:00+00:00"  # a skipped run never happened
    assert store.due_tasks(now=now) == []


def test_skip_to_next_returns_none_for_missing_task(tmp_path) -> None:
    store = SchedulerStore(
        str(tmp_path / "scheduler.db"),
        timezone_name="UTC",
    )
    assert store.skip_to_next(999, "0 * * * *") is None


def test_resume_enables_task_with_refreshed_next_run(tmp_path) -> None:
    store = SchedulerStore(
        str(tmp_path / "scheduler.db"),
        timezone_name="UTC",
    )
    created = store.add_task(chat_id="telegram:42", prompt="report", cron_expr="0 * * * *")
    task_id = int(created["id"])
    store.set_enabled(task_id, False)
    # Pause spanned several occurrences: next_run_at is stale (in the past).
    store.mark_ran(task_id, "0 * * * *", ran_at=datetime(2026, 6, 24, 7, 0, 0, tzinfo=timezone.utc))

    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
    next_run = store.resume(task_id, now)

    assert next_run == "2026-10-30T13:00:00+00:00"
    task = store.list_tasks()[0]
    assert task["enabled"] == 1
    assert task["next_run_at"] == "2026-10-30T13:00:00+00:00"
    assert store.due_tasks(now=now) == []  # resumed task does not fire on the next poll


def test_resume_keeps_slot_when_next_run_still_in_future(tmp_path) -> None:
    store = SchedulerStore(
        str(tmp_path / "scheduler.db"),
        timezone_name="UTC",
    )
    created = store.add_task(chat_id="telegram:42", prompt="report", cron_expr="0 * * * *")
    task_id = int(created["id"])
    store.set_enabled(task_id, False)

    now = datetime.now(timezone.utc)
    next_run = store.resume(task_id, now)

    assert next_run == created["next_run_at"]  # short pause: same upcoming slot, no drift


def test_resume_missing_task_returns_none(tmp_path) -> None:
    store = SchedulerStore(
        str(tmp_path / "scheduler.db"),
        timezone_name="UTC",
    )
    assert store.resume(999) is None
