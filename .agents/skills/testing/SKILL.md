---
name: testing
description: How to write and run tests in nanobot - unit tests, integration tests, fixtures, and conventions
---

## What I do

I guide writing, running, and maintaining tests in the nanobot project. I define when to use unit tests vs integration tests, what patterns to follow, and how to run them.

## When to use me

Use this when:
- Writing new tests for any nanobot module
- Deciding whether a test should be unit or integration
- Setting up test fixtures for async code, BotCore, or SQLite stores
- Running tests locally (`just test`, `just test-all`)
- Debugging failing tests
- Understanding test conventions and patterns in this repo

## Key Principles

### 1. Unit tests by default, integration tests for external services

**Unit tests** test code in isolation using fakes, mocks, and in-memory or temp-path stores. They must NOT require running external services (LM Studio, Qdrant, Telegram API).

**Integration tests** test real end-to-end flows with live external services. They are marked with `@pytest.mark.integration` and skipped by default.

| Aspect | Unit Test | Integration Test |
|--------|-----------|------------------|
| External services | Mocked or faked | Real (LM Studio, Qdrant) |
| Database | `:memory:` or `tmp_path` | Real paths, real Qdrant |
| Speed | Fast (<1s per test) | Slow (seconds to minutes) |
| Markers | None (default) | `@pytest.mark.integration` |
| Run command | `just test` | `just test-all` |
| When to write | Always | When testing LLM extraction, vector search, or real API flows |

**Write integration tests when:**
- Testing mem0/VectorStore with real LLM fact extraction and embedding
- Testing Qdrant round-trips (save → search)
- Verifying LLM response format compatibility (e.g., json_schema vs json_object)
- Testing deduplication or semantic search that requires real embeddings

**Write unit tests for everything else** — tool logic, message routing, guard behavior, command handling, context store operations, prompt rendering, plan store CRUD, schedule logic.

### 2. Test location mirrors source structure

```
src/nanobot/subagents/manager.py  →  tests/subagents/test_manager.py
src/nanobot/plans/store.py        →  tests/plans/test_plan_store.py
src/nanobot/agent_run.py          →  tests/agent_run/test_agent_run.py
src/nanobot/mcp_servers/web/      →  tests/mcp_servers/test_web_*.py
```

Top-level modules (core.py, config.py, messages.py) get top-level test files:
```
src/nanobot/core.py       →  tests/test_core_queue.py
src/nanobot/config.py     →  tests/test_config.py
src/nanobot/messages.py   →  tests/test_messages.py
```

### 3. Async tests use `asyncio_mode = "auto"`

The project uses `pytest-asyncio` with `asyncio_mode = "auto"`. This means:

- **You do NOT need `@pytest.mark.asyncio`** on most async tests — pytest automatically detects `async def test_*` functions
- Use `@pytest.mark.asyncio` only when needed for async methods on classes (rare)
- For running async code inside sync tests, use `asyncio.run()`:

```python
def test_my_sync_wrapper() -> None:
    result = asyncio.run(my_async_function())
    assert result == expected
```

### 4. Fake classes, not mock objects

This project uses **fake implementations** that match real interfaces, not `MagicMock` or `Mock` objects. Fakes are defined inline in test files.

```python
# CORRECT: Fake classes matching real interfaces
class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class _FakeLlm:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = replies
        self._idx = 0

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> dict:
        if self._idx >= len(self._replies):
            raise RuntimeError("No fake LLM reply left")
        reply = self._replies[self._idx]
        self._idx += 1
        return reply
```

```python
# WRONG: MagicMock — too permissive, hides real interface errors
mock_channel = MagicMock()
mock_channel.send.return_value = None  # Accepts any args, any call count
```

**When to use `unittest.mock.patch`** (sparingly):
- `patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=...)` — patching a specific method on a real BotCore instance
- `patch.object(bot.memory, "add_message")` — stubbing out slow/external dependencies on a real object
- Never patch the module under test itself — patch its dependencies

### 5. BotCore fixture pattern

When tests need a real BotCore instance (for integration-style unit tests):

