from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent_run import AgentRun
from nanobot.cancel_token import CancellationToken, LlmCallCancelledError
from nanobot.channels.base import IncomingMessage
from nanobot.config import AppConfig
from nanobot.context_store import ContextStore
from nanobot.core_reports import build_context_report, build_full_context_report
from nanobot.core_scratchpad import scratchpad_tool_spec
from nanobot.core_utils import (
    attach_human_timestamps,
    command_name,
    human_now,
    trim_history_by_chars,
    unscoped_chat_id,
)
from nanobot.evaluator import EvaluationResult, LearningEvaluator
from nanobot.hooks import ToolCallEvent, ToolHook, build_default_tool_hooks
from nanobot.llm import LlmClient
from nanobot.memory import ConversationStore
from nanobot.memstore.tools import register_memory_tools
from nanobot.messages import OrchestratorMessage, ScheduledTaskMessage, SubagentResultMessage, UserMessage
from nanobot.plans import PlanStore, register_plan_tools
from nanobot.prompts import PromptStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore
from nanobot.skills import RatioFilter, SkillStore, SkillVectorStore, register_skill_tools
from nanobot.subagents import SubagentManager
from nanobot.subagents.manager import SubagentRunResult
from nanobot.tools import McpToolSource, ToolRegistry, ToolStatsStore
from nanobot.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Tool name patterns that are always available regardless of skill matching.
# These form the "core tool set" — essential tools the agent needs on every turn.
# Keep this under ~20 patterns to stay below the tool-count accuracy threshold.
# session__scratchpad_write is not listed here because it's not in ToolRegistry;
# it's always prepended separately by _list_openai_tools().
CORE_TOOL_PATTERNS: list[str] = [
    # Memory (essential for context retrieval, persistence, and lifecycle)
    "memory__search",
    "memory__save",
    "memory__save_turn",
    "memory__update",
    "memory__delete",
    "memory__list",
    # Skill management (essential for skill discovery)
    "skill__list",
    "skill__get",
    # Plan read-only (agent may always need to check plan status)
    "plan__get",
    "plan__list",
    # Timer (utility needed across conversations)
    "timer__*",
    # Scheduler (utility needed across conversations)
    "scheduler__*",
]


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
            # Web scripts use a separate VectorStore config (config.web-scripts.mem0.yaml)
            # to avoid Qdrant lock contention — see docs/mem0-vector-store-patterns.md.
        self._mcp_source = McpToolSource(config.mcp_servers)
        self.tool_stats = ToolStatsStore(config.database_path) if config.enable_tool_stats else None
        self.tools = ToolRegistry(stats_store=self.tool_stats)
        self.scheduler_store = SchedulerStore(config.scheduler_db_path, timezone_name=config.working_timezone)
        self.plan_store = PlanStore(config.plan_db_path)
        self.skills = SkillStore(config.skill_db_path)
        self.prompts = PromptStore(config.prompt_db_path)
        register_plan_tools(self.tools, self.plan_store)
        self.vector_store: VectorStore | None = None
        self.mem0_skill_store: SkillVectorStore | None = None
        if config.mem0_config_path:
            vs_path = Path(config.mem0_config_path)
            if vs_path.exists():
                self.vector_store = VectorStore(str(vs_path))
                self.mem0_skill_store = SkillVectorStore(
                    self.vector_store,
                    score_filter=RatioFilter(min_top_ratio=0.7, min_score=0.45),
                )
                register_memory_tools(self.tools, self.vector_store)
                logger.info("VectorStore initialized from %s", config.mem0_config_path)
            else:
                logger.warning("mem0_config_path specified but file not found: %s", config.mem0_config_path)
        register_skill_tools(self.tools, self.skills, self.mem0_skill_store)
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
            mem0_store=self.mem0_skill_store,
        )
        self.evaluator: LearningEvaluator | None = None
        if config.enable_evaluator:
            self.evaluator = LearningEvaluator(llm=self.llm, prompts=self.prompts, tool_registry=self.tools)
            logger.info("LearningEvaluator enabled")
        self._cancel_tokens: dict[str, CancellationToken] = {}
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
        # Signal cancellation to all in-flight requests
        if self._cancel_tokens:
            logger.info("Cancelling %d in-flight request(s)", len(self._cancel_tokens))
        for token in self._cancel_tokens.values():
            token.cancel()
        if self._queue_task is not None:
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass
        await self.scheduler.stop()
        await self._mcp_source.stop()

    def cancel_request(self, scope: str) -> bool:
        """Cancel a specific in-flight request. Returns True if a token was found and cancelled."""
        token = self._cancel_tokens.get(scope)
        if token:
            token.cancel()
            return True
        return False

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
                elif isinstance(msg, ScheduledTaskMessage):
                    await self._handle_scheduled_task_message(msg)
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
            elif isinstance(msg, ScheduledTaskMessage):
                await self._handle_scheduled_task_message(msg)
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
            await self._process(scope, msg.text, user_id=msg.user_id)

    async def _handle_subagent_result(self, msg: SubagentResultMessage) -> None:
        logger.info(
            "Subagent result run_id=%s scope=%s success=%s tools=%d",
            msg.run_id,
            msg.parent_scope,
            msg.success,
            len(msg.tool_trace),
        )

        if self._should_notify_user(msg):
            text = msg.summary if msg.success else self._format_failure_summary(msg)
            self.memory.add_message(msg.parent_scope, "assistant", text)
            await self._send(msg.parent_scope, text)

    @staticmethod
    def _format_failure_summary(msg: SubagentResultMessage) -> str:
        error = ""
        if msg.metadata and isinstance(msg.metadata, dict):
            error = msg.metadata.get("message") or msg.metadata.get("error", "")
            # Full tracebacks are not useful in user-facing messages
            if len(error) > 300:
                error = error[:300] + "…"
        if error:
            if "exceed_context_size" in error or "exceeds the available context" in error:
                return (
                    "Scheduled task failed: the response grew too large to fit in the model's context window. "
                    "This usually happens when a web page returns very large content. "
                    "The task will retry on its next scheduled run."
                )
            return f"Scheduled task failed: {error}"
        return "Scheduled task failed with an unexpected error. It will retry on its next scheduled run."

    def _should_notify_user(self, msg: SubagentResultMessage) -> bool:
        if not msg.success:
            return True
        if not msg.summary.strip():
            return False
        if "NO_ACTION_NEEDED" in msg.summary.upper():
            return False
        if len(msg.tool_trace) == 0:
            return len(msg.summary) > 50
        return True

    async def _handle_scheduled_task(
        self, scoped_id: str, prompt: str, *, task_id: int = 0, cron_expr: str = ""
    ) -> None:
        # Mark task as ran immediately so it won't appear as due on the next poll cycle.
        # Without this, the 20s poll interval would re-enqueue the same task during execution.
        if task_id and cron_expr:
            self.scheduler_store.mark_ran(task_id, cron_expr)
        await self._message_queue.put(
            ScheduledTaskMessage(scope=scoped_id, prompt=prompt, task_id=task_id, cron_expr=cron_expr)
        )

    async def _handle_scheduled_task_message(self, msg: ScheduledTaskMessage) -> None:
        logger.info("Scheduled task triggered scope=%s task_id=%d", msg.scope, msg.task_id)
        token = CancellationToken()
        self._cancel_tokens[msg.scope] = token
        self.active_requests[msg.scope] = ActiveRequest(
            chat_id=msg.scope,
            started_at=datetime.now(),
            current_step="scheduled task",
        )
        try:
            system_content = self.prompts.render("subagent_scheduled", user_id=msg.scope)
            time_content = self.prompts.render(
                "subagent_time",
                working_timezone=self.config.working_timezone,
                current_time=human_now(self.config.working_timezone),
            )
            messages = [
                {"role": "system", "content": system_content},
                {"role": "system", "content": time_content},
                {"role": "user", "content": msg.prompt},
            ]
            run = self.subagent_manager.spawn(scope=msg.scope, goal=msg.prompt)
            skill_names = self._get_active_skill_names(run.id)
            result = await self.subagent_manager.execute(
                run, messages, self._list_openai_tools(skill_names), cancel_token=token
            )
            result_msg = SubagentResultMessage(
                run_id=result.run_id,
                parent_scope=msg.scope,
                success=result.success,
                summary=result.reply,
                tool_trace=result.tool_trace,
                metadata={"error": result.error} if result.error else None,
            )
            await self.on_subagent_result(result_msg)
            await self._evaluate_turn(msg.scope, msg.prompt, result)
        except LlmCallCancelledError:
            logger.info("Scheduled request cancelled scope=%s task_id=%d", msg.scope, msg.task_id)
        except asyncio.CancelledError:
            logger.info("Scheduled request interrupted by shutdown scope=%s task_id=%d", msg.scope, msg.task_id)
            raise
        except Exception:
            logger.exception("Scheduled task execution failed task_id=%d scope=%s", msg.task_id, msg.scope)
        finally:
            self.active_requests.pop(msg.scope, None)
            self._cancel_tokens.pop(msg.scope, None)

    async def _process(self, scope: str, user_text: str, user_id: str = "") -> None:
        logger.info("Processing message for scope=%s user_id=%s", scope, user_id)
        token = CancellationToken()
        self._cancel_tokens[scope] = token
        self.active_requests[scope] = ActiveRequest(
            chat_id=scope,
            started_at=datetime.now(),
            current_step="processing user message",
        )
        typing_task = asyncio.create_task(self._typing_heartbeat(scope))
        try:
            self.memory.add_message(scope, "user", user_text)
            self.contexts.put("chat", scope, "last_user_message", {"text": user_text})

            history = self.memory.get_recent_messages(scope, limit=self.config.history_message_limit)
            history = attach_human_timestamps(history, timezone_name=self.config.working_timezone)
            history = trim_history_by_chars(history, self.config.history_char_limit)
            messages = self._system_messages(user_id=user_id)
            messages.extend(history)

            run = self.subagent_manager.spawn(scope=scope, goal=user_text)
            skill_names = self._get_active_skill_names(run.id)
            result = await self.subagent_manager.execute(
                run, messages, self._list_openai_tools(skill_names), cancel_token=token
            )

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

            await self._evaluate_turn(scope, user_text, result)
        except LlmCallCancelledError:
            logger.info("Request cancelled scope=%s", scope)
            self.memory.add_message(scope, "assistant", "Request was cancelled.")
            try:
                await self._send(scope, "Request was cancelled.")
            except Exception:  # pylint: disable=broad-except
                logger.debug("Failed to send cancellation message (channel may be stopped) scope=%s", scope)
        except asyncio.CancelledError:
            logger.info("Request interrupted by shutdown scope=%s", scope)
            raise
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            self.active_requests.pop(scope, None)
            self._cancel_tokens.pop(scope, None)

    async def _typing_heartbeat(self, scope: str) -> None:
        """Periodically send a typing indicator to the channel while processing.

        Telegram's chat_action typing indicator expires after 5 seconds, so
        we re-send every 4 seconds to keep it alive. The task is cancelled
        by _process() when the agent run completes.
        """
        while True:
            try:
                channel_name, raw_chat_id = unscoped_chat_id(scope)
                channel = self.channels.get(channel_name)
                if channel is not None:
                    await channel.send_typing(raw_chat_id)
            except Exception:  # pylint: disable=broad-except
                logger.debug("typing_heartbeat failed scope=%s", scope, exc_info=True)
            await asyncio.sleep(4)

    def _system_messages(self, user_id: str = "") -> list[dict[str, str]]:
        """Build the ordered list of system messages for prompt caching.

        Returns separate system messages so the static prefix (orchestrator_main)
        can be cached across requests while the dynamic time block changes per-request.
        """
        static_content = self.prompts.render(
            "orchestrator_main",
            assistant_name=self.config.assistant_name,
        )
        time_content = self.prompts.render(
            "orchestrator_main_time",
            working_timezone=self.config.working_timezone,
            current_time=human_now(self.config.working_timezone),
        )
        messages = [
            {"role": "system", "content": static_content},
            {"role": "system", "content": time_content},
        ]
        if user_id:
            user_content = self.prompts.render(
                "orchestrator_user_context",
                user_id=user_id,
            )
            messages.append({"role": "system", "content": user_content})
        return messages

    def _get_active_skill_names(self, run_id: str) -> list[str]:
        active_skills_data = self.contexts.get("subagent_run", run_id, "active_skills")
        if active_skills_data and isinstance(active_skills_data, dict):
            skill_names = active_skills_data.get("skills", [])
            if isinstance(skill_names, list):
                return skill_names
        return []

    def _list_openai_tools(self, skill_names: list[str] | None = None) -> list[dict[str, Any]]:
        patterns = list(CORE_TOOL_PATTERNS)
        if skill_names:
            for name in skill_names:
                skill = self.skills.get_by_name(name)
                if skill and skill.is_active and skill.tools_allowlist:
                    patterns.extend(skill.tools_allowlist)
        return [scratchpad_tool_spec(), *self.tools.list_openai_specs(patterns)]

    def _build_context_report(self, scope: str) -> str:
        return build_context_report(self, scope)

    def _build_full_context_report(self, scope: str) -> str:
        return build_full_context_report(self, scope)

    async def _evaluate_turn(
        self,
        scope: str,
        user_request: str,
        worker_result: SubagentRunResult,
    ) -> None:
        """Run evaluator on worker result. Non-blocking: failures are logged, not raised."""
        if self.evaluator is None:
            return
        scratchpad = self.contexts.get("subagent_run", worker_result.run_id, "scratchpad")
        active_skills = self.skills.list_active()
        try:
            result = await self.evaluator.evaluate(
                scope, user_request, worker_result, scratchpad=scratchpad, active_skills=active_skills
            )
            self._execute_skill_decisions(scope, result)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Evaluator failed scope=%s", scope)

    def _execute_skill_decisions(self, scope: str, result: EvaluationResult) -> None:
        """Execute skill decisions from evaluator. Each operation is independent and fault-tolerant."""
        for op in result.decisions:
            if op.action == "skip":
                logger.info("Evaluator skipped skill name=%s reason=%s", op.name, op.reason[:80])
                continue
            try:
                if op.action == "create":
                    existing = self.skills.get_by_name(op.name)
                    if existing is not None:
                        logger.warning("Evaluator tried to create existing skill name=%s, skipping", op.name)
                        continue
                    skill = self.skills.create(
                        name=op.name,
                        description=op.description,
                        instructions=op.instructions,
                        trigger_mode=op.trigger_mode,
                        tools_allowlist=op.tools_allowlist,
                        is_active=True,
                    )
                    if op.trigger_mode == "intelligent" and self.mem0_skill_store:
                        try:
                            self.mem0_skill_store.store_skill(skill)
                        except Exception:  # pylint: disable=broad-except
                            logger.exception("Failed to sync skill '%s' to mem0", op.name)
                    logger.info("Evaluator created skill name=%s trigger_mode=%s", op.name, op.trigger_mode)
                elif op.action == "update":
                    existing = self.skills.get_by_name(op.name)
                    if existing is None:
                        logger.warning("Evaluator tried to update non-existent skill name=%s", op.name)
                        continue
                    update_kwargs: dict[str, Any] = {}
                    if op.description:
                        update_kwargs["description"] = op.description
                    if op.instructions:
                        update_kwargs["instructions"] = op.instructions
                    if op.trigger_mode:
                        update_kwargs["trigger_mode"] = op.trigger_mode
                    if op.tools_allowlist:
                        update_kwargs["tools_allowlist"] = op.tools_allowlist
                    updated = self.skills.update(existing.id, **update_kwargs)
                    if updated and updated.trigger_mode == "intelligent" and self.mem0_skill_store:
                        try:
                            self.mem0_skill_store.remove_skill(op.name)
                            self.mem0_skill_store.store_skill(updated)
                        except Exception:  # pylint: disable=broad-except
                            logger.exception("Failed to sync updated skill '%s' to mem0", op.name)
                    logger.info("Evaluator updated skill name=%s", op.name)
                elif op.action == "deprecate":
                    existing = self.skills.get_by_name(op.name)
                    if existing is None:
                        logger.warning("Evaluator tried to deprecate non-existent skill name=%s", op.name)
                        continue
                    if not existing.is_active:
                        logger.info("Evaluator deprecated already-inactive skill name=%s, skipping", op.name)
                        continue
                    self.skills.set_active(existing.id, is_active=False)
                    if existing.trigger_mode == "intelligent" and self.mem0_skill_store:
                        try:
                            self.mem0_skill_store.remove_skill(op.name)
                        except Exception:  # pylint: disable=broad-except
                            logger.exception("Failed to remove deprecated skill '%s' from mem0", op.name)
                    logger.info("Evaluator deprecated skill name=%s", op.name)
            except Exception:  # pylint: disable=broad-except
                logger.exception("Evaluator skill operation failed action=%s name=%s", op.action, op.name)

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
