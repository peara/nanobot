#!/usr/bin/env python3
"""Test scheduled task prompt against a real LLM.

Validates that the subagent_scheduled prompt causes the LLM to:
1. Call memory__search with the correct user_id (not a placeholder)
2. Call memory__save with the correct user_id when saving results
3. Respond NO_ACTION_NEEDED when nothing changed
4. Follow the full search → act → save round-trip

Usage:
    # Run all scheduled task fixtures
    uv run python scripts/eval/test_scheduled_tasks.py

    # Run specific fixtures
    uv run python scripts/eval/test_scheduled_tasks.py search_before_acting

    # Verbose (show tool args and LLM reasoning)
    uv run python scripts/eval/test_scheduled_tasks.py --verbose

    # Only validate tool names (not arg presence)
    uv run python scripts/eval/test_scheduled_tasks.py --tool-names-only

    # Use an editable prompt file for iteration (doesn't touch defaults.py)
    uv run python scripts/eval/test_scheduled_tasks.py --prompt prompts/subagent_scheduled.txt

    # List available fixtures
    uv run python scripts/eval/test_scheduled_tasks.py --list

Requires a running LLM endpoint (LM Studio, Ollama, etc.) at the configured URL.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from conf import FIXTURES_DIR, MEMORY_TOOL_SELECTION_SCHEMA, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULTS_PATH = PROJECT_ROOT / "src" / "nanobot" / "prompts" / "defaults.py"
EVAL_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
FIXTURES_SUBDIR = FIXTURES_DIR / "scheduled_tasks"

TOOL_CATALOG = """\

## Available Tools

### memory__search
Semantic search over long-term memories. Returns the most relevant memories for a query.
- query (string, required): What to search for
- user_id (string, required): User ID (e.g., 'telegram:123')

### memory__save
Save a fact or observation to long-term memory. mem0 deduplicates and extracts key info automatically.
- text (string, required): Text to save as memory
- user_id (string, required): User ID (e.g., 'telegram:123')

### memory__list
List all memories in a namespace. No semantic search — returns everything matching the given filters.
- user_id (string, required): User ID (e.g., 'telegram:123')

