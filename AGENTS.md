# NanoBot Agent Guidelines

**Generated:** 2026-05-02
**Commit:** 2a294b3
**Branch:** main

## Overview
Minimal personal assistant bot with Telegram interface, OpenAI-compatible LLM backend (Ollama/vLLM), and MCP tool extensibility. Uses Python 3.11+ with uv for dependency management.

## Structure
```
nanobot/
├── src/nanobot/           # Main package
│   ├── core.py            # BotCore - orchestrator, message queue, command dispatch
│   ├── main.py            # Entry point
│   ├── debug_cli.py       # Debug/inspection CLI
│   ├── config.py          # Config loading (load_config, AppConfig)
│   ├── mcp_hub.py         # MCP server connections
│   ├── memory.py          # SQLite conversation history
│   ├── agent_run.py       # AgentRun - LLM chat loop with tools + finalize path
│   ├── subagents/         # SubagentManager + SubagentRunStore
│   ├── channels/          # Telegram, GitHub, File channel implementations
│   ├── mcp_servers/       # timer, memory, scheduler, web MCP servers
│   ├── core_commands/     # Built-in commands (session, status, reset)
│   ├── plans/             # Plan storage and management
│   ├── tools/             # ToolRegistry + ToolStatsStore
│   ├── evaluator/         # LearningEvaluator - skill learning extraction
│   ├── prompts/           # PromptStore - centralized prompt management
│   ├── memstore/          # Memstore tools for key-value storage
│   ├── skills/            # Skill injection and matching (mem0-backed)
│   ├── vector_store/      # Native vector store with multi-collection support
│   └── hooks/             # Tool event hooks (FileTraceHook, etc.)
├── src/web_agent/         # Web browsing agent (Playwright-based)
│   ├── browser/           # BrowserInteractor - page interaction, multi-tab, snapshot
│   ├── service.py         # WebAgentTool - read/interact web pages
│   └── ...                # classifiers, extractors, fetchers, scorers
├── scripts/               # Utility scripts
│   ├── eval/              # Prompt testing toolkit for evaluator iteration
│   └── file_channel_test.py  # FileChannel integration test
├── tests/                 # Pytest tests (mirrors src structure)
│   ├── agent_run/         # Tests for agent_run.py
│   ├── channels/          # Tests for channels/
│   ├── core_commands/     # Tests for core_commands/
│   ├── evaluator/         # Tests for evaluator/
│   ├── mcp_servers/       # Tests for mcp_servers/ (incl. test_interactor_tabs.py)
│   ├── memstore/          # Tests for memstore/
│   ├── plans/             # Tests for plans/
│   ├── prompts/           # Tests for prompts/
│   ├── scheduler/         # Tests for scheduler_store.py
│   ├── skills/            # Tests for skills/
│   ├── subagents/         # Tests for subagents/
│   ├── tools/             # Tests for tools/
│   ├── vector_store/      # Tests for vector_store/
│   └── test_*.py          # Top-level module tests
└── config.yaml            # Bot configuration
```

## Where to Look
| Task | Location | Notes |
|------|----------|-------|
| Add new channel | `src/nanobot/channels/` | Implement Channel interface |
| Add MCP tool | `src/nanobot/mcp_servers/` | Create server.py in subpackage |
| Add built-in command | `src/nanobot/core_commands/commands/` | Register in command_manager.py |
| Modify message flow | `src/nanobot/core.py` | `_process()`, SubagentManager |
| Modify agent loop | `src/nanobot/agent_run.py` | `AgentRun.run()` - tool calling, finalize exit path |
| Modify scratchpad finalize | `src/nanobot/agent_run.py` | `_finalize_response_message()` builds no-tools prompt |
| Add browser interaction | `src/web_agent/browser/interactor.py` | `BrowserInteractor` - click, switch_tab, multi-tab |
| Add web read/interact flow | `src/web_agent/service.py` | `WebAgentTool` - interact_page, read flow |
| Add run tracking | `src/nanobot/subagents/` | SubagentManager, SubagentRunStore |
| Test fixtures | `tests/conftest.py` | Minimal - most fixtures inline |
| Config schema | `src/nanobot/config.py` | AppConfig, ModelConfig dataclasses |
| Skill learning extraction | `src/nanobot/evaluator/` | LearningEvaluator - evaluates agent turns for learnings |
| Prompt management | `src/nanobot/prompts/` | PromptStore - centralized prompt templates |
| Key-value storage | `src/nanobot/memstore/` | Memstore tools for context/state |
| Skill matching/injection | `src/nanobot/skills/` | SkillMatcher, skill tools (mem0-backed) |
| Vector embeddings | `src/nanobot/vector_store/` | Native vector store with multi-collection support |
| Tool event hooks | `src/nanobot/hooks/` | FileTraceHook, tool event handlers |
| LLM call logging | `src/nanobot/llm.py` | `LlmClient.chat()` - all LLM I/O logged via `nanobot.llm.io` |

