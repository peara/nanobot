from __future__ import annotations

import sqlite3

import pytest

from nanobot.browser.execution_budget import ExecutionBudget
from nanobot.browser.selector_resolver import SelectorResolver
from nanobot.scripts.registry import ScriptRegistry


class _FakeLocator:
    def __init__(self, count_value: int, mapping: dict[str, int]) -> None:
        self._count_value = count_value
        self._mapping = mapping

    async def count(self) -> int:
        return self._count_value

    @property
    def first(self) -> _FakeLocator:
        return self

    def nth(self, index: int) -> _FakeLocator:
        del index
        return self

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._mapping.get(selector, 0), self._mapping)


class _FakeContext:
    def __init__(self, mapping: dict[str, int]) -> None:
        self._mapping = mapping

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._mapping.get(selector, 0), self._mapping)


@pytest.mark.asyncio
async def test_selector_fallback_updates_selector_stats(tmp_path) -> None:
    db_path = str(tmp_path / "scripts.db")
    registry = ScriptRegistry(db_path)

    code = """
def script(browser, params):
    return {\"ok\": True}
"""
    script_id, _ = registry.create_script(
        name="fallback",
        description="fallback",
        domain="example.com",
        task_type="extraction",
        code=code,
        params_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        selector_manifest={"title": [".wrong", ".right"]},
        validation_rules=[],
        embedding_text="fallback",
        created_by="test",
    )

    traces: list[dict[str, str]] = []
    resolver = SelectorResolver(
        {"title": [".wrong", ".right"]},
        ExecutionBudget(),
        lambda trace: traces.append(trace),
        lambda key, selector, success: registry.update_selector_stat(script_id, key, selector, success),
    )

    context = _FakeContext({".wrong": 0, ".right": 1})
    located = await resolver.find(context, "title", "https://example.com")
    assert located is not None

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT selector, success_count, failure_count
            FROM selector_stats
            WHERE script_id = ?
            ORDER BY selector ASC
            """,
            (script_id,),
        ).fetchall()

    assert rows[0][0] == ".right"
    assert rows[0][1] == 1
    assert rows[1][0] == ".wrong"
    assert rows[1][2] == 1
    assert any(item["action"] == "selector_fallback_attempt" for item in traces)
