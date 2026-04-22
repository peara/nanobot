from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nanobot.evaluator.store import (
    LEARNING_EXTRACTION_SCHEMA,
    QUALITY_ASSESSMENT_SCHEMA,
    LearningExtraction,
    QualityAssessment,
    parse_learning_from_json,
    parse_quality_from_json,
)

if TYPE_CHECKING:
    from nanobot.llm import LlmClient
    from nanobot.prompts import PromptStore
    from nanobot.skills.models import Skill
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
        scratchpad: dict[str, Any] | None = None,
        active_skills: list[Skill] | None = None,
    ) -> QualityAssessment:
        """Run full evaluation pipeline. Returns quality, may update skills later."""
        quality = await self.assess_quality(scope, user_request, worker_result, scratchpad)
        self._log_quality(scope, quality)

        if quality.has_learnings:
            extraction = await self.extract_learnings(scope, user_request, worker_result, scratchpad, active_skills)
            self._log_extraction(scope, extraction)
            # Phase 3 (skill lifecycle) will be wired here later

        return quality

    async def assess_quality(
        self,
        scope: str,
        user_request: str,
        worker_result: SubagentRunResult,
        scratchpad: dict[str, Any] | None = None,
    ) -> QualityAssessment:
        """Phase 1: Fast quality assessment with learning gate."""
        system_prompt = self._get_prompt("quality_assessment", "QUALITY_ASSESSMENT_PROMPT")
        user_message = self._build_quality_input(user_request, worker_result, scratchpad)

        response = await self._llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[],
            response_format=QUALITY_ASSESSMENT_SCHEMA,
        )

        content = response.get("content") or "{}"
        if not content or content == "{}":
            return self._default_quality(worker_result)
        return parse_quality_from_json(content)

    async def extract_learnings(
        self,
        scope: str,
        user_request: str,
        worker_result: SubagentRunResult,
        scratchpad: dict[str, Any] | None = None,
        active_skills: list[Skill] | None = None,
    ) -> LearningExtraction:
        """Phase 2: Extract learnings with direction for skill lifecycle."""
        system_prompt = self._get_prompt("learning_extraction", "LEARNING_EXTRACTION_PROMPT")
        user_message = self._build_learning_input(user_request, worker_result, scratchpad, active_skills)

        response = await self._llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[],
            response_format=LEARNING_EXTRACTION_SCHEMA,
        )

        content = response.get("content") or '{"learnings": []}'
        if not content:
            return LearningExtraction(learnings=[])
        return parse_learning_from_json(content)

    def _get_prompt(self, key: str, fallback_name: str) -> str:
        try:
            return self._prompts.render(key)
        except KeyError:
            from nanobot.prompts import defaults

            return getattr(defaults, fallback_name)

    @staticmethod
    def _summarize_tool_trace(tool_trace: list[dict[str, Any]], max_entries: int = 15) -> str:
        """Build a compact tool trace summary with args preview."""
        if not tool_trace:
            return ""
        lines: list[str] = []
        shown = tool_trace[:max_entries]
        for entry in shown:
            name = entry.get("name", "?")
            args = entry.get("args", {})
            preview = entry.get("result_preview", "")
            args_summary = json.dumps(args, ensure_ascii=False)[:120]
            result_tag = ""
            if preview:
                result_tag = " -> " + preview[:60].replace("\n", " ")
            lines.append(f"  - {name}({args_summary}){result_tag}")
        remaining = len(tool_trace) - max_entries
        if remaining > 0:
            lines.append(f"  ... and {remaining} more tool calls")
        return "\n".join(lines)

    @staticmethod
    def _summarize_scratchpad(scratchpad: dict[str, Any]) -> str:
        """Build a compact scratchpad summary."""
        parts: list[str] = []
        for key in ("goal", "current_step", "next_step"):
            val = str(scratchpad.get(key, "")).strip()
            if val:
                parts.append(f"  {key}: {val[:200]}")
        known_facts = scratchpad.get("known_facts", [])
        if known_facts:
            facts = [str(f)[:80] for f in known_facts[:5]]
            parts.append(f"  known_facts: {', '.join(facts)}")
            remaining_facts = len(known_facts) - 5
            if remaining_facts > 0:
                parts[-1] += f" (+{remaining_facts} more)"
        tool_journal = scratchpad.get("tool_journal", [])
        if tool_journal:
            entries = [str(e)[:80] for e in tool_journal[:5]]
            parts.append(f"  tool_journal: {', '.join(entries)}")
            remaining_entries = len(tool_journal) - 5
            if remaining_entries > 0:
                parts[-1] += f" (+{remaining_entries} more)"
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _summarize_active_skills(skills: list[Skill]) -> str:
        """Build a compact active skills summary."""
        if not skills:
            return "  (none)"
        lines: list[str] = []
        for skill in skills:
            desc = skill.description[:80].replace("\n", " ")
            lines.append(f"  - {skill.name}: {desc}")
        return "\n".join(lines)

    def _build_quality_input(
        self,
        user_request: str,
        worker_result: SubagentRunResult,
        scratchpad: dict[str, Any] | None = None,
    ) -> str:
        parts = [
            "User request:",
            user_request,
            "",
            "Agent reply:",
            worker_result.reply,
        ]

        if scratchpad:
            scratchpad_summary = self._summarize_scratchpad(scratchpad)
            if scratchpad_summary:
                parts.extend(["", "Agent scratchpad:", scratchpad_summary])

        if not worker_result.success:
            parts.extend(["", "Run status: FAILED"])

        if worker_result.error:
            parts.extend(["Error:", worker_result.error])

        tool_summary = self._summarize_tool_trace(worker_result.tool_trace)
        if tool_summary:
            parts.extend(["", f"Tool trace ({len(worker_result.tool_trace)} calls):", tool_summary])

        parts.append("")
        parts.append("Assess the quality and determine if there are learnings worth extracting.")
        return "\n".join(parts)

    def _build_learning_input(
        self,
        user_request: str,
        worker_result: SubagentRunResult,
        scratchpad: dict[str, Any] | None = None,
        active_skills: list[Skill] | None = None,
    ) -> str:
        parts = [
            "User request:",
            user_request,
            "",
            "Agent reply:",
            worker_result.reply,
        ]

        if active_skills:
            skills_summary = self._summarize_active_skills(active_skills)
            parts.extend(["", "Existing active skills:", skills_summary])

        if scratchpad:
            scratchpad_summary = self._summarize_scratchpad(scratchpad)
            if scratchpad_summary:
                parts.extend(["", "Agent scratchpad:", scratchpad_summary])

        if not worker_result.success:
            parts.extend(["", "Run status: FAILED"])

        if worker_result.error:
            parts.extend(["Error:", worker_result.error])

        tool_summary = self._summarize_tool_trace(worker_result.tool_trace)
        if tool_summary:
            parts.extend(["", f"Tool trace ({len(worker_result.tool_trace)} calls):", tool_summary])

        parts.append("")
        parts.append(
            "Extract learnings from this interaction. Focus on user preferences, workflow \
patterns, and constraints that would help future interactions."
        )
        return "\n".join(parts)

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

    def _log_extraction(self, scope: str, extraction: LearningExtraction) -> None:
        if not extraction.learnings:
            return
        for item in extraction.learnings:
            logger.info(
                "Learning extracted scope=%s category=%s direction=%s confidence=%s observation=%s",
                scope,
                item.category,
                item.direction,
                item.confidence,
                item.observation[:80],
            )
