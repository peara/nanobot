# ADR-0001: Structured scratchpad as harness-owned working memory for a small local model

- **Status:** Accepted (retroactive) — shipped incrementally on main in direct commits; no tracking PR exists (see References)

## Context

Nanobot is local-first: the example config targets Ollama on localhost (`gpt-oss:120b`, temperature 0.2, **max_tokens 800**), and there is no big-model fallback. Small local models degrade long before their nominal context limit; in long tool-calling chains they lose track of which step they are on, what earlier tools found, and whether the work is done — and history trimming makes it worse: dropped intermediate results are gone by final-answer time. The problem was pattern-driven, not a single incident; the first scratchpad doc recorded it in one line: *"Local and smaller LLMs lose track in long tool-calling chains."*

## Decision

Give each run a structured scratchpad that the **harness owns, not the model**:

- **Structured state, fixed budgets.** Six capped fields (`goal`/`current_step`/`next_step` at 600 chars, `context` at 1200, `known_facts`/`tool_journal` as 30-entry arrays clipped to 600 chars each) keep working-memory prompt cost constant, however long the tool chain gets; state persists in the `contexts` SQLite table keyed by run.
- **Per-run isolation.** Each run gets its own scratchpad keyed by `run_id`; every run starts empty, and delegated children inherit nothing. This replaced the original chat-scoped scratchpad carried across turns, after concurrent runs on the same scope corrupted each other's state.
- **Finalize is a distinct exit path.** `mode=finalize` triggers one explicit no-tools LLM call whose prompt is built from the scratchpad (goal plus a summary of `context`, `known_facts`, `tool_journal`); the final answer is assembled from the notes, and anything not written down is lost.
- **Prompt shape is part of the design.** State is injected twice — a system message with the full state JSON, and a user-role message rendered as the **last message before generation**. Ending the prompt on the current state fixed empty generations: a small model confronted with tool results alone stalls.

This is Anthropic's *structured note-taking* pattern with two deltas: the state is per-run, and the update protocol is harness-enforced rather than left to model discretion.

## Alternatives considered

- **Trust model discretion / freeform notes in context.** Rejected: small models do not keep this discipline on their own; freeform notes grow unboundedly and survive trimming no better than tool results.
- **Bigger model or cloud fallback.** Rejected: local-first is a product constraint, and — as [ADR-0003](ADR-0003-tool-guards-at-dispatch-boundary.md) records — switching models changed the flavor of failures rather than removing them.
- **Per-conversation scratchpad carried across turns** (the original shape). Rejected after a user message and a scheduled task arriving together clobbered each other's state; per-run isolation is what makes concurrency safe.
- **Retrieval over past tool results instead of explicit notes.** Not pursued: retrieval cannot cheaply reconstruct "current step / next step" orientation, and it reintroduces unbounded prompt cost.

## Consequences

**Positive:** long tool chains stay coherent within tight token budgets at constant working-memory cost; final answers are grounded in recorded facts, and honest failure reports ship (a text-only stop after real work is a clean exit); per-run isolation makes concurrency and delegation safe with no clearing protocol.

**Downsides (honest):**

- The model spends calls writing notes — and can over-comply, burning the whole budget on bookkeeping (the token-loop failure; see [ADR-0002](ADR-0002-asymmetric-protocol-enforcement.md)).
- Protocol enforcement is a real subsystem with its own failure modes: a production incident was partly caused by the harness's own tool-surface logic (see ADR-0002).
- Facts the model fails to write are *silently* absent from the final answer — the relaxed path logs the miss but ships anyway.
- Nontrivial maintenance: limits, templates, a protocol state machine, and pinned tests.

## References

- Initial scratchpad integration: commit `a0be4c2`.
- Per-run scratchpad isolation: commit `96dc6ac`.
- Current semantics: [docs/SCRATCHPAD.md](../SCRATCHPAD.md).