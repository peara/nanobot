# Architecture Decision Records

ADRs capture the *why* behind shipped design decisions in nanobot: the context that forced a choice, the decision, the alternatives that were rejected, and the honest consequences — including downsides. ARCHITECTURE.md and the subsystem docs describe *what* the system does; these records describe *why it is built that way and what it cost*.

Most records here are **retroactive**: the decisions shipped first and were documented later, mined from git history, commit messages, and the issues/incidents that motivated them. Dates and commit references are verified against the repository — no invented history.

## Index

| ADR | Decision | Status |
|-----|----------|--------|
| [ADR-0001 — Structured scratchpad as harness-owned working memory](ADR-0001-scratchpad-as-harness-owned-working-memory.md) | Per-run structured working memory with fixed budgets, injected into every prompt at bounded cost, with a dedicated finalize exit path — because a small local model cannot stay oriented through long tool chains on its own. | Accepted (retroactive) |
| [ADR-0002 — Asymmetric harness-enforced protocol compliance](ADR-0002-asymmetric-protocol-enforcement.md) | Enforce the scratchpad protocol in the harness, graded by recoverability: hard abort only for narrative-without-work, lenient synthetic append for work-without-notes, and tool-surface control that handles both under-compliance and the over-compliance token loop. | Accepted (retroactive) |
| [ADR-0003 — Tool guards at the dispatch boundary](ADR-0003-tool-guards-at-dispatch-boundary.md) | Validate and intercept every tool call at one boundary — `SchemaValidationGuard` pre-call against declared schemas, `WebScriptGuard` pre+post for script-flow lies — because small local models emit malformed arguments no matter which model is chosen. | Accepted (retroactive) |
| [ADR-0004 — Four graded exit paths for the agent loop](ADR-0004-four-graded-exit-paths.md) | Exit the agent loop on four paths graded by what actually happened — implicit text stop, scratchpad finalize, a soft landing that synthesizes partial progress at the 30-call limit, a hard abort only for the provably stuck 3× identical repeat — with an empty-reply fallback so no exit ships silence. | Accepted (retroactive) |
| [ADR-0005 — Message-history surgery for bounded, cache-friendly prompts](ADR-0005-message-history-surgery.md) | Assemble every prompt by structural surgery on the message list — trim to the last complete tool round behind a byte-stable prefix, merge consecutive same-role messages, hoist system messages and the static prompt first — so cost stays bounded and prefix-cacheable without windowing or summarizing. | Accepted (retroactive) |
| [ADR-0006 — Three stores split by access pattern](ADR-0006-three-stores-split-by-access-pattern.md) | Right store for the job: SQLite for exact replay, ContextStore for mutable scoped state, one native multi-collection VectorStore for associative recall — with mem0 demoted from primary interface to the contained backend of the memories collection. | Accepted (retroactive) |

## Conventions

- File name: `ADR-NNNN-<slug>.md`, numbered in the order written; numbers are never reused or renumbered.
- Sections per record: Context → Decision → Alternatives considered → Consequences → References (max 3 durable links). Consequences must state real downsides, not only benefits.
- Status is one of: Proposed, Accepted, Accepted (retroactive), Superseded (with a pointer to the superseding record). Retroactive records name the single most relevant PR or issue in the status line.
- Body prose states evidence as timeless facts — forces and insight, not changelogs. Commit hashes, dates, and PR/issue/incident identifiers live only in the status line and References, verified via `git log` / `gh` — no invented history.
- ADRs are immutable once Accepted: a changed decision gets a new record that supersedes the old one.