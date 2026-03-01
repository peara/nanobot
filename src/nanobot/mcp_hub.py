from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
import json
import os
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nanobot.config import McpServerConfig


@dataclass
class ToolBinding:
    server_name: str
    tool_name: str
    schema: dict[str, Any]
    description: str


class McpHub:
    def __init__(self, servers: list[McpServerConfig]) -> None:
        self.servers_cfg = servers
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, ToolBinding] = {}

    async def start(self) -> None:
        for cfg in self.servers_cfg:
            env = os.environ.copy()
            env.update(cfg.env)
            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=env,
            )
            read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            self._sessions[cfg.name] = session

            tools = await session.list_tools()
            for tool in tools.tools:
                key = f"{cfg.name}__{tool.name}"
                self._tools[key] = ToolBinding(
                    server_name=cfg.name,
                    tool_name=tool.name,
                    schema=tool.inputSchema or {"type": "object", "properties": {}},
                    description=tool.description or "",
                )

    async def stop(self) -> None:
        await self._stack.aclose()
        self._sessions.clear()
        self._tools.clear()

    def list_openai_tools(self) -> list[dict]:
        items = []
        for key, tool in self._tools.items():
            items.append(
                {
                    "type": "function",
                    "function": {
                        "name": key,
                        "description": tool.description,
                        "parameters": tool.schema,
                    },
                }
            )
        return items

    async def call_tool(self, namespaced_tool_name: str, arguments: dict[str, Any]) -> str:
        if namespaced_tool_name not in self._tools:
            raise KeyError(f"Unknown MCP tool: {namespaced_tool_name}")
        binding = self._tools[namespaced_tool_name]
        session = self._sessions[binding.server_name]
        result = await session.call_tool(binding.tool_name, arguments)
        return self._normalize_result(result)

    @staticmethod
    def _normalize_result(result: Any) -> str:
        # MCP tool responses often come as content parts; flatten to text.
        if hasattr(result, "content"):
            parts = []
            for part in result.content:
                text = getattr(part, "text", None)
                if text is not None:
                    parts.append(text)
                else:
                    parts.append(json.dumps(part.model_dump(), ensure_ascii=True))
            return "\n".join(parts).strip()
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=True)
        return str(result)
