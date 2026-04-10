from __future__ import annotations

import fnmatch
import logging
from typing import TYPE_CHECKING

from nanobot.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.tools.mcp_source import McpToolSource

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: list[McpToolSource] = []

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.warning("Tool %s already registered, overwriting", tool.name)
        self._tools[tool.name] = tool

    def add_source(self, source: McpToolSource) -> None:
        self._sources.append(source)
        for tool in source.list_tools():
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self, patterns: list[str] | None = None) -> list[Tool]:
        tools = list(self._tools.values())
        if patterns is None:
            return tools
        return [t for t in tools if self._matches(t.name, patterns)]

    def list_openai_specs(self, patterns: list[str] | None = None) -> list[dict]:
        return [t.to_openai_spec() for t in self.list_tools(patterns)]

    async def call(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return await tool.call(args)

    @staticmethod
    def _matches(name: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(name, p) for p in patterns)
