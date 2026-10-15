# Scratchpad

Structured working memory for the bot's turn — how it keeps track of goals, progress, and findings during multi-step tasks.

## Overview

Local and smaller LLMs lose track in long tool-calling chains. The scratchpad gives the bot an explicit place to record what it's doing, what it's learned, and what comes next. It's not long-term memory — it's per-run working state that gets injected into the prompt so the model stays oriented.

The scratchpad is not optional for tool-calling turns: the **harness enforces a protocol** — init at the start of a work turn, append after each tool result, finalize before the answer. If the model skips a step, the harness notices and corrects it.

## State fields

| Field | Type | Limit | What it holds |
|-------|------|-------|---------------|
| `goal` | string | 600 chars | What the bot is trying to accomplish this turn |
| `context` | string | 1200 chars | Running summary of progress and key information |
| `known_facts` | string[] | 30 items, 600 chars each | Facts extracted from tool results |
| `current_step` | string | 600 chars | What the bot is doing right now |
| `next_step` | string | 600 chars | What the bot plans to do next |
| `tool_journal` | string[] | 30 items, 600 chars each | Log of tools called and what they returned |
| `updated_at` | string | — | Auto-set timestamp (bot timezone) |

All string fields are clipped to their limits. Arrays are deduplicated and keep only the most recent entries.

## Storage and scope isolation

The scratchpad lives in the `contexts` table, keyed by run:

- Inside a subagent run: `contexts(scope_type="subagent_run", scope_id=run_id, key="scratchpad")` — **every run gets its own scratchpad**, so concurrent runs on the same chat scope never clobber each other's working state.
- Outside any run (e.g. the `/scratchpad` command): falls back to `contexts(scope_type="chat", scope_id=scope, key="scratchpad")`.

