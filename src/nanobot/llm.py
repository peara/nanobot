from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from openai import AsyncOpenAI

from nanobot.config import ModelConfig

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
    ) -> dict:
        kwargs: dict[str, Any] = {}
        if response_format is not None:
            kwargs["response_format"] = response_format
        msg_count, msg_chars = _message_summary(messages)
        tool_names = _tool_names(tools)
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
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            tools=(tools or None),  # type: ignore[arg-type]
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )
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
        return message
