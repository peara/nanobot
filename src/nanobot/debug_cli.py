from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from nanobot.config import AppConfig, load_config

PLACEHOLDER_SCOPES = {"12345", "123456789", "1234567890", "<current_chat_id>", "current_chat_id", "default"}


def _trim_history_by_chars(messages: list[dict[str, Any]], char_limit: int) -> list[dict[str, Any]]:
    if char_limit <= 0:
        return messages
    kept_reversed: list[dict[str, Any]] = []
    total = 0
    for msg in reversed(messages):
        content = str(msg.get("content", ""))
        msg_len = len(content)
        if kept_reversed and total + msg_len > char_limit:
            break
        kept_reversed.append(msg)
        total += msg_len
    kept_reversed.reverse()
    return kept_reversed


def _connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _latest_scope(db_path: str) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT chat_id FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    return str(row[0]) if row else None


def _list_scopes(config: AppConfig) -> None:
    with _connect(config.database_path) as conn:
        rows = conn.execute(
            "SELECT chat_id, COUNT(*) as c, MAX(id) as last_id FROM messages GROUP BY chat_id ORDER BY last_id DESC"
        ).fetchall()
    if not rows:
        print("No messages found.")
        return
    for chat_id, count, last_id in rows:
        print(f"{chat_id}\tcount={count}\tlast_id={last_id}")


