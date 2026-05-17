---
name: add-feature
description: Step-by-step guide for adding new features (channel, command, MCP server, tool, hook) to nanobot
---

## What I do

I guide the implementation of new features in nanobot by providing exact file paths, registration points, and code templates for each feature type. This prevents the most common class of bugs: "I created the file but forgot to register it."

## When to use me

Use this when:
- Adding a new channel (Telegram, Discord, Slack, etc.)
- Adding a new slash command (`/mycommand`)
- Adding a new MCP server (e.g., a `weather` server)
- Adding a new built-in tool (e.g., `search__semantic`)
- Adding a new tool hook (e.g., logging, metrics, notification)
- Any time you're creating a new module that needs to plug into the bot

## Principles

1. **Every feature type has a registration step.** Missing registration is the #1 source of "why isn't it working" bugs. Each section below ends with an explicit checklist.
2. **Follow the existing patterns.** Don't invent new registries or dispatchers. Use the same patterns the existing features use.
3. **Test location mirrors source.** `src/nanobot/foo/` → `tests/foo/`.

---

## 1. Add a New Channel

A channel routes messages between users and the bot. Implement the `Channel` ABC, register in the channel factory, and add config.

### Files to Create/Modify

| Action | File | What |
|--------|------|------|
| Create | `src/nanobot/channels/<name>.py` | `MyChannel(Channel)` implementation |
| Modify | `src/nanobot/channels/__init__.py` | Add import |
| Modify | `src/nanobot/main.py` | Add to `build_channels()` factory |
| Modify | `config.yaml` | Add channel config entry |
| Create | `tests/channels/test_<name>.py` | Tests |

### Channel Interface (`channels/base.py`)

```python
class Channel(ABC):
    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    def set_handler(self, handler: MessageHandler) -> None: ...
    async def emit(self, message: IncomingMessage) -> None: ...   # inbound: channel → core

    @abstractmethod
    async def start(self) -> None: ...       # Start listening/polling
    @abstractmethod
    async def stop(self) -> None: ...        # Graceful shutdown
    @abstractmethod
    async def send(self, chat_id: str, text: str) -> None: ...  # outbound: core → channel
```

### Minimal Template

```python
from __future__ import annotations

import logging
from nanobot.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)


class DiscordChannel(Channel):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    async def start(self) -> None:
        # Start polling/listening for incoming messages
        # When a message arrives, call:
        #   await self.emit(IncomingMessage(
        #       channel="discord",
        #       chat_id=str(message.channel_id),
        #       user_id=str(message.author.id),
        #       text=message.content,
        #   ))
        ...

    async def stop(self) -> None:
        # Graceful shutdown
        ...

    async def send(self, chat_id: str, text: str) -> None:
        # Send outbound message from bot to user
        ...
```

### Registration: `main.py` → `build_channels()`

Add a new branch to the factory function:

```python
elif cfg.type == "discord":
    channels["discord"] = DiscordChannel(token=cfg.token)
```

If your channel produces extra hooks (like `FileTraceHook` for `FileChannel`), return them from the factory:

```python
extra_hooks: list[ToolHook] = []
# ...
if cfg.type == "file":
    file_channel = FileChannel(...)
    if capture_tool_calls:
        extra_hooks.append(FileTraceHook(out_file=file_channel._out_file))
```

### Registration: `config.yaml`

```yaml
channels:
  - type: "discord"
    token: "${DISCORD_BOT_TOKEN}"
    options:
      poll_interval: 1.0
```

### Checklist

- [ ] `Channel` ABC methods implemented (`start`, `stop`, `send`)
- [ ] `self.emit(IncomingMessage(channel="<name>", ...))` called on inbound messages
- [ ] Import added to `channels/__init__.py`
- [ ] Branch added in `build_channels()` in `main.py`
- [ ] Config entry added to `config.yaml`
- [ ] Tests created in `tests/channels/`
- [ ] Scope format documented: `<name>:<chat_id>` (used by `core_utils.scoped_chat_id`)

---

## 2. Add a New Built-in Command

A slash command (`/mycommand`) processed before the LLM. Implement `BaseCommand`, register in `CommandManager`.

