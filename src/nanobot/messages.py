from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union


@dataclass(frozen=True)
class UserMessage:
    """Message from a user via a channel.

    Immutable to prevent accidental modification during processing.
    """

    channel: str
    chat_id: str
    text: str
    user_id: str = ""
    metadata: dict[str, Any] | None = None

    @property
    def scope(self) -> str:
        """Compose the scope identifier for this message's context."""
        return f"{self.channel}:{self.chat_id}"


@dataclass(frozen=True)
class SubagentResultMessage:
    """Result from a subagent run, sent to the orchestrator.

    Immutable to ensure the result cannot be modified after the subagent completes.
    """

    run_id: str
    parent_scope: str
    success: bool
    summary: str
    tool_trace: list[dict[str, Any]]
    metadata: dict[str, Any] | None = None


# Union type for all messages that can go through the orchestrator's message queue
OrchestratorMessage = Union[UserMessage, SubagentResultMessage]
