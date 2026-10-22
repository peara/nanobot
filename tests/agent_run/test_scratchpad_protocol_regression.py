from __future__ import annotations

import asyncio
import json
import tempfile
from types import SimpleNamespace
from typing import Any

from nanobot.agent_run import AgentRun
from nanobot.core_scratchpad import SCRATCHPAD_TOOL_NAME
from nanobot.prompts import PromptStore
from nanobot.tools.base import Tool
from nanobot.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Scratchpad protocol regression suite
#
# Captured from a real production incident: run-297119136c,
# scope=telegram:500506690, 2026-06-08 15:39:48 + 15:40:00 (see
# data/llm.log lines 10651-10667). The user asked the bot to build a
# script for seller credibility. The bot called session__scratchpad_write
# with mode="init" (correct), then on the very next turn emitted a
# 1109-char text reply with finish_reason="stop" and ZERO tool calls.
# It never called mode="append" or mode="finalize". The user got an
# empty narrative promise instead of a real script.
#
# Two compounding issues:
#   A) The prompt at src/nanobot/prompts/defaults.py:64-69 is explicit
#      that work-needed turns must end with mode="finalize" before the
#      final answer. The model violated this.
#   B) The continue call's tool list (data/llm.log:10658) did NOT include
#      session__scratchpad_write. _tools_for_chat at agent_run.py:218
#      strips scratchpad unless round_used_external_tool was True, and
#      an init-only round never sets that flag. So the model was told
#      "you must call finalize" but finalize was not in its schema.
#
# The recovery shape (implemented in src/nanobot/agent_run.py) is to
# detect the init/append-then-text-only-stop pattern, inject a correction
# system message, re-prompt with the scratchpad tool re-included, and
# cap retries at MAX_SCRATCHPAD_PROTOCOL_RETRIES (2). After the cap,
# return SCRATCHPAD_PROTOCOL_ABORT_REPLY.
#
# These tests document the contract:
#   - test_scratchpad_protocol_happy_path: baseline; the protocol works
#     end-to-end when the model follows it.
#   - test_scratchpad_protocol_init_only_continue_recovers_after_nudge:
#     positive case; on a single text-only-stop violation, the loop
#     nudges the model and the model self-corrects via finalize.
#   - test_scratchpad_protocol_init_only_continue_cap_exceeded_aborts_safely:
#     cap-exceeded case; the model keeps violating past the cap, and
#     the loop returns SCRATCHPAD_PROTOCOL_ABORT_REPLY.
# ---------------------------------------------------------------------------


class _FakeContexts:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], Any] = {}

    def get(self, kind: str, scope: str, key: str) -> Any:
        return self._data.get((kind, scope, key))

    def put(self, kind: str, scope: str, key: str, value: Any) -> None:
        self._data[(kind, scope, key)] = value


class _FakeLlm:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = replies
        self._idx = 0

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
        *,
        scope: str | None = None,
        cancel_token: Any | None = None,
    ) -> dict:
        del messages, tools, response_format, scope, cancel_token
        if self._idx >= len(self._replies):
            raise RuntimeError("No fake LLM reply left")
        reply = self._replies[self._idx]
        self._idx += 1
        return reply


class _RecordingFakeLlm(_FakeLlm):
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        super().__init__(replies)
        self.calls_messages: list[list[dict[str, Any]]] = []
        self.calls_tools: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        response_format: dict[str, Any] | None = None,
        *,
        scope: str | None = None,
        cancel_token: Any | None = None,
    ) -> dict:
        self.calls_messages.append(messages)
        self.calls_tools.append(tools)
        return await super().chat(messages, tools, response_format, scope=scope, cancel_token=cancel_token)


class _FakeHost:
    def __init__(self, llm: _FakeLlm) -> None:
        from nanobot.hooks import ToolCallEvent

        self.config = SimpleNamespace(working_timezone="UTC")
        self.llm = llm
        self.contexts = _FakeContexts()
        self.tools = ToolRegistry()
        self.active_requests: dict[str, Any] = {}
        self.tool_hooks: list[Any] = []
        self.tool_guards: list[Any] = []
        self.events: list[ToolCallEvent] = []
        self._temp_dir = tempfile.mkdtemp()
        self.prompts = PromptStore(f"{self._temp_dir}/prompts.db", seed_defaults=True)

    async def _dispatch_after_tool_call(self, event: Any) -> None:
        self.events.append(event)


