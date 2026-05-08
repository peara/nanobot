from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from nanobot.mcp_tools.create_script import create_script


class _FakeRegistry:
    def __init__(self) -> None:
        self.last_call: dict[str, Any] | None = None

    def create_script(self, **kwargs: Any) -> tuple[str, str]:
        self.last_call = kwargs
        return "scr_test", "ver_test"


def test_create_script_normalizes_selector_manifest_and_required_fields() -> None:
    registry = _FakeRegistry()
    runtime = SimpleNamespace(registry=registry)
    payload = {
        "name": "Extract GitHub Issues",
        "description": "desc",
        "code": "def script(browser, params):\n    return {'issues': []}",
        "params_schema": {"type": "object", "required": "url", "properties": {"url": {"type": "string"}}},
        "output_schema": {
            "type": "object",
            "required": "issues",
            "properties": {"issues": {"type": "array", "items": {"type": "object"}}},
        },
        "selector_manifest": {"issue_row": "div[id^='issue_']", "issue_title_link": ["a[data-hovercard-type='issue']"]},
        "embedding_text": "extract github issues",
        "created_by": "llm",
    }
    result = create_script(runtime, payload)
    assert result["status"] == "created"
    assert registry.last_call is not None
    assert registry.last_call["params_schema"]["required"] == ["url"]
    assert registry.last_call["output_schema"]["required"] == ["issues"]
    assert registry.last_call["selector_manifest"]["issue_row"] == ["div[id^='issue_']"]


def test_create_script_normalizes_loop_guard_lambda_before_ast_validation() -> None:
    registry = _FakeRegistry()
    runtime = SimpleNamespace(registry=registry)
    payload = {
        "name": "Extract GitHub Issues",
        "description": "desc",
        "code": (
            "def script(browser, params):\n"
            "    while browser.loop_guard(lambda: True):\n"
            "        break\n"
            "    return {'issues': []}"
        ),
        "params_schema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
        "output_schema": {
            "type": "object",
            "required": ["issues"],
            "properties": {"issues": {"type": "array", "items": {"type": "object"}}},
        },
        "selector_manifest": {"issue_row": ["div[id^='issue_']"]},
        "embedding_text": "extract github issues",
        "created_by": "llm",
    }
    result = create_script(runtime, payload)
    assert result["status"] == "created"
    assert registry.last_call is not None
    assert 'browser.loop_guard("pagination", max_iterations=20)' in registry.last_call["code"]


def test_create_script_converts_javascript_like_code_to_python_fallback() -> None:
    registry = _FakeRegistry()
    runtime = SimpleNamespace(registry=registry)
    payload = {
        "name": "Extract GitHub Issues",
        "description": "desc",
        "code": (
            "var result = [];\n"
            "while (browser.loop_guard(20)) {\n"
            "  var rows = browser.query_selector_all('a');\n"
            "}\n"
            "return result;"
        ),
        "params_schema": {"type": "object", "required": "repo_url", "properties": {"repo_url": {"type": "string"}}},
        "output_schema": {
            "type": "object",
            "required": "issues",
            "properties": {"issues": {"type": "array", "items": {"type": "object"}}},
        },
        "selector_manifest": {"issue_link": {"selector": "a[data-hovercard-type='issue']", "fallback": "a.Link--primary"}},
        "embedding_text": "extract github issues",
        "created_by": "llm",
    }
    result = create_script(runtime, payload)
    assert result["status"] == "created"
    assert registry.last_call is not None
    assert registry.last_call["code"].startswith("def script(browser, params):")
    assert registry.last_call["selector_manifest"]["issue_link"] == [
        "a[data-hovercard-type='issue']",
        "a.Link--primary",
    ]


def test_create_script_infers_schema_type_when_missing() -> None:
    registry = _FakeRegistry()
    runtime = SimpleNamespace(registry=registry)
    payload = {
        "name": "Extract GitHub Issues",
        "description": "desc",
        "code": "def script(browser, params):\n    return {'issues': []}",
        "params_schema": {"required": "url", "properties": {"url": {"type": "string"}}},
        "output_schema": {"properties": {"issues": {"type": "array", "items": {"type": "object"}}}},
        "selector_manifest": {"issue_row": ["div[id^='issue_']"]},
        "embedding_text": "extract github issues",
        "created_by": "llm",
    }
    result = create_script(runtime, payload)
    assert result["status"] == "created"
    assert registry.last_call is not None
    assert registry.last_call["params_schema"]["type"] == "object"
    assert registry.last_call["output_schema"]["type"] == "object"


def test_create_script_falls_back_for_invalid_schema_types() -> None:
    registry = _FakeRegistry()
    runtime = SimpleNamespace(registry=registry)
    payload = {
        "name": "Extract GitHub Issues",
        "description": "desc",
        "code": "def script(browser, params):\n    return {'issues': []}",
        "params_schema": "invalid",
        "output_schema": ["invalid"],
        "selector_manifest": {"issue_row": ["div[id^='issue_']"]},
        "embedding_text": "extract github issues",
        "created_by": "llm",
    }
    result = create_script(runtime, payload)
    assert result["status"] == "created"
    assert registry.last_call is not None
    assert registry.last_call["params_schema"]["type"] == "object"
    assert registry.last_call["output_schema"]["type"] == "object"
