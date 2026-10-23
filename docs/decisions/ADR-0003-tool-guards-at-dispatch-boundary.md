# ADR-0003: Tool guards at the dispatch boundary for small-model JSON tolerance

- **Status:** Accepted (retroactive)
- **Shipped in:** `SchemaValidationGuard` in `f27749b` (2026-06-03, relates to #40); `WebScriptGuard` lifecycle in `3ffed73` (2026-05-13), hardened in `ed33318` (2026-05-14). Context receipts: `381bf85` (2026-03-08), `6d7011a` (2026-05-18), `c6a8c8f` (2026-06-03). Current implementation: `src/nanobot/agent_tool_guards.py`

## Context

Local-first means living with what small models actually emit. The original model, `gpt-oss:120b` (per the
repo-start example config), "failed miserably at tool calling, often failed to generate correct JSON"; one
day after the scratchpad landed, `381bf85` (2026-03-08) stopped enforcing structured JSON in tool-enabled
turns just to stabilize the flow. Switching to the gemma family did not end the failures — it changed their
flavor:

- `6d7011a` (2026-05-18): `json_schema` mode with an empty schema caused gemma to return **empty strings**
  for mem0's structured prompts, silently breaking memory saves.
- `c6a8c8f` (2026-06-03): `gemma4:31b-cloud` returned a **bare JSON array at the root** despite
  `response_format: json_schema` strict mode, crashing evaluator parsers.

And the sharpest receipt, which directly motivated this ADR — `f27749b` (2026-06-03): the model called
`skill__delete` with `skill_id` when only `name` is declared. The unknown field was **silently dropped**,
the tool ran with missing required fields, and the model received a misleading "not found" error it could
not self-correct from — a wrong-shape call that *looked* like a data problem. The lesson: switching models
once changed the shape of the failures but did not stop them; the defense moved from model choice to
hardening the boundary where model output meets tool input.

## Decision

Validate and intercept at the **dispatch boundary** — one place, before any tool code runs. Every tool call
passes a guard chain (`agent_run.py`: `[*host_guards, WebScriptGuard(), SchemaValidationGuard()]`), with a
`pre_call` hook before dispatch and a `post_result` hook after:

- **`SchemaValidationGuard` (pre-call).** Validates arguments against the tool's declared JSON schema —
  missing required fields (None or empty string counts as missing), `additionalProperties: false`
  violations, field-name suggestions via Levenshtein distance ("Did you mean…?") — and shipped with a
  schema audit adding `additionalProperties: false` to ten tools that read fixed keys without locking them.
- **`WebScriptGuard` (pre + post).** The same boundary also catches semantic lies, not just shape errors.
  Pre-call: blocks JavaScript submitted to the Python-only `web__create_script` ("…JavaScript syntax
  detected. Use: async def script(page, params) -> dict."), normalizes `web__invoke_script` params
  (string → dict), and blocks false "script saved" claims in `memory__save` after a failed create.
  Post-result: tracks whether the turn actually produced usable web data, can force-finalize with a
  synthesized reply when it did, and rewrites finalize replies that claim "the data didn't survive" in
  contradiction of the turn's real results. Guards share one `ToolCallContext` per turn, so post-result
  checks can see what pre-call checks saw.

Errors are structured and correctable, never exceptions and never silent drops: a blocked call never raises
into the run — the guard's block payload (`ok: false` + error code + message + key diffs) is returned to
the model **as the tool result**, in-band feedback it can act on in the next round.

## Alternatives considered

- **Per-tool validation inside each tool.** Rejected: every tool would reimplement tolerance logic; the dispatch boundary already holds the schema — one place enforces, every tool benefits.
- **Let it fail and let the model retry.** Rejected on the `f27749b` receipt: silent field drops produce misleading downstream errors ("not found") the model cannot map back to its mistake.
- **Stricter `response_format` enforcement (json_schema everywhere).** Rejected by evidence: small models break strict mode anyway (empty strings, bare arrays); `381bf85` removed structured-JSON enforcement in tool turns for exactly this reason.
- **Bigger model.** Rejected on the local-first constraint — and empirically insufficient: two model families, same class of failures in different shapes.

## Consequences

**Positive:**

- Wrong-shape calls fail fast with self-correctable errors; the model can recover on its next round instead
  of chasing misleading tool output.
- The guard chain is extensible (`host_guards` + built-ins) and orthogonal to tool implementations — adding
  a guard requires no tool changes.
- One boundary catches both schema noise and semantic lies, which individual tools could not see (only the
  boundary knows turn-level state like "the create actually failed").

**Downides (honest):**

- Several guard checks are heuristic pattern lists (JavaScript markers, "data didn't survive" phrasings) —
  they can false-positive on legitimate text or miss paraphrases; they are string matching, not parsing.
- Two sources of truth (JSON schemas + guard heuristics) must be kept in sync; the `f27749b` audit found
  ten tools that had never locked their schemas, and skill tools were deliberately left unlocked.
- `post_result` force-finalize makes guard behavior part of run control flow — a guard bug can end a
  user's turn, so these paths carry pinned regression tests.
- The boundary cannot fix generation-side failures: empty strings and bare arrays from `json_schema` mode
  still had to be fixed at the config layer (`6d7011a`), not by guards.