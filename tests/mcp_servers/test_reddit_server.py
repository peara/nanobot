from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# asyncpraw/asyncprawcore may not be installed yet; stub them for import
_asyncpraw_stub = MagicMock()
_asyncpraw_models = MagicMock()
_asyncpraw_models.Submission = MagicMock
_asyncpraw_stub.models = _asyncpraw_models
_asyncprawcore_exc = MagicMock()
_asyncprawcore_exc.NotFound = type(
    "NotFound",
    (Exception,),
    {
        "__module__": "asyncprawcore.exceptions",
        "__init__": lambda self, *a, **kw: Exception.__init__(self, *a),
    },
)
_asyncprawcore_exc.Forbidden = type(
    "Forbidden",
    (Exception,),
    {
        "__module__": "asyncprawcore.exceptions",
        "__init__": lambda self, *a, **kw: Exception.__init__(self, *a),
    },
)

# asyncprawcore.exceptions must be reachable via asyncprawcore.exceptions
# so that except clauses in the server module resolve correctly.
_asyncprawcore_stub = MagicMock()
_asyncprawcore_stub.exceptions = _asyncprawcore_exc

sys.modules.setdefault("asyncpraw", _asyncpraw_stub)
sys.modules.setdefault("asyncpraw.models", _asyncpraw_models)
sys.modules.setdefault("asyncprawcore", _asyncprawcore_stub)
sys.modules.setdefault("asyncprawcore.exceptions", _asyncprawcore_exc)

from nanobot.mcp_servers.reddit import server  # noqa: E402

_NotFound = server.asyncprawcore.exceptions.NotFound
_Forbidden = server.asyncprawcore.exceptions.Forbidden


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
        assert len(result) == len("a" * 500) + len(" [truncated]")

    def test_custom_limit(self) -> None:
        text = "a" * 100
        result = server._truncate(text, 50)
        assert result == "a" * 50 + " [truncated]"

    def test_empty_string(self) -> None:
        assert server._truncate("", 500) == ""


class TestSubmissionToDict:
    def _make_submission(self, **overrides: Any) -> MagicMock:
        sub = MagicMock()
        sub.id = overrides.get("id", "abc123")
        sub.title = overrides.get("title", "Test Post")
        sub.selftext = overrides.get("selftext", "Post body")
        sub.author = overrides.get("author", MagicMock(__str__=lambda s: "testuser"))
        sub.score = overrides.get("score", 42)
        sub.num_comments = overrides.get("num_comments", 10)
        sub.created_utc = overrides.get("created_utc", 1700000000.0)
        sub.permalink = overrides.get("permalink", "/r/python/comments/abc123/test_post/")
        sub.url = overrides.get("url", "https://example.com")
        sub.is_self = overrides.get("is_self", True)
        sub.link_flair_text = overrides.get("link_flair_text", "Discussion")
        sub.over_18 = overrides.get("over_18", False)
        sub.stickied = overrides.get("stickied", False)
        return sub

    def test_basic_fields(self) -> None:
        sub = self._make_submission()
        result = server._submission_to_dict(sub)
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

    def test_permalink_url(self) -> None:
        sub = self._make_submission(permalink="/r/python/comments/abc123/test_post/")
        result = server._submission_to_dict(sub)
        assert result["permalink"] == "https://reddit.com/r/python/comments/abc123/test_post/"

    def test_deleted_author(self) -> None:
        sub = self._make_submission(author=None)
        result = server._submission_to_dict(sub)
        assert result["author"] == "[deleted]"

    def test_created_utc_iso_format(self) -> None:
        sub = self._make_submission(created_utc=1700000000.0)
        result = server._submission_to_dict(sub)
        expected = datetime.fromtimestamp(1700000000.0, tz=timezone.utc).isoformat()
        assert result["created_utc"] == expected

    def test_long_selftext_truncated(self) -> None:
        sub = self._make_submission(selftext="x" * 600)
        result = server._submission_to_dict(sub)
        assert result["body"].endswith("[truncated]")
        assert len(result["body"]) < 600

    def test_empty_selftext(self) -> None:
        sub = self._make_submission(selftext="")
        result = server._submission_to_dict(sub)
        assert result["body"] == ""

    def test_none_selftext(self) -> None:
        sub = self._make_submission(selftext=None)
        # selftext=None is falsy, so `or ""` converts it
        result = server._submission_to_dict(sub)
        assert result["body"] == ""


