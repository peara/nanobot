"""Unit tests for the traced() decorator (PR #49 refactor).

The decorator owns span lifecycle; these tests pin its contract so the four
instrumentation sites only need to pin their own attributes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.trace import SpanKind

from nanobot.cancel_token import LlmCallCancelledError
from nanobot.config import TracingConfig
from nanobot.tracing import current_run_id, init_tracing, payload_capture_enabled, semconv
from nanobot.tracing.decorators import traced


def _rows(db_path: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM spans ORDER BY start_ts, span_id").fetchall()]
    finally:
        conn.close()


def _attrs(x: int, *, scope: str = "run-z") -> dict[str, Any]:
    return {"k.x": x, semconv.RUN_ID: scope}


def _on_result(span: Any, result: int) -> None:
    span.set_attribute("k.result", result)


@traced("add", kind=SpanKind.CLIENT, operation="chat", attributes=_attrs, on_result=_on_result, use_run_id_context=True)
async def _add(x: int, *, scope: str = "run-z") -> int:
    assert current_run_id() == scope
    return x + 1


@traced("mul", kind=SpanKind.INTERNAL, operation="execute_tool", attributes=_attrs)
def _mul(x: int, *, scope: str = "run-z") -> int:
    return x * 2


@traced("boom", kind=SpanKind.INTERNAL)
async def _boom() -> None:
    raise RuntimeError("kaboom")


@traced("cancel", kind=SpanKind.INTERNAL)
async def _cancel() -> None:
    raise LlmCallCancelledError(scope="telegram:1")


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_async_span_records_attributes_and_result(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db))
        assert await _add(3) == 4
        rows = _rows(db)
        assert len(rows) == 1
        attrs = json.loads(rows[0]["attributes_json"])
        assert rows[0]["name"] == "add"
        assert rows[0]["kind"] == "CLIENT"
        assert attrs["k.x"] == 3
        assert attrs["k.result"] == 4
        assert attrs[semconv.GEN_AI_OPERATION_NAME] == "chat"
        assert attrs[semconv.RUN_ID] == "run-z"
        assert current_run_id() is None

    def test_sync_span_records_attributes(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db))
        assert _mul(5) == 10
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0]["kind"] == "INTERNAL"
        assert "k.result" not in json.loads(rows[0]["attributes_json"])  # no on_result wired

    def test_wraps_metadata_preserved(self) -> None:
        assert _add.__name__ == "_add"
        assert _mul.__name__ == "_mul"

    @pytest.mark.asyncio
    async def test_run_id_context_only_when_flagged(self, tmp_path: Path) -> None:
        init_tracing(TracingConfig(db_path=str(tmp_path / "traces.db")))
        assert _mul(2) == 4  # no use_run_id_context flag
        assert current_run_id() is None

    @pytest.mark.asyncio
    async def test_nested_spans_form_parent_child_tree(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db))

        @traced("outer", kind=SpanKind.INTERNAL, attributes=lambda: {"k.x": 0, semconv.RUN_ID: "run-z"})
        async def outer() -> None:
            await _add(1)

        await outer()
        rows = _rows(db)
        by_name = {r["name"]: r for r in rows}
        assert set(by_name) == {"outer", "add"}
        assert by_name["add"]["parent_span_id"] == by_name["outer"]["span_id"]
        assert by_name["add"]["trace_id"] == by_name["outer"]["trace_id"]


class TestExceptionPolicy:
    @pytest.mark.asyncio
    async def test_generic_error_marks_span_and_reraises(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db))
        with pytest.raises(RuntimeError, match="kaboom"):
            await _boom()
        rows = _rows(db)
        assert len(rows) == 1
        rec = rows[0]
        assert rec["status"] == "error"
        assert rec["status_message"] == "kaboom"
        attrs = json.loads(rec["attributes_json"])
        assert attrs[semconv.GEN_AI_ERROR_TYPE] == "RuntimeError"
        events = json.loads(rec["events_json"])
        assert len(events) == 1
        assert events[0]["name"] == "exception"
        assert "RuntimeError" in events[0]["attributes"]["exception.type"]

    @pytest.mark.asyncio
    async def test_cancellation_marks_cancelled_not_error(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db))
        with pytest.raises(LlmCallCancelledError):
            await _cancel()
        rows = _rows(db)
        assert len(rows) == 1
        rec = rows[0]
        assert rec["status"] == "unset"
        assert json.loads(rec["attributes_json"])[semconv.CANCELLED] is True
        assert json.loads(rec["events_json"]) == []


class TestTracingOff:
    """Without init_tracing, decorated functions work and record nothing."""

    @pytest.mark.asyncio
    async def test_noop_without_runtime(self, tmp_path: Path) -> None:
        assert await _add(7) == 8
        assert _mul(3) == 6
        with pytest.raises(RuntimeError, match="kaboom"):
            await _boom()
        assert not Path(str(tmp_path / "traces.db")).exists()
        assert current_run_id() is None

    @pytest.mark.asyncio
    async def test_cancellation_error_without_runtime(self) -> None:
        with pytest.raises(LlmCallCancelledError):
            await _cancel()
        assert payload_capture_enabled() is False
