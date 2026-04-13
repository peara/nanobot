from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry
from nanobot.tools.stats import ToolStatsStore


class _FakeTool(Tool):
    def __init__(self, name: str, result: str = "ok", delay_ms: int = 0) -> None:
        self._name = name
        self._result = result
        self._delay_ms = delay_ms

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake tool {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        if self._delay_ms > 0:
            await asyncio.sleep(self._delay_ms / 1000)
        if self._result == "ERROR":
            raise RuntimeError("Tool failed")
        return self._result


def test_tool_stats_store_records_call(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)

    store.record_call(
        scope="telegram:123",
        tool_name="timer__time_now",
        started_at="2026-04-11T10:00:00+00:00",
        duration_ms=42,
        success=True,
    )

    calls = store.get_calls(scope="telegram:123")
    assert len(calls) == 1
    call = calls[0]
    assert call["scope"] == "telegram:123"
    assert call["tool_name"] == "timer__time_now"
    assert call["duration_ms"] == 42
    assert call["success"] == 1


def test_tool_stats_store_records_failed_call(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)

    store.record_call(
        scope="telegram:123",
        tool_name="playwright__browser_navigate",
        started_at="2026-04-11T10:00:00+00:00",
        duration_ms=5000,
        success=False,
        error_preview="Connection timeout",
    )

    calls = store.get_calls(scope="telegram:123")
    assert len(calls) == 1
    assert calls[0]["success"] == 0
    assert calls[0]["error_preview"] == "Connection timeout"


def test_tool_stats_store_get_summary(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)

    store.record_call(
        scope="telegram:123",
        tool_name="timer__time_now",
        started_at="2026-04-11T10:00:00+00:00",
        duration_ms=10,
        success=True,
    )
    store.record_call(
        scope="telegram:123",
        tool_name="timer__time_now",
        started_at="2026-04-11T10:01:00+00:00",
        duration_ms=20,
        success=True,
    )
    store.record_call(
        scope="telegram:123",
        tool_name="timer__time_now",
        started_at="2026-04-11T10:02:00+00:00",
        duration_ms=30,
        success=False,
    )

    summary = store.get_summary(scope="telegram:123")
    assert len(summary) == 1
    tool_stats = summary[0]
    assert tool_stats["tool_name"] == "timer__time_now"
    assert tool_stats["call_count"] == 3
    assert tool_stats["success_count"] == 2
    assert tool_stats["fail_count"] == 1
    assert tool_stats["avg_duration_ms"] == 20.0


def test_tool_stats_store_filters_by_scope(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)

    store.record_call(
        scope="telegram:123",
        tool_name="timer__time_now",
        started_at="2026-04-11T10:00:00+00:00",
        duration_ms=10,
        success=True,
    )
    store.record_call(
        scope="telegram:456",
        tool_name="timer__time_now",
        started_at="2026-04-11T10:01:00+00:00",
        duration_ms=20,
        success=True,
    )

    calls_123 = store.get_calls(scope="telegram:123")
    calls_456 = store.get_calls(scope="telegram:456")
    assert len(calls_123) == 1
    assert len(calls_456) == 1
    assert calls_123[0]["duration_ms"] == 10
    assert calls_456[0]["duration_ms"] == 20


def test_tool_stats_store_filters_by_tool_name(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)

    store.record_call(
        scope="telegram:123",
        tool_name="timer__time_now",
        started_at="2026-04-11T10:00:00+00:00",
        duration_ms=10,
        success=True,
    )
    store.record_call(
        scope="telegram:123",
        tool_name="scheduler__list",
        started_at="2026-04-11T10:01:00+00:00",
        duration_ms=20,
        success=True,
    )

    timer_calls = store.get_calls(scope="telegram:123", tool_name="timer__time_now")
    assert len(timer_calls) == 1
    assert timer_calls[0]["tool_name"] == "timer__time_now"


def test_tool_stats_store_limit(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)

    for i in range(10):
        store.record_call(
            scope="telegram:123",
            tool_name="timer__time_now",
            started_at=f"2026-04-11T10:0{i}:00+00:00",
            duration_ms=i,
            success=True,
        )

    calls = store.get_calls(scope="telegram:123", limit=5)
    assert len(calls) == 5


def test_registry_records_stats_on_call(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)
    registry = ToolRegistry(stats_store=store)
    registry.register(_FakeTool("test__echo", result="hello"))

    result = asyncio.run(registry.call("test__echo", {"input": "hello"}, scope="telegram:123"))

    assert result == "hello"
    calls = store.get_calls(scope="telegram:123")
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "test__echo"
    assert calls[0]["success"] == 1
    assert calls[0]["input_preview"] is not None


def test_registry_records_stats_on_failure(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)
    registry = ToolRegistry(stats_store=store)
    registry.register(_FakeTool("test__fail", result="ERROR"))

    with pytest.raises(RuntimeError, match="Tool failed"):
        asyncio.run(registry.call("test__fail", {}, scope="telegram:123"))

    calls = store.get_calls(scope="telegram:123")
    assert len(calls) == 1
    assert calls[0]["success"] == 0
    assert "Tool failed" in (calls[0]["error_preview"] or "")


def test_registry_without_stats_store_works() -> None:
    registry = ToolRegistry(stats_store=None)
    registry.register(_FakeTool("test__echo", result="ok"))

    result = asyncio.run(registry.call("test__echo", {}, scope="telegram:123"))

    assert result == "ok"


def test_registry_does_not_record_without_scope(tmp_path) -> None:
    db_path = str(tmp_path / "stats.db")
    store = ToolStatsStore(db_path)
    registry = ToolRegistry(stats_store=store)
    registry.register(_FakeTool("test__echo", result="hello"))

    result = asyncio.run(registry.call("test__echo", {}))

    assert result == "hello"
    calls = store.get_calls()
    assert len(calls) == 0
