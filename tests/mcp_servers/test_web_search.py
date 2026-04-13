from __future__ import annotations

import asyncio

from web_agent import search


def test_search_web_uses_tavily_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    async def _fake_search_tavily(
        query: str,
        *,
        limit: int,
        domains: list[str] | None,
        freshness: str | None,
    ) -> dict[str, object]:
        assert query == "gia vang sjc"
        assert limit == 3
        assert domains is None
        assert freshness is None
        return {
            "provider": "tavily",
            "results": [
                {
                    "title": "Gia vang",
                    "url": "https://example.com/gia-vang",
                    "snippet": "Example",
                    "score": 0.9,
                    "published_date": None,
                    "source": "tavily",
                }
            ],
            "provider_metadata": {"topic": "finance"},
        }

    monkeypatch.setattr(search, "_search_tavily", _fake_search_tavily)

    payload = asyncio.run(search.search_web("gia vang sjc", limit=3))

    assert payload["ok"] is True
    assert payload["provider"] == "tavily"
    assert payload["results"][0]["url"] == "https://example.com/gia-vang"


def test_search_web_falls_back_to_exa_after_tavily_failure(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("EXA_API_KEY", "exa-test")

    async def _fake_search_tavily(
        query: str,
        *,
        limit: int,
        domains: list[str] | None,
        freshness: str | None,
    ) -> dict[str, object]:
        assert query == "gia vang sjc"
        assert limit == 4
        assert domains == ["sjc.com.vn"]
        assert freshness == "day"
        raise RuntimeError("503 upstream error")

    async def _fake_search_exa(
        query: str,
        *,
        limit: int,
        domains: list[str] | None,
        freshness: str | None,
    ) -> dict[str, object]:
        assert query == "gia vang sjc"
        assert limit == 4
        assert domains == ["sjc.com.vn"]
        assert freshness == "day"
        return {
            "provider": "exa",
            "results": [
                {
                    "title": "SJC",
                    "url": "https://sjc.com.vn/gia-vang",
                    "snippet": "Banggia",
                    "score": None,
                    "published_date": "2026-04-11T00:00:00Z",
                    "source": "exa",
                }
            ],
            "provider_metadata": {"search_type": "auto"},
        }

    monkeypatch.setattr(search, "_search_tavily", _fake_search_tavily)
    monkeypatch.setattr(search, "_search_exa", _fake_search_exa)

    payload = asyncio.run(
        search.search_web(
            "gia vang sjc",
            limit=4,
            domains=["sjc.com.vn"],
            freshness="day",
        )
    )

    assert payload["ok"] is True
    assert payload["provider"] == "exa"
    assert "tavily_failed" in payload["warnings"]
    assert payload["provider_attempts"][0]["provider"] == "tavily"
    assert payload["provider_attempts"][1]["provider"] == "exa"


def test_search_web_fails_cleanly_when_no_provider_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    payload = asyncio.run(search.search_web("gia vang sjc"))

    assert payload["ok"] is False
    assert payload["error"] == "search_provider_unavailable"
    assert "TAVILY_API_KEY" in payload["message"]
