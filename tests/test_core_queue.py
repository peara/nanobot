from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.channels.base import IncomingMessage
from nanobot.core import BotCore
from nanobot.messages import SubagentResultMessage, UserMessage
from nanobot.scripts.router import route_request
from nanobot.subagents.manager import SubagentRunResult
from nanobot.tools.base import Tool


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class _FakeTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake tool {self._name}"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any]) -> str:
        del args
        return "{}"


def _make_config() -> Any:
    import tempfile

    from nanobot.config import AppConfig, ModelConfig

    tmp = tempfile.mkdtemp()
    return AppConfig(
        assistant_name="TestBot",
        database_path=f"{tmp}/nanobot.db",
        scheduler_db_path=f"{tmp}/scheduler.db",
        plan_db_path=f"{tmp}/plans.db",
        skill_db_path=f"{tmp}/skills.db",
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost", api_key="test", model="test"),
        channels=[],
        mcp_servers=[],
        prompt_db_path=f"{tmp}/prompts.db",
    )


@pytest.fixture
def bot() -> BotCore:
    config = _make_config()
    channels: dict[str, Any] = {"telegram": _FakeChannel()}
    return BotCore(config, channels)


def test_user_message_scope() -> None:
    msg = UserMessage(channel="telegram", chat_id="123", text="hello")
    assert msg.scope == "telegram:123"


@pytest.mark.asyncio
async def test_on_incoming_enqueues_user_message(bot: BotCore) -> None:
    incoming = IncomingMessage(
        channel="telegram",
        chat_id="123",
        user_id="user1",
        text="hello world",
    )

    await bot.on_incoming(incoming)

    assert bot._message_queue.qsize() == 1
    msg = bot._message_queue.get_nowait()
    assert isinstance(msg, UserMessage)
    assert msg.channel == "telegram"
    assert msg.chat_id == "123"
    assert msg.text == "hello world"


@pytest.mark.asyncio
async def test_on_subagent_result_enqueues_message(bot: BotCore) -> None:
    result = SubagentResultMessage(
        run_id="subagent-test123",
        parent_scope="telegram:123",
        success=True,
        summary="Task done",
        tool_trace=[],
    )

    await bot.on_subagent_result(result)

    assert bot._message_queue.qsize() == 1
    msg = bot._message_queue.get_nowait()
    assert isinstance(msg, SubagentResultMessage)
    assert msg.run_id == "subagent-test123"


