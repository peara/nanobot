from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

from nanobot.channels.base import Channel
from nanobot.channels.github import GithubChannel
from nanobot.channels.telegram import TelegramChannel
from nanobot.config import load_config
from nanobot.core import BotCore


def setup_logging() -> None:
    log_dir = Path("data")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "nanobot.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def build_channels(config) -> dict[str, Channel]:
    channels: dict[str, Channel] = {}
    for cfg in config.channels:
        if cfg.type == "telegram":
            if not cfg.token:
                raise ValueError("Telegram channel requires token")
            channels["telegram"] = TelegramChannel(cfg.token)
            continue
        if cfg.type == "github":
            if not cfg.token:
                raise ValueError("GitHub channel requires token")
            opts = cfg.options or {}
            channels["github"] = GithubChannel(
                token=cfg.token,
                bot_username=opts.get("bot_username", "nanobot"),
                repo_owner=opts.get("repo_owner", ""),
                repo_name=opts.get("repo_name", ""),
                poll_interval=opts.get("poll_interval_seconds", 30),
                trigger=opts.get("trigger", "assignment"),
                label_name=opts.get("label_name", "nanobot"),
                opencode_url=opts.get("opencode_server_url", "http://localhost:4096"),
                opencode_username=opts.get("opencode_username", "opencode"),
                opencode_password=opts.get("opencode_password"),
            )
            continue
        raise ValueError(f"Unsupported channel type: {cfg.type}")
    return channels


async def run(config_path: str) -> None:
    config = load_config(config_path)
    channels = build_channels(config)
    core = BotCore(config, channels)

    for ch in channels.values():
        ch.set_handler(core.on_incoming)

    await core.start()
    for ch in channels.values():
        await ch.start()

    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    await stop_event.wait()

    for ch in channels.values():
        await ch.stop()
    await core.stop()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Run nanobot")
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml")
    args = parser.parse_args()
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
