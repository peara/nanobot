from __future__ import annotations

import json
import os
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP

try:
    from mem0 import Memory
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("mem0ai is not installed. Install dependencies and restart: uv add mem0ai") from exc

mcp = FastMCP("nanobot-memory")
_MEMORY: Memory | None = None
_MEMORY_SOURCE = "default"


def _memory() -> Memory:
    global _MEMORY, _MEMORY_SOURCE
    if _MEMORY is not None:
        return _MEMORY

    config_path = os.environ.get("MEM0_CONFIG_PATH", "").strip()
    config_json = os.environ.get("MEM0_CONFIG_JSON", "").strip()
    try:
        if config_path:
            with open(config_path, encoding="utf-8") as fh:
                config_data = yaml.safe_load(fh) or {}
            _MEMORY = Memory.from_config(config_data)
            _MEMORY_SOURCE = f"config_file:{config_path}"
        elif config_json:
            _MEMORY = Memory.from_config(json.loads(config_json))
            _MEMORY_SOURCE = "config_json"
        else:
            _MEMORY = Memory()
            _MEMORY_SOURCE = "default"
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(
            "Failed to initialize mem0. Set MEM0_CONFIG_PATH to a valid config file "
            "(recommended), or configure OPENAI_API_KEY for default mem0 settings."
        ) from exc
    return _MEMORY


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON must decode to an object.")
    return value


def _search_with_compat(mem: Memory, query: str, user_id: str, limit: int) -> dict[str, Any]:
    try:
        return mem.search(query=query, user_id=user_id, limit=limit)  # type: ignore[call-arg]
    except TypeError:
        return mem.search(query=query, filters={"user_id": user_id}, limit=limit)  # type: ignore[call-arg]


@mcp.tool()
def memory_health() -> dict[str, Any]:
    """Return memory backend health and config source."""
    _memory()
    return {
        "ok": True,
        "backend": "mem0",
        "config_source": _MEMORY_SOURCE,
    }


@mcp.tool()
def memory_search(query: str, user_id: str, limit: int = 5) -> dict[str, Any]:
    """Search relevant long-term memories for a user."""
    mem = _memory()
    safe_limit = max(1, min(limit, 20))
    result = _search_with_compat(mem, query=query, user_id=user_id, limit=safe_limit)
    if isinstance(result, dict):
        return result
    return {"results": result}


@mcp.tool()
def memory_save(text: str, user_id: str, role: str = "user", metadata_json: str | None = None) -> dict[str, Any]:
    """Save a single memory candidate as a chat message."""
    mem = _memory()
    metadata = _parse_json(metadata_json)
    messages = [{"role": role, "content": text}]
    result = mem.add(messages, user_id=user_id, metadata=metadata)  # type: ignore[call-arg]
    if isinstance(result, dict):
        return result
    return {"ok": True, "result": result}


@mcp.tool()
def memory_save_turn(
    user_id: str,
    user_text: str,
    assistant_text: str,
    metadata_json: str | None = None,
) -> dict[str, Any]:
    """Save a user+assistant turn so mem0 can extract durable facts."""
    mem = _memory()
    metadata = _parse_json(metadata_json)
    messages = [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]
    result = mem.add(messages, user_id=user_id, metadata=metadata)  # type: ignore[call-arg]
    if isinstance(result, dict):
        return result
    return {"ok": True, "result": result}


if __name__ == "__main__":
    mcp.run()
