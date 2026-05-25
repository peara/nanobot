# NanoBot roadmap

Living document aligned with [ARCHITECTURE.md](ARCHITECTURE.md). Phases are **priorities**, not promises; order can change.

---

## Principles

- **Personal, lean runtime**: favor small prompts, clear stores, and explicit policies over “send everything.”
- **Orchestrator + workers**: main agent coordinates; specialized agents handle bounded tasks with shared scratchpad/tool access.
- **Right store for the job**: SQLite for exact and structured data; mem0 for semantic long-term memory; scratchpad for volatile plans.
- **Browser-first personal automation**: rich web workflows via MCP (e.g. Playwright), without positioning the product as full-device control.

---

## Phase A — Foundations (mostly in place)

- [x] Chat channel abstraction (Telegram).
- [x] Single-agent loop with MCP tool calls and persistence.
- [x] Scratchpad / plan-adjacent context in `ContextStore`.
- [x] Scheduler store + runner integrated with core.
- [x] SQLite transcript and context blobs.
- [x] Optional mem0-backed memory MCP; browse trace hooks for Playwright tools.

**Hardening that still pays off**

- Context budgeting documented in code (limits, summarization triggers)—tie directly to architecture’s “intelligent context.”
- Optional: ~~LLM lifecycle hooks (`before_llm_call` / `after_llm_call`) for observability and token accounting.~~ Done — implemented as `nanobot.llm.io` logger (see docs/logging.md).

---

## Phase B — Orchestration and sub-agents

**Goal**: The main agent can **delegate** a job to a sub-agent that runs its own short loop (or a bounded number of turns) with a **defined scope**, **shared or forked scratchpad**, and the **same MCP surface** (or an allowlisted subset).

**Open design choices** (to resolve when implementing)

- **Invocation shape**: tool call (“run sub-agent with this brief”) vs internal API vs separate lightweight process.
- **Identity and audit**: how sub-agent turns appear in SQLite (flatten into parent vs separate correlation id).
- **Budgets**: max turns, max tokens, and mandatory handoff summary back to orchestrator.

**Deliverables (sketch)**

- [ ] Sub-agent contract (inputs: goal, tools allowlist, budget; outputs: summary + optional artifacts).
- [ ] Persistence story for delegated runs.
- [ ] User-visible behavior: orchestrator explains handoffs when helpful (configurable).

---

## Phase C — Context policy as product

**Goal**: Make “manage context intelligently” **explicit and tunable** (config + hooks), not only implicit in code.

- [ ] Documented defaults: what goes into each prompt slice, max sizes, when to suggest `/ctx` / scratchpad edits.
- [ ] Optional automatic compaction (summarize old thread into scratchpad or mem0 with user-aligned rules).
- [ ] Clear boundaries: what must never be sent to mem0 vs what is safe to recall semantically.

---

## Phase D — Task dashboard (exploratory)

**Goal**: A **Linear-like** view of work items—likely backed by **SQLite**, surfaced through a **web UI** and/or **Telegram** commands, with links back into chat context.

**Not decided yet**

- Issue model (statuses, projects, recurring tasks vs one-off).
- How much the LLM writes directly to “issues” vs user-confirmed creation.
- Whether the dashboard is read-only v1 or full CRUD.

**Early steps**

- [ ] Data model sketch (`tasks` / `issues` table, relation to `scope` / chat).
- [ ] Minimal read API or export for a tiny UI.
- [ ] Integration point with scheduler (due dates → notifications).

---

## Phase E — Efficiency and deployment

- [ ] Profiling pass on hot paths (MCP connect, large context builds).
- [ ] Documented “small VPS” and “home box” deployment profiles (which MCPs on, memory backends).
- [ ] Optional: model routing (small model for triage, larger for tool-heavy turns)—only if it reduces average cost/latency without hurting quality.

---

## Parking lot

Ideas that are useful but not scheduled:

- Additional channels (Matrix, web chat, SMS).
- Richer calendar/news **integrations** beyond browser automation (official APIs where available).
- Federated or multi-user (explicitly out of scope for “personal agent” unless requirements change).

---

## How to use this file

When starting a larger change, add a one-line note under the relevant phase or open a short “Decision” subsection with date and outcome. Keep [ARCHITECTURE.md](ARCHITECTURE.md) in sync when the **conceptual** picture changes.