class _OkTool(Tool):
    """External tool that always succeeds. Tool names NOT registered on the
    fake host raise inside ToolRegistry.call, which the dispatcher catches —
    that is how the walks below simulate a failed external attempt."""

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
        return "page content: sunny 24C, light wind"


# Generic scenario data. A multi-step task that requires the model to
# do real work (web search) and report back, so any deviation from the
# scratchpad protocol is detectable in the trace. Not coupled to a
# specific production incident — see the file header for forensic context.
SCRATCHPAD_PROTOCOL_USER_MESSAGE = (
    "What's the weather in Tokyo right now? Search the web and tell me whether it's a good day for a picnic."
)

SCRATCHPAD_PROTOCOL_TOOL_LIST: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": SCRATCHPAD_TOOL_NAME}},
    {"type": "function", "function": {"name": "web__search_web"}},
    {"type": "function", "function": {"name": "web__read_page"}},
]

SCRATCHPAD_PROTOCOL_SCOPE = "telegram:eval-scratchpad-protocol"


def _scratchpad_call(call_id: str, mode: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": SCRATCHPAD_TOOL_NAME,
            "arguments": json.dumps({"mode": mode, **fields}),
        },
    }


def _ext_call(call_id: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}),
        },
    }


# ---------------------------------------------------------------------------
# Baseline: the protocol works end-to-end when the model follows it.
# ---------------------------------------------------------------------------
def test_scratchpad_protocol_happy_path_init_tool_append_finalize() -> None:
    """Model follows the full protocol: init -> ext tool -> append -> finalize -> answer."""
    init_args = {
        "current_step": "Searching for Tokyo weather.",
        "goal": "Tell the user whether it's a good day for a picnic in Tokyo.",
        "known_facts": [],
        "next_step": "Search for current Tokyo weather.",
    }
    append_args = {
        "current_step": "Searched; sunny, 24C, light wind.",
        "known_facts": ["Tokyo current weather: sunny, 24C, light wind."],
        "next_step": "Compose the picnic recommendation.",
        "tool_journal": ["web__search_web('Tokyo weather') -> sunny, 24C, light wind"],
    }
    finalize_args = {
        "current_step": "Composing recommendation.",
        "next_step": "Reply to user.",
        "tool_journal": ["web__search_web('Tokyo weather') -> sunny, 24C, light wind"],
    }
    final_answer = "It's sunny and 24C in Tokyo with light wind — great day for a picnic!"

    replies: list[dict[str, Any]] = [
        {"content": "", "tool_calls": [_scratchpad_call("c1", "init", **init_args)]},
        {"content": "", "tool_calls": [_ext_call("c2", "web__search_web", {"query": "Tokyo weather"})]},
        {"content": "", "tool_calls": [_scratchpad_call("c3", "append", **append_args)]},
        {"content": "", "tool_calls": [_scratchpad_call("c4", "finalize", **finalize_args)]},
        {"content": final_answer, "tool_calls": None},
    ]
    llm = _RecordingFakeLlm(replies)
    host = _FakeHost(llm)
    run = AgentRun(host)

    text, trace = asyncio.run(
        run.run(
            scope_for_tools=SCRATCHPAD_PROTOCOL_SCOPE,
            messages=[{"role": "user", "content": SCRATCHPAD_PROTOCOL_USER_MESSAGE}],
            tools=SCRATCHPAD_PROTOCOL_TOOL_LIST,
        )
    )

    assert text == final_answer
    assert [item["name"] for item in trace] == [
        SCRATCHPAD_TOOL_NAME,
        "web__search_web",
        SCRATCHPAD_TOOL_NAME,
        SCRATCHPAD_TOOL_NAME,
    ]
    assert [item["name"] for item in trace if item["name"] == SCRATCHPAD_TOOL_NAME] == [
        SCRATCHPAD_TOOL_NAME,
        SCRATCHPAD_TOOL_NAME,
        SCRATCHPAD_TOOL_NAME,
    ]
    # The finalize call must route through the tools-stripped finalize path.
    finalize_call = llm.calls_messages[4]
    assert llm.calls_tools[4] == []
    assert any("completed your research" in str(m.get("content", "")) for m in finalize_call)


