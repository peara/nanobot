from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from nanobot.config import McpServerConfig, _deep_merge, load_config


class TestDeepMerge:
    def test_scalar_override(self) -> None:
        result = _deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_dict_merge(self) -> None:
        result = _deep_merge(
            {"model": {"base_url": "http://old", "api_key": "x"}},
            {"model": {"api_key": "y"}},
        )
        assert result == {"model": {"base_url": "http://old", "api_key": "y"}}

    def test_list_append(self) -> None:
        result = _deep_merge(
            {"mcp_servers": [{"name": "timer"}]},
            {"mcp_servers": [{"name": "reddit"}]},
        )
        assert result == {"mcp_servers": [{"name": "timer"}, {"name": "reddit"}]}

    def test_new_key_added(self) -> None:
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_empty_override(self) -> None:
        base = {"a": 1, "b": [1, 2]}
        result = _deep_merge(base, {})
        assert result == base

    def test_empty_base(self) -> None:
        result = _deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_nested_dict_merge(self) -> None:
        result = _deep_merge(
            {"logging": {"format": "old", "handlers": [{"name": "console"}]}},
            {"logging": {"handlers": [{"name": "file"}]}},
        )
        assert result == {
            "logging": {
                "format": "old",
                "handlers": [{"name": "console"}, {"name": "file"}],
            }
        }

    def test_scalar_overrides_list(self) -> None:
        result = _deep_merge({"a": [1, 2]}, {"a": "replaced"})
        assert result == {"a": "replaced"}

    def test_list_does_not_override_scalar(self) -> None:
        result = _deep_merge({"a": "scalar"}, {"a": [1, 2]})
        assert result == {"a": [1, 2]}


class TestLoadConfigOverride:
    def _write_yaml(self, path: Path, data: dict[str, Any]) -> None:
        path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")

    def test_override_merges_mcp_servers(self, tmp_path: Path) -> None:
        base_config = {
            "assistant_name": "Nano",
            "database_path": str(tmp_path / "nanobot.db"),
            "scheduler_db_path": str(tmp_path / "scheduler.db"),
            "plan_db_path": str(tmp_path / "plans.db"),
            "skill_db_path": str(tmp_path / "skills.db"),
            "poll_interval_seconds": 20,
            "working_timezone": "UTC",
            "history_message_limit": 24,
            "history_char_limit": 12000,
            "model": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "test",
                "model": "test-model",
            },
            "channels": [],
            "mcp_servers": [
                {"name": "timer", "command": "python", "args": ["-m", "nanobot.mcp_servers.timer.server"]},
            ],
        }
        override_config = {
            "mcp_servers": [
                {
                    "name": "reddit",
                    "command": "python",
                    "args": ["-m", "nanobot.mcp_servers.reddit.server"],
                    "required_env": ["PRAW_CLIENT_ID", "PRAW_CLIENT_SECRET", "PRAW_REFRESH_TOKEN"],
                    "env": {
                        "PRAW_CLIENT_ID": "${PRAW_CLIENT_ID}",
                        "PRAW_CLIENT_SECRET": "${PRAW_CLIENT_SECRET}",
                        "PRAW_REFRESH_TOKEN": "${PRAW_REFRESH_TOKEN}",
                    },
                },
            ],
        }

        config_path = tmp_path / "config.yaml"
        override_path = tmp_path / "config.override.yaml"
        self._write_yaml(config_path, base_config)
        self._write_yaml(override_path, override_config)

        config = load_config(str(config_path))
        server_names = [s.name for s in config.mcp_servers]
        assert "timer" in server_names
        assert "reddit" in server_names
        reddit = next(s for s in config.mcp_servers if s.name == "reddit")
        assert reddit.required_env == ["PRAW_CLIENT_ID", "PRAW_CLIENT_SECRET", "PRAW_REFRESH_TOKEN"]

    def test_override_scalar_value(self, tmp_path: Path) -> None:
        base_config = {
            "assistant_name": "Nano",
            "database_path": str(tmp_path / "nanobot.db"),
            "scheduler_db_path": str(tmp_path / "scheduler.db"),
            "plan_db_path": str(tmp_path / "plans.db"),
            "skill_db_path": str(tmp_path / "skills.db"),
            "poll_interval_seconds": 20,
            "working_timezone": "UTC",
            "history_message_limit": 24,
            "history_char_limit": 12000,
            "model": {"base_url": "http://old", "api_key": "test", "model": "old-model"},
            "channels": [],
            "mcp_servers": [],
        }
        override_config = {
            "poll_interval_seconds": 30,
            "model": {"model": "new-model"},
        }

        config_path = tmp_path / "config.yaml"
        override_path = tmp_path / "config.override.yaml"
        self._write_yaml(config_path, base_config)
        self._write_yaml(override_path, override_config)

        config = load_config(str(config_path))
        assert config.poll_interval_seconds == 30
        assert config.model.model == "new-model"
        assert config.model.base_url == "http://old"

    def test_no_override_file_loads_normally(self, tmp_path: Path) -> None:
        base_config = {
            "assistant_name": "Nano",
            "database_path": str(tmp_path / "nanobot.db"),
            "scheduler_db_path": str(tmp_path / "scheduler.db"),
            "plan_db_path": str(tmp_path / "plans.db"),
            "skill_db_path": str(tmp_path / "skills.db"),
            "poll_interval_seconds": 20,
            "working_timezone": "UTC",
            "history_message_limit": 24,
            "history_char_limit": 12000,
            "model": {"base_url": "http://localhost:11434/v1", "api_key": "test", "model": "test"},
            "channels": [],
            "mcp_servers": [],
        }

        config_path = tmp_path / "config.yaml"
        self._write_yaml(config_path, base_config)

        config = load_config(str(config_path))
        assert config.assistant_name == "Nano"
        assert config.mcp_servers == []


