from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent_run import AgentRun
from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig
from nanobot.context_store import ContextStore
from nanobot.core_plan import process_plan
from nanobot.core_reports import build_context_report, build_full_context_report
from nanobot.core_router import MessageRouter
from nanobot.core_scratchpad import scratchpad_tool_spec
from nanobot.core_utils import (
    SCHEDULED_SYSTEM_MARKER,
    clip,
    command_name,
    human_now,
    scoped_chat_id,
    unscoped_chat_id,
)
from nanobot.hooks import ToolCallEvent, ToolHook, build_default_tool_hooks
from nanobot.llm import LlmClient
from nanobot.mcp_hub import McpHub
from nanobot.memory import ConversationStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore

logger = logging.getLogger(__name__)


@dataclass
class ActiveRequest:
    chat_id: str
    started_at: datetime
    current_step: str


SCRATCHPAD_PROTOCOL_CORRECTION = (
    "Protocol violation: after any external tool result, call session__scratchpad_write first "
    "(mode='append' or mode='finalize') before requesting another external tool."
)
MAX_SCRATCHPAD_PROTOCOL_RETRIES = 2
SCRATCHPAD_PROTOCOL_ABORT_REPLY = "I got stuck enforcing scratchpad updates in this turn. Please try again."
EMPTY_REPLY_FALLBACK = "I'm sorry, I hit an empty model response. Please try again."


class BotCore:
    def __init__(self, config: AppConfig, channels: dict[str, Any]) -> None:
        self.config = config
        self.channels = channels
        self.llm = LlmClient(config.model)
        self.memory = ConversationStore(config.database_path)
        self.contexts = ContextStore(config.database_path)
        for server in config.mcp_servers:
            if server.name == "scheduler":
                server.env = dict(server.env)
                server.env.setdefault("SCHEDULER_DB_PATH", config.scheduler_db_path)
        self.mcp = McpHub(config.mcp_servers)
        self.scheduler_store = SchedulerStore(config.scheduler_db_path)
        self.tool_hooks: list[ToolHook] = build_default_tool_hooks()
        self.scheduler = SchedulerRunner(
            store=self.scheduler_store,
            on_due_task=self._handle_scheduled_task,
            poll_interval_seconds=config.poll_interval_seconds,
        )
        from nanobot.core_commands.command_manager import CommandManager

        self.command_manager = CommandManager(self)
        self.active_requests: dict[str, ActiveRequest] = {}
        self.agent_run = AgentRun(self)
        self.router = MessageRouter(self)

    async def start(self) -> None:
        logger.info(
            "Runtime paths cwd=%s database=%s scheduler_db=%s",
            os.getcwd(),
            str(Path(self.config.database_path).resolve()),
            str(Path(self.config.scheduler_db_path).resolve()),
        )
        await self.mcp.start()
        await self.scheduler.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.mcp.stop()

    async def on_incoming(self, message: IncomingMessage) -> None:
        scope = scoped_chat_id(message.channel, message.chat_id)
        cmd = command_name(message.text)
        if cmd is not None:
            await self.command_manager.handle(cmd, message, scope)
        else:
            await self._process(scope, message.text)

    async def _handle_scheduled_task(self, scoped_id: str, prompt: str) -> None:
        await self._process_scheduled(scoped_id, prompt)

    async def _process(self, scope: str, user_text: str) -> None:
        logger.info("Processing message for scope=%s", scope)
        self.active_requests[scope] = ActiveRequest(
            chat_id=scope,
            started_at=datetime.now(),
            current_step="processing user message",
        )
        try:
            self.memory.add_message(scope, "user", user_text)
            self.contexts.put("chat", scope, "last_user_message", {"text": user_text})
            await self.router.route_user_message(scope)
        finally:
            self.active_requests.pop(scope, None)

    async def _process_scheduled(self, scope: str, prompt: str) -> None:
        logger.info("Processing scheduled task for scope=%s prompt=%s", scope, clip(prompt, limit=200))
        messages = [
            self._base_system_message(),
            {"role": "system", "content": SCHEDULED_SYSTEM_MARKER},
            {"role": "user", "content": prompt},
        ]
        await self._run_agent_turn(scope=scope, messages=messages, persist_assistant=True)

    async def _process_plan(self, chat_scope: str, raw_text: str) -> None:
        await process_plan(self, chat_scope, raw_text)

    def _base_system_message(self) -> dict[str, str]:
        return {
            "role": "system",
            "content": self.config.system_prompt_template.format(
                assistant_name=self.config.assistant_name,
                working_timezone=self.config.working_timezone,
                current_time=human_now(self.config.working_timezone),
            ),
        }

    async def _run_agent_turn(self, scope: str, messages: list[dict], persist_assistant: bool) -> None:
        if scope in self.active_requests:
            self.active_requests[scope].current_step = "calling tools"
        reply, _ = await self.agent_run.run(
            scope_for_tools=scope,
            messages=messages,
            tools=self._list_openai_tools(),
        )
        final_reply = str(reply or "")
        if not final_reply.strip():
            logger.warning(
                "Assistant produced empty/whitespace reply scope=%s chars=%d; using fallback",
                scope,
                len(final_reply),
            )
            final_reply = EMPTY_REPLY_FALLBACK
        if persist_assistant:
            self.memory.add_message(scope, "assistant", final_reply)
        self.contexts.put("chat", scope, "last_assistant_message", {"text": final_reply})
        await self._send(scope, final_reply)

    async def _run_agent_loop(
        self,
        scope_for_tools: str,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        return await self.agent_run.run(
            scope_for_tools=scope_for_tools,
            messages=messages,
            tools=tools,
            response_format=response_format,
        )

    def _list_openai_tools(self) -> list[dict]:
        return [scratchpad_tool_spec(), *self.mcp.list_openai_tools()]

    def _build_context_report(self, scope: str) -> str:
        return build_context_report(self, scope)

    def _build_full_context_report(self, scope: str) -> str:
        return build_full_context_report(self, scope)

    async def _dispatch_after_tool_call(self, event: ToolCallEvent) -> None:
        for hook in self.tool_hooks:
            try:
                await hook.after_tool_call(event, self)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "after_tool_call hook failed hook=%s tool=%s", hook.__class__.__name__, event.tool_name
                )

    async def _send(self, scope: str, text: str) -> None:
        channel_name, raw_chat_id = unscoped_chat_id(scope)
        channel = self.channels.get(channel_name)
        if channel is None:
            logger.error("No channel configured for scope=%s channel=%s", scope, channel_name)
            raise KeyError(f"No channel configured for '{channel_name}'")
        logger.info("Sending message via channel=%s chat_id=%s", channel_name, raw_chat_id)
        await channel.send(raw_chat_id, text)
