# ADR-0005: Message-history surgery for bounded, cache-friendly prompts

- **Status:** Accepted (retroactive) — shipped incrementally on main in direct commits; no tracking PR exists (see References)

## Context

Long tool chains fatten the prompt with page dumps and search results, and every round re-sends the whole history to a small local model that degrades long before its nominal context limit — with the latency and token cost to match. But trimming a tool-calling conversation is not trimming text: the OpenAI message format has structural invariants (an assistant message carrying `tool_calls` must be immediately followed by its tool results), and a naive cut can orphan either half into an invalid payload. A cut can also drop the one message that matters most — the user's request. And on the other side of the ledger, OpenAI-compatible runtimes cache prompts by identical leading bytes, so an assembly that churns early messages re-pays full prompt cost every round.

What makes aggressive trimming safe at all is [ADR-0001](ADR-0001-scratchpad-as-harness-owned-working-memory.md)'s scratchpad: the distilled record of earlier rounds — `known_facts`, `tool_journal` — is already injected into every prompt at bounded cost. Raw intermediate rounds are redundant in a way plain histories are not.

## Decision

Do structural surgery on the message list at assembly time — one single assembly path for every LLM call in the run — not windowing, not summarization:

- **`trim_to_last_tool_round`.** Keep everything before the first tool round (system messages, the user's request, early text) plus the last complete tool round (the assistant's tool calls and their results); drop all intermediate rounds. The cut lands on round boundaries by construction, so the payload is always structurally valid, and the surviving prefix is byte-identical round over round — exactly what a prefix cache keys on. The initial call of a run is deliberately untrimmed: the caller's history may hold earlier turns' tool rounds that this run needs. (That history arrives already bounded at load time — first a message-count limit, then a character budget; the structural trim is the in-run policy.)
- **Role merging.** Consecutive same-role messages — except system and tool — are merged with blank-line joins. Message construction legitimately produces these (the scratchpad state message and finalize prompts are both user-role), and some OpenAI-compatible backends reject or mishandle consecutive same-role messages.
- **Static-prefix-first ordering.** All system messages are hoisted to the front in order (empty ones dropped), and the orchestrator's system prompt is split into a static part (identity, rules) ahead of a dynamic part (time, live context). Leading bytes stay byte-identical across rounds and turns; everything volatile — scratchpad state message, finalize prompts — is pushed to the tail, landing last.

`messages` is the single source of truth: the prepared list is derived inside this one assembly path immediately before each LLM call and never carried across loop iterations — a stale prepared list is precisely how a mid-run correction message can silently fail to reach the model.

## Alternatives considered

- **Sliding window (keep the last N messages).** Rejected: a window boundary can split an assistant `tool_calls` message from its tool results — an invalid payload — and can keep stale tool noise while dropping the user's actual request; it also churns the prefix every round, defeating prefix caching.
- **LLM summarization of old rounds.** Rejected: an extra LLM call per run on a local model, with hallucination injected straight into the prompt — and it duplicates the scratchpad, which already maintains the distilled record at bounded cost.
- **Send everything and trust the context window.** Rejected: small local models degrade long before nominal capacity, and every round re-pays full prompt cost — ADR-0001's whole premise.

## Consequences

**Positive:** per-round prompt cost stays bounded and roughly constant however long the chain; the payload is structurally valid by construction; the byte-stable prefix maximizes prefix-cache hits; and dropped rounds stay recoverable through the scratchpad rather than being lost outright.

**Downsides (honest):**

- The trim is only as lossless as the notes: intermediate rounds are invisible to the model afterward, so a skipped or thin scratchpad append (the relaxed path logs and proceeds — [ADR-0002](ADR-0002-asymmetric-protocol-enforcement.md)) means real loss, and the model can re-call tools it already used.
- The stable prefix grows across a multi-turn conversation up to the outer character budget, eroding the cache benefit over long sessions; nothing measures cache-hit rates, so the gain is a sound bet, not an observed number.
- The initial call is untrimmed by design, so the largest single prompt of a run is the first one.
- Role merging is structural, not semantic — two distinct messages become one long one, and a backend that did give consecutive same-role messages distinct treatment loses that distinction.

## References

- `trim_to_last_tool_round` born with the extracted AgentRun: commit `606a210`.
- Static + dynamic prompt split for cache efficiency: commit `8a34a32`.
- Current semantics: [docs/MESSAGE_ASSEMBLY.md](../MESSAGE_ASSEMBLY.md).