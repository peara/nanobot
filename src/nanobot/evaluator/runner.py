from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nanobot.evaluator.store import (
    QUALITY_ASSESSMENT_SCHEMA,
    QualityAssessment,
    parse_quality_from_json,
)

if TYPE_CHECKING:
    from nanobot.llm import LlmClient
    from nanobot.prompts import PromptStore
    from nanobot.subagents.manager import SubagentRunResult

logger = logging.getLogger(__name__)


class LearningEvaluator:
    """Three-phase skill lifecycle evaluator.

    Phase 1: Quality Assessment (always runs)
    Phase 2: Learning Extraction (conditional)
    Phase 3: Skill Lifecycle (LLM with tools)
    """

    def __init__(
        self,
        llm: LlmClient,
        prompts: PromptStore,
    ) -> None:
        self._llm = llm
        self._prompts = prompts

    async def evaluate(
        self,
        scope: str,
        user_request: str,
        worker_result: SubagentRunResult,
    ) -> QualityAssessment:
        """Run full evaluation pipeline. Returns quality, may update skills later."""
        quality = await self.assess_quality(scope, user_request, worker_result)
        self._log_quality(scope, quality)
        return quality

    async def assess_quality(
        self,
        scope: str,
        user_request: str,
        worker_result: SubagentRunResult,
    ) -> QualityAssessment:
        """Phase 1: Fast quality assessment with learning gate."""
        system_prompt = self._get_quality_prompt()
        user_message = self._build_quality_input(user_request, worker_result)

        response = await self._llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[],
            response_format=QUALITY_ASSESSMENT_SCHEMA,
        )

        content = response.get("content", "{}")
        if not content:
            return self._default_quality(worker_result)
        return parse_quality_from_json(content)

    def _get_quality_prompt(self) -> str:
        try:
            return self._prompts.render("quality_assessment")
        except KeyError:
            from nanobot.prompts.defaults import QUALITY_ASSESSMENT_PROMPT

            return QUALITY_ASSESSMENT_PROMPT

    def _build_quality_input(
        self,
        user_request: str,
        worker_result: SubagentRunResult,
    ) -> str:
        return f"""User request:
{user_request}

Agent reply:
{worker_result.reply}

Assess the quality and determine if there are learnings worth extracting."""

    def _default_quality(self, worker_result: SubagentRunResult) -> QualityAssessment:
        return QualityAssessment(
            quality_score=3 if worker_result.success else 1,
            quality_reason="No response from evaluator",
            has_learnings=False,
            confidence="low",
        )

    def _log_quality(self, scope: str, quality: QualityAssessment) -> None:
        logger.info(
            "Quality assessment scope=%s score=%d has_learnings=%s confidence=%s",
            scope,
            quality.quality_score,
            quality.has_learnings,
            quality.confidence,
        )
