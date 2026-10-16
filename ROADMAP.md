# NanoBot roadmap

Living document aligned with [ARCHITECTURE.md](ARCHITECTURE.md). Phases are **priorities**, not promises; order can change.

---

## Principles

- **Personal, lean runtime**: favor small prompts, clear stores, and explicit policies over "send everything."
- **Orchestrator + workers**: main agent coordinates; focused child agents handle bounded sub-tasks with their own scratchpad and skill matching, depth-capped at 1.
- **Right store for the job**: SQLite for exact and structured data; VectorStore (Qdrant) for semantic long-term memory; scratchpad for volatile per-run working state.
- **Production-shaped, lean infra**: cron + SQLite + one box is production enough for ≤3 users. No collector fleets, no K8s.

---

## Phase A — Foundations ✅ (complete)

- [x] Chat channel abstraction (Telegram, GitHub, File).
- [x] Single-agent loop with MCP tool calls and persistence.
- [x] Scratchpad / plan-adjacent context in `ContextStore` — per-run isolation, harness-enforced protocol.
- [x] Scheduler store + runner integrated with core.
- [x] SQLite transcript and context blobs.
- [x] Browse trace hooks for Playwright tools; config-driven logging with per-module handlers.
- [x] Structured LLM call logging via `nanobot.llm.io` (INFO one-liners + DEBUG full payloads).
- [x] `/stop` cancellation with CancellationToken propagation into delegated children.
- [x] Bot-owned SQLite notebook (`notebook__query`).
- [x] Web scripts: AST-validated sandboxed extraction scripts, semantically searchable.

---

## Phase B — Orchestration and sub-agents ✅ (shipped 2026-06)

**Goal**: the main agent delegates a job to a sub-agent that runs its own short loop with a **defined scope**, **own scratchpad**, and the **same MCP surface** (minus `delegate_task` itself).

Delivered:

- [x] `delegate_task` tool — control-plane dispatch (spec prepended, intercepted in the LLM loop).
- [x] Persistence for delegated runs — every message goes through a `SubagentRun` record; `parent_run_id` chains runs; runs are visible to `/status` and debug CLI.
- [x] Depth gating, triple-layered: spec strip at depth ≥ 1, spawn refusal past MAX_SUBAGENT_DEPTH (1), dispatch refusal in `run_delegate_task`.
- [x] Cancellation propagation — parent cancel cancels children mid-generation.

Deferred from the original sketch:

- [ ] Explicit budgets per delegated run (max turns, max tokens, mandatory handoff summary). The child currently inherits the same loop limits as the parent.
- [ ] Orchestrator-explains-handoffs UX (configurable).

---

## Phase C — Context policy as product

**Goal**: make "manage context intelligently" **explicit and tunable** (config + hooks), not only implicit in code.

- [ ] Documented defaults: what goes into each prompt slice, max sizes, when to suggest `/ctx` / scratchpad edits.
- [ ] Optional automatic compaction (summarize old thread into scratchpad or memory with user-aligned rules).
- [ ] Clear boundaries: what must never be sent to the vector store vs what is safe to recall semantically.

---

## Phase D — Observability: OTel-traced runs, SQLite-persisted (next up)

