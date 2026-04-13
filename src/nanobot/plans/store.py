from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from nanobot.plans.models import Plan, PlanBrief, iso, utc_now


class PlanStore:
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
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    constraints_json TEXT,
                    required_inputs_json TEXT,
                    risk_flags_json TEXT,
                    steps_json TEXT,
                    notes TEXT,
                    source_type TEXT,
                    source_scope TEXT,
                    version INTEGER DEFAULT 1,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_plans_source
                ON plans(source_type, source_scope)
                """
            )

    def create(
        self,
        name: str,
        goal: str,
        constraints: list[str] | None = None,
        required_inputs: list[str] | None = None,
        risk_flags: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
        notes: str = "",
        source_type: str = "",
        source_scope: str = "",
    ) -> Plan:
        """Create a new plan and return it with assigned ID."""
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO plans (
                    name, goal, constraints_json, required_inputs_json, risk_flags_json,
                    steps_json, notes, source_type, source_scope, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    goal,
                    json.dumps(constraints or []),
                    json.dumps(required_inputs or []),
                    json.dumps(risk_flags or []),
                    json.dumps(steps) if steps else None,
                    notes,
                    source_type,
                    source_scope,
                    iso(now),
                    iso(now),
                ),
            )
            plan_id = cur.lastrowid
            if plan_id is None:
                raise RuntimeError("Failed to insert plan")

        return Plan(
            id=plan_id,
            name=name,
            goal=goal,
            constraints=constraints or [],
            required_inputs=required_inputs or [],
            risk_flags=risk_flags or [],
            steps=steps,
            notes=notes,
            source_type=source_type,
            source_scope=source_scope,
            created_at=now,
            updated_at=now,
        )

    def create_from_brief(
        self,
        brief: PlanBrief,
        name: str,
        source_type: str = "",
        source_scope: str = "",
        notes: str = "",
    ) -> Plan:
        """Create a plan from a PlanBrief."""
        return self.create(
            name=name,
            goal=brief.goal,
            constraints=brief.constraints,
            required_inputs=brief.required_inputs,
            risk_flags=brief.risk_flags,
            notes=notes or brief.notes,
            source_type=source_type,
            source_scope=source_scope,
        )

    def get(self, plan_id: int) -> Plan | None:
        """Get a plan by ID."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, goal, constraints_json, required_inputs_json, risk_flags_json,
                       steps_json, notes, source_type, source_scope, version, success_count,
                       failure_count, last_run_at, created_at, updated_at
                FROM plans
                WHERE id = ?
                """,
                (plan_id,),
            ).fetchone()
            if row is None:
                return None
            return Plan.from_row(row)

    def list_plans(
        self,
        source_type: str | None = None,
        source_scope: str | None = None,
        limit: int = 100,
    ) -> list[Plan]:
        """List plans, optionally filtered by source."""
        with self._connect() as conn:
            query = """
                SELECT id, name, goal, constraints_json, required_inputs_json, risk_flags_json,
                       steps_json, notes, source_type, source_scope, version, success_count,
                       failure_count, last_run_at, created_at, updated_at
                FROM plans
            """
            params: list[Any] = []
            conditions: list[str] = []

            if source_type is not None:
                conditions.append("source_type = ?")
                params.append(source_type)
            if source_scope is not None:
                conditions.append("source_scope = ?")
                params.append(source_scope)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [Plan.from_row(row) for row in rows]

    def update(
        self,
        plan_id: int,
        name: str | None = None,
        goal: str | None = None,
        constraints: list[str] | None = None,
        required_inputs: list[str] | None = None,
        risk_flags: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
        notes: str | None = None,
        increment_version: bool = False,
    ) -> Plan | None:
        """Update a plan. Returns updated plan or None if not found."""
        now = utc_now()
        updates: list[str] = []
        params: list[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if goal is not None:
            updates.append("goal = ?")
            params.append(goal)
        if constraints is not None:
            updates.append("constraints_json = ?")
            params.append(json.dumps(constraints))
        if required_inputs is not None:
            updates.append("required_inputs_json = ?")
            params.append(json.dumps(required_inputs))
        if risk_flags is not None:
            updates.append("risk_flags_json = ?")
            params.append(json.dumps(risk_flags))
        if steps is not None:
            updates.append("steps_json = ?")
            params.append(json.dumps(steps) if steps else None)
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        if increment_version:
            updates.append("version = version + 1")

        if not updates:
            return self.get(plan_id)

        updates.append("updated_at = ?")
        params.append(iso(now))
        params.append(plan_id)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE plans SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        return self.get(plan_id)

    def increment_stats(self, plan_id: int, success: bool) -> Plan | None:
        """Increment success or failure count and update last_run_at."""
        now = utc_now()
        with self._connect() as conn:
            if success:
                conn.execute(
                    """
                    UPDATE plans
                    SET success_count = success_count + 1, last_run_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (iso(now), iso(now), plan_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE plans
                    SET failure_count = failure_count + 1, last_run_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (iso(now), iso(now), plan_id),
                )
        return self.get(plan_id)

    def delete(self, plan_id: int) -> bool:
        """Delete a plan. Returns True if deleted."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            return cur.rowcount > 0
