"""Tests for the `trace` subcommand of the debug CLI (issue #48).

Feeds a seeded TraceStore through the _trace_list/_trace_show view functions
and asserts the replay output: tree indentation, per-run token accounting,
and the anomaly summary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.config import (
    AppConfig,
    ChannelConfig,
    ModelConfig,
    TracingConfig,
)
from nanobot.debug_cli import _latest_trace_run_id, _trace_db_path, _trace_list, _trace_show
from nanobot.tracing import semconv
from nanobot.tracing.store import SpanRecord, TraceStore


def _build_config(tmp_path: Path, *, tracing: TracingConfig | None | object = ...) -> AppConfig:
    if isinstance(tracing, type(...)):
        tracing = TracingConfig(db_path=str(tmp_path / "traces.db"))
    return AppConfig(
        assistant_name="Nano",
        database_path=str(tmp_path / "nanobot.db"),
        scheduler_db_path=str(tmp_path / "scheduler.db"),
        plan_db_path=str(tmp_path / "plans.db"),
        skill_db_path=str(tmp_path / "skills.db"),
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost:1/v1", api_key="dummy", model="m"),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[],
        prompt_db_path=str(tmp_path / "prompts.db"),
        tracing=tracing,
    )


def _record(
    span_id: str,
    *,
    parent_span_id: str | None = None,
    run_id: str | None = "run-abc123",
    name: str = semconv.OP_CHAT,
    kind: str = "CLIENT",
    start_ts: float = 1000.0,
    status: str = "unset",
    status_message: str | None = None,
    attributes: dict | None = None,
) -> SpanRecord:
    return SpanRecord(
        trace_id="4" * 32,
        span_id=span_id,
        parent_span_id=parent_span_id,
        run_id=run_id,
        scope="telegram:1",
        kind=kind,
        name=name,
        start_ts=start_ts,
        end_ts=start_ts + 1.0,
        duration_ms=1000.0,
        status=status,
        status_message=status_message,
        attributes_json=json.dumps(attributes or {}),
        events_json="[]",
    )


def _seed_run_tree(store: TraceStore) -> None:
    store.insert_spans(
        [
            _record(
                "a" * 16,
                run_id="run-abc123",
                name=semconv.OP_INVOKE_AGENT,
                kind="INTERNAL",
                start_ts=1000.0,
                attributes={semconv.RUN_ID: "run-abc123", semconv.RUN_DEPTH: 0},
            ),
            _record(
                "b" * 16,
                parent_span_id="a" * 16,
                name=semconv.OP_CHAT,
                start_ts=1001.0,
                attributes={
                    semconv.GEN_AI_REQUEST_MODEL: "stub",
                    semconv.GEN_AI_RESPONSE_MODEL: "stub",
                    semconv.GEN_AI_USAGE_INPUT_TOKENS: 500,
                    semconv.GEN_AI_USAGE_OUTPUT_TOKENS: 30,
                    semconv.GEN_AI_RESPONSE_FINISH_REASONS: ["stop"],
                },
            ),
            _record(
                "c" * 16,
                parent_span_id="a" * 16,
                name=semconv.OP_EXECUTE_TOOL,
                kind="INTERNAL",
                start_ts=1002.0,
                status="error",
                status_message="connection refused",
                attributes={semconv.GEN_AI_TOOL_NAME: "web__search_web"},
            ),
            _record(
                "d" * 16,
                parent_span_id="a" * 16,
                name=semconv.OP_CHAT,
                start_ts=1003.0,
                attributes={
                    semconv.GEN_AI_REQUEST_MODEL: "stub",
                    semconv.GEN_AI_USAGE_INPUT_TOKENS: 700,
                    semconv.GEN_AI_USAGE_OUTPUT_TOKENS: 40,
                    semconv.GEN_AI_RESPONSE_FINISH_REASONS: ["length"],
                },
            ),
        ]
    )


class TestTraceDbPath:
    def test_raises_when_tracing_not_configured(self, tmp_path: Path) -> None:
        config = _build_config(tmp_path, tracing=None)
        with pytest.raises(SystemExit, match="Tracing is not configured"):
            _trace_db_path(config)

    def test_returns_db_path_when_configured(self, tmp_path: Path) -> None:
        config = _build_config(tmp_path)
        assert _trace_db_path(config) == str(tmp_path / "traces.db")


class TestTraceList:
    def test_lists_recent_runs(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        store = TraceStore(str(tmp_path / "traces.db"))
        _seed_run_tree(store)
        _trace_list(_build_config(tmp_path), limit=10)
        out = capsys.readouterr().out
        assert "run-abc123" in out
        assert "4" in out  # span count
        assert "1" in out  # error count

    def test_no_runs_message(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        TraceStore(str(tmp_path / "traces.db"))
        _trace_list(_build_config(tmp_path), limit=10)
        assert "No traced runs found." in capsys.readouterr().out

    def test_latest_trace_run_id(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        assert _latest_trace_run_id(db) is None
        _seed_run_tree(TraceStore(db))
        assert _latest_trace_run_id(db) == "run-abc123"


class TestTraceShow:
    def test_replays_tree_with_tokens_and_anomalies(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        store = TraceStore(str(tmp_path / "traces.db"))
        _seed_run_tree(store)
        _trace_show(_build_config(tmp_path), "run-abc123")
        out = capsys.readouterr().out

        assert "Trace replay for run-abc123" in out
        assert "invoke_agent run_id=run-abc123 depth=0" in out
        assert "├─" in out and "└─" in out
        lines = out.splitlines()
        tree_lines = [ln for ln in lines if "├─" in ln or "└─" in ln]
        assert len(tree_lines) == 3  # two middle children + one last child
        assert all(ln.startswith(("├─ ", "│  ├─ ", "│  └─ ", "└─ ")) for ln in tree_lines)
        assert all(not ln.startswith(("├─ │", "└─ │")) for ln in tree_lines)  # indentation sanity
        chat_lines = [ln for ln in tree_lines if "chat model=stub" in ln]
        assert len(chat_lines) == 2
        assert "chat model=stub in=500 out=30 finish=stop" in out
        assert "execute_tool tool=web__search_web" in out
        assert "ERROR=connection refused" in out

        assert "Token accounting:" in out
        assert "run-abc123: 2 chat call(s), input=1200 output=70 total=1270" in out

        assert "Anomalies:" in out
        assert "length truncations: 1" in out
        assert "error spans: 1" in out

    def test_unknown_run_message(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        TraceStore(str(tmp_path / "traces.db"))
        _trace_show(_build_config(tmp_path), "run-nope")
        assert "No spans found for run_id: run-nope" in capsys.readouterr().out
