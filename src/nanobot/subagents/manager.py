from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nanobot.subagents.store import SubagentRun, SubagentRunStore

if TYPE_CHECKING:
    from nanobot.agent_run import AgentRun
    from nanobot.context_store import ContextStore
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
    ) -> None:
        self._store = SubagentRunStore(db_path)
        self._contexts = contexts
        self._agent_run = agent_run
        self._tools = tools

    def spawn(
        self,
        scope: str,
        parent_run_id: str | None = None,
        goal: str | None = None,
    ) -> SubagentRun:
        """Create a new subagent run record."""
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
        logger.info("Spawned subagent run run_id=%s scope=%s parent=%s", run_id, scope, parent_run_id)
        return run

    async def execute(
        self,
        run: SubagentRun,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> SubagentRunResult:
        """Execute a subagent run and return the result."""
        self._store.set_status(run.id, "running")
        self._contexts.put("subagent_run", run.id, "status", {"value": "running"})
        logger.info("Starting subagent run run_id=%s", run.id)

        success = True
        error: str | None = None
        reply = ""
        tool_trace: list[dict[str, Any]] = []

        try:
            reply, tool_trace = await self._agent_run.run(
                scope_for_tools=run.scope,
                messages=messages,
                tools=tools,
                response_format=response_format,
                run_id=run.id,
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
        """Get a run by ID."""
        return self._store.get(run_id)

    def list_by_scope(
        self,
        scope: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SubagentRun]:
        """List runs for a scope."""
        return self._store.list_by_scope(scope, status=status, limit=limit)
