from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from typing import Any

from nanobot.config import AppConfig, load_config
from nanobot.hooks import HookDebugCommand, build_default_tool_hooks
from nanobot.plans import PlanStore
from nanobot.prompts import PromptStore
from nanobot.skills import SkillStore, SkillVectorStore
from nanobot.tracing import semconv
from nanobot.tracing.store import SpanRecord, TraceStore
from nanobot.vector_store import VectorStore

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

    prompts = PromptStore(config.prompt_db_path)
    if full:
        payload = {
            "model": config.model.model,
            "temperature": config.model.temperature,
            "max_tokens": config.model.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": prompts.render(
                        "orchestrator_main",
                        assistant_name=config.assistant_name,
                    ),
                },
                {
                    "role": "system",
                    "content": prompts.render(
                        "orchestrator_main_time",
                        working_timezone=config.working_timezone,
                        current_time="(debug context time unavailable)",
                    ),
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


def _plans_list(config: AppConfig, limit: int) -> None:
    store = PlanStore(config.plan_db_path)
    plans = store.list_plans(limit=limit)
    if not plans:
        print("No plans found.")
        return
    for plan in plans:
        goal_preview = plan.goal[:60] + ("..." if len(plan.goal) > 60 else "")
        print(f"id={plan.id}\tname={plan.name}")
        print(f"\tgoal: {goal_preview}")
        print(f"\tstats: {plan.success_count} success, {plan.failure_count} failure")


def _plans_show(config: AppConfig, plan_id: int) -> None:
    store = PlanStore(config.plan_db_path)
    plan = store.get(plan_id)
    if not plan:
        print(f"Plan {plan_id} not found.")
        return
    print(f"Plan {plan.id}: {plan.name}")
    print(f"  Goal: {plan.goal}")
    print(f"  Constraints: {', '.join(plan.constraints) or 'none'}")
    print(f"  Required inputs: {', '.join(plan.required_inputs) or 'none'}")
    print(f"  Risk flags: {', '.join(plan.risk_flags) or 'none'}")
    print(f"  Notes: {plan.notes or 'none'}")
    print(f"  Source: {plan.source_type} ({plan.source_scope})")
    print(f"  Version: {plan.version}")
    print(f"  Stats: {plan.success_count} success, {plan.failure_count} failure")
    print(f"  Created: {plan.created_at.isoformat() if plan.created_at else 'unknown'}")
    print(f"  Updated: {plan.updated_at.isoformat() if plan.updated_at else 'unknown'}")
    if plan.steps:
        print(f"  Steps ({len(plan.steps)}):")
        for i, step in enumerate(plan.steps, 1):
            print(f"    {i}. {step}")


def _skills_resync(config: AppConfig) -> None:
    if not config.mem0_config_path:
        raise SystemExit("No mem0_config_path configured. Set it in config.yaml to enable skill resync.")

    skill_store = SkillStore(config.skill_db_path)
    vector_store = VectorStore(config.mem0_config_path)
    mem0_store = SkillVectorStore(vector_store)

    skills = skill_store.list_all()
    intelligent_skills = [s for s in skills if s.trigger_mode == "intelligent"]

    if not intelligent_skills:
        print("No intelligent skills found in SQLite.")
        return

    print(f"Resyncing {len(intelligent_skills)} intelligent skills to mem0...")
    for skill in intelligent_skills:
        try:
            mem0_store.remove_skill(skill.name)
            mem0_store.store_skill(skill)
            print(f"  [OK] {skill.name}")
        except Exception as exc:
            print(f"  [FAIL] {skill.name}: {exc}")

    print("Done.")


def _trace_db_path(config: AppConfig) -> str:
    if config.tracing is None:
        raise SystemExit("Tracing is not configured. Add a `tracing:` section to config.yaml (see docs/TRACING.md).")
    return config.tracing.db_path


def _fmt_duration(duration_ms: float) -> str:
    if duration_ms >= 1000:
        return f"{duration_ms / 1000:.2f}s"
    return f"{duration_ms:.0f}ms"


def _span_time(rec: SpanRecord) -> str:
    return datetime.fromtimestamp(rec.start_ts).strftime("%H:%M:%S")


def _format_span_line(rec: SpanRecord) -> str:
    attrs = rec.attributes()
    parts = [rec.name]
    if rec.name == semconv.OP_INVOKE_AGENT:
        parts.append(f"run_id={rec.run_id or '-'}")
        if semconv.RUN_DEPTH in attrs:
            parts.append(f"depth={attrs[semconv.RUN_DEPTH]}")
        goal = attrs.get(semconv.RUN_GOAL)
        if goal:
            parts.append(f'goal="{goal}"')
    elif rec.name == semconv.OP_CHAT:
        model = attrs.get(semconv.GEN_AI_RESPONSE_MODEL) or attrs.get(semconv.GEN_AI_REQUEST_MODEL) or "-"
        parts.append(f"model={model}")
        if semconv.GEN_AI_USAGE_INPUT_TOKENS in attrs:
            parts.append(f"in={attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS]}")
        if semconv.GEN_AI_USAGE_OUTPUT_TOKENS in attrs:
            parts.append(f"out={attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS]}")
        finish = attrs.get(semconv.GEN_AI_RESPONSE_FINISH_REASONS) or []
        parts.append(f"finish={','.join(str(f) for f in finish) or '-'}")
        if attrs.get(semconv.LLM_EMPTY_CONTENT):
            parts.append("empty_content")
        if attrs.get(semconv.CANCELLED):
            parts.append("cancelled")
        if rec.status == "error" and semconv.LLM_MESSAGE_CHARS in attrs:
            parts.append(f"msgs={attrs.get(semconv.LLM_MESSAGE_COUNT, '-')} chars={attrs[semconv.LLM_MESSAGE_CHARS]}")
    elif rec.name == semconv.OP_EXECUTE_TOOL:
        parts.append(f"tool={attrs.get(semconv.GEN_AI_TOOL_NAME, '-')}")
        if semconv.RUN_ID in attrs:
            parts.append(f"run_id={attrs[semconv.RUN_ID]}")
    elif rec.name == semconv.SPAN_DELEGATE_TASK:
        if semconv.CHILD_RUN_ID in attrs:
            parts.append(f"child_run_id={attrs[semconv.CHILD_RUN_ID]}")
        goal = attrs.get(semconv.RUN_GOAL)
        if goal:
            parts.append(f'goal="{goal}"')
    if rec.status == "error":
        parts.append(f"ERROR={rec.status_message or '?'}")
    parts.append(_fmt_duration(rec.duration_ms))
    return " ".join(parts)


def _print_span_tree(
    rec: SpanRecord,
    children: dict[str | None, list[SpanRecord]],
    line_prefix: str,
    kids_prefix: str,
) -> None:
    print(f"{line_prefix}{_span_time(rec)} {_format_span_line(rec)}")
    kids = children.get(rec.span_id, [])
    for i, kid in enumerate(kids):
        last = i == len(kids) - 1
        branch = "└─ " if last else "├─ "
        _print_span_tree(
            kid,
            children,
            kids_prefix + branch,
            kids_prefix + ("   " if last else "│  "),
        )


def _trace_token_accounting(trace_spans: list[SpanRecord]) -> None:
    runs: dict[str, dict[str, int]] = {}
    for rec in trace_spans:
        if rec.name != semconv.OP_CHAT:
            continue
        attrs = rec.attributes()
        run_key = rec.run_id or "-"
        bucket = runs.setdefault(run_key, {"calls": 0, "input": 0, "output": 0})
        bucket["calls"] += 1
        bucket["input"] += int(attrs.get(semconv.GEN_AI_USAGE_INPUT_TOKENS, 0) or 0)
        bucket["output"] += int(attrs.get(semconv.GEN_AI_USAGE_OUTPUT_TOKENS, 0) or 0)
    if not runs:
        return
    print("\nToken accounting:")
    for run_key, bucket in runs.items():
        total = bucket["input"] + bucket["output"]
        print(
            f"  {run_key}: {bucket['calls']} chat call(s), "
            f"input={bucket['input']} output={bucket['output']} total={total}"
        )


def _trace_anomalies(trace_spans: list[SpanRecord]) -> None:
    length_truncations = 0
    empty_replies = 0
    cancelled = 0
    errors = 0
    for rec in trace_spans:
        attrs = rec.attributes()
        if rec.name == semconv.OP_CHAT:
            if "length" in (attrs.get(semconv.GEN_AI_RESPONSE_FINISH_REASONS) or []):
                length_truncations += 1
            if attrs.get(semconv.LLM_EMPTY_CONTENT):
                empty_replies += 1
            if attrs.get(semconv.CANCELLED):
                cancelled += 1
        if rec.status == "error":
            errors += 1
    print("\nAnomalies:")
    print(f"  length truncations: {length_truncations}")
    print(f"  empty replies: {empty_replies}")
    print(f"  cancelled spans: {cancelled}")
    print(f"  error spans: {errors}")


def _trace_list(config: AppConfig, limit: int) -> None:
    store = TraceStore(_trace_db_path(config))
    rows = store.recent_runs(limit=limit)
    if not rows:
        print("No traced runs found.")
        return
    print(f"{'run_id':<16} {'started':<20} {'spans':>5} {'errors':>6}")
    for row in rows:
        started = datetime.fromtimestamp(float(row["started_ts"])).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{row['run_id']:<16} {started:<20} {row['span_count']:>5} {row['error_count'] or 0:>6}")


def _latest_trace_run_id(db_path: str) -> str | None:
    rows = TraceStore(db_path).recent_runs(limit=1)
    return str(rows[0]["run_id"]) if rows else None


def _trace_show(config: AppConfig, run_id: str) -> None:
    store = TraceStore(_trace_db_path(config))
    run_spans = store.list_by_run(run_id)
    if not run_spans:
        print(f"No spans found for run_id: {run_id}")
        return
    anchor = run_spans[0]
    trace_spans = store.list_by_trace(anchor.trace_id)
    span_ids = {s.span_id for s in trace_spans}
    children: dict[str | None, list[SpanRecord]] = {}
    roots: list[SpanRecord] = []
    for rec in trace_spans:
        if rec.parent_span_id and rec.parent_span_id in span_ids:
            children.setdefault(rec.parent_span_id, []).append(rec)
        else:
            roots.append(rec)
    if len(trace_spans) > len(run_spans):
        print(f"(run {run_id} is part of a larger trace — showing the full tree)\n")
    print(f"Trace replay for {run_id}")
    print(f"trace_id: {anchor.trace_id}")
    started = datetime.fromtimestamp(anchor.start_ts)
    ended = datetime.fromtimestamp(max(s.end_ts for s in trace_spans))
    print(f"started: {started.strftime('%Y-%m-%d %H:%M:%S')}  ended: {ended.strftime('%H:%M:%S')}")
    print(f"spans: {len(trace_spans)}")
    print("\nSpan tree:")
    for root in sorted(roots, key=lambda s: s.start_ts):
        _print_span_tree(root, children, "", "")
    _trace_token_accounting(trace_spans)
    _trace_anomalies(trace_spans)


def main() -> None:
    hook_commands: dict[str, HookDebugCommand] = {}
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

    plans = sub.add_parser("plans", help="Inspect persistent plans")
    plans_sub = plans.add_subparsers(dest="plans_cmd", required=True)
    plans_list = plans_sub.add_parser("list", help="List saved plans")
    plans_list.add_argument("--limit", type=int, default=20, help="Number of plans to show")
    plans_show = plans_sub.add_parser("show", help="Show plan details")
    plans_show.add_argument("id", type=int, help="Plan ID")

    sub.add_parser("skills-resync", help="Resync intelligent skills to mem0 (remove + re-store)")

    trace = sub.add_parser("trace", help="Inspect OTel span traces (ROADMAP Phase D)")
    trace_sub = trace.add_subparsers(dest="trace_cmd", required=True)
    trace_list = trace_sub.add_parser("list", help="List recent traced runs")
    trace_list.add_argument("--limit", type=int, default=20, help="Number of recent runs to show")
    trace_show = trace_sub.add_parser("show", help="Replay a run end-to-end from its spans")
    trace_show.add_argument("--run-id", help="Run id, e.g. run-abc123")
    trace_show.add_argument("--latest", action="store_true", help="Use the most recent traced run")

    for hook in build_default_tool_hooks():
        debug_commands = getattr(hook, "debug_commands", None)
        if not callable(debug_commands):
            continue
        for cmd in debug_commands():
            cmd_parser = sub.add_parser(cmd.name, help=cmd.help)
            cmd.add_arguments(cmd_parser)
            hook_commands[cmd.name] = cmd

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

    if args.cmd == "plans":
        if args.plans_cmd == "list":
            _plans_list(config, int(args.limit))
            return
        if args.plans_cmd == "show":
            _plans_show(config, args.id)
            return

    if args.cmd == "skills-resync":
        _skills_resync(config)
        return

    if args.cmd == "trace":
        if args.trace_cmd == "list":
            _trace_list(config, int(args.limit))
            return
        if args.trace_cmd == "show":
            run_id = args.run_id
            if args.latest:
                run_id = _latest_trace_run_id(_trace_db_path(config))
                if not run_id:
                    raise SystemExit("No traced runs found.")
            if not run_id:
                raise SystemExit("Run id is required. Pass --run-id or --latest.")
            _trace_show(config, run_id)
            return

    if args.cmd in hook_commands:
        hook_commands[args.cmd].run(args, config.database_path)
        return


if __name__ == "__main__":
    main()
