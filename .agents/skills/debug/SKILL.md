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
- Log files (`data/nanobot.log`, `.log.1`, `.log.2`, `.log.3`): RotatingFileHandler with 2MB max, 3 backups

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
# View latest bot logs (rotating file, max 2MB per file, 3 backups)
tail -f data/nanobot.log

# View specific backup
cat data/nanobot.log.1

# Search for specific errors
grep "ERROR" data/nanobot.log
grep "run-abc123" data/nanobot.log

# Follow logs with scope filter
tail -f data/nanobot.log | grep "telegram:500506690"
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
