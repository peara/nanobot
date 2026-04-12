from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class SubagentRun:
    """Represents a single agent execution run."""

    id: str
    parent_run_id: str | None
    scope: str
    status: str  # "pending" | "running" | "completed" | "failed"
    created_at: datetime
    completed_at: datetime | None = None
    goal: str | None = None
    error: str | None = None


class SubagentRunStore:
    """SQLite-backed storage for subagent run metadata."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subagent_runs (
                    id TEXT PRIMARY KEY,
                    parent_run_id TEXT,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    goal TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_subagent_runs_scope
                ON subagent_runs(scope)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_subagent_runs_status
                ON subagent_runs(status)
                """
            )

    def _row_to_run(self, row: sqlite3.Row) -> SubagentRun:
        return SubagentRun(
            id=row["id"],
            parent_run_id=row["parent_run_id"],
            scope=row["scope"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            goal=row["goal"],
            error=row["error"],
        )

    def create(
        self,
        run_id: str,
        scope: str,
        parent_run_id: str | None = None,
        goal: str | None = None,
    ) -> SubagentRun:
        """Create a new run record with status 'pending'."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subagent_runs (
                    id, parent_run_id, scope, status, created_at, goal
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, parent_run_id, scope, "pending", now, goal),
            )
        return SubagentRun(
            id=run_id,
            parent_run_id=parent_run_id,
            scope=scope,
            status="pending",
            created_at=datetime.fromisoformat(now),
            goal=goal,
        )

    def get(self, run_id: str) -> SubagentRun | None:
        """Get a run by ID."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM subagent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            return self._row_to_run(row) if row else None

    def set_status(self, run_id: str, status: str, error: str | None = None) -> None:
        """Update run status. Sets completed_at if status is terminal."""
        status_value = status
        completed_at_value = None
        if status in ("completed", "failed"):
            completed_at_value = datetime.now().isoformat()

        with self._connect() as conn:
            if error is not None:
                conn.execute(
                    """
                    UPDATE subagent_runs
                    SET status = ?, completed_at = ?, error = ?
                    WHERE id = ?
                    """,
                    (status_value, completed_at_value, error, run_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE subagent_runs
                    SET status = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (status_value, completed_at_value, run_id),
                )

    def list_by_scope(
        self,
        scope: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SubagentRun]:
        """List runs for a scope, optionally filtered by status."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if status is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM subagent_runs
                    WHERE scope = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (scope, status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM subagent_runs
                    WHERE scope = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (scope, limit),
                ).fetchall()
            return [self._row_to_run(row) for row in rows]

    def store_result(self, run_id: str, result: dict[str, Any]) -> None:
        """Store the run result in context store (delegated to caller)."""
        # This is a no-op here - the ContextStore is used by SubagentManager
        # to store result/summary/tool_trace for backward compatibility.
        # Keeping this method for future direct storage if needed.
        pass