### Files to Create/Modify

| Action | File | What |
|--------|------|------|
| Create | `src/nanobot/core_commands/commands/<name>.py` | Command class |
| Modify | `src/nanobot/core_commands/command_manager.py` | Import + register |

### BaseCommand Contract (`commands/base.py`)

```python
class BaseCommand(ABC):
    def __init__(self, core) -> None:
        self.core = core

    @classmethod
    @abstractmethod
    def names(cls) -> list[str]:
        """Slash-commands this handler responds to (e.g. ["/reset"])."""

    @abstractmethod
    async def handle(self, raw_text: str, scope: str) -> None:
        """Process the command. raw_text is the full user message, scope is the chat scope."""

    async def _send(self, scope: str, text: str) -> None:
        """Convenience: send a reply via core._send."""

    async def handle_with_error_handling(self, raw_text: str, scope: str) -> None:
        """Wrapper that catches exceptions and sends an error reply."""
```

### Minimal Template (simplest real example: `reset.py`)

```python
from __future__ import annotations

import logging

from nanobot.core_commands.commands.base import BaseCommand

logger = logging.getLogger(__name__)


class MyCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/mycommand"]

    async def handle(self, raw_text: str, scope: str) -> None:
        # Parse args with command_body() from nanobot.core_utils if needed
        # Access bot state via self.core.memory, self.core.config, etc.
        # Send reply with self._send()
        await self._send(scope, "Response text")
```

### Subcommand Pattern (see `plan.py` for full example)

For commands with sub-actions like `/plan list`, `/plan show <id>`:

```python
from nanobot.core_utils import command_body

class PlanCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/plan"]

    async def handle(self, raw_text: str, scope: str) -> None:
        body = command_body(raw_text)  # "/plan list" → "list"
        parts = body.split(maxsplit=1)
        subcmd = parts[0] if parts else ""
        # dispatch subcmd...
```

### Registration: `command_manager.py`

Two changes required:

1. Add import at top of file:
```python
from nanobot.core_commands.commands.mycommand import MyCommand  # noqa: F401
```

2. Add register call in `_register_commands()`:
```python
def _register_commands(self) -> None:
    # ... existing registrations ...
    self._register(MyCommand)
```

### Checklist

- [ ] `names()` returns list of `/command` strings (lowercase, with `/` prefix)
- [ ] `handle()` is async and uses `self._send()` for replies
- [ ] `self.core` gives access to memory, config, contexts, tools
- [ ] Import added to `command_manager.py`
- [ ] `self._register(MyCommand)` added to `_register_commands()`
- [ ] Tests in `tests/core_commands/test_<name>.py`

---

## 3. Add a New MCP Server

An MCP server exposes tools to the LLM via the Model Context Protocol stdio transport. Use `FastMCP` and configure in `config.yaml`.

### Files to Create/Modify

| Action | File | What |
|--------|------|------|
| Create | `src/nanobot/mcp_servers/<name>/__init__.py` | Empty (`__all__ = []`) |
| Create | `src/nanobot/mcp_servers/<name>/server.py` | FastMCP server + tools |
| Modify | `config.yaml` | Add mcp_servers entry |
| Create | `tests/mcp_servers/test_<name>.py` | Tests |

### Minimal Template (modeled on `timer/server.py`)

```python
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("nanobot-weather")


@mcp.tool()
async def weather_current(location: str) -> dict[str, str]:
    """Get current weather for a location."""
    # Implementation goes here
    return {"ok": "true", "location": location, "temperature": "22C"}


if __name__ == "__main__":
    mcp.run()
```

### Key Conventions

1. **Server name**: `FastMCP("nanobot-<name>")` — must use `nanobot-` prefix
2. **Tool decorator**: `@mcp.tool()` on every tool function (sync or async)
3. **Return format**: `dict[str, Any]` with `"ok"` key pattern
4. **Entry point**: `if __name__ == "__main__": mcp.run()` — required for `python -m` execution
5. **Config via env vars**: Use `os.environ.get("KEY", default)` for paths/flags
6. **Tool naming**: Tools become `<server_name>__<tool_name>` in the LLM (e.g., `weather__current`). The `__` separator is added automatically by `McpHub`.

