from __future__ import annotations

import fnmatch
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from nanobot.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.tools.mcp_source import McpToolSource
    from nanobot.tools.stats import ToolStatsStore

logger = logging.getLogger(__name__)


def _clip(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


class ToolRegistry:
    def __init__(self, stats_store: ToolStatsStore | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: list[McpToolSource] = []
        self._stats_store = stats_store

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

    async def call(
        self,
        name: str,
        args: dict,
        scope: str | None = None,
        run_id: str | None = None,
    ) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")

        started_at = datetime.now(timezone.utc).isoformat()
        start_time = time.monotonic()
        success = True
        error_preview: str | None = None
        result: str = ""

        try:
            result = await tool.call(args)
            return result
        except Exception as e:
            success = False
            error_preview = _clip(str(e))
            raise
        finally:
            if self._stats_store and scope:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                input_preview = _clip(str(args)) if args else None
                output_chars = len(result) if result else None
                self._stats_store.record_call(
                    scope=scope,
                    tool_name=name,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    success=success,
                    error_preview=error_preview,
                    input_preview=input_preview,
                    output_chars=output_chars,
                    run_id=run_id,
                )

    @staticmethod
    def _matches(name: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(name, p) for p in patterns)
