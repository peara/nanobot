"""Integration tests for LLM tool choice with skill instructions.

Tests that the LLM respects skill instructions to use web__invoke_script
over web__create_script when both are available. Requires LM Studio running
at localhost:1234 with a loaded model.

Run: uv run pytest tests/skills/test_llm_tool_choice.py -v -m integration
Skip: uv run pytest tests/skills/ -v -k "not llm_tool_choice"
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig
from nanobot.core import BotCore
from nanobot.llm import LlmClient
from nanobot.prompts import PromptStore
from nanobot.skills.injection import build_skill_messages
from nanobot.skills.models import Skill
from nanobot.tools.base import Tool

LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
LMSTUDIO_MODEL = "google/gemma-4-31b"


def _lmstudio_reachable() -> bool:
    try:
        req = urllib.request.Request(f"{LMSTUDIO_BASE_URL.rstrip('/')}/models")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


requires_lmstudio = pytest.mark.skipif(
    not _lmstudio_reachable(),
    reason="LM Studio not reachable at localhost:1234",
)

YAHOO_SKILL_INSTRUCTIONS = (
    "When searching Yahoo Auctions, ALWAYS use web__search_scripts first "
    "to find an existing NanoScript, then web__invoke_script to run it. "
    "NEVER use web__create_script to create a new script for Yahoo Auctions — "
    "the script yahoo_auctions_quality_search already exists. "
    "Example flow: web__search_scripts('yahoo auctions') → find script → "
    "web__invoke_script(name='yahoo_auctions_quality_search', params={...})"
)

NANOSCRIPT_CONSTRAINT_INSTRUCTIONS = (
    "When creating new NanoScripts, use web__create_script. "
    "Follow the NanoScript format: async def script(page, params): ... "
    "Include params_schema and result_schema."
)

USER_PROMPT = "Search Yahoo Auctions for Minolta 100mm f/2.5 lens"

SYSTEM_PROMPT = """You are Nano, a personal assistant.
Be careful, detail-oriented, and explicit about what you have verified vs inferred.
Do not claim an action is completed unless a tool call or direct evidence confirms it.
Keep responses concise, practical, and friendly.
Use memory_save or memory_save_turn when the user asks to remember something important.
Keep track of progress and next actions internally before responding.
When useful, call available tools.
Only claim a script/procedure was saved after the create/save tool returns ok=true.
Never persist memories that contradict the immediately previous tool result.
If a web tool already returned usable extracted data in this turn, present that data directly to the user now.
Do not claim the data was lost or that you must re-run extraction in the same turn.
Reusable artifacts boundary:
- Web Script = executable extractor returning structured data. Use web__create_script only for browser/page extraction code.
- Skill = reusable workflow/policy. Use skills for tool routing, parameter mapping, output formatting, language, bullet-count, and user-facing response strategy.
- Never store formatting, language, bullet-count policy, or answer templates inside a web script.
- Never store executable scraping logic inside a skill when a web script is the appropriate extractor layer.
- If the user asks to save a reusable procedure/workflow, decide which reusable artifact is needed:
  - pure extractor -> create/update a web script;
  - routing, formatting, language, parameter mapping, or multi-step workflow -> create/update a skill;
  - both extraction and workflow are reusable -> create/update the script first, then create/update a skill that references it.
- If an existing script can handle a variant through params, invoke it with params instead of creating a duplicate script.
- When invoking web__invoke_script, params must be a JSON object/dict, for example {"limit": 10}. Never pass params as a string or DSML/XML-like markup.
web__create_script accepts Python NanoScript only (not JavaScript). Use:
async def script(page, params: dict[str, Any]) -> dict[str, Any]:
Return structured data only (items/metadata), never answer templates.
When constructing code for web__create_script, generate ONLY Python code in this exact shape:
async def script(page, params):
    # extraction logic
    return {"items": [...], "metadata": {...}}
Do not call web__create_script if you cannot produce valid Python NanoScript in this shape.
If web__create_script returns invalid_script_language or invalid_script for JavaScript syntax, do NOT call web__create_script again in this turn.
Immediately switch strategy: call web__read_page or web__invoke_script (or reply with available results if enough data is already present).