### Registration: `config.yaml`

```yaml
mcp_servers:
  - name: "weather"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.weather.server"]
    env: {}
```

If your server needs DB paths or config:

```yaml
  - name: "weather"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.weather.server"]
    env:
      WEATHER_DB_PATH: "./data/weather.db"
```

### Registration: No Python Changes Needed

McpHub auto-discovers tools from config. The `name` in config becomes the tool namespace prefix. No `import` or `register()` call needed in Python code.

### Hot-Reload

Use `/reload mcp` command to hot-reload MCP servers without restarting the bot.

### Checklist

- [ ] Package created: `mcp_servers/<name>/__init__.py` (empty) + `server.py`
- [ ] `FastMCP("nanobot-<name>")` used as server name
- [ ] `@mcp.tool()` decorator on all tool functions
- [ ] `if __name__ == "__main__": mcp.run()` entry point present
- [ ] Config entry added to `config.yaml` under `mcp_servers`
- [ ] Tool names will appear as `<name>__<tool>` to the LLM
- [ ] `python -m nanobot.mcp_servers.<name>.server` runs standalone
- [ ] Tests created in `tests/mcp_servers/`

---

## 4. Add a New Built-in Tool

A built-in tool is a Python class registered directly in `ToolRegistry` (not via MCP). Implement the `Tool` ABC, then register in `BotCore.__init__()`.

### Files to Create/Modify

| Action | File | What |
|--------|------|------|
| Create | Tool class in existing module or new module | `MyTool(Tool)` implementation |
| Create/Modify | `register_*_tools()` function | Registration helper |
| Modify | `src/nanobot/core.py` | Call registration in `__init__()` |

### Tool ABC Contract (`tools/base.py`)

```python
class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...          # e.g. "memory__search"

    @property
    @abstractmethod
    def description(self) -> str: ...   # Short description for LLM

    @property
    @abstractmethod
    def schema(self) -> dict[str, Any]: ...  # OpenAI function-calling JSON schema

    @abstractmethod
    async def call(self, args: dict[str, Any]) -> str: ...  # Execute, return string

    def to_openai_spec(self) -> dict[str, Any]:  # Auto-generated from above
        ...
```

### Minimal Template

```python
from __future__ import annotations

import json
from typing import Any

from nanobot.tools.base import Tool


class SearchSemanticTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._store = vector_store

    @property
    def name(self) -> str:
        return "search__semantic"

    @property
    def description(self) -> str:
        return "Search for semantically similar documents."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
            },
            "required": ["query"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        query = args["query"]
        limit = args.get("limit", 5)
        results = await self._store.search(query, limit=limit)
        return json.dumps([r.as_dict() for r in results], ensure_ascii=False)
```

### Naming Convention

Use double-underscore prefix to group by domain: `memory__search`, `memory__save`, `plan__get`, `skill__list`. This matches the MCP convention (`timer__time_now`).

### Registration Pattern

If the tool belongs to an existing domain (e.g., adding to memory tools), add it to that module's `register_*_tools()` function:

```python
# In src/nanobot/memstore/tools.py (or wherever the group lives)
def register_memory_tools(registry: ToolRegistry, vector_store: VectorStore) -> None:
    registry.register(MemorySearchTool(vector_store))
    registry.register(MemorySaveTool(vector_store))
    # ... add new tool here
```

If it's a new domain, create a new module with its own `register_*_tools()`:

```python
# src/nanobot/search/tools.py
def register_search_tools(registry: ToolRegistry, vector_store: VectorStore) -> None:
    registry.register(SearchSemanticTool(vector_store))
```

Then call it in `BotCore.__init__()` (`core.py`):

```python
# In core.py __init__():
register_search_tools(self.tools, self.vector_store)
```

### Checklist

- [ ] Tool class extends `Tool` ABC with `name`, `description`, `schema`, `call()`
- [ ] Name follows `domain__action` convention (double underscore)
- [ ] Schema follows OpenAI function-calling format
- [ ] `call()` returns a string (typically JSON)
- [ ] Constructor receives dependencies (stores, clients) — no global state
- [ ] `register_*_tools()` function updated or created
- [ ] Registration call added to `BotCore.__init__()` in `core.py`
- [ ] Tests created

