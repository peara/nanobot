from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from nanobot.messages import SubagentResultMessage

if TYPE_CHECKING:
    from nanobot.core import BotCore


class SubagentRunner:
    def __init__(self, bot: BotCore) -> None:
        self._bot = bot

    async def run(self, goal: str, parent_scope: str, system_prompt: str) -> SubagentResultMessage:
        run_id = f"subagent-{uuid.uuid4().hex[:10]}"

        self._bot.contexts.put("subagent_run", run_id, "goal", {"text": goal})
        self._bot.contexts.put("subagent_run", run_id, "parent_scope", {"value": parent_scope})
        self._bot.contexts.put("subagent_run", run_id, "status", {"value": "running"})

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": goal},
        ]

        success = True
        error: str | None = None
        reply = ""
        tool_trace: list[dict] = []

        try:
            reply, tool_trace = await self._bot.agent_run.run(
                scope_for_tools=parent_scope,
                messages=messages,
                tools=self._bot._list_openai_tools(),
            )
        except Exception as exc:
            success = False
            error = str(exc)
            self._bot.contexts.put("subagent_run", run_id, "error", {"message": error})
            self._bot.contexts.put("subagent_run", run_id, "status", {"value": "failed"})

        result = SubagentResultMessage(
            run_id=run_id,
            parent_scope=parent_scope,
            success=success,
            summary=reply or (f"Error: {error}" if error else ""),
            tool_trace=tool_trace,
            metadata={"error": error} if error else None,
        )

        self._bot.contexts.put(
            "subagent_run",
            run_id,
            "result",
            {
                "summary": result.summary,
                "tool_trace": result.tool_trace,
                "success": result.success,
            },
        )
        if success:
            self._bot.contexts.put("subagent_run", run_id, "status", {"value": "completed"})

        return result
