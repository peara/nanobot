"""OTel tracing runtime for nanobot (ROADMAP Phase D, issue #48).

Manual instrumentation only — opentelemetry-sdk, no auto-instrumentation.
Four span sites (see docs/TRACING.md): invoke_agent, chat, execute_tool,
delegate_task. Spans persist to SQLite via SQLiteSpanExporter so `just trace
<run_id>` works with zero infra; an OTLP exporter later is a config decision,
not a rewrite.

When tracing is not initialized (no `tracing:` config section, or test
fakes), get_tracer() returns no-op spans and every helper below degrades to
a cheap no-op — instrumentation sites never branch on tracing state.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from opentelemetry import trace as trace_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Span

from nanobot.config import TracingConfig
from nanobot.tracing.exporter import SQLiteSpanExporter
from nanobot.tracing.redaction import redact_payload
from nanobot.tracing.store import TraceStore

logger = logging.getLogger(__name__)

_run_id_var: ContextVar[str | None] = ContextVar("nanobot_run_id", default=None)


@dataclass
class TracingRuntime:
    provider: TracerProvider
    tracer: trace_api.Tracer
    store: TraceStore
    capture_payloads: bool


_runtime: TracingRuntime | None = None


def init_tracing(config: TracingConfig, *, on_export_failure: Callable[[str], None] | None = None) -> None:
    """(Re-)initialize tracing. Production calls this once from BotCore;
    re-init shuts down the previous runtime first so tests can swap configs.
    """
    global _runtime
    shutdown_tracing()
    store = TraceStore(config.db_path)
    exporter = SQLiteSpanExporter(store, on_failure=on_export_failure)
    provider = TracerProvider(resource=Resource.create({"service.name": "nanobot"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _runtime = TracingRuntime(
        provider=provider,
        tracer=provider.get_tracer("nanobot"),
        store=store,
        capture_payloads=config.capture_payloads,
    )
    logger.info(
        "OTel tracing initialized db=%s capture_payloads=%s",
        config.db_path,
        config.capture_payloads,
    )


def shutdown_tracing() -> None:
    global _runtime
    if _runtime is None:
        return
    try:
        _runtime.provider.shutdown()
    except Exception:  # pylint: disable=broad-except
        logger.exception("Error shutting down tracing provider")
    _runtime = None


def get_tracer() -> trace_api.Tracer:
    if _runtime is None:
        return trace_api.get_tracer("nanobot")
    return _runtime.tracer


def current_run_id() -> str | None:
    return _run_id_var.get()


@contextmanager
def run_id_context(run_id: str) -> Iterator[None]:
    """Thread run_id into spans opened downstream (LlmClient.chat has no
    run_id parameter — it joins via this contextvar). Token-reset keeps the
    child run's id from leaking back into the parent's context.
    """
    token = _run_id_var.set(run_id)
    try:
        yield
    finally:
        _run_id_var.reset(token)


def payload_capture_enabled() -> bool:
    return _runtime is not None and _runtime.capture_payloads


def set_payload_attributes(span: Span, payloads: dict[str, str]) -> None:
    """Set payload attributes only when tracing.capture_payloads is on.

    nanobot.llm.io keeps full-payload logging duty; spans carry the skeleton
    + metadata by default. When the flag turns on, every payload passes
    through redact_payload here — the single choke point (detect-secrets
    backed; see nanobot.tracing.redaction).
    """
    if not payload_capture_enabled():
        return
    for key, value in payloads.items():
        span.set_attribute(key, redact_payload(value))
