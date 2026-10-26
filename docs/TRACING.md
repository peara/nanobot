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
| Decorator | `src/nanobot/tracing/decorators.py` | `@traced(...)` — owns span lifecycle for all sites |
| CLI view | `src/nanobot/debug_cli.py` | `just trace <run_id>` |

Manual instrumentation only — `opentelemetry-sdk`, no auto-instrumentation
packages. Span lifecycle (open/end, kind, name, exception recording, status,
run-id context) is centralized in the `@traced` decorator; each call site is
one decorator line plus an attribute extractor, with `get_current_span()` sets
only where the body alone knows the data (HTTP responses, mid-function
discoveries, swallowed-exception tracebacks). Span sites:

| Span | Site | Kind | Key attributes |
|---|---|---|---|
| `invoke_agent` | `SubagentManager.execute` (root for main-chat, scheduled, delegated runs) | INTERNAL | run_id, parent_run_id, scope, goal, depth |
| `invoke_agent` | `_execute_plan_traced` in `plans/runner.py` (root for `/plan` runs) | INTERNAL | run_id, scope, goal, depth |
| `chat` | `LlmClient.chat` | CLIENT | model, tokens, finish_reason, error.type |
| `execute_tool` | `ToolCallDispatcher.dispatch` | INTERNAL | tool name, run_id, ok/error status |
| `delegate_task` | `run_delegate_task` | INTERNAL | goal, child_run_id, refusal errors |

Main-chat messages, scheduled tasks, and delegated children flow through
`SubagentManager.execute`; `/plan` runs flow through the traced wrapper in
`plans/runner.py` — both emit the `invoke_agent` trace root for their run and
open the run-id contextvar. The evaluator's post-turn LLM calls (quality /
learning extraction) deliberately run *outside* any run: they emit standalone
root `chat` spans with no run_id, unreachable via `just trace` (which queries
by run) — their I/O duty stays with `nanobot.evaluator.io` logging, and the
spans are queryable via SQL on the `spans` table if ever needed.

**Anomaly alerting decision** anomalies are *persisted
and surfaced*, not push-alerted — `finish_reason=length` and empty replies
land as span attributes (plus the pre-existing `llm.py` WARNING logs) and are
summarized by `just trace` on demand. Exporter failure remains the only push
alert (once per process to the owner), since a broken exporter makes all of
the above invisible.

The decorator's shared exception policy: `LlmCallCancelledError` marks the
span `nanobot.cancelled` and re-raises (never error status); any other
exception sets `gen_ai.error.type`, status ERROR, and records the exception.
Sites with non-raising contracts (execute, dispatch) swallow their
exceptions internally and derive the span status from the returned result
via `on_result` instead. OTel's own auto-record-on-exception context-manager
default is disabled so this policy is the single owner of error semantics.

Main-chat messages, scheduled tasks, and delegated children flow through
`SubagentManager.execute`, so the `invoke_agent` span is the trace root for
those runs and carries the `subagent_runs` join key. `/plan` runs get the same
root from the traced wrapper in `plans/runner.py`. Delegated children nest
under the parent's trace for free (ambient OTel context — no explicit
propagation).

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
  payload passes through the redaction gate at attribute-setting time.
  Redaction is backed by [`detect-secrets`](https://github.com/Yelp/detect-secrets)
  (pinned `==1.5.0`): the gitleaks rule families — AWS keys, GitHub PATs,
  Slack/OpenAI tokens, JWTs, PEM private-key blocks, Basic auth,
  keyword-adjacent values (`password=`/`api_key=`), high-entropy strings.
  Best effort, **not a guarantee**: known residual gaps are low-entropy
  base64 blobs, multi-word unquoted prose values (trimmed to first token),
  and short synthetic strings below the entropy gates. The battery in
  `tests/tracing/test_redaction.py` doubles as the upgrade canary — the
  integration reaches detect-secrets plugin internals, so re-verify on any
  version bump.

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
