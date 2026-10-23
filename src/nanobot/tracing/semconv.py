"""GenAI semantic-convention strings for nanobot's manual OTel instrumentation.

Pinned vintage: the OTel GenAI conventions as documented by Langfuse
(langfuse.com/docs/opentelemetry/get-started attribute-mapping table and the
langfuse ingestion code, OtelIngestionProcessor.ts):

- Tokens: ``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens`` —
  the names after the v1.28.0 rename (Langfuse still reads the pre-1.28
  ``prompt_tokens``/``completion_tokens`` as backward compat; the current
  names are canonical).
- System: ``gen_ai.system`` — kept on the pre-1.37 name because Langfuse's
  ingestion reads ``gen_ai.system`` and does NOT read its v1.37.0
  replacement ``gen_ai.provider.name``.
- Span kinds: pre-v1.41.0 ``invoke_agent`` semantics (INTERNAL) — which also
  matches the v1.41 in-process-agent variant: no server.address/port, no
  token usage on the agent span.
- Content: ``gen_ai.prompt`` / ``gen_ai.completion`` JSON-string attributes
  (payload capture only) — consumed by Langfuse as input/output.

The upstream conventions are Development-status and churning (token renames
in v1.28.0, gen_ai.system rename in v1.37.0, the invoke_agent span-kind
split in v1.41.0, the GenAI repo move in v1.42.0 / June 2026). This module
owns every attribute string we emit, so a future re-pin is a diff against
this one file — never a sweep through instrumentation sites. When a platform
(Langfuse et al.) enters and brings an OTLP exporter, migrate the strings
here; the spans, exporters, and CLI do not change.

Everything not covered by the GenAI conventions (run join keys, anomaly
flags) lives under the ``nanobot.*`` namespace below.
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
