from __future__ import annotations

import json
import logging
import re
from typing import Any

from nanobot.notebook.store import NotebookStore
from nanobot.tools.base import Tool

logger = logging.getLogger(__name__)


# Statements the bot is not allowed to execute. These would let the bot reach
# beyond its own DB file, which would violate the operator/bot boundary.
_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bATTACH\b", re.IGNORECASE),
    re.compile(r"\bDETACH\b", re.IGNORECASE),
    re.compile(r"\bVACUUM\s+INTO\b", re.IGNORECASE),
)


def _split_statements(sql: str) -> list[str]:
    """Split on `;`, strip comments and whitespace, drop empty statements.

    The single-statement guard relies on this; PR 2 may add proper tokenizer
    parsing (sqlparse) if real-world bot-generated SQL trips it.
    """
    statements: list[str] = []
    for raw in sql.split(";"):
        cleaned: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            cleaned.append(line)
        statement = "\n".join(cleaned).strip()
        if statement:
            statements.append(statement)
    return statements


class NotebookQueryTool(Tool):
    """Run a single SQL statement against the bot's notebook.

    The bot has full control of schema and contents. Single-statement only.
    DDL is auto-captured into the db_migrations system table.
    """

    ROW_LIMIT = 50

    def __init__(self, notebook_store: NotebookStore) -> None:
        self._store = notebook_store

    @property
    def name(self) -> str:
        return "notebook__query"

    @property
    def description(self) -> str:
        return (
            "Run a single SQL statement against the bot's private notebook database. "
            "Use for DDL (CREATE/ALTER/DROP), DML (INSERT/UPDATE/DELETE), and SELECT. "
            "DDL is auto-logged to db_migrations; SELECT results are capped at 50 rows. "
            "Multi-statement scripts are rejected — issue one call per statement."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "Single SQL statement to execute. Must not contain `;` except as the trailing terminator."
                    ),
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        }

    async def call(self, args: dict[str, Any]) -> str:
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return json.dumps({"ok": False, "error": "sql must be a non-empty string"}, ensure_ascii=True)

        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.search(sql):
                return json.dumps(
                    {"ok": False, "error": f"statement not allowed: {pattern.pattern}"},
                    ensure_ascii=True,
                )

        statements = _split_statements(sql)
        if len(statements) != 1:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"expected exactly one statement, got {len(statements)}. "
                        "Issue one notebook__query call per statement."
                    ),
                },
                ensure_ascii=True,
            )

        statement = statements[0]
        ddl_type, table_name = NotebookStore.classify_ddl(statement)

        try:
            result = self._store.execute(statement, row_limit=self.ROW_LIMIT)
        except Exception as e:
            logger.warning("notebook__query failed: %s", e)
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=True)

        if ddl_type and result.get("ok"):
            try:
                self._store.record_migration(statement, ddl_type, table_name)
            except Exception as e:
                # Migration log is best-effort; the DDL already succeeded.
                logger.warning("Failed to record migration: %s", e)

        return json.dumps(result, ensure_ascii=True)


def register_notebook_tools(registry: Any, notebook_store: NotebookStore) -> None:
    registry.register(NotebookQueryTool(notebook_store))
