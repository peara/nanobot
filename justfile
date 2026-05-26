# ──────────────────────────────────────────────
# nanobot justfile
# ──────────────────────────────────────────────
# Run `just --list` to see all recipes.
# Install just: https://github.com/casey/just#installation

# Default config path (override: `just run config.staging.yaml`)
config := "config.yaml"

# ── Setup ─────────────────────────────────────

# Install dependencies (dev group included)
install:
    uv sync --group dev

# Install deps + pre-commit hooks
setup: install
    uv run pre-commit install

# ── Run ────────────────────────────────────────

# Start the bot
run config=config:
    uv run python -m nanobot.main --config {{ config }}

# ── Quality ────────────────────────────────────

# Lint with ruff
lint:
    uv run ruff check .

# Auto-format with ruff
format:
    uv run ruff format .

# Type check with mypy
typecheck:
    uv run mypy

# Run all quality gates (lint + format + typecheck)
check: lint format typecheck

# ── Test ───────────────────────────────────────

# Run all tests (unit only, excludes integration)
test:
    uv run pytest -m "not integration"

# Run all tests including integration (requires live LM Studio/Qdrant)
test-all:
    uv run pytest

# Run tests for a specific package (e.g. just test-pkg subagents)
test-pkg pkg:
    uv run pytest tests/{{ pkg }}/ -m "not integration"

# Run a specific test file (e.g. just test-file plans/test_plan_store.py)
test-file file:
    uv run pytest tests/{{ file }}

# Run tests matching a name filter (e.g. just test-name test_plan_store)
test-name name:
    uv run pytest -k {{ name }} -m "not integration"

# ── Reset ──────────────────────────────────────

# Full state reset (scheduler + history + context + mem0)
reset:
    uv run python reset_state.py

# Preview what reset would clear (no changes)
reset-dry:
    uv run python reset_state.py --dry-run

# Reset local SQLite only (keep mem0)
reset-local:
    uv run python reset_state.py --skip-mem0

# ── Debug CLI ──────────────────────────────────

# List message scopes
scopes config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} scopes

# Show context report for a scope
ctx scope config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} ctx --scope {{ scope }}

# Reset message history for a scope
reset-scope scope config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} reset --scope {{ scope }}

# Resync intelligent skills to mem0
resync-skills config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} skills-resync

# Seed predefined skills into the database (idempotent)
seed-skills config=config:
    uv run python scripts/seed_skills.py --config {{ config }}

# Force re-seed skills (deletes existing seed skills first)
seed-skills-force config=config:
    uv run python scripts/seed_skills.py --config {{ config }} --force

# List scheduled tasks
scheduler-list config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} scheduler list

# Clear all scheduled tasks
scheduler-clear config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} scheduler clear

# Clear invalid scheduled tasks
scheduler-clear-invalid config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} scheduler clear-invalid

# List recent plan runs
plan-list config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} plan list

# Show plan run details
plan-show run_id config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} plan show --run-id {{ run_id }}

# List persistent plans
plans-list config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} plans list

# Show persistent plan details
plans-show plan_id config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} plans show {{ plan_id }}

# Browse conversation history for a scope
browse scope config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} browse --scope {{ scope }}

# Show tool call history for a scope
tools scope config=config:
    uv run python -m nanobot.debug_cli --config {{ config }} tools --scope {{ scope }}

# ── Eval ───────────────────────────────────────

# List available eval fixtures and prompts
eval-list:
    uv run python scripts/eval/call_eval.py --list

# Run eval against a fixture
eval-run fixture phase="quality_assessment":
    uv run python scripts/eval/call_eval.py --phase {{ phase }} --fixture {{ fixture }}

# Run eval against last log entry
eval-last phase="quality_assessment":
    uv run python scripts/eval/call_eval.py --phase {{ phase }} --last-log

# Validate memory_tool_selection result against fixture expectations
eval-validate fixture:
    uv run python scripts/eval/call_eval.py --phase memory_tool_selection --fixture {{ fixture }} --validate

# Show the default prompt for a phase
eval-show-prompt phase="quality_assessment":
    uv run python scripts/eval/call_eval.py --phase {{ phase }} --show-prompt

# ── E2E Tests ──────────────────────────────────

# Send a message via FileChannel (requires running bot)
chat msg="":
    uv run python scripts/file_channel_test.py {{ if msg != "" { '"' + msg + '"' } else { "" } }}

# List available memory tool test fixtures
memory-test-list:
    uv run python scripts/eval/test_memory_tools.py --list

# Run memory tool E2E tests (requires running bot)
memory-test *args:
    uv run python scripts/eval/test_memory_tools.py {{ args }}

# ── MCP Servers ────────────────────────────────

# Run timer MCP server standalone
mcp-timer:
    uv run python -m nanobot.mcp_servers.timer.server

# Run scheduler MCP server standalone
mcp-scheduler:
    uv run python -m nanobot.mcp_servers.scheduler.server

# Run web MCP server standalone
mcp-web:
    uv run python -m nanobot.mcp_servers.web.server

# ── Pre-commit ─────────────────────────────────

# Run all pre-commit hooks
pre-commit:
    uv run pre-commit run --all-files

# ── Reset + Resync (common workflow) ──────────

# Full reset then resync skills
reset-and-resync: reset resync-skills