from __future__ import annotations

import argparse
import asyncio
import signal

from nanobot.channels.base import Channel
from nanobot.channels.github import GithubChannel
from nanobot.channels.telegram import TelegramChannel
from nanobot.config import load_config
from nanobot.core import BotCore
from nanobot.logging import setup_logging


def build_channels(config) -> tuple[dict[str, Channel], list]:
    channels: dict[str, Channel] = {}
    extra_hooks: list = []
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
                notification_chat_id=opts.get("notification_chat_id", config.owner_chat_id),
                telegram_channel=channels.get("telegram"),
            )
            continue
        if cfg.type == "file":
            opts = cfg.options or {}
            capture_tool_calls = opts.get("capture_tool_calls", False)
            from nanobot.channels.file import FileChannel, FileTraceHook

            file_channel = FileChannel(
                sessions_dir=opts.get("sessions_dir", "./data/chat/sessions"),
                session_id=opts.get("session_id"),
                capture_tool_calls=capture_tool_calls,
                poll_interval=opts.get("poll_interval", 0.5),
                user_id=opts.get("user_id", "file_user"),
            )
            channels["file"] = file_channel
            if capture_tool_calls:
                extra_hooks.append(FileTraceHook(out_file=file_channel._out_file))
            continue
        raise ValueError(f"Unsupported channel type: {cfg.type}")
    return channels, extra_hooks


async def run(config) -> None:
    channels, extra_hooks = build_channels(config)
    core = BotCore(config, channels)

    for ch in channels.values():
        ch.set_handler(core.on_incoming)

    await core.start()
    # Register any extra hooks (e.g. FileTraceHook) with the core after startup
    for hook in extra_hooks:
        core.tool_hooks.append(hook)
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
    parser = argparse.ArgumentParser(description="Run nanobot")
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    setup_logging(config)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
