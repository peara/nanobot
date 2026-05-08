from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from nanobot.memstore.tools import (
    MemoryDeleteTool,
    MemoryHealthTool,
    MemoryListTool,
    MemorySaveTool,
    MemorySaveTurnTool,
    MemorySearchTool,
    MemoryUpdateTool,
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
        mock_memory.search.return_value = {"results": [{"id": "1", "memory": "test memory"}]}

        tool = MemorySearchTool(mock_vs)
        result = await tool.call({"query": "test", "user_id": "user1"})
        data = json.loads(result)
        assert "results" in data
        call_kwargs = mock_memory.search.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"

    @pytest.mark.asyncio
    async def test_search_with_limit(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.search.return_value = {"results": []}

        tool = MemorySearchTool(mock_vs)
        await tool.call({"query": "test", "user_id": "user1", "limit": 10})
        mock_memory.search.assert_called_once()
        call_kwargs = mock_memory.search.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"
        assert call_kwargs.kwargs.get("limit") == 10

    @pytest.mark.asyncio
    async def test_search_with_agent_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.search.return_value = {"results": []}

        tool = MemorySearchTool(mock_vs)
        await tool.call({"query": "test", "user_id": "user1", "agent_id": "agent-42"})
        mock_memory.search.assert_called_once()
        call_kwargs = mock_memory.search.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"
        assert call_kwargs.kwargs.get("filters", {}).get("agent_id") == "agent-42"

    @pytest.mark.asyncio
    async def test_search_with_filters_json_passthrough(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.search.return_value = {"results": []}

        tool = MemorySearchTool(mock_vs)
        filters = {"category": {"in": ["billing"]}}
        await tool.call({"query": "test", "user_id": "user1", "filters_json": json.dumps(filters)})
        mock_memory.search.assert_called_once()


class TestMemorySaveTool:
    @pytest.mark.asyncio
    async def test_save_message(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = {"id": "mem1"}

        tool = MemorySaveTool(mock_vs)
        result = await tool.call({"text": "remember this", "user_id": "user1", "role": "user"})
        data = json.loads(result)
        assert data["id"] == "mem1"

    @pytest.mark.asyncio
    async def test_save_with_agent_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = {"id": "mem1"}

        tool = MemorySaveTool(mock_vs)
        await tool.call({"text": "test", "user_id": "user1", "agent_id": "agent-42"})
        mock_memory.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_with_expiration_days(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = {"id": "mem1"}

        tool = MemorySaveTool(mock_vs)
        await tool.call({"text": "test", "user_id": "user1", "expiration_days": 30})
        call_kwargs = mock_memory.add.call_args
        assert "expiration_date" in call_kwargs.kwargs

    @pytest.mark.asyncio
    async def test_save_with_run_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = {"id": "mem1"}

        tool = MemorySaveTool(mock_vs)
        await tool.call({"text": "test", "user_id": "user1", "run_id": "run-99"})
        call_kwargs = mock_memory.add.call_args
        assert call_kwargs.kwargs.get("run_id") == "run-99"


class TestMemorySaveTurnTool:
    @pytest.mark.asyncio
    async def test_save_turn(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = [{"id": "mem2"}]

        tool = MemorySaveTurnTool(mock_vs)
        result = await tool.call({"user_id": "user1", "user_text": "hello", "assistant_text": "hi there"})
        data = json.loads(result)
        assert "ok" in data or "id" in data

    @pytest.mark.asyncio
    async def test_save_turn_with_agent_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = [{"id": "mem2"}]

        tool = MemorySaveTurnTool(mock_vs)
        await tool.call({"user_id": "user1", "user_text": "hello", "assistant_text": "hi", "agent_id": "agent-42"})
        mock_memory.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_turn_with_run_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.add.return_value = [{"id": "mem2"}]

        tool = MemorySaveTurnTool(mock_vs)
        await tool.call({"user_id": "user1", "user_text": "hello", "assistant_text": "hi", "run_id": "run-99"})
        call_kwargs = mock_memory.add.call_args
        assert call_kwargs.kwargs.get("run_id") == "run-99"


class TestMemoryListTool:
    @pytest.mark.asyncio
    async def test_list_returns_results(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.get_all.return_value = {"results": [{"id": "1", "memory": "test"}]}

        tool = MemoryListTool(mock_vs)
        result = await tool.call({"user_id": "user1"})
        data = json.loads(result)
        assert "results" in data
        mock_memory.get_all.assert_called_once()
        call_kwargs = mock_memory.get_all.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"

    @pytest.mark.asyncio
    async def test_list_with_agent_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.get_all.return_value = {"results": []}

        tool = MemoryListTool(mock_vs)
        await tool.call({"user_id": "user1", "agent_id": "agent-42"})
        call_kwargs = mock_memory.get_all.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"
        assert call_kwargs.kwargs.get("agent_id") == "agent-42"
        assert call_kwargs.kwargs.get("filters", {}).get("agent_id") == "agent-42"

    @pytest.mark.asyncio
    async def test_list_with_run_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.get_all.return_value = {"results": []}

        tool = MemoryListTool(mock_vs)
        await tool.call({"user_id": "user1", "run_id": "run-99"})
        call_kwargs = mock_memory.get_all.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"
        assert call_kwargs.kwargs.get("run_id") == "run-99"
        assert call_kwargs.kwargs.get("filters", {}).get("run_id") == "run-99"

    @pytest.mark.asyncio
    async def test_list_with_filters_json_passthrough(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.get_all.return_value = {"results": []}

        tool = MemoryListTool(mock_vs)
        filters = {"category": {"in": ["billing"]}}
        await tool.call({"user_id": "user1", "filters_json": json.dumps(filters)})
        call_kwargs = mock_memory.get_all.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"
        assert "category" in call_kwargs.kwargs.get("filters", {})

    @pytest.mark.asyncio
    async def test_list_with_limit(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.get_all.return_value = {"results": []}

        tool = MemoryListTool(mock_vs)
        await tool.call({"user_id": "user1", "limit": 100})
        call_kwargs = mock_memory.get_all.call_args
        assert call_kwargs.kwargs.get("user_id") == "user1"
        assert call_kwargs.kwargs.get("limit") == 100


class TestMemoryDeleteTool:
    @pytest.mark.asyncio
    async def test_delete_by_memory_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.delete.return_value = {"message": "Memory deleted successfully!"}

        tool = MemoryDeleteTool(mock_vs)
        result = await tool.call({"memory_id": "mem-123"})
        data = json.loads(result)
        assert "message" in data or "deleted" in data
        mock_memory.delete.assert_called_once_with("mem-123")

    @pytest.mark.asyncio
    async def test_delete_by_namespace(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.delete_all.return_value = {"message": "Memories deleted successfully!"}

        tool = MemoryDeleteTool(mock_vs)
        await tool.call({"user_id": "user1", "agent_id": "agent-42"})
        mock_memory.delete_all.assert_called_once_with(user_id="user1", agent_id="agent-42")

    @pytest.mark.asyncio
    async def test_delete_no_filter_returns_error(self) -> None:
        mock_vs, _ = _make_mock_vector_store()

        tool = MemoryDeleteTool(mock_vs)
        result = await tool.call({})
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_delete_memory_id_takes_priority(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.delete.return_value = {"message": "Memory deleted successfully!"}

        tool = MemoryDeleteTool(mock_vs)
        await tool.call({"memory_id": "mem-123", "user_id": "user1"})
        mock_memory.delete.assert_called_once_with("mem-123")
        mock_memory.delete_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_memory_id(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.delete.side_effect = AttributeError("'NoneType' object has no attribute 'payload'")

        tool = MemoryDeleteTool(mock_vs)
        result = await tool.call({"memory_id": "nonexistent-id"})
        data = json.loads(result)
        assert "error" in data
        assert not data.get("ok", True)


class TestMemoryUpdateTool:
    @pytest.mark.asyncio
    async def test_update_memory(self) -> None:
        mock_vs, mock_memory = _make_mock_vector_store()
        mock_memory.update.return_value = {"message": "Memory updated successfully!"}

        tool = MemoryUpdateTool(mock_vs)
        result = await tool.call({"memory_id": "mem-123", "text": "Updated content"})
        data = json.loads(result)
        assert "message" in data or "updated" in data
        mock_memory.update.assert_called_once_with("mem-123", "Updated content")

    @pytest.mark.asyncio
    async def test_update_missing_memory_id(self) -> None:
        mock_vs, _ = _make_mock_vector_store()

        tool = MemoryUpdateTool(mock_vs)
        result = await tool.call({"text": "some text"})
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_update_missing_text(self) -> None:
        mock_vs, _ = _make_mock_vector_store()

        tool = MemoryUpdateTool(mock_vs)
        result = await tool.call({"memory_id": "mem-123"})
        data = json.loads(result)
        assert "error" in data


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
        assert registry.has("memory__list")
        assert registry.has("memory__delete")
        assert registry.has("memory__update")
        assert registry.has("memory__health")
