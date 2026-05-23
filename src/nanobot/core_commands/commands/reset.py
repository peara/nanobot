from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand

logger = logging.getLogger(__name__)


class ResetCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/reset"]

    async def handle(self, raw_text: str, scope: str) -> None:
        deleted = self.core.memory.clear_chat(scope)
        await self._send(
            scope,
            f"Context reset complete.\nscope: {scope}\ndeleted_messages: {deleted}",
        )
