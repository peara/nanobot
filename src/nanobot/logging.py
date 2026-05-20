from __future__ import annotations

import logging
import logging.config
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from nanobot.config import HandlerConfig, LoggingConfig

DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"


class HandlerFactory:
    """Registry mapping handler type names to builder callables.

    Built-in types ``console`` and ``file`` are always available.
    Register additional types with :meth:`register`.
    """

    def __init__(self) -> None:
        self._registry: dict[str, Callable[[HandlerConfig], logging.Handler]] = {
            "console": self._build_console,
            "file": self._build_file,
        }

    def register(self, type_name: str, builder: Callable[[HandlerConfig], logging.Handler]) -> None:
        """Register a custom handler builder callable.

        *builder* receives a :class:`HandlerConfig` and returns a
        :class:`logging.Handler`.
        """
        self._registry[type_name] = builder

    def create(self, cfg: HandlerConfig) -> logging.Handler:
        """Create a handler from config, looking up the type in the registry."""
        builder = self._registry.get(cfg.type)
        if builder is None:
            raise ValueError(f"Unknown handler type: {cfg.type!r}")
        return builder(cfg)

    @staticmethod
    def _build_console(cfg: HandlerConfig) -> logging.Handler:
        handler = logging.StreamHandler()
        return handler

    @staticmethod
    def _build_file(cfg: HandlerConfig) -> RotatingFileHandler:
        opts = cfg.options
        filepath_str = opts.get("filepath")
        if filepath_str is None:
            raise ValueError("File handler requires 'filepath' in options")
        filepath = Path(filepath_str)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = int(opts.get("max_bytes", 2_000_000))
        backup_count = int(opts.get("backup_count", 3))
        encoding = str(opts.get("encoding", "utf-8"))
        return RotatingFileHandler(
            filepath,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding=encoding,
        )


def _level_from_str(level_str: str) -> int:
    """Convert a level string like 'INFO' or 'DEBUG' to the int constant."""
    level = logging.getLevelName(level_str)
    if isinstance(level, int):
        return level
    return getattr(logging, level_str, logging.NOTSET)


def _build_dict_config(
    cfg: LoggingConfig,
    factory: HandlerFactory | None = None,
) -> dict[str, Any]:
    """Convert a :class:`LoggingConfig` into a ``dictConfig``-compatible dict."""
    if factory is None:
        factory = HandlerFactory()

    fmt = cfg.format or DEFAULT_FORMAT

    # formatters
    formatters = {"default": {"format": fmt}}

    # handlers — dictConfig needs explicit class/args, so we introspect
    # the concrete handler objects returned by the factory
    handlers: dict[str, dict[str, Any]] = {}
    handler_names: list[str] = []
    for h_cfg in cfg.handlers:
        handler_obj = factory.create(h_cfg)
        handlers[h_cfg.name] = _handler_to_dict_entry(handler_obj, h_cfg)
        handler_names.append(h_cfg.name)

    # named loggers — handlers list means propagate=False
    loggers: dict[str, dict[str, Any]] = {}
    for name, l_cfg in cfg.loggers.items():
        l_handlers = l_cfg.handlers if l_cfg.handlers else []
        level = _level_from_str(l_cfg.level) if l_cfg.level != "NOTSET" else logging.NOTSET
        loggers[name] = {
            "handlers": l_handlers,
            "level": level,
            "propagate": False if l_handlers else True,
        }

    # root
    root: dict[str, Any] = {
        "level": logging.INFO,
        "handlers": handler_names,
    }

    dict_cfg: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "root": root,
    }
    if loggers:
        dict_cfg["loggers"] = loggers

    return dict_cfg


def _handler_to_dict_entry(
    handler: logging.Handler,
    cfg: HandlerConfig,
) -> dict[str, Any]:
    """Convert a concrete handler into a dictConfig entry by introspecting its class."""
    level = _level_from_str(cfg.level) if cfg.level != "NOTSET" else logging.NOTSET

    entry: dict[str, Any] = {
        "level": level,
        "formatter": "default",
    }

    if isinstance(handler, RotatingFileHandler):
        entry["class"] = "logging.handlers.RotatingFileHandler"
        entry["filename"] = str(handler.baseFilename)
        entry["maxBytes"] = handler.maxBytes
        entry["backupCount"] = handler.backupCount
        entry["encoding"] = "utf-8"
    elif isinstance(handler, logging.StreamHandler):
        entry["class"] = "logging.StreamHandler"
    else:
        cls = type(handler)
        entry["class"] = f"{cls.__module__}.{cls.__qualname__}"

    return entry


def setup_logging(config: Any) -> None:
    """Configure Python logging from :class:`AppConfig.logging`.

    Requires a ``logging`` section in config. Raises :class:`ValueError` if
    ``config.logging`` is ``None``.
    """
    if config.logging is None:
        raise ValueError(
            "config.logging is None — add a 'logging' section to config.yaml. "
            "See config.example.yaml for the schema."
        )
    dict_cfg = _build_dict_config(config.logging)
    logging.config.dictConfig(dict_cfg)


def get_logger(name: str) -> logging.Logger:
    """Drop-in replacement for ``logging.getLogger(name)``."""
    return logging.getLogger(name)