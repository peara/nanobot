from __future__ import annotations

from nanobot.messages import SubagentResultMessage, UserMessage


def test_user_message_scope_property() -> None:
    msg = UserMessage(
        channel="telegram",
        chat_id="12345",
        text="hello",
        user_id="user1",
    )
    assert msg.scope == "telegram:12345"


def test_user_message_default_user_id() -> None:
    msg = UserMessage(
        channel="telegram",
        chat_id="12345",
        text="hello",
    )
    assert msg.user_id == ""


def test_user_message_frozen() -> None:
    msg = UserMessage(
        channel="telegram",
        chat_id="12345",
        text="hello",
    )
    try:
        msg.text = "modified"  # type: ignore
        raise AssertionError("Should not be able to modify frozen dataclass")
    except AttributeError:
        pass


def test_subagent_result_message_fields() -> None:
    msg = SubagentResultMessage(
        run_id="subagent-abc123",
        parent_scope="telegram:12345",
        success=True,
        summary="Task completed",
        tool_trace=[{"name": "timer__time_now", "args": {}, "result_preview": "12:00"}],
    )
    assert msg.run_id == "subagent-abc123"
    assert msg.parent_scope == "telegram:12345"
    assert msg.success is True
    assert msg.summary == "Task completed"
    assert len(msg.tool_trace) == 1


def test_subagent_result_message_frozen() -> None:
    msg = SubagentResultMessage(
        run_id="subagent-abc123",
        parent_scope="telegram:12345",
        success=True,
        summary="Task completed",
        tool_trace=[],
    )
    try:
        msg.success = False  # type: ignore
        raise AssertionError("Should not be able to modify frozen dataclass")
    except AttributeError:
        pass


def test_user_message_with_metadata() -> None:
    msg = UserMessage(
        channel="telegram",
        chat_id="12345",
        text="hello",
        metadata={"key": "value"},
    )
    assert msg.metadata == {"key": "value"}


def test_subagent_result_with_metadata() -> None:
    msg = SubagentResultMessage(
        run_id="subagent-abc123",
        parent_scope="telegram:12345",
        success=True,
        summary="Done",
        tool_trace=[],
        metadata={"duration_ms": 500},
    )
    assert msg.metadata == {"duration_ms": 500}
