"""Command manager for routing commands."""

from __future__ import annotations

import logging

from nanobot.channels.base import IncomingMessage
from nanobot.core_commands.commands.base import BaseCommand
from nanobot.core_commands.commands.context import ContextCommand  # noqa: F401

# Import all command classes at runtime to avoid circular imports
from nanobot.core_commands.commands.help import HelpCommand  # noqa: F401
from nanobot.core_commands.commands.plan import PlanCommand  # noqa: F401
from nanobot.core_commands.commands.reload import ReloadCommand  # noqa: F401
from nanobot.core_commands.commands.reset import ResetCommand  # noqa: F401
from nanobot.core_commands.commands.scratchpad import ScratchpadCommand  # noqa: F401

logger = logging.getLogger(__name__)


class CommandManager:
    def __init__(self, core) -> None:
        self.core = core
        self._commands: dict[str, type[BaseCommand]] = {}
        self._register_commands()

    def _register_commands(self) -> None:
        self._register(HelpCommand)
        self._register(ContextCommand)
        self._register(ResetCommand)
        self._register(PlanCommand)
        self._register(ScratchpadCommand)
        self._register(ReloadCommand)

    def _register(self, command_class: type[BaseCommand]) -> None:
        for name in command_class.names():
            if name in self._commands:
                logger.warning("Command %s already registered, skipping", name)
                continue
            self._commands[name] = command_class
            logger.debug("Registered command: %s", name)

    async def handle(self, cmd: str, message: IncomingMessage, scope: str) -> bool:
        command_class = self._commands.get(cmd)
        if command_class is None:
            return False
        handler = command_class(core=self.core)
        await handler.handle_with_error_handling(message.text, scope)
        return True
