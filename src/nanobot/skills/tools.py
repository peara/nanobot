from __future__ import annotations

import json
import logging
from typing import Any

from nanobot.skills.store import SkillStore
from nanobot.tools.base import Tool

logger = logging.getLogger(__name__)


class SkillListTool(Tool):
    def __init__(self, skill_store: SkillStore) -> None:
        self._store = skill_store

    @property
    def name(self) -> str:
        return "skill__list"

    @property
    def description(self) -> str:
        return "List all skills with brief descriptions. Shows active skills by default."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "Only show active skills (default: true)",
                }
            },
            "additionalProperties": False,
        }

    async def call(self, args: dict[str, Any]) -> str:
        active_only = args.get("active_only", True)
        if active_only:
            skills = self._store.list_active()
        else:
            skills = self._store.list_all()

        summaries: list[dict[str, Any]] = []
        for skill in skills:
            desc = skill.description or ""
            summaries.append(
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": (desc[:100] + "...") if len(desc) > 100 else desc,
                    "trigger_mode": skill.trigger_mode,
                    "priority": skill.priority,
                    "is_active": skill.is_active,
                }
            )

        return json.dumps({"skills": summaries}, ensure_ascii=True)


class SkillGetTool(Tool):
    def __init__(self, skill_store: SkillStore) -> None:
        self._store = skill_store

    @property
    def name(self) -> str:
        return "skill__get"

    @property
    def description(self) -> str:
        return "Get full skill details by name or ID."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (required if no skill_id)",
                },
                "skill_id": {
                    "type": "integer",
                    "description": "Skill ID (required if no name)",
                },
            },
        }

    async def call(self, args: dict[str, Any]) -> str:
        name = args.get("name")
        skill_id = args.get("skill_id")

        if name:
            skill = self._store.get_by_name(name)
        elif skill_id:
            skill = self._store.get(int(skill_id))
        else:
            return json.dumps({"error": "Provide either name or skill_id"}, ensure_ascii=True)

        if skill is None:
            return json.dumps({"error": f"Skill not found: {name or skill_id}"}, ensure_ascii=True)

        return json.dumps({"ok": True, "skill": skill.as_dict()}, ensure_ascii=True)


class SkillCreateTool(Tool):
    def __init__(self, skill_store: SkillStore) -> None:
        self._store = skill_store

    @property
    def name(self) -> str:
        return "skill__create"

    @property
    def description(self) -> str:
        return "Create a new skill with trigger patterns for automatic activation."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique skill name (required)",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of when to use this skill (required)",
                },
                "instructions": {
                    "type": "string",
                    "description": "Full skill instructions to inject into context (required)",
                },
                "trigger_mode": {
                    "type": "string",
                    "enum": ["always", "pattern", "intelligent"],
                    "description": "How skill is activated (default: pattern)",
                },
                "trigger_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Regex patterns for pattern mode",
                },
                "priority": {
                    "type": "integer",
                    "description": "Higher priority skills are loaded first (default: 0)",
                },
            },
            "required": ["name", "description", "instructions"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", ""))
        description = str(args.get("description", ""))
        instructions = str(args.get("instructions", ""))
        trigger_mode = str(args.get("trigger_mode", "pattern"))
        trigger_patterns = args.get("trigger_patterns")
        priority = int(args.get("priority", 0))

        if not name or not description or not instructions:
            return json.dumps({"error": "name, description, and instructions are required"}, ensure_ascii=True)

        if trigger_patterns and not isinstance(trigger_patterns, list):
            trigger_patterns = [str(trigger_patterns)]

        try:
            skill = self._store.create(
                name=name,
                description=description,
                instructions=instructions,
                trigger_mode=trigger_mode,
                trigger_patterns=trigger_patterns,
                priority=priority,
                is_active=True,
            )
            return json.dumps({"ok": True, "skill": skill.as_dict()}, ensure_ascii=True)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=True)
        except Exception as e:
            return json.dumps({"error": f"Failed to create skill: {e}"}, ensure_ascii=True)


class SkillUpdateTool(Tool):
    def __init__(self, skill_store: SkillStore) -> None:
        self._store = skill_store

    @property
    def name(self) -> str:
        return "skill__update"

    @property
    def description(self) -> str:
        return "Update an existing skill by name."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name to update (required)",
                },
                "description": {
                    "type": "string",
                    "description": "New description",
                },
                "instructions": {
                    "type": "string",
                    "description": "New instructions",
                },
                "trigger_mode": {
                    "type": "string",
                    "enum": ["always", "pattern", "intelligent"],
                    "description": "New trigger mode",
                },
                "trigger_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New trigger patterns",
                },
                "priority": {
                    "type": "integer",
                    "description": "New priority",
                },
                "is_active": {
                    "type": "boolean",
                    "description": "Activate or deactivate",
                },
            },
            "required": ["name"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", ""))

        existing = self._store.get_by_name(name)
        if existing is None:
            return json.dumps({"error": f"Skill not found: {name}"}, ensure_ascii=True)

        trigger_patterns = args.get("trigger_patterns")
        if trigger_patterns and not isinstance(trigger_patterns, list):
            trigger_patterns = [str(trigger_patterns)]

        updated = self._store.update(
            existing.id,
            description=args.get("description"),
            instructions=args.get("instructions"),
            trigger_mode=args.get("trigger_mode"),
            trigger_patterns=trigger_patterns,
            priority=args.get("priority"),
            is_active=args.get("is_active"),
        )

        if updated is None:
            return json.dumps({"error": f"Failed to update skill: {name}"}, ensure_ascii=True)

        return json.dumps({"ok": True, "skill": updated.as_dict()}, ensure_ascii=True)


class SkillActivateTool(Tool):
    def __init__(self, skill_store: SkillStore) -> None:
        self._store = skill_store

    @property
    def name(self) -> str:
        return "skill__activate"

    @property
    def description(self) -> str:
        return "Activate or deactivate a skill by name."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (required)",
                },
                "is_active": {
                    "type": "boolean",
                    "description": "True to activate, False to deactivate (default: true)",
                },
            },
            "required": ["name"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", ""))
        is_active = args.get("is_active", True)

        existing = self._store.get_by_name(name)
        if existing is None:
            return json.dumps({"error": f"Skill not found: {name}"}, ensure_ascii=True)

        updated = self._store.set_active(existing.id, bool(is_active))
        if updated is None:
            return json.dumps({"error": f"Failed to update skill: {name}"}, ensure_ascii=True)

        return json.dumps(
            {
                "ok": True,
                "skill": updated.name,
                "is_active": updated.is_active,
            },
            ensure_ascii=True,
        )


class SkillDeleteTool(Tool):
    def __init__(self, skill_store: SkillStore) -> None:
        self._store = skill_store

    @property
    def name(self) -> str:
        return "skill__delete"

    @property
    def description(self) -> str:
        return "Delete a skill by name."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name to delete (required)",
                },
            },
            "required": ["name"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", ""))

        deleted = self._store.delete_by_name(name)
        if not deleted:
            return json.dumps({"error": f"Skill not found: {name}"}, ensure_ascii=True)

        return json.dumps({"ok": True, "deleted": name}, ensure_ascii=True)


def register_skill_tools(registry: Any, skill_store: SkillStore) -> None:
    registry.register(SkillListTool(skill_store))
    registry.register(SkillGetTool(skill_store))
    registry.register(SkillCreateTool(skill_store))
    registry.register(SkillUpdateTool(skill_store))
    registry.register(SkillActivateTool(skill_store))
    registry.register(SkillDeleteTool(skill_store))
