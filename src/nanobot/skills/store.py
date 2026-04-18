from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from nanobot.skills.models import Skill, iso, utc_now


class SkillStore:
    """SQLite-backed storage for skills.

    Skills are reusable expertise/knowledge that can be injected into agent context
    based on trigger conditions (always, pattern, or intelligent matching).
    """

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
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    trigger_mode TEXT NOT NULL DEFAULT 'pattern',
                    trigger_patterns_json TEXT,
                    tools_allowlist_json TEXT,
                    priority INTEGER DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skills_trigger_mode ON skills(trigger_mode)
                """
            )

    def create(
        self,
        name: str,
        description: str,
        instructions: str,
        trigger_mode: str = "pattern",
        trigger_patterns: list[str] | None = None,
        tools_allowlist: list[str] | None = None,
        priority: int = 0,
        is_active: bool = True,
    ) -> Skill:
        if trigger_mode not in {"always", "pattern", "intelligent"}:
            raise ValueError(f"Invalid trigger_mode '{trigger_mode}'. Must be: always, pattern, or intelligent")

        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO skills (
                    name, description, instructions, trigger_mode, trigger_patterns_json,
                    tools_allowlist_json, priority, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    description,
                    instructions,
                    trigger_mode,
                    json.dumps(trigger_patterns) if trigger_patterns else None,
                    json.dumps(tools_allowlist) if tools_allowlist else None,
                    priority,
                    1 if is_active else 0,
                    iso(now),
                    iso(now),
                ),
            )
            skill_id = cur.lastrowid
            if skill_id is None:
                raise RuntimeError("Failed to insert skill")

        return Skill(
            id=skill_id,
            name=name,
            description=description,
            instructions=instructions,
            trigger_mode=trigger_mode,
            trigger_patterns=trigger_patterns or [],
            tools_allowlist=tools_allowlist,
            priority=priority,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )

    def get(self, skill_id: int) -> Skill | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, description, instructions, trigger_mode, trigger_patterns_json,
                       tools_allowlist_json, priority, is_active, created_at, updated_at
                FROM skills
                WHERE id = ?
                """,
                (skill_id,),
            ).fetchone()
            if row is None:
                return None
            return Skill.from_row(row)

    def get_by_name(self, name: str) -> Skill | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, description, instructions, trigger_mode, trigger_patterns_json,
                       tools_allowlist_json, priority, is_active, created_at, updated_at
                FROM skills
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
            if row is None:
                return None
            return Skill.from_row(row)

    def list_active(self) -> list[Skill]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, instructions, trigger_mode, trigger_patterns_json,
                       tools_allowlist_json, priority, is_active, created_at, updated_at
                FROM skills
                WHERE is_active = 1
                ORDER BY priority DESC, name ASC
                """
            ).fetchall()
            return [Skill.from_row(row) for row in rows]

    def list_all(self) -> list[Skill]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, instructions, trigger_mode, trigger_patterns_json,
                       tools_allowlist_json, priority, is_active, created_at, updated_at
                FROM skills
                ORDER BY priority DESC, name ASC
                """
            ).fetchall()
            return [Skill.from_row(row) for row in rows]

    def update(
        self,
        skill_id: int,
        name: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        trigger_mode: str | None = None,
        trigger_patterns: list[str] | None = None,
        tools_allowlist: list[str] | None = None,
        priority: int | None = None,
        is_active: bool | None = None,
    ) -> Skill | None:
        now = utc_now()
        updates: list[str] = []
        params: list[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if instructions is not None:
            updates.append("instructions = ?")
            params.append(instructions)
        if trigger_mode is not None:
            if trigger_mode not in {"always", "pattern", "intelligent"}:
                raise ValueError(f"Invalid trigger_mode '{trigger_mode}'")
            updates.append("trigger_mode = ?")
            params.append(trigger_mode)
        if trigger_patterns is not None:
            updates.append("trigger_patterns_json = ?")
            params.append(json.dumps(trigger_patterns) if trigger_patterns else None)
        if tools_allowlist is not None:
            updates.append("tools_allowlist_json = ?")
            params.append(json.dumps(tools_allowlist) if tools_allowlist else None)
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)

        if not updates:
            return self.get(skill_id)

        updates.append("updated_at = ?")
        params.append(iso(now))
        params.append(skill_id)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE skills SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        return self.get(skill_id)

    def set_active(self, skill_id: int, is_active: bool) -> Skill | None:
        return self.update(skill_id, is_active=is_active)

    def delete(self, skill_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
            return cur.rowcount > 0

    def delete_by_name(self, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM skills WHERE name = ?", (name,))
            return cur.rowcount > 0
