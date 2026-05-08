from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import nanobot.scripts.executor as executor_module
from nanobot.scripts.executor import ScriptExecutor
from nanobot.scripts.models import RuntimeBudgetLimits
from nanobot.scripts.registry import ScriptRegistry


@dataclass
class _FakeNode:
    text: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    visible: bool = True
    children: dict[str, list["_FakeNode"]] = field(default_factory=dict)


class _FakeLocator:
    def __init__(self, nodes: list[_FakeNode]) -> None:
        self._nodes = nodes

    async def count(self) -> int:
        return len(self._nodes)

    @property
    def first(self) -> _FakeLocator:
        return _FakeLocator(self._nodes[:1])

    def nth(self, index: int) -> _FakeLocator:
        if 0 <= index < len(self._nodes):
            return _FakeLocator([self._nodes[index]])
        return _FakeLocator([])

    def locator(self, selector: str) -> _FakeLocator:
        nested: list[_FakeNode] = []
        for node in self._nodes:
            nested.extend(node.children.get(selector, []))
        return _FakeLocator(nested)

    async def inner_text(self) -> str:
        if not self._nodes:
            return ""
        return self._nodes[0].text

    async def get_attribute(self, name: str) -> str | None:
        if not self._nodes:
            return None
        return self._nodes[0].attrs.get(name)

    async def click(self) -> None:
        return None

    async def is_visible(self) -> bool:
        if not self._nodes:
            return False
        return self._nodes[0].visible


class _FakePage:
    def __init__(self, root_mapping: dict[str, list[_FakeNode]]) -> None:
        self._root_mapping = root_mapping
        self.url = "about:blank"

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._root_mapping.get(selector, []))

    async def wait_for_load_state(self, state: str, timeout: int = 10000) -> None:
        del state, timeout
        return None

    async def wait_for_selector(self, selector: str, timeout: int = 5000) -> None:
        del timeout
        if not self._root_mapping.get(selector):
            raise TimeoutError(selector)


class _FakeBrowserInteractor:
    def __init__(self, headless: bool = True, root_mapping: dict[str, list[_FakeNode]] | None = None) -> None:
        del headless
        self.page = _FakePage(root_mapping or {})

    async def __aenter__(self) -> _FakeBrowserInteractor:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def open(self, url: str) -> None:
        self.page.url = url

    async def snapshot(self) -> dict[str, Any]:
        return {"url": self.page.url}


SCRIPT_CODE = """
def script(browser, params):
    browser.goto(params[\"url\"])
    issues = []
    rows = browser.find_all(\"issue_row\")
    for row in rows:
        title_el = row.find(\"issue_title\")
        if not title_el:
            continue
        issues.append({
            \"title\": title_el.text(),
            \"url\": title_el.attr(\"href\"),
        })
    return {\"issues\": issues}
"""

PARAMS_SCHEMA = {
    "type": "object",
    "required": ["url"],
    "properties": {"url": {"type": "string"}},
}

OUTPUT_SCHEMA = {
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

MANIFEST = {
    "issue_row": [".wrong_row", ".row"],
    "issue_title": [".title"],
}


@pytest.mark.asyncio
async def test_invoke_existing_script_and_persist_trace(tmp_path, monkeypatch) -> None:
    row1 = _FakeNode(children={".title": [_FakeNode(text="Issue 1", attrs={"href": "https://example.com/1"})]})
    row2 = _FakeNode(children={".title": [_FakeNode(text="Issue 2", attrs={"href": "https://example.com/2"})]})
    mapping = {".wrong_row": [], ".row": [row1, row2]}

    monkeypatch.setattr(
        executor_module,
        "BrowserInteractor",
        lambda headless=True: _FakeBrowserInteractor(headless, mapping),
    )

    registry = ScriptRegistry(str(tmp_path / "scripts.db"))
    script_id, _ = registry.create_script(
        name="Extract Issues",
        description="Extract GitHub issues",
        domain="github.com",
        task_type="extraction",
        code=SCRIPT_CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        embedding_text="get issues",
        created_by="test",
    )

    executor = ScriptExecutor(registry, headless=True)
    result = await executor.invoke(script_id, {"url": "https://github.com/org/repo/issues"})

    assert result["status"] == "success"
    assert result["result"]["issues"][0]["title"] == "Issue 1"
    assert result["execution_id"] is not None
    assert registry.count_execution_traces(str(result["execution_id"])) > 0


@pytest.mark.asyncio
async def test_invoke_params_schema_invalid(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        executor_module,
        "BrowserInteractor",
        lambda headless=True: _FakeBrowserInteractor(headless, {}),
    )

    registry = ScriptRegistry(str(tmp_path / "scripts.db"))
    script_id, _ = registry.create_script(
        name="Extract Issues",
        description="Extract GitHub issues",
        domain="github.com",
        task_type="extraction",
        code=SCRIPT_CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        embedding_text="get issues",
        created_by="test",
    )

    executor = ScriptExecutor(registry, headless=True)
    result = await executor.invoke(script_id, {})

    assert result["status"] == "failed"
    assert result["error"]["type"] == "PARAMS_VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_invoke_output_suspicious(tmp_path, monkeypatch) -> None:
    mapping = {".wrong_row": [], ".row": []}
    monkeypatch.setattr(
        executor_module,
        "BrowserInteractor",
        lambda headless=True: _FakeBrowserInteractor(headless, mapping),
    )

    registry = ScriptRegistry(str(tmp_path / "scripts.db"))
    script_id, _ = registry.create_script(
        name="Extract Issues",
        description="Extract GitHub issues",
        domain="github.com",
        task_type="extraction",
        code=SCRIPT_CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        embedding_text="get issues",
        created_by="test",
    )

    executor = ScriptExecutor(registry, headless=True, budget_limits=RuntimeBudgetLimits(max_output_items=1000))
    result = await executor.invoke(script_id, {"url": "https://github.com/org/repo/issues"})

    assert result["status"] == "suspicious"
    assert result["execution_id"] is not None
