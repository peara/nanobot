# MCP Servers

How to configure, extend, and manage MCP (Model Context Protocol) servers in nanobot.

## Overview

MCP servers are subprocesses that expose tools to the LLM via the Model Context Protocol stdio transport. NanoBot starts each server from `config.yaml`, connects over stdio, and auto-discovers every tool the server declares. Tools appear to the LLM with a `name__tool` naming convention, where `name` is the server's config key. No Python registration is needed. McpHub manages the full lifecycle: startup, tool discovery, dispatch, and shutdown.

## Configuration

MCP servers are declared in `config.yaml` under the `mcp_servers` key:

```yaml
mcp_servers:
  - name: "timer"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.timer.server"]
    env: {}

  - name: "scheduler"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.scheduler.server"]
    env: {}

  - name: "web"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.web.server"]
    env:
      WEB_AGENT_HEADLESS: "true"
      WEB_AGENT_SAVE_OUTPUTS: "true"

  - name: "playwright"
    command: "npx"
    args:
      - "-y"
      - "@playwright/mcp@latest"
      - "--browser"
      - "chrome"
      - "--headless"
    env: {}
```

### Server config fields

Each entry maps to the `McpServerConfig` dataclass in `config.py`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Server identifier. Becomes the tool namespace prefix. Must be unique. |
| `command` | `str` | Executable to run (e.g., `"python"`, `"npx"`). |
| `args` | `list[str]` | Arguments passed to the command. Default: `[]`. |
| `env` | `dict[str, str]` | Extra environment variables passed to the subprocess. Values support `${VAR}` expansion from the parent environment. Default: `{}`. |
| `required_env` | `list[str]` | Environment variable names that must be present before the server starts. Default: `[]`. |

### required_env

A list of environment variable names that must be set before nanobot attempts to start the server. If any variable is missing, McpHub logs a `WARNING` and skips the server entirely. The bot continues without it. This is the mechanism for optional services that depend on credentials.

```yaml
- name: "reddit"
  command: "python"
  args: ["-m", "nanobot.mcp_servers.reddit.server"]
  env: {}
```

The Reddit server uses public JSON endpoints — no credentials are required. If `required_env` is empty or absent, the server always starts.

### env

A dict of environment variables injected into the server subprocess. Values can reference the parent process environment using `${VAR}` syntax. Expansion happens at config load time via `os.path.expandvars()` (see `_expand_env_value` in `config.py`).

```yaml
env:
  WEB_AGENT_HEADLESS: "true"                  # literal value
  REDDIT_USER_AGENT: "mybot/2.0 (by /u/user)" # literal value for Reddit
  DB_PATH: "./data/scheduler.db"              # literal path
```

All values from `env` are merged into a copy of the parent environment before the subprocess starts. The parent's full environment is always inherited; `env` adds or overrides specific keys.

## Config override

### config.override.yaml

A git-ignored override file that deep-merges into `config.yaml`. The path is derived automatically: `config.yaml` maps to `config.override.yaml`. If the override file exists, `load_config()` reads both and merges them with `_deep_merge()`.

```python
# config.py — load_config()
override_path = str(config_path).removesuffix(".yaml") + ".override.yaml"
if Path(override_path).is_file():
    override_data = yaml.safe_load(override_raw) or {}
    data = _deep_merge(data, override_data)
```

Use this file for credentials, machine-specific paths, and optional servers that shouldn't appear in the shared config.

### Deep merge rules

| Type | Rule |
|------|------|
| `dict` | Merge recursively (keys in override win on conflict) |
| `list` | Append (override list items are added after base list items) |
| `scalar` | Override wins (replace base value entirely) |

Example: adding custom configuration in `config.override.yaml`:

```yaml
mcp_servers:
  - name: "reddit"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.reddit.server"]
    env:
      REDDIT_USER_AGENT: "mybot/2.0 (by /u/myusername)"
```

Since `mcp_servers` is a list, this entry is appended after the base servers defined in `config.yaml`. The base servers (timer, scheduler, web, playwright) remain unchanged.

### Why override?

Keeps `config.yaml` (and `config.example.yaml`) clean of credentials and machine-specific settings. Allows per-machine configuration without git drift. Matches the Docker Compose override pattern: a shared base config plus a local overlay that never touches version control.

## Graceful degradation

`McpHub.start()` wraps each server launch in a `try/except`. Two skip paths exist:

1. **Missing `required_env`** — logged at `WARNING` level, server is skipped.
2. **Startup crash** (connection failure, subprocess exit, protocol error) — logged at `ERROR` with full traceback, server is skipped.

In both cases, the bot continues with whatever servers did start. No single server can block startup.

```
INFO     Started MCP server 'timer' (2 tools)
INFO     Started MCP server 'scheduler' (7 tools)
INFO     Started MCP server 'reddit' (5 tools)
WARNING  Skipping MCP server 'broken': missing required env vars: ['API_KEY']
ERROR    Failed to start MCP server 'broken', skipping
         Traceback (most recent call last): ...
INFO     MCP servers started: ['timer', 'scheduler', 'reddit']
```

Tools from skipped servers simply don't appear in the LLM's tool list. The agent cannot call tools that were never registered.

## Adding a new MCP server

1. **Create** the server package under `src/nanobot/mcp_servers/<name>/`:

`__init__.py`:
```python
from __future__ import annotations

__all__ = []
```

`server.py`:
```python
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("nanobot-weather")


@mcp.tool()
async def weather_current(location: str) -> dict[str, str]:
    """Get current weather for a location."""
    ...
    return {"ok": "true", "location": location, "temperature": "22C"}


if __name__ == "__main__":
    mcp.run()
```

2. **Implement tools** using the `@mcp.tool()` decorator. Each function becomes a tool the LLM can call. Tool names are auto-namespaced: a tool named `weather_current` on a server named `"weather"` appears as `weather__weather_current` to the LLM. The `__` separator is added by McpHub when it discovers tools.

3. **Add a config entry** in `config.yaml` (or `config.override.yaml` for optional services):

```yaml
mcp_servers:
  - name: "weather"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.weather.server"]
    env: {}
```

4. **Test standalone** by running the server directly:

```bash
python -m nanobot.mcp_servers.weather.server
```

If the server starts without errors, it will work when launched by McpHub.

5. **Add to config.yaml** if the server should be enabled everywhere, or to `config.override.yaml` if it needs credentials or is machine-specific.

No Python registration is needed. McpHub auto-discovers tools from the config entry.

## Optional services

Services that require credentials (API keys, OAuth tokens) use the `required_env` + `config.override.yaml` pattern together. The server entry goes in `config.override.yaml` with `required_env` listing every credential variable. If any credential is absent, the server is silently skipped at startup without affecting other servers.

The Reddit server is an exception — it uses public JSON endpoints and requires no credentials. It can be added directly to `config.yaml` with no `required_env`.