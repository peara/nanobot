from __future__ import annotations

import json
import logging
from typing import Any, Literal

from nanobot.agent_tool_guards import (
    PostResultAction,
    PreCallResult,
    SchemaValidationGuard,
    ToolCallContext,
    ToolGuard,
    WebScriptGuard,
    parse_tool_result_json,
)
from nanobot.cancel_token import CancellationToken, LlmCallCancelledError
from nanobot.core_scratchpad import (
    SCRATCHPAD_TOOL_NAME,
    apply_scratchpad_append_from_content,
    apply_scratchpad_tool_call,
    scratchpad_assistant_message,
    scratchpad_tool_result,
)
from nanobot.core_utils import human_now, tool_result_preview
from nanobot.hooks import ToolCallEvent

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS_PER_TURN = 30
MAX_IDENTICAL_TOOL_CALL_REPEATS = 3
REPEATED_TOOL_CALL_ABORT_REPLY = (
    "I got stuck repeating the same tool call in this turn. "
    "The source may be redirecting or returning unhelpful content. Please try another source or rephrase the request."
)
TOOL_CALL_LIMIT_ABORT_REPLY = (
    "I used too many tool calls in this turn and stopped to avoid looping. Please narrow the request or try again."
)


def _tool_call_limit_finalize_message(host: Any, scope: str, *, run_id: str | None = None) -> dict[str, str] | None:
    scratchpad = host.contexts.get("subagent_run" if run_id else "chat", run_id or scope, "scratchpad") or {}
    goal = str(scratchpad.get("goal", ""))
    context = str(scratchpad.get("context", ""))
    current_step = str(scratchpad.get("current_step", ""))
    known_facts = scratchpad.get("known_facts", [])
    tool_journal = scratchpad.get("tool_journal", [])
    summary_parts: list[str] = []
    if context:
        summary_parts.append(context)
    if current_step:
        summary_parts.append(f"Current step: {current_step}")
    if known_facts:
        facts_text = "\n".join(f"- {f}" for f in known_facts[:15])
        summary_parts.append(f"Key findings:\n{facts_text}")
    if tool_journal:
        journal_text = "\n".join(f"- {j}" for j in tool_journal[:10])
        summary_parts.append(f"Actions taken:\n{journal_text}")
    summary = "\n\n".join(summary_parts) if summary_parts else "No partial results available."
    content = host.prompts.render("tool_call_limit_finalize", goal=goal, summary=summary)
    return {"role": "user", "content": content}


def _finalize_response_message(host: Any, scope: str, *, run_id: str | None = None) -> dict[str, str] | None:
    scratchpad = host.contexts.get("subagent_run" if run_id else "chat", run_id or scope, "scratchpad") or {}
    goal = str(scratchpad.get("goal", ""))
    context = str(scratchpad.get("context", ""))
    current_step = str(scratchpad.get("current_step", ""))
    known_facts = scratchpad.get("known_facts", [])
    tool_journal = scratchpad.get("tool_journal", [])
    summary_parts: list[str] = []
    if context:
        summary_parts.append(context)
    if current_step:
        summary_parts.append(f"Current step: {current_step}")
    if known_facts:
        facts_text = "\n".join(f"- {f}" for f in known_facts[:15])
        summary_parts.append(f"Key findings:\n{facts_text}")
    if tool_journal:
        journal_text = "\n".join(f"- {j}" for j in tool_journal[:10])
        summary_parts.append(f"Actions taken:\n{journal_text}")
    summary = "\n\n".join(summary_parts) if summary_parts else "No summary available."
    content = host.prompts.render("finalize_response", goal=goal, summary=summary)
    return {"role": "user", "content": content}