def _show_context(config: AppConfig, scope: str, full: bool, tail: int) -> None:
    with _connect(config.database_path) as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (scope,)).fetchone()[0])
        rows = conn.execute(
            """
            SELECT role, content
            FROM messages
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (scope, config.history_message_limit),
        ).fetchall()
    rows = list(reversed(rows))
    recent = [{"role": role, "content": content} for role, content in rows]
    trimmed = _trim_history_by_chars(recent, config.history_char_limit)

    if full:
        payload = {
            "model": config.model.model,
            "temperature": config.model.temperature,
            "max_tokens": config.model.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": config.system_prompt_template.format(assistant_name=config.assistant_name),
                },
                *trimmed,
            ],
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    recent_chars = sum(len(str(m.get("content", ""))) for m in recent)
    trimmed_chars = sum(len(str(m.get("content", ""))) for m in trimmed)
    print("Context report")
    print(f"scope: {scope}")
    print(f"total_messages_in_db: {total}")
    print(f"recent_window_limit: {config.history_message_limit}")
    print(f"char_limit: {config.history_char_limit}")
    print(f"messages_after_limit: {len(recent)} ({recent_chars} chars)")
    print(f"messages_after_trim: {len(trimmed)} ({trimmed_chars} chars)")
    print("included_tail:")
    tail_items = trimmed[-max(1, tail) :]
    if not tail_items:
        print("- (empty)")
    else:
        for m in tail_items:
            content = str(m["content"]).strip().replace("\n", " ")
            if len(content) > 120:
                content = f"{content[:120]}..."
            print(f"- {m['role']}: {content}")


def _reset_scope(config: AppConfig, scope: str) -> None:
    with _connect(config.database_path) as conn:
        cur = conn.execute("DELETE FROM messages WHERE chat_id = ?", (scope,))
        conn.commit()
        deleted = int(cur.rowcount)
    print(f"Deleted {deleted} messages from scope: {scope}")


def _scheduler_list(config: AppConfig) -> None:
    with _connect(config.scheduler_db_path) as conn:
        rows = conn.execute(
            "SELECT id, chat_id, cron_expr, enabled, next_run_at FROM scheduled_tasks ORDER BY id ASC"
        ).fetchall()
    if not rows:
        print("No scheduled tasks.")
        return
    for task_id, chat_id, cron_expr, enabled, next_run_at in rows:
        print(f"id={task_id}\tchat_id={chat_id}\tenabled={enabled}\tcron={cron_expr}\tnext_run_at={next_run_at}")


def _is_invalid_scope(scope: str) -> bool:
    value = scope.strip()
    return (":" not in value) or (value.lower() in PLACEHOLDER_SCOPES)


def _scheduler_clear(config: AppConfig) -> None:
    with _connect(config.scheduler_db_path) as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0])
        conn.execute("DELETE FROM scheduled_tasks")
        conn.commit()
    print(f"Cleared scheduled tasks: {before}")


def _scheduler_clear_invalid(config: AppConfig, purge_messages: bool) -> None:
    with _connect(config.scheduler_db_path) as conn:
        rows = conn.execute("SELECT DISTINCT chat_id FROM scheduled_tasks").fetchall()
        invalid_scopes = sorted({str(r[0]) for r in rows if _is_invalid_scope(str(r[0]))})
        if not invalid_scopes:
            print("No invalid scheduler scopes found.")
            return
        qmarks = ",".join("?" for _ in invalid_scopes)
        before = int(
            conn.execute(
                f"SELECT COUNT(*) FROM scheduled_tasks WHERE chat_id IN ({qmarks})",
                invalid_scopes,
            ).fetchone()[0]
        )
        conn.execute(f"DELETE FROM scheduled_tasks WHERE chat_id IN ({qmarks})", invalid_scopes)
        conn.commit()
    print(f"Removed invalid scheduled tasks: {before}")
    print("Invalid scopes:", ", ".join(invalid_scopes))

    if purge_messages:
        with _connect(config.database_path) as conn:
            qmarks = ",".join("?" for _ in invalid_scopes)
            before_msgs = int(
                conn.execute(f"SELECT COUNT(*) FROM messages WHERE chat_id IN ({qmarks})", invalid_scopes).fetchone()[0]
            )
            conn.execute(f"DELETE FROM messages WHERE chat_id IN ({qmarks})", invalid_scopes)
            conn.commit()
        print(f"Purged messages in invalid scopes: {before_msgs}")


def main() -> None:
    parser = argparse.ArgumentParser(description="nanobot debug CLI")
    parser.add_argument("--config", default="config.yaml", help="Path to app config file")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scopes", help="List message scopes")

    ctx = sub.add_parser("ctx", help="Show context report for a scope")
    ctx.add_argument("--scope", help="Scoped chat id, e.g. telegram:500506690")
    ctx.add_argument("--latest", action="store_true", help="Use latest scope from DB")
    ctx.add_argument("--full", action="store_true", help="Output full LLM payload JSON")
    ctx.add_argument("--tail", type=int, default=8, help="Tail count for compact report")

    reset = sub.add_parser("reset", help="Clear local message history for a scope")
    reset.add_argument("--scope", help="Scoped chat id, e.g. telegram:500506690")
    reset.add_argument("--latest", action="store_true", help="Use latest scope from DB")

    sched = sub.add_parser("scheduler", help="Inspect or clean scheduler tasks")
    sched_sub = sched.add_subparsers(dest="scheduler_cmd", required=True)
    sched_sub.add_parser("list", help="List scheduled tasks")
    sched_sub.add_parser("clear", help="Delete all scheduled tasks")
    clear_invalid = sched_sub.add_parser("clear-invalid", help="Delete tasks with invalid placeholder chat scopes")
    clear_invalid.add_argument(
        "--purge-messages",
        action="store_true",
        help="Also delete conversation messages stored under invalid scopes",
    )

    args = parser.parse_args()
    config = load_config(args.config)

    if args.cmd == "scopes":
        _list_scopes(config)
        return

    if args.cmd in {"ctx", "reset"}:
        scope = args.scope
        if args.latest:
            scope = _latest_scope(config.database_path)
        if not scope:
            raise SystemExit("Scope is required. Pass --scope or --latest.")
        if args.cmd == "ctx":
            _show_context(config, scope, bool(args.full), int(args.tail))
            return
        _reset_scope(config, scope)
        return

    if args.cmd == "scheduler":
        if args.scheduler_cmd == "list":
            _scheduler_list(config)
            return
        if args.scheduler_cmd == "clear":
            _scheduler_clear(config)
            return
        if args.scheduler_cmd == "clear-invalid":
            _scheduler_clear_invalid(config, bool(args.purge_messages))
            return


if __name__ == "__main__":
    main()
