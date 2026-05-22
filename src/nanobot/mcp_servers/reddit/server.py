from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpraw
import asyncprawcore.exceptions
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("nanobot-reddit")

_VALID_SORTS = {"hot", "new", "top", "rising"}
_VALID_TIME_FILTERS = {"hour", "day", "week", "month", "year", "all"}
_VALID_SEARCH_SORTS = {"relevance", "hot", "top", "new", "comments"}
_MAX_POST_LIMIT = 25
_MAX_COMMENT_LIMIT = 25

_reddit: asyncpraw.Reddit | None = None


def _truncate(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + " [truncated]"


def _reddit_client() -> asyncpraw.Reddit:
    """Lazily create and cache the asyncpraw Reddit client from env vars."""
    global _reddit  # pylint: disable=global-statement
    if _reddit is None:
        client_id = os.environ.get("PRAW_CLIENT_ID", "")
        client_secret = os.environ.get("PRAW_CLIENT_SECRET", "")
        refresh_token = os.environ.get("PRAW_REFRESH_TOKEN", "")
        user_agent = os.environ.get("PRAW_USER_AGENT", "nanobot-reddit/1.0")
        if not client_id or not client_secret:
            raise ValueError("PRAW_CLIENT_ID and PRAW_CLIENT_SECRET env vars required")
        _reddit = asyncpraw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            user_agent=user_agent,
        )
    return _reddit


def _submission_to_dict(submission: asyncpraw.models.Submission) -> dict[str, Any]:
    return {
        "id": submission.id,
        "title": submission.title,
        "body": _truncate(submission.selftext or ""),
        "author": str(submission.author) if submission.author else "[deleted]",
        "score": submission.score,
        "num_comments": submission.num_comments,
        "created_utc": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc).isoformat(),
        "permalink": f"https://reddit.com{submission.permalink}",
        "url": submission.url,
        "is_self": submission.is_self,
        "flair": submission.link_flair_text,
        "over_18": submission.over_18,
        "stickied": submission.stickied,
    }


@mcp.tool()
def reddit_health() -> dict[str, Any]:
    """Check Reddit API connectivity and credential status."""
    return {
        "ok": True,
        "has_client_id": bool(os.environ.get("PRAW_CLIENT_ID")),
        "has_client_secret": bool(os.environ.get("PRAW_CLIENT_SECRET")),
        "has_refresh_token": bool(os.environ.get("PRAW_REFRESH_TOKEN")),
        "user_agent": os.environ.get("PRAW_USER_AGENT", "nanobot-reddit/1.0"),
    }


@mcp.tool()
async def reddit_get_subreddit(subreddit: str) -> dict[str, Any]:
    """Get subreddit info: name, title, description, subscribers, active users, etc."""
    try:
        reddit = _reddit_client()
        sub = await reddit.subreddit(subreddit)
        await sub.load()
        return {
            "ok": True,
            "id": sub.id,
            "name": sub.display_name,
            "title": sub.title,
            "description": _truncate(sub.public_description or "", 500),
            "description_long": _truncate(sub.description or "", 1000),
            "subscribers": sub.subscribers,
            "active_user_count": sub.active_user_count,
            "over18": sub.over18,
            "created_utc": datetime.fromtimestamp(sub.created_utc, tz=timezone.utc).isoformat(),
            "url": f"https://reddit.com/r/{sub.display_name}",
        }
    except asyncprawcore.exceptions.NotFound:
        return {"ok": False, "error": "not_found", "message": f"Subreddit r/{subreddit} not found"}
    except asyncprawcore.exceptions.Forbidden:
        return {"ok": False, "error": "forbidden", "message": f"Subreddit r/{subreddit} is private or quarantined"}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("reddit_get_subreddit failed subreddit=%s", subreddit)
        return {"ok": False, "error": "api_error", "message": str(exc)}


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
        return {
            "ok": False,
            "error": "invalid_sort",
            "message": f"Invalid sort '{sort}'. Must be one of: {', '.join(sorted(_VALID_SORTS))}",
        }
    if time_filter not in _VALID_TIME_FILTERS:
        return {
            "ok": False,
            "error": "invalid_time_filter",
            "message": f"Invalid time_filter '{time_filter}'. Must be one of: {', '.join(sorted(_VALID_TIME_FILTERS))}",
        }
    limit = min(limit, _MAX_POST_LIMIT)

    try:
        reddit = _reddit_client()
        sub = await reddit.subreddit(subreddit)
        listing = getattr(sub, sort)(limit=limit, time_filter=time_filter)
        posts = []
        async for submission in listing:
            posts.append(_submission_to_dict(submission))
        return {"ok": True, "subreddit": subreddit, "sort": sort, "posts": posts}
    except asyncprawcore.exceptions.NotFound:
        return {"ok": False, "error": "not_found", "message": f"Subreddit r/{subreddit} not found"}
    except asyncprawcore.exceptions.Forbidden:
        return {"ok": False, "error": "forbidden", "message": f"Subreddit r/{subreddit} is private or quarantined"}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("reddit_get_posts failed subreddit=%s", subreddit)
        return {"ok": False, "error": "api_error", "message": str(exc)}


