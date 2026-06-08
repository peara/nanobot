from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DDL_KEYWORDS = frozenset({"CREATE", "ALTER", "DROP", "RENAME"})

_DDL_TABLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*ALTER\s+TABLE\s+[\w\"]+\s+RENAME\s+TO\s+(?P<name>[\w\"]+)", re.IGNORECASE),
    re.compile(
        r"^\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w\"]+)",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<name>[\w\"]+)", re.IGNORECASE),
    re.compile(r"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<name>[\w\"]+)", re.IGNORECASE),
)


def _classify_ddl(sql: str) -> tuple[str, str | None]:
    """Return (sql_type, table_name) if sql is DDL, else ("", None).

    Best-effort table-name extraction; may return None for unusual syntax.
    The migration log is informational — the bot can always query sqlite_master directly.
    """
    stripped = sql.lstrip()
    while stripped.startswith("--"):
        newline = stripped.find("\n")
        if newline == -1:
            return ("", None)
        stripped = stripped[newline + 1 :].lstrip()
    first_token = stripped.split(None, 1)[0].upper() if stripped else ""
    if first_token not in _DDL_KEYWORDS:
        return ("", None)
    for pattern in _DDL_TABLE_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return (first_token, match.group("name").strip('"'))
    return (first_token, None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotebookStore:
    """Bot-owned SQLite notebook. The bot has full control of schema and contents.

    PR 1 concurrency: single connection, check_same_thread=False, no asyncio.Lock.
    PR 2 will add explicit locking once the second tool and injection paths are in.
    """

    BUSY_TIMEOUT_MS = 5000

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            # busy_timeout smooths SQLITE_BUSY under contention; it does not cancel
            # runaway queries (that is a PR 2 concern).
            self._conn.execute(f"PRAGMA busy_timeout={self.BUSY_TIMEOUT_MS}")
        return self._conn

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS db_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sql TEXT NOT NULL,
                    sql_type TEXT NOT NULL,
                    table_name TEXT,
                    executed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_db_migrations_table_name
                ON db_migrations(table_name)
                """
            )

    def execute(self, sql: str, *, row_limit: int = 50) -> dict[str, Any]:
        """Run a single SQL statement.

        Returns:
            {"ok": True, "rows": [...], "row_count": N, "truncated": bool} for SELECT/PRAGMA
            {"ok": True, "rows_affected": N} for DML/DDL (0 for DDL where sqlite returns -1)
            {"ok": False, "error": str} on error
        """
        conn = self._connect()
        try:
            cur = conn.execute(sql)
        except sqlite3.Error as e:
            return {"ok": False, "error": str(e)}

        if cur.description is None:
            conn.commit()
            # sqlite returns -1 for rowcount on DDL; normalize to 0
            rows_affected = cur.rowcount if cur.rowcount >= 0 else 0
            return {"ok": True, "rows_affected": rows_affected}

        try:
            raw_rows = cur.fetchmany(row_limit + 1)
            truncated = len(raw_rows) > row_limit
            if truncated:
                raw_rows = raw_rows[:row_limit]
            conn.commit()
            return {
                "ok": True,
                "rows": [dict(row) for row in raw_rows],
                "row_count": len(raw_rows),
                "truncated": truncated,
            }
        except sqlite3.Error as e:
            return {"ok": False, "error": str(e)}

    def record_migration(self, sql: str, sql_type: str, table_name: str | None) -> None:
        """Append a row to db_migrations. Called by the tool layer after DDL execution."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO db_migrations (sql, sql_type, table_name, executed_at) VALUES (?, ?, ?, ?)",
            (sql, sql_type, table_name, _now_iso()),
        )
        conn.commit()

    @staticmethod
    def classify_ddl(sql: str) -> tuple[str, str | None]:
        """Public re-export of the DDL classifier for use by the tool layer."""
        return _classify_ddl(sql)
