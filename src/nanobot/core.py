from __future__ import annotations

import json
from typing import Any

from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig
from nanobot.llm import LlmClient
from nanobot.mcp_hub import McpHub
from nanobot.memory import ConversationStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore


def scoped_chat_id(channel: str, chat_id: str) -> str:
    return f"{channel}:{chat_id}"


def unscoped_chat_id(scoped: str) -> tuple[str, str]:
    channel, _, chat = scoped.partition(":")
    return channel, chat


class BotCore:
    def __init__(self, config: AppConfig, channels: dict[str, Any]) -> None:
        self.config = config
        self.channels = channels
        self.llm = LlmClient(config.model)
        self.memory = ConversationStore(config.database_path)
        for server in config.mcp_servers:
            if server.name == "scheduler":
                server.env = dict(server.env)
                server.env.setdefault("SCHEDULER_DB_PATH", config.scheduler_db_path)
        self.mcp = McpHub(config.mcp_servers)
        self.scheduler_store = SchedulerStore(config.scheduler_db_path)
        self.scheduler = SchedulerRunner(
            store=self.scheduler_store,
            on_due_task=self._handle_scheduled_task,
            poll_interval_seconds=config.poll_interval_seconds,
        )

    async def start(self) -> None:
        await self.mcp.start()
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.mcp.stop()

    async def on_incoming(self, message: IncomingMessage) -> None:
        scope = scoped_chat_id(message.channel, message.chat_id)
        await self._process(scope, message.text)

    async def _handle_scheduled_task(self, scoped_id: str, prompt: str) -> None:
        await self._process(scoped_id, f"[scheduled task]\n{prompt}")

    async def _process(self, scope: str, user_text: str) -> None:
        self.memory.add_message(scope, "user", user_text)
        history = self.memory.get_recent_messages(scope, limit=24)
        system = {
            "role": "system",
            "content": (
                f"You are {self.config.assistant_name}, a personal assistant. "
                "When useful, call available tools. "
                "For scheduler actions in current chat, pass chat_id exactly as the current scoped chat id. "
                "Format responses as plain text suitable for Telegram. "
                "Do not use markdown tables, HTML tags, or raw markup."
            ),
        }
        messages = [system, *history]
        tools = self.mcp.list_openai_tools()

        assistant_message = await self.llm.chat(messages=messages, tools=tools)

        while assistant_message.get("tool_calls"):
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_message.get("content") or "",
                    "tool_calls": assistant_message["tool_calls"],
                }
            )
            for tool_call in assistant_message["tool_calls"]:
                fn_name = tool_call["function"]["name"]
                raw_args = tool_call["function"].get("arguments") or "{}"
                args = json.loads(raw_args)
                if fn_name.endswith("__schedule_task") and "chat_id" not in args:
                    args["chat_id"] = scope
                try:
                    result = await self.mcp.call_tool(fn_name, args)
                except Exception as exc:  # pylint: disable=broad-except
                    result = f"Tool call failed: {exc}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": fn_name,
                        "content": result,
                    }
                )
            assistant_message = await self.llm.chat(messages=messages, tools=tools)

        reply = assistant_message.get("content") or "I could not generate a response."
        self.memory.add_message(scope, "assistant", reply)
        await self._send(scope, reply)

    async def _send(self, scope: str, text: str) -> None:
        channel_name, raw_chat_id = unscoped_chat_id(scope)
        channel = self.channels.get(channel_name)
        if channel is None:
            raise KeyError(f"No channel configured for '{channel_name}'")
        await channel.send(raw_chat_id, text)
