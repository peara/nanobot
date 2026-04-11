from __future__ import annotations

import asyncio
from typing import Any

from nanobot.mcp_servers.web import server


def test_web_health_reports_defaults_and_capabilities() -> None:
    payload = server.web_health()

    assert payload["ok"] is True
    assert "defaults" in payload
    assert "capabilities" in payload
    assert "headless" in payload["defaults"]
    assert "read_ready" in payload["capabilities"]


def test_read_page_uses_tool_and_persists_outputs(monkeypatch) -> None:
    events: dict[str, Any] = {}

    class _FakeTool:
        async def read(self, url: str) -> dict[str, Any]:
            return {
                "ok": True,
                "url": url,
                "title": "Example",
                "markdown": "# Example",
            }

    def _fake_build_tool(*, quality_threshold: float | None, headless: bool | None) -> _FakeTool:
        events["build"] = {"quality_threshold": quality_threshold, "headless": headless}
        return _FakeTool()

    def _fake_save_result_payload(command: str, url: str, payload: dict[str, Any]) -> dict[str, str]:
        events["save"] = {"command": command, "url": url, "payload": payload}
        return {"json_path": "/tmp/read.json", "markdown_path": "/tmp/read.md"}

    monkeypatch.setattr(server, "_build_tool", _fake_build_tool)
    monkeypatch.setattr(server, "save_result_payload", _fake_save_result_payload)

    payload = asyncio.run(
        server.read_page(
            "https://example.com",
            quality_threshold=0.7,
            headless=False,
            save_outputs=True,
        )
    )

    assert payload["ok"] is True
    assert payload["saved_outputs"]["json_path"] == "/tmp/read.json"
    assert events["build"] == {"quality_threshold": 0.7, "headless": False}
    assert events["save"]["command"] == "read"


def test_search_google_web_returns_search_payload(monkeypatch) -> None:
    async def _fake_search_google(query: str, *, limit: int, language: str) -> dict[str, Any]:
        return {
            "ok": True,
            "query": query,
            "results": [{"title": "Example", "url": "https://example.com", "snippet": "result"}],
            "result_count": 1,
            "language": language,
            "limit": limit,
        }

    monkeypatch.setattr(server, "search_google", _fake_search_google)

    payload = asyncio.run(server.search_google_web("gia xang viet nam", limit=3, language="vi"))

    assert payload["ok"] is True
    assert payload["query"] == "gia xang viet nam"
    assert payload["results"][0]["url"] == "https://example.com"


def test_search_web_returns_search_payload(monkeypatch) -> None:
    async def _fake_search_web(
        query: str,
        *,
        limit: int,
        language: str,
        domains: list[str] | None,
        freshness: str | None,
        provider: str,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "query": query,
            "provider": provider,
            "results": [{"title": "Example", "url": "https://example.com", "snippet": "result"}],
            "result_count": 1,
            "language": language,
            "limit": limit,
            "domains": domains,
            "freshness": freshness,
        }

    monkeypatch.setattr(server, "search_web_impl", _fake_search_web)

    payload = asyncio.run(
        server.search_web(
            "gia vang viet nam",
            limit=4,
            language="vi",
            domains=["sjc.com.vn"],
            freshness="day",
            provider="exa",
        )
    )

    assert payload["ok"] is True
    assert payload["query"] == "gia vang viet nam"
    assert payload["provider"] == "exa"
    assert payload["results"][0]["url"] == "https://example.com"


def test_snapshot_page_normalizes_payload_without_ok(monkeypatch) -> None:
    class _FakeTool:
        async def snapshot(self, url: str) -> dict[str, Any]:
            return {
                "url": url,
                "title": "Snapshot",
                "visible_text": "Visible",
            }

    monkeypatch.setattr(server, "_build_tool", lambda **_: _FakeTool())

    payload = asyncio.run(server.snapshot_page("https://example.com"))

    assert payload["ok"] is True
    assert payload["title"] == "Snapshot"


def test_interact_page_returns_failure_payload_on_exception(monkeypatch) -> None:
    class _FakeTool:
        async def interact(self, url: str, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            del url, steps
            raise RuntimeError("boom")

    monkeypatch.setattr(server, "_build_tool", lambda **_: _FakeTool())

    payload = asyncio.run(server.interact_page("https://example.com", steps=[{"action": "click", "target": "next"}]))

    assert payload["ok"] is False
    assert payload["error"] == "tool_failed"
    assert "boom" in payload["message"]
