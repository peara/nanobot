from __future__ import annotations

from nanobot.core_commands.commands.base import BaseCommand
from nanobot.core_commands.commands.context import ContextCommand
from nanobot.core_commands.commands.help import HelpCommand
from nanobot.core_commands.commands.plan import PlanCommand
from nanobot.core_commands.commands.reload import ReloadCommand
from nanobot.core_commands.commands.reset import ResetCommand
from nanobot.core_commands.commands.scratchpad import ScratchpadCommand

__all__ = [
    "BaseCommand",
    "HelpCommand",
    "ContextCommand",
    "ResetCommand",
    "PlanCommand",
    "ScratchpadCommand",
    "ReloadCommand",
]


def get_all_commands() -> list[type[BaseCommand]]:
    return [
        HelpCommand,
        ContextCommand,
        ResetCommand,
        PlanCommand,
        ScratchpadCommand,
        ReloadCommand,
    ]
