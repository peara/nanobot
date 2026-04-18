from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 800


@dataclass
class ChannelConfig:
    type: str
    token: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class AppConfig:
    assistant_name: str
    database_path: str
    scheduler_db_path: str
    plan_db_path: str
    skill_db_path: str
    poll_interval_seconds: int
    working_timezone: str
    history_message_limit: int
    history_char_limit: int
    model: ModelConfig
    channels: list[ChannelConfig]
    mcp_servers: list[McpServerConfig]
    owner_chat_id: int = 0
    enable_tool_stats: bool = False
    prompt_db_path: str = "./data/prompts.db"


def _expand_env_value(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env_value(v) for k, v in value.items()}
    return value


def load_config(config_path: str) -> AppConfig:
    raw = Path(config_path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    data = _expand_env_value(data)

    model_cfg = ModelConfig(**data["model"])
    channel_cfg = [ChannelConfig(**c) for c in data.get("channels", [])]
    mcp_cfg = [McpServerConfig(**s) for s in data.get("mcp_servers", [])]

    return AppConfig(
        assistant_name=data.get("assistant_name", "Nano"),
        database_path=data.get("database_path", "./data/nanobot.db"),
        scheduler_db_path=data.get("scheduler_db_path", "./data/scheduler.db"),
        plan_db_path=data.get("plan_db_path", "./data/plans.db"),
        skill_db_path=data.get("skill_db_path", "./data/skills.db"),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 20)),
        working_timezone=data.get("working_timezone", "UTC"),
        history_message_limit=int(data.get("history_message_limit", 24)),
        history_char_limit=int(data.get("history_char_limit", 12000)),
        model=model_cfg,
        channels=channel_cfg,
        mcp_servers=mcp_cfg,
        owner_chat_id=int(data.get("owner_chat_id", 0)),
        enable_tool_stats=bool(data.get("enable_tool_stats", False)),
        prompt_db_path=data.get("prompt_db_path", "./data/prompts.db"),
    )
