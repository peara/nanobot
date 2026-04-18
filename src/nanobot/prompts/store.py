from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from nanobot.prompts.defaults import DEFAULT_PROMPTS
from nanobot.prompts.models import (
    Prompt,
    PromptVariableError,
    extract_variables,
    iso,
    utc_now,
)


class PromptStore:
    def __init__(self, db_path: str, seed_defaults: bool = True) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if seed_defaults:
            self._seed_defaults(DEFAULT_PROMPTS)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'orchestrator',
                    variables_json TEXT DEFAULT '[]',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_prompts_role ON prompts(role)
                """
            )

    def save(
        self,
        name: str,
        content: str,
        role: str = "orchestrator",
        variables: list[str] | None = None,
    ) -> Prompt:
        if not content.strip():
            raise ValueError("Prompt content cannot be empty")

        auto_vars = extract_variables(content)
        if variables is None:
            variables = auto_vars

        now = utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, version, is_active FROM prompts WHERE name = ?",
                (name,),
            ).fetchone()

            if existing:
                prompt_id, current_version, is_active = existing
                new_version = current_version + 1
                conn.execute(
                    """
                    UPDATE prompts
                    SET content = ?, role = ?, variables_json = ?, version = ?,
                        is_active = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (content, role, json.dumps(variables), new_version, iso(now), prompt_id),
                )
                return Prompt(
                    id=prompt_id,
                    name=name,
                    content=content,
                    role=role,
                    variables=variables,
                    is_active=True,
                    version=new_version,
                    created_at=utc_now(),
                    updated_at=now,
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO prompts (name, content, role, variables_json, version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (name, content, role, json.dumps(variables), iso(now), iso(now)),
                )
                prompt_id = cur.lastrowid
                if prompt_id is None:
                    raise RuntimeError("Failed to insert prompt")
                return Prompt(
                    id=prompt_id,
                    name=name,
                    content=content,
                    role=role,
                    variables=variables,
                    is_active=True,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )

    def get_active(self, name: str) -> Prompt | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, content, role, variables_json, is_active, version, created_at, updated_at
                FROM prompts
                WHERE name = ? AND is_active = 1
                """,
                (name,),
            ).fetchone()
            if row is None:
                return None
            return Prompt.from_row(row)

    def render(self, prompt_name: str, **variables: str) -> str:
        prompt = self.get_active(prompt_name)
        if prompt is None:
            raise KeyError(f"Prompt not found: {prompt_name}")

        missing = [v for v in prompt.variables if v not in variables]
        if missing:
            missing_str = ", ".join(sorted(missing))
            raise PromptVariableError(f"Missing required variables: {missing_str}")

        return prompt.content.format(**variables)

    def list_all(self, role: str | None = None) -> list[Prompt]:
        with self._connect() as conn:
            if role is not None:
                rows = conn.execute(
                    """
                    SELECT id, name, content, role, variables_json, is_active, version, created_at, updated_at
                    FROM prompts
                    WHERE role = ?
                    ORDER BY name
                    """,
                    (role,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, name, content, role, variables_json, is_active, version, created_at, updated_at
                    FROM prompts
                    ORDER BY name
                    """
                ).fetchall()
            return [Prompt.from_row(row) for row in rows]

    def set_active(self, name: str, version: int) -> Prompt | None:
        now = utc_now()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, content, role, variables_json, is_active, version, created_at, updated_at
                FROM prompts
                WHERE name = ? AND version = ?
                """,
                (name, version),
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                """
                UPDATE prompts SET is_active = 0, updated_at = ? WHERE name = ?
                """,
                (iso(now), name),
            )
            conn.execute(
                """
                UPDATE prompts SET is_active = 1, updated_at = ? WHERE name = ? AND version = ?
                """,
                (iso(now), name, version),
            )
            return Prompt.from_row(row)

    def deactivate(self, name: str) -> bool:
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE prompts SET is_active = 0, updated_at = ? WHERE name = ? AND is_active = 1
                """,
                (iso(now), name),
            )
            return cur.rowcount > 0

    def _seed_defaults(self, defaults: dict[str, tuple[str, str, list[str]]]) -> int:
        count = 0
        for prompt_name, (content, role, variables) in defaults.items():
            if self.get_active(prompt_name) is None:
                self.save(prompt_name, content, role, variables)
                count += 1
        return count
