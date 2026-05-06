from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from nanobot.tools.base import Tool
from nanobot.vector_store import COLLECTION_MEMORIES, VectorStore

if TYPE_CHECKING:
    from mem0 import Memory

logger = logging.getLogger(__name__)


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


def _search_with_compat(
    mem: Memory, query: str, user_id: str, limit: int, filters: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Internal compatibility wrapper for mem.search with optional filters.

    Keeps backward compatibility with older mem.search signatures while
    supporting the new filters-based API used by MEM0 v3.
    """
    # If explicit filters are provided, try the new API path first
    if filters:
        try:
            return mem.search(query=query, filters=filters, top_k=limit)  # type: ignore[call-arg]
        except TypeError:
            try:
                return mem.search(query=query, filters=filters, limit=limit)  # type: ignore[call-arg]
            except TypeError:
                # Fallback to a minimal signature preserving user_id
                return mem.search(query=query, user_id=user_id, limit=limit)  # type: ignore[call-arg]
    # No advanced filters provided; fallback to traditional signature
    try:
        return mem.search(query=query, user_id=user_id, limit=limit)  # type: ignore[call-arg]
    except TypeError:
        return mem.search(query=query, filters={"user_id": user_id}, limit=limit)  # type: ignore[call-arg]


class MemorySearchTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._memories = vector_store.get_collection(COLLECTION_MEMORIES)

    @property
    def name(self) -> str:
        return "memory__search"

    @property
    def description(self) -> str:
        return "Search relevant long-term memories for a user."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "user_id": {
                    "type": "string",
                    "description": "User ID (e.g., 'telegram:123')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 5)",
                    "default": 5,
                },
                # MEM0 v3 enhancements
                "agent_id": {
                    "type": "string",
                    "description": "Agent/task namespace (optional)",
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by categories (optional)",
                },
                "created_after": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) for delta filtering (optional)",
                },
                "filters_json": {
                    "type": "string",
                    "description": "Raw JSON filters object for advanced queries (optional)",
                },
            },
            "required": ["query", "user_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", ""))
        user_id = str(args.get("user_id", ""))
        limit = int(args.get("limit", 5))
        safe_limit = max(1, min(limit, 20))

        # Build base and optional filters according to MEM0 v3 API
        filters: dict[str, Any] = {"user_id": user_id}

        agent_id = args.get("agent_id")
        if agent_id:
            filters["agent_id"] = str(agent_id)

        categories = args.get("categories")
        if isinstance(categories, list) and categories:
            filters["categories"] = {"in": categories}

        created_after = args.get("created_after")
        if isinstance(created_after, str) and created_after:
            filters["created_at"] = {"gte": created_after}

        # Merge raw JSON filters, if provided
        filters_json = args.get("filters_json")
        if isinstance(filters_json, str) and filters_json:
            parsed = _parse_json(filters_json)
            # Merge/overlay without mutating existing structure unexpectedly
            for k, v in parsed.items():
                filters[k] = v

        logger.debug("MemorySearchTool filters being used: %s", filters)

        result = _search_with_compat(self._memories, query=query, user_id=user_id, limit=safe_limit, filters=filters)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=True)
        return json.dumps({"results": result}, ensure_ascii=True)


class MemorySaveTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._memories = vector_store.get_collection(COLLECTION_MEMORIES)

    @property
    def name(self) -> str:
        return "memory__save"

    @property
    def description(self) -> str:
        return "Save a single memory candidate as a chat message."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to save as memory",
                },
                "user_id": {
                    "type": "string",
                    "description": "User ID (e.g., 'telegram:123')",
                },
                "role": {
                    "type": "string",
                    "description": "Role of the message (default: 'user')",
                    "default": "user",
                },
                "metadata_json": {
                    "type": "string",
                    "description": "Optional JSON metadata to attach",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent/task namespace (optional)",
                },
                "run_id": {
                    "type": "string",
                    "description": "Session/run identifier (optional)",
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Memory categories for organization (optional)",
                },
                "expiration_days": {
                    "type": "integer",
                    "description": "Days until auto-cleanup (optional)",
                },
            },
            "required": ["text", "user_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        text = str(args.get("text", ""))
        user_id = str(args.get("user_id", ""))
        role = str(args.get("role", "user"))
        metadata = _parse_json(args.get("metadata_json"))

        agent_id = args.get("agent_id")
        run_id = args.get("run_id")
        categories = args.get("categories")
        expiration_days = args.get("expiration_days")
        expiration_date: str | None = None
        if isinstance(expiration_days, int):
            expiration_date = (date.today() + timedelta(days=expiration_days)).isoformat()

        messages = [{"role": role, "content": text}]
        add_kwargs: dict[str, Any] = {"user_id": user_id, "metadata": metadata}
        if agent_id is not None:
            add_kwargs["agent_id"] = agent_id
        if run_id is not None:
            add_kwargs["run_id"] = run_id
        if expiration_date is not None:
            add_kwargs["expiration_date"] = expiration_date
        if isinstance(categories, list) and categories:
            add_kwargs["categories"] = categories

        logger.debug("MemorySaveTool add parameters: %s", add_kwargs)
        result = self._memories.add(messages, **add_kwargs)  # type: ignore[call-arg]
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=True)
        return json.dumps({"ok": True, "result": result}, ensure_ascii=True)


class MemorySaveTurnTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._memories = vector_store.get_collection(COLLECTION_MEMORIES)

    @property
    def name(self) -> str:
        return "memory__save_turn"

    @property
    def description(self) -> str:
        return "Save a user+assistant turn so mem0 can extract durable facts."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "User ID (e.g., 'telegram:123')",
                },
                "user_text": {
                    "type": "string",
                    "description": "User message text",
                },
                "assistant_text": {
                    "type": "string",
                    "description": "Assistant message text",
                },
                "metadata_json": {
                    "type": "string",
                    "description": "Optional JSON metadata to attach",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent/task namespace (optional)",
                },
                "run_id": {
                    "type": "string",
                    "description": "Session/run identifier (optional)",
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Memory categories for organization (optional)",
                },
            },
            "required": ["user_id", "user_text", "assistant_text"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        user_id = str(args.get("user_id", ""))
        user_text = str(args.get("user_text", ""))
        assistant_text = str(args.get("assistant_text", ""))
        metadata = _parse_json(args.get("metadata_json"))

        # Optional MEM0 v3 fields
        agent_id = args.get("agent_id")
        run_id = args.get("run_id")
        categories = args.get("categories")

        messages = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
        add_kwargs: dict[str, Any] = {"user_id": user_id, "metadata": metadata}
        if agent_id is not None:
            add_kwargs["agent_id"] = agent_id
        if run_id is not None:
            add_kwargs["run_id"] = run_id
        if isinstance(categories, list) and categories:
            add_kwargs["categories"] = categories

        logger.debug("MemorySaveTurnTool add parameters: %s", add_kwargs)
        result = self._memories.add(messages, **add_kwargs)  # type: ignore[call-arg]
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=True)
        return json.dumps({"ok": True, "result": result}, ensure_ascii=True)


class MemoryHealthTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._vector_store = vector_store

    @property
    def name(self) -> str:
        return "memory__health"

    @property
    def description(self) -> str:
        return "Return memory backend health and config source."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    async def call(self, args: dict[str, Any]) -> str:
        return json.dumps(self._vector_store.health_check(), ensure_ascii=True)


def register_memory_tools(registry: Any, vector_store: VectorStore) -> None:
    registry.register(MemorySearchTool(vector_store))
    registry.register(MemorySaveTool(vector_store))
    registry.register(MemorySaveTurnTool(vector_store))
    registry.register(MemoryHealthTool(vector_store))
