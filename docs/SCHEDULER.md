# Scheduler

Recurring task automation using cron expressions.

## Overview

NanoBot manages scheduled tasks in an SQLite database (`scheduler.db`). A background runner polls every 20 seconds for due tasks and executes their prompts automatically.

## Storage

Tasks are stored in `scheduled_tasks` table:
- `id`: Unique task ID
- `chat_id`: Target chat scope (e.g., `telegram:123456`)
- `prompt`: Message/LLM prompt to execute
- `cron_expr`: Cron syntax for schedule
- `enabled`: Whether active (1/0)
- `last_run_at`: Timestamp of last execution
- `next_run_at`: Calculated next run time

## Commands

### List Tasks
```bash
uv run python -m nanobot.debug_cli --config config.yaml scheduler list
```
Output format: `id=1 chat_id=telegram:123 enabled=1 cron="0 * * * *" next_run_at=2026-03-17T...`

### Clear All Tasks
```bash
uv run python -m nanobot.debug_cli --config config.yaml scheduler clear
```
Deletes all scheduled tasks.

### Clear Invalid Scopes
```bash
uv run python -m nanobot.debug_cli --config config.yaml scheduler clear-invalid [--purge-messages]
```
Removes tasks with placeholder chat IDs (`12345`, `<current_chat_id>`, etc.). Use `--purge-messages` to also delete associated conversation history.

## MCP Tools (for LLM access)

| Tool | Purpose |
|------|--------|
| `schedule_task(chat_id, prompt, cron_expr)` | Create recurring task |
| `list_tasks()` | List all tasks |
| `delete_task(task_id)` | Remove single task |
| `pause_task(task_id)` | Disable task |
| `resume_task(task_id)` | Enable task |
| `scheduler_health()` | Overview with due count and next 5 tasks |

## Cron Syntax Examples

- `"0 * * * *"` - Every hour at minute 0
- `"0 9 * * *"` - Daily at 9 AM UTC
- `"0 0 * * 0"` - Weekly on Sundays
- `"*/15 * * * *"` - Every 15 minutes

## Configuration

Scheduler DB path configured in `config.yaml`:
```yaml
scheduler_db_path: ./data/scheduler.db
```

Default: `./data/scheduler.db`