class TestRedditHealth:
    def test_returns_env_status(self) -> None:
        with patch.dict("os.environ", {"PRAW_CLIENT_ID": "id", "PRAW_CLIENT_SECRET": "secret"}, clear=False):
            result = server.reddit_health()
        assert result["ok"] is True
        assert result["has_client_id"] is True
        assert result["has_client_secret"] is True

    def test_missing_env_vars(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = server.reddit_health()
        assert result["ok"] is True
        assert result["has_client_id"] is False
        assert result["has_client_secret"] is False
        assert result["has_refresh_token"] is False

    def test_custom_user_agent(self) -> None:
        with patch.dict("os.environ", {"PRAW_USER_AGENT": "custom/2.0"}, clear=False):
            result = server.reddit_health()
        assert result["user_agent"] == "custom/2.0"

    def test_default_user_agent(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = server.reddit_health()
        assert result["user_agent"] == "nanobot-reddit/1.0"


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

    async def test_limit_capped_at_max(self) -> None:
        fake_submission = MagicMock()
        fake_submission.id = "x1"
        fake_submission.title = "T"
        fake_submission.selftext = ""
        fake_submission.author = None
        fake_submission.score = 0
        fake_submission.num_comments = 0
        fake_submission.created_utc = 1700000000.0
        fake_submission.permalink = "/r/test/x1"
        fake_submission.url = "https://example.com"
        fake_submission.is_self = False
        fake_submission.link_flair_text = None
        fake_submission.over_18 = False
        fake_submission.stickied = False

        async def _aiter(*_args: Any, **_kwargs: Any) -> Any:
            yield fake_submission

        mock_subreddit = AsyncMock()
        mock_subreddit.hot = MagicMock(return_value=_aiter())
        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_posts("python", limit=100)
        assert result["ok"] is True


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


@pytest.mark.asyncio
class TestRedditGetSubreddit:
    async def test_successful_fetch(self) -> None:
        mock_sub = AsyncMock()
        mock_sub.id = "2qhg4"
        mock_sub.display_name = "python"
        mock_sub.title = "Python"
        mock_sub.public_description = "News about Python"
        mock_sub.description = "Detailed description"
        mock_sub.subscribers = 2000000
        mock_sub.active_user_count = 5000
        mock_sub.over18 = False
        mock_sub.created_utc = 1160693407.0
        mock_sub.load = AsyncMock()

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_sub)

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_subreddit("python")

        assert result["ok"] is True
        assert result["name"] == "python"
        assert result["title"] == "Python"
        assert result["subscribers"] == 2000000
        assert result["url"] == "https://reddit.com/r/python"

    async def test_not_found(self) -> None:
        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(
            side_effect=_NotFound(response=MagicMock()),
        )

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_subreddit("nonexistent12345")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_forbidden(self) -> None:
        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(
            side_effect=_Forbidden(response=MagicMock()),
        )

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_subreddit("private_sub")
        assert result["ok"] is False
        assert result["error"] == "forbidden"


@pytest.mark.asyncio
class TestRedditGetPost:
    async def test_successful_fetch_with_comments(self) -> None:
        mock_comment = MagicMock()
        mock_comment.id = "c1"
        mock_comment.author = MagicMock(__str__=lambda s: "commenter")
        mock_comment.body = "Great post!"
        mock_comment.score = 15
        mock_comment.created_utc = 1700000100.0

        mock_comments = MagicMock()
        mock_comments.replace_more = AsyncMock()
        mock_comments.__iter__ = MagicMock(return_value=iter([mock_comment]))
        mock_comments.__getitem__ = MagicMock(return_value=[mock_comment])

        mock_submission = AsyncMock()
        mock_submission.id = "abc123"
        mock_submission.title = "Test Post"
        mock_submission.selftext = "Body text"
        mock_submission.author = MagicMock(__str__=lambda s: "poster")
        mock_submission.score = 100
        mock_submission.num_comments = 5
        mock_submission.created_utc = 1700000000.0
        mock_submission.permalink = "/r/python/comments/abc123/test_post/"
        mock_submission.url = "https://example.com"
        mock_submission.is_self = True
        mock_submission.link_flair_text = None
        mock_submission.over_18 = False
        mock_submission.stickied = False
        mock_submission.comments = mock_comments
        mock_submission.load = AsyncMock()

        mock_reddit = AsyncMock()
        mock_reddit.submission = AsyncMock(return_value=mock_submission)

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_post("abc123")

        assert result["ok"] is True
        assert result["id"] == "abc123"
        assert result["title"] == "Test Post"
        assert len(result["top_comments"]) == 1
        assert result["top_comments"][0]["id"] == "c1"
        assert result["top_comments"][0]["author"] == "commenter"

    async def test_not_found(self) -> None:
        mock_reddit = AsyncMock()
        mock_reddit.submission = AsyncMock(
            side_effect=_NotFound(response=MagicMock()),
        )

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_post("nonexistent")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_comment_limit_capped(self) -> None:
        mock_comments = MagicMock()
        mock_comments.replace_more = AsyncMock()
        mock_comments.__iter__ = MagicMock(return_value=iter([]))
        mock_comments.__getitem__ = MagicMock(return_value=[])

        mock_submission = AsyncMock()
        mock_submission.id = "p1"
        mock_submission.title = "T"
        mock_submission.selftext = ""
        mock_submission.author = None
        mock_submission.score = 0
        mock_submission.num_comments = 0
        mock_submission.created_utc = 1700000000.0
        mock_submission.permalink = "/r/test/p1"
        mock_submission.url = "https://example.com"
        mock_submission.is_self = False
        mock_submission.link_flair_text = None
        mock_submission.over_18 = False
        mock_submission.stickied = False
        mock_submission.comments = mock_comments
        mock_submission.load = AsyncMock()

        mock_reddit = AsyncMock()
        mock_reddit.submission = AsyncMock(return_value=mock_submission)

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_post("p1", comment_limit=50)
        # comment_limit capped at 25 internally, function still works
        assert result["ok"] is True


@pytest.mark.asyncio
class TestRedditGetPosts:
    async def test_successful_hot_posts(self) -> None:
        fake_submission = MagicMock()
        fake_submission.id = "s1"
        fake_submission.title = "Hot Post"
        fake_submission.selftext = "Content"
        fake_submission.author = MagicMock(__str__=lambda s: "user1")
        fake_submission.score = 99
        fake_submission.num_comments = 20
        fake_submission.created_utc = 1700000000.0
        fake_submission.permalink = "/r/python/comments/s1/hot_post/"
        fake_submission.url = "https://example.com"
        fake_submission.is_self = True
        fake_submission.link_flair_text = "News"
        fake_submission.over_18 = False
        fake_submission.stickied = False

        async def _aiter(*_args: Any, **_kwargs: Any) -> Any:
            yield fake_submission

        mock_subreddit = AsyncMock()
        mock_subreddit.hot = MagicMock(return_value=_aiter())

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_posts("python", sort="hot")

        assert result["ok"] is True
        assert result["subreddit"] == "python"
        assert result["sort"] == "hot"
        assert len(result["posts"]) == 1
        assert result["posts"][0]["id"] == "s1"

    async def test_not_found(self) -> None:
        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(
            side_effect=_NotFound(response=MagicMock()),
        )

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_posts("nonexistent12345")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_forbidden(self) -> None:
        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(
            side_effect=_Forbidden(response=MagicMock()),
        )

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_get_posts("private_sub")
        assert result["ok"] is False
        assert result["error"] == "forbidden"


@pytest.mark.asyncio
class TestRedditSearch:
    async def test_successful_search(self) -> None:
        fake_submission = MagicMock()
        fake_submission.id = "s2"
        fake_submission.title = "Search Result"
        fake_submission.selftext = ""
        fake_submission.author = MagicMock(__str__=lambda s: "search_user")
        fake_submission.score = 5
        fake_submission.num_comments = 1
        fake_submission.created_utc = 1700000000.0
        fake_submission.permalink = "/r/all/comments/s2/search_result/"
        fake_submission.url = "https://example.com/2"
        fake_submission.is_self = False
        fake_submission.link_flair_text = None
        fake_submission.over_18 = False
        fake_submission.stickied = False

        async def _aiter(*_args: Any, **_kwargs: Any) -> Any:
            yield fake_submission

        mock_subreddit = AsyncMock()
        mock_subreddit.search = MagicMock(return_value=_aiter())

        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(return_value=mock_subreddit)

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_search("python tutorial", subreddit="learnpython")

        assert result["ok"] is True
        assert result["query"] == "python tutorial"
        assert result["subreddit"] == "learnpython"
        assert len(result["posts"]) == 1

    async def test_search_all_reddit(self) -> None:
        """Subreddit=None searches all of Reddit via r/all."""
        fake_submission = MagicMock()
        fake_submission.id = "s3"
        fake_submission.title = "All Result"
        fake_submission.selftext = ""
        fake_submission.author = None
        fake_submission.score = 1
        fake_submission.num_comments = 0
        fake_submission.created_utc = 1700000000.0
        fake_submission.permalink = "/r/all/comments/s3/all_result/"
        fake_submission.url = "https://example.com/3"
        fake_submission.is_self = False
        fake_submission.link_flair_text = None
        fake_submission.over_18 = False
        fake_submission.stickied = False

        async def _aiter(*_args: Any, **_kwargs: Any) -> Any:
            yield fake_submission

        mock_all = AsyncMock()
        mock_all.search = MagicMock(return_value=_aiter())

        mock_reddit = AsyncMock()
        # subreddit=None path calls await reddit.subreddit("all")
        mock_reddit.subreddit = AsyncMock(return_value=mock_all)

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_search("test query", subreddit=None)

        assert result["ok"] is True
        assert result["subreddit"] is None

    async def test_search_not_found_subreddit(self) -> None:
        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(
            side_effect=_NotFound(response=MagicMock()),
        )

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_search("test", subreddit="nonexistent12345")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    async def test_generic_api_error(self) -> None:
        mock_reddit = AsyncMock()
        mock_reddit.subreddit = AsyncMock(side_effect=RuntimeError("Network error"))

        with patch.object(server, "_reddit_client", return_value=mock_reddit):
            result = await server.reddit_search("test")
        assert result["ok"] is False
        assert result["error"] == "api_error"


class TestRedditClient:
    def test_missing_client_id_raises(self) -> None:
        server._reddit = None  # Reset global for test isolation
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="PRAW_CLIENT_ID"):
                server._reddit_client()

    def test_creates_client_from_env(self) -> None:
        server._reddit = None  # Reset global for test isolation
        env = {
            "PRAW_CLIENT_ID": "test_id",
            "PRAW_CLIENT_SECRET": "test_secret",
            "PRAW_REFRESH_TOKEN": "test_token",
            "PRAW_USER_AGENT": "test_agent/1.0",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch.object(server.asyncpraw, "Reddit") as mock_reddit_cls:
                mock_reddit_cls.return_value = MagicMock()
                server._reddit_client()
                mock_reddit_cls.assert_called_once_with(
                    client_id="test_id",
                    client_secret="test_secret",
                    refresh_token="test_token",
                    user_agent="test_agent/1.0",
                )

    def test_caches_client(self) -> None:
        server._reddit = None  # Reset global for test isolation
        with patch.dict(
            "os.environ",
            {
                "PRAW_CLIENT_ID": "id",
                "PRAW_CLIENT_SECRET": "secret",
            },
            clear=False,
        ):
            with patch.object(server.asyncpraw, "Reddit") as mock_reddit_cls:
                mock_reddit_cls.return_value = MagicMock()
                client1 = server._reddit_client()
                client2 = server._reddit_client()
                assert mock_reddit_cls.call_count == 1
                assert client1 is client2