### Test Location Convention
Tests mirror source structure. For `src/nanobot/subagents/manager.py`, tests go in `tests/subagents/test_manager.py`.

| Source | Tests |
|--------|-------|
| `src/nanobot/agent_run.py` | `tests/agent_run/test_agent_run.py` |
| `src/nanobot/channels/` | `tests/channels/` |
| `src/nanobot/core_commands/` | `tests/core_commands/` |
| `src/nanobot/evaluator/` | `tests/evaluator/` |
| `src/nanobot/mcp_servers/` | `tests/mcp_servers/` |
| `src/nanobot/memstore/` | `tests/memstore/` |
| `src/nanobot/plans/` | `tests/plans/` |
| `src/nanobot/prompts/` | `tests/prompts/` |
| `src/nanobot/skills/` | `tests/skills/` |
| `src/nanobot/subagents/` | `tests/subagents/` |
| `src/nanobot/tools/` | `tests/tools/` |
| `src/nanobot/vector_store/` | `tests/vector_store/` |
| Top-level modules (core.py, etc.) | `tests/test_*.py` (top-level) |

## Commands

This project uses [just](https://github.com/casey/just) as a command runner. Run `just --list` to see all recipes.

```bash
just install                    # Install dependencies (dev group included)
just setup                      # Install deps + pre-commit hooks
just run                        # Start the bot (uses config.yaml by default)
just run config.staging.yaml    # Start with alternate config

just check                      # Lint + format + typecheck (all quality gates)
just lint                       # Ruff lint only
just format                     # Ruff format only
just typecheck                   # mypy only

just test                       # Run all tests
just test-pkg subagents          # Run tests for a package
just test-file plans/test_plan_store.py  # Run specific test file
just test-name test_plan_store    # Run tests matching a name

just reset                      # Full state reset (scheduler + history + mem0)
just reset-dry                  # Preview what reset clears
just reset-local                # Reset local SQLite only (keep mem0)
just resync-skills              # Re-index intelligent skills to mem0

just scopes                     # List message scopes
just ctx <scope>                # Show context report
just scheduler-list             # List scheduled tasks
just plan-list                  # List plan runs

just eval-list                  # List eval fixtures
just eval-run <fixture>         # Run eval against a fixture

just chat "hello bot"           # Send message via FileChannel
just pre-commit                 # Run pre-commit hooks
```

<details>
<summary>Raw commands (without just)</summary>

```bash
uv sync --group dev                    # Install dependencies
uv run pytest                          # Run tests
uv run pytest tests/subagents/         # Run tests for a package
uv run pytest tests/plans/test_plan_store.py -k name  # Run specific test
uv run ruff check . && uv run ruff format .  # Lint + format
uv run mypy                            # Type check
python -m nanobot.main --config config.yaml  # Run bot
uv run python -m nanobot.debug_cli --config config.yaml scopes  # Debug CLI
```
</details>

## Conventions

### Imports
- `from __future__ import annotations` at top of ALL files
- Order: stdlib → third-party → local `nanobot.*` (alphabetically within groups)

### Type Annotations
- Explicit return types on ALL functions: `-> None`, `-> str`, etc.
- Modern generics: `list[type]`, `dict[str, type]` (NOT `List`, `Dict`)
- Union syntax: `str | None` (NOT `Optional[str]`)
- `cast(Any, ...)` for dynamic code/mocks

### Naming
- Classes: PascalCase (`BotCore`)
- Functions/variables: snake_case (`load_config`)
- Constants: UPPER_SNAKE_CASE (`MAX_TOKENS`)
- Private: single underscore (`_process`)

### Formatting (Ruff: 120 chars, double quotes)
- Line length: 120 max
- Quotes: `"` only (never `'`)
- Indent: 4 spaces
- Blank lines: 2 between top-level, 1 inside functions
- Trailing commas in multi-line calls

### Error Handling
- Specific exceptions (KeyError, ValueError) - broad only when necessary with `# pylint: disable=broad-except`
- NEVER silently swallow - always log or re-raise
- `logger.exception()` for full traceback
- Surface failures to the user: errors in scheduled tasks, subagents, and background processes must result in a user-visible message, not silent drops

### Logging
- `logging.getLogger(__name__)` per module
- Include context: scope, chat_id, tool_name
- Clip long strings with `clip()` helper

### Async
- `async def`/`await` throughout
- `AsyncExitStack` for multiple async context managers (see McpHub)

### Data Classes
- `@dataclass` over plain dicts for structured data
- `field(default_factory=...)` for mutable defaults
- `frozen=True` for immutable (e.g., ToolCallEvent)

### Testing
- `tests/` mirrors source structure
- Package tests: `tests/subagents/test_manager.py` tests `src/nanobot/subagents/`
- Top-level module tests: `tests/test_core_queue.py` tests `src/nanobot/core.py`
- `tmp_path` fixture for temp files
- Fake implementations: `_FakeChannel`, `_FakeLlm`, `_FakeMcp`
- `@pytest.mark.asyncio` for async tests
- In-memory SQLite: `:memory:`

### Git Commits
- Link to GitHub issues when applicable: append `Closes #xxx` or `Fixes #xxx`
- Ask user about issue linkage if not mentioned
- See `.agents/skills/git-commit/SKILL.md` for detailed workflow

## Anti-Patterns (THIS PROJECT)
- **Type errors**: Suppress with `# type: ignore` only when unavoidable
- **Empty catch blocks**: `except: pass` - Never
- **Deleting failing tests**: to "pass" - Never
- **Commit without explicit request**: Never
- **Speculate about unread code**: Never
- **Leave code in broken state**: Never
- **Commit without issue reference**: Ask about GitHub issue linkage first
- **Silent failures**: Never swallow errors without user visibility. If a scheduled task, subagent, or background process fails, the user must be notified. No error is "too internal" to surface.
- **Context trimming / data loss on error**: Never silently truncate tool results or conversation context to "fit" — partial information is worse than none. If a tool returns too much data, fail explicitly with an informative error rather than silently cutting content. The user can then act on the real problem (e.g., fix the extraction, adjust the query).
- **Incremental extensibility**: When adding error handling or new failure modes, build incrementally on existing patterns (e.g., `_should_notify_user`, `_format_failure_summary`) rather than introducing parallel mechanisms. Each new failure type should be a new branch in an existing handler, not a new handler.

## Documentation Index

Before exploring the codebase, check these docs — they often contain the answer you'd otherwise search for:

### Architecture & Systems

| Topic | Doc | Read when |
|-------|-----|-----------|
| Scratchpad (working memory, init/append/finalize lifecycle, prompt templates, limits) | `docs/SCRATCHPAD.md` | Modifying agent loop, scratchpad protocol, finalize path, tool call limits |
| Skills (trigger modes, matching flow, vector search, score filtering, CRUD, tool gating) | `docs/SKILLS.md` | Adding/modifying skills, skill matching, tool filtering, vector search scoring |
| Evaluator (3-phase pipeline, quality gate, learning extraction, skill lifecycle) | `docs/EVALUATOR.md` | Modifying evaluator, adding evaluation phases, debugging learning extraction |
| Scheduler (cron tasks, storage, MCP tools, config) | `docs/SCHEDULER.md` | Adding scheduled features, debugging cron, scheduler CLI commands |
| Channels (Channel ABC, message flow, adding new channels, startup/shutdown) | `docs/CHANNELS.md` | Adding a new channel, modifying message flow, debugging channel issues |
| MCP Servers (config, lifecycle, required_env, override pattern, adding new servers) | `docs/MCP_SERVERS.md` | Adding MCP servers, config override, debugging server startup |
| Web Agent (browser interaction, content extraction, snapshots, multi-tab, hooks) | `docs/WEB_AGENT.md` | Modifying browser interaction, extraction pipeline, adding web tools |
| Logging (config-driven setup, handler factory, per-module filtering, LLM/evaluator log separation) | `docs/logging.md` | Adding log handlers, debugging logging config, LLM call tracing |
| Vector Store / Qdrant (lock contention, separate configs, constructor injection) | `docs/mem0-vector-store-patterns.md` | Adding VectorStore consumers, debugging Qdrant lock errors, sharing instances |
| Reddit MCP (public JSON endpoints, rate limits, tool reference, error handling) | `docs/REDDIT.md` | Modifying Reddit tools, debugging rate limits, adding Reddit features |

### Skills (`.agents/skills/`)

| Skill | When to load |
|-------|-------------|
| `add-feature` | Adding any new channel, command, MCP server, tool, or hook |
| `testing` | Writing or running tests, understanding test conventions |
| `debug` | Debugging bot behavior via SQLite, session state, LLM logs |
| `bot-conversation` | Sending messages to the bot via FileChannel for testing/debugging |
| `git-commit` | Creating commits, especially those that reference GitHub issues |
| `test-async-applications` | Testing async code with external dependencies, mock timing issues |
| `debug-production-issues` | Debugging issues where tests pass but production fails, stale locks, library pitfalls |

### Skill-First Exploration Rule

**MANDATORY**: Before firing `explore` agents or doing codebase searches, check if a relevant skill or doc exists. Skills contain project-specific patterns, registration points, and code templates that eliminate the need for broad searches.

**Sequence**:
1. **Check `Documentation Index` above** — does a doc cover your topic? Read it first.
2. **Check `Skills` table above** — does a skill match your task? Call `skill(name="<skill>")` to load it before starting work.
3. **Only then** explore the codebase for details not covered in docs/skills.

This avoids redundant searches for information that's already written down.

## Notes
- Uses [just](https://github.com/casey/just) as command runner - `just --list` for all recipes
- No GitHub Actions CI - all testing/linting is local via `uv run` or `just`
- `reset_state.py` at project root (not in src/) - utility script (also available as `just reset`)
- `.agents/skills/` contains project-specific AI agent skills
- Entry points: `python -m nanobot.main` and `python -m nanobot.debug_cli`
