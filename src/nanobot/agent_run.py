from __future__ import annotations

import json
import logging
from typing import Any

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


def prepare_messages_for_chat(messages: list[dict]) -> list[dict]:
    system_contents: list[str] = []
    non_system: list[dict] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role == "system":
            content = message.get("content")
            if content is None:
                continue
            text = str(content).strip()
            if text:
                system_contents.append(text)
            continue
        non_system.append(message)
    if not system_contents:
        return non_system
    merged_system = {"role": "system", "content": "\n\n".join(system_contents)}
    return [merged_system, *non_system]


class AgentRun:
    """One LLM + tool loop for a caller-built message list (host provides deps and scratchpad storage)."""

    def __init__(self, host: Any) -> None:
        self._host = host

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
    ) -> tuple[str, list[dict[str, Any]]]:
        include_scratchpad_prompt = True
        scratchpad_msg = scratchpad_assistant_message(self._host, scope_for_tools)
        to_send = messages + ([scratchpad_msg] if scratchpad_msg else [])
        prepared_messages = prepare_messages_for_chat(to_send)
        assistant_message = await self._host.llm.chat(
            messages=prepared_messages,
            tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt),
            response_format=response_format,
        )
        tool_trace: list[dict[str, Any]] = []
        needs_scratchpad_update = False
        total_tool_calls = 0
        previous_round_signatures: list[tuple[str, str]] | None = None
        identical_round_repeats = 0
        while assistant_message.get("tool_calls"):
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
                            apply_scratchpad_append_from_content(self._host, scope_for_tools, raw_content)
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
            for tool_call in requested_calls:
                total_tool_calls += 1
                if total_tool_calls > MAX_TOOL_CALLS_PER_TURN:
                    logger.warning(
                        "Aborting tool loop after limit scope=%s total_tool_calls=%d",
                        scope_for_tools,
                        total_tool_calls,
                    )
                    return TOOL_CALL_LIMIT_ABORT_REPLY, tool_trace
                fn_name = tool_call["function"]["name"]
                raw_args = tool_call["function"].get("arguments") or "{}"
                args = json.loads(raw_args)
                if fn_name.endswith("__schedule_task"):
                    chat_id = str(args.get("chat_id", "")).strip()
                    if not chat_id or ":" not in chat_id or chat_id in {"current_chat", "this_chat", "current", "here"}:
                        args["chat_id"] = scope_for_tools
                ok = True
                error: str | None = None
                try:
                    active = getattr(self._host, "active_requests", None)
                    if isinstance(active, dict) and scope_for_tools in active:
                        active[scope_for_tools].current_step = f"calling {fn_name}"
                    logger.info("Calling tool=%s args=%s", fn_name, args)
                    if fn_name == SCRATCHPAD_TOOL_NAME:
                        scratchpad = apply_scratchpad_tool_call(self._host, scope_for_tools, args)
                        scratchpad_mode = str(args.get("mode", "")).strip().lower()
                        result = json.dumps(
                            scratchpad_tool_result(scratchpad_mode, scratchpad),
                            ensure_ascii=True,
                        )
                        needs_scratchpad_update = False
                        if scratchpad_mode == "finalize":
                            round_finalized_scratchpad = True
                    else:
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
                result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=True)
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
            if round_used_external_tool:
                include_scratchpad_prompt = True
            elif round_finalized_scratchpad:
                include_scratchpad_prompt = False
            trimmed = trim_to_last_tool_round(messages)
            scratchpad_msg = (
                scratchpad_assistant_message(self._host, scope_for_tools) if include_scratchpad_prompt else None
            )
            to_send = trimmed + ([scratchpad_msg] if scratchpad_msg else [])
            prepared_messages = prepare_messages_for_chat(to_send)
            assistant_message = await self._host.llm.chat(
                messages=prepared_messages,
                tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt),
                response_format=response_format,
            )
        reply = assistant_message.get("content") or "I could not generate a response."
        return reply, tool_trace
