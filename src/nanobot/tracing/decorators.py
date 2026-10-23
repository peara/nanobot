"""Decorator-based span instrumentation — the one place span lifecycle lives.

Call sites declare *what* a span is (name, kind, operation, attributes from
the call arguments) plus optionally how to enrich it from the return value
(``on_result``). This module owns the *how*: start/end, exception recording,
status, run-id context propagation. The four Phase D sites each reduce to a
decorator line plus one extractor (and where only the body knows, a minimal
``get_current_span()`` set — response payloads, mid-function discoveries,
and swallowed-exception tracebacks).

Exception policy (shared by all sites): ``LlmCallCancelledError`` marks the
span cancelled and re-raises; any other exception sets ``gen_ai.error.type``,
status ERROR, records the exception, and re-raises. Functions with
non-raising contracts (``execute``, ``dispatch``) swallow their exceptions
before this policy sees them — those sites derive their status from the
returned result via ``on_result`` instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, TypeVar, cast

from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from nanobot.cancel_token import LlmCallCancelledError
from nanobot.tracing import get_tracer, run_id_context, semconv

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _open_span(
    name: str,
    kind: SpanKind,
    operation: str | None,
    attributes: Callable[..., dict[str, Any]] | None,
    use_run_id_context: bool,
    args: tuple,
    kwargs: dict,
) -> tuple[AbstractContextManager[Any], AbstractContextManager[Span]]:
    span_attributes: dict[str, Any] = {}
    if operation is not None:
        span_attributes[semconv.GEN_AI_OPERATION_NAME] = operation
    if attributes is not None:
        span_attributes.update(attributes(*args, **kwargs))
    run_id = span_attributes.get(semconv.RUN_ID)
    context = run_id_context(str(run_id)) if use_run_id_context and run_id else contextlib.nullcontext()
    # record_exception/set_status_on_exception off: the policy below is the
    # single owner of error semantics — otherwise the CM auto-records every
    # escaping exception (including LlmCallCancelledError) as status=error.
    return context, get_tracer().start_as_current_span(
        name,
        kind=kind,
        attributes=span_attributes,
        record_exception=False,
        set_status_on_exception=False,
    )


def _settle_result(span: Span, result: Any, on_result: Callable[[Span, Any], None] | None) -> Any:
    if on_result is not None:
        on_result(span, result)
    return result


def traced(
    name: str,
    *,
    kind: SpanKind,
    operation: str | None = None,
    attributes: Callable[..., dict[str, Any]] | None = None,
    on_result: Callable[[Span, Any], None] | None = None,
    use_run_id_context: bool = False,
) -> Callable[[F], F]:
    """Decorate a function (sync or async) with a span.

    ``attributes(*args, **kwargs)`` builds call-time attributes; it runs
    where only the arguments are known and may read the run-id contextvar.
    ``on_result(span, result)`` enriches the span from the return value
    before the span ends. ``use_run_id_context`` opens the run-id contextvar
    around the call — only the run-owner span (invoke_agent) needs it; child
    sites (chat/execute_tool/delegate_task) inherit it ambiently.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            context, span_cm = _open_span(name, kind, operation, attributes, use_run_id_context, args, kwargs)
            with context, span_cm as span:
                try:
                    result = await func(*args, **kwargs)
                except LlmCallCancelledError:
                    span.set_attribute(semconv.CANCELLED, True)
                    raise
                except Exception as exc:
                    span.set_attribute(semconv.GEN_AI_ERROR_TYPE, type(exc).__name__)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    raise
                return _settle_result(span, result, on_result)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            context, span_cm = _open_span(name, kind, operation, attributes, use_run_id_context, args, kwargs)
            with context, span_cm as span:
                try:
                    result = func(*args, **kwargs)
                except LlmCallCancelledError:
                    span.set_attribute(semconv.CANCELLED, True)
                    raise
                except Exception as exc:
                    span.set_attribute(semconv.GEN_AI_ERROR_TYPE, type(exc).__name__)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    raise
                return _settle_result(span, result, on_result)

        if asyncio.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    return decorator
