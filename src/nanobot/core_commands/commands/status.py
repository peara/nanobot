from __future__ import annotations

import logging
from datetime import datetime

from nanobot.core_commands.commands.base import BaseCommand

logger = logging.getLogger(__name__)


class StatusCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/status"]

    async def handle(self, raw_text: str, scope: str) -> None:
        if self.core.active_requests:
            active = list(self.core.active_requests.values())[-1]
            elapsed = datetime.now() - active.started_at
            total_seconds = int(elapsed.total_seconds())
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            if minutes > 0:
                time_str = f"{minutes}m {seconds}s"
            else:
                time_str = f"{seconds}s"
            reply = f"🔴 Busy ({time_str} ago)\n└─ {active.current_step}"
        else:
            last_activity = self._get_last_activity(scope)
            if last_activity is not None:
                elapsed = datetime.now() - last_activity
                total_seconds = int(elapsed.total_seconds())
                minutes = total_seconds // 60
                seconds = total_seconds % 60
                if minutes > 0:
                    time_str = f"{minutes}m {seconds}s ago"
                else:
                    time_str = f"{seconds}s ago"
                reply = f"🟢 Free (last activity {time_str})"
            else:
                reply = "🟢 Free"
        await self._send(scope, reply)

    def _get_last_activity(self, scope: str) -> datetime | None:
        last_msg = self.core.contexts.get("chat", scope, "last_assistant_message")
        if not last_msg or "text" not in last_msg:
            return None
        try:
            timestamp_str = last_msg["timestamp"]
            return datetime.fromisoformat(str(timestamp_str))
        except Exception:
            pass
        return None
