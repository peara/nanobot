from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nanobot.mcp_servers.reddit import server


def _make_headers(rate_remaining: str | None = "100", rate_reset: str | None = "600") -> dict[str, str]:
    headers: dict[str, str] = {}
    if rate_remaining is not None:
        headers["x-ratelimit-remaining"] = rate_remaining
    if rate_reset is not None:
        headers["x-ratelimit-reset"] = rate_reset
    return headers


def _make_response(
    status_code: int = 200,
    json_data: Any = None,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock(spec=[])
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = text
    resp.headers = _make_headers() if headers is None else headers
    return resp


def _make_post_data(**overrides: Any) -> dict[str, Any]:
    defaults = {
        "id": "abc123",
        "title": "Test Post",
        "selftext": "Post body",
        "author": "testuser",
        "score": 42,
        "num_comments": 10,
        "created_utc": 1700000000.0,
        "permalink": "/r/python/comments/abc123/test_post/",
        "url": "https://example.com",
        "is_self": True,
        "link_flair_text": "Discussion",
        "over_18": False,
        "stickied": False,
    }
    defaults.update(overrides)
    return defaults


def _make_listing(children: list[dict[str, Any]], after: str | None = None) -> dict[str, Any]:
    return {"kind": "Listing", "data": {"children": children, "after": after}}


def _make_subreddit_data(**overrides: Any) -> dict[str, Any]:
    defaults = {
        "id": "2qhg4",
        "display_name": "python",
        "title": "Python",
        "public_description": "News about Python",
        "description": "Detailed description",
        "subscribers": 2000000,
        "active_user_count": 5000,
        "over18": False,
        "created_utc": 1160693407.0,
    }
    defaults.update(overrides)
    return defaults


class TestTruncate:
    def test_short_text_unchanged(self) -> None:
        assert server._truncate("hello", 500) == "hello"

    def test_exact_limit_unchanged(self) -> None:
        text = "a" * 500
        assert server._truncate(text, 500) == text

    def test_long_text_truncated(self) -> None:
        text = "a" * 600
        result = server._truncate(text, 500)
        assert result == "a" * 500 + " [truncated]"

    def test_custom_limit(self) -> None:
        text = "a" * 100
        result = server._truncate(text, 50)
        assert result == "a" * 50 + " [truncated]"

    def test_empty_string(self) -> None:
        assert server._truncate("", 500) == ""


class TestParsePost:
    def test_basic_fields(self) -> None:
        data = _make_post_data()
        result = server._parse_post(data)
        assert result["id"] == "abc123"
        assert result["title"] == "Test Post"
        assert result["body"] == "Post body"
        assert result["author"] == "testuser"
        assert result["score"] == 42
        assert result["num_comments"] == 10
        assert result["url"] == "https://example.com"
        assert result["is_self"] is True
        assert result["flair"] == "Discussion"
        assert result["over_18"] is False
        assert result["stickied"] is False

    def test_permalink_prefixed(self) -> None:
        data = _make_post_data(permalink="/r/python/comments/abc123/test_post/")
        result = server._parse_post(data)
        assert result["permalink"] == "https://reddit.com/r/python/comments/abc123/test_post/"

    def test_deleted_author(self) -> None:
        data = _make_post_data(author=None)
        result = server._parse_post(data)
        assert result["author"] == "[deleted]"

    def test_created_utc_iso_format(self) -> None:
        data = _make_post_data(created_utc=1700000000.0)
        result = server._parse_post(data)
        expected = datetime.fromtimestamp(1700000000.0, tz=timezone.utc).isoformat()
        assert result["created_utc"] == expected

    def test_long_selftext_truncated(self) -> None:
        data = _make_post_data(selftext="x" * 600)
        result = server._parse_post(data)
        assert result["body"].endswith("[truncated]")
        assert len(result["body"]) < 600

    def test_empty_selftext(self) -> None:
        data = _make_post_data(selftext="")
        result = server._parse_post(data)
        assert result["body"] == ""

    def test_none_selftext(self) -> None:
        data = _make_post_data(selftext=None)
        result = server._parse_post(data)
        assert result["body"] == ""

    def test_none_flair(self) -> None:
        data = _make_post_data(link_flair_text=None)
        result = server._parse_post(data)
        assert result["flair"] is None


class TestParseComment:
    def test_basic_fields(self) -> None:
        data = {"id": "c1", "author": "commenter", "body": "Great post!", "score": 15, "created_utc": 1700000100.0}
        result = server._parse_comment(data)
        assert result["id"] == "c1"
        assert result["author"] == "commenter"
        assert result["body"] == "Great post!"
        assert result["score"] == 15

    def test_deleted_author(self) -> None:
        data = {"id": "c1", "author": None, "body": "removed", "score": 0, "created_utc": 1700000100.0}
        result = server._parse_comment(data)
        assert result["author"] == "[deleted]"

    def test_long_body_truncated(self) -> None:
        data = {"id": "c1", "author": "u", "body": "a" * 500, "score": 1, "created_utc": 1700000100.0}
        result = server._parse_comment(data)
        assert result["body"].endswith("[truncated]")
        assert len(result["body"]) < 500


class TestParseSubreddit:
    def test_basic_fields(self) -> None:
        data = _make_subreddit_data()
        result = server._parse_subreddit(data)
        assert result["ok"] is True
        assert result["name"] == "python"
        assert result["title"] == "Python"
        assert result["subscribers"] == 2000000
        assert result["url"] == "https://reddit.com/r/python"

    def test_truncated_descriptions(self) -> None:
        data = _make_subreddit_data(public_description="a" * 600, description="b" * 1200)
        result = server._parse_subreddit(data)
        assert result["description"].endswith("[truncated]")
        assert result["description_long"].endswith("[truncated]")


class TestRedditHealth:
    def test_returns_anonymous_mode(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = server.reddit_health()
        assert result["ok"] is True
        assert result["auth_mode"] == "anonymous"
        assert "user_agent" in result
        assert "rate_limit_remaining" in result
        assert "rate_limit_reset" in result

    def test_custom_user_agent(self) -> None:
        with patch.dict("os.environ", {"REDDIT_USER_AGENT": "custom/2.0"}, clear=False):
            result = server.reddit_health()
        assert result["user_agent"] == "custom/2.0"

    def test_default_user_agent(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = server.reddit_health()
        assert result["user_agent"] == "nanobot-reddit/1.0 (by /u/nanobot)"


@pytest.mark.asyncio
class TestRedditGetSubreddit:
    async def test_successful_fetch(self) -> None:
        sub_data = _make_subreddit_data()
        response = _make_response(json_data={"kind": "t5", "data": sub_data})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_subreddit("python")

        assert result["ok"] is True
        assert result["name"] == "python"
        assert result["title"] == "Python"
        assert result["subscribers"] == 2000000
        assert result["url"] == "https://reddit.com/r/python"

    async def test_not_found(self) -> None:
        response = _make_response(status_code=404, text="Not Found")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_subreddit("nonexistent12345")

        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_forbidden(self) -> None:
        response = _make_response(status_code=403, text="Forbidden")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_subreddit("private_sub")

        assert result["ok"] is False
        assert result["error"] == "forbidden"

    async def test_network_error(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_subreddit("python")

        assert result["ok"] is False
        assert result["error"] == "api_error"


@pytest.mark.asyncio
class TestRedditGetPostsValidation:
    async def test_invalid_sort(self) -> None:
        result = await server.reddit_get_posts("python", sort="controversial")
        assert result["ok"] is False
        assert result["error"] == "invalid_sort"
        assert "controversial" in result["message"]

    async def test_invalid_time_filter(self) -> None:
        result = await server.reddit_get_posts("python", time_filter="decade")
        assert result["ok"] is False
        assert result["error"] == "invalid_time_filter"
        assert "decade" in result["message"]

    async def test_successful_hot_posts(self) -> None:
        post_data = _make_post_data(id="s1", title="Hot Post")
        listing = _make_listing([{"kind": "t3", "data": post_data}])
        response = _make_response(json_data=listing)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_posts("python", sort="hot")

        assert result["ok"] is True
        assert result["subreddit"] == "python"
        assert result["sort"] == "hot"
        assert len(result["posts"]) == 1
        assert result["posts"][0]["id"] == "s1"

    async def test_not_found(self) -> None:
        response = _make_response(status_code=404, text="Not Found")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_posts("nonexistent12345")

        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_forbidden(self) -> None:
        response = _make_response(status_code=403, text="Forbidden")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_posts("private_sub")

        assert result["ok"] is False
        assert result["error"] == "forbidden"

    async def test_top_sort_includes_time_filter(self) -> None:
        post_data = _make_post_data()
        listing = _make_listing([{"kind": "t3", "data": post_data}])
        response = _make_response(json_data=listing)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            await server.reddit_get_posts("python", sort="top", time_filter="week", limit=5)

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        assert "/r/python/top.json" in url
        assert "t=week" in url
        assert "limit=5" in url


@pytest.mark.asyncio
class TestRedditGetPost:
    async def test_successful_fetch_with_comments(self) -> None:
        post_data = _make_post_data()
        comment_data = {
            "id": "c1",
            "author": "commenter",
            "body": "Great post!",
            "score": 15,
            "created_utc": 1700000100.0,
        }
        payload = [
            _make_listing([{"kind": "t3", "data": post_data}]),
            _make_listing([{"kind": "t1", "data": comment_data}]),
        ]
        response = _make_response(json_data=payload)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_post("abc123")

        assert result["ok"] is True
        assert result["id"] == "abc123"
        assert result["title"] == "Test Post"
        assert len(result["top_comments"]) == 1
        assert result["top_comments"][0]["id"] == "c1"
        assert result["top_comments"][0]["author"] == "commenter"

    async def test_not_found(self) -> None:
        response = _make_response(status_code=404, text="Not Found")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_post("nonexistent")

        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_invalid_response_format(self) -> None:
        response = _make_response(json_data={"error": "not a listing"})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_get_post("abc123")

        assert result["ok"] is False
        assert result["error"] == "api_error"


@pytest.mark.asyncio
class TestRedditSearchValidation:
    async def test_invalid_sort(self) -> None:
        result = await server.reddit_search("python", sort="top_rated")
        assert result["ok"] is False
        assert result["error"] == "invalid_sort"

    async def test_invalid_time_filter(self) -> None:
        result = await server.reddit_search("python", time_filter="century")
        assert result["ok"] is False
        assert result["error"] == "invalid_time_filter"

    async def test_successful_search_with_subreddit(self) -> None:
        post_data = _make_post_data(id="s2", title="Search Result")
        listing = _make_listing([{"kind": "t3", "data": post_data}])
        response = _make_response(json_data=listing)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_search("python tutorial", subreddit="learnpython")

        assert result["ok"] is True
        assert result["query"] == "python tutorial"
        assert result["subreddit"] == "learnpython"
        assert len(result["posts"]) == 1

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        assert "/r/learnpython/search.json" in url
        assert "restrict_sr=on" in url

    async def test_search_all_reddit(self) -> None:
        post_data = _make_post_data(id="s3", title="All Result")
        listing = _make_listing([{"kind": "t3", "data": post_data}])
        response = _make_response(json_data=listing)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_search("test query", subreddit=None)

        assert result["ok"] is True
        assert result["subreddit"] is None

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        assert "/search.json" in url
        assert "/r/" not in url

    async def test_search_not_found_subreddit(self) -> None:
        response = _make_response(status_code=404, text="Not Found")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_search("test", subreddit="nonexistent12345")

        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_generic_api_error(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Network error"))

        with patch.object(server, "_get_client", return_value=mock_client):
            result = await server.reddit_search("test")

        assert result["ok"] is False
        assert result["error"] == "api_error"


class TestUpdateRateLimits:
    def test_parses_headers(self) -> None:
        server._rate_limit_remaining = None
        server._rate_limit_reset = None

        headers = httpx.Headers({"x-ratelimit-remaining": "95", "x-ratelimit-reset": "300"})
        server._update_rate_limits(headers)

        assert server._rate_limit_remaining == 95
        assert server._rate_limit_reset == 300

    def test_parses_float_string_headers(self) -> None:
        """Reddit returns rate limit values as float strings like '99.0'."""
        server._rate_limit_remaining = None
        server._rate_limit_reset = None

        headers = httpx.Headers({"x-ratelimit-remaining": "99.0", "x-ratelimit-reset": "300.0"})
        server._update_rate_limits(headers)

        assert server._rate_limit_remaining == 99
        assert server._rate_limit_reset == 300

    def test_handles_missing_headers(self) -> None:
        server._rate_limit_remaining = None
        server._rate_limit_reset = None

        server._update_rate_limits(httpx.Headers({}))

        assert server._rate_limit_remaining is None
        assert server._rate_limit_reset is None


class TestGetClient:
    def test_creates_client_with_user_agent(self) -> None:
        server._client = None
        with patch.dict("os.environ", {"REDDIT_USER_AGENT": "test-agent/1.0"}, clear=False):
            client = server._get_client()
            assert client is not None
            assert "test-agent/1.0" in client.headers.get("user-agent", "")
        server._client = None

    def test_caches_client(self) -> None:
        server._client = None
        client1 = server._get_client()
        client2 = server._get_client()
        assert client1 is client2
        server._client = None


@pytest.mark.asyncio
class TestRetryOn429:
    async def test_retry_on_429_then_success(self) -> None:
        """When first request returns 429 and second returns 200, function should succeed."""
        response_429 = _make_response(status_code=429)
        sub_data = _make_subreddit_data()
        response_200 = _make_response(json_data={"kind": "t5", "data": sub_data})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[response_429, response_200])

        with (
            patch.object(server, "_get_client", return_value=mock_client),
            patch.object(server.asyncio, "sleep", new_callable=AsyncMock),
        ):
            result = await server.reddit_get_subreddit("python")

        assert result["ok"] is True
        assert result["name"] == "python"
        assert mock_client.get.call_count == 2

    async def test_all_retries_exhausted_429(self) -> None:
        """When all retry attempts return 429, should return an api_error response."""
        response_429 = _make_response(status_code=429)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response_429)

        with (
            patch.object(server, "_get_client", return_value=mock_client),
            patch.object(server.asyncio, "sleep", new_callable=AsyncMock),
        ):
            result = await server.reddit_get_subreddit("python")

        assert result["ok"] is False
        assert result["error"] == "api_error"
        assert mock_client.get.call_count == server._MAX_RETRIES


@pytest.mark.asyncio
class TestRateLimitTrackingViaRequests:
    async def test_rate_limit_headers_from_response(self) -> None:
        server._rate_limit_remaining = None
        server._rate_limit_reset = None

        post_data = _make_post_data()
        listing = _make_listing([{"kind": "t3", "data": post_data}])
        response = _make_response(
            json_data=listing,
            headers={"x-ratelimit-remaining": "90", "x-ratelimit-reset": "300"},
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            await server.reddit_get_posts("python")

        assert server._rate_limit_remaining == 90
        assert server._rate_limit_reset == 300
        server._rate_limit_remaining = None
        server._rate_limit_reset = None

    async def test_rate_limit_headers_absent(self) -> None:
        server._rate_limit_remaining = None
        server._rate_limit_reset = None

        post_data = _make_post_data()
        listing = _make_listing([{"kind": "t3", "data": post_data}])
        response = _make_response(json_data=listing, headers={})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)

        with patch.object(server, "_get_client", return_value=mock_client):
            await server.reddit_get_posts("python")

        assert server._rate_limit_remaining is None
        assert server._rate_limit_reset is None