# ---------------------------------------------------------------------------
# Positive case for the fix: when the model emits text-only stop
# after init, the loop should nudge it, the model should self-correct,
# and the user should get a real answer. See src/nanobot/agent_run.py
# run() post-loop violation handling.
# ---------------------------------------------------------------------------
def test_scratchpad_protocol_init_only_continue_recovers_after_nudge() -> None:
    init_args = {
        "current_step": "Thinking about the picnic recommendation.",
        "goal": "Tell the user whether it's a good day for a picnic in Tokyo.",
        "known_facts": [],
        "next_step": "Search for current Tokyo weather.",
    }
    finalize_args = {
        "current_step": "Composing recommendation.",
        "next_step": "Reply to user.",
        "tool_journal": [],
    }
    final_answer = "I did not search the web (model was nudged), so I cannot give a real answer."

    replies: list[dict[str, Any]] = [
        # Call 1: initial LLM -> init
        {"content": "", "tool_calls": [_scratchpad_call("c1", "init", **init_args)]},
        # Call 2: continue -> text-only stop (the protocol violation)
        {"content": "I'll get started on this soon.", "tool_calls": None},
        # Call 3: after nudge, model self-corrects with finalize
        {"content": "", "tool_calls": [_scratchpad_call("c3", "finalize", **finalize_args)]},
        # Call 4: tools-stripped finalize call -> final answer
        {"content": final_answer, "tool_calls": None},
    ]
    llm = _RecordingFakeLlm(replies)
    host = _FakeHost(llm)
    run = AgentRun(host)

    text, trace = asyncio.run(
        run.run(
            scope_for_tools=SCRATCHPAD_PROTOCOL_SCOPE,
            messages=[{"role": "user", "content": SCRATCHPAD_PROTOCOL_USER_MESSAGE}],
            tools=SCRATCHPAD_PROTOCOL_TOOL_LIST,
        )
    )

    # The narrative text must NOT be returned to the user.
    assert text != "I'll get started on this soon."
    assert text == final_answer
    # The loop should have made at least 3 LLM calls (initial, continue,
    # and the recovery prompt after the nudge).
    assert len(llm.calls_messages) >= 3
    # The recovery prompt's tool list must include scratchpad so the
    # model can call finalize. (The fix re-includes it on the recovery
    # continue call by forcing include_scratchpad_prompt=True.)
    recovery_call_tools = {t["function"]["name"] for t in llm.calls_tools[2]}
    assert SCRATCHPAD_TOOL_NAME in recovery_call_tools
    # The recovery prompt must actually carry the strict correction system
    # message (issue #45's core bug: the correction was appended to
    # `messages` but never re-derived into prepared_messages, so the
    # recovery call was byte-identical to the violating call).
    from nanobot.core import SCRATCHPAD_STRICT_CORRECTION  # noqa: PLC0415

    recovery_call_messages = llm.calls_messages[2]
    assert any(
        m.get("role") == "system" and m.get("content") == SCRATCHPAD_STRICT_CORRECTION for m in recovery_call_messages
    ), "recovery prompt is missing the strict correction system message"


