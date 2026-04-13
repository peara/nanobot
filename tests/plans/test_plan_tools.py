from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nanobot.plans import PlanStore
from nanobot.plans.tools import (
    PlanAddStepTool,
    PlanGetTool,
    PlanUpdateTool,
    register_plan_tools,
)
from nanobot.tools.registry import ToolRegistry


def _make_store(tmp_path: Path) -> PlanStore:
    db_path = str(tmp_path / "plans.db")
    return PlanStore(db_path)


@pytest.mark.asyncio
async def test_plan_get_tool_returns_plan():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        plan = store.create(name="Test Plan", goal="Test Goal")

        tool = PlanGetTool(store)
        result = await tool.call({"plan_id": plan.id})

        assert str(plan.id) in result
        assert "Test Goal" in result


@pytest.mark.asyncio
async def test_plan_get_tool_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))

        tool = PlanGetTool(store)
        result = await tool.call({"plan_id": 999})

        assert "error" in result


@pytest.mark.asyncio
async def test_plan_update_tool_updates_constraints():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        plan = store.create(name="Test Plan", goal="Test Goal")

        tool = PlanUpdateTool(store)
        result = await tool.call(
            {
                "plan_id": plan.id,
                "constraints": ["new constraint"],
            }
        )

        assert "ok" in result
        updated = store.get(plan.id)
        assert updated is not None
        assert "new constraint" in updated.constraints


@pytest.mark.asyncio
async def test_plan_update_tool_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))

        tool = PlanUpdateTool(store)
        result = await tool.call(
            {
                "plan_id": 999,
                "constraints": ["constraint"],
            }
        )

        assert "error" in result


@pytest.mark.asyncio
async def test_plan_add_step_tool_appends_step():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        plan = store.create(name="Test Plan", goal="Test Goal")

        tool = PlanAddStepTool(store)
        result = await tool.call(
            {
                "plan_id": plan.id,
                "description": "New step",
                "tool_hint": "web_search",
            }
        )

        assert "ok" in result
        updated = store.get(plan.id)
        assert updated is not None
        assert updated.steps is not None
        assert len(updated.steps) == 1
        assert updated.steps[0]["description"] == "New step"
        assert updated.steps[0]["tool_hint"] == "web_search"


@pytest.mark.asyncio
async def test_plan_add_step_tool_no_tool_hint():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        plan = store.create(name="Test Plan", goal="Test Goal")

        tool = PlanAddStepTool(store)
        result = await tool.call(
            {
                "plan_id": plan.id,
                "description": "Step without hint",
            }
        )

        assert "ok" in result
        updated = store.get(plan.id)
        assert updated is not None
        assert updated.steps is not None
        assert "tool_hint" not in updated.steps[0]


@pytest.mark.asyncio
async def test_plan_add_step_tool_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))

        tool = PlanAddStepTool(store)
        result = await tool.call(
            {
                "plan_id": 999,
                "description": "Step",
            }
        )

        assert "error" in result


def test_register_plan_tools():
    registry = ToolRegistry()

    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        register_plan_tools(registry, store)

        assert registry.has("plan__get")
        assert registry.has("plan__update")
        assert registry.has("plan__add_step")
