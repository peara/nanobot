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
    if filters:
        try:
            return mem.search(query=query, filters=filters, top_k=limit)  # type: ignore[call-arg]
        except TypeError:
            try:
                return mem.search(query=query, filters=filters, limit=limit)  # type: ignore[call-arg]
            except TypeError:
                return mem.search(query=query, user_id=user_id, limit=limit)  # type: ignore[call-arg]
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
        return "Semantic search over long-term memories. Returns the most relevant memories for a query."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for",
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
                "agent_id": {
                    "type": "string",
                    "description": "Restrict to memories saved under this agent/task namespace (optional)",
                },
                "filters_json": {
                    "type": "string",
                    "description": (
                        "Advanced mem0 filters as JSON string (optional). "
                        'Equality: {"category":"work"}. '
                        'Operators: {"priority":{"gte":5}}, {"category":{"in":["work","personal"]}}. '
                        'Logic: {"AND":[{"user_id":"alice"},{"category":"work"}]}. '
                        "user_id and agent_id params are merged in unless already present in filters."
                    ),
                },
            },
            "required": ["query", "user_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", ""))
        user_id = str(args.get("user_id", ""))
        limit = int(args.get("limit", 5))
        safe_limit = max(1, min(limit, 20))

        filters_json = args.get("filters_json")
        if isinstance(filters_json, str) and filters_json:
            filters = _parse_json(filters_json)
            if "user_id" not in filters:
                filters["user_id"] = user_id
            if args.get("agent_id") and "agent_id" not in filters:
                filters["agent_id"] = str(args["agent_id"])
        else:
            filters = {"user_id": user_id}
            agent_id = args.get("agent_id")
            if agent_id:
                filters["agent_id"] = str(agent_id)

        logger.debug("MemorySearchTool filters: %s", filters)

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
        return "Save a fact or observation to long-term memory. mem0 deduplicates and extracts key info automatically."

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
                    "description": "Message role (default: 'user')",
                    "default": "user",
                },
                "metadata_json": {
                    "type": "string",
                    "description": "JSON metadata to attach (optional)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent/task namespace — isolates memories to a specific task (optional)",
                },
                "run_id": {
                    "type": "string",
                    "description": "Session/run identifier (optional)",
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
        return "Save a conversation turn (user + assistant) so mem0 can extract durable facts from it."

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
                    "description": "JSON metadata to attach (optional)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent/task namespace — isolates memories to a specific task (optional)",
                },
                "run_id": {
                    "type": "string",
                    "description": "Session/run identifier (optional)",
                },
            },
            "required": ["user_id", "user_text", "assistant_text"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        user_id = str(args.get("user_id", ""))
        user_text = str(args.get("user_text", ""))
        assistant_text = str(args.get("assistant_text", ""))
        metadata = _parse_json(args.get("metadata_json"))

        agent_id = args.get("agent_id")
        run_id = args.get("run_id")

        messages = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
        add_kwargs: dict[str, Any] = {"user_id": user_id, "metadata": metadata}
        if agent_id is not None:
            add_kwargs["agent_id"] = agent_id
        if run_id is not None:
            add_kwargs["run_id"] = run_id

        logger.debug("MemorySaveTurnTool add parameters: %s", add_kwargs)
        result = self._memories.add(messages, **add_kwargs)  # type: ignore[call-arg]
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=True)
        return json.dumps({"ok": True, "result": result}, ensure_ascii=True)


class MemoryListTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._memories = vector_store.get_collection(COLLECTION_MEMORIES)

    @property
    def name(self) -> str:
        return "memory__list"

    @property
    def description(self) -> str:
        return "List all memories in a namespace. No semantic search — returns everything matching the given filters."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "User ID (e.g., 'telegram:123')",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Restrict to memories saved under this agent/task namespace (optional)",
                },
                "run_id": {
                    "type": "string",
                    "description": "Restrict to memories from this session/run (optional)",
                },
                "filters_json": {
                    "type": "string",
                    "description": (
                        "Advanced mem0 filters as JSON string (optional). "
                        'Equality: {"category":"work"}. '
                        'Operators: {"priority":{"gte":5}}, {"category":{"in":["work","personal"]}}. '
                        'Logic: {"AND":[{"user_id":"alice"},{"category":"work"}]}. '
                        "user_id, agent_id, run_id params are merged in unless already present in filters."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 50)",
                    "default": 50,
                },
            },
            "required": ["user_id"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        user_id = str(args.get("user_id", ""))
        limit = int(args.get("limit", 50))
        safe_limit = max(1, min(limit, 200))

        filters_json = args.get("filters_json")
        if isinstance(filters_json, str) and filters_json:
            filters = _parse_json(filters_json)
            if "user_id" not in filters:
                filters["user_id"] = user_id
        else:
            filters = {"user_id": user_id}

        agent_id = args.get("agent_id")
        if agent_id and "agent_id" not in filters:
            filters["agent_id"] = str(agent_id)
        run_id = args.get("run_id")
        if run_id and "run_id" not in filters:
            filters["run_id"] = str(run_id)

        logger.debug("MemoryListTool get_all filters: %s", filters)

        get_all_kwargs: dict[str, Any] = {"limit": safe_limit}
        if user_id:
            get_all_kwargs["user_id"] = user_id
        if agent_id:
            get_all_kwargs["agent_id"] = str(agent_id)
        if run_id:
            get_all_kwargs["run_id"] = str(run_id)
        if filters:
            get_all_kwargs["filters"] = filters

        result = self._memories.get_all(**get_all_kwargs)  # type: ignore[call-arg]

        results_list: list[dict[str, Any]]
        if isinstance(result, dict) and "results" in result:
            results_list = result["results"]
        elif isinstance(result, list):
            results_list = result
        else:
            results_list = []

        return json.dumps({"results": results_list}, ensure_ascii=True)


class MemoryDeleteTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._memories = vector_store.get_collection(COLLECTION_MEMORIES)

    @property
    def name(self) -> str:
        return "memory__delete"

    @property
    def description(self) -> str:
        return "Delete a memory by ID, or delete all memories in a namespace (user_id/agent_id/run_id)."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "Delete one memory by its ID (ignores other params)",
                },
                "user_id": {
                    "type": "string",
                    "description": "Delete all memories for this user (optional)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Delete all memories for this agent namespace (optional)",
                },
                "run_id": {
                    "type": "string",
                    "description": "Delete all memories for this run (optional)",
                },
            },
        }

    async def call(self, args: dict[str, Any]) -> str:
        memory_id = args.get("memory_id")
        user_id = args.get("user_id")
        agent_id = args.get("agent_id")
        run_id = args.get("run_id")

        if memory_id:
            logger.debug("MemoryDeleteTool: deleting memory_id=%s", memory_id)
            result = self._memories.delete(str(memory_id))  # type: ignore[call-arg]
            payload = result if isinstance(result, dict) else {"ok": True, "deleted": str(memory_id)}
            return json.dumps(payload, ensure_ascii=True)

        namespace_kwargs: dict[str, str] = {}
        if user_id:
            namespace_kwargs["user_id"] = str(user_id)
        if agent_id:
            namespace_kwargs["agent_id"] = str(agent_id)
        if run_id:
            namespace_kwargs["run_id"] = str(run_id)

        if not namespace_kwargs:
            return json.dumps(
                {"error": "Provide memory_id or at least one of user_id/agent_id/run_id"},
                ensure_ascii=True,
            )

        logger.debug("MemoryDeleteTool: delete_all with %s", namespace_kwargs)
        result = self._memories.delete_all(**namespace_kwargs)  # type: ignore[call-arg]
        return json.dumps(result if isinstance(result, dict) else {"ok": True}, ensure_ascii=True)


class MemoryUpdateTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._memories = vector_store.get_collection(COLLECTION_MEMORIES)

    @property
    def name(self) -> str:
        return "memory__update"

    @property
    def description(self) -> str:
        return "Update the content of an existing memory. The memory is re-embedded with the new text."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to update",
                },
                "text": {
                    "type": "string",
                    "description": "New content for the memory",
                },
            },
            "required": ["memory_id", "text"],
        }

    async def call(self, args: dict[str, Any]) -> str:
        memory_id = str(args.get("memory_id", ""))
        text = str(args.get("text", ""))

        if not memory_id or not text:
            return json.dumps({"error": "Both memory_id and text are required"}, ensure_ascii=True)

        logger.debug("MemoryUpdateTool: updating memory_id=%s", memory_id)
        result = self._memories.update(memory_id, text)  # type: ignore[call-arg]
        return json.dumps(result if isinstance(result, dict) else {"ok": True, "updated": memory_id}, ensure_ascii=True)


class MemoryHealthTool(Tool):
    def __init__(self, vector_store: VectorStore) -> None:
        self._vector_store = vector_store

    @property
    def name(self) -> str:
        return "memory__health"

    @property
    def description(self) -> str:
        return "Check if the memory backend (Qdrant) is reachable and configured."

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
    registry.register(MemoryListTool(vector_store))
    registry.register(MemoryDeleteTool(vector_store))
    registry.register(MemoryUpdateTool(vector_store))
    registry.register(MemoryHealthTool(vector_store))
