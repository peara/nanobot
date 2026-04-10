from __future__ import annotations

import logging
from typing import Any

from nanobot.config import load_config as load_config_func
from nanobot.core_commands.commands.base import BaseCommand
from nanobot.llm import LlmClient
from nanobot.mcp_hub import McpHub
from nanobot.scheduler_runner import SchedulerRunner
from nanobot.scheduler_store import SchedulerStore

logger = logging.getLogger(__name__)


class ReloadCommand(BaseCommand):
    @classmethod
    def names(cls) -> list[str]:
        return ["/reload"]

    async def handle(self, raw_text: str, scope: str) -> None:
        if self.core.config.owner_chat_id != 0:
            from nanobot.core_utils import unscoped_chat_id

            _, raw_chat_id = unscoped_chat_id(scope)
            if int(raw_chat_id) != self.core.config.owner_chat_id:
                await self._send(scope, "Reload command restricted to owner only.")
                return
        parts = raw_text.strip().split()
        reload_type = parts[1] if len(parts) > 1 else "all"
        dry_run = "--dry-run" in raw_text or "-d" in raw_text
        await self._send(scope, f"Initiating reload (type={reload_type}, dry_run={dry_run})...")
        try:
            if reload_type == "config":
                result = await self.reload_config(dry_run=dry_run)
            elif reload_type == "mcp":
                result = await self.reload_mcp_servers()
            else:
                result = await self.reload_all(dry_run=dry_run)
            if isinstance(result, str):
                msg = result
            else:
                msg = result.get("message", "Reload completed.")
            await self._send(scope, msg)
        except Exception as e:
            logger.exception("Reload failed")
            await self._send(scope, f"Reload failed: {e}")

    async def reload_config(self, dry_run: bool = False) -> dict[str, Any] | str:
        if dry_run:
            try:
                new_config = load_config_func("config.yaml")
                message = (
                    f"Config validation passed (dry-run).\n"
                    f"Changes: assistant_name={new_config.assistant_name}, "
                    f"poll_interval={new_config.poll_interval_seconds}"
                )
                return {
                    "message": message,
                    "success": True,
                }
            except Exception as e:
                logger.exception("Config dry-run failed")
                return {"message": f"Config validation failed: {e}", "success": False, "dry_run": True}
        try:
            new_config = load_config_func("config.yaml")
            await self.core.stop()
            self.core.config = new_config
            self.core.llm = LlmClient(new_config.model)
            for server in new_config.mcp_servers:
                if server.name == "scheduler":
                    server.env = dict(server.env)
                    server.env.setdefault("SCHEDULER_DB_PATH", new_config.scheduler_db_path)
                    server.env.setdefault("SCHEDULER_TIMEZONE", new_config.working_timezone)
            self.core.mcp = McpHub(new_config.mcp_servers)
            self.core.scheduler_store = SchedulerStore(
                new_config.scheduler_db_path,
                timezone_name=new_config.working_timezone,
            )
            self.core.scheduler = SchedulerRunner(
                store=self.core.scheduler_store,
                on_due_task=self.core._handle_scheduled_task,
                poll_interval_seconds=new_config.poll_interval_seconds,
            )
            await self.core.start()
            return {"message": "Config reloaded successfully.", "success": True}
        except Exception as e:
            logger.exception("Config reload failed")
            return {"message": f"Failed to reload config: {e}", "success": False}

    async def reload_mcp_servers(self) -> dict[str, Any] | str:
        await self._send_scope_message("mcp", "Stopping MCP servers...")
        try:
            await self.core.mcp.stop()
            for server in self.core.config.mcp_servers:
                if server.name == "scheduler":
                    server.env = dict(server.env)
                    server.env.setdefault("SCHEDULER_DB_PATH", self.core.config.scheduler_db_path)
                    server.env.setdefault("SCHEDULER_TIMEZONE", self.core.config.working_timezone)
            self.core.mcp = McpHub(self.core.config.mcp_servers)
            await self.core.start()
            return {"message": "MCP servers restarted successfully.", "success": True}
        except Exception as e:
            logger.exception("MCP reload failed")
            return {"message": f"Failed to restart MCP servers: {e}", "success": False}

    async def reload_all(self, dry_run: bool = False) -> dict[str, Any] | str:
        if dry_run:
            try:
                new_config = load_config_func("config.yaml")
                message = (
                    f"Full reload validation passed (dry-run).\n"
                    f"Config: assistant_name={new_config.assistant_name}\n"
                    f"MCP servers: {[s.name for s in new_config.mcp_servers]}"
                )
                return {
                    "message": message,
                    "success": True,
                    "dry_run": True,
                }
            except Exception as e:
                logger.exception("Full reload dry-run failed")
                return {"message": f"Validation failed: {e}", "success": False, "dry_run": True}
        await self._send_scope_message("mcp", "Saving state and initiating full reload...")
        try:
            from nanobot.core_utils import human_now

            self.core.contexts.put(
                "system",
                "reload",
                "phase",
                {"timestamp": human_now(self.core.config.working_timezone), "phase": "pre_reload"},
            )
            await self.core.stop()
            for server in self.core.config.mcp_servers:
                if server.name == "scheduler":
                    server.env = dict(server.env)
                    server.env.setdefault("SCHEDULER_DB_PATH", self.core.config.scheduler_db_path)
                    server.env.setdefault("SCHEDULER_TIMEZONE", self.core.config.working_timezone)
            self.core.llm = LlmClient(self.core.config.model)
            self.core.mcp = McpHub(self.core.config.mcp_servers)
            self.core.scheduler_store = SchedulerStore(
                self.core.config.scheduler_db_path,
                timezone_name=self.core.config.working_timezone,
            )
            self.core.scheduler = SchedulerRunner(
                store=self.core.scheduler_store,
                on_due_task=self.core._handle_scheduled_task,
                poll_interval_seconds=self.core.config.poll_interval_seconds,
            )
            await self.core.start()
            self.core.contexts.put(
                "system",
                "reload",
                "phase",
                {"timestamp": human_now(self.core.config.working_timezone), "phase": "post_reload", "success": True},
            )
            return {"message": "Full reload completed successfully.", "success": True}
        except Exception as e:
            logger.exception("Full reload failed")
            self.core.contexts.put(
                "system",
                "reload",
                "phase",
                {"timestamp": human_now(self.core.config.working_timezone), "phase": "failed", "error": str(e)},
            )
            await self.core.stop()
            for server in self.core.config.mcp_servers:
                if server.name == "scheduler":
                    server.env = dict(server.env)
                    server.env.setdefault("SCHEDULER_DB_PATH", self.core.config.scheduler_db_path)
                    server.env.setdefault("SCHEDULER_TIMEZONE", self.core.config.working_timezone)
            self.core.llm = LlmClient(self.core.config.model)
            self.core.mcp = McpHub(self.core.config.mcp_servers)
            self.core.scheduler_store = SchedulerStore(
                self.core.config.scheduler_db_path,
                timezone_name=self.core.config.working_timezone,
            )
            self.core.scheduler = SchedulerRunner(
                store=self.core.scheduler_store,
                on_due_task=self.core._handle_scheduled_task,
                poll_interval_seconds=self.core.config.poll_interval_seconds,
            )
            await self.core.start()
            self.core.contexts.put(
                "system",
                "reload",
                "phase",
                {"timestamp": human_now(self.core.config.working_timezone), "phase": "post_reload", "success": True},
            )
            return {"message": "Full reload completed successfully.", "success": True}

    async def _send_scope_message(self, scope: str | None, text: str) -> None:
        if scope is None or scope == "mcp":
            await self._send("telegram:owner", text)
        else:
            await self._send(scope, text)
