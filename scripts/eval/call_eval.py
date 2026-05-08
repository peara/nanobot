#!/usr/bin/env python3
"""Test evaluator prompts against real or hand-crafted inputs.

Calls the LLM with the current prompt (or a custom one) and a fixture input,
then prints the result. Useful for iterating on prompts without restarting the bot.

Usage:
    # List available fixtures and prompts
    uv run python scripts/eval/call_eval.py --list

    # List fixtures for a specific phase
    uv run python scripts/eval/call_eval.py --phase memory_tool_selection --list

    # Run quality_assessment with a fixture
    uv run python scripts/eval/call_eval.py --fixture yahoo_search_failed_url

    # Run with a custom prompt file (iterate without touching defaults.py)
    uv run python scripts/eval/call_eval.py --fixture yahoo_search_failed_url --prompt prompts/quality_assessment.txt

    # Run with raw text input
    uv run python scripts/eval/call_eval.py --phase quality_assessment --raw "User request: ..."

    # Run against the last evaluator log entry
    uv run python scripts/eval/call_eval.py --last-log

    # Show the current default prompt
    uv run python scripts/eval/call_eval.py --show-prompt

    # Show a specific prompt file
    uv run python scripts/eval/call_eval.py --show-prompt --prompt prompts/quality_assessment.txt

    # Run learning_extraction phase
    uv run python scripts/eval/call_eval.py --phase learning_extraction --fixture ...

    # Run memory tool selection eval with a fixture
    uv run python scripts/eval/call_eval.py --phase memory_tool_selection --fixture save_simple_fact

    # Validate memory tool selection against expected results
    uv run python scripts/eval/call_eval.py --phase memory_tool_selection --fixture save_simple_fact --validate
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import Enum
from pathlib import Path

from conf import (
    FIXTURES_DIR,
    PHASE_SCHEMAS,
    PROMPTS_DIR,
    call_llm,
    extract_log_entries,
    list_fixtures,
    list_prompts,
    load_config,
    load_fixture,
    load_prompt_from_defaults,
    load_prompt_from_file,
)


class ValidationResult(Enum):
    EXACT = "exact"
    ACCEPTABLE = "acceptable"
    WRONG_TOOL = "wrong_tool"
    MISSING_ARGS = "missing_args"


def validate_tool_selection(result: dict, fixture: dict) -> list[tuple[ValidationResult, str]]:
    """Validate memory_tool_selection result against fixture expectations."""
    expected_calls = fixture.get("expected_tool_calls", [])
    acceptable_alternatives = fixture.get("acceptable_alternatives", [])
    actual_calls = result.get("tool_calls", [])

    validations: list[tuple[ValidationResult, str]] = []

    for i, expected in enumerate(expected_calls):
        idx = i + 1
        if i >= len(actual_calls):
            msg = f"Missing tool call #{idx}: expected {expected['tool_name']}"
            validations.append((ValidationResult.MISSING_ARGS, msg))
            continue

        actual = actual_calls[i]
        expected_tool = expected["tool_name"]
        actual_tool = actual.get("tool_name", "")

        if actual_tool == expected_tool:
            required_args = expected.get("required_args", {})
            all_matched = True
            for key, pattern in required_args.items():
                actual_val = str(actual.get("arguments", {}).get(key, ""))
                if pattern == "*":
                    if not actual_val:
                        msg = f"Tool #{idx}: missing required arg '{key}'"
                        validations.append((ValidationResult.MISSING_ARGS, msg))
                        all_matched = False
                        break
                elif actual_val.lower() != pattern.lower():
                    msg = f"Tool #{idx}: arg '{key}' expected '{pattern}', got '{actual_val}'"
                    validations.append((ValidationResult.MISSING_ARGS, msg))
                    all_matched = False
                    break
            if all_matched:
                validations.append((ValidationResult.EXACT, f"Tool #{idx}: {actual_tool} with correct args"))
        elif actual_tool in acceptable_alternatives:
            msg = f"Tool #{idx}: {actual_tool} (acceptable alternative for {expected_tool})"
            validations.append((ValidationResult.ACCEPTABLE, msg))
        else:
            msg = f"Tool #{idx}: expected {expected_tool}, got {actual_tool}"
            validations.append((ValidationResult.WRONG_TOOL, msg))

    return validations


def print_result(phase: str, prompt: str, user_message: str, result: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"Phase: {phase}")
    print(f"Prompt length: {len(prompt)} chars")
    print(f"Input length: {len(user_message)} chars")
    print(f"{'=' * 60}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if phase == "quality_assessment":
        score = result.get("quality_score", "?")
        learnings = result.get("has_learnings", "?")
        reason = result.get("quality_reason", "?")
        confidence = result.get("confidence", "?")
        print(f"\nSCORE: {score}/5  |  HAS_LEARNINGS: {learnings}  |  CONFIDENCE: {confidence}")
        print(f"REASON: {reason}")
    elif phase == "learning_extraction":
        learnings = result.get("learnings", [])
        print(f"\nLEARNINGS EXTRACTED: {len(learnings)}")
        for i, lg in enumerate(learnings):
            print(f"  [{i}] {lg.get('direction', '?')}: {lg.get('observation', '?')[:80]}")
    elif phase == "memory_tool_selection":
        tool_calls = result.get("tool_calls", [])
        reasoning = result.get("reasoning", "")
        print(f"\nTOOL CALLS: {len(tool_calls)}")
        for i, tc in enumerate(tool_calls):
            args_preview = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
            if len(args_preview) > 80:
                args_preview = args_preview[:77] + "..."
            print(f"  [{i}] {tc.get('tool_name', '?')}({args_preview})")
        if reasoning:
            print(f"\nREASONING: {reasoning[:200]}")


def print_validation(validations: list[tuple[ValidationResult, str]]) -> None:
    print(f"\n{'=' * 60}")
    print("VALIDATION RESULTS:")
    print(f"{'=' * 60}")
    exact = sum(1 for v, _ in validations if v == ValidationResult.EXACT)
    acceptable = sum(1 for v, _ in validations if v == ValidationResult.ACCEPTABLE)
    wrong = sum(1 for v, _ in validations if v == ValidationResult.WRONG_TOOL)
    missing = sum(1 for v, _ in validations if v == ValidationResult.MISSING_ARGS)
    for vtype, msg in validations:
        icon = {"exact": "✓", "acceptable": "≈", "wrong_tool": "✗", "missing_args": "△"}[vtype.value]
        print(f"  {icon} [{vtype.value}] {msg}")
    print(f"\nSummary: {exact} exact, {acceptable} acceptable, {wrong} wrong, {missing} missing args")
    if wrong == 0 and missing == 0:
        print("PASS ✓")
    else:
        print("FAIL ✗")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test evaluator prompts against inputs")
    parser.add_argument("--phase", default="quality_assessment", choices=list(PHASE_SCHEMAS.keys()))
    parser.add_argument("--fixture", help="Fixture name (from fixtures/)")
    parser.add_argument("--prompt", help="Custom prompt file path")
    parser.add_argument("--raw", help="Raw text to use as user message")
    parser.add_argument("--last-log", action="store_true", help="Use last evaluator log entry for this phase")
    parser.add_argument("--list", action="store_true", help="List available fixtures and prompts")
    parser.add_argument("--show-prompt", action="store_true", help="Print the resolved prompt and exit")
    parser.add_argument("--save-as", help="Save result as a fixture with this name (for regression tracking)")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate result against fixture expectations (memory_tool_selection)",
    )
    args = parser.parse_args()

    if args.list:
        print("Fixtures:")
        for name in list_fixtures(phase=args.phase):
            print(f"  {name}")
        print("\nAll fixtures:")
        for name in list_fixtures():
            print(f"  {name}")
        print("\nPrompts:")
        for name in list_prompts():
            print(f"  {name}")
        return

    if args.prompt:
        prompt_path = PROMPTS_DIR / f"{args.prompt}.txt" if "/" not in args.prompt else Path(args.prompt)
        system_prompt = load_prompt_from_file(prompt_path)
    else:
        system_prompt = load_prompt_from_defaults(args.phase)

    if args.show_prompt:
        print(system_prompt)
        return

    fixture: dict | None = None
    if args.fixture:
        fixture = load_fixture(args.fixture)
        if args.phase == "memory_tool_selection":
            user_message = fixture["scenario"]
        else:
            user_message = fixture["input"]
    elif args.raw:
        user_message = args.raw
    elif args.last_log:
        entries = extract_log_entries(phase=args.phase)
        if not entries:
            print(f"ERROR: No {args.phase} entries found in evaluator log.")
            sys.exit(1)
        user_message = entries[-1]["input"]
    else:
        print("ERROR: Specify --fixture, --raw, --last-log, --list, or --show-prompt")
        sys.exit(1)

    config = load_config()
    model_config = config.get("model", {})
    base_url = model_config.get("base_url", "http://192.168.1.7:1234/v1")
    api_key = model_config.get("api_key", "ollama")
    model = model_config.get("model", "google/gemma-4-31b")

    print(f"Model: {model}", file=sys.stderr)
    print("Calling LLM...", file=sys.stderr)

    schema = PHASE_SCHEMAS[args.phase]
    result = await call_llm(base_url, api_key, model, system_prompt, user_message, schema)

    print_result(args.phase, system_prompt, user_message, result)

    if args.validate and fixture and args.phase == "memory_tool_selection":
        validations = validate_tool_selection(result, fixture)
        print_validation(validations)

    if args.save_as:
        phase_dir = FIXTURES_DIR / "memory_tools" if args.phase == "memory_tool_selection" else FIXTURES_DIR
        phase_dir.mkdir(parents=True, exist_ok=True)
        save_path = phase_dir / f"{args.save_as}.json"
        input_key = "scenario" if args.phase == "memory_tool_selection" else "input"
        result_key = "expected_tool_calls" if args.phase == "memory_tool_selection" else "expected_result"
        result_value = result.get("tool_calls", []) if args.phase == "memory_tool_selection" else result
        fixture_data = {
            "name": args.save_as,
            "phase": args.phase,
            input_key: user_message,
            result_key: result_value,
        }
        save_path.write_text(json.dumps(fixture_data, indent=2, ensure_ascii=False))
        print(f"\nSaved as fixture: {save_path}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
