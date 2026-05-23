# Reddit

Read-only Reddit data access via public JSON endpoints.

## Overview

The Reddit MCP server provides read-only access to Reddit posts, subreddits, and search using Reddit's public `.json` endpoints. **No authentication is required** — the server works out of the box with zero configuration.

### Why no OAuth?

Reddit's **Responsible Builder Policy** (November 2025) ended self-service API key creation. Obtaining OAuth credentials now requires an approval gate that usually declines personal scripts. The nanobot Reddit server avoids this entirely by using Reddit's public JSON endpoints, which return the same data without authentication.

**Rate limits**: Unauthenticated requests are rate-limited to approximately 10–60 requests per minute (varies by endpoint and User-Agent). This is sufficient for personal bot use.

## Quick start

Add to `config.yaml` (or `config.override.yaml`):

```yaml
mcp_servers:
  - name: "reddit"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.reddit.server"]
    env: {}
```

Start the bot. The Reddit server starts with no additional setup.

## Configuration

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDDIT_USER_AGENT` | No | `nanobot-reddit/1.0 (by /u/nanobot)` | Custom User-Agent string for Reddit API requests |

Setting a descriptive User-Agent with your Reddit username is recommended by Reddit's API guidelines. Example:

```
REDDIT_USER_AGENT=mybot/2.0 (by /u/myusername)
```

### Custom User-Agent in config.override.yaml

```yaml
mcp_servers:
  - name: "reddit"
    command: "python"
    args: ["-m", "nanobot.mcp_servers.reddit.server"]
    env:
      REDDIT_USER_AGENT: "mybot/2.0 (by /u/myusername)"
```

## MCP Tools

### Tool reference

| Tool | Purpose |
|------|---------|
| `reddit_health` | Check connectivity and rate limit status |
| `reddit_get_subreddit` | Get subreddit metadata (name, description, subscribers) |
| `reddit_get_posts` | Get posts from a subreddit (hot/new/top/rising) |
| `reddit_get_post` | Get a single post with its top comments |
| `reddit_search` | Search Reddit for posts matching a query |

### reddit_health

Returns `auth_mode`, `user_agent`, and rate limit info from the last request.

Example response:
```json
{
  "ok": true,
  "auth_mode": "anonymous",
  "user_agent": "nanobot-reddit/1.0 (by /u/nanobot)",
  "rate_limit_remaining": 58,
  "rate_limit_reset": 120
}
```

### reddit_get_posts

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `subreddit` | str | (required) | Subreddit name without /r/ prefix |
| `sort` | str | `hot` | Sort method: `hot`, `new`, `top`, `rising` |
| `limit` | int | `10` | Number of posts to return (max 25) |
| `time_filter` | str | `week` | Time window for `top` sort: `hour`, `day`, `week`, `month`, `year`, `all` |

The `time_filter` parameter only affects results when `sort=top`. Each post in the response contains: `id`, `title`, `body`, `author`, `score`, `num_comments`, `created_utc`, `permalink`, `url`, `is_self`, `flair`, `over_18`, `stickied`.

Example response:
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

Comments are truncated to 300 characters. Post body is truncated to 500 characters.

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

## Error handling

All tools return a dict with `"ok": True` on success or `"ok": False` on failure. Error responses include `"error"` (machine-readable key) and `"message"` (human-readable description).

| Error | When |
|-------|------|
| `not_found` | Subreddit or post does not exist |
| `forbidden` | Subreddit is private or quarantined |
| `invalid_sort` | Sort parameter not in the allowed set |
| `invalid_time_filter` | `time_filter` not in the allowed set |
| `rate_limited` | Reddit returned 429 after retries (exponential backoff) |
| `api_error` | Network error or other Reddit API failure |

The server automatically retries on 429 responses with exponential backoff (3 attempts: 1s, 2s, 4s delays).

## Limitations

- **Read-only**: No posting, commenting, or voting.
- **Rate limits**: Approximately 10–60 requests/minute unauthenticated. The server tracks `x-ratelimit-remaining` and `x-ratelimit-reset` headers.
- **Data truncation**: Post bodies truncated to 500 characters, comments to 300 characters, descriptions to 500/1000 characters.
- **Request limits**: Maximum 25 posts per request, 25 comments per post.
- **Datacenter IPs**: Reddit may block requests from datacenter IP ranges. If you encounter 403 responses from a server environment, set a descriptive User-Agent and consider routing through a residential proxy.

## Deprecated: OAuth bootstrapping

The `external_tokens` package and its `reddit` OAuth command have been **removed**. Reddit's Responsible Builder Policy (November 2025) blocks new script app creation, making the OAuth flow unusable for new users.

The Reddit MCP server now uses public JSON endpoints with no authentication. No bootstrapping is needed — add the server to your config and it works immediately.

**Do not attempt** to create a new Reddit "script" app at https://www.reddit.com/prefs/apps — the approval form requires a Devvit app or research intent that does not accommodate personal bot use cases.

## See also

- [MCP_SERVERS.md](MCP_SERVERS.md) — general MCP server configuration and the override pattern
- [Reddit Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564) — official policy documentation