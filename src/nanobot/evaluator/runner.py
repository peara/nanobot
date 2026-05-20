from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nanobot.evaluator.store import (
    LEARNING_EXTRACTION_SCHEMA,
    QUALITY_ASSESSMENT_SCHEMA,
    SKILL_LIFECYCLE_SCHEMA,
    EvaluationResult,
    LearningExtraction,
    LearningItem,
    QualityAssessment,
    SkillOperation,
    parse_learning_from_json,
    parse_lifecycle_from_json,
    parse_quality_from_json,
)

if TYPE_CHECKING:
    from nanobot.llm import LlmClient
    from nanobot.prompts import PromptStore
    from nanobot.skills.models import Skill
    from nanobot.subagents.manager import SubagentRunResult
    from nanobot.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _build_tool_catalog_text(registry: ToolRegistry) -> str:
    """Build a compact tool catalog text grouped by namespace prefix.

    Returns an empty string if the registry has no tools.
    """
    from collections import defaultdict

    tools = registry.list_tools(patterns=None)
    if not tools:
        return ""

    groups: dict[str, list[str]] = defaultdict(list)
    for tool in tools:
        name = tool.name
        if "__" in name:
            prefix = name.split("__", 1)[0]
        else:
            prefix = name
        groups[prefix].append(name)

    lines = []
    for prefix in sorted(groups):
        tools_str = ", ".join(sorted(groups[prefix]))
        lines.append(f"  - {prefix}: {tools_str}")
    return "\n".join(lines)


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
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._tool_registry = tool_registry
        self._eval_log = logging.getLogger("nanobot.evaluator.io")

    def _log_phase(self, scope: str, phase: str, user_message: str, raw_response: str) -> None:
        self._eval_log.debug(
            "scope=%s phase=%s\n--- INPUT ---\n%s\n--- RESPONSE ---\n%s\n--- END ---",
            scope,
            phase,
            user_message,
            raw_response,
        )

    async def evaluate(
        self,
        scope: str,
        user_request: str,
        worker_result: SubagentRunResult,
        scratchpad: dict[str, Any] | None = None,
        active_skills: list[Skill] | None = None,
    ) -> EvaluationResult:
        """Run full evaluation pipeline. Returns quality assessment and skill decisions."""
        quality = await self.assess_quality(scope, user_request, worker_result, scratchpad)
        self._log_quality(scope, quality)

        if not quality.has_learnings:
            return EvaluationResult(quality=quality)

        extraction = await self.extract_learnings(scope, user_request, worker_result, scratchpad, active_skills)
        self._log_extraction(scope, extraction)

        if not extraction.learnings:
            return EvaluationResult(quality=quality)

        # Filter low-confidence learnings — only high/medium warrant skill operations
        actionable = [item for item in extraction.learnings if item.confidence != "low"]
        if not actionable:
            return EvaluationResult(quality=quality)

        decisions = await self._decide_lifecycle(scope, actionable, active_skills or [])
        self._log_decisions(scope, decisions)

        return EvaluationResult(quality=quality, decisions=decisions)

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
        if response.get("finish_reason") == "length":
            logger.warning("Evaluator quality assessment truncated scope=%s", scope)
            return self._default_quality(worker_result)
        self._log_phase(scope, "quality_assessment", user_message, content)
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
        if response.get("finish_reason") == "length":
            logger.warning("Evaluator learning extraction truncated scope=%s", scope)
            return LearningExtraction(learnings=[])
        self._log_phase(scope, "learning_extraction", user_message, content)
        if not content:
            return LearningExtraction(learnings=[])
        return parse_learning_from_json(content)

    async def _decide_lifecycle(
        self,
        scope: str,
        learnings: list[LearningItem],
        active_skills: list[Skill],
    ) -> list[SkillOperation]:
        """Phase 3: Decide what skill operations to perform from extracted learnings."""
        system_prompt = self._get_prompt("skill_lifecycle", "SKILL_LIFECYCLE_PROMPT")
        user_message = self._build_lifecycle_input(learnings, active_skills, self._tool_registry)

        response = await self._llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[],
            response_format=SKILL_LIFECYCLE_SCHEMA,
        )

        content = response.get("content") or '{"operations": []}'
        if response.get("finish_reason") == "length":
            logger.warning("Evaluator lifecycle decision truncated scope=%s", scope)
            return []
        self._log_phase(scope, "skill_lifecycle", user_message, content)
        if not content:
            return []
        return parse_lifecycle_from_json(content)

    @staticmethod
    def _build_lifecycle_input(
        learnings: list[LearningItem],
        active_skills: list[Skill],
        tool_registry: ToolRegistry | None = None,
    ) -> str:
        parts = [
            "Extracted learnings:",
        ]
        for item in learnings:
            parts.append(f"  - category={item.category} direction={item.direction} confidence={item.confidence}")
            parts.append(f"    observation: {item.observation}")
            parts.append(f"    evidence: {item.evidence}")

        skills_summary = LearningEvaluator._summarize_active_skills(active_skills)
        parts.extend(["", "Existing active skills:", skills_summary])

        if tool_registry is not None:
            catalog = _build_tool_catalog_text(tool_registry)
            if catalog:
                parts.extend(["", "Available tools:", catalog])

        parts.append("")
        parts.append("Decide which learnings warrant skill creation or update. Output operations.")
        return "\n".join(parts)

    def _get_prompt(self, key: str, fallback_name: str) -> str:
        try:
            return self._prompts.render(key)
        except KeyError:
            from nanobot.prompts import defaults

            return getattr(defaults, fallback_name)

    @staticmethod
    def _summarize_tool_trace(tool_trace: list[dict[str, Any]]) -> str:
        """Build a detailed tool trace summary for evaluator context.

        Skips session__scratchpad_write entries since the scratchpad state
        (context, tool_journal, known_facts) is included separately via
        _summarize_scratchpad. Shows full args and longer result previews
        to expose failure/success patterns and site-specific interactions.
        """
        if not tool_trace:
            return ""
        lines: list[str] = []
        for entry in tool_trace:
            name = entry.get("name", "?")
            # Scratchpad writes are redundant — their data is in the scratchpad summary
            if name == "session__scratchpad_write":
                continue
            args = entry.get("args", {})
            preview = entry.get("result_preview", "")
            args_summary = json.dumps(args, ensure_ascii=False)
            result_tag = ""
            if preview:
                result_tag = " -> " + preview[:200].replace("\n", " ")
            lines.append(f"  - {name}({args_summary}){result_tag}")
        return "\n".join(lines)

    @staticmethod
    def _summarize_scratchpad(scratchpad: dict[str, Any]) -> str:
        """Build a detailed scratchpad summary for evaluator context.

        Includes the agent's reasoning context, full tool journal, and full
        known facts — these are critical for detecting within-run learning
        (e.g., approach X failed, then approach Y worked).
        """
        parts: list[str] = []
        for key in ("goal", "context", "current_step", "next_step"):
            val = str(scratchpad.get(key, "")).strip()
            if val:
                parts.append(f"  {key}: {val}")
        known_facts = scratchpad.get("known_facts", [])
        if known_facts:
            parts.append(f"  known_facts ({len(known_facts)} items):")
            for fact in known_facts:
                parts.append(f"    - {fact}")
        tool_journal = scratchpad.get("tool_journal", [])
        if tool_journal:
            parts.append(f"  tool_journal ({len(tool_journal)} items):")
            for entry in tool_journal:
                parts.append(f"    - {entry}")
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

    def _log_decisions(self, scope: str, decisions: list[SkillOperation]) -> None:
        if not decisions:
            return
        for op in decisions:
            logger.info(
                "Skill decision scope=%s action=%s name=%s trigger_mode=%s reason=%s",
                scope,
                op.action,
                op.name,
                op.trigger_mode,
                op.reason[:80],
            )
