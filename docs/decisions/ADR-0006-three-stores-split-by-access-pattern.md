# ADR-0006: Three stores split by access pattern

- **Status:** Accepted (retroactive), shipped in issue #16 (native multi-collection VectorStore)

## Context

Nanobot needs three kinds of remembering, and they have nothing in common: **exact replay** — what was said, scheduled, planned, and run, append-mostly and never rewritten; **mutable working state** — scratchpads, pointers, traces, run status, overwritten freely and expirable; **associative recall** — facts, preferences, skill and script embeddings, found by meaning rather than key. The first long-term memory was mem0 behind an MCP server: one dependency doing LLM-mediated extraction and dedup over its own Qdrant. Its receipts piled up on the same themes — LLM-mediated dedup `NONE`'d specific data (verbatim numbers replaced by nulls) until pinned with `infer=False`; the search API churned keyword arguments across releases until the tools needed a compat shim; per-instance Qdrant clients contended on directory-level lock files, surfacing as swallowed 409s; and every new consumer — skills, web scripts — meant another full mem0 stack with its own client pointed at the same on-disk data.

The lesson was structural, not about one bug: memory interfaces must be split by access pattern, and the associative layer needs exactly one owner.

## Decision

**Right store for the job** — three stores, each matching one access pattern:

- **SQLite for the exact.** Transcript, scheduler rows, plans, skill definitions, prompts, tool stats, subagent runs, notebook: transactional, append-mostly, replayable — the record of what happened.
- **ContextStore for the mutable.** One `contexts` table of scoped JSON values keyed `(scope_type, scope_id, key)` with optional TTL: scratchpads live under `subagent_run:{run_id}` (per-run isolation, [ADR-0001](ADR-0001-scratchpad-as-harness-owned-working-memory.md)) alongside pointers, traces, and run status. Overwritable, expirable, never appended-to — working state, not history.
- **VectorStore for the associative.** One native multi-collection store over a single shared Qdrant client and a single embedder: `memories`, `skills`, `web_scripts`. Same-process consumers get the store injected; the out-of-process web-scripts MCP server gets its own config and data directory, because Qdrant's lock file is directory-level — two clients on one path contend even across collections. The `memory__*` tools are direct built-ins on the tool registry, not an MCP server.

**And mem0 demoted, not abolished.** Direct mem0 usage is gone from skills and web scripts (native `add_text`/`search_text` paths); mem0's `Memory` survives only as the backend of the `memories` collection, hidden behind the VectorStore boundary with its inference pinned. The dependency is contained to one collection behind one interface — swapping it later means re-implementing seven tool semantics against the native path.

## Alternatives considered

- **A single store for everything.** Rejected: exact and associative have incompatible shapes — a transcript store that must also do semantic recall drags an embedding stack into every read, and a vector store as system of record loses append-only replay (upserts mutate, dedup merges).
- **Keep mem0 as the primary interface** (its API everywhere, per-consumer `Memory` instances). Rejected on the receipts: dedup destroying verbatim data, keyword-arg churn, and N clients contending on one lock file — the failure the lock-contention incident documented.
- **SQLite full-text search instead of vectors.** Rejected: associative recall must be paraphrase-tolerant — "find things like X" — and lexical matching misses exactly that.

## Consequences

**Positive:** each store is right-sized for its access pattern; the transcript stays append-only and exact; working state is cheap to overwrite and expires on its own; semantic recall shares one client and one embedder, so in-process consumers cannot contend; and the memory tools are first-class built-ins — inside the guard chain ([ADR-0003](ADR-0003-tool-guards-at-dispatch-boundary.md)) and tracing, not a subprocess hop away.

**Downsides (honest):**

- Three stores is three failure domains and real schema surface; "where does X live" is a genuine question that needs the store table in ARCHITECTURE.md to answer.
- mem0 is still here: the memories collection runs on it behind `get_collection`, so its quirks still surface through the memory tools — the containment is an interface boundary, not a removal.
- The lock-file lesson forces a second Qdrant data directory for the out-of-process web MCP server; a consumer pointed at the wrong path gets silent contention, not an error.
- Embedding-dimension mismatches between embedder config and existing collections surface as confusing search failures at runtime, not at startup.
- There is no unified retrieval across the three stores — "what do you know about X" fans out explicitly; unified retrieval remains roadmap work.

## References

- mem0-backed MCP memory tools (the replaced baseline): commit `23a0cfb`.
- ContextStore (scoped mutable state): commit `30dbdeb`.
- Native multi-collection VectorStore + direct built-in `memory__*` tools: issue #16, commit `13a6c12`.