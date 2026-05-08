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
EMPTY_REPLY_FALLBACK = "I'm sorry, I hit an empty model response. Please try again."
MISSING_REQUIRED_TOOL_CALL_REPLY = (
    "I could not execute the required tool call in this turn. Please try again."
)
SEARCH_PROVIDER_UNAVAILABLE_REPLY = (
    "Web search is unavailable because no provider API key is configured. "
    "Please set TAVILY_API_KEY or EXA_API_KEY, or continue with available non-search tools."
)
SEARCH_PROVIDER_UNAVAILABLE_ERROR = "search_provider_unavailable"
BLOCKED_TOOL_RESULT_ERROR = "tool_blocked_for_turn"
CREATE_SCRIPT_FORCE_REASON = (
    "No reusable script candidate was found in this turn. "
    "Create a new script now instead of continuing DOM exploration."
)
MAX_DOM_EXPLORE_AFTER_EMPTY_SEARCH = 2
MAX_DOM_EXPLORE_BEFORE_REPAIR = 2
CREATE_SCRIPT_SCHEMA_ERROR = "schema.type is required"
CREATE_SCRIPT_REQUIRED_FIELDS = {
    "name",
    "description",
    "code",
    "params_schema",
    "output_schema",
    "embedding_text",
    "created_by",
}
SCRATCHPAD_HINT_FIELDS = {
    "mode",
    "goal",
    "context",
    "known_facts",
    "current_step",
    "next_step",
    "tool_journal",
    "updated_at",
}


def _tool_call_limit_finalize_message(host: Any, scope: str) -> dict[str, str] | None:
    scratchpad = host.contexts.get("chat", scope, "scratchpad") or {}
    goal = _resolve_goal(host, scope, scratchpad)
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
    goal = _resolve_goal(host, scope, scratchpad)
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
        cur_content = msg.get("content") or ""
        is_scratchpad_state = cur_role == "user" and str(cur_content).startswith("[Internal scratchpad state")
        if prev_role == cur_role and prev_role not in ("system", "tool") and not is_scratchpad_state:
            prev_content = prev.get("content") or ""
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


