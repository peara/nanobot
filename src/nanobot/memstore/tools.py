from __future__ import annotations

import json
import logging
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


def _search_with_compat(mem: Memory, query: str, user_id: str, limit: int) -> dict[str, Any]:
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
            },
            "required": ["query", "user_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", ""))
        user_id = str(args.get("user_id", ""))
        limit = int(args.get("limit", 5))
        safe_limit = max(1, min(limit, 20))

        result = _search_with_compat(self._memories, query=query, user_id=user_id, limit=safe_limit)
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
            },
            "required": ["text", "user_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        text = str(args.get("text", ""))
        user_id = str(args.get("user_id", ""))
        role = str(args.get("role", "user"))
        metadata = _parse_json(args.get("metadata_json"))

        messages = [{"role": role, "content": text}]
        result = self._memories.add(messages, user_id=user_id, metadata=metadata)  # type: ignore[call-arg]
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
            },
            "required": ["user_id", "user_text", "assistant_text"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        user_id = str(args.get("user_id", ""))
        user_text = str(args.get("user_text", ""))
        assistant_text = str(args.get("assistant_text", ""))
        metadata = _parse_json(args.get("metadata_json"))

        messages = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
        result = self._memories.add(messages, user_id=user_id, metadata=metadata)  # type: ignore[call-arg]
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
