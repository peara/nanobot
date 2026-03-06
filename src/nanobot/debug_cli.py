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


def _clip_text(text: str, limit: int = 180) -> str:
    compact = text.strip().replace("\n", " ")
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _q_stats(text: str) -> tuple[int, int, float]:
    total = len(text)
    if total == 0:
        return 0, 0, 0.0
    q_count = text.count("?")
    return q_count, total, (q_count / total)


def _latest_plan_run_id(db_path: str) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT scope_id
            FROM contexts
            WHERE scope_type = 'plan_run' AND key = 'status'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row[0]) if row else None


def _plan_list(config: AppConfig, limit: int) -> None:
    with _connect(config.database_path) as conn:
        run_rows = conn.execute(
            """
            SELECT scope_id, value_json, updated_at
            FROM contexts
            WHERE scope_type = 'plan_run' AND key = 'status'
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        if not run_rows:
            print("No plan runs found.")
            return
        for run_id, status_json, status_at in run_rows:
            status_val = json.loads(str(status_json)).get("value", "unknown")
            req_row = conn.execute(
                """
                SELECT value_json
                FROM contexts
                WHERE scope_type = 'plan_run' AND scope_id = ? AND key = 'request_text'
                """,
                (run_id,),
            ).fetchone()
            res_row = conn.execute(
                """
                SELECT value_json
                FROM contexts
                WHERE scope_type = 'plan_run' AND scope_id = ? AND key = 'result'
                """,
                (run_id,),
            ).fetchone()
            request_text = ""
            if req_row:
                request_text = str(json.loads(str(req_row[0])).get("text", ""))
            result_text = ""
            if res_row:
                result_text = str(json.loads(str(res_row[0])).get("text", ""))
            q_count, total, ratio = _q_stats(result_text)
            print(f"run_id={run_id}\tstatus={status_val}\tupdated_at={status_at}")
            print(f"  request={_clip_text(request_text, limit=120)}")
            if total > 0:
                print(f"  result_q_ratio={ratio:.3f} ({q_count}/{total})")


def _plan_show(config: AppConfig, run_id: str) -> None:
    with _connect(config.database_path) as conn:
        rows = conn.execute(
            """
            SELECT key, value_json, updated_at
            FROM contexts
            WHERE scope_type = 'plan_run' AND scope_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
    if not rows:
        print(f"No plan_run found for run_id: {run_id}")
        return
    print(f"plan_run: {run_id}")
    wanted_keys = [
        "status",
        "request_text",
        "plan_brief",
        "intake_raw",
        "execution_raw",
        "recovery_raw",
        "result",
        "error",
    ]
    by_key: dict[str, tuple[Any, str]] = {}
    for key, value_json, updated_at in rows:
        try:
            parsed = json.loads(str(value_json))
        except json.JSONDecodeError:
            parsed = str(value_json)
        by_key[str(key)] = (parsed, str(updated_at))

    for key in wanted_keys:
        if key not in by_key:
            continue
        value, updated_at = by_key[key]
        print(f"\n[{key}] updated_at={updated_at}")
        if key in {"intake_raw", "execution_raw", "recovery_raw", "result"} and isinstance(value, dict):
            text = str(value.get("text", ""))
            q_count, total, ratio = _q_stats(text)
            print(f"q_ratio={ratio:.3f} ({q_count}/{total})")
            print(f"preview={_clip_text(text, limit=240)}")
            continue
        if key == "request_text" and isinstance(value, dict):
            print(f"text={_clip_text(str(value.get('text', '')), limit=240)}")
            continue
        if isinstance(value, (dict, list)):
            print(json.dumps(value, ensure_ascii=True, indent=2))
        else:
            print(str(value))


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

    plan = sub.add_parser("plan", help="Inspect plan_run context traces")
    plan_sub = plan.add_subparsers(dest="plan_cmd", required=True)
    plan_list = plan_sub.add_parser("list", help="List recent plan runs")
    plan_list.add_argument("--limit", type=int, default=10, help="Number of recent runs to show")
    plan_show = plan_sub.add_parser("show", help="Show detailed plan run fields")
    plan_show.add_argument("--run-id", help="Run id, e.g. run-abc123")
    plan_show.add_argument("--latest", action="store_true", help="Use latest plan run by status update")

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

    if args.cmd == "plan":
        if args.plan_cmd == "list":
            _plan_list(config, int(args.limit))
            return
        if args.plan_cmd == "show":
            run_id = args.run_id
            if args.latest:
                run_id = _latest_plan_run_id(config.database_path)
            if not run_id:
                raise SystemExit("Run id is required. Pass --run-id or --latest.")
            _plan_show(config, run_id)
            return


if __name__ == "__main__":
    main()