@mcp.tool()
async def reddit_get_post(post_id: str, comment_limit: int = 10) -> dict[str, Any]:
    """Get a single post with its top comments.

    Args:
        post_id: Reddit post ID (e.g. 'abc123' from the permalink).
        comment_limit: Number of top-level comments to return (max 25). Default: 10.
    """
    comment_limit = min(comment_limit, _MAX_COMMENT_LIMIT)

    try:
        reddit = _reddit_client()
        submission = await reddit.submission(post_id)
        await submission.load()
        await submission.comments.replace_more(limit=0)
        comments = []
        for comment in submission.comments[:comment_limit]:
            comments.append(
                {
                    "id": comment.id,
                    "author": str(comment.author) if comment.author else "[deleted]",
                    "body": _truncate(comment.body or "", 300),
                    "score": comment.score,
                    "created_utc": datetime.fromtimestamp(comment.created_utc, tz=timezone.utc).isoformat(),
                }
            )
        result = _submission_to_dict(submission)
        result["top_comments"] = comments
        return {"ok": True, **result}
    except asyncprawcore.exceptions.NotFound:
        return {"ok": False, "error": "not_found", "message": f"Post {post_id} not found"}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("reddit_get_post failed post_id=%s", post_id)
        return {"ok": False, "error": "api_error", "message": str(exc)}


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
        return {
            "ok": False,
            "error": "invalid_sort",
            "message": f"Invalid sort '{sort}'. Must be one of: {', '.join(sorted(_VALID_SEARCH_SORTS))}",
        }
    if time_filter not in _VALID_TIME_FILTERS:
        return {
            "ok": False,
            "error": "invalid_time_filter",
            "message": f"Invalid time_filter '{time_filter}'. Must be one of: {', '.join(sorted(_VALID_TIME_FILTERS))}",
        }
    limit = min(limit, _MAX_POST_LIMIT)

    try:
        reddit = _reddit_client()
        if subreddit:
            search_target = await reddit.subreddit(subreddit)
        else:
            search_target = await reddit.subreddit("all")
        posts = []
        async for submission in search_target.search(query, sort=sort, time_filter=time_filter, limit=limit):
            posts.append(_submission_to_dict(submission))
        return {"ok": True, "query": query, "subreddit": subreddit, "posts": posts}
    except asyncprawcore.exceptions.NotFound:
        return {"ok": False, "error": "not_found", "message": f"Subreddit r/{subreddit} not found"}
    except asyncprawcore.exceptions.Forbidden:
        return {"ok": False, "error": "forbidden", "message": f"Subreddit r/{subreddit} is private or quarantined"}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("reddit_search failed query=%s subreddit=%s", query, subreddit)
        return {"ok": False, "error": "api_error", "message": str(exc)}


if __name__ == "__main__":
    mcp.run()
