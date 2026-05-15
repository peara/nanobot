# nanobot

Minimal personal assistant bot with Telegram chat, OpenAI-compatible local LLM backend (Ollama/vLLM), and MCP tool extensibility.

## Features

### Multi-channel

Telegram, GitHub, and FileChannel implementations share a common `Channel` abstraction. Messages from any channel flow through the same BotCore orchestrator.

### Skills system

Three trigger modes: `always` (inject every turn), `pattern` (regex match against user message), `intelligent` (semantic similarity via `SkillVectorStore`). `SkillMatcher` resolves which skills apply, then injects relevant context into the prompt. Six CRUD tools let the bot manage its own skills at runtime: `skill__create`, `skill__get`, `skill__update`, `skill__delete`, `skill__list`, `skill__activate`.

### Learning evaluator

Three-phase pipeline: quality assessment (was the turn useful?), learning extraction (what can be generalized?), skill lifecycle (create/update/deprecate skills). Enabled via `enable_evaluator: true` in config. Extracted learnings become reusable skills over time.

### Plans

`/plan` command creates structured plans with steps, constraints, risk flags, and required inputs. `PlanStore` persists plans with success/failure tracking. `plan_run` context traces capture intake, execution, and recovery phases for each run.

### Web scripts / NanoScripts

Sandboxed Python extraction scripts stored via MCP tools. `create_script`/`search_scripts`/`invoke_script` with an AST validator that blocks unsafe imports and calls. `ScriptVectorStore` indexes scripts for semantic search. Uses a separate `config.web-scripts.mem0.yaml` to avoid Qdrant lock contention.

### Memory & vector store

Native `VectorStore` (Qdrant local + mem0 embedder) configured via `config.mem0.yaml`. Three Qdrant collections: `nanobot_memories` (facts/preferences), `nanobot_skills` (skill embeddings), `nanobot_web_scripts` (script embeddings). Seven built-in memory tools (not an MCP server): `memory__search`, `memory__save`, `memory__save_turn`, `memory__list`, `memory__delete`, `memory__update`, `memory__health`.

**Embedding dimensions must match.** `embedding_dims` in the embedder config must equal the model's actual output. `mxbai-embed-large` = 1024 dims. Must also match `embedding_model_dims` in the vector_store config. If you change embedding models, re-create Qdrant collections. Recommended: `mxbai-embed-large` (1024 dims, best quality) or `nomic-embed-text` (lighter/faster fallback).

## Architecture

- `nanobot.core` -- BotCore orchestrator: message queue, command dispatch, SubagentManager, evaluator integration
- `nanobot.agent_run` -- AgentRun: LLM chat loop with tool calling, scratchpad protocol, and finalize exit path
- `nanobot.subagents` -- SubagentManager (spawn/execute) + SubagentRunStore (run tracking)
- `nanobot.channels` -- Channel abstraction + Telegram, GitHub, File implementations
- `nanobot.tools` -- ToolRegistry + McpToolSource + ToolStatsStore (tool call statistics)
- `nanobot.mcp_hub` -- Connects to configured MCP servers and routes tool calls
- `nanobot.skills` -- SkillMatcher (always/pattern/intelligent modes) + SkillStore + SkillVectorStore
- `nanobot.evaluator` -- LearningEvaluator: quality assessment, learning extraction, skill lifecycle
- `nanobot.plans` -- PlanStore (persistent plans) + plan runner + plan tools
- `nanobot.web_scripts` -- NanoScript system: store, AST validator, sandboxed runner, script vector store
- `nanobot.vector_store` -- Native VectorStore (Qdrant + mem0 embedder, multi-collection)
- `nanobot.memstore` -- Built-in memory tools (search/save/save_turn/list/delete/update/health)
- `nanobot.prompts` -- PromptStore: centralized prompt templates with versioning & variable rendering
- `nanobot.hooks` -- After-tool-call hooks (ToolResultRecorderHook, BrowseEventRecorderHook, FileTraceHook)
- `nanobot.core_commands` -- Built-in slash commands: help, ctx, reset, plan, scratchpad, reload, status, session
- `nanobot.mcp_servers` -- MCP server implementations: timer, scheduler, web
- `nanobot.memory` -- ConversationStore (SQLite)
- `nanobot.context_store` -- ContextStore (scoped JSON: scratchpad, run metadata, traces)
- `nanobot.scheduler_runner` -- Executes due scheduled tasks from scheduler DB

