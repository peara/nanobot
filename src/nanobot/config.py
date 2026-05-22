from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

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
    required_env: list[str] = field(default_factory=list)


@dataclass
class HandlerConfig:
    name: str
    type: str
    level: str = "NOTSET"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoggerConfig:
    handlers: list[str] = field(default_factory=list)
    level: str = "NOTSET"


@dataclass
class LoggingConfig:
    format: str
    handlers: list[HandlerConfig] = field(default_factory=list)
    loggers: dict[str, LoggerConfig] = field(default_factory=dict)


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
    enable_evaluator: bool = False
    prompt_db_path: str = "./data/prompts.db"
    mem0_config_path: str | None = None
    logging: LoggingConfig | None = None


def _expand_env_value(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env_value(v) for k, v in value.items()}
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge override into base. Lists are appended, dicts merged, scalars overridden."""
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        else:
            result[key] = value
    return result


def load_config(config_path: str) -> AppConfig:
    raw = Path(config_path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}

    override_path = str(config_path).removesuffix(".yaml") + ".override.yaml"
    if Path(override_path).is_file():
        override_raw = Path(override_path).read_text(encoding="utf-8")
        override_data = yaml.safe_load(override_raw) or {}
        data = _deep_merge(data, override_data)
        logger.info("Merged config override from %s", override_path)

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
        enable_evaluator=bool(data.get("enable_evaluator", False)),
        prompt_db_path=data.get("prompt_db_path", "./data/prompts.db"),
        mem0_config_path=data.get("mem0_config_path"),
        logging=_parse_logging(data.get("logging")),
    )


def _normalize_level(value: Any) -> str:
    if isinstance(value, int):
        import logging

        return logging.getLevelName(value)
    return str(value)


def _parse_logging(raw: dict[str, Any] | None) -> LoggingConfig | None:
    if raw is None:
        return None
    handlers = [
        HandlerConfig(
            name=h["name"],
            type=h["type"],
            level=_normalize_level(h.get("level", "NOTSET")),
            options=h.get("options", {}),
        )
        for h in raw.get("handlers", [])
    ]
    loggers = {
        name: LoggerConfig(
            handlers=cfg.get("handlers", []),
            level=_normalize_level(cfg.get("level", "NOTSET")),
        )
        for name, cfg in raw.get("loggers", {}).items()
    }
    return LoggingConfig(
        format=raw.get("format", "%(asctime)s %(levelname)s %(name)s - %(message)s"),
        handlers=handlers,
        loggers=loggers,
    )
