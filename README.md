# nanobot

Minimal personal assistant bot with:

- Telegram chat interface
- OpenAI-compatible local model backend (Ollama/vLLM)
- MCP tool extensibility
- Timer MCP for read-only time queries
- Memory MCP (mem0 OSS) for long-term semantic memory
- SQLite conversation history
- Scheduler via MCP (SQLite-backed tasks + Linux cron bridge)

## Architecture

- `nanobot.core` - Agent loop and tool-calling orchestration
- `nanobot.channels` - Channel abstraction (`Channel`) + Telegram implementation
- `nanobot.mcp_hub` - Connects to configured MCP servers and routes tool calls
- `nanobot.mcp_servers.timer.server` - MCP timer server (read-only time tools)
- `nanobot.mcp_servers.memory.server` - MCP memory server backed by mem0
- `nanobot.memory` - Conversation history store (SQLite)
- `nanobot.scheduler_runner` - Executes due scheduled tasks from scheduler DB
- `nanobot.mcp_servers.scheduler.server` - MCP scheduler server

## Quick start

1. Install `uv` (if not already installed), then sync dependencies:

```bash
uv sync --group dev
```

2. Activate the virtual env:

```bash
source .venv/bin/activate
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

## Code quality

Run locally:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy
uv run pytest
```

Install git hooks:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
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

## Playwright MCP server (web browsing + interaction)

Playwright MCP enables website interaction (navigate, click, fill forms, extract page content), so the bot can do tasks like searching products directly on sites such as Amazon.

Requirements:

- Node.js 18+ (for `npx`)
- Chrome installed locally (current config uses `--browser chrome`)

Configured server entry:

```yaml
mcp_servers:
  - name: "playwright"
    command: "npx"
    args:
      - "-y"
      - "@playwright/mcp@latest"
      - "--browser"
      - "chrome"
      - "--headless"
      - "--user-data-dir"
      - "./data/playwright/profile"
      - "--output-dir"
      - "./data/playwright/output"
      - "--save-session"
```

Persistence notes:

- Browser profile, cookies, login state, and history are kept in `./data/playwright/profile`.
- Session artifacts are saved in `./data/playwright/output`.
- Do not use `--isolated` if you want profile/history to persist between runs.

## Memory MCP server (mem0 OSS)

This server exposes long-term memory tools through MCP:

- `memory_health`
- `memory_search`
- `memory_save`
- `memory_save_turn`

Configure it in `config.yaml`:

```yaml
mcp_servers:
  - name: "memory"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.memory.server"]
    env:
      MEM0_CONFIG_PATH: "./config.mem0.yaml"
```

Then create your local mem0 config from template:

```bash
cp config.mem0.example.yaml config.mem0.yaml
```

Recommended local embedding model:

- `mxbai-embed-large` (best quality)
- `nomic-embed-text` (lighter/faster fallback)

## Notes

- This repo is intentionally minimal and designed for extension through MCP servers.
- Add more capabilities by adding more `mcp_servers` entries in `config.yaml`.
- Conversation history is persisted in SQLite; only a bounded recent window is sent to the model each turn.

## Quick full reset

If you need to frequently clear scheduler + local history/context + mem0 memory, use:

```bash
uv run python reset_state.py
```

Useful options:

```bash
# Preview counts only (no deletion)
uv run python reset_state.py --dry-run

# Clear local SQLite state only, keep mem0
uv run python reset_state.py --skip-mem0
```

## Debug CLI

Use the built-in debug CLI for reliable context and scheduler inspection:

```bash
uv run python -m nanobot.debug_cli --config config.yaml scopes
uv run python -m nanobot.debug_cli --config config.yaml ctx --scope telegram:500506690
uv run python -m nanobot.debug_cli --config config.yaml ctx --latest --full
uv run python -m nanobot.debug_cli --config config.yaml reset --scope telegram:500506690
uv run python -m nanobot.debug_cli --config config.yaml scheduler list
uv run python -m nanobot.debug_cli --config config.yaml scheduler clear
uv run python -m nanobot.debug_cli --config config.yaml scheduler clear-invalid --purge-messages
uv run python -m nanobot.debug_cli --config config.yaml browse --scope telegram:500506690 --limit 15
uv run python -m nanobot.debug_cli --config config.yaml browse --latest --full
```