### memory__search, memory__save, and memory__list all require user_id.
"""

KNOWN_PLACEHOLDERS = frozenset({"user_1234", "user_123", "example_user", "test_user", "default_user", "placeholder"})


async def call_llm(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
    response_format: dict[str, Any],
    timeout: float = 300,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": 30000,
        "response_format": response_format,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def load_prompt(prompt_file: Path | None = None) -> str:
    """Load subagent_scheduled prompt. Priority: --prompt file > eval prompts/ > defaults.py."""
    if prompt_file:
        return prompt_file.read_text()

    eval_prompt = EVAL_PROMPTS_DIR / "subagent_scheduled.txt"
    if eval_prompt.exists():
        return eval_prompt.read_text()

    content = DEFAULTS_PATH.read_text()
    match = re.search(r'SUBAGENT_SCHEDULED\s*=\s*"""(.*?)"""', content, re.DOTALL)
    if not match:
        raise ValueError(f"Could not find SUBAGENT_SCHEDULED in {DEFAULTS_PATH}")
    return match.group(1)


def build_system_prompt(base_prompt: str, user_id: str) -> str:
    formatted = base_prompt.replace("{user_id}", user_id) if "{user_id}" in base_prompt else base_prompt
    return formatted + TOOL_CATALOG


class ValidationResult(Enum):
    EXACT = "exact"
    ACCEPTABLE = "acceptable"
    WRONG_TOOL = "wrong_tool"
    MISSING_ARGS = "missing_args"
    USER_ID_CORRECT = "user_id_correct"
    USER_ID_WRONG = "user_id_wrong"


@dataclass
class FixtureResult:
    name: str
    scenario: str
    user_id: str
    expected_tool_calls: list[dict[str, Any]]
    actual_calls: list[dict[str, Any]]
    validations: list[tuple[ValidationResult, str]]
    reasoning: str


def validate_calls(
    actual_calls: list[dict[str, Any]],
    expected_calls: list[dict[str, Any]],
    acceptable_alternatives: list[str],
    fixture_user_id: str,
    tool_names_only: bool = False,
) -> list[tuple[ValidationResult, str]]:
    validations: list[tuple[ValidationResult, str]] = []

    for i, expected in enumerate(expected_calls):
        idx = i + 1
        if i >= len(actual_calls):
            validations.append(
                (ValidationResult.MISSING_ARGS, f"Missing tool #{idx}: expected {expected['tool_name']}")
            )
            continue

        actual = actual_calls[i]
        actual_tool = actual.get("tool_name", "")
        expected_tool = expected["tool_name"]

        if actual_tool == expected_tool:
            if tool_names_only:
                validations.append((ValidationResult.EXACT, f"Tool #{idx}: {actual_tool}"))
                continue

            required_args = expected.get("required_args", {})
            all_matched = True
            for key, pattern in required_args.items():
                actual_val = str(actual.get("arguments", {}).get(key, ""))
                if pattern == "*" and not actual_val:
                    validations.append((ValidationResult.MISSING_ARGS, f"Tool #{idx}: missing arg '{key}'"))
                    all_matched = False
                    break
                if pattern != "*" and actual_val.lower() != pattern.lower():
                    validations.append(
                        (
                            ValidationResult.MISSING_ARGS,
                            f"Tool #{idx}: arg '{key}' expected '{pattern}', got '{actual_val}'",
                        )
                    )
                    all_matched = False
                    break
            if all_matched:
                validations.append((ValidationResult.EXACT, f"Tool #{idx}: {actual_tool} with correct args"))
        elif actual_tool in acceptable_alternatives:
            validations.append(
                (ValidationResult.ACCEPTABLE, f"Tool #{idx}: {actual_tool} (acceptable for {expected_tool})")
            )
        else:
            validations.append(
                (ValidationResult.WRONG_TOOL, f"Tool #{idx}: expected {expected_tool}, got {actual_tool}")
            )

    for i, actual in enumerate(actual_calls):
        actual_uid = str(actual.get("arguments", {}).get("user_id", ""))
        if not actual_uid:
            continue
        idx = i + 1
        if actual_uid in KNOWN_PLACEHOLDERS:
            validations.append(
                (
                    ValidationResult.USER_ID_WRONG,
                    f"Tool #{idx}: user_id placeholder '{actual_uid}', expected '{fixture_user_id}'",
                )
            )
        elif actual_uid == fixture_user_id:
            validations.append((ValidationResult.USER_ID_CORRECT, f"Tool #{idx}: user_id '{actual_uid}'"))
        elif actual_uid != fixture_user_id:
            validations.append(
                (ValidationResult.USER_ID_WRONG, f"Tool #{idx}: user_id '{actual_uid}' != expected '{fixture_user_id}'")
            )

    return validations


def load_fixtures(names: list[str] | None = None) -> list[dict[str, Any]]:
    fixtures = []
    search_dirs = [FIXTURES_SUBDIR, FIXTURES_DIR]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for p in sorted(search_dir.glob("*.json")):
            if p.parent == FIXTURES_DIR and (FIXTURES_SUBDIR / p.name).exists():
                continue
            data = json.loads(p.read_text())
            if data.get("phase") != "scheduled_task":
                continue
            if names and p.stem not in names:
                continue
            fixtures.append(data)
    return fixtures


def print_results(results: list[FixtureResult], verbose: bool = False) -> None:
    width = 70
    print(f"\n{'=' * width}")
    print("SCHEDULED TASK PROMPT TEST RESULTS")
    print(f"{'=' * width}")

    exact = acceptable = wrong = missing = uid_correct = uid_wrong = 0

    for result in results:
        print(f"\n  {result.name}: {result.scenario[:60]}")
        print(f"  User ID: {result.user_id}")
        actual = ", ".join(c.get("tool_name", "?") for c in result.actual_calls) or "(none)"
        print(f"  Tools: {actual}")

        for vtype, msg in result.validations:
            icon = {
                "exact": "✓",
                "acceptable": "≈",
                "wrong_tool": "✗",
                "missing_args": "△",
                "user_id_correct": "✓",
                "user_id_wrong": "✗",
            }[vtype.value]
            print(f"    {icon} [{vtype.value}] {msg}")

        for vtype, _ in result.validations:
            if vtype == ValidationResult.EXACT:
                exact += 1
            elif vtype == ValidationResult.ACCEPTABLE:
                acceptable += 1
            elif vtype == ValidationResult.WRONG_TOOL:
                wrong += 1
            elif vtype == ValidationResult.MISSING_ARGS:
                missing += 1
            elif vtype == ValidationResult.USER_ID_CORRECT:
                uid_correct += 1
            elif vtype == ValidationResult.USER_ID_WRONG:
                uid_wrong += 1

        if verbose:
            if result.reasoning:
                print(f"    Reasoning: {result.reasoning[:200]}")
            for tc in result.actual_calls:
                args_preview = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
                if len(args_preview) > 100:
                    args_preview = args_preview[:97] + "..."
                print(f"    Call: {tc.get('tool_name', '?')}({args_preview})")

    total_checks = exact + acceptable + wrong + missing
    passed = exact + acceptable
    print(f"\n{'=' * width}")
    print(
        f"Tool selection: {passed}/{total_checks} passed "
        f"({exact} exact, {acceptable} acceptable, {wrong} wrong, {missing} missing)"
    )
    print(f"User ID check:  {uid_correct} correct, {uid_wrong} wrong (placeholder/incorrect)")
    if wrong == 0 and missing == 0 and uid_wrong == 0:
        print("ALL PASSED ✓")
    else:
        print("SOME FAILED ✗")
    print(f"{'=' * width}")


async def run_fixture(
    fixture: dict[str, Any],
    system_prompt: str,
    config: dict[str, Any],
    tool_names_only: bool = False,
    timeout: float = 300,
) -> FixtureResult:
    model_config = config.get("model", {})
    base_url = model_config.get("base_url", "http://localhost:1234/v1")
    api_key = model_config.get("api_key", "ollama")
    model = model_config.get("model", "google/gemma-4-31b")

    scenario = fixture["scenario"]
    user_id = fixture.get("user_id", "telegram:123")

    result = await call_llm(
        base_url, api_key, model, system_prompt, scenario, MEMORY_TOOL_SELECTION_SCHEMA, timeout=timeout
    )

    actual_calls = result.get("tool_calls", [])
    expected_calls = fixture.get("expected_tool_calls", [])
    acceptable_alternatives = fixture.get("acceptable_alternatives", [])

    validations = validate_calls(actual_calls, expected_calls, acceptable_alternatives, user_id, tool_names_only)

    return FixtureResult(
        name=fixture["name"],
        scenario=scenario,
        user_id=user_id,
        expected_tool_calls=expected_calls,
        actual_calls=actual_calls,
        validations=validations,
        reasoning=result.get("reasoning", ""),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test scheduled task prompts against real LLM")
    parser.add_argument("fixtures", nargs="*", help="Specific fixture names (default: all)")
    parser.add_argument("--timeout", type=float, default=300, help="Seconds to wait for LLM response")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show tool args and LLM reasoning")
    parser.add_argument("--tool-names-only", action="store_true", help="Only validate tool names, not arguments")
    parser.add_argument("--prompt", type=Path, help="Use an editable prompt file instead of defaults.py")
    parser.add_argument("--list", action="store_true", help="List available fixtures and exit")
    args = parser.parse_args()

    if args.list:
        for f in load_fixtures():
            print(f"  {f['name']}: {f.get('description', '')[:60]}")
        return

    fixture_names = args.fixtures if args.fixtures else None
    fixtures = load_fixtures(fixture_names)
    if not fixtures:
        print("ERROR: No scheduled_task fixtures found.", file=sys.stderr)
        sys.exit(1)

    base_prompt = load_prompt(args.prompt)
    config = load_config()

    all_results: list[FixtureResult] = []
    for fixture in fixtures:
        user_id = fixture.get("user_id", "telegram:123")
        system_prompt = build_system_prompt(base_prompt, user_id)

        print(f"  Running: {fixture['name']}...", file=sys.stderr)
        try:
            result = await run_fixture(fixture, system_prompt, config, args.tool_names_only, timeout=args.timeout)
            all_results.append(result)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            all_results.append(
                FixtureResult(
                    name=fixture["name"],
                    scenario=fixture["scenario"],
                    user_id=user_id,
                    expected_tool_calls=fixture.get("expected_tool_calls", []),
                    actual_calls=[],
                    validations=[(ValidationResult.MISSING_ARGS, f"LLM call failed: {e}")],
                    reasoning="",
                )
            )

    print_results(all_results, verbose=args.verbose)

    has_failures = any(
        vtype in (ValidationResult.WRONG_TOOL, ValidationResult.MISSING_ARGS, ValidationResult.USER_ID_WRONG)
        for result in all_results
        for vtype, _ in result.validations
    )
    sys.exit(1 if has_failures else 0)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
