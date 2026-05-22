from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.config import McpServerConfig
from nanobot.mcp_hub import McpHub


def _mock_stdio_and_session() -> tuple[AsyncMock, AsyncMock]:
    """Set up stdio_client and ClientSession mocks that simulate a successful server start."""
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.list_tools = AsyncMock(return_value=AsyncMock(tools=[]))

    mock_read = AsyncMock()
    mock_write = AsyncMock()
    return mock_read, mock_write


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAW_CLIENT_ID", raising=False)
    monkeypatch.delenv("PRAW_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PRAW_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("MISSING_VAR", raising=False)


class TestRequiredEnvCheck:
    async def test_skip_server_when_required_env_missing(self, caplog: pytest.LogCaptureFixture, no_env: None) -> None:
        server = McpServerConfig(
            name="reddit",
            command="python",
            required_env=["PRAW_CLIENT_ID", "PRAW_CLIENT_SECRET"],
        )
        hub = McpHub([server])
        with caplog.at_level(logging.WARNING):
            await hub.start()
        assert "reddit" not in hub._sessions
        assert len(hub._tools) == 0
        assert any("Skipping MCP server 'reddit'" in record.message for record in caplog.records)

    async def test_skip_server_when_required_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAW_CLIENT_ID", "")
        server = McpServerConfig(
            name="reddit",
            command="python",
            required_env=["PRAW_CLIENT_ID"],
        )
        hub = McpHub([server])
        await hub.start()
        assert "reddit" not in hub._sessions

    async def test_env_from_config_satisfies_required_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PRAW_CLIENT_ID", raising=False)
        server = McpServerConfig(
            name="reddit",
            command="python",
            required_env=["PRAW_CLIENT_ID"],
            env={"PRAW_CLIENT_ID": "value_from_config"},
        )
        hub = McpHub([server])
        with patch("nanobot.mcp_hub.stdio_client") as mock_stdio, \
             patch("nanobot.mcp_hub.ClientSession") as mock_session_cls:
            mock_read, mock_write = _mock_stdio_and_session()
            mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
            mock_stdio.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await hub.start()
        assert "reddit" in hub._sessions

    async def test_start_server_when_required_env_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAW_CLIENT_ID", "test_id")
        monkeypatch.setenv("PRAW_CLIENT_SECRET", "test_secret")
        server = McpServerConfig(
            name="test",
            command="echo",
            args=["hello"],
            required_env=["PRAW_CLIENT_ID", "PRAW_CLIENT_SECRET"],
        )
        hub = McpHub([server])
        with patch("nanobot.mcp_hub.stdio_client") as mock_stdio, \
             patch("nanobot.mcp_hub.ClientSession") as mock_session_cls:
            mock_read, mock_write = _mock_stdio_and_session()
            mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
            mock_stdio.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await hub.start()
        assert "test" in hub._sessions

    async def test_no_required_env_always_passes(self) -> None:
        server = McpServerConfig(name="basic", command="echo")
        assert server.required_env == []
        hub = McpHub([server])
        with patch("nanobot.mcp_hub.stdio_client") as mock_stdio, \
             patch("nanobot.mcp_hub.ClientSession") as mock_session_cls:
            mock_read, mock_write = _mock_stdio_and_session()
            mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
            mock_stdio.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await hub.start()
        assert "basic" in hub._sessions


class TestGracefulDegradation:
    async def test_failed_server_does_not_crash_bot(self, caplog: pytest.LogCaptureFixture) -> None:
        server = McpServerConfig(name="broken", command="echo")
        hub = McpHub([server])
        with patch("nanobot.mcp_hub.stdio_client", side_effect=RuntimeError("boom")), \
             caplog.at_level(logging.ERROR):
            await hub.start()
        assert "broken" not in hub._sessions
        assert any("Failed to start MCP server 'broken'" in r.message for r in caplog.records)

    async def test_skip_logs_warning(self, caplog: pytest.LogCaptureFixture, no_env: None) -> None:
        server = McpServerConfig(
            name="reddit",
            command="python",
            required_env=["MISSING_VAR_A", "MISSING_VAR_B"],
        )
        hub = McpHub([server])
        with caplog.at_level(logging.WARNING):
            await hub.start()
        assert any("Skipping MCP server 'reddit'" in r.message for r in caplog.records)
        assert any("MISSING_VAR_A" in r.message for r in caplog.records)

    async def test_failure_logs_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        server = McpServerConfig(name="broken", command="echo")
        hub = McpHub([server])
        with patch("nanobot.mcp_hub.stdio_client", side_effect=RuntimeError("boom")), \
             caplog.at_level(logging.ERROR):
            await hub.start()
        assert any("Failed to start MCP server 'broken'" in r.message for r in caplog.records)

    async def test_summary_log_on_start(self, caplog: pytest.LogCaptureFixture) -> None:
        server = McpServerConfig(name="test", command="echo")
        hub = McpHub([server])
        with patch("nanobot.mcp_hub.stdio_client") as mock_stdio, \
             patch("nanobot.mcp_hub.ClientSession") as mock_session_cls, \
             caplog.at_level(logging.INFO):
            mock_read, mock_write = _mock_stdio_and_session()
            mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
            mock_stdio.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await hub.start()
        assert any("MCP servers started" in r.message for r in caplog.records)

    async def test_mixed_skip_and_succeed(
        self, monkeypatch: pytest.MonkeyPatch, no_env: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        skipped = McpServerConfig(name="skipped", command="python", required_env=["MISSING_VAR"])
        good = McpServerConfig(name="good", command="echo")

        hub = McpHub([skipped, good])
        with patch("nanobot.mcp_hub.stdio_client") as mock_stdio, \
             patch("nanobot.mcp_hub.ClientSession") as mock_session_cls, \
             caplog.at_level(logging.INFO):
            mock_read, mock_write = _mock_stdio_and_session()
            mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
            mock_stdio.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            await hub.start()

        assert "skipped" not in hub._sessions
        assert "good" in hub._sessions
        assert any("MCP servers started: ['good']" in r.message for r in caplog.records)

    async def test_all_servers_skip_still_logs_summary(self, caplog: pytest.LogCaptureFixture, no_env: None) -> None:
        server = McpServerConfig(name="unavailable", command="python", required_env=["MISSING_VAR"])
        hub = McpHub([server])
        with caplog.at_level(logging.INFO):
            await hub.start()
        assert "unavailable" not in hub._sessions
        assert any("MCP servers started: []" in r.message for r in caplog.records)