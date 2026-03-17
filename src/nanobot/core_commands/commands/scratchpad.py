from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand
from nanobot.core_scratchpad import scratchpad_command

logger = logging.getLogger(__name__)


class ScratchpadCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/scratchpad"]

    async def handle(self, raw_text: str, scope: str) -> None:
        await scratchpad_command(self.core, scope, raw_text)
