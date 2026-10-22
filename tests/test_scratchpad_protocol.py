"""Unit tests for ScratchpadMachine — the scratchpad protocol state machine.

Each test names the docs/SCRATCHPAD.md "Mode lifecycle" diagram edge it
pins, plus the issue #45 semantics: D1 = M3 (attempt = work), D2
(finalize terminal within its round), and the append-does-not-resolve
rule. The driver-level behavior these edges imply is pinned by
tests/agent_run/ (regression suite); this file pins the machine itself.

Run: uv run pytest tests/test_scratchpad_protocol.py -v
"""

from __future__ import annotations

from typing import Any

from nanobot.scratchpad_protocol import SCRATCHPAD_TOOL_NAME as TOOL
from nanobot.scratchpad_protocol import ScratchpadMachine, ScratchpadState


def _machine() -> ScratchpadMachine:
    return ScratchpadMachine(max_corrections=2)


def _ext_call(name: str = "web__search_web") -> dict[str, Any]:
    return {"id": "c1", "type": "function", "function": {"name": name, "arguments": "{}"}}


def _scratchpad_call(mode: str) -> dict[str, Any]:
    return {"id": "c2", "type": "function", "function": {"name": TOOL, "arguments": f'{{"mode": "{mode}"}}'}}


# ---------------------------------------------------------------------------
# on_dispatch: routing one dispatch outcome to its diagram edge
# ---------------------------------------------------------------------------


def test_on_dispatch_routes_each_outcome_kind() -> None:
    """on_dispatch maps a dispatch outcome's scratchpad_mode to the edge:
    init -> on_init (armed), append -> write (Active, still armed),
    finalize -> Finalized, external (empty mode) -> AppendDue."""
    machine = _machine()

    machine.on_dispatch("init")
    assert machine.state is ScratchpadState.ACTIVE
    assert machine.on_text_only_stop(has_content=True, scope="s") == "nudge"  # armed by init

    machine.on_dispatch("append")
    assert machine.state is ScratchpadState.CORRECTING  # append keeps the trigger armed

    machine.on_dispatch("finalize")
    assert machine.state is ScratchpadState.FINALIZED

    machine = _machine()
    machine.on_dispatch("")
    assert machine.state is ScratchpadState.APPEND_DUE
    machine.on_dispatch("some-other-write-mode")  # unknown write modes take the append edge
    assert machine.state is ScratchpadState.ACTIVE


# ---------------------------------------------------------------------------
# Empty: run start
# ---------------------------------------------------------------------------


def test_start_is_empty_and_offers_tool() -> None:
    """[*] --> Empty; the first call of a turn offers the scratchpad tool."""
    machine = _machine()
    assert machine.state is ScratchpadState.EMPTY
    assert machine.offer_tool is True
    assert machine.append_due is False
    assert machine.finalized is False


def test_text_stop_from_empty_is_clean_exit() -> None:
    """Empty --> [*] : plain text reply, no tools."""
    machine = _machine()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "clean_exit"
    assert machine.state is ScratchpadState.EMPTY


# ---------------------------------------------------------------------------
# init: Empty/Active -> Active, arms the strict trigger
# ---------------------------------------------------------------------------


def test_on_init_moves_to_active_and_arms_trigger() -> None:
    """Empty --> Active : init — wipe, set goal."""
    machine = _machine()
    machine.on_init()
    assert machine.state is ScratchpadState.ACTIVE
    assert machine.append_due is False
    # The trigger is a hidden marker on Active: an init-only round does
    # NOT re-offer the scratchpad tool (over-compliance guard).
    assert machine.offer_tool is False


def test_on_init_clears_pending_append_debt() -> None:
    """init is itself a scratchpad write: any append owed is cleared."""
    machine = _machine()
    machine.on_tool_result()
    assert machine.append_due is True
    machine.on_init()
    assert machine.state is ScratchpadState.ACTIVE
    assert machine.append_due is False


def test_init_then_text_stop_with_content_nudges() -> None:
    """Active --> Correcting : text-only stop right after init-only round."""
    machine = _machine()
    machine.on_init()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "nudge"
    assert machine.state is ScratchpadState.CORRECTING
    # Nudge re-offers the scratchpad tool for exactly the recovery call.
    assert machine.offer_tool is True


def test_init_then_empty_text_stop_is_clean_exit() -> None:
    """An empty text-only stop never triggers the strict path."""
    machine = _machine()
    machine.on_init()
    assert machine.on_text_only_stop(has_content=False, scope="s") == "clean_exit"
    assert machine.state is ScratchpadState.ACTIVE


# ---------------------------------------------------------------------------
# external tool result: -> AppendDue; D1 = M3 (attempt = work)
# ---------------------------------------------------------------------------


def test_external_tool_result_sets_append_due() -> None:
    """Active --> AppendDue : external tool result."""
    machine = _machine()
    machine.on_init()
    machine.on_tool_result()
    assert machine.state is ScratchpadState.APPEND_DUE
    assert machine.append_due is True
    assert machine.offer_tool is True


def test_external_tool_result_without_init_also_sets_append_due() -> None:
    """Empty --> AppendDue : tool result, init never called."""
    machine = _machine()
    machine.on_tool_result()
    assert machine.state is ScratchpadState.APPEND_DUE
    assert machine.append_due is True


