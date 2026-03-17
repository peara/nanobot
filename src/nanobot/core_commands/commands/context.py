from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand

logger = logging.getLogger(__name__)


class ContextCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/ctx", "/ctxfull"]

    async def handle(self, raw_text: str, scope: str) -> None:
        if raw_text.strip() == "/ctx":
            report = self.core._build_context_report(scope)
        else:
            report = self.core._build_full_context_report(scope)
        await self._send(scope, report)
