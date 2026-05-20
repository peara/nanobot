from __future__ import annotations

import logging
import logging.config
import logging.handlers
from collections.abc import Generator
from pathlib import Path

import pytest

from nanobot.config import HandlerConfig, LoggerConfig, LoggingConfig
from nanobot.logging import (
    HandlerFactory,
    _build_dict_config,
    _level_from_str,
    get_logger,
    setup_logging,
)


@pytest.fixture(autouse=True)
def reset_logging() -> Generator[None]:
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    for name in ("nanobot.evaluator.io", "nanobot.test"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


class TestHandlerFactory:
    def test_console_handler(self) -> None:
        factory = HandlerFactory()
        cfg = HandlerConfig(name="my_console", type="console", level="INFO")
        handler = factory.create(cfg)
        assert isinstance(handler, logging.StreamHandler)

    def test_file_handler(self, tmp_path: Path) -> None:
        factory = HandlerFactory()
        log_file = tmp_path / "test.log"
        cfg = HandlerConfig(
            name="my_file",
            type="file",
            level="DEBUG",
            options={"filepath": str(log_file), "max_bytes": 1000, "backup_count": 2},
        )
        handler = factory.create(cfg)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 1000
        assert handler.backupCount == 2

    def test_file_handler_defaults(self, tmp_path: Path) -> None:
        factory = HandlerFactory()
        log_file = tmp_path / "test.log"
        cfg = HandlerConfig(
            name="my_file",
            type="file",
            level="DEBUG",
            options={"filepath": str(log_file)},
        )
        handler = factory.create(cfg)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 2_000_000
        assert handler.backupCount == 3

    def test_file_handler_requires_filepath(self) -> None:
        factory = HandlerFactory()
        cfg = HandlerConfig(name="bad", type="file", level="DEBUG")
        with pytest.raises(ValueError, match="filepath"):
            factory.create(cfg)

    def test_unknown_type_raises(self) -> None:
        factory = HandlerFactory()
        cfg = HandlerConfig(name="bad", type="unknown", level="INFO")
        with pytest.raises(ValueError, match="Unknown handler type"):
            factory.create(cfg)

    def test_register_custom_handler(self, tmp_path: Path) -> None:
        factory = HandlerFactory()

        class DummyHandler(logging.Handler):
            def __init__(self, label: str = "test") -> None:
                super().__init__()
                self.label = label

            def emit(self, record: logging.LogRecord) -> None:
                pass

        factory.register("dummy", lambda cfg: DummyHandler(label=cfg.options.get("label", "default")))
        cfg = HandlerConfig(name="my_dummy", type="dummy", level="INFO", options={"label": "custom"})
        handler = factory.create(cfg)
        assert isinstance(handler, DummyHandler)
        assert handler.label == "custom"


class TestBuildDictConfig:
    def test_root_setup(self, tmp_path: Path) -> None:
        cfg = LoggingConfig(
            format="%(message)s",
            handlers=[
                HandlerConfig(name="console", type="console", level="INFO"),
                HandlerConfig(
                    name="file",
                    type="file",
                    level="DEBUG",
                    options={"filepath": str(tmp_path / "test.log")},
                ),
            ],
        )
        result = _build_dict_config(cfg)
        assert result["version"] == 1
        assert "console" in result["handlers"]
        assert "file" in result["handlers"]
        assert result["root"]["level"] == logging.INFO

    def test_named_logger_propagate_false(self, tmp_path: Path) -> None:
        cfg = LoggingConfig(
            format="%(message)s",
            handlers=[HandlerConfig(name="h1", type="console", level="INFO")],
            loggers={"my.logger": LoggerConfig(handlers=["h1"], level="DEBUG")},
        )
        result = _build_dict_config(cfg)
        assert result["loggers"]["my.logger"]["propagate"] is False

    def test_named_logger_propagate_true_when_no_handlers(self) -> None:
        cfg = LoggingConfig(
            format="%(message)s",
            handlers=[HandlerConfig(name="h1", type="console", level="INFO")],
            loggers={"my.logger": LoggerConfig(handlers=[], level="NOTSET")},
        )
        result = _build_dict_config(cfg)
        assert result["loggers"]["my.logger"]["propagate"] is True

    def test_no_loggers_key_when_empty(self) -> None:
        cfg = LoggingConfig(
            format="%(message)s",
            handlers=[HandlerConfig(name="h1", type="console", level="INFO")],
        )
        result = _build_dict_config(cfg)
        assert "loggers" not in result


class TestLevelFromStr:
    @pytest.mark.parametrize(
        "level_str,expected",
        [("DEBUG", logging.DEBUG), ("INFO", logging.INFO), ("WARNING", logging.WARNING), ("ERROR", logging.ERROR)],
    )
    def test_known_levels(self, level_str: str, expected: int) -> None:
        assert _level_from_str(level_str) == expected

    def test_notset(self) -> None:
        assert _level_from_str("NOTSET") == logging.NOTSET


class TestSetupLogging:
    def test_raises_when_logging_is_none(self) -> None:
        from nanobot.config import AppConfig, ModelConfig

        app_config = AppConfig(
            assistant_name="Test",
            database_path="./data/test.db",
            scheduler_db_path="./data/scheduler.db",
            plan_db_path="./data/plans.db",
            skill_db_path="./data/skills.db",
            poll_interval_seconds=20,
            working_timezone="UTC",
            history_message_limit=24,
            history_char_limit=12000,
            model=ModelConfig(base_url="http://localhost:11434/v1", api_key="test", model="test"),
            channels=[],
            mcp_servers=[],
            logging=None,
        )
        with pytest.raises(ValueError, match="config.logging is None"):
            setup_logging(config=app_config)

    def test_with_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        from nanobot.config import AppConfig, ModelConfig

        app_config = AppConfig(
            assistant_name="Test",
            database_path="./data/test.db",
            scheduler_db_path="./data/scheduler.db",
            plan_db_path="./data/plans.db",
            skill_db_path="./data/skills.db",
            poll_interval_seconds=20,
            working_timezone="UTC",
            history_message_limit=24,
            history_char_limit=12000,
            model=ModelConfig(base_url="http://localhost:11434/v1", api_key="test", model="test"),
            channels=[],
            mcp_servers=[],
            logging=LoggingConfig(
                format="%(message)s",
                handlers=[
                    HandlerConfig(name="console", type="console", level="WARNING"),
                ],
            ),
        )
        setup_logging(config=app_config)
        root = logging.getLogger()
        assert root.level == logging.INFO
        console_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert len(console_handlers) == 1
        assert console_handlers[0].level == logging.WARNING

    def test_evaluator_logger_isolation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        from nanobot.config import AppConfig, ModelConfig

        log_file = tmp_path / "data" / "evaluator.log"
        app_config = AppConfig(
            assistant_name="Test",
            database_path="./data/test.db",
            scheduler_db_path="./data/scheduler.db",
            plan_db_path="./data/plans.db",
            skill_db_path="./data/skills.db",
            poll_interval_seconds=20,
            working_timezone="UTC",
            history_message_limit=24,
            history_char_limit=12000,
            model=ModelConfig(base_url="http://localhost:11434/v1", api_key="test", model="test"),
            channels=[],
            mcp_servers=[],
            logging=LoggingConfig(
                format="%(message)s",
                handlers=[
                    HandlerConfig(name="console", type="console", level="INFO"),
                    HandlerConfig(
                        name="file",
                        type="file",
                        level="DEBUG",
                        options={"filepath": str(tmp_path / "data" / "nanobot.log")},
                    ),
                    HandlerConfig(
                        name="evaluator_file", type="file", level="DEBUG", options={"filepath": str(log_file)}
                    ),
                ],
                loggers={
                    "nanobot.evaluator.io": LoggerConfig(handlers=["evaluator_file"], level="DEBUG"),
                },
            ),
        )
        setup_logging(config=app_config)
        eval_logger = logging.getLogger("nanobot.evaluator.io")
        assert eval_logger.propagate is False
        assert len(eval_logger.handlers) == 1


class TestGetLogger:
    def test_returns_logger_by_name(self) -> None:
        log = get_logger("test.module")
        assert log.name == "test.module"

    def test_returns_same_logger_for_same_name(self) -> None:
        log1 = get_logger("test.module")
        log2 = logging.getLogger("test.module")
        assert log1 is log2