def _check_scratchpad_violation(
    assistant_message: dict[str, Any],
    pending_scratchpad_round: bool,
    protocol_violation_count: int,
    messages: list[dict[str, Any]],
    scope_for_tools: str,
) -> Literal["no_violation", "nudge", "abort"]:
    """Decide what the loop should do when the previous reply had no tool calls.

    Returns:
      - "no_violation": the reply is a clean text-only stop; caller should
        break to the post-loop return.
      - "nudge": the previous round called session__scratchpad_write
        mode="init" but no external tool or finalize, and the model then
        emitted a text-only stop with non-empty content. A correction
        system message has been appended to messages; caller should fall
        through to the continue call at the bottom of the loop.
      - "abort": the cap (MAX_SCRATCHPAD_PROTOCOL_RETRIES) is exceeded;
        caller should return the SCRATCHPAD_PROTOCOL_ABORT_REPLY.

    This helper has no LLM, no I/O, and no scope formatting — those live
    in the caller's continue call. It only mutates `messages` (appends
    the correction system message) as a side effect when returning
    "nudge".
    """
    if not (pending_scratchpad_round and (assistant_message.get("content") or "").strip()):
        return "no_violation"
    # Lazy import: core.py imports AgentRun at module load, so a
    # top-level import here would form a circular import.
    from nanobot.core import (
        MAX_SCRATCHPAD_PROTOCOL_RETRIES,
        SCRATCHPAD_PROTOCOL_CORRECTION,
    )

    next_count = protocol_violation_count + 1
    if next_count > MAX_SCRATCHPAD_PROTOCOL_RETRIES:
        logger.warning(
            "Scratchpad protocol violation cap exceeded scope=%s retries=%d",
            scope_for_tools,
            next_count,
        )
        return "abort"
    logger.warning(
        "Scratchpad protocol violation (nudging) scope=%s retry=%d",
        scope_for_tools,
        next_count,
    )
    messages.append({"role": "system", "content": SCRATCHPAD_PROTOCOL_CORRECTION})
    return "nudge"


def trim_to_last_tool_round(messages: list[dict]) -> list[dict]:
    """Keep only the last round of tool use (assistant with tool_calls + its tool results)."""
    prefix_end = 0
    for idx, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            prefix_end = idx
            break
    last_assistant_idx: int | None = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "assistant" and messages[idx].get("tool_calls"):
            last_assistant_idx = idx
            break
    if last_assistant_idx is None:
        return list(messages)
    return [*messages[:prefix_end], *messages[last_assistant_idx:]]


