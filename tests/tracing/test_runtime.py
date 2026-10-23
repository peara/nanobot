from __future__ import annotations

from pathlib import Path

from nanobot.config import TracingConfig
from nanobot.tracing import (
    current_run_id,
    get_tracer,
    init_tracing,
    payload_capture_enabled,
    redact_payload,
    run_id_context,
    semconv,
    set_payload_attributes,
    shutdown_tracing,
)
from nanobot.tracing.store import TraceStore


def _span_id(span: object) -> str:
    return format(span.get_span_context().span_id, "016x")  # type: ignore[attr-defined]


class TestRuntimeLifecycle:
    def test_uninitialized_tracer_is_noop(self) -> None:
        with get_tracer().start_as_current_span("noop") as span:
            assert not span.is_recording()

    def test_init_makes_tracer_recording_and_persists(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db))
        assert payload_capture_enabled() is False
        with get_tracer().start_as_current_span("live") as span:
            assert span.is_recording()
            span_id = _span_id(span)
        assert TraceStore(db).get(span_id) is not None

    def test_shutdown_returns_tracer_to_noop(self, tmp_path: Path) -> None:
        init_tracing(TracingConfig(db_path=str(tmp_path / "traces.db")))
        shutdown_tracing()
        with get_tracer().start_as_current_span("noop") as span:
            assert not span.is_recording()

    def test_reinit_swaps_store(self, tmp_path: Path) -> None:
        db_a = str(tmp_path / "a.db")
        db_b = str(tmp_path / "b.db")
        init_tracing(TracingConfig(db_path=db_a))
        with get_tracer().start_as_current_span("in-a") as span_a:
            id_a = _span_id(span_a)
        init_tracing(TracingConfig(db_path=db_b))
        with get_tracer().start_as_current_span("in-b") as span_b:
            id_b = _span_id(span_b)
        assert TraceStore(db_a).get(id_a) is not None
        assert TraceStore(db_a).get(id_b) is None
        assert TraceStore(db_b).get(id_b) is not None


class TestRunIdContext:
    def test_context_sets_and_resets(self) -> None:
        assert current_run_id() is None
        with run_id_context("run-parent"):
            assert current_run_id() == "run-parent"
            with run_id_context("run-child"):
                assert current_run_id() == "run-child"
            assert current_run_id() == "run-parent"
        assert current_run_id() is None

    def test_context_resets_on_exception(self) -> None:
        try:
            with run_id_context("run-x"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert current_run_id() is None


class TestPayloadGate:
    def test_capture_off_by_default(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db))
        with get_tracer().start_as_current_span("chat") as span:
            set_payload_attributes(span, {semconv.GEN_AI_PROMPT: "hello"})
            span_id = _span_id(span)
        rec = TraceStore(db).get(span_id)
        assert rec is not None
        assert semconv.GEN_AI_PROMPT not in rec.attributes()

    def test_capture_on_sets_redacted_and_clipped(self, tmp_path: Path) -> None:
        db = str(tmp_path / "traces.db")
        init_tracing(TracingConfig(db_path=db, capture_payloads=True))
        assert payload_capture_enabled() is True
        with get_tracer().start_as_current_span("chat") as span:
            set_payload_attributes(
                span,
                {
                    semconv.GEN_AI_PROMPT: "api_key=supersecret123 and sk-abcdefgh12345678",
                    semconv.GEN_AI_COMPLETION: "x" * 6000,
                },
            )
            span_id = _span_id(span)
        rec = TraceStore(db).get(span_id)
        assert rec is not None
        attrs = rec.attributes()
        assert attrs[semconv.GEN_AI_PROMPT] == "[REDACTED] and [REDACTED]"
        assert len(attrs[semconv.GEN_AI_COMPLETION]) < 4100
        assert "[clipped" in attrs[semconv.GEN_AI_COMPLETION]

    def test_redact_payload_masks_secret_shapes(self) -> None:
        masked = redact_payload("token sk-abcdefgh12345678 here, Bearer abcdefghijk, password=hunter42")
        assert "sk-abcdefgh12345678" not in masked
        assert "Bearer abcdefghijk" not in masked
        assert "hunter42" not in masked

    def test_redact_payload_clips_long_text(self) -> None:
        clipped = redact_payload("y" * 10_000)
        assert len(clipped) < 4200
        assert "[clipped" in clipped
