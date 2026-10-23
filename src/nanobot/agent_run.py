from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from opentelemetry.trace import Span, SpanKind, Status, StatusCode

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
from nanobot.scratchpad_protocol import ScratchpadMachine
from nanobot.subagents.delegate_tool import DELEGATE_TASK_NAME
from nanobot.tracing import get_tracer, semconv

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


@lru_cache(maxsize=1)
def _core_protocol_constants() -> Any:
    """Lazy access to nanobot.core constants that would create a circular import.

    nanobot.core imports AgentRun at module load, so a top-level
    `from nanobot.core import ...` here would fail. The first call resolves
    and caches the namespace; subsequent calls return the cached object.
    """
    from nanobot import core

    return core


def build_finalize_prompt(
    host: Any,
    scope: str,
    template: Literal["tool_call_limit_finalize", "finalize_response"],
    *,
    empty_summary: str,
    run_id: str | None = None,
) -> dict[str, str] | None:
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
    summary = "\n\n".join(summary_parts) if summary_parts else empty_summary
    content = host.prompts.render(template, goal=goal, summary=summary)
    return {"role": "user", "content": content}


def _tool_call_limit_finalize_message(host: Any, scope: str, *, run_id: str | None = None) -> dict[str, str] | None:
    return build_finalize_prompt(
        host,
        scope,
        "tool_call_limit_finalize",
        empty_summary="No partial results available.",
        run_id=run_id,
    )


def _finalize_response_message(host: Any, scope: str, *, run_id: str | None = None) -> dict[str, str] | None:
    return build_finalize_prompt(
        host,
        scope,
        "finalize_response",
        empty_summary="No summary available.",
        run_id=run_id,
    )


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


def _strict_correction_message() -> dict[str, str]:
    """System message injected after a strict-path violation (nudge)."""
    return {"role": "system", "content": _core_protocol_constants().SCRATCHPAD_STRICT_CORRECTION}


