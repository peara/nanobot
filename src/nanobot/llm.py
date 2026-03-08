from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from nanobot.config import ModelConfig


class LlmClient:
    def __init__(self, config: ModelConfig) -> None:
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
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
        return response.choices[0].message.model_dump()  # type: ignore[no-any-return]
