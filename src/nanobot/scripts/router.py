from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nanobot.core_scratchpad import SCRATCHPAD_TOOL_NAME

PROCEDURAL_WEB_STRATEGY = "procedural_web"
GENERAL_STRATEGY = "general"
CREATE_SCRIPT_INTENT = "create_script"
DEFAULT_INTENT = "default"

WEB_SEARCH_TOOL = "web__search_web"
WEB_GOOGLE_SEARCH_TOOL = "web__search_google_web"
WEB_SEARCH_SCRIPTS_TOOL = "web__search_scripts"
WEB_INVOKE_SCRIPT_TOOL = "web__invoke_script"
WEB_TEST_SCRIPT_TOOL = "web__test_script"
WEB_REPAIR_SCRIPT_TOOL = "web__repair_script"
WEB_CREATE_SCRIPT_TOOL = "web__create_script"
WEB_SNAPSHOT_PAGE_TOOL = "web__snapshot_page"
WEB_INTERACT_PAGE_TOOL = "web__interact_page"
WEB_READ_PAGE_TOOL = "web__read_page"
SKILL_CREATE_TOOL = "skill__create"

BLOCKED_PROCEDURAL_TOOLS = {WEB_SEARCH_TOOL, WEB_GOOGLE_SEARCH_TOOL}
CREATE_SCRIPT_ALLOWED_TOOLS = {SCRATCHPAD_TOOL_NAME, WEB_CREATE_SCRIPT_TOOL}
PROCEDURAL_KEYWORDS = {
    "nanoscript",
    "selector",
    "pagination",
    "extract",
    "scrape",
    "crawl",
    "issues",
    "trending",
    "web automation",
    "invoke script",
    "test script",
    "repair script",
    "create script",
}
CREATE_SCRIPT_MARKERS = (
    "create script",
    "create a script",
    "create nanoscript",
    "build a script",
    "save it for reuse",
    "reusable nanoscript",
    "reusable workflow",
    "reusable browser workflow",
    "workflow i can reuse",
)
PROCEDURAL_WEB_POLICY = (
    "For web extraction/automation tasks, default to NanoScript procedural memory. "
    "Use this order first: web__search_scripts, web__invoke_script, web__test_script, web__repair_script. "
    "Only use generic web browsing tools if no reliable script is found or explicit fallback is needed."
)
CREATE_SCRIPT_POLICY = (
    "The user is explicitly asking to create a reusable NanoScript. "
    "Do not ask for generic clarification unless required fields are truly missing. "
    "Call web__create_script in this turn with a best-effort complete payload "
    "(name, description, code, params_schema, output_schema, selector_manifest, embedding_text, created_by). "
    "If selector details are uncertain, still provide a reasonable fallback selector_manifest instead of null. "
    "Generate code that passes NanoScript AST constraints: avoid while True, "
    "use browser.loop_guard(...) in while conditions, "
    "and prefer x = x + 1 over augmented assignment operators like +=."
)


@dataclass(frozen=True)
class ProceduralRoute:
    strategy: str
    intent: str
    system_messages: list[dict[str, str]]
    tools: list[dict[str, Any]]


def tool_name(tool: dict[str, Any]) -> str:
    return str(tool.get("function", {}).get("name", ""))


def execution_strategy_for_request(user_text: str) -> str:
    text = user_text.strip().lower()
    if not text:
        return GENERAL_STRATEGY
    has_url = "http://" in text or "https://" in text or "www." in text
    has_procedural_hint = any(keyword in text for keyword in PROCEDURAL_KEYWORDS)
    if has_procedural_hint and (has_url or "github" in text):
        return PROCEDURAL_WEB_STRATEGY
    if "nanoscript" in text or "script version" in text:
        return PROCEDURAL_WEB_STRATEGY
    return GENERAL_STRATEGY


def intent_for_request(user_text: str, strategy: str) -> str:
    text = user_text.strip().lower()
    if strategy != PROCEDURAL_WEB_STRATEGY:
        return DEFAULT_INTENT
    if not text:
        return DEFAULT_INTENT
    has_create_marker = any(marker in text for marker in CREATE_SCRIPT_MARKERS)
    if has_create_marker and ("script" in text or "nanoscript" in text or "workflow" in text):
        return CREATE_SCRIPT_INTENT
    if "github issues" in text and "reusable" in text and "workflow" in text:
        return CREATE_SCRIPT_INTENT
    return DEFAULT_INTENT


def system_messages_for_route(strategy: str, intent: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if strategy == PROCEDURAL_WEB_STRATEGY:
        messages.append({"role": "system", "content": PROCEDURAL_WEB_POLICY})
    if intent == CREATE_SCRIPT_INTENT:
        messages.append({"role": "system", "content": CREATE_SCRIPT_POLICY})
    return messages


def filter_tools_for_route(tools: list[dict[str, Any]], strategy: str, intent: str) -> list[dict[str, Any]]:
    if strategy == PROCEDURAL_WEB_STRATEGY:
        tools = [tool for tool in tools if tool_name(tool) not in BLOCKED_PROCEDURAL_TOOLS]
    if intent == CREATE_SCRIPT_INTENT:
        filtered = [tool for tool in tools if tool_name(tool) in CREATE_SCRIPT_ALLOWED_TOOLS]
        return filtered or tools
    return tools


def route_request(user_text: str, tools: list[dict[str, Any]]) -> ProceduralRoute:
    strategy = execution_strategy_for_request(user_text)
    intent = intent_for_request(user_text, strategy)
    return ProceduralRoute(
        strategy=strategy,
        intent=intent,
        system_messages=system_messages_for_route(strategy, intent),
        tools=filter_tools_for_route(tools, strategy, intent),
    )