def _normalize_roles(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages.

    Some models may produce consecutive user/user or assistant/assistant
    messages during message construction. This normalizes them by merging
    content, keeping the structure semantically correct.
    """
    if not messages:
        return messages
    normalized: list[dict] = [messages[0]]
    for msg in messages[1:]:
        prev = normalized[-1]
        prev_role = str(prev.get("role", ""))
        cur_role = str(msg.get("role", ""))
        if prev_role == cur_role and prev_role not in ("system", "tool"):
            prev_content = prev.get("content") or ""
            cur_content = msg.get("content") or ""
            merged_content = "\n\n".join(part for part in [prev_content, cur_content] if part)
            normalized[-1] = {**prev, "content": merged_content}
        else:
            normalized.append(msg)
    return normalized


def prepare_messages_for_chat(messages: list[dict]) -> list[dict]:
    system_messages: list[dict] = []
    non_system: list[dict] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role == "system":
            content = message.get("content")
            if content is None:
                continue
            text = str(content).strip()
            if text:
                system_messages.append({"role": "system", "content": text})
            continue
        non_system.append(message)
    non_system = _normalize_roles(non_system)
    if not system_messages:
        return non_system
    return [*system_messages, *non_system]


class AgentRun:
    """One LLM + tool loop for a caller-built message list (host provides deps and scratchpad storage)."""

    def __init__(self, host: Any) -> None:
        self._host = host
        host_guards = list(getattr(host, "tool_guards", None) or [])
        guard_classes: set[type] = set()
        guards: list[ToolGuard] = []
        for guard in [*host_guards, WebScriptGuard(), SchemaValidationGuard()]:
            if type(guard) not in guard_classes:
                guard_classes.add(type(guard))
                guards.append(guard)
        self._tool_guards = guards

    def _resolve_tool_schema(self, fn_name: str) -> dict[str, Any] | None:
        tool = self._host.tools.get(fn_name)
        if tool is None:
            return None
        return tool.schema

    def _pre_call_guard(
        self,
        fn_name: str,
        args: dict[str, Any],
        ctx: ToolCallContext,
    ) -> PreCallResult:
        result = PreCallResult(normalized_args=args)
        for guard in self._tool_guards:
            action = guard.pre_call(fn_name, args, ctx)
            if action is None:
                continue
            if action.normalized_args is not None:
                args = action.normalized_args
                result.normalized_args = args
            if action.block:
                return action
        return result

    def _post_result_guard(
        self,
        fn_name: str,
        args: dict[str, Any],
        payload: dict[str, Any] | None,
        ctx: ToolCallContext,
    ) -> PostResultAction | None:
        for guard in self._tool_guards:
            action = guard.post_result(fn_name, args, payload, ctx)
            if action is not None and action.force_finalize:
                return action
        return None

    def _rewrite_finalize_reply(self, reply: str, ctx: ToolCallContext) -> str:
        for guard in self._tool_guards:
            rewritten = guard.rewrite_finalize_reply(reply, ctx)
            if rewritten is not None:
                return rewritten
        return reply

    @staticmethod
    def _tool_call_signature(tool_call: dict[str, Any]) -> tuple[str, str]:
        fn_name = str(tool_call.get("function", {}).get("name", ""))
        raw_args = tool_call.get("function", {}).get("arguments") or "{}"
        try:
            parsed_args = json.loads(raw_args)
        except json.JSONDecodeError:
            normalized_args = raw_args
        else:
            normalized_args = json.dumps(parsed_args, ensure_ascii=True, sort_keys=True)
        return fn_name, str(normalized_args)

    @staticmethod
    def _tools_for_chat(tools: list[dict], *, allow_scratchpad: bool) -> list[dict]:
        if allow_scratchpad:
            return tools
        return [tool for tool in tools if str(tool.get("function", {}).get("name", "")) != SCRATCHPAD_TOOL_NAME]

    async def run(
        self,
        scope_for_tools: str,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
        run_id: str | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        if cancel_token and cancel_token.is_cancelled:
            raise LlmCallCancelledError(scope=scope_for_tools)
        include_scratchpad_prompt = True
        scratchpad_msg = scratchpad_assistant_message(self._host, scope_for_tools, run_id=run_id)
        to_send = messages + ([scratchpad_msg] if scratchpad_msg else [])
        prepared_messages = prepare_messages_for_chat(to_send)
        assistant_message = await self._host.llm.chat(
            messages=prepared_messages,
            tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt),
            response_format=response_format,
            scope=scope_for_tools,
            cancel_token=cancel_token,
        )
        tool_trace: list[dict[str, Any]] = []
        needs_scratchpad_update = False
        total_tool_calls = 0
        scratchpad_tool_calls = 0
        guard_ctx = ToolCallContext(scope=scope_for_tools)
        previous_round_signatures: list[tuple[str, str]] | None = None
        identical_round_repeats = 0
        pending_scratchpad_round = False
        protocol_violation_count = 0

        # Scratchpad-protocol enforcement: if the model called
        # session__scratchpad_write mode="init" but did not follow with
        # an external tool or a finalize, and then emits a text-only
        # stop, nudge it up to MAX_SCRATCHPAD_PROTOCOL_RETRIES times
        # before aborting. We only catch init (not append) because a long
        # append-only sequence followed by a final answer is a legitimate
        # pattern (see test_agent_run_does_not_abort_on_many_scratchpad_tool_calls).
        # See tests/agent_run/test_scratchpad_protocol_regression.py
        # and the helper _check_scratchpad_violation.
        while True:
            if cancel_token and cancel_token.is_cancelled:
                raise LlmCallCancelledError(scope=scope_for_tools)
            if not assistant_message.get("tool_calls"):
                action = _check_scratchpad_violation(
                    assistant_message,
                    pending_scratchpad_round,
                    protocol_violation_count,
                    messages,
                    scope_for_tools,
                )
                if action == "abort":
                    # Lazy import: core.py imports AgentRun at module load.
                    from nanobot.core import SCRATCHPAD_PROTOCOL_ABORT_REPLY

                    return SCRATCHPAD_PROTOCOL_ABORT_REPLY, tool_trace
                if action == "no_violation":
                    break
                # action == "nudge": the helper appended a correction
                # system message; fall through to the continue call at
                # the bottom of the loop.
                protocol_violation_count += 1
                include_scratchpad_prompt = True
            else:
                requested_calls = assistant_message["tool_calls"]
                requested_signatures = [self._tool_call_signature(tool_call) for tool_call in requested_calls]
                if previous_round_signatures == requested_signatures:
                    identical_round_repeats += 1
                else:
                    identical_round_repeats = 1
                previous_round_signatures = requested_signatures
                if identical_round_repeats >= MAX_IDENTICAL_TOOL_CALL_REPEATS:
                    logger.warning(
                        "Aborting repeated identical tool calls scope=%s repeats=%d calls=%s",
                        scope_for_tools,
                        identical_round_repeats,
                        requested_signatures,
                    )
                    return REPEATED_TOOL_CALL_ABORT_REPLY, tool_trace
                include_scratchpad_prompt = needs_scratchpad_update
                if needs_scratchpad_update:
                    scratchpad_seen = False
                    protocol_violation = False
                    for tool_call in requested_calls:
                        name = str(tool_call.get("function", {}).get("name", ""))
                        if name == SCRATCHPAD_TOOL_NAME:
                            scratchpad_seen = True
                            continue
                        if not scratchpad_seen:
                            protocol_violation = True
                            break
                    if protocol_violation:
                        proposed_tools = [str(call.get("function", {}).get("name", "")) for call in requested_calls]
                        logger.warning(
                            "Scratchpad protocol violation (relaxed, not blocking) scope=%s proposed_tools=%s",
                            scope_for_tools,
                            proposed_tools,
                        )
                        raw_content = assistant_message.get("content") or ""
                        if raw_content.strip():
                            try:
                                apply_scratchpad_append_from_content(
                                    self._host, scope_for_tools, raw_content, run_id=run_id
                                )
                                needs_scratchpad_update = False
                            except Exception:  # pylint: disable=broad-except
                                logger.exception(
                                    "Failed to apply synthetic scratchpad from content scope=%s",
                                    scope_for_tools,
                                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_message.get("content") or "",
                        "tool_calls": requested_calls,
                    }
                )
                round_used_external_tool = False
                round_finalized_scratchpad = False
                round_saw_scratchpad_init = False
                post_finalize_reply: str | None = None
                for tool_call in requested_calls:
                    total_tool_calls += 1
                    guard_ctx.total_calls = total_tool_calls
                    if total_tool_calls > MAX_TOOL_CALLS_PER_TURN:
                        logger.warning(
                            "Aborting tool loop after limit scope=%s total_tool_calls=%d",
                            scope_for_tools,
                            total_tool_calls,
                        )
                        finalize_msg = _tool_call_limit_finalize_message(self._host, scope_for_tools, run_id=run_id)
                        trimmed = trim_to_last_tool_round(messages)
                        to_send = trimmed + ([finalize_msg] if finalize_msg else [])
                        prepared_messages = prepare_messages_for_chat(to_send)
                        final_message = await self._host.llm.chat(
                            messages=prepared_messages,
                            tools=[],
                            scope=f"{scope_for_tools}:limit_finalize",
                            cancel_token=cancel_token,
                        )
                        reply = final_message.get("content") or TOOL_CALL_LIMIT_ABORT_REPLY
                        return reply, tool_trace
                    fn_name = tool_call["function"]["name"]
                    guard_ctx.current_tool_name = fn_name
                    raw_args = tool_call["function"].get("arguments") or "{}"
                    args = json.loads(raw_args)
                    if fn_name == SCRATCHPAD_TOOL_NAME:
                        scratchpad_tool_calls += 1
                        guard_ctx.scratchpad_calls = scratchpad_tool_calls
                    if fn_name.endswith("__schedule_task"):
                        chat_id = str(args.get("chat_id", "")).strip()
                        if (
                            not chat_id
                            or ":" not in chat_id
                            or chat_id in {"current_chat", "this_chat", "current", "here"}
                        ):
                            args["chat_id"] = scope_for_tools
                    ok = True
                    error: str | None = None
                    try:
                        guard_ctx.tool_schema = self._resolve_tool_schema(fn_name)
                        pre_action = self._pre_call_guard(fn_name, args, guard_ctx)
                        if pre_action.normalized_args is not None:
                            args = pre_action.normalized_args
                        if pre_action.block:
                            ok = False
                            error = pre_action.block_error
                            result = json.dumps(pre_action.block_payload or {"ok": False}, ensure_ascii=True)
                            logger.warning(
                                "Blocked tool call via guard scope=%s tool=%s error=%s",
                                scope_for_tools,
                                fn_name,
                                error,
                            )
                        elif fn_name == SCRATCHPAD_TOOL_NAME:
                            active = getattr(self._host, "active_requests", None)
                            if isinstance(active, dict) and scope_for_tools in active:
                                active[scope_for_tools].current_step = f"calling {fn_name}"
                            logger.info("Calling tool=%s args=%s", fn_name, args)
                            scratchpad = apply_scratchpad_tool_call(self._host, scope_for_tools, args, run_id=run_id)
                            scratchpad_mode = str(args.get("mode", "")).strip().lower()
                            result = json.dumps(
                                scratchpad_tool_result(scratchpad_mode, scratchpad),
                                ensure_ascii=True,
                            )
                            needs_scratchpad_update = False
                            if scratchpad_mode == "init":
                                round_saw_scratchpad_init = True
                            elif scratchpad_mode == "finalize":
                                round_finalized_scratchpad = True
                            logger.info("Tool succeeded tool=%s", fn_name)
                        else:
                            active = getattr(self._host, "active_requests", None)
                            if isinstance(active, dict) and scope_for_tools in active:
                                active[scope_for_tools].current_step = f"calling {fn_name}"
                            logger.info("Calling tool=%s args=%s", fn_name, args)
                            result = await self._host.tools.call(fn_name, args, scope=scope_for_tools, run_id=run_id)
                            needs_scratchpad_update = True
                            round_used_external_tool = True
                            logger.info("Tool succeeded tool=%s", fn_name)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Tool failed tool=%s", fn_name)
                        ok = False
                        error = str(exc)
                        result = f"Tool call failed: {exc}"
                        if fn_name != SCRATCHPAD_TOOL_NAME:
                            needs_scratchpad_update = True
                    finally:
                        guard_ctx.tool_schema = None
                    result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=True)
                    payload = parse_tool_result_json(result_text)
                    post_action = self._post_result_guard(fn_name, args, payload, guard_ctx)
                    if post_action is not None and post_action.force_finalize:
                        logger.info(
                            "Guard force-finalize suppressed (letting LLM decide) scope=%s tool=%s",
                            scope_for_tools,
                            fn_name,
                        )
                    logger.info(
                        "Tool result tool=%s chars=%d preview=%s",
                        fn_name,
                        len(result_text),
                        tool_result_preview(result_text),
                    )
                    if fn_name != SCRATCHPAD_TOOL_NAME:
                        tz = getattr(getattr(self._host, "config", None), "working_timezone", "UTC")
                        await self._host._dispatch_after_tool_call(
                            ToolCallEvent(
                                scope=scope_for_tools,
                                call_id=str(tool_call.get("id", "")),
                                tool_name=fn_name,
                                args=args,
                                result=result_text,
                                result_preview=tool_result_preview(result_text, limit=1200),
                                ok=ok,
                                error=error,
                                at=human_now(str(tz or "UTC")),
                            )
                        )
                    tool_trace.append(
                        {
                            "name": fn_name,
                            "args": args,
                            "result_preview": tool_result_preview(result_text, limit=300),
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": fn_name,
                            "content": result_text,
                        }
                    )
                pending_scratchpad_round = (
                    round_saw_scratchpad_init and not round_used_external_tool and not round_finalized_scratchpad
                )
                if round_used_external_tool:
                    include_scratchpad_prompt = True
                elif round_finalized_scratchpad:
                    finalize_msg = _finalize_response_message(self._host, scope_for_tools, run_id=run_id)
                    trimmed = trim_to_last_tool_round(messages)
                    to_send = trimmed + ([finalize_msg] if finalize_msg else [])
                    prepared_messages = prepare_messages_for_chat(to_send)
                    final_message = await self._host.llm.chat(
                        messages=prepared_messages,
                        tools=[],
                        scope=f"{scope_for_tools}:finalize",
                        cancel_token=cancel_token,
                    )
                    finish_reason = final_message.get("finish_reason")
                    reply = final_message.get("content") or ""
                    if not reply.strip():
                        logger.warning(
                            "LLM returned empty reply in finalize path scope=%s finish_reason=%s",
                            scope_for_tools,
                            finish_reason,
                        )
                        # Lazy import: core.py imports AgentRun at module load, so a
                        # top-level import here would form a circular import.
                        from nanobot.core import EMPTY_REPLY_FALLBACK

                        reply = EMPTY_REPLY_FALLBACK
                    reply = self._rewrite_finalize_reply(reply, guard_ctx)
                    return reply, tool_trace
                if post_finalize_reply is not None:
                    logger.warning("Guard force-finalize reached (disabled) scope=%s", scope_for_tools)
                trimmed = trim_to_last_tool_round(messages)
                scratchpad_msg = (
                    scratchpad_assistant_message(self._host, scope_for_tools, run_id=run_id)
                    if include_scratchpad_prompt
                    else None
                )
                to_send = trimmed + ([scratchpad_msg] if scratchpad_msg else [])
                prepared_messages = prepare_messages_for_chat(to_send)
            assistant_message = await self._host.llm.chat(
                messages=prepared_messages,
                tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt),
                response_format=response_format,
                scope=f"{scope_for_tools}:continue",
                cancel_token=cancel_token,
            )
        finish_reason = assistant_message.get("finish_reason")
        reply = assistant_message.get("content") or ""
        if not reply.strip():
            logger.warning(
                "LLM returned empty reply in main loop scope=%s finish_reason=%s",
                scope_for_tools,
                finish_reason,
            )
            from nanobot.core import EMPTY_REPLY_FALLBACK

            reply = EMPTY_REPLY_FALLBACK
        return self._rewrite_finalize_reply(reply, guard_ctx), tool_trace
