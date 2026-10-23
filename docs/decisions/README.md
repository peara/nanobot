# Architecture Decision Records

ADRs capture the *why* behind shipped design decisions in nanobot: the context that forced a choice, the decision, the alternatives that were rejected, and the honest consequences — including downsides. ARCHITECTURE.md and the subsystem docs describe *what* the system does; these records describe *why it is built that way and what it cost*.

Most records here are **retroactive**: the decisions shipped first and were documented later, mined from git history, commit messages, and the issues/incidents that motivated them. Dates and commit references are verified against the repository — no invented history.

## Index

| ADR | Decision | Status |
|-----|----------|--------|
| [ADR-0001 — Structured scratchpad as harness-owned working memory](ADR-0001-scratchpad-as-harness-owned-working-memory.md) | Per-run structured working memory with fixed budgets, injected into every prompt at bounded cost, with a dedicated finalize exit path — because a small local model cannot stay oriented through long tool chains on its own. | Accepted (retroactive) |
| [ADR-0002 — Asymmetric harness-enforced protocol compliance](ADR-0002-asymmetric-protocol-enforcement.md) | Enforce the scratchpad protocol in the harness, graded by recoverability: hard abort only for narrative-without-work, lenient synthetic append for work-without-notes, and tool-surface control that handles both under-compliance and the over-compliance token loop. | Accepted (retroactive) |
| [ADR-0003 — Tool guards at the dispatch boundary](ADR-0003-tool-guards-at-dispatch-boundary.md) | Validate and intercept every tool call at one boundary — `SchemaValidationGuard` pre-call against declared schemas, `WebScriptGuard` pre+post for script-flow lies — because small local models emit malformed arguments no matter which model is chosen. | Accepted (retroactive) |

## Conventions

- File name: `ADR-NNNN-<slug>.md`, numbered in the order written; numbers are never reused or renumbered.
- Sections per record: Context → Decision → Alternatives considered → Consequences. Consequences must state real downsides, not only benefits.
- Status is one of: Proposed, Accepted, Accepted (retroactive), Superseded (with a pointer to the superseding record).
- Every retroactive record cites the commit/PR (and, where relevant, issue or incident) where the decision shipped, verified via `git log` / `gh`.
- ADRs are immutable once Accepted: a changed decision gets a new record that supersedes the old one.