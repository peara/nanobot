from __future__ import annotations

from openai import AsyncOpenAI

from nanobot.config import ModelConfig


class LlmClient:
    def __init__(self, config: ModelConfig) -> None:
        self.client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.model_dump()
