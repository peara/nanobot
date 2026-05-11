from __future__ import annotations

from typing import Any, cast

import pytest

from nanobot.evaluator.runner import LearningEvaluator
from nanobot.subagents.manager import SubagentRunResult


class _FakePromptStore:
    def render(self, key: str) -> str:
        raise KeyError(key)


class _FakeLlmInvalidQualityJson:
    async def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[Any],
        response_format: dict[str, Any],
    ) -> dict[str, str]:
        del messages, tools, response_format
        return {"content": "not-json"}


@pytest.mark.asyncio
async def test_assess_quality_falls_back_to_default_when_json_invalid() -> None:
    evaluator = LearningEvaluator(
        llm=cast(Any, _FakeLlmInvalidQualityJson()),
        prompts=cast(Any, _FakePromptStore()),
    )
    worker_result = SubagentRunResult(
        run_id="run-1",
        success=True,
        reply="done",
        tool_trace=[],
    )

    quality = await evaluator.assess_quality(
        scope="telegram:1",
        user_request="test",
        worker_result=worker_result,
    )

    assert quality.quality_score == 3
    assert quality.has_learnings is False
    assert quality.confidence == "low"