# ---------------------------------------------------------------------------
# Cap-exceeded case: when the model keeps emitting text-only stop past
# the retry cap, the loop must abort with the safe
# SCRATCHPAD_PROTOCOL_ABORT_REPLY rather than ship the narrative text.
# ---------------------------------------------------------------------------
def test_scratchpad_protocol_init_only_continue_cap_exceeded_aborts_safely() -> None:
    init_args = {
        "current_step": "Thinking about the picnic recommendation.",
        "goal": "Tell the user whether it's a good day for a picnic in Tokyo.",
        "known_facts": [],
        "next_step": "Search for current Tokyo weather.",
    }
    from nanobot.core import SCRATCHPAD_PROTOCOL_ABORT_REPLY  # noqa: PLC0415

    # Model keeps emitting text-only stop on every recovery prompt.
    replies: list[dict[str, Any]] = [
        # Call 1: initial -> init
        {"content": "", "tool_calls": [_scratchpad_call("c1", "init", **init_args)]},
        # Call 2: continue -> text-only stop
        {"content": "I'll get started on this soon.", "tool_calls": None},
        # Call 3: after first nudge -> still text-only stop
        {"content": "Let me think more about this.", "tool_calls": None},
        # Call 4: after second nudge -> still text-only stop
        {"content": "I'm still thinking.", "tool_calls": None},
    ]
    llm = _RecordingFakeLlm(replies)
    host = _FakeHost(llm)
    run = AgentRun(host)

    text, trace = asyncio.run(
        run.run(
            scope_for_tools=SCRATCHPAD_PROTOCOL_SCOPE,
            messages=[{"role": "user", "content": SCRATCHPAD_PROTOCOL_USER_MESSAGE}],
            tools=SCRATCHPAD_PROTOCOL_TOOL_LIST,
        )
    )

    # The user must get the safe abort message, not any of the model's
    # narrative text.
    assert text == SCRATCHPAD_PROTOCOL_ABORT_REPLY
    assert text != "I'll get started on this soon."
    assert text != "Let me think more about this."
    assert text != "I'm still thinking."
    # Companion to the recovery-test assertion (issue #45): every recovery
    # prompt must carry the strict correction — the cap is reached through
    # nudges that the model must actually see.
    from nanobot.core import SCRATCHPAD_STRICT_CORRECTION  # noqa: PLC0415

    assert len(llm.calls_messages) == 4
    for recovery_messages in (llm.calls_messages[2], llm.calls_messages[3]):
        assert any(
            m.get("role") == "system" and m.get("content") == SCRATCHPAD_STRICT_CORRECTION for m in recovery_messages
        ), "recovery prompt is missing the strict correction system message"


# ---------------------------------------------------------------------------
# Batch semantics: how the model groups calls into messages must not
# change the protocol outcome. An external attempt counts as work —
# success or failure alike — so a failed attempt resolves init-pending
# and the honest report ships instead of tripping the strict nudge.
# ---------------------------------------------------------------------------


def test_failed_tool_after_init_in_same_batch_ships_honest_report() -> None:
    """[init, ext(fails)] in ONE message, then an honest text stop.

    The failed attempt is real work: it resolves init-pending, the state is
    plain AppendDue, and the text stop takes the clean-exit edge. (The old
    round-based encoding treated a failed attempt as "never tried", so this
    walk aborted the turn while the same events split across two messages
    shipped — message grouping decided the protocol outcome.)"""
    init_args = {
        "goal": "Tell the user whether it's a good day for a picnic in Tokyo.",
        "current_step": "Searching for Tokyo weather.",
        "known_facts": [],
    }
    honest_reply = "The web search tool failed, so I cannot answer right now."

    replies: list[dict[str, Any]] = [
        {
            "content": "",
            "tool_calls": [
                _scratchpad_call("c1", "init", **init_args),
                _ext_call("c2", "web__search_web", {"query": "Tokyo weather"}),
            ],
        },
        {"content": honest_reply, "tool_calls": None},
    ]
    llm = _RecordingFakeLlm(replies)
    host = _FakeHost(llm)
    run = AgentRun(host)

    text, trace = asyncio.run(
        run.run(
            scope_for_tools=SCRATCHPAD_PROTOCOL_SCOPE,
            messages=[{"role": "user", "content": SCRATCHPAD_PROTOCOL_USER_MESSAGE}],
            tools=SCRATCHPAD_PROTOCOL_TOOL_LIST,
        )
    )

    assert text == honest_reply
    # No nudge fired: initial call + one continue call, nothing else.
    assert len(llm.calls_messages) == 2
    assert [item["name"] for item in trace] == [SCRATCHPAD_TOOL_NAME, "web__search_web"]


