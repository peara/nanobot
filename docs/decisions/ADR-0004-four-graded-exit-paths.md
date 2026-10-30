# ADR-0004: Four graded exit paths for the agent loop

- **Status:** Accepted (retroactive) — shipped incrementally on main in direct commits; no tracking PR exists (see References)

## Context

A tool-calling loop needs a termination policy, and a small local model gives it plenty to terminate on: it rambles past useful work until the budget is gone, it loops on the same call against pages that redirect or return junk, and it occasionally stops with empty content — a generation glitch, not an answer (strict `json_schema` mode made some models return empty strings outright; see [ADR-0003](ADR-0003-tool-guards-at-dispatch-boundary.md)). Local-first prices every wasted round in user-visible latency, and the scratchpad protocol needs the loop to recognize its own exits — a finalize must end the run with a real answer, not be treated as one more call in the chain.

The load-bearing observation: these failures are not equally recoverable. A model that stopped talking after real work has something worth shipping; a model three rounds into an identical loop has provably nothing. One uniform abort rule cannot serve both.

## Decision

Give the loop four exit paths, graded by what actually happened — plus one fallback so no exit ever ships silence:

- **Implicit text stop.** Text with no tool calls is the model's own stop signal; it exits by default. The scratchpad protocol adds its verdict on top — clean exit after work, bounded abort after init-only narrative ([ADR-0002](ADR-0002-asymmetric-protocol-enforcement.md)).
- **Scratchpad finalize.** `mode=finalize` is intercepted in-process; once the protocol machine sees it, the round routes to one explicit no-tools LLM call whose prompt is built from the scratchpad — goal plus a summary of `context`, `known_facts`, `tool_journal`. The final answer is assembled from the notes ([ADR-0001](ADR-0001-scratchpad-as-harness-owned-working-memory.md)).
- **Soft landing on the tool-call limit.** Hitting `MAX_TOOL_CALLS_PER_TURN = 30` is not evidence of a stuck run — long extraction flows legitimately burn many calls — so the loop makes one no-tools call with a limit-finalize prompt built from the same scratchpad, letting the model synthesize partial progress. Only if that call also comes back empty does a fixed abort reply ship.
- **Hard abort on the identical-repeat guard.** A round whose full call signature (function name plus canonicalized arguments) repeats identically three times is stuck by definition; no further LLM call is spent. A fixed reply tells the user the source may be redirecting and asks for another source or a rephrase.
- **Empty-reply fallback.** Any exit whose reply is empty or whitespace — the main-loop text stop, the finalize call — ships a fixed fallback message instead of silence.

## Alternatives considered

- **Uniform hard abort on any exit** (the original shape: both the limit and the repeat guard ended in fixed replies). Rejected: the limit case carries recoverable partial work, and aborting discards it for a dead-end message the user can only retry from scratch.
- **No budget — trust the model to terminate.** Rejected: a local model loops on unhelpful sources well past any useful point; the guard's receipts are redirecting pages and unchanging results.
- **Auto-retry instead of abort.** Rejected: an identical call against the same failing source reproduces the same failure; the honest move is to surface it and let the user change the input.

## Consequences

**Positive:** every failure mode terminates with a user-visible, honest message — no silent hangs, no silence; partial work is preserved and summarized at the cost of one bounded no-tools call; the repeat guard costs a signature comparison, not an LLM round; and all four paths compose with the protocol machine and the guard chain rather than bypassing them.

**Downsides (honest):**

- The soft landing spends one more LLM call on a run already thirty calls deep — real added latency for a user who has been waiting throughout.
- The limit is a blunt per-turn count with a tuned, not derived, value: a legitimate multi-part task gets the same 30 calls as a stuck one.
- The repeat guard compares canonicalized argument strings: a loop that mutates one character of a URL evades it — and two calls differing only in key order count as identical, the canonicalization's intended other side.
- The finalize and soft-landing calls are made by the same small model that just failed — they can come back empty or narrative too, which is why the fallback exists; a fallback reply is still a dead end the user must retry.
- Four graded paths plus the protocol abort and cancellation is real cognitive surface — the exit contracts are pinned by protocol-walk tests, not prose.

## References

- Loop guard origin (`MAX_TOOL_CALLS_PER_TURN`, `MAX_IDENTICAL_TOOL_CALL_REPEATS`, abort replies): commit `37f767f`.
- Soft landing on the tool-call limit: commit `b873619`.
- Empty-reply fallback: commit `5df7c82`.