def test_should_notify_user_returns_false_for_unsuccessful(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=False,
        summary="Failed",
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_false_for_empty_summary(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="   ",
        tool_trace=[{"name": "tool", "args": {}, "result_preview": "ok"}],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_false_for_no_action_needed(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="NO_ACTION_NEEDED",
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_false_for_no_tools_and_short_summary(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="OK",  # Less than 50 chars
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is False


def test_should_notify_user_returns_true_for_no_tools_but_long_summary(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="This is a substantial message that exceeds fifty characters for length.",
        tool_trace=[],
    )
    assert bot._should_notify_user(msg) is True


def test_should_notify_user_returns_true_for_tools_used(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="test",
        parent_scope="telegram:123",
        success=True,
        summary="Done checking weather.",
        tool_trace=[{"name": "timer__time_now", "args": {}, "result_preview": "12:00"}],
    )
    assert bot._should_notify_user(msg) is True


@pytest.mark.asyncio
async def test_handle_subagent_result_notifies_when_should(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="subagent-test",
        parent_scope="telegram:123",
        success=True,
        summary="I completed the task.",
        tool_trace=[{"name": "timer__time_now", "args": {}, "result_preview": "12:00"}],
    )

    with patch.object(bot.memory, "add_message") as mock_add:
        with patch.object(bot, "_send", new_callable=AsyncMock) as mock_send:
            await bot._handle_subagent_result(msg)

            mock_add.assert_called_once_with("telegram:123", "assistant", "I completed the task.")
            mock_send.assert_called_once_with("telegram:123", "I completed the task.")


@pytest.mark.asyncio
async def test_handle_subagent_result_does_not_notify_when_should_not(bot: BotCore) -> None:
    msg = SubagentResultMessage(
        run_id="subagent-test",
        parent_scope="telegram:123",
        success=True,
        summary="NO_ACTION_NEEDED",
        tool_trace=[],
    )

    with patch.object(bot.memory, "add_message") as mock_add:
        with patch.object(bot, "_send", new_callable=AsyncMock) as mock_send:
            await bot._handle_subagent_result(msg)

            mock_add.assert_not_called()
            mock_send.assert_not_called()


def test_execution_strategy_detects_procedural_web_request() -> None:
    route = route_request(
        "Please extract GitHub issues from https://github.com/microsoft/vscode/issues and reuse script."
        ,
        [],
    )
    assert route.strategy == "procedural_web"


def test_execution_strategy_keeps_general_for_non_web_request() -> None:
    route = route_request("Set a reminder at 7pm tomorrow.", [])
    assert route.strategy == "general"


def test_filter_tools_for_procedural_strategy_blocks_search_web() -> None:
    tools = [
        {"type": "function", "function": {"name": "web__search_web"}},
        {"type": "function", "function": {"name": "web__search_scripts"}},
        {"type": "function", "function": {"name": "web__invoke_script"}},
    ]
    route = route_request("Extract issues from https://github.com/org/repo/issues", tools)
    names = [str(tool.get("function", {}).get("name", "")) for tool in route.tools]
    assert "web__search_web" not in names
    assert "web__search_scripts" in names
    assert "web__invoke_script" in names


def test_intent_detects_create_script_for_natural_language() -> None:
    route = route_request(
        "Please create a reusable NanoScript that extracts GitHub issues from a repo issues page with pagination.",
        [],
    )
    assert route.strategy == "procedural_web"
    assert route.intent == "create_script"


def test_intent_detects_create_script_for_reusable_workflow_language() -> None:
    text = (
        "I need a reusable browser workflow to extract GitHub issues with pagination "
        "from a repo issues URL."
    )
    route = route_request(text, [])
    assert route.strategy == "procedural_web"
    assert route.intent == "create_script"


def test_filter_tools_for_create_intent_keeps_only_create_and_scratchpad() -> None:
    tools = [
        {"type": "function", "function": {"name": "session__scratchpad_write"}},
        {"type": "function", "function": {"name": "web__create_script"}},
        {"type": "function", "function": {"name": "web__search_scripts"}},
        {"type": "function", "function": {"name": "web__invoke_script"}},
    ]
    route = route_request("Please create a reusable NanoScript for GitHub issues.", tools)
    names = [str(tool.get("function", {}).get("name", "")) for tool in route.tools]
    assert names == ["session__scratchpad_write", "web__create_script"]


@pytest.mark.asyncio
async def test_process_procedural_request_prefers_nanoscript_tools(bot: BotCore) -> None:
    captured: dict[str, Any] = {}
    bot.tools.register(_FakeTool("web__search_web"))
    bot.tools.register(_FakeTool("web__search_scripts"))
    bot.tools.register(_FakeTool("web__invoke_script"))

    async def _fake_execute(run, messages, tools, response_format=None, procedural_intent="default"):  # type: ignore[no-untyped-def]
        del run, response_format
        captured["messages"] = messages
        captured["tools"] = tools
        captured["procedural_intent"] = procedural_intent
        return SubagentRunResult(run_id="run-test", success=True, reply="done", tool_trace=[])

    with patch.object(bot.subagent_manager, "execute", new=AsyncMock(side_effect=_fake_execute)):
        with patch.object(bot, "_send", new_callable=AsyncMock):
            await bot._process(
                "telegram:123",
                "Please extract GitHub issues from https://github.com/microsoft/vscode/issues and reuse it.",
            )

    tools = captured["tools"]
    names = [str(tool.get("function", {}).get("name", "")) for tool in tools]
    assert "web__search_web" not in names
    assert any("web__search_scripts" == name for name in names)
    assert any("web__invoke_script" == name for name in names)

    messages = captured["messages"]
    assert any("NanoScript procedural memory" in str(message.get("content", "")) for message in messages)
    assert captured["procedural_intent"] == "default"


@pytest.mark.asyncio
async def test_process_create_script_request_injects_create_policy_and_toolset(bot: BotCore) -> None:
    captured: dict[str, Any] = {}
    bot.tools.register(_FakeTool("web__create_script"))
    bot.tools.register(_FakeTool("web__search_scripts"))
    bot.tools.register(_FakeTool("web__invoke_script"))

    async def _fake_execute(run, messages, tools, response_format=None, procedural_intent="default"):  # type: ignore[no-untyped-def]
        del run, response_format
        captured["messages"] = messages
        captured["tools"] = tools
        captured["procedural_intent"] = procedural_intent
        return SubagentRunResult(run_id="run-test", success=True, reply="done", tool_trace=[])

    with patch.object(bot.subagent_manager, "execute", new=AsyncMock(side_effect=_fake_execute)):
        with patch.object(bot, "_send", new_callable=AsyncMock):
            await bot._process(
                "telegram:123",
                "Please create a reusable NanoScript that extracts GitHub issues from a repo issues page with pagination. Save it for reuse.",
            )

    tools = captured["tools"]
    names = [str(tool.get("function", {}).get("name", "")) for tool in tools]
    assert names == ["session__scratchpad_write", "web__create_script"]
    messages = captured["messages"]
    assert any("Call web__create_script in this turn" in str(message.get("content", "")) for message in messages)
    assert captured["procedural_intent"] == "create_script"