```python
def _make_config(*, enable_evaluator: bool = False) -> Any:
    tmp = tempfile.mkdtemp()
    return AppConfig(
        assistant_name="TestBot",
        database_path=f"{tmp}/nanobot.db",
        scheduler_db_path=f"{tmp}/scheduler.db",
        plan_db_path=f"{tmp}/plans.db",
        skill_db_path=f"{tmp}/skills.db",
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost", api_key="test", model="test"),
        channels=[],
        mcp_servers=[],
        prompt_db_path=f"{tmp}/prompts.db",
        enable_evaluator=enable_evaluator,
    )


class TestMyFeature:
    def test_something(self) -> None:
        config = _make_config()
        bot = BotCore(config, {"telegram": _FakeChannel()})
        # test bot behavior...
```

Key points:
- Use `tempfile.mkdtemp()` for all DB paths — each test gets isolated storage
- Use `_FakeChannel()` — never real Telegram in unit tests
- Model config points to `http://localhost` — no real LLM calls
- `channels=[]` and `mcp_servers=[]` — no external connections

### 6. Store/database fixture patterns

For stores that use SQLite, use `tmp_path` (pytest built-in) or `tempfile`:

```python
# Pattern A: tmp_path fixture (preferred for path-based stores)
def test_plan_store(tmp_path: Path) -> None:
    store = PlanStore(str(tmp_path / "plans.db"))
    plan = store.create(name="Test", goal="Do something")
    assert plan.name == "Test"


# Pattern B: tempfile.mkdtemp (used when creating full AppConfig)
def _make_store(tmp_path: Path) -> PlanStore:
    db_path = str(tmp_path / "plans.db")
    return PlanStore(db_path)
```

For in-memory stores when the store supports `:memory:`:

```python
# Only use :memory: if the store explicitly supports it
# Check the store's constructor — most use file paths
```

### 7. Integration test requirements

Integration tests **require live external services**. They must:

1. **Be marked with `@pytest.mark.integration`**
2. **Include a skip condition** for unavailable services
3. **Document what's required** in the module docstring

```python
"""Integration tests for mem0 memory pipeline (VectorStore + real LLM).

Exercises the full mem0 pipeline: fact extraction LLM -> dedup LLM ->
embedding -> Qdrant storage -> retrieval. Requires LM Studio running at
localhost:1234 with the configured model loaded. The bot must be STOPPED
(Qdrant local mode uses exclusive file locks).

Run: uv run pytest tests/memstore/test_integration.py -v
Skip: uv run pytest tests/memstore/test_integration.py -v -k "not integration"
"""

requires_lmstudio = pytest.mark.skipif(
    not _lmstudio_reachable(),
    reason="LM Studio not reachable at localhost:1234",
)


@requires_lmstudio
class TestMem0SaveSearch:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_and_search_fact(self, vs: VectorStore) -> None:
        # ...
```

4. **Be excluded from default test runs** — `just test` uses `-m "not integration"` to skip them
5. **Provide both run and skip commands** in the module docstring

### 8. Running tests

```bash
# Unit tests only (excludes integration — FAST, no external services needed)
just test

# All tests including integration (requires LM Studio + Qdrant)
just test-all

# Specific package (unit only)
just test-pkg subagents

# Specific test file
just test-file plans/test_plan_store.py

# Specific test by name
just test-name test_plan_store

# Run only integration tests
uv run pytest -m integration

# Run a single integration test file
uv run pytest tests/memstore/test_integration.py -v
```

### 9. Recording tools for agent_run tests

When testing `AgentRun` behavior (tool call sequences, guard logic, finalize paths), use `_RecordingFakeLlm` and `_RecordingTool`:

```python
class _RecordingFakeLlm(_FakeLlm):
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        super().__init__(replies)
        self.calls_messages: list[list[dict[str, Any]]] = []
        self.calls_tools: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> dict:
        self.calls_messages.append(messages)
        self.calls_tools.append(tools)
        return await super().chat(messages, tools, response_format)


class _RecordingTool(Tool):
    def __init__(self, name: str, call_log: list[tuple[str, dict]]) -> None:
        self._name = name
        self._call_log = call_log

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Recording tool {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        self._call_log.append((self._name, dict(args)))
        return "ok"
```

