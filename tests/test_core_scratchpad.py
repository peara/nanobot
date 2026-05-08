from __future__ import annotations

from nanobot.context_store import ContextStore
from nanobot.core_scratchpad import apply_scratchpad_tool_call


class _Cfg:
    working_timezone = "UTC"


class _Bot:
    def __init__(self, db_path: str) -> None:
        self.contexts = ContextStore(db_path)
        self.config = _Cfg()


def test_scratchpad_write_defaults_to_append_when_mode_missing(tmp_path) -> None:
    bot = _Bot(str(tmp_path / "bot.db"))
    state = apply_scratchpad_tool_call(
        bot,
        "telegram:42",
        {
            "goal": "Test goal",
            "current_step": "step",
            "tool_journal": ["entry"],
        },
    )

    assert state["goal"] == "Test goal"
    assert state["current_step"] == "step"
    assert state["tool_journal"] == ["entry"]


def test_scratchpad_write_normalizes_string_lists(tmp_path) -> None:
    bot = _Bot(str(tmp_path / "bot.db"))
    state = apply_scratchpad_tool_call(
        bot,
        "telegram:42",
        {
            "mode": "init",
            "known_facts": "fact A, fact B",
            "tool_journal": "<item><![CDATA[first note]]></item>",
        },
    )

    assert state["known_facts"] == ["fact A", "fact B"]
    assert state["tool_journal"] == ["first note"]
