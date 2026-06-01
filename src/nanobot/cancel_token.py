from __future__ import annotations

import asyncio


class LlmCallCancelledError(Exception):
    """Raised when an LLM call or agent run is cancelled via token."""

    def __init__(self, scope: str | None = None) -> None:
        self.scope = scope
        msg = f"LLM call cancelled (scope={scope})" if scope else "LLM call cancelled"
        super().__init__(msg)


class CancellationToken:
    """Hierarchical cancellation token for async work trees.

    Supports parent-child linking so that cancelling a parent cascades
    to all children, and checking ``is_cancelled`` on a child walks
    up the parent chain.
    """

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._event: asyncio.Event = asyncio.Event()
        self._parent: CancellationToken | None = parent
        self._children: list[CancellationToken] = []
        if parent is not None:
            parent._children.append(self)

    def cancel(self) -> None:
        """Cancel this token and cascade to all children recursively."""
        self._event.set()
        for child in self._children:
            child.cancel()

    @property
    def is_cancelled(self) -> bool:
        """True if this token or any ancestor is cancelled."""
        if self._event.is_set():
            return True
        if self._parent is not None:
            return self._parent.is_cancelled
        return False

    async def wait(self) -> None:
        """Block until this token or any ancestor is cancelled."""
        if self._parent is None:
            await self._event.wait()
        else:
            done, _ = await asyncio.wait(
                [asyncio.ensure_future(self._event.wait()), asyncio.ensure_future(self._parent.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Suppress CancelledError from pending tasks
            for t in done:
                if not t.cancelled():
                    _ = t.exception()  # re-raise if failed, else consume result

    def create_child(self) -> CancellationToken:
        """Create a child token linked to this token."""
        return CancellationToken(parent=self)
