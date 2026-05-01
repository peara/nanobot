from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from web_agent.cache import Cache
from web_agent.config import DEFAULT_QUALITY_THRESHOLD
from web_agent.dependencies import capabilities
from web_agent.search import search_google
from web_agent.search import search_web as search_web_impl
from web_agent.service import DomainChromeCache, WebAgentTool, save_result_payload

logger = logging.getLogger(__name__)

mcp = FastMCP("nanobot-web")

_chrome_cache = DomainChromeCache(cache=Cache(max_entries=50))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _default_headless() -> bool:
    return _env_bool("WEB_AGENT_HEADLESS", True)


def _default_save_outputs() -> bool:
    return _env_bool("WEB_AGENT_SAVE_OUTPUTS", True)


def _default_quality_threshold() -> float:
    raw = os.environ.get("WEB_AGENT_QUALITY_THRESHOLD")
    if raw is None:
        return DEFAULT_QUALITY_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid WEB_AGENT_QUALITY_THRESHOLD=%s; using default.", raw)
        return DEFAULT_QUALITY_THRESHOLD


def _build_tool(*, quality_threshold: float | None, headless: bool | None) -> WebAgentTool:
    return WebAgentTool(
        quality_threshold=quality_threshold if quality_threshold is not None else _default_quality_threshold(),
        headless=_default_headless() if headless is None else headless,
        chrome_cache=_chrome_cache,
    )