These let you assert exact LLM call sequences and tool argument flow without real LLM calls.

## Common Patterns

### Simple synchronous test

```python
def test_message_scope() -> None:
    msg = UserMessage(channel="telegram", chat_id="123", text="hello")
    assert msg.scope == "telegram:123"
```

### Async test (auto-detected, no decorator needed)

```python
async def test_bot_enqueue(bot: BotCore) -> None:
    incoming = IncomingMessage(channel="telegram", chat_id="123", user_id="u1", text="hi")
    await bot.on_incoming(incoming)
    assert bot._message_queue.qsize() == 1
```

### Test with mocked method on real BotCore

```python
@pytest.mark.asyncio
async def test_evaluate_turn_calls_evaluator() -> None:
    config = _make_config(enable_evaluator=True)
    bot = BotCore(config, {"telegram": _FakeChannel()})

    with patch.object(bot.evaluator, "evaluate", new_callable=AsyncMock, return_value=eval_result) as mock_eval:
        await bot._evaluate_turn("telegram:123", "hello", result)
        assert mock_eval.call_count == 1
```

### Integration test with skip guard

```python
def _service_reachable() -> bool:
    try:
        req = urllib.request.Request("http://localhost:1234/v1/models")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False

requires_service = pytest.mark.skipif(
    not _service_reachable(),
    reason="Service not reachable at localhost:1234",
)

@requires_service
class TestMyFeature:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_real_pipeline(self) -> None:
        # ...
```

### Testing store CRUD (create, read, update, delete)

```python
def test_plan_create_and_retrieve() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = PlanStore(str(Path(tmpdir) / "plans.db"))
        plan = store.create(name="Test", goal="Do something")
        retrieved = store.get(plan.id)
        assert retrieved is not None
        assert retrieved.name == "Test"
```

## Repo-Specific Conventions

### Imports in test files

```python
from __future__ import annotations  # ALWAYS first

# stdlib
import json
import tempfile
from typing import Any
from unittest.mock import AsyncMock, patch

# third-party
import pytest

# local
from nanobot.core import BotCore
from nanobot.config import AppConfig, ModelConfig
```

### Type annotations

- Explicit return types on ALL test functions: `-> None`, `-> str`
- Use `from __future__ import annotations` for modern type syntax in signatures

### Ruff formatting in tests

- Line length: 120 chars
- Double quotes only
- Trailing commas in multi-line calls (same as source code)

### Error handling in tests

- **Never** use empty `except:` blocks in tests
- If testing error paths, assert on the specific exception or error message
- Use `pytest.raises(ValueError)` for expected exceptions

### Conftest fixtures

`tests/conftest.py` is minimal — only `process_incoming_sync` helper. Most fixtures are defined inline in each test file using `_make_config()`, `_FakeChannel()`, etc.

This is intentional: tests should be self-contained and not rely on hidden shared state. Prefer inline setup over shared fixtures.

### Test naming

- Test functions: `test_<behavior>` — e.g., `test_should_notify_user_returns_true_for_tools_used`
- Test classes: `Test<Feature>` — e.g., `TestEvaluatorIntegration`, `TestMem0SaveSearch`
- Integration test classes: group by feature, e.g., `TestMem0DedupRegression`

## Checklist

When writing tests for a new feature:

- [ ] Test file mirrors source location: `src/nanobot/foo/` → `tests/foo/`
- [ ] Unit tests use fakes/mocks, no live services
- [ ] Integration tests marked `@pytest.mark.integration` with skip guards
- [ ] Integration test file has module docstring with run/skip commands
- [ ] `from __future__ import annotations` at top
- [ ] Explicit return type annotations on all test functions
- [ ] `just test` passes (unit tests only)
- [ ] All store/DB paths use `tmp_path` or `tempfile.mkdtemp()`
- [ ] No `MagicMock` — use `_Fake*` classes or `patch.object`
- [ ] Async tests work without `@pytest.mark.asyncio` (use `async def test_*`)
- [ ] Double quotes for strings, 120 char line limit, trailing commas