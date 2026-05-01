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
   - optional scratchpad system message (`_scratchpad_system_message(scope)`)
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
- Storage key: `contexts(scope_type="chat", key="scratchpad")`
- Injection: `_scratchpad_system_message(scope)` is added to prompt before history.
- Reset behavior: `/reset` clears chat history and sets scratchpad empty.

**Modes:**

| Mode | Behavior |
|------|----------|
| `init` | Resets scratchpad to empty state — goal and context only. Clears all accumulated facts and tool journal. |
| `append` | Adds to scratchpad without reset. Preserves existing state. |
| `finalize` | Marks scratchpad as finalized. Triggers explicit no-tools LLM call with `finalize_response` prompt to produce the final answer. Loop exits immediately after. |

Current limitation:

- Scratchpad writes depend on model deciding to call `session__scratchpad_write`.
- If model does not call it, scratchpad remains stale/empty.

## 6) Scheduled Turn Path (`_process_scheduled`)

Current scheduled flow:

- Builds a separate prompt with:
  - base system prompt
  - scheduler marker system message
  - scheduled prompt as user content
- Runs `_run_agent_turn(...)`.
- Persists assistant reply only.
