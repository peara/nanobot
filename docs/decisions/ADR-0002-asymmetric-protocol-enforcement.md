# ADR-0002: Asymmetric harness-enforced protocol compliance for the scratchpad

- **Status:** Accepted (retroactive), shipped in PR #46 — the `ScratchpadMachine` that now enforces the protocol (resolving #45)

## Context

The scratchpad protocol ([ADR-0001](ADR-0001-scratchpad-as-harness-owned-working-memory.md)) requires init at turn start, append after each external tool result, and finalize before the final answer. Small local models break such protocols in **both directions**: **under-compliance** — skipping appends, stopping with a narrative answer instead of finalizing — and **over-compliance** — looping on the scratchpad tool and burning the token budget on bookkeeping. Prompting alone fixed neither.

The two directions taught different lessons. Under-compliance peaked in a production incident: after an init, the model stopped with a long narrative promise instead of the requested script — and the harness was a compounding cause, because the tool-stripping guard had removed the scratchpad tool from the very call whose prompt still demanded finalize. Over-compliance (the token loop) taught that the tool surface itself, not the prompt, controls behavior.

## Decision

Enforce the protocol in the harness — not with prompts alone — but **asymmetrically, graded by recoverability**:

- **Hard abort only for unrecoverable narrative-without-work (strict path).** An init followed by a text-only stop with no external tool attempt or finalize is a hallucinated completion; it must not reach the user. The harness injects a correction system message — a protocol-violation notice telling the model to resume with tools or finalize — and re-offers the scratchpad tool, up to `MAX_SCRATCHPAD_PROTOCOL_RETRIES = 2` nudges; if the model still stops, the turn ends with a fixed safe reply. Bounded, never a livelock.
- **Lenient synthetic append for work-without-notes (relaxed path).** If the model requests another external tool after real work without a scratchpad write in between, the work happened — only the notes were skipped. The harness writes the append itself, logs the violation, and proceeds.
- **Prevention by tool-surface control.** `session__scratchpad_write` is offered only while an append is due (and at the first call of a turn, and on the single recovery call after a nudge) — visible when a write is required, absent when over-compliance would loop on it.
- **Deliberately not enforced.** A text-only stop after real tool work — successful or failed — is a clean exit: the model attempted something, so its honest report ships. A turn that never calls init simply runs without a scratchpad.

## Alternatives considered

- **Uniform hard abort on any violation** (the original shape). Rejected: work-without-notes is recoverable; aborting discards real work over missing bookkeeping.
- **Prompts only, no harness enforcement.** Rejected: the incident shows even an explicit "you must call finalize" instruction is skipped under pressure.
- **Always keep the scratchpad tool in the schema.** Rejected: that is the token loop — the tool surface itself must gate visibility.
- **Unbounded nudging.** Rejected: silent livelock; the cap guarantees termination with a user-visible message.

## Consequences

**Positive:** both failure directions are handled by one coherent policy with a bounded worst case (2 nudges → fixed reply), lenient paths preserve real work, and the truth table is executable (`ScratchpadMachine`, edge methods named after its transitions) and pinned by protocol-walk tests.

**Downsides (honest):**

- The policy is genuinely subtle — attempts count as work while appends do not, batched calls change protocol outcomes, the tool surface shifts between rounds. It took a production incident and a silent regression (the correction never reached the model because the test asserted the tool set, not the correction text) before tests pinned the contract.
- The incident was partly self-inflicted: the over-compliance guard (tool stripping) composed with the strict finalize instruction into a broken run — two correct-in-isolation mechanisms, wrong together.
- Synthetic appends mean missed notes silently degrade final-answer quality — the user never learns the summary was thinner than the work.
- The hard-abort reply is a dead end; the user must retry manually.

## References

- Token-loop fix (tool-surface control): PR #9.
- Bounded nudge recovery after incident `run-297119136c`: commit `ddf2208`.
- `ScratchpadMachine` + pinned protocol-walk tests, resolving #45: PR #46.