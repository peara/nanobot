---
name: debug
description: Debugging nanobot - SQLite database inspection, session analysis, and troubleshooting
---

## What I do

I help debug nanobot issues by querying SQLite databases, inspecting session state, and analyzing conversation context and plan runs.

## When to use me

Use this when:
- Investigating why the bot responded unexpectedly
- Checking conversation history and context for a chat
- Debugging plan_run execution and tool traces
- Inspecting scheduler tasks or database state
- Verifying what data is persisted vs. ephemeral
- Troubleshooting context window or memory issues

## Key Principles

### 1. Database is the source of truth
All persistent state lives in SQLite:
- Main DB (`data/nanobot.db`): messages, contexts (including plan_run traces)
- Scheduler DB (`data/scheduler.db`): scheduled tasks
- Log files (`data/nanobot.log` by default, configured in `logging` section of `config.yaml`): rotation and paths are config-driven

### 2. Scopes are hierarchical
- Chat scope: `telegram:<chat_id>` or `github:<issue_id>`
- Plan run scope: `plan_run:run-<uuid>` - stores execution traces
- Context keys: arbitrary JSON values under `(scope_type, scope_id, key)`

### 3. Always check plan_run traces first
When debugging unexpected /plan behavior:
1. Get the run_id from chat context or `plan list`
2. Inspect with `plan show --run-id <id>`
3. Check intake_raw, execution_raw, tool_trace, error fields

## Common Patterns

### Inspect conversation context
```bash
# List all chat scopes
just scopes

# Show context for specific scope
just ctx telegram:500506690

# Show what would be sent to LLM (full payload)
just ctx telegram:500506690  # add --full manually via debug_cli

# Use latest scope automatically
just ctx --latest  # add --tail 10 manually via debug_cli
```

### Debug plan runs
```bash
# List recent plan executions
just plan-list

# Show detailed execution trace
just plan-show run-abc123

# Show latest plan run (add --latest manually via debug_cli)
```

### Check scheduler state
```bash
# List scheduled tasks
just scheduler-list

# Clear all tasks
just scheduler-clear

# Remove invalid placeholder scopes (add --purge-messages manually)
just scheduler-clear-invalid
```

### Reset state (nuclear option)
```bash
# Preview what would be deleted
just reset-dry

# Full reset (local DB + scheduler + mem0)
just reset

# Skip mem0 reset
just reset-local
```

### Query SQLite directly
```bash
# Connect to main database
sqlite3 data/nanobot.db

# Useful queries
SELECT DISTINCT chat_id FROM messages ORDER BY id DESC LIMIT 20;
SELECT scope_id, key FROM contexts WHERE scope_type = 'plan_run' ORDER BY id DESC LIMIT 10;
SELECT chat_id, cron_expr, enabled FROM scheduled_tasks;
```

### Read log files
```bash
# View latest bot logs (paths and rotation configured in config.yaml → logging)
tail -f data/nanobot.log

# View specific backup
cat data/nanobot.log.1

# Search for specific errors
grep "ERROR" data/nanobot.log
grep "run-abc123" data/nanobot.log

# Follow logs with scope filter
tail -f data/nanobot.log | grep "telegram:500506690"
```

### Inspect LLM call logs

**Important:** LLM IO logs go to a **separate file** (`data/llm.log`), NOT to the main `nanobot.log`. The logger name is `nanobot.llm.io` and is configured in `config.yaml` under `logging.loggers`.

The `llm.log` has two levels:
- **INFO** (`REQUEST`/`RESPONSE`): Summaries — scope, model, message count, char count, tools, finish_reason, token usage, elapsed time
- **DEBUG** (`REQUEST_FULL`/`RESPONSE_FULL`): Complete payloads — full messages array, full response including `reasoning_content` (chain-of-thought), `content`, `tool_calls`, `finish_reason`

**Scope suffix convention** identifies the agent loop phase:
- `telegram:500506690` → Initial LLM call (first turn)
- `telegram:500506690:continue` → Tool loop continuation calls
- `telegram:500506690:finalize` → Final response call (after scratchpad finalize)
- `telegram:500506690:eval_quality` → Quality assessment call
- `telegram:500506690:eval_learning` → Learning extraction call
- `telegram:500506690:limit_finalize` → Tool call limit reached, forced finalize

**finish_reason values:**
- `tool_calls` → Model wants to call tools (loop continues)
- `stop` → Model finished generating (loop ends)

