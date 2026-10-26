"""End-to-end instrumentation tests over a real BotCore (issue #48).

The four span sites are exercised through the real flow — SubagentManager.execute,
AgentRun's LLM loop, ToolCallDispatcher.dispatch, and run_delegate_task — with
only the OpenAI transport stubbed. This is the acceptance-criteria test: a run
with LLM rounds, tool calls, and a delegated child produces a full parent→child
span tree in SQLite, joinable with subagent_runs via run_id.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nanobot.config import AppConfig, ChannelConfig, McpServerConfig, ModelConfig, TracingConfig
from nanobot.core import BotCore
from nanobot.plans import process_plan
from nanobot.subagents.delegate_tool import DEPTH_REFUSED_MESSAGE, run_delegate_task
from nanobot.tracing import get_tracer, semconv
from nanobot.tracing.store import TraceStore


def _build_config(
    tmp_path: Path,
    *,
    capture_payloads: bool = False,
    owner_chat_id: int = 0,
) -> AppConfig:
    return AppConfig(
        assistant_name="Nano",
        database_path=str(tmp_path / "nanobot.db"),
        scheduler_db_path=str(tmp_path / "scheduler.db"),
        plan_db_path=str(tmp_path / "plans.db"),
        skill_db_path=str(tmp_path / "skills.db"),
        poll_interval_seconds=20,
        working_timezone="UTC",
        history_message_limit=24,
        history_char_limit=12000,
        model=ModelConfig(base_url="http://localhost:11434/v1", api_key="dummy", model="stub-model"),
        channels=[ChannelConfig(type="telegram")],
        mcp_servers=[McpServerConfig(name="none", command="echo", args=["ok"])],
        prompt_db_path=str(tmp_path / "prompts.db"),
        owner_chat_id=owner_chat_id,
        tracing=TracingConfig(
            db_path=str(tmp_path / "traces.db"),
            capture_payloads=capture_payloads,
        ),
    )


def _fake_response(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
) -> Any:
    raw = {"role": "assistant", "content": content, "tool_calls": tool_calls}

    def _dump() -> dict[str, Any]:
        return dict(raw)

    message = SimpleNamespace(content=content, tool_calls=tool_calls, model_dump=_dump)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage, model="stub-model")


def _delegate_call(goal: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "delegate_task", "arguments": json.dumps({"goal": goal})},
        }
    ]


class _StubCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if self._idx >= len(self._responses):
            raise RuntimeError("No stubbed LLM response left")
        response = self._responses[self._idx]
        self._idx += 1
        if isinstance(response, Exception):
            raise response
        return response


def _make_bot(tmp_path: Path, responses: list[Any]) -> BotCore:
    bot = BotCore(config=_build_config(tmp_path), channels={"telegram": cast(Any, object())})
    stub = _StubCompletions(responses)
    bot.llm.client = cast(Any, SimpleNamespace(chat=SimpleNamespace(completions=stub)))
    return bot


def _spans(tmp_path: Path) -> TraceStore:
    return TraceStore(str(tmp_path / "traces.db"))


class TestFullSpanTree:
    @pytest.mark.asyncio
    async def test_parent_rounds_tool_calls_and_delegated_child_form_one_trace(self, tmp_path: Path) -> None:
        responses = [
            _fake_response(tool_calls=_delegate_call("child sub-task"), prompt_tokens=500, completion_tokens=30),
            _fake_response(content="child done", prompt_tokens=200, completion_tokens=10),
            _fake_response(content="parent final answer", prompt_tokens=600, completion_tokens=40),
        ]
        bot = _make_bot(tmp_path, responses)
        parent = bot.subagent_manager.spawn(scope="telegram:1", goal="orchestrate the job")
        await bot.subagent_manager.execute(
            parent,
            [{"role": "user", "content": "do the job"}],
            bot._list_openai_tools([]),
        )

        store = _spans(tmp_path)
        parent_spans = store.list_by_run(parent.id)
        parent_names = [s.name for s in parent_spans]
        assert semconv.OP_INVOKE_AGENT in parent_names
        assert semconv.OP_CHAT in parent_names
        assert semconv.OP_EXECUTE_TOOL in parent_names
        assert semconv.SPAN_DELEGATE_TASK in parent_names

        delegate_span = next(s for s in parent_spans if s.name == semconv.SPAN_DELEGATE_TASK)
        child_run_id = delegate_span.attributes()[semconv.CHILD_RUN_ID]

        child_spans = store.list_by_run(child_run_id)
        assert [s.name for s in child_spans] == [semconv.OP_INVOKE_AGENT, semconv.OP_CHAT]

        all_spans = [*parent_spans, *child_spans]
        assert len({s.trace_id for s in all_spans}) == 1

        root = next(s for s in parent_spans if s.name == semconv.OP_INVOKE_AGENT)
        assert root.parent_span_id is None
        assert root.attributes()[semconv.RUN_GOAL] == "orchestrate the job"
        assert root.attributes()[semconv.RUN_DEPTH] == 0

        execute_tool_span = next(s for s in parent_spans if s.name == semconv.OP_EXECUTE_TOOL)
        assert execute_tool_span.attributes()[semconv.GEN_AI_TOOL_NAME] == "delegate_task"
        assert execute_tool_span.parent_span_id == root.span_id
        assert delegate_span.parent_span_id == execute_tool_span.span_id

        child_root = child_spans[0]
        assert child_root.parent_span_id == delegate_span.span_id
        assert child_root.attributes()[semconv.PARENT_RUN_ID] == parent.id
        assert child_root.attributes()[semconv.RUN_DEPTH] == 1
        assert child_root.attributes()[semconv.RUN_GOAL] == "child sub-task"
        assert child_spans[1].parent_span_id == child_root.span_id

        chat_spans = sorted(
            (s for s in parent_spans if s.name == semconv.OP_CHAT),
            key=lambda s: s.start_ts,
        )
        first_chat = chat_spans[0]
        assert first_chat.attributes()[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 500
        assert first_chat.attributes()[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 30
        assert first_chat.attributes()[semconv.GEN_AI_REQUEST_MODEL] == "stub-model"
        assert first_chat.attributes()[semconv.GEN_AI_RESPONSE_FINISH_REASONS] == ["stop"]
        assert first_chat.attributes()[semconv.SCOPE] == "telegram:1"

        child_run = bot.subagent_manager.get(child_run_id)
        assert child_run is not None
        assert child_run.parent_run_id == parent.id

    @pytest.mark.asyncio
    async def test_payload_capture_off_by_default(self, tmp_path: Path) -> None:
        responses = [_fake_response(content="plain answer")]
        bot = _make_bot(tmp_path, responses)
        parent = bot.subagent_manager.spawn(scope="telegram:1", goal="g")
        await bot.subagent_manager.execute(parent, [{"role": "user", "content": "hi"}], bot._list_openai_tools([]))
        for span in _spans(tmp_path).list_by_run(parent.id):
            attrs = span.attributes()
            assert semconv.GEN_AI_PROMPT not in attrs
            assert semconv.GEN_AI_COMPLETION not in attrs

    @pytest.mark.asyncio
    async def test_payload_capture_on_records_redacted_payloads(self, tmp_path: Path) -> None:
        responses = [_fake_response(content="the answer")]
        config = _build_config(tmp_path, capture_payloads=True)
        bot = BotCore(config=config, channels={"telegram": cast(Any, object())})
        bot.llm.client = cast(Any, SimpleNamespace(chat=SimpleNamespace(completions=_StubCompletions(responses))))
        parent = bot.subagent_manager.spawn(scope="telegram:1", goal="g")
        secret = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        await bot.subagent_manager.execute(parent, [{"role": "user", "content": secret}], bot._list_openai_tools([]))
        chat_span = next(s for s in _spans(tmp_path).list_by_run(parent.id) if s.name == semconv.OP_CHAT)
        attrs = chat_span.attributes()
        assert secret not in attrs[semconv.GEN_AI_PROMPT]
        assert "[REDACTED]" in attrs[semconv.GEN_AI_PROMPT]
        assert attrs[semconv.GEN_AI_COMPLETION] == "the answer"


class TestErrorVisibility:
    """Issue #35 P0: LLM errors must be visible with request-size counts."""

    @pytest.mark.asyncio
    async def test_llm_error_marks_chat_and_run_spans(self, tmp_path: Path) -> None:
        responses = [RuntimeError("400: requested tokens exceed context window")]
        bot = _make_bot(tmp_path, responses)
        parent = bot.subagent_manager.spawn(scope="telegram:1", goal="g")
        result = await bot.subagent_manager.execute(
            parent, [{"role": "user", "content": "hello"}], bot._list_openai_tools([])
        )
        assert result.success is False
        assert result.error is not None

        spans = _spans(tmp_path).list_by_run(parent.id)
        chat_span = next(s for s in spans if s.name == semconv.OP_CHAT)
        assert chat_span.status == "error"
        attrs = chat_span.attributes()
        assert attrs[semconv.GEN_AI_ERROR_TYPE] == "RuntimeError"
        assert attrs[semconv.LLM_MESSAGE_COUNT] >= 1
        assert semconv.LLM_MESSAGE_CHARS in attrs
        events = chat_span.events()
        assert events
        assert "RuntimeError" in events[0]["attributes"]["exception.type"]

        run_span = next(s for s in spans if s.name == semconv.OP_INVOKE_AGENT)
        assert run_span.status == "error"
        assert "400" in (run_span.status_message or "")

    @pytest.mark.asyncio
    async def test_anomaly_flags_on_length_truncation_and_empty_reply(self, tmp_path: Path) -> None:
        responses = [_fake_response(content="", finish_reason="length", completion_tokens=0)]
        bot = _make_bot(tmp_path, responses)
        parent = bot.subagent_manager.spawn(scope="telegram:1", goal="g")
        await bot.subagent_manager.execute(parent, [{"role": "user", "content": "hi"}], bot._list_openai_tools([]))
        chat_span = next(s for s in _spans(tmp_path).list_by_run(parent.id) if s.name == semconv.OP_CHAT)
        attrs = chat_span.attributes()
        assert attrs[semconv.GEN_AI_RESPONSE_FINISH_REASONS] == ["length"]
        assert attrs[semconv.LLM_EMPTY_CONTENT] is True


