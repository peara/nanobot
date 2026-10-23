from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from openai import AsyncOpenAI
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from nanobot.cancel_token import CancellationToken, LlmCallCancelledError
from nanobot.config import ModelConfig
from nanobot.tracing import current_run_id, get_tracer, semconv, set_payload_attributes

logger = logging.getLogger(__name__)
_io_logger = logging.getLogger("nanobot.llm.io")


def _message_summary(messages: list[dict]) -> tuple[int, int]:
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # multimodal content: list of parts
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text", "")
                    total_chars += len(text) if isinstance(text, str) else 0
    return len(messages), total_chars


def _tool_names(tools: list[dict] | None) -> list[str]:
    if not tools:
        return []
    return [t.get("function", {}).get("name", "?") for t in tools]


class LlmClient:
    def __init__(self, config: ModelConfig) -> None:
        self.client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
        *,
        scope: str | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> dict:
        span_attributes: dict[str, Any] = {
            semconv.GEN_AI_OPERATION_NAME: semconv.OP_CHAT,
            semconv.GEN_AI_SYSTEM: semconv.GEN_AI_SYSTEM_VALUE,
            semconv.GEN_AI_REQUEST_MODEL: self.model,
            semconv.GEN_AI_REQUEST_MAX_TOKENS: self.max_tokens,
            semconv.GEN_AI_REQUEST_TEMPERATURE: self.temperature,
            semconv.SCOPE: scope or "-",
            semconv.LLM_TOOL_COUNT: len(tools or []),
        }
        run_id = current_run_id()
        if run_id:
            span_attributes[semconv.RUN_ID] = run_id
        with get_tracer().start_as_current_span(
            semconv.OP_CHAT, kind=SpanKind.CLIENT, attributes=span_attributes
        ) as span:
            try:
                return await self._chat(span, messages, tools, response_format, scope=scope, cancel_token=cancel_token)
            except LlmCallCancelledError:
                span.set_attribute(semconv.CANCELLED, True)
                raise
            except Exception as exc:
                span.set_attribute(semconv.GEN_AI_ERROR_TYPE, type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise

    async def _chat(
        self,
        span: Span,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None,
        *,
        scope: str | None,
        cancel_token: CancellationToken | None,
    ) -> dict:
        if cancel_token is not None and cancel_token.is_cancelled:
            raise LlmCallCancelledError(scope=scope)

        kwargs: dict[str, Any] = {}
        if response_format is not None:
            kwargs["response_format"] = response_format
        msg_count, msg_chars = _message_summary(messages)
        tool_names = _tool_names(tools)
        span.set_attribute(semconv.LLM_MESSAGE_COUNT, msg_count)
        span.set_attribute(semconv.LLM_MESSAGE_CHARS, msg_chars)
        _io_logger.info(
            "REQUEST scope=%s model=%s msgs=%d chars=%d tools=%s temp=%.2f max_tokens=%d response_format=%s",
            scope or "-",
            self.model,
            msg_count,
            msg_chars,
            tool_names or "(none)",
            self.temperature,
            self.max_tokens,
            "yes" if response_format else "no",
        )
        _io_logger.debug(
            "REQUEST_FULL scope=%s messages=%s",
            scope or "-",
            messages,
        )
        start = time.monotonic()

        if cancel_token is None:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                tools=(tools or None),  # type: ignore[arg-type]
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                **kwargs,
            )
        else:
            llm_task = asyncio.ensure_future(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=(tools or None),  # type: ignore[arg-type]
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    **kwargs,
                )
            )
            cancel_waiter = asyncio.ensure_future(cancel_token.wait())
            done, pending = await asyncio.wait(
                [llm_task, cancel_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in list(pending):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            if cancel_token.is_cancelled:
                raise LlmCallCancelledError(scope=scope)
            response = llm_task.result()

        elapsed = time.monotonic() - start
        choice = response.choices[0]
        message = choice.message.model_dump()
        message["finish_reason"] = choice.finish_reason
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        usage = response.usage
        _io_logger.info(
            "RESPONSE scope=%s finish_reason=%s content_chars=%d tool_calls=%d "
            "prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed=%.2fs",
            scope or "-",
            choice.finish_reason,
            len(content) if isinstance(content, str) else 0,
            len(tool_calls),
            usage.prompt_tokens if usage else "-",
            usage.completion_tokens if usage else "-",
            usage.total_tokens if usage else "-",
            elapsed,
        )
        if choice.finish_reason == "length":
            logger.warning(
                "LLM response truncated (finish_reason=length) model=%s max_tokens=%d",
                self.model,
                self.max_tokens,
            )
        if _io_logger.isEnabledFor(logging.DEBUG):
            _io_logger.debug(
                "RESPONSE_FULL scope=%s response=%s",
                scope or "-",
                message,
            )
        span.set_attribute(semconv.GEN_AI_RESPONSE_FINISH_REASONS, [choice.finish_reason])
        if usage is not None:
            span.set_attribute(semconv.GEN_AI_USAGE_INPUT_TOKENS, usage.prompt_tokens)
            span.set_attribute(semconv.GEN_AI_USAGE_OUTPUT_TOKENS, usage.completion_tokens)
        if getattr(response, "model", None):
            span.set_attribute(semconv.GEN_AI_RESPONSE_MODEL, response.model)
        if not tool_calls and not (isinstance(content, str) and content.strip()):
            span.set_attribute(semconv.LLM_EMPTY_CONTENT, True)
        span.set_attribute(semconv.LLM_ELAPSED_SECONDS, round(elapsed, 3))
        set_payload_attributes(
            span,
            {
                semconv.GEN_AI_PROMPT: json.dumps(messages, ensure_ascii=True),
                semconv.GEN_AI_COMPLETION: str(content),
            },
        )
        return message
