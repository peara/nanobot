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
from nanobot.evaluator import EvaluationResult, LearningEvaluator
from nanobot.hooks import ToolCallEvent, ToolHook, build_default_tool_hooks
from nanobot.llm import LlmClient
from nanobot.memory import ConversationStore
from nanobot.memstore.tools import register_memory_tools
from nanobot.messages import OrchestratorMessage, SubagentResultMessage, UserMessage
from nanobot.plans import PlanStore, register_plan_tools
from nanobot.prompts import PromptStore
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore
from nanobot.skills import SkillStore, SkillVectorStore, register_skill_tools
from nanobot.subagents import SubagentManager
from nanobot.subagents.manager import SubagentRunResult
from nanobot.tools import McpToolSource, ToolRegistry, ToolStatsStore
from nanobot.vector_store import VectorStore

logger = logging.getLogger(__name__)

PROCEDURAL_WEB_STRATEGY = "procedural_web"
GENERAL_STRATEGY = "general"
BLOCKED_PROCEDURAL_TOOLS = {"web__search_web", "web__search_google_web"}
CREATE_SCRIPT_INTENT = "create_script"
DEFAULT_INTENT = "default"
PROCEDURAL_KEYWORDS = {
    "nanoscript",
    "selector",
    "pagination",
    "extract",
    "scrape",
    "crawl",
    "issues",
    "trending",
    "web automation",
    "invoke script",
    "test script",
    "repair script",
    "create script",
}
PROCEDURAL_WEB_POLICY = (
    "For web extraction/automation tasks, default to NanoScript procedural memory. "
    "Use this order first: web__search_scripts, web__invoke_script, web__test_script, web__repair_script. "
    "Only use generic web browsing tools if no reliable script is found or explicit fallback is needed."
)
CREATE_SCRIPT_POLICY = (
    "The user is explicitly asking to create a reusable NanoScript. "
    "Do not ask for generic clarification unless required fields are truly missing. "
    "Call web__create_script in this turn with a best-effort complete payload "
    "(name, description, code, params_schema, output_schema, selector_manifest, embedding_text, created_by). "
    "If selector details are uncertain, still provide a reasonable fallback selector_manifest instead of null. "
    "Generate code that passes NanoScript AST constraints: avoid while True, "
    "use browser.loop_guard(...) in while conditions, "
    "and prefer x = x + 1 over augmented assignment operators like +=."
)


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
        self.vector_store: VectorStore | None = None
        self.mem0_skill_store: SkillVectorStore | None = None
        if config.mem0_config_path:
            vs_path = Path(config.mem0_config_path)
            if vs_path.exists():
                self.vector_store = VectorStore(str(vs_path))
                self.mem0_skill_store = SkillVectorStore(self.vector_store)
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
            self.evaluator = LearningEvaluator(llm=self.llm, prompts=self.prompts)
            logger.info("LearningEvaluator enabled")
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
        await self._evaluate_turn(scoped_id, prompt, result)

    async def _process(self, scope: str, user_text: str, user_id: str = "") -> None:
        logger.info("Processing message for scope=%s user_id=%s", scope, user_id)
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
            strategy = self._execution_strategy_for_request(user_text)
            self.contexts.put("chat", scope, "execution_strategy", {"value": strategy})
            intent = self._intent_for_request(user_text, strategy)
            self.contexts.put("chat", scope, "execution_intent", {"value": intent})
            messages = self._system_messages(user_id=user_id)
            policy_message = self._strategy_policy_message(strategy)
            if policy_message is not None:
                messages.append(policy_message)
            intent_message = self._intent_policy_message(intent)
            if intent_message is not None:
                messages.append(intent_message)
            messages.extend(history)

            run = self.subagent_manager.spawn(scope=scope, goal=user_text)
            tools = self._filter_tools_for_strategy(self._list_openai_tools(), strategy)
            tools = self._filter_tools_for_intent(tools, intent)
            result = await self.subagent_manager.execute(run, messages, tools)

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
        finally:
            self.active_requests.pop(scope, None)

    def _system_messages(self, user_id: str = "") -> list[dict[str, str]]:
        """Build the ordered list of system messages for prompt caching.

        Returns separate system messages so the static prefix (orchestrator_main)
        can be cached across requests while the dynamic time block changes per-request.
        """
        current_time = human_now(self.config.working_timezone)
        static_content = self.prompts.render(
            "orchestrator_main",
            assistant_name=self.config.assistant_name,
            working_timezone=self.config.working_timezone,
            current_time=current_time,
        )
        time_content = self.prompts.render(
            "orchestrator_main_time",
            working_timezone=self.config.working_timezone,
            current_time=current_time,
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

    def _list_openai_tools(self, patterns: list[str] | None = None) -> list[dict]:
        return [scratchpad_tool_spec(), *self.tools.list_openai_specs(patterns)]

    @staticmethod
    def _execution_strategy_for_request(user_text: str) -> str:
        text = user_text.strip().lower()
        if not text:
            return GENERAL_STRATEGY
        has_url = "http://" in text or "https://" in text or "www." in text
        has_procedural_hint = any(keyword in text for keyword in PROCEDURAL_KEYWORDS)
        if has_procedural_hint and (has_url or "github" in text):
            return PROCEDURAL_WEB_STRATEGY
        if "nanoscript" in text or "script version" in text:
            return PROCEDURAL_WEB_STRATEGY
        return GENERAL_STRATEGY

    @staticmethod
    def _strategy_policy_message(strategy: str) -> dict[str, str] | None:
        if strategy != PROCEDURAL_WEB_STRATEGY:
            return None
        return {"role": "system", "content": PROCEDURAL_WEB_POLICY}

    @staticmethod
    def _intent_for_request(user_text: str, strategy: str) -> str:
        text = user_text.strip().lower()
        if strategy != PROCEDURAL_WEB_STRATEGY:
            return DEFAULT_INTENT
        if not text:
            return DEFAULT_INTENT
        create_markers = (
            "create script",
            "create a script",
            "create nanoscript",
            "build a script",
            "save it for reuse",
            "reusable nanoscript",
            "reusable workflow",
            "reusable browser workflow",
            "workflow i can reuse",
        )
        has_create_marker = any(marker in text for marker in create_markers)
        if has_create_marker and ("script" in text or "nanoscript" in text or "workflow" in text):
            return CREATE_SCRIPT_INTENT
        if "github issues" in text and "reusable" in text and "workflow" in text:
            return CREATE_SCRIPT_INTENT
        return DEFAULT_INTENT

    @staticmethod
    def _intent_policy_message(intent: str) -> dict[str, str] | None:
        if intent == CREATE_SCRIPT_INTENT:
            return {"role": "system", "content": CREATE_SCRIPT_POLICY}
        return None

    @staticmethod
    def _filter_tools_for_strategy(tools: list[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
        if strategy != PROCEDURAL_WEB_STRATEGY:
            return tools
        filtered: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("function", {}).get("name", ""))
            if name in BLOCKED_PROCEDURAL_TOOLS:
                continue
            filtered.append(tool)
        return filtered

    @staticmethod
    def _filter_tools_for_intent(tools: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
        if intent != CREATE_SCRIPT_INTENT:
            return tools
        allowed = {"session__scratchpad_write", "web__create_script"}
        filtered: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("function", {}).get("name", ""))
            if name in allowed:
                filtered.append(tool)
        # Safety fallback: never drop all tools unexpectedly.
        return filtered or tools

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
        scratchpad = self.contexts.get("chat", scope, "scratchpad")
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
                    updated = self.skills.update(existing.id, **update_kwargs)
                    if updated and updated.trigger_mode == "intelligent" and self.mem0_skill_store:
                        try:
                            self.mem0_skill_store.remove_skill(op.name)
                            self.mem0_skill_store.store_skill(updated)
                        except Exception:  # pylint: disable=broad-except
                            logger.exception("Failed to sync updated skill '%s' to mem0", op.name)
                    logger.info("Evaluator updated skill name=%s", op.name)
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
