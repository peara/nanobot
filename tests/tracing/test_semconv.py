"""Lock the pinned GenAI semconv vintage (issue #48).

These exact strings are the contract with the Langfuse-documented attribute
vintage (see src/nanobot/tracing/semconv.py docstring). Any intentional
re-pin of the vintage must consciously update both semconv.py and these
assertions — that is the migration window the wrapper owns.
"""

from __future__ import annotations

from nanobot.tracing import semconv


def test_genai_attribute_names_pinned_to_langfuse_vintage() -> None:
    assert semconv.GEN_AI_SYSTEM == "gen_ai.system"
    assert semconv.GEN_AI_OPERATION_NAME == "gen_ai.operation.name"
    assert semconv.GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
    assert semconv.GEN_AI_RESPONSE_MODEL == "gen_ai.response.model"
    assert semconv.GEN_AI_REQUEST_MAX_TOKENS == "gen_ai.request.max_tokens"
    assert semconv.GEN_AI_REQUEST_TEMPERATURE == "gen_ai.request.temperature"
    assert semconv.GEN_AI_RESPONSE_FINISH_REASONS == "gen_ai.response.finish_reasons"
    assert semconv.GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert semconv.GEN_AI_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"
    assert semconv.GEN_AI_ERROR_TYPE == "gen_ai.error.type"
    assert semconv.GEN_AI_TOOL_NAME == "gen_ai.tool.name"
    assert semconv.GEN_AI_TOOL_DESCRIPTION == "gen_ai.tool.description"
    assert semconv.GEN_AI_AGENT_NAME == "gen_ai.agent.name"
    assert semconv.GEN_AI_PROMPT == "gen_ai.prompt"
    assert semconv.GEN_AI_COMPLETION == "gen_ai.completion"


def test_span_and_operation_names_pinned() -> None:
    assert semconv.OP_CHAT == "chat"
    assert semconv.OP_EXECUTE_TOOL == "execute_tool"
    assert semconv.OP_INVOKE_AGENT == "invoke_agent"
    assert semconv.SPAN_DELEGATE_TASK == "delegate_task"


def test_nanobot_namespace_attributes() -> None:
    assert semconv.RUN_ID == "nanobot.run_id"
    assert semconv.PARENT_RUN_ID == "nanobot.parent_run_id"
    assert semconv.CHILD_RUN_ID == "nanobot.child_run_id"
    assert semconv.RUN_GOAL == "nanobot.run.goal"
    assert semconv.RUN_DEPTH == "nanobot.run.depth"
    assert semconv.SCOPE == "nanobot.scope"
    assert semconv.CANCELLED == "nanobot.cancelled"
    assert semconv.LLM_EMPTY_CONTENT == "nanobot.llm.empty_content"
    assert semconv.TOOL_OK == "nanobot.tool.ok"


def test_genai_system_value_is_openai_compatible() -> None:
    assert semconv.GEN_AI_SYSTEM_VALUE == "openai-compatible"
