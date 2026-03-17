from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseCommand(ABC):
    def __init__(self, core) -> None:
        self.core = core

    @classmethod
    @abstractmethod
    def names(cls) -> list[str]:
        pass

    @abstractmethod
    async def handle(self, raw_text: str, scope: str) -> None:
        pass

    async def _send(self, scope: str, text: str) -> None:
        await self.core._send(scope, text)

    async def handle_with_error_handling(self, raw_text: str, scope: str) -> None:
        try:
            await self.handle(raw_text, scope)
        except Exception as e:
            logger.exception("Command %s failed", self.__class__.__name__)
            await self._send(scope, f"Error: {str(e)}")
