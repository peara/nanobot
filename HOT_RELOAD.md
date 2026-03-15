# Hot-Reload Feature

The nanobot agent now supports hot-reloading via Telegram commands. This allows you to reload configuration, MCP servers, or the entire agent without manually restarting.

## Setup

1. Set your Telegram chat ID in `config.yaml`:
   ```yaml
   owner_chat_id: 123456789  # Replace with your actual chat ID
   ```

2. Get your Telegram chat ID by messaging @userinfobot on Telegram.

## Commands

### Reload Everything (Full Hot-Reload)
```/reload```
Restarts all components including LLM client, MCP servers, and scheduler.

**Dry-run mode (validate without applying):**
```/reload --dry-run```
or
```/reload -d```

### Reload Configuration Only
```/reload config```
Reloads settings from `config.yaml` only.

**Dry-run:**
```/reload config --dry-run```

### Reload MCP Servers Only
```/reload mcp```
Restarts all MCP server processes (timer, scheduler, memory, playwright).

## Usage Examples

1. Edit your `config.yaml` to change settings
2. Send `/reload config` in Telegram
3. Bot will respond with success/failure message

For MCP server code changes:
1. Edit the relevant MCP server file
2. Send `/reload mcp`
3. Servers will restart with updated code

## Security Notes

- Only users whose chat ID matches `owner_chat_id` can execute reload commands
- If `owner_chat_id` is set to 0 (default), the `/reload` command is disabled
- The bot will reject reload attempts from unauthorized chats

## State Preservation

During reload, conversation history and context are preserved in SQLite databases. Only runtime state (in-memory) is reset.
