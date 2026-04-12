from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from nanobot.core_scratchpad import clear_scratchpad
from nanobot.core_utils import command_body, extract_json_object, looks_garbled_text

logger = logging.getLogger(__name__)


async def process_plan(bot: Any, chat_scope: str, raw_text: str) -> None:
    request_text = command_body(raw_text)
    if not request_text:
        await bot._send(chat_scope, "Usage: /plan <request>")
        return
    clear_scratchpad(bot, chat_scope)

    run_id = f"run-{uuid.uuid4().hex[:10]}"
    logger.info("Starting plan run run_id=%s chat_scope=%s", run_id, chat_scope)
    bot.memory.add_message(chat_scope, "user", raw_text)
    bot.contexts.put("chat", chat_scope, "last_plan_run_id", {"run_id": run_id})
    bot.contexts.put("plan_run", run_id, "chat_scope", {"value": chat_scope})
    bot.contexts.put("plan_run", run_id, "request_text", {"text": request_text})
    bot.contexts.put("plan_run", run_id, "status", {"value": "created"})

    # Pass 1: extract plan brief in chat-facing intake mode.
    intake_messages = [
        bot._base_system_message(),
        {
            "role": "system",
            "content": (
                "Extract a concise planning brief as strict JSON object only. "
                "Include keys: goal (string), constraints (array of strings), "
                "required_inputs (array of strings), risk_flags (array of strings), "
                "notes (string)."
            ),
        },
        {"role": "user", "content": request_text},
    ]
    intake_reply, _ = await bot.agent_run.run(scope_for_tools=chat_scope, messages=intake_messages, tools=[])
    bot.contexts.put("plan_run", run_id, "intake_raw", {"text": intake_reply})
    plan_brief = extract_json_object(intake_reply) or {
        "goal": request_text,
        "constraints": [],
        "required_inputs": [],
        "risk_flags": [],
        "notes": "" if looks_garbled_text(intake_reply) else intake_reply.strip(),
    }
    bot.contexts.put("plan_run", run_id, "plan_brief", plan_brief)
    bot.contexts.put("plan_run", run_id, "status", {"value": "planning"})

    # Pass 2: run execution mode using only plan-run context payload.
    run_payload = {
        "run_id": run_id,
        "request_text": request_text,
        "plan_brief": plan_brief,
    }
    run_messages = [
        {
            "role": "system",
            "content": (
                "You are an execution agent operating in a dedicated plan_run scope. "
                "Use only the provided run payload as context, execute the task, and provide "
                "a practical final answer. If important inputs are missing, clearly ask for them."
            ),
        },
        {"role": "system", "content": json.dumps(run_payload, ensure_ascii=True)},
        {"role": "user", "content": "Execute this plan request and return the final result."},
    ]
    bot.contexts.put("plan_run", run_id, "status", {"value": "running"})
    try:
        final_reply, tool_trace = await bot.agent_run.run(
            scope_for_tools=chat_scope,
            messages=run_messages,
            tools=bot.tools.list_openai_specs(),
        )
        bot.contexts.put("plan_run", run_id, "execution_raw", {"text": final_reply})
        if looks_garbled_text(final_reply):
            logger.warning("Detected garbled /plan output run_id=%s, attempting recovery pass", run_id)
            recovery_payload = {
                "request_text": request_text,
                "plan_brief": plan_brief,
                "tool_trace_preview": tool_trace[:8],
            }
            recovery_messages = [
                bot._base_system_message(),
                {
                    "role": "system",
                    "content": (
                        "Rewrite a clear, concise plain-text answer in English. "
                        "Do not output long runs of '?' characters. "
                        "If data is incomplete, state what is missing."
                    ),
                },
                {"role": "user", "content": json.dumps(recovery_payload, ensure_ascii=True)},
            ]
            recovered_reply, _ = await bot.agent_run.run(
                scope_for_tools=chat_scope,
                messages=recovery_messages,
                tools=[],
            )
            bot.contexts.put("plan_run", run_id, "recovery_raw", {"text": recovered_reply})
            if looks_garbled_text(recovered_reply):
                final_reply = (
                    "I could not produce a readable plan result for this request. "
                    "Please retry with a more specific query."
                )
            else:
                final_reply = recovered_reply
        bot.contexts.put("plan_run", run_id, "tool_trace", tool_trace)
        bot.contexts.put("plan_run", run_id, "result", {"text": final_reply})
        bot.contexts.put("plan_run", run_id, "status", {"value": "completed"})
        bot.memory.add_message(chat_scope, "assistant", final_reply)
        bot.contexts.put("chat", chat_scope, "last_assistant_message", {"text": final_reply})
        await bot._send(chat_scope, final_reply)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Plan run failed run_id=%s", run_id)
        bot.contexts.put("plan_run", run_id, "error", {"message": str(exc)})
        bot.contexts.put("plan_run", run_id, "status", {"value": "failed"})
        await bot._send(chat_scope, f"Plan run failed ({run_id}): {exc}")
