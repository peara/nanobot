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

Implication:

- Model can become context-heavy/noisy within the current loop.
- Follow-up turns do not automatically retain prior tool raw output unless assistant summarized it.

## 5) Scratchpad (Current)

Current scratchpad design:

- Tool name: `session__scratchpad_write`
- Storage key: `contexts(scope_type="chat", key="scratchpad")`
- Injection: `_scratchpad_system_message(scope)` is added to prompt before history.
- Reset behavior: `/reset` clears chat history and sets scratchpad empty.

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
