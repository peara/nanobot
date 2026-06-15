"""delegate_task — orchestrator-level task decomposition.

Lets the orchestrator (depth 0) spawn a focused subagent with its own goal
and skill matching. The child gets a separate context (no inherited user
history), a focused-scope system prompt (subagent_delegated, not the
orchestrator's main), and a depth-1 tool list (delegate_task itself is
stripped from the child's spec at depth >= 1).

The depth-1 child can never spawn its own sub-subagent — that would
require depth 2, which is refused by SubagentManager.spawn. The LLM at
depth >= 1 also never sees delegate_task in its function spec, so this
is a triple-layer defense:
  1. Tool spec strip in agent_run._tools_for_chat (depth >= 1).
  2. SubagentManager.spawn raises ValueError if depth > MAX_SUBAGENT_DEPTH.
  3. Defensive check in run_delegate_task() (depth >= 1 → refuse).

Dispatch model: delegate_task is NOT registered in ToolRegistry (it would
need parent run context that leaf tools don't carry). Instead, it follows
the same pattern as session__scratchpad_write: its spec is prepended to
the LLM tool list by BotCore._list_openai_tools, and the LLM loop
intercepts the tool call in ToolCallDispatcher.dispatch, calling
run_delegate_task() directly with the local scope, run_id, and
cancel_token. No shared state on BotCore is needed.

See issue #43.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from nanobot.cancel_token import CancellationToken
from nanobot.core_utils import human_now

logger = logging.getLogger(__name__)

DELEGATE_TASK_NAME = "delegate_task"

# Error message returned by the defensive depth check. Kept as a constant
# so tests can assert on the exact message.
DEPTH_REFUSED_MESSAGE = "delegate_task: not available at this depth (max=1)"


def delegate_task_spec() -> dict[str, Any]:
    """OpenAI function spec for the delegate_task tool.

    Prepended to the LLM tool list by BotCore._list_openai_tools, mirroring
    how scratchpad_tool_spec() works. The Tool ABC is not used here because
    delegate_task is dispatched as a control-plane operation by the LLM
    loop, not as a leaf tool through ToolRegistry.
    """
    return {
        "type": "function",
        "function": {
            "name": DELEGATE_TASK_NAME,
            "description": (
                "Spawn a focused subagent with its own goal and skill matching. "
                "Use when the request mixes multiple distinct methods (e.g. a domain "
                "task AND a reusable procedure), or when an isolated scratchpad would "
                "help. Returns the subagent's reply as a tool result. "
                "Only available to the orchestrator (depth 0); returns an error at "
                "depth >= 1."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "Focused goal for the subagent. Should be a narrow "
                            "sub-task with one clear method, not the user's full message."
                        ),
                    },
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
        },
    }


async def run_delegate_task(
    core: Any,
    args: dict[str, Any],
    *,
    scope: str,
    run_id: str,
    cancel_token: CancellationToken | None,
) -> str:
    """Execute the delegate_task tool with the parent's run context.

    Called by ToolCallDispatcher.dispatch when the LLM emits a delegate_task
    tool call. The parent context (scope, run_id, cancel_token) is passed
    explicitly from the LLM loop's local variables — no shared state on
    BotCore is read.

    Returns a JSON string suitable for use as a tool result message.
    """
    depth = _depth_of_run(core, run_id)
    if depth >= 1:
        logger.warning("delegate_task called at depth >= 1; refusing")
        return json.dumps({"error": DEPTH_REFUSED_MESSAGE})

    goal = str(args.get("goal", "") or "").strip()
    if not goal:
        return json.dumps({"error": "delegate_task: 'goal' is required and must be non-empty"})

    if run_id is None:
        return json.dumps(
            {
                "error": "delegate_task: no active orchestrator run (parent run_id missing)",
            }
        )

    system_content = core.prompts.render("subagent_delegated")
    time_content = core.prompts.render(
        "subagent_time",
        working_timezone=core.config.working_timezone,
        current_time=human_now(core.config.working_timezone),
    )
    messages = [
        {"role": "system", "content": system_content},
        {"role": "system", "content": time_content},
        {"role": "user", "content": goal},
    ]

    run = core.subagent_manager.spawn(
        scope=scope,
        parent_run_id=run_id,
        goal=goal,
    )
    logger.info(
        "delegate_task spawned child run_id=%s parent_run_id=%s scope=%s",
        run.id,
        run_id,
        scope,
    )

    # The child gets delegate_task in its tool list (it's prepended by
    # _list_openai_tools), but the child's AgentRun._tools_for_chat strips
    # it before the LLM sees it (depth >= 1). So the child can never call
    # delegate_task even if it tried.
    skill_names = core._get_active_skill_names(run.id)
    tools = core._list_openai_tools(skill_names)
    result = await core.subagent_manager.execute(
        run,
        messages,
        tools,
        cancel_token=cancel_token,
    )

    return json.dumps(
        {
            "run_id": result.run_id,
            "reply": result.reply,
            "success": result.success,
            "tool_calls": result.tool_trace,
            "error": result.error,
        },
        ensure_ascii=False,
    )


def _depth_of_run(core: Any, run_id: str | None) -> int:
    """Return the depth of the run with the given id, or -1 if unknown.

    Used by run_delegate_task's defensive check and by agent_run's spec
    strip. Mirrors BotCore._current_run_depth's logic but takes the
    run_id as a parameter rather than reading shared state.
    """
    if run_id is None:
        return -1
    run = core.subagent_manager.get(run_id)
    if run is None:
        return -1
    return core.subagent_manager._compute_depth(run.parent_run_id)