def test_failed_tool_after_init_in_separate_messages_ships_honest_report() -> None:
    """[init] then [ext(fails)] in SEPARATE messages, then the same honest
    text stop. Walks the same diagram as the batched variant — the outcome
    must be identical, which is the point of attempt = work."""
    init_args = {
        "goal": "Tell the user whether it's a good day for a picnic in Tokyo.",
        "current_step": "Searching for Tokyo weather.",
        "known_facts": [],
    }
    honest_reply = "The web search tool failed, so I cannot answer right now."

    replies: list[dict[str, Any]] = [
        {"content": "", "tool_calls": [_scratchpad_call("c1", "init", **init_args)]},
        {"content": "", "tool_calls": [_ext_call("c2", "web__search_web", {"query": "Tokyo weather"})]},
        {"content": honest_reply, "tool_calls": None},
    ]
    llm = _RecordingFakeLlm(replies)
    host = _FakeHost(llm)
    run = AgentRun(host)

    text, trace = asyncio.run(
        run.run(
            scope_for_tools=SCRATCHPAD_PROTOCOL_SCOPE,
            messages=[{"role": "user", "content": SCRATCHPAD_PROTOCOL_USER_MESSAGE}],
            tools=SCRATCHPAD_PROTOCOL_TOOL_LIST,
        )
    )

    assert text == honest_reply
    assert len(llm.calls_messages) == 3
    assert [item["name"] for item in trace] == [SCRATCHPAD_TOOL_NAME, "web__search_web"]


def test_finalize_in_batch_with_external_tool_routes_to_finalize_path() -> None:
    """[finalize, web__read_page] in one message.

    The external call still dispatches (its tool_call_id needs its tool
    message), but finalize is terminal within its round: the loop routes to
    the no-tools finalize call instead of a tools-enabled continue. (The old
    round summary took the used-external branch first and skipped the
    finalize path entirely for this batch shape.)"""
    final_answer = "It's sunny and 24C — great day for a picnic."
    replies: list[dict[str, Any]] = [
        {
            "content": "",
            "tool_calls": [
                _scratchpad_call("c1", "finalize", goal="Tokyo picnic weather"),
                _ext_call("c2", "web__read_page", {"url": "https://example.com"}),
            ],
        },
        {"content": final_answer, "tool_calls": None},
    ]
    llm = _RecordingFakeLlm(replies)
    host = _FakeHost(llm)
    host.tools.register(_OkTool("web__read_page"))
    run = AgentRun(host)

    text, trace = asyncio.run(
        run.run(
            scope_for_tools=SCRATCHPAD_PROTOCOL_SCOPE,
            messages=[{"role": "user", "content": SCRATCHPAD_PROTOCOL_USER_MESSAGE}],
            tools=SCRATCHPAD_PROTOCOL_TOOL_LIST,
        )
    )

    assert text == final_answer
    # Both calls dispatched; the second LLM call is the no-tools finalize
    # prompt, not a tools-enabled continue.
    assert [item["name"] for item in trace] == [SCRATCHPAD_TOOL_NAME, "web__read_page"]
    assert len(llm.calls_messages) == 2
    assert llm.calls_tools[1] == []
    assert any("completed your research" in str(m.get("content", "")) for m in llm.calls_messages[1])


def test_failed_then_successful_tool_in_one_batch_recovers() -> None:
    """[web__search_web(fails), web__read_page(succeeds)] in one message.

    Within-batch recovery: both calls dispatch in order, and the honest
    answer built from the successful read ships. (A "drop the rest of the
    batch after a failed call" rule was considered and rejected — it would
    destroy this recovery.)"""
    answer = "The search failed but I read the page: sunny 24C."
    replies: list[dict[str, Any]] = [
        {
            "content": "",
            "tool_calls": [
                _ext_call("c1", "web__search_web", {"query": "Tokyo weather"}),
                _ext_call("c2", "web__read_page", {"url": "https://example.com"}),
            ],
        },
        {"content": answer, "tool_calls": None},
    ]
    llm = _RecordingFakeLlm(replies)
    host = _FakeHost(llm)
    host.tools.register(_OkTool("web__read_page"))  # search stays unregistered -> fails
    run = AgentRun(host)

    text, trace = asyncio.run(
        run.run(
            scope_for_tools=SCRATCHPAD_PROTOCOL_SCOPE,
            messages=[{"role": "user", "content": SCRATCHPAD_PROTOCOL_USER_MESSAGE}],
            tools=SCRATCHPAD_PROTOCOL_TOOL_LIST,
        )
    )

    assert text == answer
    assert [item["name"] for item in trace] == ["web__search_web", "web__read_page"]
    assert len(llm.calls_messages) == 2
