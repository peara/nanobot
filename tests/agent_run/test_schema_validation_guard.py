from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from nanobot.agent_run import AgentRun
from nanobot.agent_tool_guards import (
    SchemaValidationGuard,
    ToolGuard,
    _suggest_field_name,
    _validate_tool_schema,
)
from nanobot.hooks import ToolCallEvent
from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry


class _FakeContexts:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], Any] = {}

    def get(self, kind: str, scope: str, key: str) -> Any:
        return self._data.get((kind, scope, key))

    def put(self, kind: str, scope: str, key: str, value: Any) -> None:
        self._data[(kind, scope, key)] = value


class _FakeLlm:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = replies
        self._idx = 0

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
        *,
        scope: str | None = None,
        cancel_token: Any | None = None,
    ) -> dict:
        del messages, tools, response_format, scope, cancel_token
        if self._idx >= len(self._replies):
            raise RuntimeError("No fake LLM reply left")
        reply = self._replies[self._idx]
        self._idx += 1
        return reply


class _StrictTool(Tool):
    def __init__(self) -> None:
        self._call_log: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "test__strict"

    @property
    def description(self) -> str:
        return "Strict schema tool"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    async def call(self, args: dict[str, Any]) -> str:
        self._call_log.append(dict(args))
        return json.dumps({"ok": True, "name": args.get("name")})


class _FakeHost:
    def __init__(self, llm: _FakeLlm) -> None:
        import tempfile

        from nanobot.prompts import PromptStore

        self.config = SimpleNamespace(working_timezone="UTC")
        self.llm = llm
        self.contexts = _FakeContexts()
        self.tools = ToolRegistry()
        self.active_requests: dict[str, Any] = {}
        self.tool_hooks: list[Any] = []
        self.tool_guards: list[ToolGuard] = []
        self.events: list[ToolCallEvent] = []
        self._temp_dir = tempfile.mkdtemp()
        self.prompts = PromptStore(f"{self._temp_dir}/prompts.db", seed_defaults=True)

    async def _dispatch_after_tool_call(self, event: ToolCallEvent) -> None:
        self.events.append(event)


# --- _suggest_field_name tests ---


def test_suggest_field_name_exact_match() -> None:
    assert _suggest_field_name("name", ["name", "value"]) == "name"


def test_suggest_field_name_close_typo() -> None:
    assert _suggest_field_name("nme", ["name", "value"]) == "name"


def test_suggest_field_name_far_typo() -> None:
    assert _suggest_field_name("xyz", ["name", "value"]) is None


def test_suggest_field_name_empty_inputs() -> None:
    assert _suggest_field_name("", ["name"]) is None
    assert _suggest_field_name("name", []) is None


def test_suggest_field_name_short_typo_filter() -> None:
    assert _suggest_field_name("n", ["name", "value"]) is None


# --- _validate_tool_schema tests ---


def test_validate_skips_none_schema() -> None:
    assert _validate_tool_schema("x", {"name": "foo"}, None) is None


def test_validate_skips_empty_schema() -> None:
    assert _validate_tool_schema("x", {"name": "foo"}, {}) is None


def test_validate_skips_no_properties() -> None:
    assert _validate_tool_schema("x", {"name": "foo"}, {"type": "object"}) is None


def test_validate_skips_empty_properties() -> None:
    assert _validate_tool_schema("x", {"name": "foo"}, {"type": "object", "properties": {}}) is None


def test_validate_blocks_missing_required() -> None:
    schema = {"required": ["name"], "properties": {"name": {"type": "string"}}, "additionalProperties": False}
    result = _validate_tool_schema("test__strict", {"skill_id": 8}, schema)
    assert result is not None
    assert result.block is True
    assert result.block_payload is not None
    assert "name" in result.block_payload["missing_required"]
    assert "skill_id" in result.block_payload["received_keys"]


def test_validate_blocks_unknown_field_with_additional_properties_false() -> None:
    schema = {"additionalProperties": False, "properties": {"name": {"type": "string"}}}
    result = _validate_tool_schema("test__strict", {"skill_id": 8, "name": "foo"}, schema)
    assert result is not None
    assert result.block is True
    assert result.block_payload is not None
    assert "skill_id" in result.block_payload["received_keys"]
    assert "name" in result.block_payload["allowed_keys"]
    assert result.block_payload["missing_required"] == []


def test_validate_allows_unknown_field_without_additional_properties_false() -> None:
    schema = {"properties": {"name": {"type": "string"}}}
    result = _validate_tool_schema("test__strict", {"skill_id": 8, "name": "foo"}, schema)
    assert result is None


def test_validate_passes_valid_args() -> None:
    schema = {
        "required": ["name"],
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}},
    }
    result = _validate_tool_schema("test__strict", {"name": "foo"}, schema)
    assert result is None


def test_validate_blocks_both_missing_and_unknown() -> None:
    schema = {
        "required": ["name"],
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}},
    }
    result = _validate_tool_schema("test__strict", {"skill_id": 8}, schema)
    assert result is not None
    assert result.block is True
    payload = result.block_payload
    assert payload is not None
    assert "name" in payload["missing_required"]
    assert "skill_id" in payload["received_keys"]


def test_validate_treats_empty_string_as_missing() -> None:
    schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
    result = _validate_tool_schema("test__strict", {"name": ""}, schema)
    assert result is not None
    assert "name" in result.block_payload["missing_required"]


def test_validate_treats_none_as_missing() -> None:
    schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
    result = _validate_tool_schema("test__strict", {"name": None}, schema)
    assert result is not None
    assert "name" in result.block_payload["missing_required"]


def test_validate_includes_suggestion_in_message() -> None:
    schema = {"additionalProperties": False, "properties": {"name": {"type": "string"}}}
    result = _validate_tool_schema("test__strict", {"nme": "foo"}, schema)
    assert result is not None
    assert "Did you mean 'name'" in result.block_error


def test_validate_no_suggestion_when_far() -> None:
    schema = {"additionalProperties": False, "properties": {"name": {"type": "string"}}}
    result = _validate_tool_schema("test__strict", {"xyz": "foo"}, schema)
    assert result is not None
    assert "Did you mean" not in result.block_error


# --- Integration test: SchemaValidationGuard via AgentRun ---


def test_schema_validation_guard_blocks_via_agent_run() -> None:
    llm = _FakeLlm(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "test__strict",
                            "arguments": json.dumps({"skill_id": 8}),
                        },
                    }
                ],
            },
            {"content": "The tool reported a schema error.", "tool_calls": None},
        ]
    )
    host = _FakeHost(llm)
    strict_tool = _StrictTool()
    host.tools.register(strict_tool)
    run = AgentRun(host)

    async def _go() -> None:
        text, trace = await run.run(
            scope_for_tools="telegram:1",
            messages=[{"role": "user", "content": "call strict tool"}],
            tools=[{"type": "function", "function": {"name": "test__strict"}}],
        )
        assert "schema error" in text.lower() or "schema_mismatch" in text.lower() or "missing required" in text.lower()
        assert len(strict_tool._call_log) == 0
        assert trace[0]["name"] == "test__strict"
        assert "schema_mismatch" in trace[0]["result_preview"]

    asyncio.run(_go())


def test_default_guards_include_schema_validation() -> None:
    llm = _FakeLlm([{"content": "ok", "tool_calls": None}])
    host = _FakeHost(llm)
    run = AgentRun(host)

    schema_guard_count = sum(1 for guard in run._tool_guards if isinstance(guard, SchemaValidationGuard))
    assert schema_guard_count == 1
