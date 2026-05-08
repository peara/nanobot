#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

import yaml


def _count_if_table_exists(conn: sqlite3.Connection, table: str) -> int | None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    if not row:
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _clear_main_db(path: Path, dry_run: bool) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with sqlite3.connect(path) as conn:
        before_messages = _count_if_table_exists(conn, "messages")
        before_contexts = _count_if_table_exists(conn, "contexts")
        if not dry_run:
            if before_messages is not None:
                conn.execute("DELETE FROM messages")
            if before_contexts is not None:
                conn.execute("DELETE FROM contexts")
            conn.commit()
        after_messages = _count_if_table_exists(conn, "messages")
        after_contexts = _count_if_table_exists(conn, "contexts")
    return {
        "exists": True,
        "before_messages": before_messages,
        "after_messages": after_messages,
        "before_contexts": before_contexts,
        "after_contexts": after_contexts,
    }


def _clear_scheduler_db(path: Path, dry_run: bool) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with sqlite3.connect(path) as conn:
        before_tasks = _count_if_table_exists(conn, "scheduled_tasks")
        if not dry_run and before_tasks is not None:
            conn.execute("DELETE FROM scheduled_tasks")
            conn.commit()
        after_tasks = _count_if_table_exists(conn, "scheduled_tasks")
    return {
        "exists": True,
        "before_tasks": before_tasks,
        "after_tasks": after_tasks,
    }


def _reset_mem0(config_path: Path, dry_run: bool) -> dict[str, Any]:
    if not config_path.exists():
        return {"attempted": False, "ok": False, "reason": f"config not found: {config_path}"}
    if dry_run:
        return {"attempted": True, "ok": True, "method": "dry-run"}

    try:
        from mem0 import Memory
    except Exception as exc:  # pylint: disable=broad-except
        return {"attempted": True, "ok": False, "reason": f"mem0 import failed: {exc}"}

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        mem = Memory.from_config(cfg)
    except Exception as exc:  # pylint: disable=broad-except
        return {"attempted": True, "ok": False, "reason": f"mem0 init failed: {exc}"}

    for method in ("reset", "delete_all", "clear", "wipe"):
        if not hasattr(mem, method):
            continue
        fn = getattr(mem, method)
        try:
            fn()  # type: ignore[misc]
            return {"attempted": True, "ok": True, "method": method}
        except TypeError:
            try:
                fn(user_id="*")  # type: ignore[misc]
                return {"attempted": True, "ok": True, "method": f"{method}(user_id=*)"}
            except Exception:
                continue
        except Exception:
            continue
    return {"attempted": True, "ok": False, "reason": "no supported mem0 clear method found"}


def _ensure_qdrant_collections(config_path: Path, dry_run: bool) -> dict[str, Any]:
    """Ensure Qdrant collections exist after mem0 reset wipes them."""
    if not config_path.exists():
        return {"attempted": False, "ok": False, "reason": f"config not found: {config_path}"}
    if dry_run:
        return {"attempted": True, "ok": True, "method": "dry-run"}

    try:
        from nanobot.vector_store import COLLECTION_SKILLS, VectorStore
    except Exception as exc:  # pylint: disable=broad-except
        return {"attempted": True, "ok": False, "reason": f"VectorStore import failed: {exc}"}

    try:
        vector_store = VectorStore(str(config_path))
        vector_store.ensure_collection(COLLECTION_SKILLS)
    except Exception as exc:  # pylint: disable=broad-except
        return {"attempted": True, "ok": False, "reason": f"collection init failed: {exc}"}

    return {"attempted": True, "ok": True, "collections": ["nanobot_skills"]}


def _print_report(title: str, payload: dict[str, Any]) -> None:
    print(f"\n[{title}]")
    for key, value in payload.items():
        print(f"- {key}: {value}")


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Clear nanobot scheduler, message history, local context memory, and mem0 memory."
    )
    parser.add_argument("--database", default=str(repo_root / "data/nanobot.db"), help="Path to main SQLite database")
    parser.add_argument(
        "--scheduler-db",
        default=str(repo_root / "data/scheduler.db"),
        help="Path to scheduler SQLite database",
    )
    parser.add_argument("--mem0-config", default=str(repo_root / "config.mem0.yaml"), help="Path to mem0 config")
    parser.add_argument("--skip-mem0", action="store_true", help="Skip mem0 reset")
    parser.add_argument("--dry-run", action="store_true", help="Show current counts without deleting")
    args = parser.parse_args()

    dry_run = bool(args.dry_run)
    main_result = _clear_main_db(Path(args.database), dry_run=dry_run)
    scheduler_result = _clear_scheduler_db(Path(args.scheduler_db), dry_run=dry_run)

    mem0_result: dict[str, Any]
    if args.skip_mem0:
        mem0_result = {"attempted": False, "ok": True, "reason": "skipped by flag"}
    else:
        mem0_result = _reset_mem0(Path(args.mem0_config), dry_run=dry_run)

    qdrant_result: dict[str, Any]
    if args.skip_mem0:
        qdrant_result = {"attempted": False, "ok": True, "reason": "skipped by flag"}
    else:
        qdrant_result = _ensure_qdrant_collections(Path(args.mem0_config), dry_run=dry_run)

    mode = "DRY RUN" if dry_run else "RESET COMPLETE"
    print(f"nanobot state reset - {mode}")
    _print_report("local-db", main_result)
    _print_report("scheduler-db", scheduler_result)
    _print_report("mem0", mem0_result)
    _print_report("qdrant-collections", qdrant_result)


if __name__ == "__main__":
    main()
