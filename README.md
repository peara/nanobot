# nanobot

Minimal personal assistant bot with:

- Telegram chat interface
- OpenAI-compatible local model backend (Ollama/vLLM)
- MCP tool extensibility
- Timer MCP for read-only time queries
- SQLite conversation history
- Scheduler via MCP (SQLite-backed tasks + Linux cron bridge)

## Architecture

- `nanobot.core` - Agent loop and tool-calling orchestration
- `nanobot.channels` - Channel abstraction (`Channel`) + Telegram implementation
- `nanobot.mcp_hub` - Connects to configured MCP servers and routes tool calls
- `nanobot.mcp_servers.timer.server` - MCP timer server (read-only time tools)
- `nanobot.memory` - Conversation history store (SQLite)
- `nanobot.scheduler_runner` - Executes due scheduled tasks from scheduler DB
- `nanobot.mcp_servers.scheduler.server` - MCP scheduler server

## Quick start

1. Create and activate a virtual env:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -e .
```

3. Copy config and set env vars:

```bash
cp config.example.yaml config.yaml
export TELEGRAM_BOT_TOKEN="..."
```

You can customize behavior in `config.yaml`:

- `system_prompt_template` (supports `{assistant_name}`)
- `history_message_limit` (how many recent messages to load)
- `history_char_limit` (hard cap on total history characters sent to model)

4. Run:

```bash
python -m nanobot.main --config config.yaml
```

## Timer MCP server

The timer server is started by the bot via stdio MCP using:

```yaml
mcp_servers:
  - name: timer
    command: python
    args: ["-m", "nanobot.mcp_servers.timer.server"]
```

Tools exposed:

- `time_now`
- `time_epoch`

## Scheduler MCP server

The scheduler server is started by the bot via stdio MCP using:

```yaml
mcp_servers:
  - name: scheduler
    command: python
    args: ["-m", "nanobot.mcp_servers.scheduler.server"]
```

Tools exposed:

- `schedule_task`
- `list_tasks`
- `delete_task`
- `pause_task`
- `resume_task`
- `cron_list`
- `cron_add`
- `cron_remove`

## Notes

- This repo is intentionally minimal and designed for extension through MCP servers.
- Add more capabilities by adding more `mcp_servers` entries in `config.yaml`.
- Conversation history is persisted in SQLite; only a bounded recent window is sent to the model each turn.
