from __future__ import annotations

import sqlite3
from pathlib import Path


class NanoScriptSqlite:
    """SQLite manager for NanoScript tables with file-based migrations."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        migration_path = Path(__file__).parent / "migrations" / "001_nanoscript.sql"
        sql = migration_path.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(sql)
