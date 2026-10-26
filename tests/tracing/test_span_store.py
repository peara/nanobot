from __future__ import annotations

import json
from pathlib import Path

from nanobot.tracing.store import SpanRecord, TraceStore


def _record(
    span_id: str,
    *,
    trace_id: str = "t" * 32,
    parent_span_id: str | None = None,
    run_id: str | None = None,
    scope: str | None = None,
    name: str = "chat",
    start_ts: float = 1000.0,
    status: str = "unset",
) -> SpanRecord:
    return SpanRecord(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        run_id=run_id,
        scope=scope,
        kind="CLIENT",
        name=name,
        start_ts=start_ts,
        end_ts=start_ts + 1.5,
        duration_ms=1500.0,
        status=status,
        status_message="boom" if status == "error" else None,
        attributes_json=json.dumps({"nanobot.run_id": run_id, "n": 1}),
        events_json=json.dumps([{"name": "exception"}]),
    )


def test_init_creates_schema_idempotently(tmp_path: Path) -> None:
    db = str(tmp_path / "traces.db")
    TraceStore(db)
    TraceStore(db)
    with TraceStore(db)._connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(spans)").fetchall()]
    assert cols == [
        "trace_id",
        "span_id",
        "parent_span_id",
        "run_id",
        "scope",
        "kind",
        "name",
        "start_ts",
        "end_ts",
        "duration_ms",
        "status",
        "status_message",
        "attributes_json",
        "events_json",
    ]


def test_memory_db_persists_across_connections() -> None:
    """':memory:' must share one connection — per-call connects would each
    create a fresh empty database and lose the schema."""
    store = TraceStore(":memory:")
    store.insert_spans([_record("a" * 16, run_id="run-1")])
    assert store.get("a" * 16) is not None
    assert [r.name for r in store.list_by_run("run-1")] == ["chat"]
    assert len(store.recent_runs(limit=5)) == 1


def test_insert_and_get_roundtrip(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces.db"))
    rec = _record("a" * 16, run_id="run-1", scope="telegram:1")
    store.insert_spans([rec])
    got = store.get("a" * 16)
    assert got is not None
    assert got.run_id == "run-1"
    assert got.attributes() == {"nanobot.run_id": "run-1", "n": 1}
    assert got.events() == [{"name": "exception"}]
    assert got.duration_ms == 1500.0
    assert got.status_message is None


def test_insert_empty_is_noop(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces.db"))
    store.insert_spans([])
    assert store.get("a" * 16) is None


def test_duplicate_span_id_ignored(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces.db"))
    rec = _record("a" * 16, run_id="run-1")
    store.insert_spans([rec])
    store.insert_spans([rec])
    assert len(store.list_by_run("run-1")) == 1


def test_list_by_run_orders_by_start(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces.db"))
    store.insert_spans(
        [
            _record("b" * 16, run_id="run-1", start_ts=2000.0),
            _record("a" * 16, run_id="run-1", start_ts=1000.0),
            _record("c" * 16, run_id="run-2", start_ts=1500.0),
        ]
    )
    rows = store.list_by_run("run-1")
    assert [r.span_id for r in rows] == ["a" * 16, "b" * 16]


def test_list_by_trace_filters_and_orders(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces.db"))
    store.insert_spans(
        [
            _record("a" * 16, trace_id="t" * 32, start_ts=1000.0),
            _record("b" * 16, trace_id="t" * 32, start_ts=2000.0),
            _record("c" * 16, trace_id="f" * 32, start_ts=1500.0),
        ]
    )
    rows = store.list_by_trace("t" * 32)
    assert [r.span_id for r in rows] == ["a" * 16, "b" * 16]


def test_recent_runs_groups_counts_and_excludes_null_run_id(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces.db"))
    store.insert_spans(
        [
            _record("a" * 16, run_id="run-old", start_ts=1000.0),
            _record("b" * 16, run_id="run-new", start_ts=3000.0),
            _record("c" * 16, run_id="run-new", start_ts=3100.0, status="error"),
            _record("d" * 16, run_id=None, start_ts=4000.0),
        ]
    )
    rows = store.recent_runs(limit=10)
    assert [r["run_id"] for r in rows] == ["run-new", "run-old"]
    assert rows[0]["span_count"] == 2
    assert rows[0]["error_count"] == 1
    assert rows[1]["span_count"] == 1
    assert rows[1]["error_count"] == 0
