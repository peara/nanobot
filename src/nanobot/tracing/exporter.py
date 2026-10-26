from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanContext, StatusCode

from nanobot.tracing import semconv
from nanobot.tracing.store import SpanRecord, TraceStore

logger = logging.getLogger(__name__)


def _hex_id(value: int, width: int) -> str:
    return format(value, f"0{width}x")


def _jsonable_attrs(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: (list(value) if isinstance(value, tuple) else value) for key, value in attributes.items()}


def _parent_span_id(span: ReadableSpan) -> str | None:
    parent: SpanContext | None = span.parent
    return _hex_id(parent.span_id, 16) if parent is not None else None


def _status_name(span: ReadableSpan) -> str:
    return span.status.status_code.name.lower() if span.status is not None else StatusCode.UNSET.name.lower()


def to_record(span: ReadableSpan) -> SpanRecord:
    """Convert a finished ReadableSpan to a persistable SpanRecord."""
    attributes = _jsonable_attrs(dict(span.attributes or {}))
    start_ts = (span.start_time or 0) / 1e9
    end_ts = (span.end_time or 0) / 1e9
    events = [
        {
            "name": event.name,
            "attributes": _jsonable_attrs(dict(event.attributes or {})),
            "timestamp": (event.timestamp or 0) / 1e9,
        }
        for event in (span.events or [])
    ]
    return SpanRecord(
        trace_id=_hex_id(span.context.trace_id, 32),
        span_id=_hex_id(span.context.span_id, 16),
        parent_span_id=_parent_span_id(span),
        run_id=attributes.get(semconv.RUN_ID),
        scope=attributes.get(semconv.SCOPE),
        kind=span.kind.name,
        name=span.name,
        start_ts=start_ts,
        end_ts=end_ts,
        duration_ms=max(0.0, (end_ts - start_ts) * 1000.0),
        status=_status_name(span),
        status_message=span.status.description if span.status is not None else None,
        attributes_json=json.dumps(attributes, ensure_ascii=True),
        events_json=json.dumps(events, ensure_ascii=True),
    )


class SQLiteSpanExporter(SpanExporter):
    """Persists finished spans to SQLite via TraceStore.

    Telemetry must not fail silently: any export error is logged with a
    traceback and forwarded to ``on_failure`` (BotCore wires that to a
    one-time owner notification), then reported as FAILURE to the processor.
    """

    def __init__(self, store: TraceStore, *, on_failure: Callable[[str], None] | None = None) -> None:
        self._store = store
        self._on_failure = on_failure

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            self._store.insert_spans([to_record(span) for span in spans])
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Span export failed count=%d db=%s", len(spans), self._store.db_path)
            description = f"{type(exc).__name__}: {exc}"
            if self._on_failure is not None:
                try:
                    self._on_failure(description)
                except Exception:  # pylint: disable=broad-except
                    logger.exception("Span-export failure handler crashed")
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
