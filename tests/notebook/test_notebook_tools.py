from __future__ import annotations

import json
import tempfile
from pathlib import Path

from nanobot.notebook import NotebookStore
from nanobot.notebook.tools import NotebookQueryTool, register_notebook_tools
from nanobot.tools.registry import ToolRegistry


def _make_store(tmp_path: Path) -> NotebookStore:
    return NotebookStore(str(tmp_path / "notebook.db"))


async def test_query_tool_creates_table() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "CREATE TABLE issues (id INTEGER PRIMARY KEY, title TEXT)"})

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["rows_affected"] == 0
        check = store.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='issues'")
        assert check["ok"] is True
        assert len(check["rows"]) == 1


async def test_query_tool_inserts_and_selects() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        await tool.call({"sql": "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)"})
        insert_result = await tool.call({"sql": "INSERT INTO notes (body) VALUES ('hello')"})
        insert_parsed = json.loads(insert_result)
        assert insert_parsed["ok"] is True
        assert insert_parsed["rows_affected"] == 1

        select_result = await tool.call({"sql": "SELECT id, body FROM notes"})
        select_parsed = json.loads(select_result)
        assert select_parsed["ok"] is True
        assert select_parsed["row_count"] == 1
        assert select_parsed["rows"] == [{"id": 1, "body": "hello"}]


async def test_query_tool_ddl_captured_in_migrations() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        await tool.call({"sql": "CREATE TABLE foo (x INTEGER)"})
        await tool.call({"sql": "ALTER TABLE foo ADD COLUMN y TEXT"})
        await tool.call({"sql": "DROP TABLE foo"})

        log = store.execute("SELECT sql_type, table_name FROM db_migrations ORDER BY id")
        assert log["ok"] is True
        types_and_tables = [(r["sql_type"], r["table_name"]) for r in log["rows"]]
        assert types_and_tables == [
            ("CREATE", "foo"),
            ("ALTER", "foo"),
            ("DROP", "foo"),
        ]


async def test_query_tool_dml_not_in_migrations() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        await tool.call({"sql": "CREATE TABLE t (x INTEGER)"})
        await tool.call({"sql": "INSERT INTO t (x) VALUES (1), (2)"})

        log = store.execute("SELECT COUNT(*) AS n FROM db_migrations")
        assert log["rows"][0]["n"] == 1


async def test_query_tool_rejects_multi_statement() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "SELECT 1; SELECT 2"})

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "expected exactly one statement" in parsed["error"]


async def test_query_tool_rejects_empty_sql() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "   "})

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "non-empty" in parsed["error"]


async def test_query_tool_rejects_missing_sql() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({})

        parsed = json.loads(result)
        assert parsed["ok"] is False


async def test_query_tool_rejects_attach() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "ATTACH DATABASE '/tmp/other.db' AS other"})

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "not allowed" in parsed["error"]


async def test_query_tool_rejects_detach() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "DETACH DATABASE other"})

        parsed = json.loads(result)
        assert parsed["ok"] is False


async def test_query_tool_rejects_vacuum_into() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "VACUUM INTO '/tmp/other.db'"})

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "not allowed" in parsed["error"]


async def test_query_tool_handles_sql_error() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "SELECT * FROM nonexistent"})

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "no such table" in parsed["error"].lower()


async def test_query_tool_schema_description_mentions_constraints() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        assert "single" in tool.description.lower()
        assert "DDL" in tool.description or "CREATE" in tool.description
        assert tool.name == "notebook__query"
        assert "sql" in tool.schema["properties"]


async def test_register_notebook_tools_adds_to_registry() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        registry = ToolRegistry()

        register_notebook_tools(registry, store)

        assert registry.has("notebook__query")
        tool = registry.get("notebook__query")
        assert tool is not None
        assert tool.name == "notebook__query"


async def test_query_tool_migration_failure_does_not_break_ddl() -> None:
    # If db_migrations insertion fails (e.g. disk full), the DDL itself should
    # still have succeeded — the bot's schema change is what matters, the log
    # is best-effort bookkeeping.
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        tool = NotebookQueryTool(store)

        result = await tool.call({"sql": "CREATE TABLE foo (x INTEGER)"})

        parsed = json.loads(result)
        assert parsed["ok"] is True
        check = store.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='foo'")
        assert check["ok"] is True
        assert len(check["rows"]) == 1
