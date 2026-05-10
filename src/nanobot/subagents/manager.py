from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nanobot.skills import Skill, SkillMatcher, SkillStore, SkillVectorStore
from nanobot.skills.injection import build_skill_messages
from nanobot.subagents.store import SubagentRun, SubagentRunStore

if TYPE_CHECKING:
    from nanobot.agent_run import AgentRun
    from nanobot.context_store import ContextStore
    from nanobot.prompts import PromptStore
    from nanobot.tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class SubagentRunResult:
    """Result from executing a subagent run."""

    run_id: str
    success: bool
    reply: str
    tool_trace: list[dict[str, Any]]
    error: str | None = None


class SubagentManager:
    """Orchestrates subagent runs with tracking and context storage."""

    def __init__(
        self,
        db_path: str,
        contexts: ContextStore,
        agent_run: AgentRun,
        tools: ToolRegistry,
        skills: SkillStore,
        prompts: PromptStore,
        mem0_store: SkillVectorStore | None = None,
    ) -> None:
        self._store = SubagentRunStore(db_path)
        self._contexts = contexts
        self._agent_run = agent_run
        self._tools = tools
        self._skills = skills
        self._prompts = prompts
        self._skill_matcher = SkillMatcher(skills, mem0_store=mem0_store)

    def spawn(
        self,
        scope: str,
        parent_run_id: str | None = None,
        goal: str | None = None,
    ) -> SubagentRun:
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        run = self._store.create(
            run_id=run_id,
            scope=scope,
            parent_run_id=parent_run_id,
            goal=goal,
        )
        self._contexts.put("subagent_run", run_id, "goal", {"text": goal} if goal else {"text": ""})
        if parent_run_id:
            self._contexts.put("subagent_run", run_id, "parent_run_id", {"value": parent_run_id})
        self._contexts.put("subagent_run", run_id, "status", {"value": "pending"})

        if goal:
            relevant_skills = self._skill_matcher.find_relevant_skills(goal)
            skill_names = [s.name for s in relevant_skills]
            if skill_names:
                self._contexts.put("subagent_run", run_id, "active_skills", {"skills": skill_names})
                logger.info("Matched skills for run_id=%s skills=%s", run_id, skill_names)

        logger.info("Spawned subagent run run_id=%s scope=%s parent=%s", run_id, scope, parent_run_id)
        return run

    async def execute(
        self,
        run: SubagentRun,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
        procedural_intent: str = "default",
    ) -> SubagentRunResult:
        self._store.set_status(run.id, "running")
        self._contexts.put("subagent_run", run.id, "status", {"value": "running"})
        logger.info("Starting subagent run run_id=%s", run.id)

        active_skills_data = self._contexts.get("subagent_run", run.id, "active_skills")
        skill_messages: list[dict] = []
        if active_skills_data and isinstance(active_skills_data, dict):
            skill_names = active_skills_data.get("skills", [])
            if skill_names:
                loaded_skills: list[Skill] = []
                for name in skill_names:
                    skill = self._skills.get_by_name(name)
                    if skill is not None and skill.is_active:
                        loaded_skills.append(skill)
                skill_messages = build_skill_messages(loaded_skills, self._prompts)
                if skill_messages:
                    logger.info("Injecting %d skill messages for run_id=%s", len(skill_messages), run.id)

        # Insert skill messages after only the first system message (static prompt)
        # so they sit between the cacheable static prefix and the dynamic time block.
        if skill_messages and messages:
            insert_at = 1 if str(messages[0].get("role", "")) == "system" else 0
            enhanced_messages = messages[:insert_at] + skill_messages + messages[insert_at:]
        else:
            enhanced_messages = messages

        success = True
        error: str | None = None
        reply = ""
        tool_trace: list[dict[str, Any]] = []

        try:
            reply, tool_trace = await self._agent_run.run(
                scope_for_tools=run.scope,
                messages=enhanced_messages,
                tools=tools,
                response_format=response_format,
                run_id=run.id,
                procedural_intent=procedural_intent,
            )
        except Exception as exc:
            success = False
            error = str(exc)
            self._store.set_status(run.id, "failed", error=error)
            self._contexts.put("subagent_run", run.id, "error", {"message": error})
            self._contexts.put("subagent_run", run.id, "status", {"value": "failed"})
            logger.exception("Subagent run failed run_id=%s", run.id)
            reply = f"Error: {error}"

        result = SubagentRunResult(
            run_id=run.id,
            success=success,
            reply=reply or (f"Error: {error}" if error else ""),
            tool_trace=tool_trace,
            error=error,
        )

        self._contexts.put(
            "subagent_run",
            run.id,
            "result",
            {
                "summary": result.reply,
                "tool_trace": result.tool_trace,
                "success": result.success,
            },
        )
        if success:
            self._store.set_status(run.id, "completed")
            self._contexts.put("subagent_run", run.id, "status", {"value": "completed"})

        logger.info(
            "Subagent run completed run_id=%s success=%s tools=%d",
            run.id,
            success,
            len(tool_trace),
        )
        return result

    def get(self, run_id: str) -> SubagentRun | None:
        return self._store.get(run_id)

    def list_by_scope(
        self,
        scope: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SubagentRun]:
        return self._store.list_by_scope(scope, status=status, limit=limit)
