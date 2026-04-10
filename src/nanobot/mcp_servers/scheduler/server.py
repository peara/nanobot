from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from nanobot.scheduler_store import SchedulerStore


def _scheduler_store() -> SchedulerStore:
    db_path = os.environ.get("SCHEDULER_DB_PATH", "./data/scheduler.db")
    timezone_name = os.environ.get("SCHEDULER_TIMEZONE", "UTC")
    return SchedulerStore(db_path, timezone_name=timezone_name)


def _run_crontab(args: list[str], stdin_data: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["crontab", *args],
        input=stdin_data,
        capture_output=True,
        text=True,
        check=False,
    )


def _get_current_crontab_lines() -> list[str]:
    result = _run_crontab(["-l"])
    if result.returncode != 0:
        stderr = (result.stderr or "").strip().lower()
        # "no crontab for user" is not an error for our use case.
        if "no crontab" in stderr:
            return []
        raise RuntimeError(result.stderr.strip() or "Failed to read crontab.")
    return [line for line in result.stdout.splitlines() if line.strip()]


mcp = FastMCP("nanobot-scheduler")


@mcp.tool()
def schedule_task(chat_id: str, prompt: str, cron_expr: str) -> dict[str, Any]:
    """Create a bot-managed recurring task using cron syntax."""
    store = _scheduler_store()
    return store.add_task(chat_id=chat_id, prompt=prompt, cron_expr=cron_expr)


@mcp.tool()
def list_tasks() -> list[dict[str, Any]]:
    """List bot-managed scheduled tasks from SQLite."""
    store = _scheduler_store()
    return store.list_tasks()


@mcp.tool()
def delete_task(task_id: int) -> dict[str, Any]:
    """Delete a bot-managed scheduled task."""
    store = _scheduler_store()
    ok = store.delete_task(task_id)
    return {"deleted": ok, "task_id": task_id}


@mcp.tool()
def pause_task(task_id: int) -> dict[str, Any]:
    """Disable a bot-managed scheduled task."""
    store = _scheduler_store()
    ok = store.set_enabled(task_id, enabled=False)
    return {"paused": ok, "task_id": task_id}


@mcp.tool()
def resume_task(task_id: int) -> dict[str, Any]:
    """Enable a bot-managed scheduled task."""
    store = _scheduler_store()
    ok = store.set_enabled(task_id, enabled=True)
    return {"resumed": ok, "task_id": task_id}


@mcp.tool()
def cron_list() -> list[str]:
    """List current Linux crontab lines for the current user."""
    return _get_current_crontab_lines()


@mcp.tool()
def cron_add(schedule: str, command: str, tag: str | None = None) -> dict[str, Any]:
    """Add a Linux crontab entry. Optionally attach a removable tag."""
    lines = _get_current_crontab_lines()
    suffix = f" # nanobot:{tag}" if tag else ""
    entry = f"{schedule} {command}{suffix}"
    lines.append(entry)
    content = "\n".join(lines) + "\n"
    result = _run_crontab(["-"], stdin_data=content)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to update crontab.")
    return {"ok": True, "entry": entry}


@mcp.tool()
def cron_remove(match: str) -> dict[str, Any]:
    """Remove Linux crontab lines containing a substring."""
    lines = _get_current_crontab_lines()
    kept = [line for line in lines if match not in line]
    removed_count = len(lines) - len(kept)
    content = ("\n".join(kept) + "\n") if kept else ""
    result = _run_crontab(["-"], stdin_data=content)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to update crontab.")
    return {"ok": True, "removed": removed_count}


@mcp.tool()
def cron_export_json() -> str:
    """Export current crontab as JSON string for easier LLM parsing."""
    return json.dumps(_get_current_crontab_lines(), ensure_ascii=True)


@mcp.tool()
def scheduler_health() -> dict[str, Any]:
    """Return scheduler health information and pending task overview."""
    store = _scheduler_store()
    tasks = store.list_tasks()
    due = store.due_tasks()
    return {
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "task_count": len(tasks),
        "due_count": len(due),
        "next_five_tasks": tasks[:5],
    }


if __name__ == "__main__":
    mcp.run()
