from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from nanobot.web_scripts.models import WebScript, iso, utc_now


class WebScriptStore:
    """SQLite-backed storage for browser data extraction scripts."""

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
                CREATE TABLE IF NOT EXISTS web_scripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    code TEXT NOT NULL,
                    params_schema_json TEXT NOT NULL DEFAULT '{}',
                    result_schema_json TEXT NOT NULL DEFAULT '{}',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    vector_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_web_scripts_name ON web_scripts(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_web_scripts_active ON web_scripts(is_active)")

    def create(
        self,
        *,
        name: str,
        description: str,
        code: str,
        params_schema: dict[str, Any] | None = None,
        result_schema: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        overwrite: bool = False,
        is_active: bool = True,
    ) -> WebScript:
        self._validate_json("params_schema", params_schema or {})
        self._validate_json("result_schema", result_schema or {})
        self._validate_json("tags", tags or [])
        existing = self.get_by_name(name)
        if existing is not None:
            if not overwrite:
                raise ValueError(f"Web script already exists: {name}")
            updated = self.update(
                existing.id,
                description=description,
                code=code,
                params_schema=params_schema or {},
                result_schema=result_schema or {},
                tags=tags or [],
                is_active=is_active,
            )
            if updated is None:
                raise RuntimeError(f"Failed to update web script: {name}")
            return updated

        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO web_scripts (
                    name, description, code, params_schema_json, result_schema_json,
                    tags_json, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    description,
                    code,
                    json.dumps(params_schema or {}, ensure_ascii=True),
                    json.dumps(result_schema or {}, ensure_ascii=True),
                    json.dumps(tags or [], ensure_ascii=True),
                    1 if is_active else 0,
                    iso(now),
                    iso(now),
                ),
            )
            script_id = cur.lastrowid
            if script_id is None:
                raise RuntimeError("Failed to insert web script")
        script = self.get(script_id)
        if script is None:
            raise RuntimeError("Failed to read inserted web script")
        return script

    def get(self, script_id: int) -> WebScript | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, description, code, params_schema_json, result_schema_json,
                       tags_json, is_active, vector_id, created_at, updated_at
                FROM web_scripts
                WHERE id = ?
                """,
                (script_id,),
            ).fetchone()
        return WebScript.from_row(row) if row is not None else None

    def get_by_name(self, name: str) -> WebScript | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, description, code, params_schema_json, result_schema_json,
                       tags_json, is_active, vector_id, created_at, updated_at
                FROM web_scripts
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
        return WebScript.from_row(row) if row is not None else None

    def list_active(self) -> list[WebScript]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, code, params_schema_json, result_schema_json,
                       tags_json, is_active, vector_id, created_at, updated_at
                FROM web_scripts
                WHERE is_active = 1
                ORDER BY name ASC
                """
            ).fetchall()
        return [WebScript.from_row(row) for row in rows]

    def search(self, query: str, limit: int = 5) -> list[WebScript]:
        tokens = [token for token in query.lower().split() if token]
        if not tokens:
            return self.list_active()[:limit]
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, code, params_schema_json, result_schema_json,
                       tags_json, is_active, vector_id, created_at, updated_at
                FROM web_scripts
                WHERE is_active = 1
                ORDER BY name ASC
                """
            ).fetchall()
        scripts = [WebScript.from_row(row) for row in rows]

        scored: list[tuple[int, WebScript]] = []
        for script in scripts:
            haystack = " ".join([script.name, script.description, " ".join(script.tags)]).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, script))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [script for _, script in scored[:limit]]

    def update(
        self,
        script_id: int,
        *,
        description: str | None = None,
        code: str | None = None,
        params_schema: dict[str, Any] | None = None,
        result_schema: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        is_active: bool | None = None,
        vector_id: str | None = None,
    ) -> WebScript | None:
        updates: list[str] = []
        params: list[Any] = []
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if code is not None:
            updates.append("code = ?")
            params.append(code)
        if params_schema is not None:
            self._validate_json("params_schema", params_schema)
            updates.append("params_schema_json = ?")
            params.append(json.dumps(params_schema, ensure_ascii=True))
        if result_schema is not None:
            self._validate_json("result_schema", result_schema)
            updates.append("result_schema_json = ?")
            params.append(json.dumps(result_schema, ensure_ascii=True))
        if tags is not None:
            self._validate_json("tags", tags)
            updates.append("tags_json = ?")
            params.append(json.dumps(tags, ensure_ascii=True))
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
        if vector_id is not None:
            updates.append("vector_id = ?")
            params.append(vector_id)
        if not updates:
            return self.get(script_id)

        now = utc_now()
        updates.append("updated_at = ?")
        params.append(iso(now))
        params.append(script_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE web_scripts SET {', '.join(updates)} WHERE id = ?", params)
        return self.get(script_id)

    @staticmethod
    def _validate_json(name: str, value: Any) -> None:
        try:
            json.dumps(value, ensure_ascii=True)
        except TypeError as exc:
            raise ValueError(f"{name} must be JSON-serializable") from exc
