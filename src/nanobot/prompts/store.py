from __future__ import annotations

import json
import re
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
        self._migrate_legacy_unique_on_name()
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
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'orchestrator',
                    variables_json TEXT DEFAULT '[]',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(name, version)
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

    def _migrate_legacy_unique_on_name(self) -> None:
        """Migrate from old schema (UNIQUE on name) to versioned schema (UNIQUE on (name, version)).

        Older versions of the prompts table had a UNIQUE constraint on ``name``
        alone, enforcing a single-row-per-name design. The new design allows
        multiple rows per name (history) with UNIQUE on ``(name, version)``.

        SQLite cannot drop a UNIQUE constraint via ALTER TABLE, so we detect
        the old shape and run the standard recreate dance:
            1. CREATE TABLE prompts_new with the new schema.
            2. INSERT INTO prompts_new SELECT ... FROM prompts (existing rows
               already have ``is_active=1``, which is preserved).
            3. DROP TABLE prompts.
            4. ALTER TABLE prompts_new RENAME TO prompts.
            5. Recreate indexes.

        Idempotent: detects the new shape (by checking the table's CREATE
        statement for the old ``UNIQUE`` on ``name`` alone) and no-ops if
        already migrated. Safe to run on every init.

        Note: a UNIQUE on a single column in the CREATE TABLE statement may
        be implemented by SQLite as either a column-level constraint or an
        auto-created index. We check the table's own CREATE SQL so both
        shapes are detected.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = 'prompts'
                """
            ).fetchone()
            if row is None or row[0] is None:
                return
            table_sql = row[0]

            # Old shape: column-level UNIQUE on ``name`` alone, expressed in
            # the CREATE TABLE statement as ``name TEXT NOT NULL UNIQUE``
            # (possibly with extra whitespace). The new shape uses a
            # composite ``UNIQUE(name, version)`` at the table level.
            #
            # We need a precise check because the old schema's CREATE TABLE
            # SQL also contains the word ``version`` (in the ``version INTEGER``
            # column definition), so a naive substring check would misfire.
            has_composite_unique = (
                re.search(r"UNIQUE\s*\(\s*name\s*,\s*version\s*\)", table_sql.replace("\n", " ")) is not None
            )
            has_old_column_unique = re.search(r"name\s+TEXT\s+NOT\s+NULL\s+UNIQUE", table_sql) is not None
            if has_composite_unique or not has_old_column_unique:
                return

            # Preserve existing data: copy all rows to a temp table, recreate
            # with the new schema, copy back. Wrapped in a transaction so a
            # mid-migration failure leaves the original table intact.
            conn.execute("PRAGMA foreign_keys=off")
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    CREATE TABLE prompts_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        content TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'orchestrator',
                        variables_json TEXT DEFAULT '[]',
                        is_active INTEGER NOT NULL DEFAULT 1,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(name, version)
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO prompts_new (
                        id, name, content, role, variables_json,
                        is_active, version, created_at, updated_at
                    )
                    SELECT id, name, content, role, variables_json,
                           is_active, version, created_at, updated_at
                    FROM prompts
                    """
                )
                conn.execute("DROP TABLE prompts")
                conn.execute("ALTER TABLE prompts_new RENAME TO prompts")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_role ON prompts(role)")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.execute("PRAGMA foreign_keys=on")

    def save(
        self,
        name: str,
        content: str,
        role: str = "orchestrator",
        variables: list[str] | None = None,
    ) -> Prompt:
        """Save a new version of a prompt. Preserves prior versions for rollback.

        On every save:
        1. All currently-active rows for the name are deactivated.
        2. A new row is inserted with ``is_active=1`` and ``version = max(prev) + 1``
           (or ``1`` for first save).
        3. The active row is now the new row; prior versions remain queryable
           via ``list_versions()`` / ``get_version()`` and re-activatable via
           ``set_active()``.

        This is an append-only-on-version model: prior content is never lost
        unless explicitly deleted by a future ``cleanup_versions()`` call.
        """
        if not content.strip():
            raise ValueError("Prompt content cannot be empty")

        auto_vars = extract_variables(content)
        if variables is None:
            variables = auto_vars

        now = utc_now()
        with self._connect() as conn:
            # Compute next version from any prior row (active or not) so the
            # counter is monotonic across the full history.
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM prompts WHERE name = ?",
                (name,),
            ).fetchone()
            current_max = int(version_row[0]) if version_row else 0
            new_version = current_max + 1

            # Deactivate any currently-active rows in the same transaction.
            # Defensive: under normal operation there is exactly one, but
            # legacy data could have more.
            conn.execute(
                "UPDATE prompts SET is_active = 0, updated_at = ? WHERE name = ? AND is_active = 1",
                (iso(now), name),
            )

            cur = conn.execute(
                """
                INSERT INTO prompts (
                    name, content, role, variables_json,
                    is_active, version, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (name, content, role, json.dumps(variables), new_version, iso(now), iso(now)),
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
                version=new_version,
                created_at=now,
                updated_at=now,
            )

    def get_active(self, name: str) -> Prompt | None:
        """Return the currently-active row for ``name``, or None if none is active."""
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

    def list_versions(self, name: str) -> list[Prompt]:
        """Return all versions of ``name`` ordered by version descending (newest first).

        Includes both active and inactive rows. Use this to discover the
        history of a prompt before rolling back with ``set_active()``.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, content, role, variables_json, is_active, version, created_at, updated_at
                FROM prompts
                WHERE name = ?
                ORDER BY version DESC
                """,
                (name,),
            ).fetchall()
            return [Prompt.from_row(row) for row in rows]

    def get_version(self, name: str, version: int) -> Prompt | None:
        """Return a specific historical version of ``name``, regardless of active state."""
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
        """Return one entry per prompt name — the currently-active version.

        Does NOT include historical (inactive) versions. For full history of a
        single prompt, use ``list_versions(name)``.
        """
        with self._connect() as conn:
            if role is not None:
                rows = conn.execute(
                    """
                    SELECT id, name, content, role, variables_json, is_active, version, created_at, updated_at
                    FROM prompts
                    WHERE role = ? AND is_active = 1
                    ORDER BY name
                    """,
                    (role,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, name, content, role, variables_json, is_active, version, created_at, updated_at
                    FROM prompts
                    WHERE is_active = 1
                    ORDER BY name
                    """,
                ).fetchall()
            return [Prompt.from_row(row) for row in rows]

    def set_active(self, name: str, version: int) -> Prompt | None:
        """Roll back to a prior version of ``name`` by making ``version`` the active row.

        Deactivates whichever row is currently active for the name and activates
        the row at the given version. Returns the now-active Prompt, or None if
        the version doesn't exist.

        This is the canonical rollback path — use ``list_versions()`` to discover
        available versions, then ``set_active(name, version)`` to switch.
        """
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
        """Deactivate the currently-active row for ``name``.

        After this call, ``get_active(name)`` returns None. Use ``set_active()``
        to reactivate a specific version. This is mainly useful for tests; the
        live bot should prefer ``set_active()`` for rollback rather than leaving
        a prompt with no active version.
        """
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
