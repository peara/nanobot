# NanoBot Agent Guidelines

**Generated:** 2026-04-11
**Commit:** 9062d3b
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
│   ├── agent_run.py       # AgentRun - LLM chat loop with tools
│   ├── subagents/         # SubagentManager + SubagentRunStore
│   ├── channels/          # Telegram/GitHub channel implementations
│   ├── mcp_servers/       # timer, memory, scheduler MCP servers
│   ├── core_commands/     # Built-in commands (session, status, reset)
│   └── tools/             # ToolRegistry + ToolStatsStore
├── tests/                 # Pytest tests (mirrors src structure)
└── config.yaml            # Bot configuration
```

## Where to Look
| Task | Location | Notes |
|------|----------|-------|
| Add new channel | `src/nanobot/channels/` | Implement Channel interface |
| Add MCP tool | `src/nanobot/mcp_servers/` | Create server.py in subpackage |
| Add built-in command | `src/nanobot/core_commands/commands/` | Register in command_manager.py |
| Modify message flow | `src/nanobot/core.py` | `_process()`, SubagentManager |
| Modify agent loop | `src/nanobot/agent_run.py` | `AgentRun.run()` - tool calling |
| Add run tracking | `src/nanobot/subagents/` | SubagentManager, SubagentRunStore |
| Test fixtures | `tests/conftest.py` | Minimal - most fixtures inline |
| Config schema | `src/nanobot/config.py` | AppConfig, ModelConfig dataclasses |

## Commands
```bash
uv sync --group dev                    # Install dependencies
uv run pytest                          # Run tests
uv run pytest tests/test_foo.py -k name  # Run specific test
uv run ruff check . && uv run ruff format .  # Lint + format
uv run mypy                            # Type check
python -m nanobot.main --config config.yaml  # Run bot
uv run python -m nanobot.debug_cli --config config.yaml scopes  # Debug CLI
```

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
- `tmp_path` fixture for temp files
- Fake implementations: `_FakeChannel`, `_FakeLlm`, `_FakeMcp`
- `@pytest.mark.asyncio` for async tests
- In-memory SQLite: `:memory:`

## Anti-Patterns (THIS PROJECT)
- **Type errors**: Suppress with `# type: ignore` only when unavoidable
- **Empty catch blocks**: `except: pass` - Never
- **Deleting failing tests**: to "pass" - Never
- **Commit without explicit request**: Never
- **Speculate about unread code**: Never
- **Leave code in broken state**: Never

## Notes
- No GitHub Actions CI - all testing/linting is local via `uv run`
- `reset_state.py` at project root (not in src/) - utility script
- `.agents/skills/` contains project-specific AI agent skills
- Entry points: `python -m nanobot.main` and `python -m nanobot.debug_cli`
