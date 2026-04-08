from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.core import BotCore


async def process_incoming_sync(bot: "BotCore", message) -> None:
    await bot.on_incoming(message)
    await bot._process_one_message()
