#!/usr/bin/env python3
"""Test evaluator prompts against real or hand-crafted inputs.

Calls the LLM with the current prompt (or a custom one) and a fixture input,
then prints the result. Useful for iterating on prompts without restarting the bot.

Usage:
    # List available fixtures and prompts
    uv run python scripts/eval/call_eval.py --list

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
"""

from __future__ import annotations

import argparse
import json
import sys

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
    args = parser.parse_args()

    if args.list:
        print("Fixtures:")
        for name in list_fixtures():
            print(f"  {name}")
        print("\nPrompts:")
        for name in list_prompts():
            print(f"  {name}")
        return

    if args.prompt:
        prompt_path = PROMPTS_DIR / f"{args.prompt}.txt" if "/" not in args.prompt else args.prompt
        system_prompt = load_prompt_from_file(prompt_path)
    else:
        system_prompt = load_prompt_from_defaults(args.phase)

    if args.show_prompt:
        print(system_prompt)
        return

    if args.fixture:
        fixture = load_fixture(args.fixture)
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

    if args.save_as:
        save_path = FIXTURES_DIR / f"{args.save_as}.json"
        fixture_data = {
            "name": args.save_as,
            "phase": args.phase,
            "input": user_message,
            "expected_result": result,
        }
        save_path.write_text(json.dumps(fixture_data, indent=2, ensure_ascii=False))
        print(f"\nSaved as fixture: {save_path}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
