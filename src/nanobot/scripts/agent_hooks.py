from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nanobot.scripts.router import (
    CREATE_SCRIPT_INTENT,
    DEFAULT_INTENT,
    SKILL_CREATE_TOOL,
    WEB_CREATE_SCRIPT_TOOL,
    WEB_GOOGLE_SEARCH_TOOL,
    WEB_INTERACT_PAGE_TOOL,
    WEB_INVOKE_SCRIPT_TOOL,
    WEB_READ_PAGE_TOOL,
    WEB_REPAIR_SCRIPT_TOOL,
    WEB_SEARCH_SCRIPTS_TOOL,
    WEB_SEARCH_TOOL,
    WEB_SNAPSHOT_PAGE_TOOL,
    tool_name,
)
from nanobot.scripts.schemas import is_create_script_schema_error, normalize_create_script_args

BLOCKED_TOOL_RESULT_ERROR = "tool_blocked_for_turn"
SEARCH_PROVIDER_UNAVAILABLE_REPLY = (
    "Web search is unavailable because no provider API key is configured. "
    "Please set TAVILY_API_KEY or EXA_API_KEY, or continue with available non-search tools."
)
SEARCH_PROVIDER_UNAVAILABLE_ERROR = "search_provider_unavailable"
CREATE_SCRIPT_FORCE_REASON = (
    "No reusable script candidate was found in this turn. "
    "Create a new script now instead of continuing DOM exploration."
)
MAX_DOM_EXPLORE_AFTER_EMPTY_SEARCH = 2
MAX_DOM_EXPLORE_BEFORE_REPAIR = 2


