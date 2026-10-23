# Tracing (OTel spans, SQLite-persisted)

OpenTelemetry-traced runs per ROADMAP Phase D (issue #48): manual
instrumentation at four sites, spans persisted to SQLite by a custom exporter,
`gen_ai.*` attribute strings pinned in one module. The backend is a config
decision — an OTLP exporter can be added later against the same spans, no
rewrite.

## Overview

| Piece | Where | Notes |
|---|---|---|
| `TracingConfig` | `src/nanobot/config.py` | `tracing:` YAML section; absent = tracing off |
| semconv pin | `src/nanobot/tracing/semconv.py` | Every `gen_ai.*` / `nanobot.*` attribute string |
| Span store | `src/nanobot/tracing/store.py` | `spans` table in `data/traces.db` |
| Exporter | `src/nanobot/tracing/exporter.py` | `SQLiteSpanExporter` + failure alerting |
| Runtime | `src/nanobot/tracing/__init__.py` | Tracer provider, run_id contextvar, payload gate |
| CLI view | `src/nanobot/debug_cli.py` | `just trace <run_id>` |

Manual instrumentation only — `opentelemetry-sdk`, no auto-instrumentation
packages. Four span sites:

| Span | Site | Kind | Key attributes |
|---|---|---|---|
| `invoke_agent` | `SubagentManager.execute` | INTERNAL | run_id, parent_run_id, scope, goal, depth |
| `chat` | `LlmClient.chat` | CLIENT | model, tokens, finish_reason, error.type |
| `execute_tool` | `ToolCallDispatcher.dispatch` | INTERNAL | tool name, run_id, ok/error status |
| `delegate_task` | `run_delegate_task` | INTERNAL | goal, child_run_id, refusal errors |

Every message — main chat, scheduled tasks, delegated children — flows through
`SubagentManager.execute`, so the `invoke_agent` span is the trace root for all
runs and carries the `subagent_runs` join key. Delegated children nest under
the parent's trace for free (ambient OTel context — no explicit propagation).

## Configuration

Add a `tracing:` section to `config.yaml` (absent = tracing off, zero
overhead):

```yaml
tracing:
  db_path: "./data/traces.db"
  capture_payloads: false   # default off
```

- **`db_path`** — SQLite file for the `spans` table. Separate from the main
  `nanobot.db`, following the store-per-concern layout (scheduler, plans,
  skills, prompts each have their own DB).
- **`capture_payloads`** — default **off**. `nanobot.llm.io` logging keeps
  full-payload duty; spans carry the skeleton + metadata only. When on,
  `gen_ai.prompt` / `gen_ai.completion` attributes are recorded — every
  payload passes through the redaction gate at attribute-setting time
  (secret-shaped strings masked, content clipped at 4000 chars).

## run_id threading

`LlmClient.chat` has no `run_id` parameter, so spans join with
`subagent_runs`, `tool_calls`, and scratchpad states through a contextvar:
`SubagentManager.execute` opens `run_id_context(run.id)` around the
`invoke_agent` span, and `chat` spans read it when they start. Tool and
delegation spans receive `run_id` explicitly. The contextvar is token-reset
when the run finishes, so a child's id never leaks back into the parent.

## Exporter failure alerting

Telemetry must not fail silently. If persisting a span batch raises, the
exporter:

1. logs `ERROR` with a traceback,
2. invokes the failure callback BotCore wires — a **once-per-process**
   notification to `owner_chat_id` (`Tracing failure: spans may be missing
   from …`),
3. returns `SpanExportResult.FAILURE`.

Note: OTel's `SimpleSpanProcessor` ignores that return value, so step 1–2 are
the only failure surface. Anomalies (finish_reason=length, empty replies,
error spans) are recorded as span attributes and summarized by `just trace`.

## `just trace` — run replay

```bash
just trace run-abc123       # replay one run
just trace-latest           # replay the most recent run
just trace-list             # recent runs with span/error counts
```

`trace show` prints the run's full span tree (LLM rounds → tool calls →
delegated children), per-run token accounting summed from chat spans, and an
anomaly summary. A child run given to `just trace` replays its parent trace
(child spans share the parent's trace_id). LLM errors appear with
`nanobot.llm.message_count` / `message_chars` and the exception event — the
issue #35 P0 fix (400/context-overflow errors visible with request sizes).

## Schema

`spans` table in `data/traces.db`:

| Column | Type | Notes |
|---|---|---|
| `trace_id` / `span_id` | TEXT | Lowercase hex (32 / 16 chars) |
| `parent_span_id` | TEXT | NULL = trace root |
| `run_id` | TEXT | Join key for `subagent_runs` |
| `scope`, `kind`, `name` | TEXT | kind: INTERNAL / CLIENT / … |
| `start_ts` / `end_ts` / `duration_ms` | REAL | Epoch seconds / milliseconds |
| `status`, `status_message` | TEXT | unset / ok / error |
| `attributes_json`, `events_json` | TEXT | Full OTel attributes + events |

Indexes on `run_id`, `trace_id`, `start_ts`.

## Semconv pin (the migration window)

The GenAI conventions are Development-status and churning (token renames in
v1.28.0, `gen_ai.system` → `gen_ai.provider.name` in v1.37.0, the
`invoke_agent` kind split in v1.41.0, repo move in v1.42.0). We pin to the
vintage Langfuse documents (their OTel attribute-mapping table and ingestion
code): `gen_ai.usage.input_tokens`/`output_tokens`, `gen_ai.system`,
`gen_ai.response.finish_reasons` (plural array), `gen_ai.prompt`/`gen_ai.completion`
as JSON strings. `src/nanobot/tracing/semconv.py` owns every string; a future
re-pin is a diff against that one file. `tests/tracing/test_semconv.py` locks
the strings so any change is a conscious one.