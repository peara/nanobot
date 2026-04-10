from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nanobot.mcp_hub import McpHub
from nanobot.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.config import McpServerConfig

logger = logging.getLogger(__name__)


class McpToolWrapper(Tool):
    def __init__(self, name: str, description: str, schema: dict[str, Any], mcp_hub: McpHub) -> None:
        self._name = name
        self._description = description
        self._schema = schema
        self._mcp = mcp_hub

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    async def call(self, args: dict[str, Any]) -> str:
        return await self._mcp.call_tool(self._name, args)


class McpToolSource:
    def __init__(self, servers: list[McpServerConfig]) -> None:
        self._mcp = McpHub(servers)
        self._tools: list[Tool] = []

    async def start(self) -> None:
        await self._mcp.start()
        self._tools = self._build_tools()

    async def stop(self) -> None:
        await self._mcp.stop()
        self._tools.clear()

    def list_tools(self) -> list[Tool]:
        return self._tools

    def _build_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for key, binding in self._mcp._tools.items():
            tool = McpToolWrapper(
                name=key,
                description=binding.description,
                schema=binding.schema,
                mcp_hub=self._mcp,
            )
            tools.append(tool)
        return tools
