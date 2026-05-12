from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from nanobot.vector_store import VectorStore
from nanobot.web_scripts import (
    NanoScriptInvalidResultError,
    NanoScriptRunner,
    NanoScriptRuntimeError,
    NanoScriptValidationError,
    NanoScriptValidator,
    WebScriptStore,
    WebScriptVectorStore,
)
from nanobot.web_scripts.runner import RESERVED_RESPONSE_KEYS
from web_agent.cache import Cache
from web_agent.config import DEFAULT_QUALITY_THRESHOLD
from web_agent.dependencies import capabilities
from web_agent.search import search_google
from web_agent.search import search_web as search_web_impl
from web_agent.service import DomainChromeCache, WebAgentTool, save_result_payload

logger = logging.getLogger(__name__)

mcp = FastMCP("nanobot-web")

_chrome_cache = DomainChromeCache(cache=Cache(max_entries=50))
_script_store_cache: tuple[str, WebScriptStore] | None = None
_script_vector_cache: tuple[str, WebScriptVectorStore | None] | None = None


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


def _script_db_path() -> str:
    return os.environ.get("WEB_SCRIPT_DB_PATH", "./data/web_scripts.db")


def _script_vector_config_path() -> str | None:
    return os.environ.get("WEB_SCRIPT_VECTOR_CONFIG") or os.environ.get("MEM0_CONFIG_PATH")


def _build_script_store() -> WebScriptStore:
    global _script_store_cache  # pylint: disable=global-statement
    db_path = _script_db_path()
    if _script_store_cache is None or _script_store_cache[0] != db_path:
        _script_store_cache = (db_path, WebScriptStore(db_path))
    return _script_store_cache[1]


def _build_script_vector_store() -> WebScriptVectorStore | None:
    global _script_vector_cache  # pylint: disable=global-statement
    config_path = _script_vector_config_path()
    if not config_path:
        return None
    if _script_vector_cache is not None and _script_vector_cache[0] == config_path:
        return _script_vector_cache[1]
    if not Path(config_path).exists():
        logger.warning("WEB_SCRIPT_VECTOR_CONFIG not found: %s", config_path)
        _script_vector_cache = (config_path, None)
        return None
    try:
        vector_store = WebScriptVectorStore(VectorStore(config_path))
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to initialize web script vector store config=%s", config_path)
        _script_vector_cache = (config_path, None)
        return None
    _script_vector_cache = (config_path, vector_store)
    return vector_store


def _script_metadata(script: Any) -> dict[str, Any]:
    return script.as_dict(include_code=False)


def _contains_response_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in RESERVED_RESPONSE_KEYS:
                return True
            if _contains_response_key(item):
                return True
    if isinstance(value, list):
        return any(_contains_response_key(item) for item in value)
    return False


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
            "script_db_path": _script_db_path(),
            "script_vector_config": _script_vector_config_path(),
        },
        "capabilities": capabilities(),
    }


@mcp.tool()
def create_script(
    name: str,
    description: str,
    code: str,
    params_schema: dict[str, Any] | None = None,
    result_schema: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create or update a reusable Python NanoScript browser data extractor.

    Scripts execute browser/page extraction code and return structured data only.
    Do not include answer text, formatting rules, language rules, summaries, or
    response templates. Use skills for reusable workflow, routing, param mapping,
    and user-facing response policy. If params_schema is empty, treat params as
    unspecified/flexible rather than unsupported.
    """
    if not name.strip() or not description.strip() or not code.strip():
        return {"ok": False, "error": "invalid_input", "message": "name, description, and code are required"}
    if _contains_response_key(params_schema or {}) or _contains_response_key(result_schema or {}):
        return {
            "ok": False,
            "error": "invalid_script",
            "message": "script schemas must describe extracted data, not response text or answer templates",
        }
    try:
        NanoScriptValidator().validate(code)
        store = _build_script_store()
        script = store.create(
            name=name.strip(),
            description=description.strip(),
            code=code,
            params_schema=params_schema or {},
            result_schema=result_schema or {},
            tags=tags or [],
            overwrite=overwrite,
        )
    except (NanoScriptValidationError, ValueError) as exc:
        return {"ok": False, "error": "invalid_script", "message": str(exc)}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to create web script name=%s", name)
        return {"ok": False, "error": "store_failed", "message": str(exc)}

    vector_indexed = False
    vector_error: str | None = None
    vector_store = _build_script_vector_store()
    if vector_store is not None:
        try:
            vector_id = vector_store.store_script(script)
            updated = store.update(script.id, vector_id=vector_id)
            if updated is not None:
                script = updated
            vector_indexed = True
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to index web script name=%s", script.name)
            vector_error = str(exc)

    payload: dict[str, Any] = {
        "ok": True,
        "script": _script_metadata(script),
        "vector_indexed": vector_indexed,
    }
    if vector_error is not None:
        payload["vector_error"] = vector_error
    return payload


@mcp.tool()
def search_scripts(query: str, limit: int = 5) -> dict[str, Any]:
    """Find reusable browser data extraction scripts for a task.

    Search results expose metadata and schemas for the agent/skills to decide whether
    to invoke a script with params. Empty params_schema means unspecified/flexible,
    not that params are unsupported.
    """
    store = _build_script_store()
    scripts = []
    used_vector = False
    vector_store = _build_script_vector_store()
    if vector_store is not None:
        try:
            names = vector_store.search_scripts(query, limit=limit)
            for script_name in names:
                script = store.get_by_name(script_name)
                if script is not None and script.is_active:
                    scripts.append(script)
            used_vector = True
        except Exception:  # pylint: disable=broad-except
            logger.exception("Failed to search web scripts via vector store query=%s", query)
            scripts = []
            used_vector = False
    if not scripts:
        scripts = store.search(query, limit=limit)
    return {
        "ok": True,
        "query": query,
        "used_vector": used_vector,
        "scripts": [_script_metadata(script) for script in scripts[:limit]],
    }


@mcp.tool()
async def invoke_script(
    name: str,
    params: dict[str, Any] | None = None,
    headless: bool | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Invoke a reusable browser data extraction script and return structured data only."""
    store = _build_script_store()
    script = store.get_by_name(name)
    if script is None or not script.is_active:
        return {"ok": False, "error": "not_found", "message": f"Web script not found: {name}"}
    try:
        runner = NanoScriptRunner(headless=_default_headless() if headless is None else headless)
        return await runner.run(script, params=params or {}, timeout_seconds=timeout_seconds)
    except TimeoutError:
        return {
            "ok": False,
            "script": name,
            "error": "timeout",
            "message": f"Script timed out after {timeout_seconds}s",
        }
    except NanoScriptInvalidResultError as exc:
        return {"ok": False, "script": name, "error": "invalid_result", "message": str(exc)}
    except NanoScriptValidationError as exc:
        return {"ok": False, "script": name, "error": "invalid_script", "message": str(exc)}
    except NanoScriptRuntimeError as exc:
        return {"ok": False, "script": name, "error": "runtime_error", "message": str(exc)}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to invoke web script name=%s", name)
        return {"ok": False, "script": name, "error": "runtime_error", "message": str(exc)}


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
