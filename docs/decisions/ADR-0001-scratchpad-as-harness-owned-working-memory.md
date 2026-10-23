# ADR-0001: Structured scratchpad as harness-owned working memory for a small local model

- **Status:** Accepted (retroactive)
- **Shipped in:** `a0be4c2` (2026-03-07, initial scratchpad), reshaped by `8afd7af` (2026-04-28, explicit
  no-tools finalize call) and `96dc6ac` (2026-05-23, per-run isolation); current semantics in
  [docs/SCRATCHPAD.md](../SCRATCHPAD.md)

## Context

Nanobot is local-first by design: the repo scaffold landed 2026-03-01 (`3b092fa`) with the example config
pointing at Ollama on localhost (`gpt-oss:120b`, temperature 0.2, **max_tokens 800**); the real config has
always targeted local, OpenAI-compatible endpoints, with no big-model fallback.

Small local models degrade long before their nominal context limit. In long tool-calling chains they lose
track — which step, what earlier tools found, whether the work is done — and history trimming makes it
worse: dropped intermediate results are gone by final-answer time. The motivation was written down in the
first committed version of the scratchpad doc (`b3a8d51`, 2026-05-16): *"Local and smaller LLMs lose track
in long tool-calling chains."* No issue
tracks the origin; it was pattern-driven, not a single incident. Getting it right took three months
(Mar 7 → Jun 9) — see [ADR-0002](ADR-0002-asymmetric-protocol-enforcement.md) for the arc.

## Decision

Give each run a structured scratchpad that the **harness owns, not the model**:

- **Structured state, fixed budgets.** Six fields with hard caps — `goal`, `current_step`, `next_step` at
  600 chars, `context` at 1200, `known_facts` / `tool_journal` as 30-entry arrays clipped to 600 chars per
  entry — so every prompt pays a bounded, constant cost for working memory, however long the tool chain
  gets. State persists in the `contexts` SQLite table keyed by run (debug CLI: `just ctx <scope>`).
- **Per-run isolation.** Each run gets its own scratchpad keyed by `run_id` (`96dc6ac`); every new run
  starts empty, and delegated children inherit nothing. This replaced the original Mar 7 chat-scoped
  scratchpad carried across turns, after concurrent runs on the same scope corrupted each other's state.
- **Finalize is a distinct exit path.** `mode=finalize` triggers one explicit no-tools LLM call (`8afd7af`)
  whose prompt is built from the scratchpad — goal plus a summary of `context`, `known_facts`, and
  `tool_journal`. The final answer is assembled from the notes; anything not written down is lost.
- **Prompt shape is part of the design.** The state is injected twice: a system message with the full state
  JSON, and a user-role message rendered as the **last message before the model generates**. This shape
  (`62467ad`, 2026-03-10) fixed empty generations — a small model confronted with tool results alone
  would stall; ending the prompt on the current state gives it something to respond to.

This is Anthropic's *structured note-taking* pattern — notes persisted outside the context window and pulled
back in later — with two deltas: the state is per-run, and the update protocol is harness-enforced.

## Alternatives considered

- **Trust model discretion / freeform notes in context.** Rejected: small local models do not keep this
  discipline on their own; freeform notes grow unboundedly and survive trimming no better than tool results.
- **Bigger model or cloud fallback.** Rejected: local-first is a product constraint, and — as
  [ADR-0003](ADR-0003-tool-guards-at-dispatch-boundary.md) records — switching models changed the flavor
  of failures rather than removing them.
- **Per-conversation scratchpad carried across turns** (the original `a0be4c2` shape). Rejected after a user
  message and a scheduled task arriving together clobbered each other's state; per-run isolation is what
  makes concurrency safe.
- **Retrieval over past tool results instead of explicit notes.** Not pursued: retrieval cannot cheaply
  reconstruct "current step / next step" plan orientation, and it reintroduces unbounded prompt cost.

## Consequences

**Positive:**

- Long tool chains stay coherent within tight token budgets; working-memory prompt cost is constant.
- Final answers are grounded in recorded facts; honest failure reports ship (a text-only stop after real
  work is a clean exit).
- Per-run isolation makes concurrent runs and delegation safe with no clearing protocol.

**Downides (honest):**

- The model spends calls writing notes — and can over-comply, burning the whole budget on bookkeeping
  (the token-loop failure, PR #9; see ADR-0002).
- Protocol enforcement is a real subsystem with its own failure modes: the June 2026 incident
  (run-297119136c) was partly caused by the harness's own tool-surface logic.
- Facts the model fails to write are *silently* absent from the final answer — the relaxed path logs the
  miss but ships anyway.
- Maintenance cost is nontrivial: limits, templates, a protocol state machine (PR #46 `ScratchpadMachine`),
  and pinned tests.