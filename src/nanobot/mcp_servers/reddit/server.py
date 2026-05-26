from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("nanobot-reddit")

_VALID_SORTS = {"hot", "new", "top", "rising"}
_VALID_TIME_FILTERS = {"hour", "day", "week", "month", "year", "all"}
_VALID_SEARCH_SORTS = {"relevance", "hot", "top", "new", "comments"}
_MAX_POST_LIMIT = 25
_MAX_COMMENT_LIMIT = 25
_MAX_RETRIES = 3
_RETRY_DELAYS = (1, 2, 4)  # exponential backoff in seconds

_client: httpx.AsyncClient | None = None
_rate_limit_remaining: int | None = None
_rate_limit_reset: int | None = None


def _default_user_agent() -> str:
    return os.environ.get("REDDIT_USER_AGENT", "nanobot-reddit/1.0 (by /u/nanobot)")


def _get_client() -> httpx.AsyncClient:
    """Lazily create and cache the httpx AsyncClient singleton."""
    global _client  # pylint: disable=global-statement
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url="https://www.reddit.com",
            headers={
                "User-Agent": _default_user_agent(),
                "Accept": "application/json",
            },
            timeout=15.0,
            follow_redirects=True,
        )
    return _client


def _truncate(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + " [truncated]"


def _update_rate_limits(headers: httpx.Headers) -> None:
    """Parse rate limit headers from Reddit response."""
    global _rate_limit_remaining, _rate_limit_reset  # pylint: disable=global-statement
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    _rate_limit_remaining = int(float(remaining)) if remaining is not None else None
    _rate_limit_reset = int(float(reset)) if reset is not None else None


def _parse_post(data: dict[str, Any]) -> dict[str, Any]:
    """Extract post fields from a t3 data dict."""
    author = data.get("author")
    if author is None or author == "[deleted]":
        author = "[deleted]"
    created_utc = data.get("created_utc", 0.0)
    permalink = data.get("permalink", "")
    if permalink and not permalink.startswith("http"):
        permalink = f"https://reddit.com{permalink}"
    return {
        "id": data.get("id", ""),
        "title": data.get("title", ""),
        "body": _truncate(data.get("selftext", "") or "", 500),
        "author": author,
        "score": data.get("score", 0),
        "num_comments": data.get("num_comments", 0),
        "created_utc": datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat() if created_utc else "",
        "permalink": permalink,
        "url": data.get("url", ""),
        "is_self": data.get("is_self", False),
        "flair": data.get("link_flair_text"),
        "over_18": data.get("over_18", False),
        "stickied": data.get("stickied", False),
    }


def _parse_comment(data: dict[str, Any]) -> dict[str, Any]:
    """Extract comment fields from a t1 data dict."""
    author = data.get("author")
    if author is None or author == "[deleted]":
        author = "[deleted]"
    created_utc = data.get("created_utc", 0.0)
    return {
        "id": data.get("id", ""),
        "author": author,
        "body": _truncate(data.get("body", "") or "", 300),
        "score": data.get("score", 0),
        "created_utc": datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat() if created_utc else "",
    }


def _parse_subreddit(data: dict[str, Any]) -> dict[str, Any]:
    """Extract subreddit fields from a t5 data dict."""
    created_utc = data.get("created_utc", 0.0)
    display_name = data.get("display_name", "")
    return {
        "ok": True,
        "id": data.get("id", ""),
        "name": display_name,
        "title": data.get("title", ""),
        "description": _truncate(data.get("public_description", "") or "", 500),
        "description_long": _truncate(data.get("description", "") or "", 1000),
        "subscribers": data.get("subscribers", 0),
        "active_user_count": data.get("active_user_count"),
        "over18": data.get("over18", False),
        "created_utc": datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat() if created_utc else "",
        "url": f"https://reddit.com/r/{display_name}",
    }


def _error_response(error: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message}


async def _request_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.get(url)
            _update_rate_limits(response.headers)
            if response.status_code == 429:
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "Rate limited (429), retrying in %ds (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                return response
            return response
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning("HTTP error, retrying in %ds (attempt %d/%d): %s", delay, attempt + 1, _MAX_RETRIES, exc)
                await asyncio.sleep(delay)
    assert last_exc is not None, "All retries failed but no exception captured"
    raise last_exc


async def _handle_response(response: httpx.Response) -> dict[str, Any]:
    """Convert an HTTP response to an error dict if it's an error status."""
    if response.status_code == 404:
        return _error_response("not_found", f"Not found (HTTP {response.status_code})")
    if response.status_code == 403:
        return _error_response("forbidden", f"Forbidden (HTTP {response.status_code})")
    if response.status_code >= 400:
        return _error_response("api_error", f"HTTP {response.status_code}: {response.text[:200]}")
    return {}  # no error — caller should check for "ok" key absence


@mcp.tool()
def reddit_health() -> dict[str, Any]:
    """Check Reddit API connectivity and rate limit status."""
    return {
        "ok": True,
        "auth_mode": "anonymous",
        "user_agent": _default_user_agent(),
        "rate_limit_remaining": _rate_limit_remaining,
        "rate_limit_reset": _rate_limit_reset,
    }


@mcp.tool()
async def reddit_get_subreddit(subreddit: str) -> dict[str, Any]:
    """Get subreddit info: name, title, description, subscribers, active users, etc.

    Args:
        subreddit: Subreddit name without /r/ prefix.
    """
    client = _get_client()
    try:
        response = await _request_with_retry(client, f"/r/{subreddit}/about.json")
    except httpx.HTTPError as exc:
        logger.exception("reddit_get_subreddit network error subreddit=%s", subreddit)
        return _error_response("api_error", str(exc))

    err = await _handle_response(response)
    if err:
        err["message"] = f"Subreddit r/{subreddit}: {err['message']}"
        return err

    try:
        payload = response.json()
        data = payload.get("data", payload)
        return _parse_subreddit(data)
    except (ValueError, KeyError) as exc:
        logger.exception("reddit_get_subreddit parse error subreddit=%s", subreddit)
        return _error_response("api_error", f"Failed to parse response: {exc}")


@mcp.tool()
async def reddit_get_posts(
    subreddit: str,
    sort: str = "hot",
    limit: int = 10,
    time_filter: str = "week",
) -> dict[str, Any]:
    """Get posts from a subreddit (hot/new/top/rising).

    Args:
        subreddit: Subreddit name without /r/ prefix.
        sort: Sort method — hot, new, top, or rising. Default: hot.
        limit: Number of posts to return (max 25). Default: 10.
        time_filter: Time filter for top sort — hour, day, week, month, year, all. Default: week.
    """
    if sort not in _VALID_SORTS:
        return _error_response(
            "invalid_sort",
            f"Invalid sort '{sort}'. Must be one of: {', '.join(sorted(_VALID_SORTS))}",
        )
    if time_filter not in _VALID_TIME_FILTERS:
        return _error_response(
            "invalid_time_filter",
            f"Invalid time_filter '{time_filter}'. Must be one of: {', '.join(sorted(_VALID_TIME_FILTERS))}",
        )
    limit = min(limit, _MAX_POST_LIMIT)

    client = _get_client()
    params = f"?limit={limit}&raw_json=1"
    if sort == "top":
        params += f"&t={time_filter}"
    url = f"/r/{subreddit}/{sort}.json{params}"

    try:
        response = await _request_with_retry(client, url)
    except httpx.HTTPError as exc:
        logger.exception("reddit_get_posts network error subreddit=%s", subreddit)
        return _error_response("api_error", str(exc))

    err = await _handle_response(response)
    if err:
        err["message"] = f"Subreddit r/{subreddit}: {err['message']}"
        return err

    try:
        payload = response.json()
        children = payload.get("data", {}).get("children", [])
        posts = [_parse_post(child.get("data", {})) for child in children if child.get("kind") == "t3"]
        return {"ok": True, "subreddit": subreddit, "sort": sort, "posts": posts}
    except (ValueError, KeyError) as exc:
        logger.exception("reddit_get_posts parse error subreddit=%s", subreddit)
        return _error_response("api_error", f"Failed to parse response: {exc}")


@mcp.tool()
async def reddit_get_post(post_id: str, comment_limit: int = 10) -> dict[str, Any]:
    """Get a single post with its top comments.

    Args:
        post_id: Reddit post ID (e.g. 'abc123' from the permalink).
        comment_limit: Number of top-level comments to return (max 25). Default: 10.
    """
    comment_limit = min(comment_limit, _MAX_COMMENT_LIMIT)

    client = _get_client()
    url = f"/comments/{post_id}.json?limit={comment_limit}&raw_json=1"

    try:
        response = await _request_with_retry(client, url)
    except httpx.HTTPError as exc:
        logger.exception("reddit_get_post network error post_id=%s", post_id)
        return _error_response("api_error", str(exc))

    err = await _handle_response(response)
    if err:
        err["message"] = f"Post {post_id}: {err['message']}"
        return err

    try:
        payload = response.json()
        # Comments endpoint returns array of two listings: [post_listing, comments_listing]
        if not isinstance(payload, list) or len(payload) < 2:
            return _error_response("api_error", f"Unexpected response format for post {post_id}")

        post_children = payload[0].get("data", {}).get("children", [])
        if not post_children or post_children[0].get("kind") != "t3":
            return _error_response("api_error", f"Post {post_id} not found in response")

        post_data = _parse_post(post_children[0].get("data", {}))

        comment_children = payload[1].get("data", {}).get("children", [])
        comments = [_parse_comment(child.get("data", {})) for child in comment_children if child.get("kind") == "t1"][
            :comment_limit
        ]

        return {"ok": True, **post_data, "top_comments": comments}
    except (ValueError, KeyError) as exc:
        logger.exception("reddit_get_post parse error post_id=%s", post_id)
        return _error_response("api_error", f"Failed to parse response: {exc}")


@mcp.tool()
async def reddit_search(
    query: str,
    subreddit: str | None = None,
    sort: str = "relevance",
    time_filter: str = "week",
    limit: int = 10,
) -> dict[str, Any]:
    """Search Reddit for posts matching a query.

    Args:
        query: Search query string.
        subreddit: Optional subreddit name to search within (without /r/). None = search all of Reddit.
        sort: Sort method — relevance, hot, top, new, comments. Default: relevance.
        time_filter: Time filter — hour, day, week, month, year, all. Default: week.
        limit: Number of results (max 25). Default: 10.
    """
    if sort not in _VALID_SEARCH_SORTS:
        return _error_response(
            "invalid_sort",
            f"Invalid sort '{sort}'. Must be one of: {', '.join(sorted(_VALID_SEARCH_SORTS))}",
        )
    if time_filter not in _VALID_TIME_FILTERS:
        return _error_response(
            "invalid_time_filter",
            f"Invalid time_filter '{time_filter}'. Must be one of: {', '.join(sorted(_VALID_TIME_FILTERS))}",
        )
    limit = min(limit, _MAX_POST_LIMIT)

    client = _get_client()
    encoded_query = quote_plus(query)

    if subreddit:
        url = (
            f"/r/{subreddit}/search.json"
            f"?q={encoded_query}&restrict_sr=on&sort={sort}"
            f"&t={time_filter}&limit={limit}&raw_json=1"
        )
    else:
        url = f"/search.json?q={encoded_query}&sort={sort}&t={time_filter}&limit={limit}&raw_json=1"

    try:
        response = await _request_with_retry(client, url)
    except httpx.HTTPError as exc:
        logger.exception("reddit_search network error query=%s", query)
        return _error_response("api_error", str(exc))

    err = await _handle_response(response)
    if err:
        label = f"r/{subreddit}" if subreddit else "all"
        err["message"] = f"Search on {label}: {err['message']}"
        return err

    try:
        payload = response.json()
        children = payload.get("data", {}).get("children", [])
        posts = [_parse_post(child.get("data", {})) for child in children if child.get("kind") == "t3"]
        return {"ok": True, "query": query, "subreddit": subreddit, "posts": posts}
    except (ValueError, KeyError) as exc:
        logger.exception("reddit_search parse error query=%s", query)
        return _error_response("api_error", f"Failed to parse response: {exc}")


if __name__ == "__main__":
    mcp.run()
