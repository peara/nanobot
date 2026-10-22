"""Per-run scratchpad protocol state machine.

Mirrors the "Mode lifecycle" diagram in docs/SCRATCHPAD.md (states
Empty / Active / AppendDue / Correcting / Finalized, edge methods named
after the diagram transitions). The machine is the single authority for
the protocol decisions in ``AgentRun.run()``:

- ``offer_tool`` — should ``session__scratchpad_write`` be in the tool
  schema (with its user-role state message) for the next LLM call?
- ``on_text_only_stop`` — strict-path verdict for a text-only stop:
  clean exit, nudge, or abort.
- ``append_due`` / ``skipped_append`` — relaxed-path check: an external
  tool result arrived and the model requests another external tool
  before any scratchpad write.
- ``on_dispatch`` — feed one dispatch outcome to the machine; routes to
  the diagram edge it implies.

Protocol semantics:

- An external tool *attempt* counts as work — success or failure alike.
  It resolves init-pending and sets AppendDue, so how the model groups
  calls into messages cannot change the protocol outcome.
- Writing notes is not work: an append does not resolve init-pending.
  Only an external attempt or a finalize disarms the strict trigger.
- Finalize is terminal within its round: once dispatched, later calls
  in the same batch still dispatch (each ``tool_call_id`` needs its
  ``tool`` message) but no further edge transitions, and the round
  routes to the finalize path regardless of what else it contained.

Internally the machine tracks three facts and projects them onto the
five diagram states:

- ``_mode`` — the Empty/Active/AppendDue/Finalized lifecycle (what the
  scratchpad is owed next).
- ``_init_pending`` + ``_corrections`` — the strict trigger: init was
  called and no external attempt or finalize has followed, plus how
  many nudges it has already caused. Armed with prior nudges the
  reported state is Correcting; armed with none it is a hidden marker
  on Active (the diagram's entry edge "text-only stop right after
  init-only round" fires from there).
- ``_awaiting_recovery`` — set by a nudge, cleared by any dispatch
  edge; re-offers the scratchpad tool for exactly the next LLM call.

The compound "AppendDue ∧ init-pending" is unreachable: ``on_init``
leaves Active (init is itself a scratchpad write, so any append debt is
settled) and ``on_tool_result`` resolves init-pending. That property is
what keeps the diagram at five states with no extra markers.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Literal

from nanobot.core_scratchpad import SCRATCHPAD_TOOL_NAME

logger = logging.getLogger(__name__)

TextStopVerdict = Literal["clean_exit", "nudge", "abort"]


class ScratchpadState(str, Enum):
    EMPTY = "Empty"
    ACTIVE = "Active"
    APPEND_DUE = "AppendDue"
    CORRECTING = "Correcting"
    FINALIZED = "Finalized"


class ScratchpadMachine:
    """Protocol authority for one ``AgentRun.run()`` loop. See module docstring."""

    def __init__(self, *, max_corrections: int) -> None:
        self._mode = ScratchpadState.EMPTY
        self._init_pending = False
        self._corrections = 0
        self._awaiting_recovery = False
        self._max_corrections = max_corrections

    # ------------------------------------------------------------------
    # Queries (the driver reads these; it never touches internal fields)
    # ------------------------------------------------------------------

    @property
    def state(self) -> ScratchpadState:
        """Current diagram state. Correcting is a projection of an armed
        strict trigger with prior nudges (descriptive)."""
        if self._mode is ScratchpadState.FINALIZED:
            return ScratchpadState.FINALIZED
        if self._init_pending and self._corrections > 0:
            return ScratchpadState.CORRECTING
        return self._mode

    @property
    def offer_tool(self) -> bool:
        """Gate for the scratchpad tool in the schema and its user-role
        state message: offered at the first call of a turn (Empty), while
        an append is due (AppendDue), and on the single recovery call
        after a nudge (awaiting recovery)."""
        return self._mode in (ScratchpadState.EMPTY, ScratchpadState.APPEND_DUE) or self._awaiting_recovery

    @property
    def append_due(self) -> bool:
        """An external tool result has arrived and no scratchpad write
        has followed (relaxed-path trigger)."""
        return self._mode is ScratchpadState.APPEND_DUE

    @property
    def finalized(self) -> bool:
        """Finalize was dispatched in the current round (terminal)."""
        return self._mode is ScratchpadState.FINALIZED

    def skipped_append(self, requested_calls: list[dict[str, Any]]) -> bool:
        """Relaxed check: an append is due and the batch requests an
        external tool before any scratchpad write. Equivalent to the old
        scan (violation iff the batch's first call is external while an
        append is owed)."""
        if not self.append_due or not requested_calls:
            return False
        first_name = str(requested_calls[0].get("function", {}).get("name", ""))
        return first_name != SCRATCHPAD_TOOL_NAME

    # ------------------------------------------------------------------
    # Edges (diagram transitions; each dispatch edge clears _awaiting_recovery)
    # ------------------------------------------------------------------

    def on_dispatch(self, scratchpad_mode: str) -> None:
        """Feed one dispatch outcome to the machine, routing to the diagram
        edge it implies: a scratchpad write mode (init/append/finalize, or
        any other write mode) takes its write edge; the empty mode of an
        external call (success or failure alike) is a tool-result edge."""
        if scratchpad_mode == "init":
            self.on_init()
        elif scratchpad_mode == "finalize":
            self.on_finalize()
        elif scratchpad_mode:
            self.on_scratchpad_write()
        else:
            self.on_tool_result()

    def on_init(self) -> None:
        """``init — wipe, set goal``: Empty/Active -> Active. Arms the
        strict trigger (init alone is a promise of work, not work). Any
        pending append debt is cleared — init is itself a scratchpad
        write."""
        if self.finalized:
            return
        self._mode = ScratchpadState.ACTIVE
        self._init_pending = True
        self._awaiting_recovery = False

    def on_tool_result(self) -> None:
        """``external tool result``: -> AppendDue. Resolves init-pending —
        an attempt counts as work, success or failure."""
        if self.finalized:
            return
        self._mode = ScratchpadState.APPEND_DUE
        self._init_pending = False
        self._awaiting_recovery = False

    def on_scratchpad_write(self) -> None:
        """``append by model, or synthetic append by harness``:
        AppendDue -> Active. Does NOT resolve init-pending — writing
        notes is not work."""
        if self.finalized:
            return
        self._mode = ScratchpadState.ACTIVE
        self._awaiting_recovery = False

    def on_finalize(self) -> None:
        """``finalize``: -> Finalized; terminal within its round. Resolves
        init-pending — finalize is the honest escape from a nudge."""
        self._mode = ScratchpadState.FINALIZED
        self._init_pending = False
        self._awaiting_recovery = False

    def on_text_only_stop(self, *, has_content: bool, scope: str) -> TextStopVerdict:
        """``text-only stop``: strict-path verdict.

        - Active with the trigger armed and a non-empty answer: nudge
          (up to ``max_corrections``), then abort. The state reports
          Correcting while armed with prior nudges, and persists through
          recovery prompts until a dispatch edge resolves or re-arms it.
        - Any other stop (no init pending, empty content, append owed,
          never inited): clean exit — the reply ships.
        """
        if self.finalized:
            return "clean_exit"
        if not (self._init_pending and has_content):
            return "clean_exit"
        self._corrections += 1
        if self._corrections > self._max_corrections:
            logger.warning(
                "Scratchpad protocol violation cap exceeded scope=%s retries=%d",
                scope,
                self._corrections,
            )
            return "abort"
        logger.warning(
            "Scratchpad protocol violation (nudging) scope=%s retry=%d",
            scope,
            self._corrections,
        )
        self._awaiting_recovery = True
        return "nudge"
