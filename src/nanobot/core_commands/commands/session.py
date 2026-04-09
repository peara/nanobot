from __future__ import annotations

import logging
from datetime import datetime

from nanobot.core_commands.commands.base import BaseCommand

logger = logging.getLogger(__name__)


class SessionCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/session"]

    async def handle(self, raw_text: str, scope: str) -> None:
        session_info = self.core.contexts.get("chat", scope, "opencode_session")

        if not session_info:
            await self._send(scope, "No active OpenCode session for this chat.")
            return

        session_id = session_info.get("session_id", "unknown")
        created_at = session_info.get("created_at", "unknown")
        last_activity = session_info.get("last_activity")
        message_count = session_info.get("message_count", 0)

        lines = [
            f"**OpenCode Session:** `{session_id}`",
            f"**Created:** {created_at}",
            f"**Messages exchanged:** {message_count}",
        ]

        if last_activity:
            try:
                last_dt = datetime.fromisoformat(str(last_activity))
                elapsed = datetime.now() - last_dt
                minutes = int(elapsed.total_seconds() // 60)
                if minutes < 1:
                    lines.append("**Last activity:** just now")
                elif minutes < 60:
                    lines.append(f"**Last activity:** {minutes}m ago")
                else:
                    hours = minutes // 60
                    lines.append(f"**Last activity:** {hours}h ago")
            except Exception:
                lines.append(f"**Last activity:** {last_activity}")
        else:
            lines.append("**Last activity:** never")

        last_plan = self.core.contexts.get("chat", scope, "last_plan_run_id")
        if last_plan:
            run_id = last_plan.get("run_id")
            if run_id:
                plan_status = self.core.contexts.get("plan_run", run_id, "status")
                if plan_status:
                    status_val = plan_status.get("value", "unknown")
                    lines.append(f"**Plan run:** `{run_id}` ({status_val})")

        await self._send(scope, "\n".join(lines))
