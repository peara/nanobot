from __future__ import annotations

import logging
from typing import Any

import httpx
from openai import AsyncOpenAI

from nanobot.config import ModelConfig

logger = logging.getLogger(__name__)


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
    ) -> dict:
        kwargs: dict[str, Any] = {}
        if response_format is not None:
            kwargs["response_format"] = response_format
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            tools=(tools or None),  # type: ignore[arg-type]
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **kwargs,
        )
        choice = response.choices[0]
        message = choice.message.model_dump()
        message["finish_reason"] = choice.finish_reason
        if choice.finish_reason == "length":
            logger.warning(
                "LLM response truncated (finish_reason=length) model=%s max_tokens=%d",
                self.model,
                self.max_tokens,
            )
        return message