def _assistant_transcript(
    assistant_message: dict[str, Any],
    requested_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Transcript entry for an assistant message that requested tool calls."""
    return {
        "role": "assistant",
        "content": assistant_message.get("content") or "",
        "tool_calls": requested_calls,
    }


def _tool_transcript(tool_call: dict[str, Any], outcome: ToolCallOutcome) -> dict[str, Any]:
    """Transcript entry for one dispatched tool call's result."""
    return {
        "role": "tool",
        "tool_call_id": tool_call["id"],
        "name": outcome.name,
        "content": outcome.result_text,
    }


class _RoundRepeatTracker:
    """Detects a tool-call round repeating identically (loop guard)."""

    def __init__(self, scope: str) -> None:
        self._scope = scope
        self._signatures: list[tuple[str, str]] | None = None
        self._repeats = 0

    def note(self, requested_calls: list[dict[str, Any]]) -> bool:
        """Record one round; True when identical repeats hit the abort cap."""
        signatures = [_tool_call_signature(call) for call in requested_calls]
        self._repeats = self._repeats + 1 if self._signatures == signatures else 1
        self._signatures = signatures
        if self._repeats >= MAX_IDENTICAL_TOOL_CALL_REPEATS:
            logger.warning(
                "Aborting repeated identical tool calls scope=%s repeats=%d calls=%s",
                self._scope,
                self._repeats,
                signatures,
            )
            return True
        return False


class _ToolBudget:
    """Counts tool calls against the per-turn limit."""

    def __init__(self, scope: str) -> None:
        self._scope = scope
        self._spent = 0

    def spend(self) -> bool:
        """Consume one tool call; True when the limit is exceeded."""
        self._spent += 1
        if self._spent > MAX_TOOL_CALLS_PER_TURN:
            logger.warning(
                "Aborting tool loop after limit scope=%s total_tool_calls=%d",
                self._scope,
                self._spent,
            )
            return True
        return False


@dataclass(frozen=True)
class ToolCallOutcome:
    """Result of dispatching a single tool call. Caller appends the tool message."""

    name: str
    args_used: dict[str, Any]
    result_text: str
    payload: dict[str, Any] | None
    ok: bool
    error: str | None
    scratchpad_mode: str  # "" for non-scratchpad calls


class ToolCallDispatcher:
    """Executes one tool call: pre-guard -> call -> post-guard -> hook -> trace.

    Owns the per-call side effects (hook dispatch, trace append, active_requests
    current_step, scratchpad calls, tool calls). Returns a ToolCallOutcome; the
    caller is responsible for appending the tool message to its message list
    and for feeding the outcome to the protocol machine.
    """

    def __init__(self, host: Any) -> None:
        self._host = host
        host_guards = list(getattr(host, "tool_guards", None) or [])
        guard_classes: set[type] = set()
        guards: list[ToolGuard] = []
        for guard in [*host_guards, WebScriptGuard(), SchemaValidationGuard()]:
            if type(guard) not in guard_classes:
                guard_classes.add(type(guard))
                guards.append(guard)
        self._guards = guards

    async def dispatch(
        self,
        tool_call: dict[str, Any],
        guard_ctx: ToolCallContext,
        *,
        scope: str,
        run_id: str | None,
        tool_trace: list[dict[str, Any]],
        cancel_token: CancellationToken | None = None,
    ) -> ToolCallOutcome:
        fn_name = str(tool_call.get("function", {}).get("name", ""))
        span_attributes: dict[str, Any] = {
            semconv.GEN_AI_OPERATION_NAME: semconv.OP_EXECUTE_TOOL,
            semconv.GEN_AI_TOOL_NAME: fn_name,
            semconv.SCOPE: scope,
        }
        if run_id:
            span_attributes[semconv.RUN_ID] = run_id
        with get_tracer().start_as_current_span(
            semconv.OP_EXECUTE_TOOL, kind=SpanKind.INTERNAL, attributes=span_attributes
        ) as span:
            outcome = await self._dispatch_traced(
                span,
                tool_call,
                guard_ctx,
                scope=scope,
                run_id=run_id,
                tool_trace=tool_trace,
                cancel_token=cancel_token,
            )
            span.set_attribute(semconv.TOOL_OK, outcome.ok)
            if not outcome.ok and outcome.error:
                span.set_status(Status(StatusCode.ERROR, outcome.error))
            return outcome

    async def _dispatch_traced(
        self,
        span: Span,
        tool_call: dict[str, Any],
        guard_ctx: ToolCallContext,
        *,
        scope: str,
        run_id: str | None,
        tool_trace: list[dict[str, Any]],
        cancel_token: CancellationToken | None = None,
    ) -> ToolCallOutcome:
        fn_name = str(tool_call.get("function", {}).get("name", ""))
        guard_ctx.current_tool_name = fn_name
        guard_ctx.total_calls += 1
        raw_args = tool_call.get("function", {}).get("arguments") or "{}"
        args: dict[str, Any] = json.loads(raw_args)
        if fn_name == SCRATCHPAD_TOOL_NAME:
            guard_ctx.scratchpad_calls += 1
        if fn_name.endswith("__schedule_task"):
            chat_id = str(args.get("chat_id", "")).strip()
            if not chat_id or ":" not in chat_id or chat_id in {"current_chat", "this_chat", "current", "here"}:
                args["chat_id"] = scope
        ok = True
        error: str | None = None
        result: Any = None
        scratchpad_mode = ""
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
                    scope,
                    fn_name,
                    error,
                )
            elif fn_name == SCRATCHPAD_TOOL_NAME:
                self._note_active_step(scope, fn_name)
                logger.info("Calling tool=%s args=%s", fn_name, args)
                scratchpad = apply_scratchpad_tool_call(self._host, scope, args, run_id=run_id)
                scratchpad_mode = str(args.get("mode", "")).strip().lower()
                result = json.dumps(
                    scratchpad_tool_result(scratchpad_mode, scratchpad),
                    ensure_ascii=True,
                )
                logger.info("Tool succeeded tool=%s", fn_name)
            elif fn_name == DELEGATE_TASK_NAME:
                # Control-plane dispatch: delegate_task is not in the
                # ToolRegistry. Its spec is prepended by _list_openai_tools
                # and intercepted here. We pass scope/run_id/cancel_token
                # explicitly from the LLM loop's local state — no shared
                # BotCore attribute is read.
                # Lazy import: delegate_tool references BotCore under
                # TYPE_CHECKING, and core imports agent_run at module
                # load — top-level import here would trip the linter's
                # import-cycle detector even though no runtime cycle exists.
                from nanobot.subagents.delegate_tool import run_delegate_task

                self._note_active_step(scope, fn_name)
                logger.info("Calling tool=%s args=%s", fn_name, args)
                if run_id is None:
                    result = json.dumps(
                        {
                            "error": "delegate_task: no active run (run_id missing)",
                        }
                    )
                else:
                    result = await run_delegate_task(
                        self._host,
                        args,
                        scope=scope,
                        run_id=run_id,
                        cancel_token=cancel_token,
                    )
                logger.info("Tool succeeded tool=%s", fn_name)
            else:
                self._note_active_step(scope, fn_name)
                logger.info("Calling tool=%s args=%s", fn_name, args)
                result = await self._host.tools.call(fn_name, args, scope=scope, run_id=run_id)
                logger.info("Tool succeeded tool=%s", fn_name)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Tool failed tool=%s", fn_name)
            span.record_exception(exc)
            ok = False
            error = str(exc)
            result = f"Tool call failed: {exc}"
        finally:
            guard_ctx.tool_schema = None
        result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=True)
        payload = parse_tool_result_json(result_text)
        post_action = self._post_result_guard(fn_name, args, payload, guard_ctx)
        if post_action is not None and post_action.force_finalize:
            logger.info(
                "Guard force-finalize suppressed (letting LLM decide) scope=%s tool=%s",
                scope,
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
                    scope=scope,
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
        return ToolCallOutcome(
            name=fn_name,
            args_used=args,
            result_text=result_text,
            payload=payload,
            ok=ok,
            error=error,
            scratchpad_mode=scratchpad_mode,
        )

    def _note_active_step(self, scope: str, fn_name: str) -> None:
        active = getattr(self._host, "active_requests", None)
        if isinstance(active, dict) and scope in active:
            active[scope].current_step = f"calling {fn_name}"

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
        for guard in self._guards:
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
        for guard in self._guards:
            action = guard.post_result(fn_name, args, payload, ctx)
            if action is not None and action.force_finalize:
                return action
        return None


class AgentRun:
    """One LLM + tool loop for a caller-built message list (host provides deps and scratchpad storage)."""

    def __init__(self, host: Any) -> None:
        self._host = host
        self._dispatcher = ToolCallDispatcher(host)

    @property
    def _tool_guards(self) -> list[ToolGuard]:
        return self._dispatcher._guards

    def _rewrite_finalize_reply(self, reply: str, ctx: ToolCallContext) -> str:
        for guard in self._dispatcher._guards:
            rewritten = guard.rewrite_finalize_reply(reply, ctx)
            if rewritten is not None:
                return rewritten
        return reply

    def _build_chat_messages(
        self,
        messages: list[dict],
        *,
        scope: str,
        run_id: str | None,
        trim: bool,
        include_scratchpad: bool,
        extra: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Single message-assembly path for every llm.chat() call in the run:
        trim to the last tool round -> scratchpad user-message injection
        (when the tool is offered) -> optional finalize prompt ->
        prepare_messages_for_chat.

        ``messages`` is the single source of truth; the returned list is
        derived here immediately before the call that consumes it and is
        never carried across loop iterations. ``trim=False`` is used only
        for the initial call, where the caller's full history (which may
        contain earlier turns' tool rounds) must not be truncated.
        """
        parts = trim_to_last_tool_round(messages) if trim else list(messages)
        if include_scratchpad:
            scratchpad_msg = scratchpad_assistant_message(self._host, scope, run_id=run_id)
            if scratchpad_msg:
                parts = [*parts, scratchpad_msg]
        if extra is not None:
            parts = [*parts, extra]
        return prepare_messages_for_chat(parts)

    def _write_synthetic_append(
        self,
        machine: ScratchpadMachine,
        requested_calls: list[dict[str, Any]],
        assistant_message: dict[str, Any],
        *,
        scope: str,
        run_id: str | None,
    ) -> None:
        """Relaxed-path recovery: the batch leads with an external call while
        an append is owed. Non-blocking — the harness writes a synthetic
        append built from the model's own content, and the round proceeds."""
        proposed_tools = [str(call.get("function", {}).get("name", "")) for call in requested_calls]
        logger.warning(
            "Scratchpad protocol violation (relaxed, not blocking) scope=%s proposed_tools=%s",
            scope,
            proposed_tools,
        )
        raw_content = assistant_message.get("content") or ""
        if not raw_content.strip():
            return
        try:
            apply_scratchpad_append_from_content(self._host, scope, raw_content, run_id=run_id)
            machine.on_scratchpad_write()
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Failed to apply synthetic scratchpad from content scope=%s",
                scope,
            )

    async def _finalize_with_tool_call_limit(
        self,
        messages: list[dict],
        tool_trace: list[dict[str, Any]],
        scope: str,
        run_id: str | None,
        cancel_token: CancellationToken | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        finalize_msg = _tool_call_limit_finalize_message(self._host, scope, run_id=run_id)
        prepared_messages = self._build_chat_messages(
            messages,
            scope=scope,
            run_id=run_id,
            trim=True,
            include_scratchpad=False,
            extra=finalize_msg,
        )
        final_message = await self._host.llm.chat(
            messages=prepared_messages,
            tools=[],
            scope=f"{scope}:limit_finalize",
            cancel_token=cancel_token,
        )
        reply = final_message.get("content") or TOOL_CALL_LIMIT_ABORT_REPLY
        return reply, tool_trace

    async def _finalize_after_scratchpad(
        self,
        messages: list[dict],
        tool_trace: list[dict[str, Any]],
        scope: str,
        run_id: str | None,
        cancel_token: CancellationToken | None,
        guard_ctx: ToolCallContext,
    ) -> tuple[str, list[dict[str, Any]]]:
        finalize_msg = _finalize_response_message(self._host, scope, run_id=run_id)
        prepared_messages = self._build_chat_messages(
            messages,
            scope=scope,
            run_id=run_id,
            trim=True,
            include_scratchpad=False,
            extra=finalize_msg,
        )
        final_message = await self._host.llm.chat(
            messages=prepared_messages,
            tools=[],
            scope=f"{scope}:finalize",
            cancel_token=cancel_token,
        )
        finish_reason = final_message.get("finish_reason")
        reply = final_message.get("content") or ""
        if not reply.strip():
            logger.warning(
                "LLM returned empty reply in finalize path scope=%s finish_reason=%s",
                scope,
                finish_reason,
            )
            reply = _core_protocol_constants().EMPTY_REPLY_FALLBACK
        reply = self._rewrite_finalize_reply(reply, guard_ctx)
        return reply, tool_trace

    def _tools_for_chat(
        self,
        tools: list[dict],
        *,
        allow_scratchpad: bool,
        run_id: str | None,
    ) -> list[dict]:
        # Filter out the scratchpad tool if the protocol says to.
        if not allow_scratchpad:
            tools = [t for t in tools if str(t.get("function", {}).get("name", "")) != SCRATCHPAD_TOOL_NAME]
        # Strip delegate_task at depth >= 1 so the depth-1 subagent cannot
        # call it. The spec is prepended by _list_openai_tools (scratchpad
        # pattern), but only the orchestrator should see it. The depth is
        # computed from the run_id local to this LLM call — no shared state
        # is read. If the host lacks _compute_run_depth (test fakes may
        # not), fall back to -1, which is not >= 1, so the strip is a
        # no-op.
        depth = getattr(self._host, "_compute_run_depth", lambda _id: -1)(run_id)
        if depth >= 1:
            tools = [t for t in tools if str(t.get("function", {}).get("name", "")) != "delegate_task"]
        return tools

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
        # ScratchpadMachine (scratchpad_protocol.py — the docs/SCRATCHPAD.md
        # "Mode lifecycle" diagram as code) is the single protocol authority:
        # tool gating, the append-due check, and the nudge/abort decision.
        # Only init arms the strict trigger — a long append-only sequence
        # ending in a final answer is a legitimate pattern.
        machine = ScratchpadMachine(max_corrections=_core_protocol_constants().MAX_SCRATCHPAD_PROTOCOL_RETRIES)
        prepared_messages = self._build_chat_messages(
            messages,
            scope=scope_for_tools,
            run_id=run_id,
            trim=False,
            include_scratchpad=machine.offer_tool,
        )
        assistant_message = await self._host.llm.chat(
            messages=prepared_messages,
            tools=self._tools_for_chat(tools, allow_scratchpad=machine.offer_tool, run_id=run_id),
            response_format=response_format,
            scope=scope_for_tools,
            cancel_token=cancel_token,
        )
        tool_trace: list[dict[str, Any]] = []
        guard_ctx = ToolCallContext(scope=scope_for_tools)
        repeats = _RoundRepeatTracker(scope=scope_for_tools)
        budget = _ToolBudget(scope=scope_for_tools)

        while True:
            if cancel_token and cancel_token.is_cancelled:
                raise LlmCallCancelledError(scope=scope_for_tools)
            if not assistant_message.get("tool_calls"):
                verdict = machine.on_text_only_stop(
                    has_content=bool((assistant_message.get("content") or "").strip()),
                    scope=scope_for_tools,
                )
                if verdict == "abort":
                    return _core_protocol_constants().SCRATCHPAD_PROTOCOL_ABORT_REPLY, tool_trace
                if verdict == "clean_exit":
                    break
                messages.append(_strict_correction_message())
            else:
                calls = assistant_message["tool_calls"]
                if repeats.note(calls):
                    return REPEATED_TOOL_CALL_ABORT_REPLY, tool_trace
                if machine.skipped_append(calls):
                    self._write_synthetic_append(
                        machine, calls, assistant_message, scope=scope_for_tools, run_id=run_id
                    )
                messages.append(_assistant_transcript(assistant_message, calls))
                for call in calls:
                    if budget.spend():
                        return await self._finalize_with_tool_call_limit(
                            messages,
                            tool_trace,
                            scope_for_tools,
                            run_id,
                            cancel_token,
                        )
                    outcome = await self._dispatcher.dispatch(
                        call,
                        guard_ctx,
                        scope=scope_for_tools,
                        run_id=run_id,
                        tool_trace=tool_trace,
                        cancel_token=cancel_token,
                    )
                    machine.on_dispatch(outcome.scratchpad_mode)
                    messages.append(_tool_transcript(call, outcome))
                if machine.finalized:
                    # Finalize is terminal within its round: later calls in
                    # the batch still dispatched above (each tool_call_id
                    # needs its tool message), but the round routes to the
                    # finalize path regardless of what else it contained.
                    return await self._finalize_after_scratchpad(
                        messages,
                        tool_trace,
                        scope_for_tools,
                        run_id,
                        cancel_token,
                        guard_ctx,
                    )
            # prepared_messages is re-derived from `messages` + machine state
            # immediately before the llm.chat() call that consumes it — never
            # carried across loop iterations.
            prepared_messages = self._build_chat_messages(
                messages,
                scope=scope_for_tools,
                run_id=run_id,
                trim=True,
                include_scratchpad=machine.offer_tool,
            )
            assistant_message = await self._host.llm.chat(
                messages=prepared_messages,
                tools=self._tools_for_chat(tools, allow_scratchpad=machine.offer_tool, run_id=run_id),
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
            reply = _core_protocol_constants().EMPTY_REPLY_FALLBACK
        return self._rewrite_finalize_reply(reply, guard_ctx), tool_trace