---

## 5. Add a New Tool Hook

Hooks run after every tool call (except `scratchpad__*`). They receive a `ToolCallEvent` and can observe, log, or transform results. No base class required — just satisfy the `ToolHook` protocol.

### Files to Create/Modify

| Action | File | What |
|--------|------|------|
| Create | Hook class (new file or existing hooks module) | Implement `after_tool_call()` |
| Modify | `src/nanobot/hooks/tool_hooks.py` | Add to `build_default_tool_hooks()` if always-on, OR inject via channel setup if conditional |
| Create | Tests | In `tests/` |

### ToolHook Protocol (`hooks/tool_hooks.py`)

```python
class ToolHook(Protocol):
    async def after_tool_call(self, event: ToolCallEvent, bot: Any) -> None: ...

@dataclass(frozen=True)
class ToolCallEvent:
    scope: str
    call_id: str
    tool_name: str
    args: dict[str, Any]
    result: str
    result_preview: str
    ok: bool
    error: str | None
    at: str  # ISO timestamp
```

No base class needed — just implement the `after_tool_call` method with matching signature.

### Option A: Always-On Hook

Add to `build_default_tool_hooks()` in `hooks/tool_hooks.py`:

```python
def build_default_tool_hooks() -> list[ToolHook]:
    return [
        ToolResultRecorderHook(),
        BrowseEventRecorderHook(),
        MyNewHook(),  # <-- add here
    ]
```

### Option B: Conditional Hook (e.g., channel-specific)

Inject from channel setup, like `FileTraceHook`:

```python
# In main.py build_channels():
extra_hooks: list[ToolHook] = []
if cfg.type == "file":
    extra_hooks.append(FileTraceHook(out_file=file_channel._out_file))
return channels, extra_hooks

# Then in run():
for hook in extra_hooks:
    core.tool_hooks.append(hook)
```

### Minimal Template

```python
from __future__ import annotations

import logging
from typing import Any

from nanobot.hooks.tool_hooks import ToolCallEvent

logger = logging.getLogger(__name__)


class MetricsHook:
    """Record tool call metrics to SQLite."""

    def __init__(self, db_path: str = "./data/metrics.db") -> None:
        self._db_path = db_path

    async def after_tool_call(self, event: ToolCallEvent, bot: Any) -> None:
        if not event.ok:
            logger.warning("Tool %s failed: %s", event.tool_name, event.error)
        # Log metrics, write to DB, send notification, etc.
        logger.info("Tool call: %s ok=%s scope=%s", event.tool_name, event.ok, event.scope)
```

### Dispatch Mechanism

Hooks are called sequentially by `BotCore._dispatch_after_tool_call()` after each tool call in `AgentRun.run()`. Failures are logged but don't prevent other hooks or the tool call from completing.

### Checklist

- [ ] Class implements `async def after_tool_call(self, event: ToolCallEvent, bot: Any) -> None`
- [ ] If always-on: added to `build_default_tool_hooks()` in `hooks/tool_hooks.py`
- [ ] If conditional: injected via `extra_hooks` in `build_channels()` or appended to `core.tool_hooks`
- [ ] Hook doesn't raise unhandled exceptions (or they're caught by `_dispatch_after_tool_call`)
- [ ] Tests created

---

## Quick Reference: Registration Points Summary

| Feature | Where to Register | How |
|---------|-------------------|-----|
| Channel | `main.py` → `build_channels()` | Add `elif cfg.type == "..."` branch |
| Command | `command_manager.py` → `_register_commands()` | Add `self._register(MyCommand)` + import |
| MCP Server | `config.yaml` → `mcp_servers:` entry | Add YAML entry (no Python changes) |
| Built-in Tool | `core.py` → `BotCore.__init__()` | Add `register_*_tools()` call |
| Tool Hook | `hooks/tool_hooks.py` → `build_default_tool_hooks()` | Add to return list (or inject via channel setup) |

**The most commonly forgotten step**: Registration. After creating any file, check this table for where to register it.