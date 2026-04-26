from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from nanobot.memstore.tools import (
    MemoryHealthTool,
    MemorySaveTool,
    MemorySaveTurnTool,
    MemorySearchTool,
    register_memory_tools,
)
from nanobot.tools.registry import ToolRegistry
from nanobot.vector_store import VectorStore


def _make_mock_vector_store() -> tuple[MagicMock, MagicMock]:
    mock_memory = MagicMock()
    mock_vs = MagicMock(spec=VectorStore)
    mock_vs.get_collection.return_value = mock_memory
    return mock_vs, mock_memory


class TestMemorySearchTool:
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.search.return_value = {"results": [{"id": "1", "text": "test memory"}]}

        tool = MemorySearchTool(mock_vs)
        result = await tool.call({"query": "test", "user_id": "user1"})
        data = json.loads(result)
        assert "results" in data

    @pytest.mark.asyncio
    async def test_search_with_limit(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.search.return_value = {"results": []}

        tool = MemorySearchTool(mock_vs)
        await tool.call({"query": "test", "user_id": "user1", "limit": 10})
        mock_memory.search.assert_called_once()


class TestMemorySaveTool:
    @pytest.mark.asyncio
    async def test_save_message(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = {"id": "mem1"}

        tool = MemorySaveTool(mock_vs)
        result = await tool.call(
            {
                "text": "remember this",
                "user_id": "user1",
                "role": "user",
            }
        )
        data = json.loads(result)
        assert data["id"] == "mem1"


class TestMemorySaveTurnTool:
    @pytest.mark.asyncio
    async def test_save_turn(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = [{"id": "mem2"}]

        tool = MemorySaveTurnTool(mock_vs)
        result = await tool.call(
            {
                "user_id": "user1",
                "user_text": "hello",
                "assistant_text": "hi there",
            }
        )
        data = json.loads(result)
        assert "ok" in data or "id" in data


class TestMemoryHealthTool:
    @pytest.mark.asyncio
    async def test_health_check(self) -> None:
        mock_vs = MagicMock(spec=VectorStore)
        mock_vs.health_check.return_value = {"ok": True, "config_source": "test", "collections": []}

        tool = MemoryHealthTool(mock_vs)
        result = await tool.call({})
        data = json.loads(result)
        assert data["ok"] is True
        assert "config_source" in data


class TestRegisterMemoryTools:
    def test_register_all_tools(self) -> None:
        mock_vs = MagicMock(spec=VectorStore)
        registry = ToolRegistry()

        mock_memory = MagicMock()
        mock_vs.get_collection.return_value = mock_memory
        register_memory_tools(registry, mock_vs)

        assert registry.has("memory__search")
        assert registry.has("memory__save")
        assert registry.has("memory__save_turn")
        assert registry.has("memory__health")
