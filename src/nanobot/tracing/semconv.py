"""GenAI semantic-convention attribute strings — the single owner.

Pinned to the attribute names Langfuse's OpenTelemetry ingestion consumes:

- Tokens: ``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens``
  (Langfuse also accepts the older ``prompt_tokens`` / ``completion_tokens``).
- System: ``gen_ai.system`` — Langfuse does not read its successor
  ``gen_ai.provider.name``.
- Content: ``gen_ai.prompt`` / ``gen_ai.completion`` as JSON strings
  (payload capture only).
- Span kind: ``invoke_agent`` INTERNAL — the in-process-agent variant.

The upstream conventions are development-status and rename attributes
between versions; every string we emit is defined here, so a re-pin is a
diff against this one file and nothing else.

Attributes not covered by the conventions (run join keys, anomaly flags)
live under the ``nanobot.*`` namespace below.
"""

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_SYSTEM_VALUE = "openai-compatible"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"

GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_ERROR_TYPE = "gen_ai.error.type"

GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_DESCRIPTION = "gen_ai.tool.description"

# Payload-capture-only attributes (tracing.capture_payloads): full request
# messages / response content. nanobot.llm.io keeps full-payload logging duty;
# these are redacted + clipped at attribute-setting time before landing here.
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"

# Operation-name values (also used as span names).
OP_CHAT = "chat"
OP_EXECUTE_TOOL = "execute_tool"
OP_INVOKE_AGENT = "invoke_agent"

# nanobot join keys and flags — join spans with subagent_runs / tool_calls /
# scratchpad states, and mark anomalies for the trace CLI to count.
SPAN_DELEGATE_TASK = "delegate_task"

RUN_ID = "nanobot.run_id"
PARENT_RUN_ID = "nanobot.parent_run_id"
CHILD_RUN_ID = "nanobot.child_run_id"
RUN_GOAL = "nanobot.run.goal"
RUN_DEPTH = "nanobot.run.depth"
SCOPE = "nanobot.scope"

LLM_MESSAGE_COUNT = "nanobot.llm.message_count"
LLM_MESSAGE_CHARS = "nanobot.llm.message_chars"
LLM_TOOL_COUNT = "nanobot.llm.tool_count"
LLM_EMPTY_CONTENT = "nanobot.llm.empty_content"
LLM_ELAPSED_SECONDS = "nanobot.llm.elapsed_seconds"
TOOL_OK = "nanobot.tool.ok"
CANCELLED = "nanobot.cancelled"
