from __future__ import annotations

from typing import Any

from nanobot.core_utils import attach_human_timestamps, trim_history_by_chars


class MessageRouter:
    """Dispatches an incoming chat turn to an agent run (single default path for now)."""

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    async def route_user_message(self, scope: str) -> None:
        """Run default assistant turn after the caller persisted the user message to memory."""
        history = self._bot.memory.get_recent_messages(scope, limit=self._bot.config.history_message_limit)
        history = attach_human_timestamps(history, timezone_name=self._bot.config.working_timezone)
        history = trim_history_by_chars(history, self._bot.config.history_char_limit)
        messages = [self._bot._base_system_message()]
        messages.extend(history)
        await self._bot._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)