def _normalize_payload(payload: Any, *, strip_debug: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": True, "result": payload}
    normalized = dict(payload)
    normalized.setdefault("ok", "error" not in normalized)
    if strip_debug:
        normalized = {k: v for k, v in normalized.items() if not k.startswith("_")}
    return normalized


def _attach_saved_outputs(payload: dict[str, Any], *, operation: str, url: str, save_outputs: bool) -> dict[str, Any]:
    if not save_outputs or not payload.get("ok"):
        return payload
    try:
        payload.setdefault("saved_outputs", save_result_payload(operation, url, payload))
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to persist web-agent output operation=%s url=%s", operation, url)
        payload["saved_outputs_error"] = str(exc)
    return payload


def _failure_payload(operation: str, url: str, exc: Exception) -> dict[str, Any]:
    logger.exception("web-agent tool failed operation=%s url=%s", operation, url)
    return {
        "ok": False,
        "operation": operation,
        "url": url,
        "error": "tool_failed",
        "message": str(exc),
    }


@mcp.tool()
def web_health() -> dict[str, Any]:
    """Report web-agent dependency readiness and default runtime settings."""
    return {
        "ok": True,
        "defaults": {
            "headless": _default_headless(),
            "quality_threshold": _default_quality_threshold(),
            "save_outputs": _default_save_outputs(),
        },
        "capabilities": capabilities(),
    }


@mcp.tool()
async def search_web(
    query: str,
    limit: int = 5,
    language: str = "vi",
    domains: list[str] | None = None,
    freshness: str | None = None,
    provider: str = "auto",
) -> dict[str, Any]:
    """Search the web via Tavily or Exa and return candidate result URLs before reading a page."""
    try:
        return _normalize_payload(
            await search_web_impl(
                query,
                limit=limit,
                language=language,
                domains=domains,
                freshness=freshness,
                provider=provider,
            )
        )
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_payload("search_web", query, exc)


@mcp.tool()
async def search_google_web(query: str, limit: int = 5, language: str = "vi") -> dict[str, Any]:
    """Backward-compatible alias for search_web; provider selection is automatic."""
    try:
        payload = await search_google(query, limit=limit, language=language)
        normalized = _normalize_payload(payload)
        normalized.setdefault("warnings", [])
        normalized["warnings"] = list(normalized["warnings"]) + ["search_google_web_alias"]
        return normalized
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_payload("search_google", query, exc)


@mcp.tool()
async def read_page(
    url: str,
    quality_threshold: float | None = None,
    headless: bool | None = None,
    save_outputs: bool | None = None,
) -> dict[str, Any]:
    """Read a webpage and return the best extracted content, links, markdown, and trace."""
    try:
        tool = _build_tool(quality_threshold=quality_threshold, headless=headless)
        payload = _normalize_payload(await tool.read(url))
        do_save = _default_save_outputs() if save_outputs is None else save_outputs
        payload = _attach_saved_outputs(payload, operation="read", url=url, save_outputs=do_save)
        return _normalize_payload(payload, strip_debug=True)
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_payload("read", url, exc)


@mcp.tool()
async def snapshot_page(url: str, headless: bool | None = None) -> dict[str, Any]:
    """Open a webpage and return elements for browser interaction.

    Returns:
    - visible_text: Page text content
    - buttons: List of {text, type, aria_label} - use "text" as target for click
    - links: List of {text, href} - use "text" as target for click
    - inputs: List of {name, type, placeholder, label} - use "name", "placeholder", or CSS selector as target
    - candidate_actions: Suggested actions like "click:Next" - use quoted text as target

    Use this BEFORE interact_page to discover element identifiers for the "target" parameter.
    """
    try:
        tool = _build_tool(quality_threshold=None, headless=headless)
        return _normalize_payload(await tool.snapshot(url))
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_payload("snapshot", url, exc)


@mcp.tool()
async def interact_page(
    url: str,
    steps: list[dict[str, Any]] | None = None,
    quality_threshold: float | None = None,
    headless: bool | None = None,
    save_outputs: bool | None = None,
) -> dict[str, Any]:
    """Run browser actions and extract the page content.

    Steps format: [{"action": "...", ...fields...}]
    Each action requires specific fields:

    - click:  {"action": "click", "target": "..."} — clicks an element; auto-detects new tabs
    - type:   {"action": "type", "target": "...", "text": "..."}
    - select: {"action": "select", "target": "...", "value": "..."}
    - scroll: {"action": "scroll", "amount": 500} OR {"action": "scroll", "until_text": "..."}
    - wait_for: {"action": "wait_for", "selector": "..."} OR {"action": "wait_for", "text": "..."}
    - switch_tab: {"action": "switch_tab", "index": N} — switch to background tab by index

    "target" can be:
    - CSS selector: "#search", ".btn-primary", "input[name='q']"
    - Button/link text from snapshot: "Submit", "Next"
    - Placeholder text from snapshot: "探す"
    - Input name attribute: use CSS "[name='p']" or the placeholder text

    When a click opens a new tab (e.g. target="_blank"), the browser automatically
    switches to it and the old tab is saved in background_tabs. Use switch_tab to
    return to a previous tab (index 0 = first background tab).

    Use snapshot_page first to discover available targets from buttons[], inputs[], and candidate_actions.
    """
    try:
        tool = _build_tool(quality_threshold=quality_threshold, headless=headless)
        payload = _normalize_payload(await tool.interact(url, steps=steps))
        do_save = _default_save_outputs() if save_outputs is None else save_outputs
        payload = _attach_saved_outputs(payload, operation="interact", url=url, save_outputs=do_save)
        return _normalize_payload(payload, strip_debug=True)
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_payload("interact", url, exc)


@mcp.tool()
def domain_chrome(domain: str) -> dict[str, Any]:
    """Retrieve the stored navigation chrome (header items and links) for a domain.

    When web tools detect repeated navigation elements across calls to the same domain,
    they are removed from the main payload and noted in 'chrome_omitted'. Use this tool
    to retrieve them if needed (e.g., to navigate, log in, or browse categories).

    Args:
        domain: The domain to retrieve chrome for (e.g. "auctions.yahoo.co.jp").
    """
    baseline = _chrome_cache.get_baseline(domain)
    if baseline is None:
        return {
            "ok": False,
            "domain": domain,
            "error": "not_found",
            "message": f"No chrome baseline stored for domain '{domain}'. "
            f"Cached domains: {_chrome_cache.cached_domains}",
        }
    return {
        "ok": True,
        "domain": domain,
        "items": baseline["items"],
        "links": baseline["links"],
    }


if __name__ == "__main__":
    mcp.run()
