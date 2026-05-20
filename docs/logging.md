# Logging

Config-driven logging built on Python's stdlib `logging` module. No external dependencies.

## Overview

Nanobot configures logging through the `logging` section in `config.yaml`. A `logging` section is required — `setup_logging()` raises `ValueError` if it's missing. Use `config.example.yaml` as a starting point.

Design principles:

- **Levels go on handlers, not loggers.** Loggers pass everything through; handlers decide what to emit. This avoids logger-level settings silently blocking messages that other handlers need.
- **A logger with its own `handlers` list does not propagate to the root.** Assign dedicated handlers and it writes only there.

## Quick start

Add a `logging` section to `config.yaml` (copy from `config.example.yaml`):

```yaml
logging:
  format: "%(asctime)s %(levelname)s %(name)s - %(message)s"
  handlers:
    - name: console
      type: console
      level: INFO
    - name: file
      type: file
      level: DEBUG
      options:
        filepath: "data/nanobot.log"
        max_bytes: 2000000
        backup_count: 3
        encoding: "utf-8"
```

Then use logging as usual:

```python
import logging
logger = logging.getLogger(__name__)
```

## Configuration

Add a `logging` section to `config.yaml`:

```yaml
logging:
  format: "%(asctime)s %(levelname)s %(name)s - %(message)s"
  handlers:
    - name: console
      type: console
      level: INFO
    - name: file
      type: file
      level: DEBUG
      options:
        path: "data/nanobot.log"
        max_bytes: 2000000
        backup_count: 3
        encoding: "utf-8"
  loggers:
    nanobot.evaluator.io:
      handlers: [evaluator-file]
      level: DEBUG
```

### Schema reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `format` | string | `%(asctime)s %(levelname)s %(name)s - %(message)s` | Log format string |
| `handlers` | list | `[]` | Handler definitions (see below) |
| `loggers` | dict | `{}` | Logger overrides by dotted name |

When `handlers` is empty, the root logger gets no handlers (and no log output is emitted). 

### HandlerConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Unique identifier; referenced by `loggers` entries |
| `type` | string | required | Handler type: `console` or `file` (or a custom registered type) |
| `level` | string | `"NOTSET"` | Minimum level this handler emits. `NOTSET` = pass everything |
| `options` | dict | `{}` | Type-specific options (path, rotation, etc.) |

### LoggerConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `handlers` | list of strings | `[]` | Handler names to attach to this logger |
| `level` | string | `"NOTSET"` | Logger level (rarely needed — prefer handler-level filtering) |

When a logger has a non-empty `handlers` list, `propagate` is set to `False`, so messages go only to those handlers.

## Handler types

### console

Streams to `sys.stdout`. No options.

```yaml
- name: console
  type: console
  level: INFO
```

### file

Rotating file handler with size-based rotation.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `filepath` | string | `data/nanobot.log` | Log file path |
| `max_bytes` | int | `2000000` (2 MB) | Max file size before rotation |
| `backup_count` | int | `3` | Number of rotated backups to keep |
| `encoding` | string | `"utf-8"` | File encoding |

```yaml
- name: file
  type: file
  level: DEBUG
  options:
    filepath: "data/nanobot.log"
    max_bytes: 5000000
    backup_count: 5
    encoding: "utf-8"
```

## Handler Factory

The `HandlerFactory` registry lets you register custom handler types (Loki, Datadog, syslog) without modifying nanobot source:

```python
from nanobot.logging import HandlerFactory, setup_logging
from logging.handlers import SysLogHandler

# Register a custom handler type
HandlerFactory.register("syslog", lambda opts: SysLogHandler(
    address=(opts.get("host", "localhost"), opts.get("port", 514))
))

# Then use it in config:
# logging:
#   handlers:
#     - name: syslog
#       type: syslog
#       level: WARNING
#       options:
#         host: "logs.example.com"
#         port: 514
```

The factory function receives the `options` dict and must return a `logging.Handler` instance. Register custom types before calling `setup_logging()`.

### Built-in registrations

