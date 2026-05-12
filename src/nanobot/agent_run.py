from __future__ import annotations

import json
import logging
import re
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
MAX_SCRATCHPAD_TOOL_CALLS_PER_TURN = 8
REPEATED_TOOL_CALL_ABORT_REPLY = (
    "I got stuck repeating the same tool call in this turn. "
    "The source may be redirecting or returning unhelpful content. Please try another source or rephrase the request."
)
TOOL_CALL_LIMIT_ABORT_REPLY = (
    "I used too many tool calls in this turn and stopped to avoid looping. Please narrow the request or try again."
)

_SCRIPT_SAVED_PATTERNS = (
    r"\bscript saved\b",
    r"\bsaved as\b",
    r"\buse\s+[a-z0-9_-]+\b",
)
_JAVASCRIPT_SCRIPT_MARKERS = ("const ", "let ", "=>", "document.queryselector", "?.", "array.from(")
_DATA_LOSS_REPLY_PATTERNS = (
    "didn't survive",
    "did not survive",
    "need to re-run",
    "need to rerun",
    "would need to re-run",
    "would need to rerun",
)
_BLOCKED_WHEN_ITEMS_EXIST = (
    "would you like me to fetch",
    "i don't have the actual",
    "no actual web work done yet",
)


def _parse_tool_result_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _contains_script_saved_claim(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in _SCRIPT_SAVED_PATTERNS)


def _normalize_memory_user_id(args: dict[str, Any], scope_for_tools: str) -> None:
    if not str(args.get("user_id", "")).strip():
        args["user_id"] = scope_for_tools
        return
    user_id = str(args.get("user_id", "")).strip()
    if ":" in user_id:
        return
    if ":" not in scope_for_tools:
        return
    channel = scope_for_tools.split(":", 1)[0]
    args["user_id"] = f"{channel}:{user_id}"


def _looks_like_javascript_script(code: str) -> bool:
    lowered = code.lower()
    return any(marker in lowered for marker in _JAVASCRIPT_SCRIPT_MARKERS)


def _has_usable_web_data(tool_name: str, payload: dict[str, Any]) -> bool:
    if tool_name not in {"web__read_page", "web__invoke_script"}:
        return False
    if payload.get("ok") is not True:
        return False
    if tool_name == "web__invoke_script":
        data = payload.get("data")
        if not isinstance(data, dict):
            return False
        items = data.get("items")
        if isinstance(items, list) and items:
            return True
        return bool(str(data.get("content", "")).strip())
    items = payload.get("items")
    if isinstance(items, list) and items:
        return True
    return bool(str(payload.get("content", "")).strip())


def _reply_claims_data_lost(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in (*_DATA_LOSS_REPLY_PATTERNS, *_BLOCKED_WHEN_ITEMS_EXIST))


def _rewrite_data_loss_reply(text: str) -> str:
    if not _reply_claims_data_lost(text):
        return text
    return (
        "I already fetched and parsed the page in this turn. "
        "Here are the extracted results now, and the reusable script status is reported separately."
    )


