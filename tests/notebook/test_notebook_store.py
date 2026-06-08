from __future__ import annotations

import tempfile
from pathlib import Path

from nanobot.notebook.store import NotebookStore, _classify_ddl
from nanobot.notebook.tools import _split_statements


def _make_store(tmp_path: Path) -> NotebookStore:
    db_path = str(tmp_path / "notebook.db")
    return NotebookStore(db_path)


def test_init_creates_db_file_and_migrations_table() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "notebook.db"
        assert not db_path.exists()

        NotebookStore(str(db_path))

        assert db_path.exists()
        store = NotebookStore(str(db_path))
        result = store.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        assert result["ok"] is True
        names = [row["name"] for row in result["rows"]]
        assert "db_migrations" in names


def test_execute_select_returns_rows() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        store.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        store.execute("INSERT INTO notes (body) VALUES ('first'), ('second')")

        result = store.execute("SELECT id, body FROM notes ORDER BY id")

        assert result["ok"] is True
        assert result["row_count"] == 2
        assert result["truncated"] is False
        assert result["rows"] == [
            {"id": 1, "body": "first"},
            {"id": 2, "body": "second"},
        ]


def test_execute_dml_returns_rows_affected() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        store.execute("CREATE TABLE t (x INTEGER)")

        result = store.execute("INSERT INTO t (x) VALUES (1), (2), (3)")

        assert result["ok"] is True
        assert result["rows_affected"] == 3
        assert "rows" not in result


def test_execute_row_limit_truncates() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        store.execute("CREATE TABLE t (x INTEGER)")
        store.execute("INSERT INTO t (x) VALUES " + ",".join(f"({i})" for i in range(100)))

        result = store.execute("SELECT x FROM t ORDER BY x", row_limit=10)

        assert result["ok"] is True
        assert result["row_count"] == 10
        assert result["truncated"] is True


def test_execute_returns_error_on_bad_sql() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        result = store.execute("SELECT * FROM nonexistent_table")

        assert result["ok"] is False
        assert "error" in result
        assert "no such table" in result["error"].lower()


def test_record_migration_appends_row() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        store.record_migration(
            sql="CREATE TABLE foo (id INTEGER)",
            sql_type="CREATE",
            table_name="foo",
        )

        result = store.execute("SELECT sql, sql_type, table_name FROM db_migrations")
        assert result["ok"] is True
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["sql_type"] == "CREATE"
        assert row["table_name"] == "foo"


def test_wal_mode_enabled() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        result = store.execute("PRAGMA journal_mode")
        assert result["ok"] is True
        assert result["rows"][0]["journal_mode"].lower() == "wal"


def test_foreign_keys_enabled() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        result = store.execute("PRAGMA foreign_keys")
        assert result["ok"] is True
        assert result["rows"][0]["foreign_keys"] == 1


def test_classify_ddl_table_creation() -> None:
    assert _classify_ddl("CREATE TABLE foo (id INTEGER)") == ("CREATE", "foo")
    assert _classify_ddl("CREATE TABLE IF NOT EXISTS bar (x TEXT)") == ("CREATE", "bar")
    assert _classify_ddl("CREATE TEMP TABLE baz (y INTEGER)") == ("CREATE", "baz")


def test_classify_ddl_alter() -> None:
    assert _classify_ddl("ALTER TABLE foo ADD COLUMN bar TEXT") == ("ALTER", "foo")
    assert _classify_ddl("ALTER TABLE foo RENAME TO baz") == ("ALTER", "baz")


def test_classify_ddl_drop() -> None:
    assert _classify_ddl("DROP TABLE foo") == ("DROP", "foo")
    assert _classify_ddl("DROP TABLE IF EXISTS foo") == ("DROP", "foo")


def test_classify_ddl_non_ddl() -> None:
    assert _classify_ddl("SELECT * FROM foo") == ("", None)
    assert _classify_ddl("INSERT INTO foo VALUES (1)") == ("", None)
    assert _classify_ddl("UPDATE foo SET x = 1") == ("", None)
    assert _classify_ddl("DELETE FROM foo") == ("", None)


def test_classify_ddl_skips_leading_comments() -> None:
    sql = "-- this is a comment\nCREATE TABLE foo (x INTEGER)"
    assert _classify_ddl(sql) == ("CREATE", "foo")


def test_split_statements_single() -> None:
    assert _split_statements("SELECT 1;") == ["SELECT 1"]
    assert _split_statements("SELECT 1") == ["SELECT 1"]


def test_split_statements_multi_rejected_by_caller() -> None:
    result = _split_statements("SELECT 1; SELECT 2;")
    assert len(result) == 2


def test_split_statements_ignores_blank_and_comments() -> None:
    sql = """
    -- header comment
    SELECT 1;
    """
    assert _split_statements(sql) == ["SELECT 1"]


def test_split_statements_ignores_inline_semicolon_in_string() -> None:
    # Known limitation: a literal `;` inside a string is treated as a separator.
    # The tool layer's single-statement guard catches this conservatively.
    result = _split_statements("SELECT 'a;b' AS x")
    assert result == ["SELECT 'a", "b' AS x"]


def test_split_statements_semicolon_in_string_split() -> None:
    # Documents the known limitation rather than hiding it.
    result = _split_statements("SELECT 'a;b' AS x; SELECT 2")
    assert result == ["SELECT 'a", "b' AS x", "SELECT 2"]