def _resolve_goal(host: Any, scope: str, scratchpad: dict[str, Any]) -> str:
    goal = str(scratchpad.get("goal", "")).strip()
    if goal:
        return goal
    last_user_message = host.contexts.get("chat", scope, "last_user_message") or {}
    last_text = str(last_user_message.get("text", "")).strip()
    if last_text:
        return last_text
    return "Respond to the user's latest request."


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

    @staticmethod
    def _tool_exists(tools: list[dict[str, Any]], tool_name: str) -> bool:
        for tool in tools:
            name = str(tool.get("function", {}).get("name", ""))
            if name == tool_name:
                return True
        return False

    def _initial_tool_choice(self, scope_for_tools: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        intent_payload = self._host.contexts.get("chat", scope_for_tools, "execution_intent") or {}
        intent = str(intent_payload.get("value", "")).strip().lower()
        if intent == "create_script" and self._tool_exists(tools, "web__create_script"):
            return {"type": "function", "function": {"name": "web__create_script"}}
        return None

    @staticmethod
    def _default_params_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "description": "Repository issues URL"},
                "max_pages": {"type": "integer", "default": 5},
            },
        }

    @staticmethod
    def _default_output_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["issues"],
            "properties": {
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["title", "url"],
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                        },
                    },
                }
            },
        }

    @staticmethod
    def _default_selector_manifest() -> dict[str, list[str]]:
        return {
            "issue_row": ["div[id^='issue_']", ".js-issue-row", "[data-testid='list-view-item']"],
            "issue_title_link": ["a[data-hovercard-type='issue']", "a.js-navigation-open", "a.Link--primary"],
            "next_page": ["a.next_page", "a[rel='next']", "a[aria-label='Next Page']"],
        }

    def _normalize_create_script_args(self, args: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(args)
        params_schema = normalized.get("params_schema")
        if not isinstance(params_schema, dict) or "type" not in params_schema:
            normalized["params_schema"] = self._default_params_schema()
        output_schema = normalized.get("output_schema")
        if not isinstance(output_schema, dict) or "type" not in output_schema:
            normalized["output_schema"] = self._default_output_schema()
        selector_manifest = normalized.get("selector_manifest")
        if not isinstance(selector_manifest, dict):
            normalized["selector_manifest"] = self._default_selector_manifest()
        return normalized

    @staticmethod
    def _is_create_script_schema_error(result_text: str) -> bool:
        try:
            payload = json.loads(result_text)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        error = payload.get("error")
        if not isinstance(error, dict):
            return False
        if str(error.get("type", "")) != "PARAMS_VALIDATION_ERROR":
            return False
        message = str(error.get("message", ""))
        return CREATE_SCRIPT_SCHEMA_ERROR in message

    @staticmethod
    def _is_empty_search_scripts_result(result: str) -> bool:
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        candidates = payload.get("candidates")
        return isinstance(candidates, list) and len(candidates) == 0

    @staticmethod
    def _extract_failed_execution_id_from_invoke(result_text: str) -> str | None:
        try:
            payload = json.loads(result_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        status = str(payload.get("status", "")).strip().lower()
        execution_id = str(payload.get("execution_id", "")).strip()
        if not execution_id:
            return None
        if status in {"failed", "suspicious"}:
            return execution_id
        return None

    async def _chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        response_format: dict[str, Any] | None,
        tool_choice: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if tool_choice is None:
            return await self._host.llm.chat(messages=messages, tools=tools, response_format=response_format)
        try:
            return await self._host.llm.chat(
                messages=messages,
                tools=tools,
                response_format=response_format,
                tool_choice=tool_choice,
            )
        except TypeError:
            # Keep compatibility with local/mock clients that haven't adopted tool_choice yet.
            return await self._host.llm.chat(messages=messages, tools=tools, response_format=response_format)

    @staticmethod
    def _tool_parameters_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        by_name: dict[str, dict[str, Any]] = {}
        for tool in tools:
            function = tool.get("function", {})
            name = str(function.get("name", ""))
            if not name:
                continue
            parameters = function.get("parameters", {})
            if isinstance(parameters, dict):
                by_name[name] = parameters
        return by_name

    @staticmethod
    def _looks_like_misrouted_scratchpad_call(
        fn_name: str,
        args: dict[str, Any],
        tool_parameters: dict[str, dict[str, Any]],
    ) -> bool:
        if fn_name == SCRATCHPAD_TOOL_NAME:
            return False
        if not isinstance(args, dict):
            return False
        if CREATE_SCRIPT_REQUIRED_FIELDS.intersection(args.keys()):
            return False
        has_scratchpad_hints = bool(SCRATCHPAD_HINT_FIELDS.intersection(args.keys()))
        if not has_scratchpad_hints:
            return False

        parameters = tool_parameters.get(fn_name, {})
        required = set(parameters.get("required") or [])
        properties_raw = parameters.get("properties")
        properties = set(properties_raw.keys()) if isinstance(properties_raw, dict) else set()

        if required and (required - set(args.keys())):
            return True
        if properties and not (set(args.keys()) & properties):
            return True
        return False

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
        initial_tools = self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt)
        initial_tool_choice = self._initial_tool_choice(scope_for_tools, initial_tools)
        assistant_message = await self._chat(
            messages=prepared_messages,
            tools=initial_tools,
            response_format=response_format,
            tool_choice=initial_tool_choice,
        )
        if initial_tool_choice is not None and not assistant_message.get("tool_calls"):
            reminder = {
                "role": "user",
                "content": "You must execute the required tool call now before any final reply.",
            }
            retry_messages = prepare_messages_for_chat([*to_send, reminder])
            assistant_message = await self._chat(
                messages=retry_messages,
                tools=initial_tools,
                response_format=response_format,
                tool_choice=initial_tool_choice,
            )
            if not assistant_message.get("tool_calls"):
                return MISSING_REQUIRED_TOOL_CALL_REPLY, []
        tool_trace: list[dict[str, Any]] = []
        needs_scratchpad_update = False
        total_tool_calls = 0
        blocked_tools: dict[str, str] = {}
        force_create_script_next = False
        saw_empty_script_search = False
        dom_explore_calls_after_empty_search = 0
        create_script_schema_retry_used = False
        repair_flow_active = False
        failed_execution_id_for_repair: str | None = None
        dom_explore_calls_before_repair = 0
        repair_attempted_this_turn = False
        tool_parameters = self._tool_parameters_by_name(tools)
        intent_payload = self._host.contexts.get("chat", scope_for_tools, "execution_intent") or {}
        execution_intent = str(intent_payload.get("value", "")).strip().lower()
        previous_round_signatures: list[tuple[str, str]] | None = None
        identical_round_repeats = 0
        while assistant_message.get("tool_calls"):
            requested_calls = assistant_message["tool_calls"]
            requested_tool_names = [str(call.get("function", {}).get("name", "")) for call in requested_calls]
            if requested_calls and all(name in blocked_tools for name in requested_tool_names):
                logger.warning(
                    "Blocked tools requested again scope=%s tools=%s",
                    scope_for_tools,
                    requested_tool_names,
                )
                return SEARCH_PROVIDER_UNAVAILABLE_REPLY, tool_trace
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
                    finalize_msg = _tool_call_limit_finalize_message(self._host, scope_for_tools)
                    trimmed = trim_to_last_tool_round(messages)
                    to_send = trimmed + ([finalize_msg] if finalize_msg else [])
                    prepared_messages = prepare_messages_for_chat(to_send)
                    final_message = await self._chat(
                        messages=prepared_messages,
                        tools=[],
                        response_format=None,
                    )
                    reply = final_message.get("content") or TOOL_CALL_LIMIT_ABORT_REPLY
                    return reply, tool_trace
                fn_name = tool_call["function"]["name"]
                raw_args = tool_call["function"].get("arguments") or "{}"
                args = json.loads(raw_args)
                if self._looks_like_misrouted_scratchpad_call(fn_name, args, tool_parameters):
                    logger.warning(
                        "Remapping misrouted scratchpad payload scope=%s tool=%s keys=%s",
                        scope_for_tools,
                        fn_name,
                        sorted(args.keys()),
                    )
                    fn_name = SCRATCHPAD_TOOL_NAME
                    args.setdefault("mode", "append")
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
                        if fn_name == "web__create_script":
                            args = self._normalize_create_script_args(args)
                        blocked_for_intent = False
                        if execution_intent == "create_script" and fn_name == "skill__create":
                            result = json.dumps(
                                {
                                    "ok": False,
                                    "error": BLOCKED_TOOL_RESULT_ERROR,
                                    "message": (
                                        "Skill creation is disabled for create_script intent. "
                                        "Use web__create_script."
                                    ),
                                    "tool": fn_name,
                                },
                                ensure_ascii=True,
                            )
                            needs_scratchpad_update = True
                            force_create_script_next = True
                            blocked_for_intent = True
                            logger.info("Blocked tool=%s for create_script intent", fn_name)
                        if blocked_for_intent:
                            pass
                        elif fn_name in blocked_tools:
                            result = json.dumps(
                                {
                                    "ok": False,
                                    "error": BLOCKED_TOOL_RESULT_ERROR,
                                    "message": blocked_tools[fn_name],
                                    "tool": fn_name,
                                },
                                ensure_ascii=True,
                            )
                            needs_scratchpad_update = True
                        else:
                            result = await self._host.tools.call(fn_name, args, scope=scope_for_tools, run_id=run_id)
                            needs_scratchpad_update = True
                            round_used_external_tool = True
                            if fn_name == "web__invoke_script":
                                failed_execution_id = self._extract_failed_execution_id_from_invoke(str(result))
                                if failed_execution_id:
                                    repair_flow_active = True
                                    failed_execution_id_for_repair = failed_execution_id
                                    dom_explore_calls_before_repair = 0
                                    repair_attempted_this_turn = False
                                    blocked_tools["web__search_scripts"] = (
                                        "Do not search scripts again in this turn "
                                        "after invoke failure; repair directly."
                                    )
                                elif repair_attempted_this_turn:
                                    repair_attempted_this_turn = False
                            if repair_flow_active and fn_name in {"web__snapshot_page", "web__read_page"}:
                                dom_explore_calls_before_repair += 1
                                if dom_explore_calls_before_repair >= MAX_DOM_EXPLORE_BEFORE_REPAIR:
                                    blocked_tools["web__snapshot_page"] = (
                                        "Enough DOM context collected; repair the script now."
                                    )
                                    blocked_tools["web__read_page"] = (
                                        "Enough DOM context collected; repair the script now."
                                    )
                                    blocked_tools["web__search_scripts"] = (
                                        "Do not search scripts again in this turn after invoke failure."
                                    )
                            if fn_name == "web__repair_script":
                                repair_attempted_this_turn = True
                                repair_flow_active = False
                                failed_execution_id_for_repair = None
                            if execution_intent == "create_script" and fn_name == "web__search_scripts":
                                if self._is_empty_search_scripts_result(str(result)):
                                    saw_empty_script_search = True
                                    force_create_script_next = True
                            if execution_intent == "create_script" and saw_empty_script_search and fn_name in {
                                "web__snapshot_page",
                                "web__interact_page",
                                "web__read_page",
                            }:
                                dom_explore_calls_after_empty_search += 1
                                if dom_explore_calls_after_empty_search >= MAX_DOM_EXPLORE_AFTER_EMPTY_SEARCH:
                                    blocked_tools["web__snapshot_page"] = CREATE_SCRIPT_FORCE_REASON
                                    blocked_tools["web__interact_page"] = CREATE_SCRIPT_FORCE_REASON
                                    blocked_tools["web__read_page"] = CREATE_SCRIPT_FORCE_REASON
                                    force_create_script_next = True
                            if fn_name in {"web__search_web", "web__search_google_web"}:
                                try:
                                    search_payload = json.loads(result) if isinstance(result, str) else result
                                except json.JSONDecodeError:
                                    search_payload = None
                                if (
                                    isinstance(search_payload, dict)
                                    and str(search_payload.get("error", "")) == SEARCH_PROVIDER_UNAVAILABLE_ERROR
                                ):
                                    reason = str(search_payload.get("message") or SEARCH_PROVIDER_UNAVAILABLE_REPLY)
                                    blocked_tools["web__search_web"] = reason
                                    blocked_tools["web__search_google_web"] = reason
                            if (
                                execution_intent == "create_script"
                                and fn_name == "web__create_script"
                                and self._is_create_script_schema_error(str(result))
                            ):
                                if not create_script_schema_retry_used:
                                    create_script_schema_retry_used = True
                                    force_create_script_next = True
                                blocked_tools["web__search_scripts"] = (
                                    "Do not search again in this turn after create_script schema validation error."
                                )
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
                finalize_msg = _finalize_response_message(self._host, scope_for_tools)
                trimmed = trim_to_last_tool_round(messages)
                to_send = trimmed + ([finalize_msg] if finalize_msg else [])
                prepared_messages = prepare_messages_for_chat(to_send)
                final_message = await self._chat(
                    messages=prepared_messages,
                    tools=[],
                    response_format=None,
                )
                finish_reason = final_message.get("finish_reason")
                reply = final_message.get("content") or ""
                if not reply.strip():
                    logger.warning(
                        "LLM returned empty reply in finalize path scope=%s finish_reason=%s",
                        scope_for_tools,
                        finish_reason,
                    )
                    reply = EMPTY_REPLY_FALLBACK
                return reply, tool_trace
            trimmed = trim_to_last_tool_round(messages)
            scratchpad_msg = (
                scratchpad_assistant_message(self._host, scope_for_tools) if include_scratchpad_prompt else None
            )
            to_send = trimmed + ([scratchpad_msg] if scratchpad_msg else [])
            prepared_messages = prepare_messages_for_chat(to_send)
            tool_choice_override: dict[str, Any] | None = None
            if (
                force_create_script_next
                and execution_intent == "create_script"
                and self._tool_exists(tools, "web__create_script")
            ):
                tool_choice_override = {"type": "function", "function": {"name": "web__create_script"}}
            if repair_flow_active and not repair_attempted_this_turn and self._tool_exists(tools, "web__repair_script"):
                tool_choice_override = {"type": "function", "function": {"name": "web__repair_script"}}
            if repair_attempted_this_turn and self._tool_exists(tools, "web__invoke_script"):
                tool_choice_override = {"type": "function", "function": {"name": "web__invoke_script"}}
            assistant_message = await self._chat(
                messages=prepared_messages,
                tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt),
                response_format=response_format,
                tool_choice=tool_choice_override,
            )
            if tool_choice_override is not None and not assistant_message.get("tool_calls"):
                reminder = {
                    "role": "user",
                    "content": "No script candidate exists yet. Create the script now in this turn.",
                }
                if repair_flow_active and not repair_attempted_this_turn:
                    reminder = {
                        "role": "user",
                        "content": (
                            "The previous script invoke failed. Call web__repair_script now using the failed execution "
                            f"id {failed_execution_id_for_repair or 'from tool output'} and patched_code."
                        ),
                    }
                elif repair_attempted_this_turn:
                    reminder = {
                        "role": "user",
                        "content": "Repair has been attempted in this turn. Call web__invoke_script now to verify.",
                    }
                retry_messages = prepare_messages_for_chat([*to_send, reminder])
                assistant_message = await self._chat(
                    messages=retry_messages,
                    tools=self._tools_for_chat(tools, allow_scratchpad=include_scratchpad_prompt),
                    response_format=response_format,
                    tool_choice=tool_choice_override,
                )
            if assistant_message.get("tool_calls"):
                requested_names = [
                    str(call.get("function", {}).get("name", "")) for call in assistant_message.get("tool_calls", [])
                ]
                if "web__create_script" in requested_names:
                    force_create_script_next = False
        finish_reason = assistant_message.get("finish_reason")
        reply = assistant_message.get("content") or ""
        if not reply.strip():
            logger.warning(
                "LLM returned empty reply in main loop scope=%s finish_reason=%s",
                scope_for_tools,
                finish_reason,
            )
            reply = EMPTY_REPLY_FALLBACK
        return reply, tool_trace