def test_failed_external_attempt_resolves_init_pending() -> None:
    """D1 = M3: an attempt counts as work. The machine has one edge for
    both outcomes, so [init, ext-fails] and [init] [ext-fails] walk the
    same diagram: AppendDue, trigger disarmed, honest stop ships."""
    machine = _machine()
    machine.on_init()
    machine.on_tool_result()  # failed or successful — the machine cannot tell
    assert machine.on_text_only_stop(has_content=True, scope="s") == "clean_exit"
    assert machine.state is ScratchpadState.APPEND_DUE


def test_text_stop_with_append_due_is_clean_exit() -> None:
    """AppendDue --> [*] : text-only stop, missed append (clean exit)."""
    machine = _machine()
    machine.on_tool_result()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "clean_exit"


def test_init_pending_never_coexists_with_append_due() -> None:
    """The M3 no-compound property: after init + external attempt the
    state is plain AppendDue, not Correcting or a hidden compound."""
    machine = _machine()
    machine.on_init()
    machine.on_text_only_stop(has_content=True, scope="s")  # nudge -> Correcting
    machine.on_tool_result()  # model resumes with a tool
    assert machine.state is ScratchpadState.APPEND_DUE
    assert machine.offer_tool is True


# ---------------------------------------------------------------------------
# append: AppendDue -> Active (does not resolve init-pending)
# ---------------------------------------------------------------------------


def test_append_returns_to_active_and_clears_append_due() -> None:
    """AppendDue --> Active : append by model."""
    machine = _machine()
    machine.on_tool_result()
    machine.on_scratchpad_write()
    assert machine.state is ScratchpadState.ACTIVE
    assert machine.append_due is False
    # After a write the tool leaves the schema until the next tool result.
    assert machine.offer_tool is False


def test_synthetic_append_takes_the_same_edge() -> None:
    """AppendDue --> Active : synthetic append by harness (same edge)."""
    machine = _machine()
    machine.on_tool_result()
    machine.on_scratchpad_write()
    assert machine.state is ScratchpadState.ACTIVE


def test_append_does_not_resolve_init_pending() -> None:
    """Writing notes is not work: an append after init keeps the strict
    trigger armed, so a narrative stop still nudges."""
    machine = _machine()
    machine.on_init()
    machine.on_scratchpad_write()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "nudge"
    assert machine.state is ScratchpadState.CORRECTING


# ---------------------------------------------------------------------------
# Correcting: persistence, recovery, re-init, cap
# ---------------------------------------------------------------------------


def test_recovery_tool_round_consumes_the_reoffer() -> None:
    """Correcting --> Active/AppendDue : model resumes. The forced tool
    offer lasts exactly the recovery call; the round after a re-init
    does not re-offer."""
    machine = _machine()
    machine.on_init()
    machine.on_text_only_stop(has_content=True, scope="s")  # nudge
    assert machine.offer_tool is True
    machine.on_init()  # model re-inits during recovery
    assert machine.state is ScratchpadState.CORRECTING  # stays in Correcting
    assert machine.offer_tool is False  # awaiting recovery consumed


def test_reinit_during_recovery_keeps_correcting_and_nudges_again() -> None:
    machine = _machine()
    machine.on_init()
    machine.on_text_only_stop(has_content=True, scope="s")  # nudge #1
    machine.on_init()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "nudge"  # nudge #2
    machine.on_init()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "abort"  # cap


def test_cap_exceeded_aborts() -> None:
    """Correcting --> [*] : corrections exhausted — abort reply."""
    machine = _machine()
    machine.on_init()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "nudge"
    assert machine.on_text_only_stop(has_content=True, scope="s") == "nudge"
    assert machine.on_text_only_stop(has_content=True, scope="s") == "abort"


def test_recovery_finalize_resolves_the_trigger() -> None:
    """Finalize is the honest escape from a nudge."""
    machine = _machine()
    machine.on_init()
    machine.on_text_only_stop(has_content=True, scope="s")  # nudge
    machine.on_finalize()
    assert machine.finalized is True
    assert machine.on_text_only_stop(has_content=True, scope="s") == "clean_exit"


# ---------------------------------------------------------------------------
# finalize: terminal within its round (D2)
# ---------------------------------------------------------------------------


def test_finalize_is_terminal_within_the_round() -> None:
    """D2: [finalize, ext] in one batch — the ext dispatches (its
    tool_call_id needs a tool message) but no edge transitions after
    finalize; the round routes to the finalize path."""
    machine = _machine()
    machine.on_finalize()
    machine.on_tool_result()  # later call in the same batch
    machine.on_init()
    machine.on_scratchpad_write()
    assert machine.state is ScratchpadState.FINALIZED
    assert machine.finalized is True


def test_finalize_resolves_init_pending() -> None:
    machine = _machine()
    machine.on_init()
    machine.on_finalize()
    assert machine.on_text_only_stop(has_content=True, scope="s") == "clean_exit"


# ---------------------------------------------------------------------------
# Relaxed path: skipped_append
# ---------------------------------------------------------------------------


def test_skipped_append_true_when_external_first_while_due() -> None:
    machine = _machine()
    machine.on_tool_result()
    assert machine.skipped_append([_ext_call()]) is True
    assert machine.skipped_append([_ext_call(), _scratchpad_call("append")]) is True


def test_skipped_append_false_when_scratchpad_first() -> None:
    machine = _machine()
    machine.on_tool_result()
    assert machine.skipped_append([_scratchpad_call("append"), _ext_call()]) is False
    assert machine.skipped_append([_scratchpad_call("append")]) is False


def test_skipped_append_false_when_nothing_due() -> None:
    machine = _machine()
    assert machine.skipped_append([_ext_call()]) is False
    assert machine.skipped_append([]) is False
