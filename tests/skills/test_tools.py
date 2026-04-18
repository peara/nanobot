"""Tests for skill management tools."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from nanobot.skills import SkillStore, register_skill_tools
from nanobot.skills.tools import (
    SkillActivateTool,
    SkillCreateTool,
    SkillDeleteTool,
    SkillGetTool,
    SkillListTool,
    SkillUpdateTool,
)
from nanobot.tools.registry import ToolRegistry


class TestSkillListTool:
    @pytest.mark.asyncio
    async def test_list_active_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="skill-a", description="A", instructions="A", trigger_mode="always")
            store.create(name="skill-b", description="B", instructions="B", is_active=False)

            tool = SkillListTool(store)
            result = await tool.call({"active_only": True})
            data = json.loads(result)

            assert "skills" in data
            assert len(data["skills"]) == 1
            assert data["skills"][0]["name"] == "skill-a"

    @pytest.mark.asyncio
    async def test_list_all_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="skill-a", description="A", instructions="A")
            store.create(name="skill-b", description="B", instructions="B", is_active=False)

            tool = SkillListTool(store)
            result = await tool.call({"active_only": False})
            data = json.loads(result)

            assert len(data["skills"]) == 2


class TestSkillGetTool:
    @pytest.mark.asyncio
    async def test_get_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="test-skill", description="Test", instructions="Test instructions")

            tool = SkillGetTool(store)
            result = await tool.call({"name": "test-skill"})
            data = json.loads(result)

            assert data["ok"] is True
            assert data["skill"]["name"] == "test-skill"

    @pytest.mark.asyncio
    async def test_get_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillGetTool(store)

            result = await tool.call({"name": "nonexistent"})
            data = json.loads(result)

            assert "error" in data


class TestSkillCreateTool:
    @pytest.mark.asyncio
    async def test_create_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillCreateTool(store)

            result = await tool.call(
                {
                    "name": "new-skill",
                    "description": "A new skill",
                    "instructions": "Do something specific",
                    "trigger_mode": "pattern",
                    "trigger_patterns": ["test|example"],
                    "priority": 5,
                }
            )
            data = json.loads(result)

            assert data["ok"] is True
            assert data["skill"]["name"] == "new-skill"
            assert data["skill"]["trigger_mode"] == "pattern"

    @pytest.mark.asyncio
    async def test_create_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillCreateTool(store)

            result = await tool.call({"name": "incomplete"})
            data = json.loads(result)

            assert "error" in data


class TestSkillUpdateTool:
    @pytest.mark.asyncio
    async def test_update_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="to-update", description="Old", instructions="Old")

            tool = SkillUpdateTool(store)
            result = await tool.call(
                {
                    "name": "to-update",
                    "description": "New description",
                    "priority": 10,
                }
            )
            data = json.loads(result)

            assert data["ok"] is True
            assert data["skill"]["description"] == "New description"
            assert data["skill"]["priority"] == 10


class TestSkillActivateTool:
    @pytest.mark.asyncio
    async def test_deactivate_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="to-deactivate", description="X", instructions="X", is_active=True)

            tool = SkillActivateTool(store)
            result = await tool.call({"name": "to-deactivate", "is_active": False})
            data = json.loads(result)

            assert data["ok"] is True
            assert data["is_active"] is False

    @pytest.mark.asyncio
    async def test_activate_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="to-activate", description="X", instructions="X", is_active=False)

            tool = SkillActivateTool(store)
            result = await tool.call({"name": "to-activate", "is_active": True})
            data = json.loads(result)

            assert data["is_active"] is True


class TestSkillDeleteTool:
    @pytest.mark.asyncio
    async def test_delete_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="to-delete", description="X", instructions="X")

            tool = SkillDeleteTool(store)
            result = await tool.call({"name": "to-delete"})
            data = json.loads(result)

            assert data["ok"] is True
            assert data["deleted"] == "to-delete"

            assert store.get_by_name("to-delete") is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillDeleteTool(store)

            result = await tool.call({"name": "nonexistent"})
            data = json.loads(result)

            assert "error" in data


class TestRegisterSkillTools:
    def test_register_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            registry = ToolRegistry()

            register_skill_tools(registry, store)

            assert registry.has("skill__list")
            assert registry.has("skill__get")
            assert registry.has("skill__create")
            assert registry.has("skill__update")
            assert registry.has("skill__activate")
            assert registry.has("skill__delete")