class TestMcpServerConfigRequiredEnv:
    def test_default_required_env_is_empty(self) -> None:
        server = McpServerConfig(name="test", command="echo")
        assert server.required_env == []

    def test_required_env_from_dict(self) -> None:
        server = McpServerConfig(
            name="reddit",
            command="python",
            args=["-m", "nanobot.mcp_servers.reddit.server"],
            required_env=["PRAW_CLIENT_ID", "PRAW_CLIENT_SECRET", "PRAW_REFRESH_TOKEN"],
            env={"PRAW_CLIENT_ID": "${PRAW_CLIENT_ID}"},
        )
        assert server.required_env == ["PRAW_CLIENT_ID", "PRAW_CLIENT_SECRET", "PRAW_REFRESH_TOKEN"]

    def test_required_env_loaded_from_yaml(self, tmp_path: Path) -> None:
        base_config = {
            "assistant_name": "Nano",
            "database_path": str(tmp_path / "nanobot.db"),
            "scheduler_db_path": str(tmp_path / "scheduler.db"),
            "plan_db_path": str(tmp_path / "plans.db"),
            "skill_db_path": str(tmp_path / "skills.db"),
            "poll_interval_seconds": 20,
            "working_timezone": "UTC",
            "history_message_limit": 24,
            "history_char_limit": 12000,
            "model": {"base_url": "http://localhost:11434/v1", "api_key": "test", "model": "test"},
            "channels": [],
            "mcp_servers": [
                {
                    "name": "test",
                    "command": "echo",
                    "required_env": ["A", "B"],
                },
            ],
        }
        config_path = tmp_path / "config.yaml"
        Path(config_path).write_text(yaml.dump(base_config), encoding="utf-8")
        config = load_config(str(config_path))
        assert config.mcp_servers[0].required_env == ["A", "B"]

    def test_override_path_from_config_yaml(self) -> None:
        result = str(Path("config.yaml")).removesuffix(".yaml") + ".override.yaml"
        assert result == "config.override.yaml"

    def test_override_path_nested(self) -> None:
        result = str(Path("/path/to/mybot.yaml")).removesuffix(".yaml") + ".override.yaml"
        assert result == "/path/to/mybot.override.yaml"
