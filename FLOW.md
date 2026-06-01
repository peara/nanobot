# Runtime Flow (Current)

This document describes how `nanobot` currently processes a turn, how tool output is handled, and where scratchpad updates can be injected.

## 1) Incoming Message Path

Entry point:

- `src/nanobot/channels/telegram.py` receives Telegram text.
- Emits `IncomingMessage` to `BotCore.on_incoming()` in `src/nanobot/core.py`.

Command handling in `on_incoming()`:

- `/help`, `/ctx`, `/ctxfull`, `/reset`, `/plan` are handled directly.
- Non-command text goes to `_process(scope, user_text)`.

## 2) Normal Chat Turn (`_process`)

Current steps:

1. Persist user message in SQLite:
   - `ConversationStore.add_message(scope, "user", user_text)`
2. Persist lightweight context pointer:
   - `ContextStore.put("chat", scope, "last_user_message", ...)`
3. Load bounded history from SQLite:
   - `get_recent_messages(limit=history_message_limit)`
   - `_trim_history_by_chars(history_char_limit)`
4. Build model message list:
   - base system prompt (`_base_system_message()`)
   - scratchpad assistant message (`scratchpad_assistant_message(scope, run_id=run_id)`)
   - trimmed chat history
5. Call `_run_agent_turn(... persist_assistant=True)`.

## 3) Agent + Tool Loop (`_run_agent_loop`)

Current loop:

1. Call model once (`LlmClient.chat(messages, tools)`).
2. If model returned `tool_calls`:
   - Append assistant tool-call message to `messages`.
   - For each tool call:
     - Parse JSON args.
     - Internal special tool:
       - `session__scratchpad_write` is handled in-process (`_handle_scratchpad_tool`).
     - MCP tools:
       - `self.mcp.call_tool(fn_name, args)` via stdio MCP.
     - Append tool result to `messages` as role=`tool`.
   - Call model again with updated messages.
3. When no more tool calls:
   - Return assistant text reply + in-memory `tool_trace`.

**Scratchpad finalize path:**

When `scratchpad_write(mode=finalize)` is called, the loop does **not** continue to another round. Instead:

1. Build a `finalize_response` prompt from the scratchpad state (goal, context, known_facts, tool_journal).
2. Call the model **with no tools** (`tools=[]`) so it can only produce a text response.
3. Return that text as the final reply.

This prevents local/smaller models from hallucinating `scratchpad_write(init)` after finalize, which would wipe all accumulated context.

**Abort paths:**

- `MAX_TOOL_CALLS_PER_TURN` (30) exceeded → fixed error reply.
- `MAX_IDENTICAL_TOOL_CALL_REPEATS` (3) exceeded → fixed error reply.

Important behavior:

- Tool outputs are only in-memory for the current loop iteration.
- Tool outputs are not persisted into `messages` table by default.
- Only final assistant text is persisted for normal turns.

## 4) What Playwright Returns to the Model

From MCP hub:

- Tool result parts are flattened to one large text string (`McpHub._normalize_result()`).
- For Playwright, this is often:
  - page URL/title
  - console entries
  - full YAML accessibility snapshot
- These payloads can be very large (tens to hundreds of KB) and are appended as `role="tool"` content for the current turn.

**Multi-tab support:**

When a click opens a new tab (e.g. `target="_blank"`):

- `BrowserInteractor.click()` uses `context.expect_page()` to detect the popup.
- Old page is compressed to `{url, title}` and saved in `background_tabs`.
- `self.page` switches to the new tab automatically.
- The `interact_page` response includes `background_tabs` (list of compressed tab summaries) and `step_urls` (compact URL+title per navigation step).

The `switch_tab` action lets the LLM return to a background tab by index.

Implication:

- Model can become context-heavy/noisy within the current loop.
- Follow-up turns do not automatically retain prior tool raw output unless assistant summarized it.

## 5) Scratchpad (Current)

Current scratchpad design:

- Tool name: `session__scratchpad_write`
- Storage key: `contexts(scope_type="subagent_run", scope_id=run_id, key="scratchpad")` when inside a subagent run (per-run isolation); falls back to `contexts(scope_type="chat", scope_id=scope, key="scratchpad")` when no run context is available (e.g., `/scratchpad show` command).
- Injection: `scratchpad_assistant_message(scope, run_id=run_id)` appended as the last user-role message in the LLM prompt before each round.
- Reset behavior: Each subagent run starts with a fresh scratchpad (keyed by `run_id`), so no explicit clear is needed at turn start. The `/reset` command clears chat history but no longer clears the scratchpad (old run scratchpads are orphaned harmlessly).

**Modes:**

| Mode | Behavior |
|------|----------|
| `init` | Resets scratchpad to empty state — goal and context only. Clears all accumulated facts and tool journal. |
| `append` | Adds to scratchpad without reset. Preserves existing state. |
| `finalize` | Marks scratchpad as finalized. Triggers explicit no-tools LLM call with `finalize_response` prompt to produce the final answer. Loop exits immediately after. |

Current limitation:

- Scratchpad writes depend on model deciding to call `session__scratchpad_write`.
- If model does not call it, scratchpad remains stale/empty.

**Why per-run isolation matters:**

Without per-run keys, two concurrent subagent runs on the same chat scope (e.g., a user message and a scheduled task) would clobber each other's scratchpad state — one run writes `{goal: "Minolta search"}`, the other overwrites it with `{goal: "Reddit trending"}`, causing both runs to loop indefinitely without reaching `finalize`.

## 6) Scheduled Turn Path (`_handle_scheduled_task_message`)

Scheduled tasks are serialized through the same `asyncio.Queue` as user messages, preventing concurrent subagent runs on the same scope:

1. `SchedulerRunner._loop()` polls `SchedulerStore.due_tasks()` every N seconds.
2. For each due task, the callback `_handle_scheduled_task(scoped_id, prompt, task_id=..., cron_expr=...)` wraps it into a `ScheduledTaskMessage(scope, prompt, task_id, cron_expr)` and puts it into `_message_queue`.
3. `_process_queue_loop` dequeues the `ScheduledTaskMessage` (serialized with user messages and subagent results) and calls `_handle_scheduled_task_message`.
4. `_handle_scheduled_task_message` builds two system messages — `subagent_scheduled` (static prefix with scheduler context and user_id) and `subagent_time` (dynamic block with working timezone and current time) — then spawns a subagent run, executes it, sends the result via the channel, and evaluates the turn.
5. `active_requests[scope]` is set during execution and cleared in the `finally` block, making scheduled tasks visible to `/status`.
6. `mark_ran(task_id, cron_expr)` is called in `_handle_scheduled_task` at enqueue time — not after execution — so the scheduler won't re-enqueue the same task on the next poll cycle.

This ensures that if a user message and a scheduled task arrive for the same scope at the same time, they are processed one at a time — no concurrent scratchpad clobbering or context collision.
