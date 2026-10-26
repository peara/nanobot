from __future__ import annotations

import logging
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

from nanobot.tracing.exporter import SQLiteSpanExporter, to_record
from nanobot.tracing.store import TraceStore


def _mint_readable_spans() -> list:
    """End real SDK spans into an in-memory exporter to get ReadableSpan objects."""
    mem = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(mem))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span(
        "parent",
        kind=SpanKind.INTERNAL,
        attributes={"nanobot.run_id": "run-x", "nanobot.scope": "telegram:1", "tuple.attr": ("a", "b")},
    ) as parent:
        with tracer.start_as_current_span("child", kind=SpanKind.CLIENT) as child:
            child.set_attribute("gen_ai.usage.input_tokens", 42)
        parent.set_status(Status(StatusCode.ERROR, "boom"))
        parent.record_exception(RuntimeError("inner failure"))
    provider.shutdown()
    return mem.get_finished_spans()


class TestExportPersistsSpans:
    def test_export_writes_parent_child_tree(self, tmp_path: Path) -> None:
        store = TraceStore(str(tmp_path / "traces.db"))
        exporter = SQLiteSpanExporter(store)
        spans = _mint_readable_spans()
        assert exporter.export(spans) == SpanExportResult.SUCCESS

        parent, child = next(s for s in spans if s.name == "parent"), next(s for s in spans if s.name == "child")
        parent_rec = store.get(format(parent.context.span_id, "016x"))
        child_rec = store.get(format(child.context.span_id, "016x"))
        assert parent_rec is not None and child_rec is not None

        assert len(parent_rec.trace_id) == 32
        assert parent_rec.trace_id == child_rec.trace_id
        assert child_rec.parent_span_id == parent_rec.span_id
        assert parent_rec.parent_span_id is None
        assert parent_rec.run_id == "run-x"
        assert parent_rec.scope == "telegram:1"
        assert parent_rec.kind == "INTERNAL"
        assert child_rec.kind == "CLIENT"
        assert parent_rec.status == "error"
        assert parent_rec.status_message == "boom"
        assert parent_rec.duration_ms >= 0.0

        parent_attrs = parent_rec.attributes()
        assert parent_attrs["tuple.attr"] == ["a", "b"]
        assert child_rec.attributes()["gen_ai.usage.input_tokens"] == 42

        events = parent_rec.events()
        assert events
        assert events[0]["name"] == "exception"
        assert "RuntimeError" in events[0]["attributes"]["exception.type"]

    def test_to_record_converts_tuple_attributes_to_lists(self) -> None:
        spans = _mint_readable_spans()
        child = next(s for s in spans if s.name == "child")
        rec = to_record(child)
        assert rec.attributes()["gen_ai.usage.input_tokens"] == 42


class TestExportFailure:
    def test_failure_returns_failure_result_and_alerts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = TraceStore(str(tmp_path / "traces.db"))
        failures: list[str] = []
        exporter = SQLiteSpanExporter(store, on_failure=failures.append)

        def boom(self: TraceStore, records: object) -> None:
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(TraceStore, "insert_spans", boom)
        with caplog.at_level(logging.ERROR):
            result = exporter.export(_mint_readable_spans())

        assert result == SpanExportResult.FAILURE
        assert failures == ["RuntimeError: disk I/O error"]
        assert any("Span export failed" in r.message for r in caplog.records)

    def test_alert_handler_crash_does_not_mask_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = TraceStore(str(tmp_path / "traces.db"))

        def bad_alert(description: str) -> None:
            raise RuntimeError("alert channel down")

        exporter = SQLiteSpanExporter(store, on_failure=bad_alert)

        def boom(self: TraceStore, records: object) -> None:
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(TraceStore, "insert_spans", boom)
        with caplog.at_level(logging.ERROR):
            result = exporter.export(_mint_readable_spans())
        assert result == SpanExportResult.FAILURE
        assert any("failure handler crashed" in r.message for r in caplog.records)


class TestLifecycleSignatures:
    def test_shutdown_and_force_flush(self, tmp_path: Path) -> None:
        store = TraceStore(str(tmp_path / "traces.db"))
        exporter = SQLiteSpanExporter(store)
        assert exporter.shutdown() is None
        assert exporter.force_flush(timeout_millis=1000) is True