@dataclass
class ProceduralState:
    intent: str = DEFAULT_INTENT
    blocked_tools: dict[str, str] = field(default_factory=dict)
    force_create_script_next: bool = False
    saw_empty_script_search: bool = False
    dom_explore_calls_after_empty_search: int = 0
    create_script_schema_retry_used: bool = False
    repair_flow_active: bool = False
    failed_execution_id_for_repair: str | None = None
    dom_explore_calls_before_repair: int = 0
    repair_attempted_this_turn: bool = False

    def initial_tool_choice(self, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        if self.intent == CREATE_SCRIPT_INTENT and self._tool_exists(tools, WEB_CREATE_SCRIPT_TOOL):
            return self._tool_choice(WEB_CREATE_SCRIPT_TOOL)
        return None

    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == WEB_CREATE_SCRIPT_TOOL:
            return normalize_create_script_args(args)
        return args

    def all_requested_tools_blocked(self, tool_names: list[str]) -> bool:
        return bool(tool_names) and all(name in self.blocked_tools for name in tool_names)

    def blocked_result(self, tool: str) -> str | None:
        if self.intent == CREATE_SCRIPT_INTENT and tool == SKILL_CREATE_TOOL:
            self.force_create_script_next = True
            return self._blocked_tool_result(
                tool,
                "Skill creation is disabled for create_script intent. Use web__create_script.",
            )
        reason = self.blocked_tools.get(tool)
        if reason is None:
            return None
        return self._blocked_tool_result(tool, reason)

    def on_tool_result(self, tool: str, result_text: str) -> None:
        if tool == WEB_INVOKE_SCRIPT_TOOL:
            failed_execution_id = self._extract_failed_execution_id_from_invoke(result_text)
            if failed_execution_id:
                self.repair_flow_active = True
                self.failed_execution_id_for_repair = failed_execution_id
                self.dom_explore_calls_before_repair = 0
                self.repair_attempted_this_turn = False
                self.blocked_tools[WEB_SEARCH_SCRIPTS_TOOL] = (
                    "Do not search scripts again in this turn after invoke failure; repair directly."
                )
            elif self.repair_attempted_this_turn:
                self.repair_attempted_this_turn = False

        if self.repair_flow_active and tool in {WEB_SNAPSHOT_PAGE_TOOL, WEB_READ_PAGE_TOOL}:
            self.dom_explore_calls_before_repair += 1
            if self.dom_explore_calls_before_repair >= MAX_DOM_EXPLORE_BEFORE_REPAIR:
                self.blocked_tools[WEB_SNAPSHOT_PAGE_TOOL] = "Enough DOM context collected; repair the script now."
                self.blocked_tools[WEB_READ_PAGE_TOOL] = "Enough DOM context collected; repair the script now."
                self.blocked_tools[WEB_SEARCH_SCRIPTS_TOOL] = (
                    "Do not search scripts again in this turn after invoke failure."
                )

        if tool == WEB_REPAIR_SCRIPT_TOOL:
            self.repair_attempted_this_turn = True
            self.repair_flow_active = False
            self.failed_execution_id_for_repair = None

        if self.intent == CREATE_SCRIPT_INTENT and tool == WEB_SEARCH_SCRIPTS_TOOL:
            if self._is_empty_search_scripts_result(result_text):
                self.saw_empty_script_search = True
                self.force_create_script_next = True

        if self.intent == CREATE_SCRIPT_INTENT and self.saw_empty_script_search and tool in {
            WEB_SNAPSHOT_PAGE_TOOL,
            WEB_INTERACT_PAGE_TOOL,
            WEB_READ_PAGE_TOOL,
        }:
            self.dom_explore_calls_after_empty_search += 1
            if self.dom_explore_calls_after_empty_search >= MAX_DOM_EXPLORE_AFTER_EMPTY_SEARCH:
                self.blocked_tools[WEB_SNAPSHOT_PAGE_TOOL] = CREATE_SCRIPT_FORCE_REASON
                self.blocked_tools[WEB_INTERACT_PAGE_TOOL] = CREATE_SCRIPT_FORCE_REASON
                self.blocked_tools[WEB_READ_PAGE_TOOL] = CREATE_SCRIPT_FORCE_REASON
                self.force_create_script_next = True

        if tool in {WEB_SEARCH_TOOL, WEB_GOOGLE_SEARCH_TOOL}:
            search_payload = self._parse_json_object(result_text)
            if (
                isinstance(search_payload, dict)
                and str(search_payload.get("error", "")) == SEARCH_PROVIDER_UNAVAILABLE_ERROR
            ):
                reason = str(search_payload.get("message") or SEARCH_PROVIDER_UNAVAILABLE_REPLY)
                self.blocked_tools[WEB_SEARCH_TOOL] = reason
                self.blocked_tools[WEB_GOOGLE_SEARCH_TOOL] = reason

        if (
            self.intent == CREATE_SCRIPT_INTENT
            and tool == WEB_CREATE_SCRIPT_TOOL
            and is_create_script_schema_error(result_text)
        ):
            if not self.create_script_schema_retry_used:
                self.create_script_schema_retry_used = True
                self.force_create_script_next = True
            self.blocked_tools[WEB_SEARCH_SCRIPTS_TOOL] = (
                "Do not search again in this turn after create_script schema validation error."
            )

    def next_tool_choice(self, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        if (
            self.force_create_script_next
            and self.intent == CREATE_SCRIPT_INTENT
            and self._tool_exists(tools, WEB_CREATE_SCRIPT_TOOL)
        ):
            return self._tool_choice(WEB_CREATE_SCRIPT_TOOL)
        if self.repair_flow_active and not self.repair_attempted_this_turn and self._tool_exists(
            tools, WEB_REPAIR_SCRIPT_TOOL
        ):
            return self._tool_choice(WEB_REPAIR_SCRIPT_TOOL)
        if self.repair_attempted_this_turn and self._tool_exists(tools, WEB_INVOKE_SCRIPT_TOOL):
            return self._tool_choice(WEB_INVOKE_SCRIPT_TOOL)
        return None

    def reminder_message(self) -> dict[str, str]:
        if self.repair_flow_active and not self.repair_attempted_this_turn:
            return {
                "role": "user",
                "content": (
                    "The previous script invoke failed. Call web__repair_script now using the failed execution "
                    f"id {self.failed_execution_id_for_repair or 'from tool output'} and patched_code."
                ),
            }
        if self.repair_attempted_this_turn:
            return {
                "role": "user",
                "content": "Repair has been attempted in this turn. Call web__invoke_script now to verify.",
            }
        return {
            "role": "user",
            "content": "No script candidate exists yet. Create the script now in this turn.",
        }

    def mark_requested_tools(self, tool_names: list[str]) -> None:
        if WEB_CREATE_SCRIPT_TOOL in tool_names:
            self.force_create_script_next = False

    @staticmethod
    def _tool_exists(tools: list[dict[str, Any]], name: str) -> bool:
        return any(tool_name(tool) == name for tool in tools)

    @staticmethod
    def _tool_choice(name: str) -> dict[str, Any]:
        return {"type": "function", "function": {"name": name}}

    @staticmethod
    def _blocked_tool_result(tool: str, reason: str) -> str:
        return json.dumps(
            {
                "ok": False,
                "error": BLOCKED_TOOL_RESULT_ERROR,
                "message": reason,
                "tool": tool,
            },
            ensure_ascii=True,
        )

    @staticmethod
    def _parse_json_object(result_text: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(result_text)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _is_empty_search_scripts_result(cls, result_text: str) -> bool:
        payload = cls._parse_json_object(result_text)
        if payload is None:
            return False
        candidates = payload.get("candidates")
        return isinstance(candidates, list) and len(candidates) == 0

    @classmethod
    def _extract_failed_execution_id_from_invoke(cls, result_text: str) -> str | None:
        payload = cls._parse_json_object(result_text)
        if payload is None:
            return None
        status = str(payload.get("status", "")).strip().lower()
        execution_id = str(payload.get("execution_id", "")).strip()
        if not execution_id:
            return None
        if status in {"failed", "suspicious"}:
            return execution_id
        return None
