from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class IncomingMessage:
    channel: str
    chat_id: str
    user_id: str
    text: str


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class Channel(ABC):
    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def emit(self, message: IncomingMessage) -> None:
        if self._handler is not None:
            await self._handler(message)

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, chat_id: str, text: str) -> None: ...


class ProcessingAwareChannel(Channel, ABC):
    @abstractmethod
    async def begin_processing(self, chat_id: str) -> None: ...

    @abstractmethod
    async def end_processing(self, chat_id: str) -> None: ...
