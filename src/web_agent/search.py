from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import DEFAULT_TIMEOUT_SECONDS
from .utils import normalize_whitespace

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
EXA_SEARCH_URL = "https://api.exa.ai/search"


def _api_key(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _configured_providers() -> list[str]:
    providers: list[str] = []
    if _api_key("TAVILY_API_KEY"):
        providers.append("tavily")
    if _api_key("EXA_API_KEY"):
        providers.append("exa")
    return providers


def _provider_order(provider: str) -> list[str]:
    normalized = provider.strip().lower() or "auto"
    if normalized == "auto":
        configured = _configured_providers()
        return configured if configured else ["tavily", "exa"]
    if normalized in {"tavily", "exa"}:
        return [normalized]
    raise ValueError(f"Unsupported search provider: {provider}")


def _snippet_from_exa_result(item: dict[str, Any]) -> str:
    highlights = item.get("highlights")
    if isinstance(highlights, list):
        joined = " [...] ".join(str(part).strip() for part in highlights if str(part).strip())
        if joined:
            return normalize_whitespace(joined)
    for key in ("summary", "text"):
        value = normalize_whitespace(str(item.get(key, "")))
        if value:
            return value
    return ""


def _normalize_tavily_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": normalize_whitespace(str(item.get("title", ""))),
        "url": str(item.get("url", "")),
        "snippet": normalize_whitespace(str(item.get("content", "")))[:400],
        "score": item.get("score"),
        "published_date": item.get("published_date"),
        "source": "tavily",
    }


def _normalize_exa_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": normalize_whitespace(str(item.get("title", ""))),
        "url": str(item.get("url", "")),
        "snippet": _snippet_from_exa_result(item)[:400],
        "score": None,
        "published_date": item.get("publishedDate"),
        "source": "exa",
    }


def _filter_results(results: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in results:
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not url.startswith(("http://", "https://")) or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        filtered.append(item)
        if len(filtered) >= limit:
            break
    return filtered


def _start_published_date(freshness: str | None) -> str | None:
    if not freshness:
        return None
    now = datetime.now(UTC)
    lowered = freshness.strip().lower()
    days_map = {
        "day": 1,
        "today": 1,
        "week": 7,
        "month": 30,
    }
    days = days_map.get(lowered)
    if days is None:
        return None
    return (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")


async def _search_tavily(
    query: str,
    *,
    limit: int,
    domains: list[str] | None,
    freshness: str | None,
) -> dict[str, Any]:
    api_key = _api_key("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("missing_api_key:TAVILY_API_KEY")

    finance_tokens = ("giá", "vang", "gold", "xăng", "dầu", "price")
    topic = "finance" if any(token in query.lower() for token in finance_tokens) else "general"
    if freshness in {"day", "today", "week"}:
        topic = "news"

    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "topic": topic,
        "search_depth": "basic",
        "max_results": limit,
        "include_answer": False,
        "include_raw_content": False,
    }
    if domains:
        payload["include_domains"] = domains
    if freshness:
        payload["time_range"] = freshness

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS, headers=headers) as client:
        response = await client.post(TAVILY_SEARCH_URL, json=payload)
        response.raise_for_status()
    data = response.json()
    results = _filter_results(
        [_normalize_tavily_result(item) for item in data.get("results", []) if isinstance(item, dict)],
        limit=limit,
    )
    return {
        "provider": "tavily",
        "results": results,
        "provider_metadata": {
            "topic": topic,
            "search_depth": data.get("auto_parameters", {}).get("search_depth", "basic"),
            "request_id": data.get("request_id"),
        },
    }


async def _search_exa(
    query: str,
    *,
    limit: int,
    domains: list[str] | None,
    freshness: str | None,
) -> dict[str, Any]:
    api_key = _api_key("EXA_API_KEY")
    if not api_key:
        raise RuntimeError("missing_api_key:EXA_API_KEY")

    payload: dict[str, Any] = {
        "query": query,
        "type": "auto",
        "numResults": limit,
        "contents": {
            "text": {
                "maxCharacters": 400,
            }
        },
    }
    if domains:
        payload["includeDomains"] = domains
    start_published_date = _start_published_date(freshness)
    if start_published_date:
        payload["startPublishedDate"] = start_published_date

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS, headers=headers) as client:
        response = await client.post(EXA_SEARCH_URL, json=payload)
        response.raise_for_status()
    data = response.json()
    results = _filter_results(
        [_normalize_exa_result(item) for item in data.get("results", []) if isinstance(item, dict)],
        limit=limit,
    )
    return {
        "provider": "exa",
        "results": results,
        "provider_metadata": {
            "search_type": data.get("searchType", "auto"),
            "request_id": data.get("requestId"),
        },
    }


