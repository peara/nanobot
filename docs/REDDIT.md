# Reddit

OAuth-based Reddit integration via asyncpraw.

## Overview

The Reddit MCP server provides read-only access to Reddit posts, subreddits, and search via the official Reddit API (asyncpraw). It authenticates using a permanent OAuth refresh token obtained through an interactive bootstrap CLI. The server is optional. It only starts when `PRAW_CLIENT_ID`, `PRAW_CLIENT_SECRET`, and `PRAW_REFRESH_TOKEN` are configured. See [MCP_SERVERS.md](MCP_SERVERS.md) for the general pattern.

## Prerequisites

### Reddit app registration

1. Go to https://www.reddit.com/prefs/apps
2. Click "create another app..."
3. Choose "script" as the app type
4. Fill in a name and set the redirect URI to `http://localhost:8080`
5. Note the `client_id` (under the app name) and `client_secret`

The "script" app type is designed for single-user personal use. It is not suitable for multi-user deployment.

### Install asyncpraw

`asyncpraw` is not included in `pyproject.toml` due to an `aiosqlite` dependency conflict with `crawl4ai` on Python 3.14+. Install it separately:

```bash
uv pip install asyncpraw
```

**Without asyncpraw installed, both the MCP server and the bootstrap CLI will fail.**

## Setup

### Bootstrapping credentials

The `external_tokens` CLI walks through the OAuth flow and writes the resulting credentials to `.env` and `config.override.yaml`:

```bash
python -m nanobot.external_tokens.cli reddit \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET
```

What happens during bootstrap:

1. The CLI generates an OAuth authorization URL
2. The user opens the URL in a browser and authorizes the app
3. Reddit redirects to `localhost`, where the CLI captures the authorization code
4. The CLI exchanges the code for a permanent refresh token
5. `.env` and `config.override.yaml` are written automatically

### CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `--client-id` | (required) | Reddit app client ID |
| `--client-secret` | (required) | Reddit app client secret |
| `--redirect-port` | `8080` | Local port for OAuth redirect callback |
| `--scopes` | `identity,read,submit,edit,privatemessages,history` | OAuth scopes (comma-separated) |
| `--user-agent` | `nanobot/1.0 by u/YOUR_USERNAME` | Reddit API user agent string |
| `--config` | `config.yaml` | Path to config YAML (used to locate `.env` and override file) |

### What gets written

The bootstrap writes two files next to the config YAML:

**`.env`** (in the config directory):
```
PRAW_CLIENT_ID=your_client_id
PRAW_CLIENT_SECRET=your_client_secret
PRAW_REFRESH_TOKEN=your_refresh_token
```

**`config.override.yaml`**:
```yaml
mcp_servers:
  - name: reddit
    command: python
    args:
      - -m
      - nanobot.mcp_servers.reddit.server
    required_env:
      - PRAW_CLIENT_ID
      - PRAW_CLIENT_SECRET
      - PRAW_REFRESH_TOKEN
    env:
      PRAW_CLIENT_ID: ${PRAW_CLIENT_ID}
      PRAW_CLIENT_SECRET: ${PRAW_CLIENT_SECRET}
      PRAW_REFRESH_TOKEN: ${PRAW_REFRESH_TOKEN}
```

The `required_env` field tells nanobot to skip this server if any listed variable is missing, preventing startup errors when Reddit is not configured.

### Manual setup

As an alternative to the bootstrap CLI, add the server entry and environment variables manually.

Add to `config.override.yaml`:
```yaml
mcp_servers:
  - name: reddit
    command: python
    args: ["-m", "nanobot.mcp_servers.reddit.server"]
    required_env:
      - PRAW_CLIENT_ID
      - PRAW_CLIENT_SECRET
      - PRAW_REFRESH_TOKEN
    env:
      PRAW_CLIENT_ID: "${PRAW_CLIENT_ID}"
      PRAW_CLIENT_SECRET: "${PRAW_CLIENT_SECRET}"
      PRAW_REFRESH_TOKEN: "${PRAW_REFRESH_TOKEN}"
```

Set environment variables (e.g. in `.env` or shell):
```bash
export PRAW_CLIENT_ID="your_client_id"
export PRAW_CLIENT_SECRET="your_client_secret"
export PRAW_REFRESH_TOKEN="your_refresh_token"
```

## Configuration

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PRAW_CLIENT_ID` | Yes | Reddit app client ID |
| `PRAW_CLIENT_SECRET` | Yes | Reddit app client secret |
| `PRAW_REFRESH_TOKEN` | Yes | OAuth refresh token (obtained via CLI or manual flow) |
| `PRAW_USER_AGENT` | No | User agent string (default: `nanobot-reddit/1.0`) |

### Config entry

The server is configured as an `mcp_servers` entry. The `required_env` list ensures nanobot skips the server gracefully if credentials are absent:

```yaml
mcp_servers:
  - name: reddit
    command: python
    args: ["-m", "nanobot.mcp_servers.reddit.server"]
    required_env:
      - PRAW_CLIENT_ID
      - PRAW_CLIENT_SECRET
      - PRAW_REFRESH_TOKEN
    env:
      PRAW_CLIENT_ID: "${PRAW_CLIENT_ID}"
      PRAW_CLIENT_SECRET: "${PRAW_CLIENT_SECRET}"
      PRAW_REFRESH_TOKEN: "${PRAW_REFRESH_TOKEN}"
