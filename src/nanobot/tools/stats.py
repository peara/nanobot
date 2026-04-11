from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class ToolStatsStore:
    """SQLite-backed storage for tool call statistics."""

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
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    duration_ms INTEGER,
                    success INTEGER NOT NULL,
                    error_preview TEXT,
                    input_preview TEXT,
                    output_chars INTEGER,
                    run_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_calls_scope
                ON tool_calls(scope)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_name
                ON tool_calls(tool_name)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_calls_started_at
                ON tool_calls(started_at)
                """
            )

    def record_call(
        self,
        scope: str,
        tool_name: str,
        started_at: str,
        duration_ms: int,
        success: bool,
        error_preview: str | None = None,
        input_preview: str | None = None,
        output_chars: int | None = None,
        run_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_calls (
                    scope, tool_name, started_at, duration_ms, success,
                    error_preview, input_preview, output_chars, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    tool_name,
                    started_at,
                    duration_ms,
                    1 if success else 0,
                    error_preview,
                    input_preview,
                    output_chars,
                    run_id,
                ),
            )

    def get_calls(
        self,
        scope: str | None = None,
        tool_name: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM tool_calls WHERE 1=1"
        params: list[Any] = []

        if scope:
            query += " AND scope = ?"
            params.append(scope)
        if tool_name:
            query += " AND tool_name = ?"
            params.append(tool_name)
        if since:
            query += " AND started_at >= ?"
            params.append(since)

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_summary(
        self,
        scope: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                tool_name,
                COUNT(*) as call_count,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail_count,
                AVG(duration_ms) as avg_duration_ms,
                MAX(duration_ms) as max_duration_ms,
                MIN(duration_ms) as min_duration_ms
            FROM tool_calls
            WHERE 1=1
        """
        params: list[Any] = []

        if scope:
            query += " AND scope = ?"
            params.append(scope)
        if since:
            query += " AND started_at >= ?"
            params.append(since)

        query += " GROUP BY tool_name ORDER BY call_count DESC"

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def format_since(days: int) -> str:
        since = datetime.now() - timedelta(days=days)
        return since.isoformat()
