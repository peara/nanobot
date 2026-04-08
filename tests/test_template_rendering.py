from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from nanobot.channels.base import IncomingMessage
from nanobot.config import load_config
from nanobot.core import BotCore

jinja2 = pytest.importorskip("jinja2")


def _await_process(bot: BotCore, message: IncomingMessage) -> None:
    asyncio.run(bot.on_incoming(message))
    asyncio.run(bot._process_one_message())


def _render_template(*, template_path: Path, messages: list[dict], tools: list[dict]) -> str:
    raw = template_path.read_text(encoding="utf-8")
    env = jinja2.Environment()
    template = env.from_string(raw)

    def _raise_exception(message: str) -> None:
        raise RuntimeError(message)

    return template.render(
        messages=messages,
        tools=tools,
        add_generation_prompt=True,
        raise_exception=_raise_exception,
    )


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class _TemplateCheckingLlm:
    def __init__(self, template_path: Path) -> None:
        self.template_path = template_path
        self.last_rendered_prompt = ""
        self.last_messages: list[dict] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
    ) -> dict:
        del response_format
        self.last_messages = messages
        self.last_rendered_prompt = _render_template(template_path=self.template_path, messages=messages, tools=tools)
        return {
            "content": "hi",
            "tool_calls": None,
        }


class _FakeMcp:
    def list_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "timer__time_now",
                    "description": "Get current date-time in ISO format for a timezone.",
                    "parameters": {
                        "type": "object",
                        "properties": {"timezone_name": {"type": "string"}},
                    },
                },
            }
        ]

    async def call_tool(self, fn_name: str, args: dict) -> str:
        del fn_name, args
        raise RuntimeError("No tools expected in this test")


def test_template_accepts_real_system_prompt_tool_and_user_hello(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(str(repo_root / "config.yaml"))
    config.database_path = str(tmp_path / "nanobot.db")
    config.scheduler_db_path = str(tmp_path / "scheduler.db")
    channel = _FakeChannel()
    bot = BotCore(config=config, channels={"telegram": channel})
    llm = _TemplateCheckingLlm(repo_root / "template.jinja")
    bot.llm = cast(Any, llm)
    bot.mcp = cast(Any, _FakeMcp())

    message = IncomingMessage(channel="telegram", chat_id="42", user_id="u1", text="hello")
    _await_process(bot, message)

    assert len(channel.sent) == 1
    assert channel.sent[0][1] == "hi"
    rendered = llm.last_rendered_prompt
    system_count = sum(1 for item in llm.last_messages if str(item.get("role")) == "system")
    assert system_count == 1
    assert "hello" in rendered
    assert "<tools>" in rendered
    assert "timer__time_now" in rendered
    assert "You are Nano, a personal assistant." in rendered


def test_template_raises_when_no_user_query_exists() -> None:
    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "assistant", "content": "I can help."},
    ]

    with pytest.raises(RuntimeError, match="No user query found in messages."):
        _render_template(
            template_path=Path(__file__).resolve().parents[1] / "template.jinja",
            messages=messages,
            tools=[],
        )