class TestDelegationRefusalTraced:
    @pytest.mark.asyncio
    async def test_depth_refusal_marks_delegate_span_error(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path, [])
        orchestrator = bot.subagent_manager.spawn(scope="telegram:1", goal="root")
        child = bot.subagent_manager.spawn(scope="telegram:1", parent_run_id=orchestrator.id, goal="child")

        result = json.loads(
            await run_delegate_task(
                bot,
                {"goal": "not allowed"},
                scope="telegram:1",
                run_id=child.id,
                cancel_token=None,
            )
        )
        assert DEPTH_REFUSED_MESSAGE in result["error"]

        delegate_span = next(s for s in _spans(tmp_path).list_by_run(child.id) if s.name == semconv.SPAN_DELEGATE_TASK)
        assert delegate_span.status == "error"
        assert delegate_span.status_message == DEPTH_REFUSED_MESSAGE


class TestPlanFlowTraced:
    """Plan runs must produce the same invoke_agent-rooted tree as subagent
    runs (review finding: process_plan bypasses SubagentManager.execute)."""

    @pytest.mark.asyncio
    async def test_plan_run_produces_invoke_agent_root_with_llm_rounds(self, tmp_path: Path) -> None:
        responses = [
            _fake_response(
                content='{"goal": "research tracing", "constraints": [], "required_inputs": [], '
                '"risk_flags": [], "notes": ""}',
                prompt_tokens=150,
                completion_tokens=15,
            ),
            _fake_response(content="plan executed final result", prompt_tokens=400, completion_tokens=60),
        ]
        bot = _make_bot(tmp_path, responses)
        sent: list[tuple[str, str]] = []

        async def fake_send(scope: str, text: str) -> None:
            sent.append((scope, text))

        bot._send = fake_send  # type: ignore[method-assign]

        await process_plan(bot, "telegram:1", "/plan research tracing")

        run_id = bot.contexts.get("chat", "telegram:1", "last_plan_run_id")["run_id"]
        store = _spans(tmp_path)
        spans = store.list_by_run(run_id)
        by_name = {s.name: s for s in spans}
        assert set(by_name) == {semconv.OP_INVOKE_AGENT, semconv.OP_CHAT}
        assert len([s for s in spans if s.name == semconv.OP_CHAT]) == 2

        root = by_name[semconv.OP_INVOKE_AGENT]
        assert root.parent_span_id is None
        assert root.attributes()[semconv.RUN_DEPTH] == 0
        assert root.attributes()[semconv.RUN_GOAL] == "research tracing"
        assert root.attributes()[semconv.SCOPE] == "telegram:1"

        chats = sorted((s for s in spans if s.name == semconv.OP_CHAT), key=lambda s: s.start_ts)
        assert len({s.trace_id for s in [root, *chats]}) == 1
        assert all(c.parent_span_id == root.span_id for c in chats)
        assert all(c.attributes()[semconv.RUN_ID] == run_id for c in chats)
        assert chats[1].attributes()[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 400

        assert sent and "plan executed final result" in sent[-1][1]


class TestExporterFailureAlerting:
    @pytest.mark.asyncio
    async def test_exporter_failure_alerts_owner_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def boom(self: TraceStore, records: object) -> None:
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(TraceStore, "insert_spans", boom)

        bot = BotCore(
            config=_build_config(tmp_path, owner_chat_id=42),
            channels={"telegram": cast(Any, object())},
        )
        sent: list[tuple[str, str]] = []

        async def fake_send(scope: str, text: str) -> None:
            sent.append((scope, text))

        bot._send = fake_send  # type: ignore[method-assign]

        with caplog.at_level(logging.ERROR):
            with get_tracer().start_as_current_span("force-fail") as span:
                span.set_attribute("n", 1)
            for _ in range(20):
                await asyncio.sleep(0)

        assert any("OTel span exporter failure" in r.message for r in caplog.records)
        assert sent and sent[0][0] == "telegram:42"
        assert "Tracing failure" in sent[0][1]

        sent_before = len(sent)
        with get_tracer().start_as_current_span("force-fail-2"):
            pass
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(sent) == sent_before  # latch: no repeat alert

    @pytest.mark.asyncio
    async def test_no_owner_configured_logs_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def boom(self: TraceStore, records: object) -> None:
            raise sqlite3.OperationalError("disk full")

        monkeypatch.setattr(TraceStore, "insert_spans", boom)
        BotCore(
            config=_build_config(tmp_path, owner_chat_id=0),
            channels={"telegram": cast(Any, object())},
        )

        with caplog.at_level(logging.ERROR):
            with get_tracer().start_as_current_span("force-fail"):
                pass
            await asyncio.sleep(0)
        assert any("OTel span exporter failure" in r.message for r in caplog.records)
