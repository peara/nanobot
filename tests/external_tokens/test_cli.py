from __future__ import annotations

import subprocess
import sys

import pytest


class TestCliArgparse:
    """Tests for the CLI argparse setup."""

    def test_missing_service_exits_with_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "nanobot.external_tokens.cli"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_reddit_requires_client_id(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "nanobot.external_tokens.cli", "reddit"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "client-id" in result.stderr or "required" in result.stderr.lower()

    def test_reddit_requires_client_secret(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "nanobot.external_tokens.cli", "reddit", "--client-id", "test"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "client-secret" in result.stderr or "required" in result.stderr.lower()

    def test_reddit_help_message(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "nanobot.external_tokens.cli", "reddit", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "client-id" in result.stdout
        assert "client-secret" in result.stdout
        assert "redirect-port" in result.stdout

    def test_main_help_message(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "nanobot.external_tokens.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "reddit" in result.stdout

    def test_reddit_default_port_and_scopes(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "nanobot.external_tokens.cli", "reddit", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "8080" in result.stdout


class TestMainFunction:
    """Tests for the main() function directly."""

    def test_main_with_no_args_exits(self) -> None:
        with pytest.raises(SystemExit):
            from nanobot.external_tokens.cli import main

            sys.argv = ["cli"]
            main()

    def test_main_with_invalid_service(self) -> None:
        with pytest.raises(SystemExit):
            from nanobot.external_tokens.cli import main

            sys.argv = ["cli", "nonexistent"]
            main()
