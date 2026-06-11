"""Stub for the delegate_task tool. Filled in by file 5 of the plan.

This stub exists so that BotCore.__init__ can import and register the tool
without a circular import or runtime ImportError. The Tool ABC is fully
satisfied (name, description, schema, call) but the call() body is a
placeholder that will be implemented in a follow-up.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nanobot.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.core import BotCore


class DelegateTaskTool(Tool):
    """Spawn a focused subagent with its own goal and skill matching.

    See issue #43. The full implementation is added in the next file; this
    stub satisfies the Tool ABC so the tool can be registered and is visible
    to the orchestrator LLM.
    """

    def __init__(self, core: "BotCore") -> None:
        self._core = core

    @property
    def name(self) -> str:
        return "delegate_task"

    @property
    def description(self) -> str:
        return (
            "Spawn a focused subagent with its own goal and skill matching. "
            "Use when the request mixes multiple distinct methods (e.g. a domain "
            "task AND a reusable procedure), or when an isolated scratchpad would "
            "help. Returns the subagent's reply as a tool result. "
            "Only available to the orchestrator (depth 0); returns an error at "
            "depth >= 1."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Focused goal for the subagent. Should be a narrow "
                    "sub-task with one clear method, not the user's full message.",
                },
            },
            "required": ["goal"],
            "additionalProperties": False,
        }

    async def call(self, args: dict[str, Any]) -> str:
        # Placeholder: the real implementation arrives in the next file.
        return json.dumps({"error": "delegate_task: implementation pending"})
