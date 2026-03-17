from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand
from nanobot.core_utils import help_text

logger = logging.getLogger(__name__)


class HelpCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/help", "/commands", "/start"]

    async def handle(self, raw_text: str, scope: str) -> None:
        await self._send(scope, help_text())