| Type | Class | Options used |
|------|-------|--------------|
| `console` | `logging.StreamHandler` | None |
| `file` | `logging.handlers.RotatingFileHandler` | `path`, `max_bytes`, `backup_count`, `encoding` |

## Per-module filtering

Filtering happens at the handler level, not the logger level. This is intentional:

```yaml
# Handler-level filtering (correct)
logging:
  handlers:
    - name: console
      type: console
      level: WARNING        # Only WARNING+ to console
    - name: file
      type: file
      level: DEBUG          # Everything to file
  loggers: {}               # No logger overrides needed
```

```yaml
# Logger-level filtering (avoid — blocks messages before handlers see them)
logging:
  handlers:
    - name: console
      type: console
  loggers:
    nanobot.mcp_hub:
      level: WARNING        # This blocks DEBUG/INFO before file handler sees them
```

If you need a module to log at a different level to a specific destination, create a dedicated handler with the right level and assign it via `loggers`. The module's logger will then write only to that handler (`propagate` becomes `False`).

## Evaluator logger

The learning evaluator logs its LLM I/O to a dedicated file for debugging prompt quality and tracking extracted skills.

```yaml
logging:
  format: "%(asctime)s %(levelname)s %(name)s - %(message)s"
  handlers:
    - name: console
      type: console
      level: INFO
    - name: file
      type: file
      level: DEBUG
    - name: evaluator-file
      type: file
      level: DEBUG
      options:
        filepath: "data/evaluator.log"
  loggers:
    nanobot.evaluator.io:
      handlers: [evaluator-file]
      level: DEBUG
```

The evaluator logger (`nanobot.evaluator.io`) uses `propagate=False` by design — its messages go to `data/evaluator.log` only, not to the root handlers. This keeps prompt/response traces separate from general bot logs.

## Migration guide

Before config-driven logging, everything was hardcoded in `main.py`: root always at `INFO`, always console + file, no way to change format or levels. The evaluator logger was set up independently in `evaluator/runner.py`.

### What changed

| Before | After |
|--------|-------|
| Hardcoded in `setup_logging()` | Configurable via `logging` section in `config.yaml` |
| Root always at `INFO` | Root level respects config |
| Always console + file | Any combination of handlers |
| No custom handler types | `HandlerFactory` registry for extensibility |
| Evaluator logger hardcoded | Evaluator logger lives in `loggers` config |
| Missing config → hardcoded defaults | Missing config → `ValueError` (fail loud) |

### What stayed the same

- `logging.getLogger(__name__)` works exactly as before
- `get_logger(name)` is available as a thin wrapper that returns `logging.getLogger(name)`

## Examples

### Debug mode

Route everything to console and file at DEBUG level:

```yaml
logging:
  format: "%(asctime)s %(levelname)s %(name)s - %(message)s"
  handlers:
    - name: console
      type: console
      level: DEBUG
    - name: file
      type: file
      level: DEBUG
```

### Production

Console at WARNING (quiet), file at DEBUG (verbose for post-mortem):

```yaml
logging:
  format: "%(asctime)s %(levelname)s %(name)s - %(message)s"
  handlers:
    - name: console
      type: console
      level: WARNING
    - name: file
      type: file
      level: DEBUG
      options:
        path: "/var/log/nanobot/nanobot.log"
        max_bytes: 10000000
        backup_count: 10
```

### Evaluator-only debug

Debug to a dedicated evaluator log while keeping general log at INFO:

```yaml
logging:
  format: "%(asctime)s %(levelname)s %(name)s - %(message)s"
  handlers:
    - name: console
      type: console
      level: INFO
    - name: file
      type: file
      level: INFO
    - name: eval-debug
      type: file
      level: DEBUG
      options:
        filepath: "data/evaluator.log"
  loggers:
    nanobot.evaluator.io:
      handlers: [eval-debug]
      level: DEBUG
```

### Minimal config (override format only)

```yaml
logging:
  format: "%(levelname)s [%(name)s] %(message)s"
  handlers:
    - name: console
      type: console
      level: INFO
    - name: file
      type: file
      level: DEBUG
      options:
        filepath: "data/nanobot.log"
```