```bash
# Tail LLM logs in real time
tail -f data/llm.log

# Find ALL entries (INFO + DEBUG) for a specific scope
grep "500506690" data/llm.log

# Find only INFO-level REQUEST/RESPONSE summaries for a scope
grep -E "REQUEST|RESPONSE" data/llm.log | grep "500506690" | grep -v "FULL"

# Find calls that hit token limit (truncated response)
grep "finish_reason=length" data/llm.log

# Find slow calls (>5s elapsed)
grep -E "elapsed=[5-9]\.[0-9]+s|elapsed=[0-9]{2,}\." data/llm.log

# Find the full LLM response for a specific continue call (includes reasoning_content)
# First identify the line number of the RESPONSE_FULL you want
grep -n "RESPONSE_FULL.*500506690:continue" data/llm.log

# Find a specific phase of the agent loop
grep "500506690:finalize" data/llm.log   # Final response after scratchpad finalize
grep "500506690:continue" data/llm.log  # Tool loop calls

# Get a quick timeline of all LLM calls for a scope (INFO level only)
grep -E "^(INFO|DEBUG).*nanobot.llm.io.*(REQUEST|RESPONSE)" data/llm.log | grep "500506690" | grep -v "FULL"

# Extract the model's chain-of-thought (reasoning_content) from a specific response
# Useful for understanding WHY the model made a decision
grep "reasoning_content" data/llm.log | grep "500506690"
```

#### Debugging workflow for "LLM made a bad decision"

When the bot skipped a step, gave a wrong answer, or called the wrong tool:

1. **Identify the scope** — `just scopes` or grep `nanobot.log` for the chat_id
2. **Find the problematic LLM response** — Grep `llm.log` for the scope + `:continue` (most tool decisions happen in continue calls)
3. **Read the REQUEST_FULL** — See exactly what messages and scratchpad state were sent to the model
4. **Read the RESPONSE_FULL** — Check `reasoning_content` (the model's chain-of-thought) and `tool_calls` to understand WHY it chose that action
5. **Check the previous tool results** — The messages array includes tool results; verify the model received the data it claims it didn't have
6. **Check for stale prompts** — `PromptStore._seed_defaults` only inserts if no active prompt exists. If `defaults.py` was updated but the DB still has the old version, the bot uses stale prompts. Verify with:
   ```bash
   # Compare active prompt size with defaults
   uv run python -c "
   from nanobot.prompts.defaults import ORCHESTRATOR_MAIN
   from nanobot.prompts.store import PromptStore
   store = PromptStore('./data/prompts.db', seed_defaults=False)
   active = store.get_active('orchestrator_main')
   print(f'Defaults: {len(ORCHESTRATOR_MAIN)} chars')
   print(f'Active DB: {len(active.content)} chars')
   print(f'Match: {active.content == ORCHESTRATOR_MAIN}')
   "
   ```

## Debugging Checklist

When something is wrong:
- [ ] Check `scopes` to verify the scope exists
- [ ] Check `ctx --scope <id>` to see recent messages
- [ ] Check `plan list` if /plan was used - get the run_id
- [ ] Check `plan show --run-id <id>` for full execution trace
- [ ] Check `scheduler list` if tasks are involved
- [ ] Query contexts table directly for custom scope data
- [ ] Check if messages were trimmed by char_limit vs message_limit
- [ ] Check `data/llm.log` for LLM request/response traces (scope, tokens, finish_reason, elapsed time)
- [ ] If LLM made a bad decision (skipped step, wrong tool, bad answer): read REQUEST_FULL to see what was sent, RESPONSE_FULL to see `reasoning_content` (model's chain-of-thought) and `tool_calls`
- [ ] If prompt behavior seems wrong: verify DB prompts match `defaults.py` — `_seed_defaults` never updates existing prompts, so stale DB prompts can override code changes

## Repo-Specific Patterns

### Context storage schema
- `scope_type`: "chat", "plan_run", "session", or custom
- `scope_id`: chat identifier or run-uuid
- `key`: arbitrary string (e.g., "status", "tool_trace", "last_plan_run_id")
- `value_json`: JSON string with actual data

### Message table schema
- `chat_id`: scope identifier (e.g., "telegram:500506690")
- `role`: "system", "user", "assistant", "tool"
- `content`: message text or JSON for tool calls

### Plan run lifecycle
1. Created with status "created" + request_text
2. Brief generated (plan_brief), status "planning"
3. Execution with tool calls (execution_raw, tool_trace), status "running"
4. Completed with result + status "completed"
5. On failure: error field populated, status unchanged

### Key fields in plan_run context
- `status`: {"value": "created"|"planning"|"running"|"completed"}
- `request_text`: {"text": "original /plan request"}
- `plan_brief`: {"brief": "...", "title": "..."} (parsed from LLM)
- `intake_raw`: {"text": "raw LLM response"}
- `execution_raw`: {"text": "raw execution output"}
- `recovery_raw`: {"text": "recovery pass output"} (if garbled)
- `result`: {"text": "final cleaned result"}
- `error`: {"message": "..."} (if exception)
- `tool_trace`: list of tool calls with args and results

### Environment variables affecting debug
- `SCHEDULER_DB_PATH`: defaults to ./data/scheduler.db
- `MEM0_CONFIG_PATH`: mem0 configuration file
