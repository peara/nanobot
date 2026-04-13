from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand
from nanobot.core_utils import command_body
from nanobot.plans import process_plan

logger = logging.getLogger(__name__)


class PlanCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/plan"]

    async def handle(self, raw_text: str, scope: str) -> None:
        text = command_body(raw_text)
        if not text:
            await self.core._send(scope, "Usage: /plan <request> | /plan list | /plan show <id> | /plan delete <id>")
            return

        parts = text.split(None, 1)
        subcommand = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if subcommand == "list":
            await self._list_plans(scope)
        elif subcommand == "show":
            await self._show_plan(scope, arg)
        elif subcommand == "delete":
            await self._delete_plan(scope, arg)
        else:
            await process_plan(self.core, scope, raw_text)

    async def _list_plans(self, scope: str) -> None:
        plans = self.core.plan_store.list_plans(source_type="plan_command", limit=20)
        if not plans:
            await self.core._send(scope, "No plans found.")
            return

        lines = ["Saved plans:"]
        for plan in plans:
            goal_preview = plan.goal[:60] + ("..." if len(plan.goal) > 60 else "")
            lines.append(f"  [{plan.id}] {plan.name}")
            lines.append(f"      Goal: {goal_preview}")
            lines.append(f"      Stats: {plan.success_count} success, {plan.failure_count} failure")
        await self.core._send(scope, "\n".join(lines))

    async def _show_plan(self, scope: str, arg: str) -> None:
        if not arg:
            await self.core._send(scope, "Usage: /plan show <plan_id>")
            return

        try:
            plan_id = int(arg.strip())
        except ValueError:
            await self.core._send(scope, "Plan ID must be a number.")
            return

        plan = self.core.plan_store.get(plan_id)
        if not plan:
            await self.core._send(scope, f"Plan {plan_id} not found.")
            return

        lines = [
            f"Plan {plan.id}: {plan.name}",
            f"Goal: {plan.goal}",
            f"Constraints: {', '.join(plan.constraints) or 'none'}",
            f"Required inputs: {', '.join(plan.required_inputs) or 'none'}",
            f"Risk flags: {', '.join(plan.risk_flags) or 'none'}",
            f"Notes: {plan.notes or 'none'}",
            f"Source: {plan.source_type} ({plan.source_scope})",
            f"Stats: {plan.success_count} success, {plan.failure_count} failure",
            f"Created: {plan.created_at.isoformat() if plan.created_at else 'unknown'}",
            f"Updated: {plan.updated_at.isoformat() if plan.updated_at else 'unknown'}",
        ]
        if plan.steps:
            lines.append(f"Steps: {len(plan.steps)} defined")
            for i, step in enumerate(plan.steps, 1):
                lines.append(f"  {i}. {step}")

        await self.core._send(scope, "\n".join(lines))

    async def _delete_plan(self, scope: str, arg: str) -> None:
        if not arg:
            await self.core._send(scope, "Usage: /plan delete <plan_id>")
            return

        try:
            plan_id = int(arg.strip())
        except ValueError:
            await self.core._send(scope, "Plan ID must be a number.")
            return

        plan = self.core.plan_store.get(plan_id)
        if not plan:
            await self.core._send(scope, f"Plan {plan_id} not found.")
            return

        deleted = self.core.plan_store.delete(plan_id)
        if deleted:
            await self.core._send(scope, f"Plan {plan_id} deleted.")
        else:
            await self.core._send(scope, f"Failed to delete plan {plan_id}.")
