from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
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
from nanobot.subagents.delegate_tool import DELEGATE_TASK_NAME

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


ScratchpadProtocolAction = Literal["no_violation", "nudge", "abort"]


@dataclass
class ScratchpadRoundSummary:
    saw_scratchpad_init: bool
    used_external_tool: bool
    finalized_scratchpad: bool


@dataclass
class ScratchpadProtocolState:
    """Cross-round state for the scratchpad-protocol enforcement loop.

    Tracks:
      - protocol_violation_count: cumulative nudges issued for the same turn
      - pending_scratchpad_round: True if the last completed round called
        scratchpad mode="init" but no external tool and no finalize. The
        next text-only stop with non-empty content is a violation.
      - needs_scratchpad_update: True when an external tool has run since
        the last successful scratchpad append. Drives the "relaxed"
        protocol check on the next round (synthetic append from content).
      - include_scratchpad_prompt: whether the next LLM call should
        include session__scratchpad_write in the tool schema. Mirrors
        needs_scratchpad_update, with explicit re-enable after a nudge.

    Per-round flags (saw_init/used_external/finalized) are accumulated
    via note_* methods and consolidated at commit_round().
    """

    protocol_violation_count: int = 0
    pending_scratchpad_round: bool = False
    needs_scratchpad_update: bool = False
    include_scratchpad_prompt: bool = True
    _round_saw_scratchpad_init: bool = False
    _round_used_external_tool: bool = False
    _round_finalized_scratchpad: bool = False

    def evaluate_text_only(
        self,
        assistant_message: dict[str, Any],
        *,
        scope: str,
    ) -> ScratchpadProtocolAction:
        if not (self.pending_scratchpad_round and (assistant_message.get("content") or "").strip()):
            return "no_violation"
        next_count = self.protocol_violation_count + 1
        if next_count > _core_protocol_constants().MAX_SCRATCHPAD_PROTOCOL_RETRIES:
            logger.warning(
                "Scratchpad protocol violation cap exceeded scope=%s retries=%d",
                scope,
                next_count,
            )
            return "abort"
        logger.warning(
            "Scratchpad protocol violation (nudging) scope=%s retry=%d",
            scope,
            next_count,
        )
        self.protocol_violation_count = next_count
        self.include_scratchpad_prompt = True
        return "nudge"

    def note_scratchpad_init(self) -> None:
        self._round_saw_scratchpad_init = True

    def note_external_tool_used(self) -> None:
        self._round_used_external_tool = True
        self.needs_scratchpad_update = True

    def note_scratchpad_finalize(self) -> None:
        self._round_finalized_scratchpad = True
        self.needs_scratchpad_update = False

    def note_synthetic_append_success(self) -> None:
        self.needs_scratchpad_update = False

    def commit_round(self) -> ScratchpadRoundSummary:
        summary = ScratchpadRoundSummary(
            saw_scratchpad_init=self._round_saw_scratchpad_init,
            used_external_tool=self._round_used_external_tool,
            finalized_scratchpad=self._round_finalized_scratchpad,
        )
        self.pending_scratchpad_round = (
            summary.saw_scratchpad_init and not summary.used_external_tool and not summary.finalized_scratchpad
        )
        self._round_saw_scratchpad_init = False
        self._round_used_external_tool = False
        self._round_finalized_scratchpad = False
        return summary


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
    and for any cross-round protocol_state updates the outcome implies.
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
                # BotCore attribute is read. See issue #43.
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

    async def _finalize_with_tool_call_limit(
        self,
        messages: list[dict],
        tool_trace: list[dict[str, Any]],
        scope: str,
        run_id: str | None,
        cancel_token: CancellationToken | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        finalize_msg = _tool_call_limit_finalize_message(self._host, scope, run_id=run_id)
        trimmed = trim_to_last_tool_round(messages)
        to_send = trimmed + ([finalize_msg] if finalize_msg else [])
        prepared_messages = prepare_messages_for_chat(to_send)
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
        trimmed = trim_to_last_tool_round(messages)
        to_send = trimmed + ([finalize_msg] if finalize_msg else [])
        prepared_messages = prepare_messages_for_chat(to_send)
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
        # pattern), but only the orchestrator should see it. See issue #43.
        # The depth is computed from the run_id local to this LLM call —
        # no shared state is read. If the host lacks _compute_run_depth
        # (test fakes may not), fall back to -1, which is not >= 1, so
        # the strip is a no-op.
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
        include_scratchpad_prompt = True
        scratchpad_msg = scratchpad_assistant_message(self._host, scope_for_tools, run_id=run_id)
        to_send = messages + ([scratchpad_msg] if scratchpad_msg else [])
        prepared_messages = prepare_messages_for_chat(to_send)
        assistant_message = await self._host.llm.chat(
            messages=prepared_messages,
            tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt, run_id=run_id),
            response_format=response_format,
            scope=scope_for_tools,
            cancel_token=cancel_token,
        )
        tool_trace: list[dict[str, Any]] = []
        total_tool_calls = 0
        guard_ctx = ToolCallContext(scope=scope_for_tools)
        previous_round_signatures: list[tuple[str, str]] | None = None
        identical_round_repeats = 0
        include_scratchpad_prompt = True
        protocol_state = ScratchpadProtocolState()

        # Scratchpad-protocol enforcement: if the model called
        # session__scratchpad_write mode="init" but did not follow with
        # an external tool or a finalize, and then emits a text-only
        # stop, nudge it up to MAX_SCRATCHPAD_PROTOCOL_RETRIES times
        # before aborting. We only catch init (not append) because a long
        # append-only sequence followed by a final answer is a legitimate
        # pattern (see test_agent_run_does_not_abort_on_many_scratchpad_tool_calls).
        # See tests/agent_run/test_scratchpad_protocol_regression.py
        # and ScratchpadProtocolState.evaluate_text_only.
        while True:
            if cancel_token and cancel_token.is_cancelled:
                raise LlmCallCancelledError(scope=scope_for_tools)
            if not assistant_message.get("tool_calls"):
                action = protocol_state.evaluate_text_only(assistant_message, scope=scope_for_tools)
                if action == "abort":
                    return _core_protocol_constants().SCRATCHPAD_PROTOCOL_ABORT_REPLY, tool_trace
                if action == "no_violation":
                    break
                # action == "nudge": state mutated (count + include flag);
                # caller must append the correction system message itself.
                messages.append(
                    {"role": "system", "content": _core_protocol_constants().SCRATCHPAD_PROTOCOL_CORRECTION}
                )
                include_scratchpad_prompt = protocol_state.include_scratchpad_prompt
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
                include_scratchpad_prompt = protocol_state.needs_scratchpad_update
                if protocol_state.needs_scratchpad_update:
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
                                protocol_state.note_synthetic_append_success()
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
                post_finalize_reply: str | None = None
                for tool_call in requested_calls:
                    total_tool_calls += 1
                    if total_tool_calls > MAX_TOOL_CALLS_PER_TURN:
                        logger.warning(
                            "Aborting tool loop after limit scope=%s total_tool_calls=%d",
                            scope_for_tools,
                            total_tool_calls,
                        )
                        return await self._finalize_with_tool_call_limit(
                            messages,
                            tool_trace,
                            scope_for_tools,
                            run_id,
                            cancel_token,
                        )
                    outcome = await self._dispatcher.dispatch(
                        tool_call,
                        guard_ctx,
                        scope=scope_for_tools,
                        run_id=run_id,
                        tool_trace=tool_trace,
                        cancel_token=cancel_token,
                    )
                    if outcome.scratchpad_mode:
                        protocol_state.needs_scratchpad_update = False
                        if outcome.scratchpad_mode == "init":
                            protocol_state.note_scratchpad_init()
                        elif outcome.scratchpad_mode == "finalize":
                            protocol_state.note_scratchpad_finalize()
                    elif outcome.ok:
                        protocol_state.note_external_tool_used()
                    else:
                        protocol_state.needs_scratchpad_update = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": outcome.name,
                            "content": outcome.result_text,
                        }
                    )
                round_summary = protocol_state.commit_round()
                if round_summary.used_external_tool:
                    include_scratchpad_prompt = True
                elif round_summary.finalized_scratchpad:
                    return await self._finalize_after_scratchpad(
                        messages,
                        tool_trace,
                        scope_for_tools,
                        run_id,
                        cancel_token,
                        guard_ctx,
                    )
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
                tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt, run_id=run_id),
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