**Goal**: turn log-based observability into queryable, joinable traces, instrumented to the OpenTelemetry GenAI semantic conventions so the backend becomes a config decision, not a rewrite. Prerequisite for the eval gate, the ASR measurement lane, debugging stuck runs (issue #35), **and the artifact provenance in Phase E** — artifacts are traceable to runs only if LLM calls carry run_id spans.

**Shape (decided 2026-10-15):**

- [ ] Manual OTel instrumentation at four sites — `opentelemetry-sdk` only, no auto-instrumentation packages (one LLM call site, one tool dispatch point — manual is cheap and controls the attributes):
  - Agent run span (`SubagentManager.execute`) — `invoke_agent`, INTERNAL
  - LLM chat span (`LlmClient.chat`) — `chat`, CLIENT: model, tokens, finish_reason
  - Tool call span (`ToolCallDispatcher.dispatch`) — `execute_tool`, INTERNAL
  - Delegated child span (`run_delegate_task`) — parent context propagates for free
- [ ] Custom `SpanExporter` persisting spans to SQLite (trace_id, span_id, parent_span_id, run_id, kind, name, attrs, timing) — `just trace <run_id>` works with zero infra; add an OTLP exporter when a platform (Langfuse et al.) enters, same instrumentation, no rewrite.
- [ ] `gen_ai.*` attribute names wrapped in one `semconv.py` module, pinned to the vintage Langfuse documents — the conventions are Development-status and churning (token renames in 1.37, `invoke_agent` span-kind split in 1.41, repo move Jun 2026); the wrapper owns the strings and the migration window.
- [ ] run_id threading so spans join with `subagent_runs`, `tool_calls`, and scratchpad states.
- [ ] `just trace <run_id>` — debug CLI view: replay a run end-to-end (LLM rounds → tool calls → delegated children), plus per-run token accounting.
- [ ] Payload capture as span attributes behind a config flag, default off — `nanobot.llm.io` keeps full-payload logging duty; spans carry the skeleton + metadata. Redaction gate lives at attribute-setting time for when the flag turns on.
- [ ] Alerting on exporter failure (telemetry fails silently too) and anomalies (finish_reason=length spikes, empty-reply fallbacks).

Related open issues: #35 (observability for context overflow and stuck subagent debugging), #42 (MCP subprocess log surfacing).

---

## Phase E — Retrieval: unify + hybrid + artifact management (next after D)

**Goal**: one retrieval system instead of three incompatible one-off paths, plus indexed artifact management — every web output searchable and traceable to the run that produced it. This is the "artifact management integrated with RAG" probe answered with lived architecture, and the substrate the job-agent's postings matching reuses later.

**Today's fragmentation (verified in code, drives the design):**

| Path | Corpus | Method | Problem |
|---|---|---|---|
| `memory__search` | memories | Dense-only via mem0 `Memory.search()` | No retrieval prompt (issue #34), no lexical leg |
| `SkillVectorStore` | skills | Dense-only via `VectorStore.search_text` | Retrieval prompt + score threshold (good) — but a separate path |
| `WebScriptStore.search` | web scripts | Token-overlap scoring over all active scripts | Not RAG at all — loads everything into Python |
| *(none)* | web agent outputs | `data/web_agent/*.md` dumped to disk | Artifacts fall out of the pipeline, never indexed |
| *(none)* | notebook, transcripts | SQLite recency window | Never retrievable |

**Shape:**

- New `src/nanobot/retrieval/` package:
  - `service.py` — `RetrievalService.search(query, sources, limit)` — the one API every consumer calls
  - `lexical.py` — SQLite FTS5 wrapper (BM25 via `bm25()`) — zero new dependencies (FTS5 verified available in the project's SQLite 3.45.1)
  - `fusion.py` — RRF fusion, pure Python
  - `artifacts.py` — artifact manager: registry table (`artifact_id, run_id, scope, url, kind, title, created_at`) + indexing pass over web outputs
  - `indexers/` — per-source adapters emitting a uniform `IndexedDoc`
- The service is the seam the golden-set eval measures (plan week 3–4): hybrid BM25+dense fused with RRF, misrank → rerank decisions made against labeled data.
- Tokenizer note: FTS5 default `unicode61` handles Vietnamese fine; Japanese needs the `trigram` tokenizer — decide per corpus when postings ingestion lands (this is itself the bilingual-retrieval story).

**Ladder (each step shippable, no big-bang rewrite):**

- [ ] Artifact manager + artifact indexing — registry + chunk/index web outputs. New capability, breaks nothing, fills the artifact gap. Wants run_id spans from Phase D.
- [ ] Lexical leg for existing corpora (skills, scripts) — FTS5 index alongside the vector index.
- [ ] Hybrid `RetrievalService.search()` with RRF; the existing skill score-threshold pattern survives as a post-fusion filter option.
- [ ] Migrate paths one at a time: skill matching → web scripts (replacing the token-overlap stub) → memories last (touches mem0, riskiest).
- [ ] Deprecate the one-off paths once migrated.

**Decisions (pending user review):**

- "Artifact" scope: web agent outputs only for now; plan files / notebook rows / conversation digests join later only if a story demands (YAGNI).
- Memories migrate last; mem0 tools stay untouched until their step.
- Notebook/transcripts stay out of v1 — retrieval over them is a later ladder rung, not a rewrite.

Related open issues: #34 (retrieval prompt not applied to memory/web-script searches — superseded by unification), #32 (tool catalog semantic matching — later consumer of the same service).

---

## Phase F — Task dashboard (exploratory)

**Goal**: a **Linear-like** view of work items — likely backed by **SQLite**, surfaced through a **web UI** and/or **Telegram** commands, with links back into chat context.

Not decided yet: issue model, LLM-write vs user-confirmed creation, read-only v1 vs full CRUD. Note: issue #28 (Linear MCP server) and #30 (notebook) are adjacent — the notebook already covers "bot-owned structured data"; a dashboard would surface it.

- [ ] Data model sketch (`tasks` / `issues` table, relation to `scope` / chat).
- [ ] Minimal read API or export for a tiny UI.
- [ ] Integration point with scheduler (due dates → notifications).

---

## Phase G — Efficiency and deployment

- [ ] Profiling pass on hot paths (MCP connect, large context builds).
- [ ] Documented "small VPS" and "home box" deployment profiles (which MCPs on, memory backends).
- [ ] Optional: model routing (small model for triage, larger for tool-heavy turns) — only if it reduces average cost/latency without hurting quality.

---

## Parking lot

Ideas that are useful but not scheduled:

- Additional channels (Matrix, web chat, SMS).
- Richer calendar/news **integrations** beyond browser automation (official APIs where available).
- Federated or multi-user (explicitly out of scope for "personal agent" unless requirements change).
- Dynamic skill/tool loading (issue #29, research).
- Tool catalog store + semantic skill-tool matching (issue #32).
- Skill quality tracking and staleness detection (issue #37).

---

## How to use this file

When starting a larger change, add a one-line note under the relevant phase or open a short "Decision" subsection with date and outcome. Keep [ARCHITECTURE.md](ARCHITECTURE.md) in sync when the **conceptual** picture changes.