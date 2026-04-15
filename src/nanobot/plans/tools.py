from __future__ import annotations

import json
import logging
from typing import Any

from nanobot.plans.store import PlanStore
from nanobot.tools.base import Tool

logger = logging.getLogger(__name__)


class PlanGetTool(Tool):
    def __init__(self, plan_store: PlanStore) -> None:
        self._store = plan_store

    @property
    def name(self) -> str:
        return "plan__get"

    @property
    def description(self) -> str:
        return "Get the execution plan by ID. Review the plan structure, constraints, or steps."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "integer",
                    "description": "Plan ID to retrieve (required)",
                }
            },
            "required": ["plan_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        plan_id = int(args.get("plan_id", 0))
        plan = self._store.get(plan_id)
        if plan is None:
            return json.dumps({"error": f"Plan {plan_id} not found"}, ensure_ascii=True)
        return json.dumps(plan.as_dict(), ensure_ascii=True)


class PlanUpdateTool(Tool):
    def __init__(self, plan_store: PlanStore) -> None:
        self._store = plan_store

    @property
    def name(self) -> str:
        return "plan__update"

    @property
    def description(self) -> str:
        return (
            "Update the execution plan with learned constraints, refined steps, or additional notes. "
            "Use when you discover new requirements or an approach isn't working."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "integer",
                    "description": "Plan ID to update (required)",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New constraints to add to the plan",
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Updated execution steps",
                },
                "notes": {
                    "type": "string",
                    "description": "Additional notes or learnings",
                },
            },
            "required": ["plan_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        plan_id = int(args.get("plan_id", 0))
        constraints = args.get("constraints")
        steps = args.get("steps")
        notes = args.get("notes")

        updated = self._store.update(
            plan_id,
            constraints=constraints,
            steps=steps,
            notes=notes,
            increment_version=True,
        )
        if updated is None:
            return json.dumps({"error": f"Plan {plan_id} not found"}, ensure_ascii=True)
        return json.dumps({"ok": True, "plan": updated.as_dict()}, ensure_ascii=True)


class PlanAddStepTool(Tool):
    def __init__(self, plan_store: PlanStore) -> None:
        self._store = plan_store

    @property
    def name(self) -> str:
        return "plan__add_step"

    @property
    def description(self) -> str:
        return "Add an execution step to the plan. Use when you need to break down the execution into smaller tasks."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "integer",
                    "description": "Plan ID to add step to (required)",
                },
                "description": {
                    "type": "string",
                    "description": "Step description",
                },
                "tool_hint": {
                    "type": "string",
                    "description": "Optional hint about which tool to use for this step",
                },
            },
            "required": ["plan_id", "description"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        plan_id = int(args.get("plan_id", 0))
        description = str(args.get("description", ""))
        tool_hint = args.get("tool_hint")

        plan = self._store.get(plan_id)
        if plan is None:
            return json.dumps({"error": f"Plan {plan_id} not found"}, ensure_ascii=True)

        step = {"description": description}
        if tool_hint:
            step["tool_hint"] = tool_hint

        current_steps = plan.steps or []
        current_steps.append(step)

        updated = self._store.update(plan_id, steps=current_steps, increment_version=True)
        if updated is None:
            return json.dumps({"error": f"Failed to update plan {plan_id}"}, ensure_ascii=True)
        return json.dumps({"ok": True, "plan": updated.as_dict()}, ensure_ascii=True)


class PlanEditStepTool(Tool):
    def __init__(self, plan_store: PlanStore) -> None:
        self._store = plan_store

    @property
    def name(self) -> str:
        return "plan__edit_step"

    @property
    def description(self) -> str:
        return "Edit an existing execution step in the plan by index."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "integer",
                    "description": "Plan ID to edit step in (required)",
                },
                "step_index": {
                    "type": "integer",
                    "description": "0-based index of the step to edit (required)",
                },
                "description": {
                    "type": "string",
                    "description": "New step description",
                },
                "tool_hint": {
                    "type": "string",
                    "description": "Optional hint about which tool to use for this step",
                },
            },
            "required": ["plan_id", "step_index", "description"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        plan_id = int(args.get("plan_id", 0))
        step_index = int(args.get("step_index", -1))
        description = str(args.get("description", ""))
        tool_hint = args.get("tool_hint")

        plan = self._store.get(plan_id)
        if plan is None:
            return json.dumps({"error": f"Plan {plan_id} not found"}, ensure_ascii=True)

        steps = plan.steps or []
        if step_index < 0 or step_index >= len(steps):
            return json.dumps({"error": f"Invalid step_index {step_index} for plan {plan_id}"}, ensure_ascii=True)

        existing = steps[step_index] if isinstance(steps[step_index], dict) else {}
        new_step: dict[str, Any] = {"description": description}
        if tool_hint is not None:
            new_step["tool_hint"] = tool_hint
        else:
            if isinstance(existing, dict) and "tool_hint" in existing:
                new_step["tool_hint"] = existing.get("tool_hint")

        steps[step_index] = new_step

        updated = self._store.update(plan_id, steps=steps, increment_version=True)
        if updated is None:
            return json.dumps({"error": f"Failed to update plan {plan_id}"}, ensure_ascii=True)
        return json.dumps({"ok": True, "plan": updated.as_dict()}, ensure_ascii=True)


def register_plan_tools(registry: Any, plan_store: PlanStore) -> None:
    registry.register(PlanGetTool(plan_store))
    registry.register(PlanUpdateTool(plan_store))
    registry.register(PlanAddStepTool(plan_store))
    registry.register(PlanEditStepTool(plan_store))
