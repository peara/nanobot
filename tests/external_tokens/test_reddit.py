from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from nanobot.config import _deep_merge
from nanobot.external_tokens.reddit import (
    RedditOAuthResult,
    _build_reddit_server_config,
    _OAuthHandler,
    _write_env_file,
    _write_override_config,
)


class TestWriteEnvFile:
    """Tests for _write_env_file .env writing logic."""

    def test_creates_new_env_file(self, tmp_path: Path) -> None:
        env_path = str(tmp_path / ".env")
        _write_env_file(env_path, {"PRAW_CLIENT_ID": "abc123", "PRAW_CLIENT_SECRET": "secret"})
        content = Path(env_path).read_text(encoding="utf-8")
        assert "PRAW_CLIENT_ID=abc123" in content
        assert "PRAW_CLIENT_SECRET=secret" in content

    def test_updates_existing_key(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("PRAW_CLIENT_ID=old_value\nOTHER_KEY=keep\n", encoding="utf-8")
        _write_env_file(str(env_path), {"PRAW_CLIENT_ID": "new_value"})
        content = env_path.read_text(encoding="utf-8")
        assert "PRAW_CLIENT_ID=new_value" in content
        assert "OTHER_KEY=keep" in content
        assert "old_value" not in content

    def test_preserves_comments(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("# This is a comment\nSOME_KEY=val\n", encoding="utf-8")
        _write_env_file(str(env_path), {"NEW_KEY": "new_val"})
        content = env_path.read_text(encoding="utf-8")
        assert "# This is a comment" in content
        assert "SOME_KEY=val" in content
        assert "NEW_KEY=new_val" in content

    def test_appends_new_key_if_not_present(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=val\n", encoding="utf-8")
        _write_env_file(str(env_path), {"PRAW_REFRESH_TOKEN": "token123"})
        content = env_path.read_text(encoding="utf-8")
        assert "EXISTING=val" in content
        assert "PRAW_REFRESH_TOKEN=token123" in content

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        env_path = str(tmp_path / "subdir" / "nested" / ".env")
        _write_env_file(env_path, {"KEY": "val"})
        assert Path(env_path).is_file()


class TestBuildRedditServerConfig:
    """Tests for _build_reddit_server_config."""

    def test_returns_expected_structure(self) -> None:
        config = _build_reddit_server_config()
        assert config["name"] == "reddit"
        assert config["command"] == "python"
        assert config["args"] == ["-m", "nanobot.mcp_servers.reddit.server"]
        assert "PRAW_CLIENT_ID" in config["env"]
        assert "PRAW_CLIENT_SECRET" in config["env"]
        assert "PRAW_REFRESH_TOKEN" in config["env"]
        assert "PRAW_CLIENT_ID" in config["required_env"]
        assert len(config["required_env"]) == 3

    def test_env_values_use_env_var_syntax(self) -> None:
        config = _build_reddit_server_config()
        assert config["env"]["PRAW_CLIENT_ID"] == "${PRAW_CLIENT_ID}"
        assert config["env"]["PRAW_CLIENT_SECRET"] == "${PRAW_CLIENT_SECRET}"
        assert config["env"]["PRAW_REFRESH_TOKEN"] == "${PRAW_REFRESH_TOKEN}"


class TestWriteOverrideConfig:
    """Tests for _write_override_config and _deep_merge integration."""

    def test_creates_new_override_file(self, tmp_path: Path) -> None:
        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text("assistant_name: Nano\n", encoding="utf-8")

        reddit_server = _build_reddit_server_config()
        _write_override_config(config_path, reddit_server)

        override_path = tmp_path / "config.override.yaml"
        assert override_path.is_file()
        content = yaml.safe_load(override_path.read_text(encoding="utf-8"))
        assert "mcp_servers" in content
        assert len(content["mcp_servers"]) == 1
        assert content["mcp_servers"][0]["name"] == "reddit"

    def test_merges_with_existing_override(self, tmp_path: Path) -> None:
        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text("assistant_name: Nano\n", encoding="utf-8")

        override_path = tmp_path / "config.override.yaml"
        override_path.write_text(
            yaml.dump({"model": {"temperature": 0.5}}, default_flow_style=False),
            encoding="utf-8",
        )

        reddit_server = _build_reddit_server_config()
        _write_override_config(config_path, reddit_server)

        content = yaml.safe_load(override_path.read_text(encoding="utf-8"))
        assert "model" in content
        assert content["model"]["temperature"] == 0.5
        assert "mcp_servers" in content
        assert len(content["mcp_servers"]) == 1

    def test_appends_to_existing_mcp_servers_list(self, tmp_path: Path) -> None:
        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text("assistant_name: Nano\n", encoding="utf-8")

        existing_server = {"name": "timer", "command": "python", "args": ["-m", "nanobot.mcp_servers.timer.server"]}
        override_path = tmp_path / "config.override.yaml"
        override_path.write_text(
            yaml.dump({"mcp_servers": [existing_server]}, default_flow_style=False),
            encoding="utf-8",
        )

        reddit_server = _build_reddit_server_config()
        _write_override_config(config_path, reddit_server)

        content = yaml.safe_load(override_path.read_text(encoding="utf-8"))
        assert len(content["mcp_servers"]) == 2
        names = [s["name"] for s in content["mcp_servers"]]
        assert "timer" in names
        assert "reddit" in names


class TestOAuthHandler:
    """Tests for _OAuthHandler HTTP request parsing."""

    def test_handler_parses_success_params(self) -> None:
        from urllib.parse import urlencode

        handler = _OAuthHandler.__new__(_OAuthHandler)
        handler.path = f"/?{urlencode({'code': 'abc123', 'state': 'test_state'})}"
        handler.server = MagicMock()
        handler.server.auth_result = None
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler.do_GET()

        result = handler.server.auth_result
        assert result is not None
        assert result["ok"] is True
        assert result["code"] == "abc123"
        assert result["state"] == "test_state"

    def test_handler_parses_error_params(self) -> None:
        from urllib.parse import urlencode

        handler = _OAuthHandler.__new__(_OAuthHandler)
        handler.path = f"/?{urlencode({'error': 'access_denied'})}"
        handler.server = MagicMock()
        handler.server.auth_result = None
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler.do_GET()

        result = handler.server.auth_result
        assert result is not None
        assert result["ok"] is False
        assert result["error"] == "access_denied"


class TestDeepMergeIntegration:
    """Verify _deep_merge from config.py works correctly for override merging."""

    def test_merges_dicts(self) -> None:
        base = {"model": {"temperature": 0.2, "max_tokens": 800}}
        override = {"model": {"temperature": 0.5}}
        result = _deep_merge(base, override)
        assert result["model"]["temperature"] == 0.5
        assert result["model"]["max_tokens"] == 800

    def test_appends_lists(self) -> None:
        base = {"mcp_servers": [{"name": "timer"}]}
        override = {"mcp_servers": [{"name": "reddit"}]}
        result = _deep_merge(base, override)
        assert len(result["mcp_servers"]) == 2
        assert result["mcp_servers"][0]["name"] == "timer"
        assert result["mcp_servers"][1]["name"] == "reddit"

    def test_overrides_scalars(self) -> None:
        base = {"assistant_name": "Nano"}
        override = {"assistant_name": "Bot"}
        result = _deep_merge(base, override)
        assert result["assistant_name"] == "Bot"


class TestBootstrapReddit:
    """Tests for bootstrap_reddit with mocked asyncpraw."""

    @pytest.mark.asyncio
    async def test_raises_on_empty_scopes(self) -> None:
        from nanobot.external_tokens.reddit import bootstrap_reddit

        with pytest.raises(ValueError, match="At least one OAuth scope"):
            await bootstrap_reddit(
                client_id="test",
                client_secret="test",
                scopes="",
                config_path="/tmp/nonexistent.yaml",
            )

    @pytest.mark.asyncio
    async def test_raises_on_whitespace_only_scopes(self) -> None:
        from nanobot.external_tokens.reddit import bootstrap_reddit

        with pytest.raises(ValueError, match="At least one OAuth scope"):
            await bootstrap_reddit(
                client_id="test",
                client_secret="test",
                scopes="  ,  ,  ",
                config_path="/tmp/nonexistent.yaml",
            )


class TestGenerateAuthUrl:
    """Tests for _generate_auth_url with mocked asyncpraw."""

    @patch("asyncpraw.Reddit")
    def test_creates_reddit_instance_with_correct_params(self, mock_reddit_cls: MagicMock) -> None:
        mock_reddit = MagicMock()
        mock_reddit.auth.url.return_value = "https://reddit.com/auth?state=abc"
        mock_reddit_cls.return_value = mock_reddit

        from nanobot.external_tokens.reddit import _generate_auth_url

        result = _generate_auth_url(
            client_id="test_id",
            client_secret="test_secret",
            redirect_port=8080,
            scopes=["identity", "read"],
            user_agent="test_agent",
        )

        mock_reddit_cls.assert_called_once_with(
            client_id="test_id",
            client_secret="test_secret",
            redirect_uri="http://localhost:8080",
            user_agent="test_agent",
        )
        mock_reddit.auth.url.assert_called_once()
        assert isinstance(result, RedditOAuthResult)
        assert result.reddit == mock_reddit
        assert len(result.state) > 0

    @patch("asyncpraw.Reddit")
    def test_uses_permanent_duration(self, mock_reddit_cls: MagicMock) -> None:
        mock_reddit = MagicMock()
        mock_reddit.auth.url.return_value = "https://reddit.com/auth"
        mock_reddit_cls.return_value = mock_reddit

        from nanobot.external_tokens.reddit import _generate_auth_url

        _generate_auth_url(
            client_id="id",
            client_secret="secret",
            redirect_port=9090,
            scopes=["identity"],
            user_agent="agent",
        )

        call_kwargs = mock_reddit.auth.url.call_args[1]
        assert call_kwargs["duration"] == "permanent"
        assert call_kwargs["scopes"] == ["identity"]
