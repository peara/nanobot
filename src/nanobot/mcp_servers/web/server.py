from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from web_agent.config import DEFAULT_QUALITY_THRESHOLD
from web_agent.dependencies import capabilities
from web_agent.search import search_google
from web_agent.search import search_web as search_web_impl
from web_agent.service import WebAgentTool, save_result_payload

logger = logging.getLogger(__name__)

mcp = FastMCP("nanobot-web")


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
    )


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": True, "result": payload}
    normalized = dict(payload)
    normalized.setdefault("ok", "error" not in normalized)
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
        return _attach_saved_outputs(
            payload,
            operation="read",
            url=url,
            save_outputs=_default_save_outputs() if save_outputs is None else save_outputs,
        )
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

    - click:  {"action": "click", "target": "..."}
    - type:   {"action": "type", "target": "...", "text": "..."}
    - select: {"action": "select", "target": "...", "value": "..."}
    - scroll: {"action": "scroll", "amount": 500} OR {"action": "scroll", "until_text": "..."}
    - wait_for: {"action": "wait_for", "selector": "..."} OR {"action": "wait_for", "text": "..."}

    "target" can be:
    - CSS selector: "#search", ".btn-primary", "input[name='q']"
    - Button/link text from snapshot: "Submit", "Next"
    - Placeholder text from snapshot: "探す"
    - Input name attribute: use CSS "[name='p']" or the placeholder text

    Example steps:
    [{"action": "type", "target": "探す", "text": "search query"}, {"action": "click", "target": "検索"}]

    Use snapshot_page first to discover available targets from buttons[], inputs[], and candidate_actions.
    """
    try:
        tool = _build_tool(quality_threshold=quality_threshold, headless=headless)
        payload = _normalize_payload(await tool.interact(url, steps=steps))
        return _attach_saved_outputs(
            payload,
            operation="interact",
            url=url,
            save_outputs=_default_save_outputs() if save_outputs is None else save_outputs,
        )
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_payload("interact", url, exc)


if __name__ == "__main__":
    mcp.run()
