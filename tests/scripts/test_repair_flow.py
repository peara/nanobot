from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import nanobot.scripts.executor as executor_module
from nanobot.scripts.executor import ScriptExecutor
from nanobot.scripts.registry import ScriptRegistry
from nanobot.scripts.repair import ScriptRepairService


@dataclass
class _FakeNode:
    text: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
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
        return self._nodes[0].text if self._nodes else ""

    async def get_attribute(self, name: str) -> str | None:
        if not self._nodes:
            return None
        return self._nodes[0].attrs.get(name)

    async def click(self) -> None:
        return None

    async def is_visible(self) -> bool:
        return bool(self._nodes)


class _FakePage:
    def __init__(self, root_mapping: dict[str, list[_FakeNode]]) -> None:
        self._root_mapping = root_mapping
        self.url = "about:blank"

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._root_mapping.get(selector, []))

    async def wait_for_load_state(self, state: str, timeout: int = 10000) -> None:
        del state, timeout

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


BASE_CODE = """
def script(browser, params):
    browser.goto(params[\"url\"])
    return {\"issues\": []}
"""

PATCHED_GOOD = """
def script(browser, params):
    browser.goto(params[\"url\"])
    rows = browser.find_all(\"issue_row\")
    issues = []
    for row in rows:
        title_el = row.find(\"issue_title\")
        if not title_el:
            continue
        issues.append({\"title\": title_el.text(), \"url\": title_el.attr(\"href\")})
    return {\"issues\": issues}
"""

PATCHED_BAD = """
def script(browser, params):
    browser.goto(params[\"url\"])
    return {\"issues\": [{\"title\": \"missing_url\"}]}
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

MANIFEST = {"issue_row": [".row"], "issue_title": [".title"]}


@pytest.mark.asyncio
async def test_repair_candidate_fail_does_not_replace_current(tmp_path, monkeypatch) -> None:
    row = _FakeNode(children={".title": [_FakeNode(text="Issue 1", attrs={"href": "https://example.com/1"})]})
    mapping = {".row": [row]}
    monkeypatch.setattr(
        executor_module,
        "BrowserInteractor",
        lambda headless=True: _FakeBrowserInteractor(headless, mapping),
    )

    registry = ScriptRegistry(str(tmp_path / "scripts.db"))
    script_id, v1 = registry.create_script(
        name="Repair",
        description="Repair",
        domain="example.com",
        task_type="extraction",
        code=BASE_CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        embedding_text="repair",
        created_by="test",
    )

    executor = ScriptExecutor(registry, headless=True)
    failed = await executor.invoke(script_id, {"url": "https://example.com"})
    assert failed["status"] == "suspicious"

    failed_execution = registry.create_execution(
        script_id=script_id,
        version_id=v1,
        params={"url": "https://example.com"},
        status="failed",
        result=None,
        error_type="SCRIPT_RUNTIME_ERROR",
        error_message="boom",
        duration_ms=1,
        dom_query_count=0,
        page_count=1,
        click_count=0,
        output_item_count=0,
        confidence=0.4,
    )

    service = ScriptRepairService(registry, executor)
    result = await service.repair(
        script_id=script_id,
        failed_execution_id=failed_execution,
        patched_code=PATCHED_BAD,
        patched_selector_manifest=MANIFEST,
        changelog="bad patch",
        test_cases=[{"params": {"url": "https://example.com"}}],
    )

    assert result["status"] == "failed"
    current = registry.get_script_version(script_id)
    assert current is not None
    assert current.version_id == v1


@pytest.mark.asyncio
async def test_repair_candidate_promote_on_pass(tmp_path, monkeypatch) -> None:
    row = _FakeNode(children={".title": [_FakeNode(text="Issue 1", attrs={"href": "https://example.com/1"})]})
    mapping = {".row": [row]}
    monkeypatch.setattr(
        executor_module,
        "BrowserInteractor",
        lambda headless=True: _FakeBrowserInteractor(headless, mapping),
    )

    registry = ScriptRegistry(str(tmp_path / "scripts.db"))
    script_id, v1 = registry.create_script(
        name="Repair",
        description="Repair",
        domain="example.com",
        task_type="extraction",
        code=BASE_CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        embedding_text="repair",
        created_by="test",
    )

    executor = ScriptExecutor(registry, headless=True)
    failed_execution = registry.create_execution(
        script_id=script_id,
        version_id=v1,
        params={"url": "https://example.com"},
        status="failed",
        result=None,
        error_type="SCRIPT_RUNTIME_ERROR",
        error_message="boom",
        duration_ms=1,
        dom_query_count=0,
        page_count=1,
        click_count=0,
        output_item_count=0,
        confidence=0.4,
    )

    service = ScriptRepairService(registry, executor)
    result = await service.repair(
        script_id=script_id,
        failed_execution_id=failed_execution,
        patched_code=PATCHED_GOOD,
        patched_selector_manifest=MANIFEST,
        changelog="good patch",
        test_cases=[{"params": {"url": "https://example.com"}}],
    )

    assert result["status"] == "promoted"
    current = registry.get_script_version(script_id)
    assert current is not None
    assert current.version_id == result["new_version_id"]