Each new run starts with an empty scratchpad — there is no cross-turn carry-over and nothing to clear at turn start. (This replaced the old design where a single chat-scoped scratchpad was cleared at the start of each user message; per-run isolation is what makes concurrent runs on the same scope — e.g. a user message and a scheduled task arriving together — safe. Without it, both runs would overwrite each other's goal and both would loop without reaching finalize.)

## Mode lifecycle

Three modes, corresponding to the phases of a task:

### `init`

Called at the start of a work turn. Resets the scratchpad to empty state and sets the goal. Any existing scratchpad content is wiped.

```json
{"mode": "init", "goal": "Find the weather in Bangkok and compare with yesterday"}
```

### `append`

Called after each tool result (and before the next tool call). Updates fields incrementally — new `known_facts` and `tool_journal` entries are appended and deduplicated, not replaced. Other fields (`goal`, `context`, `current_step`, `next_step`) are overwritten if provided.

```json
{"mode": "append", "current_step": "Looked up Bangkok weather", "next_step": "Compare with yesterday", "known_facts": ["Bangkok: 34°C, humid"]}
```

### `finalize`

Signals that the bot has gathered everything it needs and no more tool calls will follow. The scratchpad state is preserved, and the agent loop makes one explicit no-tools LLM call using the `finalize_response` prompt template — which includes the goal and a summary built from the scratchpad's `context`, `known_facts`, and `tool_journal`.

```json
{"mode": "finalize", "context": "Bangkok 34°C today vs 31°C yesterday, 3°C warmer"}
```

**Critical**: Finalize means finalize. Once called, the model cannot use tools. All key findings must be in the scratchpad *before* finalizing, because the final answer is built from the scratchpad summary — anything missing is lost.

## Scratchpad protocol enforcement

The scratchpad isn't optional for tool-calling turns. After every external tool result, the model must call `session__scratchpad_write` (append or finalize) before requesting another tool. This is the **scratchpad protocol**.

The enforcement loop tracks cross-round state (`ScratchpadProtocolState` in `agent_run.py`): whether the model saw the init requirement, whether an external tool ran since the last successful append, and how many protocol violations have accumulated this turn. Enforcement has two levels:

- **Strict (after a scratchpad round)** — If the model called `session__scratchpad_write(mode=init)` but then stopped with a non-empty text answer without ever using an external tool or finalizing, that text answer is a protocol violation: it's a "hallucinated completion" — answering without doing the work or formally ending the turn.
- **Relaxed (after an external tool ran)** — If an external tool has run since the last scratchpad append, the next text-only stop gets a synthetic append built from the model's content (stripping think tags), rather than a hard violation. The model is nudged, not punished, for forgetting the append — but the harness records the state anyway.

If the model violates the protocol (e.g. requests another external tool without the intervening append), the system injects a correction message:

> Protocol violation: after any external tool result, call session__scratchpad_write first (mode='append' or mode='finalize') before requesting another external tool.

The system retries up to 2 times (`MAX_SCRATCHPAD_PROTOCOL_RETRIES`). If the model still violates the protocol, the turn is aborted with:

> I got stuck enforcing scratchpad updates in this turn. Please try again.

The init requirement is enforced with bounded recovery too: if a work turn (external tool called) never saw `mode=init`, the harness nudges once before treating later text as an answer.

## How the scratchpad is injected

The scratchpad appears in the prompt in two places:

1. **System message** (`scratchpad_system` template) — full scratchpad state as JSON, labeled as private execution state that should never be revealed verbatim to the user.
2. **User-role message** (`scratchpad_user` template) — appended as the **last message** before the model generates, so the model is clearly prompted to respond to the current state. (Despite the helper's historical name `scratchpad_assistant_message`, it renders with role `user`.)

Both templates are rendered by PromptStore and use `{state_json}` as the variable.

## Finalize and exit paths

The agent loop has four exit conditions. Two of them use the scratchpad:

1. **Scratchpad finalize** — The model calls `session__scratchpad_write(mode=finalize)`. The loop breaks and makes one no-tools LLM call with the `finalize_response` prompt, which includes `{goal}` and `{summary}` built from the scratchpad.
2. **Tool call limit** (30 calls) — The model hits `MAX_TOOL_CALLS_PER_TURN`. Instead of a hard abort, the loop makes a soft-landing no-tools call using the `tool_call_limit_finalize` prompt, which includes `{goal}` and `{summary}` from the scratchpad. The model gets to summarize partial progress.

The other two exit paths don't use the scratchpad:

3. **Implicit text response** — Model returns text with no tool calls. Loop exits with the text reply.
4. **Identical tool call repeat** (3x) — Hard abort with a fixed error reply.

The finalize path is critical for local/smaller models: without an explicit no-tools call, models tend to hallucinate `scratchpad_write(mode=init)` after finalize, which wipes all accumulated state.

## Commands

| Command | What it does |
|---------|-------------|
| `/scratchpad` | Show the chat-scoped scratchpad state (no run context) |
| `/scratchpad show` | Same as above |
| `/scratchpad clear` | Reset the chat-scoped scratchpad to empty state |

Note: these operate on the **chat-scoped fallback** scratchpad, not any active run's scratchpad — a run's scratchpad is only visible through the context store (debug CLI: `just ctx <scope>` with run context).

## Limits and clipping

| Limit | Value | What it affects |
|-------|-------|-----------------|
| `MAX_FIELD_CHARS` | 600 | `goal`, `current_step`, `next_step`, individual facts/journal entries |
| `MAX_CONTEXT_CHARS` | 1200 | `context` field (wider allowance for summaries) |
| `MAX_KNOWN_FACTS` | 30 | Maximum entries in `known_facts` |
| `MAX_TOOL_JOURNAL` | 30 | Maximum entries in `tool_journal` |
| `MAX_TOOL_CALLS_PER_TURN` | 30 | Total tool calls per turn before soft landing |
| `MAX_IDENTICAL_TOOL_CALL_REPEATS` | 3 | Same tool call repeated before hard abort |
| `MAX_SCRATCHPAD_PROTOCOL_RETRIES` | 2 | Protocol violation retries before abort |

When `known_facts` or `tool_journal` exceed their limits, only the most recent entries are kept (older entries are dropped). This prevents unbounded growth during long tool chains.

## Prompt templates

The scratchpad uses four PromptStore templates:

| Template | Variables | Purpose |
|----------|-----------|---------|
| `scratchpad_system` | `state_json` | System message with full state |
| `scratchpad_user` | `state_json` | Last user-role message reinforcing current state |
| `finalize_response` | `goal`, `summary` | Final no-tools call after finalize |
| `tool_call_limit_finalize` | `goal`, `summary` | Final no-tools call after hitting tool limit |

The `summary` variable is built from the scratchpad's `context`, `known_facts` (capped at 15 for the prompt), and `tool_journal` (capped at 10).