IMPORTANT - Scratchpad protocol is mandatory whenever tools are used:
- At the start of a work-needed turn, call session__scratchpad_write with mode="init".
- After each tool result, call session__scratchpad_write with mode="append" to update about the last call before any next tool call.
- Before the final assistant answer for work-needed turns, call session__scratchpad_write with mode="finalize".

Format responses as plain text suitable for Telegram.
Do not output JSON unless the user explicitly asks for JSON.
Do not use markdown tables, HTML tags, or raw markup.
"""


class _FakeChannel:
    async def send(self, chat_id: str, text: str) -> None:
        pass


class _FakeTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        descriptions = {
            "web__search_scripts": "Find reusable browser data extraction scripts for a task. Search by query and return matching scripts with metadata and schemas.",
            "web__invoke_script": "Invoke a reusable browser data extraction script by name and return structured data. Pass params as a JSON object.",
            "web__create_script": "Create or update a reusable Python NanoScript browser data extractor. Requires name, description, and Python code following the NanoScript format.",
            "web__read_page": "Read and extract content from a web page URL. Returns page text, links, and metadata.",
            "web__search_web": "Search the web via Tavily or Exa and return candidate result URLs before reading a page.",
            "memory__search": "Search stored memories and facts by query. Returns matching memory entries.",
            "memory__save": "Save a new memory or fact for future reference.",
            "memory__save_turn": "Save the current conversation turn as a memory.",
            "skill__list": "List all active skills.",
            "skill__get": "Get details of a specific skill by name.",
            "plan__get": "Get a plan by ID.",
            "plan__list": "List all plans.",
        }
        return descriptions.get(self._name, f"Tool {self._name}")

    @property
    def schema(self) -> dict[str, Any]:
        schemas: dict[str, dict[str, Any]] = {
            "web__search_scripts": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query for scripts"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
            "web__invoke_script": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Script name to invoke"},
                    "params": {"type": "object", "description": "Script parameters as a JSON object/dict"},
                },
                "required": ["name"],
            },
            "web__create_script": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Script name"},
                    "description": {"type": "string", "description": "What the script does"},
                    "code": {
                        "type": "string",
                        "description": "Python NanoScript code: async def script(page, params): ...",
                    },
                    "params_schema": {"type": "object", "description": "JSON Schema for parameters"},
                    "result_schema": {"type": "object", "description": "JSON Schema for result"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                    "overwrite": {"type": "boolean", "description": "Overwrite existing script", "default": False},
                },
                "required": ["name", "description", "code"],
            },
            "web__read_page": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to read"},
                    "quality_threshold": {"type": "number", "description": "Minimum quality score"},
                },
                "required": ["url"],
            },
            "web__search_web": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                    "language": {"type": "string", "description": "Language code", "default": "vi"},
                },
                "required": ["query"],
            },
        }
        return schemas.get(self._name, {"type": "object", "properties": {}})

    async def call(self, args: dict[str, Any]) -> str:
        del args
        return json.dumps({"ok": True})


WEB_TOOLS = [
    "web__search_scripts",
    "web__invoke_script",
    "web__create_script",
    "web__search_web",
    "web__read_page",
]

CORE_TOOLS = [
    "memory__search",
    "memory__save",
    "memory__save_turn",
    "skill__list",
    "skill__get",
    "plan__get",
    "plan__list",
    "timer__time_now",
    "timer__time_epoch",
    "scheduler__schedule_task",
    "scheduler__list_tasks",
    "scheduler__delete_task",
    "scheduler__pause_task",
    "scheduler__resume_task",
    "scheduler__cron_list",
    "scheduler__cron_add",
    "scheduler__cron_remove",
]


def _make_config(tmp_path: str) -> AppConfig:
    return AppConfig(
        assistant_name="Test",
        database_path=str(Path(tmp_path) / "nanobot.db"),
        scheduler_db_path=str(Path(tmp_path) / "scheduler.db"),
        plan_db_path=str(Path(tmp_path) / "plans.db"),
        skill_db_path=str(Path(tmp_path) / "skills.db"),
        prompt_db_path=str(Path(tmp_path) / "prompts.db"),
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(
            base_url=LMSTUDIO_BASE_URL,
            api_key="lm-studio",
            model=LMSTUDIO_MODEL,
            temperature=0.0,
            max_tokens=400,
        ),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
    )


def _make_bot(tmp_path: str) -> BotCore:
    bot = BotCore(config=_make_config(tmp_path), channels={"telegram": _FakeChannel()})
    for name in CORE_TOOLS:
        bot.tools.register(_FakeTool(name))
    for name in WEB_TOOLS:
        bot.tools.register(_FakeTool(name))
    return bot


def _first_tool_call_names(response: dict) -> list[str]:
    tool_calls = response.get("tool_calls") or []
    return [str(tc["function"]["name"]) for tc in tool_calls]


@requires_lmstudio
class TestLLMRespectsSkillInstructions:
    """Test that the LLM chooses web__invoke_script over web__create_script
    when skill instructions explicitly say to use the existing script."""

    @pytest.mark.integration
    async def test_yahoo_skill_chooses_invoke_not_create(self, tmp_path: Path) -> None:
        bot = _make_bot(str(tmp_path))
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses using existing NanoScript",
            instructions=YAHOO_SKILL_INSTRUCTIONS,
            trigger_mode="pattern",
            trigger_patterns=["yahoo|auction|ヤフオク"],
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )

        skill = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        assert skill is not None
        prompts = PromptStore(str(Path(tmp_path) / "prompts.db"), seed_defaults=True)
        skill_messages = build_skill_messages([skill], prompts)

        tools = bot._list_openai_tools(skill_names=["yahoo_auctions_search_workflow"])
        llm = LlmClient(bot.config.model)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *skill_messages,
            {"role": "user", "content": USER_PROMPT},
        ]

        response = await llm.chat(messages=messages, tools=tools)
        tool_names = _first_tool_call_names(response)

        assert "web__create_script" not in tool_names, (
            f"LLM chose web__create_script despite skill instructions. Tool calls: {tool_names}"
        )
        assert any(n in tool_names for n in ["web__search_scripts", "web__invoke_script"]), (
            f"LLM did not use any web script tools. Tool calls: {tool_names}"
        )

    @pytest.mark.integration
    async def test_yahoo_skill_with_constrainer_chooses_invoke(self, tmp_path: Path) -> None:
        """When nanoscript_structure_constraint co-activates and adds web__create_script,
        the LLM should still prefer web__invoke_script for Yahoo Auctions tasks."""
        bot = _make_bot(str(tmp_path))
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses using existing NanoScript",
            instructions=YAHOO_SKILL_INSTRUCTIONS,
            trigger_mode="pattern",
            trigger_patterns=["yahoo|auction|ヤフオク"],
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )
        bot.skills.create(
            name="nanoscript_structure_constraint",
            description="Guidelines for creating well-structured NanoScripts",
            instructions=NANOSCRIPT_CONSTRAINT_INSTRUCTIONS,
            trigger_mode="pattern",
            trigger_patterns=["script|nanoscript|create.*script"],
            tools_allowlist=["web__create_script"],
            priority=0,
        )

        yahoo_skill = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        nano_skill = bot.skills.get_by_name("nanoscript_structure_constraint")
        assert yahoo_skill is not None
        assert nano_skill is not None

        prompts = PromptStore(str(Path(tmp_path) / "prompts.db"), seed_defaults=True)
        skill_messages = build_skill_messages([yahoo_skill, nano_skill], prompts)

        tools = bot._list_openai_tools(
            skill_names=["yahoo_auctions_search_workflow", "nanoscript_structure_constraint"]
        )
        available_tool_names = [t["function"]["name"] for t in tools]
        assert "web__create_script" in available_tool_names, (
            "web__create_script should be available (from nanoscript_structure_constraint allowlist)"
        )
        assert "web__invoke_script" in available_tool_names

        llm = LlmClient(bot.config.model)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *skill_messages,
            {"role": "user", "content": USER_PROMPT},
        ]

        response = await llm.chat(messages=messages, tools=tools)
        tool_names = _first_tool_call_names(response)
        assert "web__create_script" not in tool_names, (
            f"LLM chose web__create_script despite yahoo skill instructions saying to use invoke_script. "
            f"Tool calls: {tool_names}"
        )

    @pytest.mark.integration
    async def test_no_skill_no_create_script_in_tools(self, tmp_path: Path) -> None:
        """Without skill instructions, web__create_script is not in the tool list at all."""
        bot = _make_bot(str(tmp_path))

        tools = bot._list_openai_tools(skill_names=None)
        available_tool_names = [t["function"]["name"] for t in tools]

        assert "web__create_script" not in available_tool_names, (
            "No skills active — web__create_script should not be in core tools"
        )


YAHOO_SKILL_INSTRUCTIONS = (
    "When searching Yahoo Auctions, ALWAYS use web__search_scripts first "
    "to find an existing NanoScript, then web__invoke_script to run it. "
    "NEVER use web__create_script to create a new script for Yahoo Auctions — "
    "the script yahoo_auctions_quality_search already exists. "
    "Example flow: web__search_scripts('yahoo auctions') → find script → "
    "web__invoke_script(name='yahoo_auctions_quality_search', params={...})"
)

NANOSCRIPT_CONSTRAINT_INSTRUCTIONS = (
    "When creating new NanoScripts, use web__create_script. "
    "Follow the NanoScript format: async def script(page, params): ... "
    "Include params_schema and result_schema."
)

USER_PROMPT = "Search Yahoo Auctions for Minolta 100mm f/2.5 lens"


class _FakeChannel:
    async def send(self, chat_id: str, text: str) -> None:
        pass


class _FakeTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        descriptions = {
            "web__search_scripts": "Find reusable browser data extraction scripts for a task.",
            "web__invoke_script": "Invoke a reusable browser data extraction script and return structured data.",
            "web__create_script": "Create or update a reusable Python NanoScript browser data extractor.",
            "web__search_web": "Search the web via Tavily or Exa and return candidate result URLs.",
            "web__read_page": "Read and extract content from a web page URL.",
            "memory__search": "Search stored memories and facts.",
            "memory__save": "Save a new memory or fact.",
        }
        return descriptions.get(self._name, f"Fake tool {self._name}")

    @property
    def schema(self) -> dict[str, Any]:
        schemas: dict[str, dict[str, Any]] = {
            "web__search_scripts": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query for scripts"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
            "web__invoke_script": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Script name"},
                    "params": {"type": "object", "description": "Script parameters"},
                },
                "required": ["name"],
            },
            "web__create_script": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Script name"},
                    "description": {"type": "string", "description": "What the script does"},
                    "code": {"type": "string", "description": "Python NanoScript code"},
                },
                "required": ["name", "description", "code"],
            },
            "web__search_web": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
        }
        return schemas.get(self._name, {"type": "object", "properties": {}})

    async def call(self, args: dict[str, Any]) -> str:
        del args
        return json.dumps({"ok": True})


WEB_TOOLS = [
    "web__search_scripts",
    "web__invoke_script",
    "web__create_script",
    "web__search_web",
    "web__read_page",
]

CORE_TOOLS = [
    "memory__search",
    "memory__save",
    "memory__save_turn",
    "skill__list",
    "skill__get",
    "plan__get",
    "plan__list",
    "timer__time_now",
    "timer__time_epoch",
    "scheduler__schedule_task",
    "scheduler__list_tasks",
    "scheduler__delete_task",
    "scheduler__pause_task",
    "scheduler__resume_task",
    "scheduler__cron_list",
    "scheduler__cron_add",
    "scheduler__cron_remove",
]


def _make_config(tmp_path: str) -> AppConfig:
    return AppConfig(
        assistant_name="Test",
        database_path=str(Path(tmp_path) / "nanobot.db"),
        scheduler_db_path=str(Path(tmp_path) / "scheduler.db"),
        plan_db_path=str(Path(tmp_path) / "plans.db"),
        skill_db_path=str(Path(tmp_path) / "skills.db"),
        prompt_db_path=str(Path(tmp_path) / "prompts.db"),
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(
            base_url=LMSTUDIO_BASE_URL,
            api_key="lm-studio",
            model=LMSTUDIO_MODEL,
            temperature=0.0,
            max_tokens=400,
        ),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
    )


def _make_bot(tmp_path: str) -> BotCore:
    bot = BotCore(config=_make_config(tmp_path), channels={"telegram": _FakeChannel()})
    for name in CORE_TOOLS:
        bot.tools.register(_FakeTool(name))
    for name in WEB_TOOLS:
        bot.tools.register(_FakeTool(name))
    return bot


def _first_tool_call_names(response: dict) -> list[str]:
    tool_calls = response.get("tool_calls") or []
    return [str(tc["function"]["name"]) for tc in tool_calls]


@requires_lmstudio
class TestLLMRespectsSkillInstructions:
    """Test that the LLM chooses web__invoke_script over web__create_script
    when skill instructions explicitly say to use the existing script."""

    @pytest.mark.integration
    async def test_yahoo_skill_chooses_invoke_not_create(self, tmp_path: Path) -> None:
        bot = _make_bot(str(tmp_path))
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses using existing NanoScript",
            instructions=YAHOO_SKILL_INSTRUCTIONS,
            trigger_mode="pattern",
            trigger_patterns=["yahoo|auction|ヤフオク"],
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )

        skill = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        assert skill is not None
        prompts = PromptStore(str(Path(tmp_path) / "prompts.db"), seed_defaults=True)
        skill_messages = build_skill_messages([skill], prompts)

        tools = bot._list_openai_tools(skill_names=["yahoo_auctions_search_workflow"])
        llm = LlmClient(bot.config.model)

        messages = [
            {"role": "system", "content": "You are Nano, a helpful assistant."},
            *skill_messages,
            {"role": "user", "content": USER_PROMPT},
        ]

        response = await llm.chat(messages=messages, tools=tools)
        tool_names = _first_tool_call_names(response)

        assert "web__create_script" not in tool_names, (
            f"LLM chose web__create_script despite skill instructions. Tool calls: {tool_names}"
        )
        assert any(n in tool_names for n in ["web__search_scripts", "web__invoke_script"]), (
            f"LLM did not use any web script tools. Tool calls: {tool_names}"
        )

    @pytest.mark.integration
    async def test_yahoo_skill_with_constrainer_chooses_invoke(self, tmp_path: Path) -> None:
        """When nanoscript_structure_constraint co-activates and adds web__create_script,
        the LLM should still prefer web__invoke_script for Yahoo Auctions tasks."""
        bot = _make_bot(str(tmp_path))
        bot.skills.create(
            name="yahoo_auctions_search_workflow",
            description="Search Yahoo Auctions for lenses using existing NanoScript",
            instructions=YAHOO_SKILL_INSTRUCTIONS,
            trigger_mode="pattern",
            trigger_patterns=["yahoo|auction|ヤフオク"],
            tools_allowlist=["web__search_scripts", "web__invoke_script", "memory__search"],
            priority=5,
        )
        bot.skills.create(
            name="nanoscript_structure_constraint",
            description="Guidelines for creating well-structured NanoScripts",
            instructions=NANOSCRIPT_CONSTRAINT_INSTRUCTIONS,
            trigger_mode="pattern",
            trigger_patterns=["script|nanoscript|create.*script"],
            tools_allowlist=["web__create_script"],
            priority=0,
        )

        yahoo_skill = bot.skills.get_by_name("yahoo_auctions_search_workflow")
        nano_skill = bot.skills.get_by_name("nanoscript_structure_constraint")
        assert yahoo_skill is not None
        assert nano_skill is not None

        prompts = PromptStore(str(Path(tmp_path) / "prompts.db"), seed_defaults=True)
        skill_messages = build_skill_messages([yahoo_skill, nano_skill], prompts)

        tools = bot._list_openai_tools(
            skill_names=["yahoo_auctions_search_workflow", "nanoscript_structure_constraint"]
        )
        available_tool_names = [t["function"]["name"] for t in tools]
        assert "web__create_script" in available_tool_names, (
            "web__create_script should be available (from nanoscript_structure_constraint allowlist)"
        )
        assert "web__invoke_script" in available_tool_names

        llm = LlmClient(bot.config.model)
        messages = [
            {"role": "system", "content": "You are Nano, a helpful assistant."},
            *skill_messages,
            {"role": "user", "content": USER_PROMPT},
        ]

        response = await llm.chat(messages=messages, tools=tools)
        tool_names = _first_tool_call_names(response)

        assert "web__create_script" not in tool_names, (
            f"LLM chose web__create_script despite yahoo skill instructions saying to use invoke_script. "
            f"Tool calls: {tool_names}"
        )

    @pytest.mark.integration
    async def test_no_skill_no_create_script_in_tools(self, tmp_path: Path) -> None:
        """Without skill instructions, web__create_script is not in the tool list at all."""
        bot = _make_bot(str(tmp_path))
        tools = bot._list_openai_tools(skill_names=None)
        available_tool_names = [t["function"]["name"] for t in tools]

        assert "web__create_script" not in available_tool_names, (
            "No skills active — web__create_script should not be in core tools"
        )
