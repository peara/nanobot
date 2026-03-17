from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand
from nanobot.core_plan import process_plan

logger = logging.getLogger(__name__)


class PlanCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/plan"]

    async def handle(self, raw_text: str, scope: str) -> None:
        await process_plan(self.core, scope, raw_text)
