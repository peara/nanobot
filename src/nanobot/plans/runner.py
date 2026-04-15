from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from nanobot.core_scratchpad import clear_scratchpad
from nanobot.core_utils import command_body, extract_json_object, looks_garbled_text
from nanobot.plans.models import PlanBrief

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

    intake_messages = [
        bot._base_system_message(),
        {
            "role": "system",
            "content": (
                "You are a planning brief extractor. Output ONLY a JSON object. "
                "Do not use tools. Do not call functions. Do not use scratchpad. "
                "Do not explain. Output only the JSON.\n\n"
                "Extract from the user request:\n"
                "- goal: the main objective (string)\n"
                "- constraints: limitations or requirements (array of strings)\n"
                "- required_inputs: information needed before starting (array of strings)\n"
                "- risk_flags: potential issues or blockers (array of strings)\n"
                "- notes: additional context (string)\n\n"
                "Example 1:\n"
                "User: Remind me to take out trash every Tuesday at 7pm\n"
                "Output: "
                '{"goal": "Set up recurring reminder for trash", '
                '"constraints": ["Tuesday at 7pm"], '
                '"required_inputs": [], '
                '"risk_flags": [], '
                '"notes": "Weekly recurring task"}\n\n'
                "Example 2:\n"
                "User: Book a flight to Tokyo under $800 leaving next week\n"
                "Output: "
                '{"goal": "Book flight to Tokyo", '
                '"constraints": ["budget under $800", "departure next week"], '
                '"required_inputs": ["exact departure date", "return date"], '
                '"risk_flags": ["price may exceed budget", "limited availability"], '
                '"notes": "International travel booking"}'
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

    plan_name = request_text[:80] + ("..." if len(request_text) > 80 else "")
    try:
        saved_plan = bot.plan_store.create_from_brief(
            brief=PlanBrief.from_dict(plan_brief),
            name=plan_name,
            source_type="plan_command",
            source_scope=chat_scope,
        )
        saved_plan_id = saved_plan.id
        logger.info("Saved plan_id=%d from plan run run_id=%s", saved_plan.id, run_id)
        bot.contexts.put("chat", chat_scope, "active_plan_id", {"plan_id": saved_plan_id})
    except Exception:
        logger.exception("Failed to create plan before execution run_id=%s", run_id)
        saved_plan_id = None

    run_payload = {
        "run_id": run_id,
        "request_text": request_text,
        "plan_brief": plan_brief,
        "active_plan_id": saved_plan_id,
    }
    run_messages = [
        {
            "role": "system",
            "content": (
                "You are an execution agent operating in a dedicated plan_run scope. "
                "You have access to plan management tools: plan__get, plan__update, plan__add_step, plan__edit_step. "
                "The active_plan_id is provided in the payload. Use it when calling plan tools. "
                "Use plan__update when you discover new constraints or your approach isn't working. "
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
            tools=bot._list_openai_tools(),
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

        # Update plan notes with the final result and increment stats
        if saved_plan_id is not None:
            try:
                existing_plan = bot.plan_store.get(saved_plan_id)
                current_notes = existing_plan.notes or ""
                new_notes = (current_notes + "\n" + final_reply).strip() if current_notes else final_reply
                bot.plan_store.update(saved_plan_id, notes=new_notes)
                bot.plan_store.increment_stats(saved_plan_id, True)
            except Exception:
                logger.exception("Failed to update plan notes/stats for plan_id=%s", saved_plan_id)

        logger.info("Plan run finished run_id=%s plan_name=%s plan_id=%s", run_id, plan_name, saved_plan_id)
        bot.memory.add_message(chat_scope, "assistant", final_reply)
        bot.contexts.put("chat", chat_scope, "last_assistant_message", {"text": final_reply})
        await bot._send(chat_scope, final_reply)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Plan run failed run_id=%s", run_id)
        bot.contexts.put("plan_run", run_id, "error", {"message": str(exc)})
        bot.contexts.put("plan_run", run_id, "status", {"value": "failed"})
        # If a plan was created before execution, record the failure in the plan
        if saved_plan_id is not None:
            try:
                existing_plan = bot.plan_store.get(saved_plan_id)
                current_notes = existing_plan.notes or ""
                note = f"Plan run failed ({run_id}): {exc}"
                new_notes = (current_notes + "\n" + note).strip() if current_notes else note
                bot.plan_store.update(saved_plan_id, notes=new_notes)
                bot.plan_store.increment_stats(saved_plan_id, False)
            except Exception:
                logger.exception("Failed to annotate plan with failure for plan_id=%s", saved_plan_id)
        await bot._send(chat_scope, f"Plan run failed ({run_id}): {exc}")
