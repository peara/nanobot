from __future__ import annotations

import asyncio
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
from nanobot.core_reports import build_context_report, build_full_context_report
from nanobot.core_scratchpad import clear_scratchpad, scratchpad_tool_spec
from nanobot.core_utils import (
    attach_human_timestamps,
    command_name,
    human_now,
    trim_history_by_chars,
    unscoped_chat_id,
)
from nanobot.hooks import ToolCallEvent, ToolHook, build_default_tool_hooks
from nanobot.llm import LlmClient
from nanobot.memory import ConversationStore
from nanobot.messages import OrchestratorMessage, SubagentResultMessage, UserMessage
from nanobot.plans import PlanStore, register_plan_tools
from nanobot.prompts import PromptStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore
from nanobot.skills import SkillStore
from nanobot.subagents import SubagentManager
from nanobot.tools import McpToolSource, ToolRegistry, ToolStatsStore

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
                server.env.setdefault("SCHEDULER_TIMEZONE", config.working_timezone)
        self._mcp_source = McpToolSource(config.mcp_servers)
        self.tool_stats = ToolStatsStore(config.database_path) if config.enable_tool_stats else None
        self.tools = ToolRegistry(stats_store=self.tool_stats)
        self.scheduler_store = SchedulerStore(config.scheduler_db_path, timezone_name=config.working_timezone)
        self.plan_store = PlanStore(config.plan_db_path)
        self.skills = SkillStore(config.skill_db_path)
        self.prompts = PromptStore(config.prompt_db_path)
        register_plan_tools(self.tools, self.plan_store)
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
        self.subagent_manager = SubagentManager(
            db_path=config.database_path,
            contexts=self.contexts,
            agent_run=self.agent_run,
            tools=self.tools,
            skills=self.skills,
            prompts=self.prompts,
        )
        self._message_queue: asyncio.Queue[OrchestratorMessage] = asyncio.Queue()
        self._queue_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        logger.info(
            "Runtime paths cwd=%s database=%s scheduler_db=%s",
            os.getcwd(),
            str(Path(self.config.database_path).resolve()),
            str(Path(self.config.scheduler_db_path).resolve()),
        )
        await self._mcp_source.start()
        self.tools.add_source(self._mcp_source)
        await self.scheduler.start()
        self._queue_task = asyncio.create_task(self._process_queue_loop())

    async def stop(self) -> None:
        if self._queue_task is not None:
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass
        await self.scheduler.stop()
        await self._mcp_source.stop()

    async def on_incoming(self, message: IncomingMessage) -> None:
        user_msg = UserMessage(
            channel=message.channel,
            chat_id=message.chat_id,
            text=message.text,
            user_id=message.user_id,
        )
        await self._message_queue.put(user_msg)

    async def on_subagent_result(self, result: SubagentResultMessage) -> None:
        await self._message_queue.put(result)

    async def _process_queue_loop(self) -> None:
        while True:
            msg = await self._message_queue.get()
            try:
                if isinstance(msg, UserMessage):
                    await self._handle_user_message(msg)
                elif isinstance(msg, SubagentResultMessage):
                    await self._handle_subagent_result(msg)
            except Exception:
                logger.exception("Error processing message type=%s", type(msg).__name__)

    async def _process_one_message(self) -> bool:
        try:
            msg = self._message_queue.get_nowait()
        except asyncio.QueueEmpty:
            return False

        try:
            if isinstance(msg, UserMessage):
                await self._handle_user_message(msg)
            elif isinstance(msg, SubagentResultMessage):
                await self._handle_subagent_result(msg)
        except Exception:
            logger.exception("Error processing message type=%s", type(msg).__name__)
        return True

    async def _handle_user_message(self, msg: UserMessage) -> None:
        scope = msg.scope
        cmd = command_name(msg.text)
        if cmd is not None:
            incoming = IncomingMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                user_id=msg.user_id,
                text=msg.text,
            )
            await self.command_manager.handle(cmd, incoming, scope)
        else:
            await self._process(scope, msg.text)

    async def _handle_subagent_result(self, msg: SubagentResultMessage) -> None:
        logger.info(
            "Subagent result run_id=%s scope=%s success=%s tools=%d",
            msg.run_id,
            msg.parent_scope,
            msg.success,
            len(msg.tool_trace),
        )

        if self._should_notify_user(msg):
            self.memory.add_message(msg.parent_scope, "assistant", msg.summary)
            await self._send(msg.parent_scope, msg.summary)

    def _should_notify_user(self, msg: SubagentResultMessage) -> bool:
        if not msg.success:
            return False
        if not msg.summary.strip():
            return False
        if "NO_ACTION_NEEDED" in msg.summary.upper():
            return False
        if len(msg.tool_trace) == 0:
            return len(msg.summary) > 50
        return True

    async def _handle_scheduled_task(self, scoped_id: str, prompt: str) -> None:
        logger.info("Scheduled task triggered scope=%s", scoped_id)
        system_content = self.prompts.render("subagent_default")
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]
        run = self.subagent_manager.spawn(scope=scoped_id, goal=prompt)
        result = await self.subagent_manager.execute(run, messages, self._list_openai_tools())
        msg = SubagentResultMessage(
            run_id=result.run_id,
            parent_scope=scoped_id,
            success=result.success,
            summary=result.reply,
            tool_trace=result.tool_trace,
            metadata={"error": result.error} if result.error else None,
        )
        await self.on_subagent_result(msg)

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
            clear_scratchpad(self, scope)

            history = self.memory.get_recent_messages(scope, limit=self.config.history_message_limit)
            history = attach_human_timestamps(history, timezone_name=self.config.working_timezone)
            history = trim_history_by_chars(history, self.config.history_char_limit)
            messages = [self._base_system_message()]
            messages.extend(history)

            run = self.subagent_manager.spawn(scope=scope, goal=user_text)
            result = await self.subagent_manager.execute(run, messages, self._list_openai_tools())

            final_reply = str(result.reply or "")
            if not final_reply.strip():
                logger.warning(
                    "Assistant produced empty/whitespace reply scope=%s chars=%d; using fallback",
                    scope,
                    len(final_reply),
                )
                final_reply = EMPTY_REPLY_FALLBACK

            self.memory.add_message(scope, "assistant", final_reply)
            self.contexts.put("chat", scope, "last_assistant_message", {"text": final_reply})
            await self._send(scope, final_reply)
        finally:
            self.active_requests.pop(scope, None)

    def _base_system_message(self) -> dict[str, str]:
        content = self.prompts.render(
            "orchestrator_main",
            assistant_name=self.config.assistant_name,
            working_timezone=self.config.working_timezone,
            current_time=human_now(self.config.working_timezone),
        )
        return {"role": "system", "content": content}

    def _list_openai_tools(self, patterns: list[str] | None = None) -> list[dict]:
        return [scratchpad_tool_spec(), *self.tools.list_openai_specs(patterns)]

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
