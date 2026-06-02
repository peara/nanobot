from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand

logger = logging.getLogger(__name__)


class StopCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/stop"]

    async def handle(self, raw_text: str, scope: str) -> None:
        active = self.core.active_requests
        if not active:
            await self._send(scope, "No active requests to cancel.")
            return

        count = len(active)
        scopes = list(active.keys())
        for s in scopes:
            self.core.cancel_request(s)

        logger.info("Stop command cancelled %d request(s): %s", count, scopes)
        await self._send(scope, f"Cancelled {count} active request(s).")
