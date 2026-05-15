# Channels

How to add a new chat surface to NanoBot.

## Overview

Messages enter NanoBot through **channels** — adapters that receive input from a chat surface (Telegram, GitHub, a file) and feed it into the orchestrator. All channels implement the same `Channel` interface, so the core processing logic doesn't care where a message came from.

## The Channel interface

```python
from nanobot.channels.base import Channel, IncomingMessage

class MyChannel(Channel):
    async def start(self) -> None:
        # Start listening for input (polling, webhook, etc.)

    async def stop(self) -> None:
        # Graceful shutdown

    async def send(self, chat_id: str, text: str) -> None:
        # Send a reply back to the user
```

Three methods to implement. The `Channel` base class provides `set_handler()` and `emit()` for free — you wire up the handler during startup, then call `emit()` whenever input arrives.

### IncomingMessage

When your channel receives a message, call:

```python
await self.emit(IncomingMessage(
    channel="my_channel",     # stable identifier for this channel type
    chat_id="user_or_room",   # unique conversation identifier
    user_id="sender",         # who sent the message
    text="raw message text",
))
```

The `chat_id` becomes part of the **scope** — the key used for conversation history, scratchpad, context, and all per-chat state. Use `scoped_chat_id("my_channel", actual_id)` from `nanobot.core_utils` to namespace it, e.g. `"my_channel:abc123"`. This prevents collisions between channels.

## Message flow

```
Channel receives input
  → self.emit(IncomingMessage)
  → BotCore.on_incoming() wraps as UserMessage
  → asyncio.Queue
  → _process_queue_loop dequeues
  → _handle_user_message()
    → CommandManager (if slash command)
    → _process() (if regular message)
      → SubagentManager.spawn/execute/AgentRun
      → LLM + tools
      → reply via channel.send(chat_id, text)
  → _evaluate_turn() (evaluator, if enabled)
```

Key points:
- All channels share the same queue, so messages are processed serially within a chat scope
- Slash commands bypass the subagent system entirely
- `on_incoming` is async and returns immediately after enqueueing

## Existing channels

### TelegramChannel

The simplest concrete example. Event-driven via `python-telegram-bot`.

```python
class TelegramChannel(Channel):
    def __init__(self, token: str) -> None: ...
    async def start(self) -> None:
        # Build Application, add MessageHandler, start polling
    async def stop(self) -> None:
        # Stop updater, shutdown app
    async def send(self, chat_id: str, text: str) -> None:
        # Normalize text (strip HTML, flatten tables), chunk at 3900 chars, send_message
```

Receives `Update` objects from Telegram, extracts `chat.id`, `user.id`, `text`, and calls `self.emit()`. The `chat_id` is the raw Telegram chat ID (integer as string) — the scope becomes `"telegram:123456"`.

### GithubChannel

Polling-based. Periodically checks GitHub issues for new work matching the configured trigger (assignment, label).

```python
class GithubChannel(Channel):
    def __init__(self, token, bot_username, repo_owner, repo_name, ...): ...
    async def start(self) -> None:
        # Create PyGithub client, start _poll_loop task
    async def stop(self) -> None:
        # Set stop event, cancel poll task
    async def send(self, chat_id: str, text: str) -> None:
        # Post comment on GitHub issue; chat_id format: "github:owner/repo#42"
```

Can optionally send notifications through a linked Telegram channel when it creates comments.

### FileChannel

Reads from JSONL files, writes replies to JSONL files. Used for testing, debugging, and programmatic interaction via the [bot-conversation skill](.agents/skills/bot-conversation/SKILL.md).

```python
class FileChannel(Channel):
    def __init__(self, sessions_dir, session_id, capture_tool_calls, poll_interval, user_id): ...
    async def start(self) -> None:
        # Create in/out dirs, write session_start event, start _poll_input task
    async def stop(self) -> None:
        # Cancel poll task, write session_end event
    async def send(self, chat_id: str, text: str) -> None:
        # Write assistant_message + turn_complete events to output JSONL
```

Extra methods for testing:
- `inject(text)` — programmatically inject a message
- `wait_for_response(timeout)` — block until the bot replies

When `capture_tool_calls=True`, the `FileTraceHook` writes tool call/result events to the output JSONL alongside assistant messages.

## Adding a new channel

1. **Create** `src/nanobot/channels/your_channel.py`:

```python
from __future__ import annotations

from nanobot.channels.base import Channel, IncomingMessage
from nanobot.core_utils import scoped_chat_id


class YourChannel(Channel):
    def __init__(self, api_key: str) -> None:
        super().__init__()
        self.api_key = api_key

    async def start(self) -> None:
        # Connect to your service, start listening
        # When a message arrives:
        #   await self.emit(IncomingMessage(
        #       channel="your_channel",
        #       chat_id=scoped_chat_id("your_channel", user_id),
        #       user_id=str(sender_id),
        #       text=message_text,
        #   ))
        ...

    async def stop(self) -> None:
        # Disconnect, clean up
        ...

    async def send(self, chat_id: str, text: str) -> None:
        # Send reply back to user via your service's API
        ...
```

2. **Export** from `src/nanobot/channels/__init__.py`:

```python
from nanobot.channels.your_channel import YourChannel
```

3. **Wire up** in `src/nanobot/main.py` — add a block in `build_channels()`:

```python
if cfg.type == "your_channel":
    channels["your_channel"] = YourChannel(cfg.token)
    continue
```

4. **Add config** in `config.yaml`:

```yaml
channels:
  - type: your_channel
    token: "your-api-key"
```

5. **Add config model** in `src/nanobot/config.py` if your channel needs more options (see `ChannelConfig` dataclass).

That's it. The `Channel.set_handler(core.on_incoming)` wiring and the message queue happen automatically in `main.py`'s `run()` function — no changes needed there.

## Startup and shutdown

```python
# main.py: run()
config = load_config(config_path)
channels, extra_hooks = build_channels(config)
core = BotCore(config, channels)

for ch in channels.values():
    ch.set_handler(core.on_incoming)  # Wire handler

await core.start()                    # Start MCP, scheduler, queue

for hook in extra_hooks:              # Register hooks (e.g. FileTraceHook)
    core.tool_hooks.append(hook)

for ch in channels.values():
    await ch.start()                  # Begin listening

# ... run until shutdown signal ...

for ch in channels.values():
    await ch.stop()                   # Stop channels first
await core.stop()                     # Then stop core
```

Channels start last and stop first. The core must be running before channels start emitting messages.