## Quick start

1. Install `uv` and sync dependencies:

```bash
uv sync --group dev
```

2. Copy config and set env vars:

```bash
cp config.example.yaml config.yaml
export TELEGRAM_BOT_TOKEN="..."
```

3. Run:

```bash
python -m nanobot.main --config config.yaml
```

An OpenAI-compatible endpoint serving embedding models (e.g. LM Studio, Ollama) must be running at the configured URL for memory, skills, and web scripts to work. See `config.mem0.yaml` for embedder settings.

## MCP servers

All MCP servers are configured as `mcp_servers` entries in `config.yaml` (see `config.example.yaml` for full format).

| Server | Command | Tools |
|--------|---------|-------|
| timer | `python -m nanobot.mcp_servers.timer.server` | `time_now`, `time_epoch` |
| scheduler | `python -m nanobot.mcp_servers.scheduler.server` | `schedule_task`, `list_tasks`, `delete_task`, `pause_task`, `resume_task`, `cron_list`, `cron_add`, `cron_remove` |
| web | `python -m nanobot.mcp_servers.web.server` | `web__search_web`, `web__read_page` (Tavily/Exa API key required for search) |
| playwright | `npx -y @playwright/mcp@latest --browser chrome --headless` | Browser navigation, click, type, extract (requires Node.js 18+, Chrome) |

## Web agent

The `interact_page` tool (via `src/web_agent/`) provides structured page interaction with multi-tab support, wrapping `BrowserInteractor` for Playwright-based navigation and extraction.

| Action | Fields | Notes |
|--------|--------|-------|
| `click` | `target` | Auto-detects new tabs |
| `type` | `target`, `text` | |
| `select` | `target`, `value` | |
| `scroll` | `amount` or `until_text` | |
| `wait_for` | `selector` or `text` | |
| `switch_tab` | `index` | Return to a background tab by index |

## Debug CLI

```bash
uv run python -m nanobot.debug_cli --config config.yaml <command> [options]
```

| Command | Description |
|---------|-------------|
| `scopes` | List message scopes |
| `ctx --scope <scope> [--latest] [--full] [--tail N]` | Show context report for a scope |
| `reset --scope <scope> [--latest]` | Clear message history for a scope |
| `scheduler list` | List scheduled tasks |
| `scheduler clear` | Delete all scheduled tasks |
| `scheduler clear-invalid [--purge-messages]` | Remove tasks with invalid placeholder scopes |
| `plan list [--limit N]` | List recent plan_run context traces |
| `plan show --run-id <id> [--latest]` | Show detailed plan run fields |
| `plans list [--limit N]` | List saved persistent plans |
| `plans show <id>` | Show plan details (goal, steps, stats) |
| `skills-resync` | Re-index intelligent skills to mem0 |
| `browse --scope <scope> [--latest] [--limit N] [--full]` | Browse conversation history |
| `tools --scope <scope> [--latest] [--limit N] [--full]` | Show tool call history |

## Quick reset

```bash
uv run python reset_state.py              # clear scheduler + history + context + mem0
uv run python reset_state.py --dry-run    # preview counts only
uv run python reset_state.py --skip-mem0  # clear local SQLite only, keep mem0
```

After a reset, re-index intelligent skills:

```bash
uv run python -m nanobot.debug_cli --config config.yaml skills-resync
```