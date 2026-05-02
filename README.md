# nanobot

Minimal personal assistant bot with:

- Telegram chat interface
- OpenAI-compatible local model backend (Ollama/vLLM)
- MCP tool extensibility
- Timer MCP for read-only time queries
- SQLite conversation history
- Scheduler via MCP (SQLite-backed tasks + Linux cron bridge)

## Architecture

- `nanobot.core` - BotCore orchestrator: message queue, command dispatch, SubagentManager integration
- `nanobot.subagents` - SubagentManager (spawn/execute) + SubagentRunStore (run tracking)
- `nanobot.agent_run` - AgentRun: LLM chat loop with tool calling, scratchpad protocol, and finalize exit path
- `nanobot.channels` - Channel abstraction (`Channel`) + Telegram, GitHub, File implementations
- `nanobot.tools` - ToolRegistry + McpToolSource + ToolStatsStore (tool call statistics)
- `nanobot.mcp_hub` - Connects to configured MCP servers and routes tool calls
- `nanobot.mcp_servers.timer.server` - MCP timer server (read-only time tools)
- `nanobot.memory` - Conversation history store (SQLite)
- `nanobot.context_store` - Scoped JSON storage (scratchpad, run metadata)
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
export TAVILY_API_KEY="..."   # recommended for structured web search
# or: export EXA_API_KEY="..."  # optional fallback / alternative provider
```

For web search, configure at least one provider:

- `TAVILY_API_KEY`: recommended default for current events, prices, and general web lookup
- `EXA_API_KEY`: optional alternative or fallback provider

If neither key is set, `web__search_web` will return a configuration error instead of searching.

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

## Web agent (built-in browser interaction)

The web agent (`src/web_agent/`) provides a built-in `interact_page` MCP tool for structured page interaction with multi-tab support. It wraps `BrowserInteractor` for Playwright-based navigation, snapshot, and extraction.

### Multi-tab support

When a click opens a new tab (e.g., `target="_blank"`):

- The browser auto-detects the popup via `context.expect_page()`.
- The old tab is compressed to `{url, title}` and saved in `background_tabs`.
- Active page switches to the new tab.
- `switch_tab(index)` action lets the LLM return to a background tab.

Actions available from `interact_page` steps:

| Action | Fields | Notes |
|--------|--------|-------|
| `click` | `target` | Auto-detects new tabs |
| `type` | `target`, `text` | |
| `select` | `target`, `value` | |
| `scroll` | `amount` or `until_text` | |
| `wait_for` | `selector` or `text` | |
| `switch_tab` | `index` | Return to a background tab by index |

## Memory (mem0 OSS)

Long-term semantic memory is configured via `mem0_config_path` in `config.yaml`:

```yaml
mem0_config_path: "./config.mem0.yaml"
```

Create your mem0 config from template:

```bash
cp config.mem0.example.yaml config.mem0.yaml
```

Recommended local embedding model:

- `mxbai-embed-large` (best quality)
- `nomic-embed-text` (lighter/faster fallback)

## Web search provider setup

The built-in web MCP server exposes `web__search_web` for structured search before `web__read_page`.

Recommended setup:

```bash
export TAVILY_API_KEY="..."
```

Optional fallback:

```bash
export EXA_API_KEY="..."
```

Notes:

- The bot will try `Tavily` first, then fall back to `Exa` when both keys are configured.
- If only one key is configured, it will use that provider only.
- This avoids the old behavior of inventing URLs or relying on fragile Google HTML scraping.

## Notes

- This repo is intentionally minimal and designed for extension through MCP servers.
- Add more capabilities by adding more `mcp_servers` entries in `config.yaml`.
- Conversation history is persisted in SQLite; only a bounded recent window is sent to the model each turn.
- Runtime architecture and current hook points are documented in `ARCHITECTURE.md`.

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
uv run python -m nanobot.debug_cli --config config.yaml tools --latest --limit 20
uv run python -m nanobot.debug_cli --config config.yaml tools --scope telegram:500506690 --full
```
