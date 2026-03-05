from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class ContextStore:
    """Scoped JSON context storage with optional expiration."""

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
                CREATE TABLE IF NOT EXISTS contexts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at REAL,
                    UNIQUE(scope_type, scope_id, key)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contexts_scope
                ON contexts(scope_type, scope_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contexts_expires_at
                ON contexts(expires_at)
                """
            )

    def put(
        self,
        scope_type: str,
        scope_id: str,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> None:
        expires_at = None if ttl_seconds is None else (time.time() + max(0, ttl_seconds))
        payload = json.dumps(value, ensure_ascii=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO contexts(scope_type, scope_id, key, value_json, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id, key)
                DO UPDATE SET
                    value_json = excluded.value_json,
                    expires_at = excluded.expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (scope_type, scope_id, key, payload, expires_at),
            )

    def get(self, scope_type: str, scope_id: str, key: str) -> Any | None:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value_json, expires_at
                FROM contexts
                WHERE scope_type = ? AND scope_id = ? AND key = ?
                """,
                (scope_type, scope_id, key),
            ).fetchone()
            if row is None:
                return None
            value_json, expires_at = row
            if expires_at is not None and float(expires_at) <= now:
                conn.execute(
                    """
                    DELETE FROM contexts
                    WHERE scope_type = ? AND scope_id = ? AND key = ?
                    """,
                    (scope_type, scope_id, key),
                )
                return None
        return json.loads(str(value_json))

    def list_scope(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value_json, expires_at
                FROM contexts
                WHERE scope_type = ? AND scope_id = ?
                ORDER BY key ASC
                """,
                (scope_type, scope_id),
            ).fetchall()
            expired_keys = [
                str(key) for key, _, expires_at in rows if expires_at is not None and float(expires_at) <= now
            ]
            if expired_keys:
                placeholders = ",".join("?" for _ in expired_keys)
                conn.execute(
                    f"""
                    DELETE FROM contexts
                    WHERE scope_type = ? AND scope_id = ? AND key IN ({placeholders})
                    """,
                    (scope_type, scope_id, *expired_keys),
                )

        result: dict[str, Any] = {}
        for key, value_json, expires_at in rows:
            if expires_at is not None and float(expires_at) <= now:
                continue
            result[str(key)] = json.loads(str(value_json))
        return result

    def delete_scope(self, scope_type: str, scope_id: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM contexts WHERE scope_type = ? AND scope_id = ?",
                (scope_type, scope_id),
            )
            return int(cur.rowcount)

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM contexts WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            return int(cur.rowcount)
