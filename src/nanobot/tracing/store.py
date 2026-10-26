from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class SpanRecord:
    """One persisted span row. attrs/events are the JSON-encoded originals."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    run_id: str | None
    scope: str | None
    kind: str
    name: str
    start_ts: float
    end_ts: float
    duration_ms: float
    status: str
    status_message: str | None
    attributes_json: str
    events_json: str

    def attributes(self) -> dict[str, Any]:
        return json.loads(self.attributes_json)

    def events(self) -> list[dict[str, Any]]:
        return json.loads(self.events_json)

    def started_at(self) -> str:
        return datetime.fromtimestamp(self.start_ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class TraceStore:
    """SQLite persistence for OTel spans — the `just trace` substrate.

    Writes come from the span exporter; reads come from the debug CLI.
    A separate traces.db (not the main nanobot.db) follows the project's
    store-per-concern layout and keeps trace volume from bloating the
    conversation store.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # ":memory:" needs one persistent connection — a fresh
        # sqlite3.connect(":memory:") per call would give every call its own
        # empty database, losing the schema _init_db just created.
        self._memory_conn: sqlite3.Connection | None = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        if self._memory_conn is None:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS spans (
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL PRIMARY KEY,
                    parent_span_id TEXT,
                    run_id TEXT,
                    scope TEXT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    start_ts REAL NOT NULL,
                    end_ts REAL NOT NULL,
                    duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    status_message TEXT,
                    attributes_json TEXT NOT NULL,
                    events_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_run_id ON spans(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_start_ts ON spans(start_ts)")

    def insert_spans(self, records: Sequence[SpanRecord]) -> None:
        if not records:
            return
        rows = [
            (
                r.trace_id,
                r.span_id,
                r.parent_span_id,
                r.run_id,
                r.scope,
                r.kind,
                r.name,
                r.start_ts,
                r.end_ts,
                r.duration_ms,
                r.status,
                r.status_message,
                r.attributes_json,
                r.events_json,
            )
            for r in records
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO spans (
                    trace_id, span_id, parent_span_id, run_id, scope, kind, name,
                    start_ts, end_ts, duration_ms, status, status_message,
                    attributes_json, events_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SpanRecord:
        return SpanRecord(
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            run_id=row["run_id"],
            scope=row["scope"],
            kind=row["kind"],
            name=row["name"],
            start_ts=float(row["start_ts"]),
            end_ts=float(row["end_ts"]),
            duration_ms=float(row["duration_ms"]),
            status=row["status"],
            status_message=row["status_message"],
            attributes_json=row["attributes_json"],
            events_json=row["events_json"],
        )

    def list_by_run(self, run_id: str) -> list[SpanRecord]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM spans WHERE run_id = ? ORDER BY start_ts ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_by_trace(self, trace_id: str) -> list[SpanRecord]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_ts ASC",
                (trace_id,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, span_id: str) -> SpanRecord | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Most-recently-started distinct run_ids with span counts and time bounds."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_id,
                       MIN(start_ts) AS started_ts,
                       MAX(end_ts) AS ended_ts,
                       COUNT(*) AS span_count,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_count
                FROM spans
                WHERE run_id IS NOT NULL
                GROUP BY run_id
                ORDER BY started_ts DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]
