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

            assert data["error"] == "skill_not_found"
            assert "nonexistent" in data["message"]
            assert "skill__list" in data["message"]

    @pytest.mark.asyncio
    async def test_get_missing_both_name_and_skill_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillGetTool(store)

            result = await tool.call({})
            data = json.loads(result)

            assert data["error"] == "invalid_argument"
            assert "name" in data["message"]
            assert "skill_id" in data["message"]
            assert data["received_keys"] == []


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

            assert data["error"] == "missing_required_parameter"
            assert "description" in data["message"]
            assert "instructions" in data["message"]
            assert sorted(data["missing_fields"]) == ["description", "instructions"]
            assert data["received_keys"] == ["name"]

    @pytest.mark.asyncio
    async def test_create_duplicate_name_returns_create_failed(self) -> None:
        # UNIQUE constraint on name triggers sqlite3.IntegrityError, which is
        # caught by the generic Exception branch -> create_failed envelope.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="dup", description="X", instructions="X")
            tool = SkillCreateTool(store)

            result = await tool.call({"name": "dup", "description": "Y", "instructions": "Y"})
            data = json.loads(result)

            assert data["error"] == "create_failed"
            assert "dup" in data["message"]
            assert "store reported an error" in data["message"]


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

    @pytest.mark.asyncio
    async def test_update_not_found_uses_standardized_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillUpdateTool(store)

            result = await tool.call({"name": "ghost", "description": "X"})
            data = json.loads(result)

            assert data["error"] == "skill_not_found"
            assert "ghost" in data["message"]
            assert "skill__list" in data["message"]

    @pytest.mark.asyncio
    async def test_update_missing_name_returns_schema_mismatch(self) -> None:
        # Regression: empty 'name' must NOT look like 'skill not found' to the LLM.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillUpdateTool(store)

            result = await tool.call({})
            data = json.loads(result)

            assert data["error"] == "missing_required_parameter"
            assert "name" in data["message"]
            assert data["received_keys"] == []

    @pytest.mark.asyncio
    async def test_update_with_skill_id_field_still_reports_missing_name(self) -> None:
        # Mirrors the 2026-06-02 incident pattern: LLM sends the wrong field
        # name, schema drops it, tool must say 'missing name' not 'not found'.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillUpdateTool(store)

            result = await tool.call({"skill_id": 8, "description": "X"})
            data = json.loads(result)

            assert data["error"] == "missing_required_parameter"
            assert sorted(data["received_keys"]) == ["description", "skill_id"]


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

    @pytest.mark.asyncio
    async def test_activate_not_found_uses_standardized_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillActivateTool(store)

            result = await tool.call({"name": "ghost"})
            data = json.loads(result)

            assert data["error"] == "skill_not_found"
            assert "ghost" in data["message"]
            assert "skill__list" in data["message"]

    @pytest.mark.asyncio
    async def test_activate_missing_name_returns_schema_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillActivateTool(store)

            result = await tool.call({})
            data = json.loads(result)

            assert data["error"] == "missing_required_parameter"
            assert "name" in data["message"]
            assert data["received_keys"] == []


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

            assert data["error"] == "skill_not_found"
            assert "nonexistent" in data["message"]

    @pytest.mark.asyncio
    async def test_delete_empty_name_returns_schema_mismatch(self) -> None:
        # Regression: empty 'name' must NOT look like 'skill not found' to the LLM,
        # otherwise it blames the system instead of its own input shape.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="real-skill", description="X", instructions="X")
            tool = SkillDeleteTool(store)

            result = await tool.call({})
            data = json.loads(result)

            assert data["error"] == "missing_required_parameter"
            assert "name" in data["message"]
            assert data["received_keys"] == []
            assert store.get_by_name("real-skill") is not None

    @pytest.mark.asyncio
    async def test_delete_with_unknown_field_still_reports_missing_name(self) -> None:
        # Regression for the 2026-06-02 incident: LLM sent {"skill_id": 8},
        # schema dropped it, tool received empty name. Error must help the LLM
        # discover that 'name' (not 'skill_id') is the right field.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillDeleteTool(store)

            result = await tool.call({"skill_id": 8})
            data = json.loads(result)

            assert data["error"] == "missing_required_parameter"
            assert data["received_keys"] == ["skill_id"]
            assert "name" in data["message"]

    @pytest.mark.asyncio
    async def test_delete_whitespace_name_treated_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            tool = SkillDeleteTool(store)

            result = await tool.call({"name": "   "})
            data = json.loads(result)

            assert data["error"] == "missing_required_parameter"


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

    def test_skill_tool_descriptions_separate_workflow_from_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))

            create_description = SkillCreateTool(store).description.lower()
            update_description = SkillUpdateTool(store).description.lower()
            instructions_description = (
                SkillCreateTool(store).schema["properties"]["instructions"]["description"].lower()
            )

            assert "workflow" in create_description
            assert "parameter mapping" in create_description
            assert "formatting" in create_description
            assert "web__create_script" in create_description
            assert "executable scraping code" in create_description
            assert "executable extraction code" in update_description
            assert "do not include executable scraping code" in instructions_description