def _extract_web_items(tool_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if tool_name == "web__invoke_script":
        data = payload.get("data")
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _synthesize_web_data_reply(items: list[dict[str, Any]], fallback: str) -> str:
    if not items:
        return _rewrite_data_loss_reply(fallback)
    lines = ["I already extracted the results in this turn. Here are the top stories:"]
    for idx, item in enumerate(items[:10], start=1):
        title = str(item.get("title", "")).strip() or "Untitled"
        url = str(item.get("url", "")).strip()
        if url:
            lines.append(f"{idx}. {title} — {url}")
        else:
            lines.append(f"{idx}. {title}")
    return "\n".join(lines)


def _looks_like_successful_script_create(tool_name: str, payload: dict[str, Any]) -> bool:
    return tool_name == "web__create_script" and payload.get("ok") is True


def _tool_call_limit_finalize_message(host: Any, scope: str) -> dict[str, str] | None:
    scratchpad = host.contexts.get("chat", scope, "scratchpad") or {}
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


def _finalize_response_message(host: Any, scope: str) -> dict[str, str] | None:
    scratchpad = host.contexts.get("chat", scope, "scratchpad") or {}
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
        scratchpad_tool_calls = 0
        last_create_script_ok: bool | None = None
        had_usable_web_data = False
        latest_web_items: list[dict[str, Any]] = []
        latest_script_status: str | None = None
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
            force_finalize_after_round = False
            for tool_call in requested_calls:
                total_tool_calls += 1
                if total_tool_calls > MAX_TOOL_CALLS_PER_TURN:
                    logger.warning(
                        "Aborting tool loop after limit scope=%s total_tool_calls=%d",
                        scope_for_tools,
                        total_tool_calls,
                    )
                    finalize_msg = _tool_call_limit_finalize_message(self._host, scope_for_tools)
                    trimmed = trim_to_last_tool_round(messages)
                    to_send = trimmed + ([finalize_msg] if finalize_msg else [])
                    prepared_messages = prepare_messages_for_chat(to_send)
                    final_message = await self._host.llm.chat(
                        messages=prepared_messages,
                        tools=[],
                    )
                    reply = final_message.get("content") or TOOL_CALL_LIMIT_ABORT_REPLY
                    return reply, tool_trace
                fn_name = tool_call["function"]["name"]
                raw_args = tool_call["function"].get("arguments") or "{}"
                args = json.loads(raw_args)
                if fn_name == SCRATCHPAD_TOOL_NAME:
                    scratchpad_tool_calls += 1
                    if scratchpad_tool_calls > MAX_SCRATCHPAD_TOOL_CALLS_PER_TURN:
                        logger.warning(
                            "Aborting tool loop after scratchpad limit scope=%s scratchpad_calls=%d",
                            scope_for_tools,
                            scratchpad_tool_calls,
                        )
                        finalize_msg = _tool_call_limit_finalize_message(self._host, scope_for_tools)
                        trimmed = trim_to_last_tool_round(messages)
                        to_send = trimmed + ([finalize_msg] if finalize_msg else [])
                        prepared_messages = prepare_messages_for_chat(to_send)
                        final_message = await self._host.llm.chat(
                            messages=prepared_messages,
                            tools=[],
                        )
                        reply = final_message.get("content") or TOOL_CALL_LIMIT_ABORT_REPLY
                        return reply, tool_trace
                if fn_name.endswith("__schedule_task"):
                    chat_id = str(args.get("chat_id", "")).strip()
                    if not chat_id or ":" not in chat_id or chat_id in {"current_chat", "this_chat", "current", "here"}:
                        args["chat_id"] = scope_for_tools
                if fn_name == "memory__save":
                    _normalize_memory_user_id(args, scope_for_tools)
                ok = True
                error: str | None = None
                try:
                    if fn_name == "web__create_script":
                        script_code = str(args.get("code", ""))
                        if _looks_like_javascript_script(script_code):
                            ok = False
                            error = "invalid_script_language"
                            result = json.dumps(
                                {
                                    "ok": False,
                                    "error": "invalid_script",
                                    "message": (
                                        "web__create_script expects Python NanoScript only. "
                                        "JavaScript syntax detected. Use: async def script(page, params) -> dict."
                                    ),
                                },
                                ensure_ascii=True,
                            )
                            logger.warning(
                                "Blocked web__create_script with JavaScript-like code scope=%s",
                                scope_for_tools,
                            )
                        else:
                            active = getattr(self._host, "active_requests", None)
                            if isinstance(active, dict) and scope_for_tools in active:
                                active[scope_for_tools].current_step = f"calling {fn_name}"
                            logger.info("Calling tool=%s args=%s", fn_name, args)
                            result = await self._host.tools.call(fn_name, args, scope=scope_for_tools, run_id=run_id)
                            needs_scratchpad_update = True
                            round_used_external_tool = True
                            logger.info("Tool succeeded tool=%s", fn_name)
                    elif fn_name == "memory__save" and last_create_script_ok is False:
                        text = str(args.get("text", ""))
                        if _contains_script_saved_claim(text):
                            ok = False
                            error = "blocked_false_memory"
                            result = json.dumps(
                                {
                                    "ok": False,
                                    "error": "blocked_false_memory",
                                    "message": (
                                        "Blocked memory__save: previous web__create_script failed, "
                                        "so script-saved claims are not allowed."
                                    ),
                                },
                                ensure_ascii=True,
                            )
                            logger.warning(
                                "Blocked contradictory memory__save after failed web__create_script scope=%s",
                                scope_for_tools,
                            )
                        else:
                            result = await self._host.tools.call(fn_name, args, scope=scope_for_tools, run_id=run_id)
                            needs_scratchpad_update = True
                            round_used_external_tool = True
                            logger.info("Calling tool=%s args=%s", fn_name, args)
                            logger.info("Tool succeeded tool=%s", fn_name)
                    else:
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
                if fn_name == "web__create_script":
                    payload = _parse_tool_result_json(result_text)
                    if payload is not None and isinstance(payload.get("ok"), bool):
                        last_create_script_ok = bool(payload["ok"])
                payload = _parse_tool_result_json(result_text)
                if payload is not None and _has_usable_web_data(fn_name, payload):
                    had_usable_web_data = True
                    extracted_items = _extract_web_items(fn_name, payload)
                    if extracted_items:
                        latest_web_items = extracted_items
                if payload is not None and _looks_like_successful_script_create(fn_name, payload):
                    script = payload.get("script")
                    if isinstance(script, dict):
                        name = str(script.get("name", "")).strip()
                        if name:
                            latest_script_status = f"{name} saved"
                if fn_name == "web__invoke_script" and payload is not None:
                    invoke_items = _extract_web_items(fn_name, payload)
                    if invoke_items:
                        force_finalize_after_round = True
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
                if (
                    scratchpad_tool_calls >= 3
                    and had_usable_web_data
                    and fn_name == SCRATCHPAD_TOOL_NAME
                    and not force_finalize_after_round
                ):
                    force_finalize_after_round = True
            if had_usable_web_data and latest_web_items and last_create_script_ok is True:
                force_finalize_after_round = True
            if round_used_external_tool:
                include_scratchpad_prompt = True
            elif round_finalized_scratchpad:
                finalize_msg = _finalize_response_message(self._host, scope_for_tools)
                trimmed = trim_to_last_tool_round(messages)
                to_send = trimmed + ([finalize_msg] if finalize_msg else [])
                prepared_messages = prepare_messages_for_chat(to_send)
                final_message = await self._host.llm.chat(
                    messages=prepared_messages,
                    tools=[],
                )
                finish_reason = final_message.get("finish_reason")
                reply = final_message.get("content") or ""
                if not reply.strip():
                    logger.warning(
                        "LLM returned empty reply in finalize path scope=%s finish_reason=%s",
                        scope_for_tools,
                        finish_reason,
                    )
                    reply = "I could not generate a response."
                if had_usable_web_data:
                    if _reply_claims_data_lost(reply):
                        reply = _synthesize_web_data_reply(latest_web_items, reply)
                    else:
                        reply = _rewrite_data_loss_reply(reply)
                return reply, tool_trace
            if force_finalize_after_round and had_usable_web_data:
                lines = _synthesize_web_data_reply(latest_web_items, "").splitlines()
                if latest_script_status:
                    lines.append(f"\nReusable script: {latest_script_status}.")
                return "\n".join(lines), tool_trace
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
        finish_reason = assistant_message.get("finish_reason")
        reply = assistant_message.get("content") or ""
        if not reply.strip():
            logger.warning(
                "LLM returned empty reply in main loop scope=%s finish_reason=%s",
                scope_for_tools,
                finish_reason,
            )
            reply = "I could not generate a response."
        if had_usable_web_data:
            if _reply_claims_data_lost(reply):
                reply = _synthesize_web_data_reply(latest_web_items, reply)
            else:
                reply = _rewrite_data_loss_reply(reply)
        return reply, tool_trace