```

## MCP Tools

### Tool reference

| Tool | Purpose |
|------|---------|
| `reddit_health` | Check env var presence and credential status |
| `reddit_get_subreddit` | Get subreddit metadata (name, description, subscribers) |
| `reddit_get_posts` | Get posts from a subreddit (hot/new/top/rising) |
| `reddit_get_post` | Get a single post with its top comments |
| `reddit_search` | Search Reddit for posts matching a query |

### reddit_get_posts

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `subreddit` | str | (required) | Subreddit name without /r/ prefix |
| `sort` | str | `hot` | Sort method: `hot`, `new`, `top`, `rising` |
| `limit` | int | `10` | Number of posts to return (max 25) |
| `time_filter` | str | `week` | Time window for `top` sort: `hour`, `day`, `week`, `month`, `year`, `all` |

The `time_filter` parameter only affects results when `sort=top`. Each post in the response contains: `id`, `title`, `body`, `author`, `score`, `num_comments`, `created_utc`, `permalink`, `url`, `is_self`, `flair`, `over_18`, `stickied`.

Example response structure:
```json
{
  "ok": true,
  "subreddit": "python",
  "sort": "hot",
  "posts": [
    {
      "id": "abc123",
      "title": "Post title",
      "body": "Post self-text (truncated to 500 chars) [truncated]",
      "author": "username",
      "score": 42,
      "num_comments": 7,
      "created_utc": "2026-05-22T10:30:00+00:00",
      "permalink": "https://reddit.com/r/python/comments/abc123/post_title/",
      "url": "https://example.com",
      "is_self": false,
      "flair": "News",
      "over_18": false,
      "stickied": false
    }
  ]
}
```

### reddit_get_post

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `post_id` | str | (required) | Reddit post ID (from the permalink, e.g. `abc123`) |
| `comment_limit` | int | `10` | Number of top-level comments (max 25) |

Comments are truncated to 300 characters. Post body is truncated to 500 characters. The `replace_more` call collapses "load more" placeholders, so only directly available top-level comments are returned.

### reddit_search

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | (required) | Search query string |
| `subreddit` | str or null | `null` | Subreddit to search within (null = search all of Reddit) |
| `sort` | str | `relevance` | Sort method: `relevance`, `hot`, `top`, `new`, `comments` |
| `time_filter` | str | `week` | Time window: `hour`, `day`, `week`, `month`, `year`, `all` |
| `limit` | int | `10` | Number of results (max 25) |

### reddit_get_subreddit

Returns: `id`, `name`, `title`, `description` (truncated to 500 chars), `description_long` (truncated to 1000 chars), `subscribers`, `active_user_count`, `over18`, `created_utc`, `url`.

### reddit_health

Returns: `has_client_id`, `has_client_secret`, `has_refresh_token`, `user_agent`. Useful for diagnosing missing credentials without making an API call.

## Error handling

All tools return a dict with `"ok": True` on success or `"ok": False` on failure. Error responses include `"error"` (machine-readable key) and `"message"` (human-readable description).

| Error | When |
|-------|------|
| `not_found` | Subreddit or post does not exist |
| `forbidden` | Subreddit is private or quarantined |
| `invalid_sort` | Sort parameter not in the allowed set |
| `invalid_time_filter` | `time_filter` not in the allowed set |
| `api_error` | Network error, rate limit, or other Reddit API failure |

asyncpraw automatically handles Reddit rate limits for write requests (retries up to `ratelimit_seconds`). Read-only requests operate within the standard 100 requests/minute allowance.

## Limitations

- Read-only access. No posting, commenting, or voting is available through the current tools.
- Post bodies are truncated to 500 characters, comments to 300 characters.
- Maximum 25 posts per request, 25 comments per post.
- asyncpraw conflicts with crawl4ai on aiosqlite for Python 3.14+, requiring a separate install.
- The "script" OAuth app type is designed for single-user/personal use, not multi-user deployment.
- Reddit blocks requests at the IP level from datacenter IPs, returning 403 on web requests. PRAW authenticates via the API and is not subject to these blocks.

## See also

- [MCP_SERVERS.md](MCP_SERVERS.md) — general MCP server configuration and the override pattern
- [asyncpraw docs](https://asyncpraw.readthedocs.io/) — asyncpraw API reference
- [Reddit app registration](https://www.reddit.com/prefs/apps) — create a Reddit app