async def search_web(
    query: str,
    *,
    limit: int = 5,
    language: str = "vi",
    domains: list[str] | None = None,
    freshness: str | None = None,
    provider: str = "auto",
) -> dict[str, Any]:
    del language
    safe_limit = max(1, min(limit, 10))
    attempts: list[dict[str, Any]] = []
    warnings: list[str] = []
    provider_metadata: dict[str, Any] = {}

    try:
        provider_order = _provider_order(provider)
    except ValueError as exc:
        return {
            "ok": False,
            "query": query,
            "provider": None,
            "provider_attempts": [],
            "configured_providers": _configured_providers(),
            "results": [],
            "result_count": 0,
            "warnings": [],
            "error": "unsupported_provider",
            "message": str(exc),
        }

    if provider == "auto" and not _configured_providers():
        return {
            "ok": False,
            "query": query,
            "provider": None,
            "provider_attempts": [
                {"provider": name, "ok": False, "error": "missing_api_key"} for name in provider_order
            ],
            "configured_providers": [],
            "results": [],
            "result_count": 0,
            "warnings": [],
            "error": "search_provider_unavailable",
            "message": "No search provider is configured. Set TAVILY_API_KEY or EXA_API_KEY.",
        }

    last_error = "no_results"
    last_message = "Search provider returned no results."
    for provider_name in provider_order:
        try:
            if provider_name == "tavily":
                result = await _search_tavily(query, limit=safe_limit, domains=domains, freshness=freshness)
            else:
                result = await _search_exa(query, limit=safe_limit, domains=domains, freshness=freshness)
        except Exception as exc:  # pylint: disable=broad-except
            error_text = str(exc)
            attempts.append({"provider": provider_name, "ok": False, "error": error_text})
            if error_text.startswith("missing_api_key:"):
                warnings.append(f"{provider_name}_not_configured")
                last_error = "missing_api_key"
                last_message = error_text
            else:
                warnings.append(f"{provider_name}_failed")
                last_error = "provider_error"
                last_message = error_text
            continue

        results = result["results"]
        attempts.append({"provider": provider_name, "ok": bool(results), "result_count": len(results)})
        if results:
            provider_metadata = result["provider_metadata"]
            return {
                "ok": True,
                "query": query,
                "provider": provider_name,
                "provider_attempts": attempts,
                "configured_providers": _configured_providers(),
                "results": results,
                "result_count": len(results),
                "warnings": warnings,
                "error": None,
                "message": None,
                "provider_metadata": provider_metadata,
            }
        warnings.append(f"{provider_name}_no_results")
        last_error = "no_results"
        last_message = f"{provider_name} returned no results."

    return {
        "ok": False,
        "query": query,
        "provider": None,
        "provider_attempts": attempts,
        "configured_providers": _configured_providers(),
        "results": [],
        "result_count": 0,
        "warnings": warnings,
        "error": last_error,
        "message": last_message,
        "provider_metadata": provider_metadata,
    }


async def search_google(query: str, *, limit: int = 5, language: str = "vi") -> dict[str, Any]:
    return await search_web(query, limit=limit, language=language, provider="auto")
