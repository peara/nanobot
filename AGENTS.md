# NanoBot Agent Guidelines

## Project Overview
Minimal personal assistant bot with Telegram interface, OpenAI-compatible LLM backend (Ollama/vLLM), and MCP tool extensibility. Uses Python 3.11+ with uv for dependency management.

## Build/Lint/Test Commands

### Dependency Management
```bash
uv sync --group dev          # Install dependencies including dev tools
source .venv/bin/activate    # Activate virtual environment
```

### Running Tests (Single Test)
```bash
uv run pytest                # Run all tests in tests/ directory
uv run pytest tests/         # Run specific test file
uv run pytest tests/test_foo.py -k test_specific  # Run single test by name pattern
uv run pytest tests/test_foo.py::test_name  # Run specific test function exactly
```

### Code Quality
```bash
uv run ruff check .          # Lint code (selects E, F, I, B rules)
uv run ruff format .         # Format code (120 char lines, double quotes)
uv run mypy                  # Type checking with strict settings
uv run pre-commit run --all-files  # Run all git hooks
```

### Development Workflow
```bash
python -m nanobot.main --config config.yaml    # Run the bot
uv run python -m nanobot.debug_cli --config config.yaml <command>  # Debug CLI for inspection
```

## Code Style Guidelines

### Imports
- Use `from __future__ import annotations` at top of all files
- Standard library imports first (alphabetically: os, sys, typing)
- Third-party imports second (alphabetically: mcp, yaml, pytest)
- Local imports last as `nanobot.*` (alphabetically within group)
- Group related imports with blank lines between groups

### Type Annotations
- Use explicit return type annotations for all functions: `-> None`, `-> str`, etc.
- Use modern generics: `list[type]`, `dict[str, type]`, not `List`, `Dict`
- Use union syntax: `str | None` instead of `Optional[str]`
- Use `cast(Any, ...)` when interfacing with dynamic code or mocks
- Annotate function parameters for public APIs and complex internal functions

### Naming Conventions
- Classes: PascalCase (`BotCore`, `Config`, `McpHub`)
- Functions/variables: snake_case (`load_config`, `chat_scope`, `tool_hooks`)
- Constants: UPPER_SNAKE_CASE (`MAX_TOKENS`, `SCRATCHPAD_TOOL_NAME`, `EMPTY_REPLY_FALLBACK`)
- Private members: single leading underscore (`_process`, `_send`, `_build_config`)

### Formatting Standards (Ruff Config)
- Line length: 120 characters maximum
- Indentation: spaces (4 spaces per level)
- Quotes: double quotes for all strings (`"` not `'`)
- Blank lines: two between top-level definitions, one inside functions
- Trailing commas in multi-line function calls and type hints

### Error Handling
- Use `try/except` with specific exception types where meaningful (KeyError, ValueError)
- Catch broad exceptions only when necessary (`# pylint: disable=broad-except`)
- Log errors with full context using `logger.exception()` for traceback
- Never silently swallow exceptions - always log or re-raise
- Use descriptive error messages including relevant context

### Logging
- Use `logging.getLogger(__name__)` for module-specific logger instance
- Include contextual information: scope, chat_id, tool_name, step number
- Use appropriate levels: `info` for normal flow, `warning` for recoverable issues, `error`/`exception` for failures
- Clip long strings in logs using helpers like `clip()` or `tool_result_preview()` to avoid log spam

### Async Patterns
- Always use `async def` and `await` appropriately throughout the codebase
- Use `AsyncExitStack` for managing multiple async context managers (see McpHub)
- Pass bot instance explicitly where needed instead of relying on closures

### Data Classes
- Prefer `@dataclass` over plain dicts for structured data (ModelConfig, ChannelConfig)
- Use `field(default_factory=...)` for mutable defaults like lists and dicts
- Mark frozen dataclasses with `frozen=True` when immutable (ToolCallEvent)
- Include type annotations on all fields

### Testing Conventions (Pytest)
- Tests live in `tests/` directory parallel to source structure
- Use `tmp_path` fixture from pytest for temporary file/directory testing
- Create fake implementations for external dependencies: `_FakeChannel`, `_FakeLlm`, `_FakeMcp`
- Mock external services with classes inheriting from expected interfaces
- Test both success paths and error/recovery scenarios (garbled output, tool failures)
- Use `asyncio.run()` to execute async test methods
- Name helper functions starting with underscore (`_build_config`)
