# ADR-0002: Asymmetric harness-enforced protocol compliance for the scratchpad

- **Status:** Accepted (retroactive)
- **Shipped in:** tuning arc `5df7c82` (2026-03-09, strict protocol) → `9d7f47a` (2026-03-10, synthetic append) → PR #9 / `87befa4` (2026-04-10, tool-surface control) → `ddf2208` (2026-06-09, bounded nudge recovery after incident run-297119136c); current enforcement is `ScratchpadMachine` from PR #46 (2026-10-23), resolving #45

## Context

The scratchpad protocol ([ADR-0001](ADR-0001-scratchpad-as-harness-owned-working-memory.md)) requires init
at turn start, append after each external tool result, finalize before the final answer. Small local models
break such protocols in **both directions**: **under-compliance** — skipping appends, stopping with a
narrative answer instead of finalizing — and **over-compliance** — looping on the scratchpad tool and burning
the token budget on bookkeeping (PR #9).

The arc ran Mar 9 → Jun 9 2026: `5df7c82` introduced the strict protocol (2-retry correction cap, violation
abort, empty-reply fallback); `9d7f47a` softened it after one day with a lenient synthetic append; PR #9
stripped the scratchpad tool from continue calls to stop the token loop; and `ddf2208` added bounded nudge
recovery after production incident **run-297119136c** (2026-06-08): after `mode=init`, the model emitted a
1109-char text-only stop with zero tool calls — the user got a narrative promise instead of a real script.
Two compounding causes: the model skipped finalize, *and* the harness had stripped the scratchpad tool from
the continue call — the model was told "you must finalize" but finalize was not in its tool list. A later
refactor (`9002d13`, 2026-06-10) silently broke the nudge path — the correction message never reached the
model — which became issue #45 and drove the `ScratchpadMachine` refactor in PR #46, protocol walks since
pinned by tests (`1afb87c`).

## Decision

Enforce the protocol in the harness — not with prompts alone — but **asymmetrically, graded by recoverability**:

- **Hard abort only for unrecoverable narrative-without-work (strict path).** An init followed by a
  text-only stop with no external tool attempt or finalize is a hallucinated completion; it must not reach
  the user. The harness injects a correction system message —

  > Protocol violation: you stopped with a text answer right after a scratchpad init, but you did no work.
  > Resume now with your tools, or call session__scratchpad_write with mode='finalize' using what you
  > actually have.

  — and re-offers the scratchpad tool, up to `MAX_SCRATCHPAD_PROTOCOL_RETRIES = 2` nudges; if the model
  still stops, the turn ends with a fixed safe reply ("I got stuck enforcing scratchpad updates in this
  turn. Please try again."). Bounded, never a livelock.

- **Lenient synthetic append for work-without-notes (relaxed path).** If the model requests another external
  tool after real work without a scratchpad write in between, the work happened — only the notes were
  skipped. The harness writes the append from the model's own content, logs the violation, and proceeds.

- **Prevention by tool-surface control.** `session__scratchpad_write` is offered only while an append is due
  (and at the first call of a turn, and on the single recovery call after a nudge) — visible when a write is
  required, absent when over-compliance would loop on it.

- **Deliberately not enforced.** A text-only stop after real tool work — successful or failed — is a clean
  exit: the model attempted something, so its honest report ships. A turn that never calls init simply runs
  without a scratchpad.

## Alternatives considered

- **Uniform hard abort on any violation** (the original Mar 9 shape). Rejected after one day: work-without-notes is recoverable; aborting discards real work over missing bookkeeping.
- **Prompts only, no harness enforcement.** Rejected: the June incident shows even an explicit "you must call finalize" instruction is skipped under pressure.
- **Always keep the scratchpad tool in the schema.** Rejected: that is the token loop (PR #9) — the tool surface itself must gate visibility.
- **Unbounded nudging.** Rejected: silent livelock; the cap guarantees termination with a user-visible message.

## Consequences

**Positive:**

- Both failure directions — violation and token loop — are handled by one coherent policy with a bounded
  worst case (2 nudges → fixed reply), and lenient paths preserve real work instead of discarding it.
- The truth table is executable (`ScratchpadMachine`, edge methods named after its transitions) and pinned
  by protocol-walk tests, so refactors can be caught.

**Downides (honest):**

- The policy is genuinely subtle — attempts count as work while appends do not, batched calls change
  protocol outcomes, the tool surface shifts between rounds. It took a production incident and a
  regression (#45: the correction text never reached the model; the old test asserted the tool set, not
  the correction) before tests pinned the contract.
- The June incident was partly self-inflicted: the over-compliance guard (tool stripping) composed with
  the strict finalize instruction into a broken run — two correct-in-isolation mechanisms, wrong together.
- Synthetic appends mean missed notes silently degrade final-answer quality — the user never learns the
  summary was thinner than the work.
- The hard-abort reply ("I got stuck enforcing scratchpad updates…") is a dead end; the user must retry
  manually.