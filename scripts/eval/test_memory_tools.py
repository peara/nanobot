#!/usr/bin/env python3
"""Test memory tool selection against a running bot via FileChannel.

Sends each fixture scenario to the bot, waits for the response,
then validates the tool calls against expected behavior.

Usage:
    # Run all memory tool fixtures
    uv run python scripts/eval/test_memory_tools.py

    # Run specific fixtures
    uv run python scripts/eval/test_memory_tools.py save_simple_fact search_recall

    # Verbose (show tool args and bot reply)
    uv run python scripts/eval/test_memory_tools.py --verbose

    # Only validate tool names (not arg presence)
    uv run python scripts/eval/test_memory_tools.py --tool-names-only

Requires a running bot with FileChannel configured in config.yaml.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

DEFAULT_SESSIONS_DIR = Path("data/chat/sessions")


class ValidationResult(Enum):
    EXACT = "exact"
    ACCEPTABLE = "acceptable"
    WRONG_TOOL = "wrong_tool"
    MISSING_ARGS = "missing_args"


@dataclass
class FixtureResult:
    name: str
    scenario: str
    expected_tool: str
    actual_tools: list[str]
    validations: list[tuple[ValidationResult, str]]
    bot_reply: str
    tool_call_details: list[dict[str, Any]]


def validate_tool_calls(
    actual_calls: list[dict[str, Any]],
    expected_calls: list[dict[str, Any]],
    acceptable_alternatives: list[str],
    tool_names_only: bool = False,
) -> list[tuple[ValidationResult, str]]:
    """Validate actual tool calls against fixture expectations."""
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
            if tool_names_only:
                validations.append((ValidationResult.EXACT, f"Tool #{idx}: {actual_tool}"))
                continue

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


def find_active_session(sessions_dir: Path = DEFAULT_SESSIONS_DIR) -> str | None:
    """Find the most recent active FileChannel session (has session_start, no session_end)."""
    out_dir = sessions_dir / "out"
    if not out_dir.exists():
        return None

    for out_file in sorted(out_dir.glob("*.jsonl"), reverse=True):
        has_start = False
        has_end = False
        for line in out_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "session_start":
                has_start = True
            if event.get("type") == "session_end":
                has_end = True

        if has_start and not has_end:
            return out_file.stem

    return None


def send_message(
    sessions_dir: Path,
    session_id: str,
    text: str,
    user_id: str = "eval",
) -> str:
    """Append a user_message to the session's input file. Returns the timestamp used."""
    in_file = sessions_dir / "in" / f"{session_id}.jsonl"
    in_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    event = {
        "type": "user_message",
        "text": text,
        "user_id": user_id,
        "timestamp": timestamp,
    }
    with in_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return timestamp


def read_events(sessions_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """Read all events from the session's output file."""
    out_file = sessions_dir / "out" / f"{session_id}.jsonl"
    if not out_file.exists():
        return []
    events = []
    for line in out_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def wait_for_turn_complete(
    sessions_dir: Path,
    session_id: str,
    previous_event_count: int,
    timeout: float = 60,
) -> list[dict[str, Any]]:
    """Wait until a new turn_complete event appears.

    Returns all new events since previous_event_count.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        events = read_events(sessions_dir, session_id)
        for event in events[previous_event_count:]:
            if event.get("type") == "turn_complete":
                return events[previous_event_count:]
        time.sleep(0.5)

    raise TimeoutError(f"No turn_complete within {timeout}s")


def extract_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract tool_call events from a list of events."""
    calls = []
    for event in events:
        if event.get("type") == "tool_call":
            calls.append(
                {
                    "tool_name": event.get("tool", ""),
                    "arguments": event.get("args", {}),
                    "call_id": event.get("call_id", ""),
                }
            )
    return calls


def extract_bot_reply(events: list[dict[str, Any]]) -> str:
    """Extract the last assistant_message from events."""
    for event in reversed(events):
        if event.get("type") == "assistant_message":
            return event.get("text", "")
    return ""


def load_fixtures(fixtures_dir: Path, names: list[str] | None = None) -> list[dict[str, Any]]:
    """Load memory tool fixtures, optionally filtered by name."""
    fixtures = []

    search_dirs = [fixtures_dir / "memory_tools", fixtures_dir]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for p in sorted(search_dir.glob("*.json")):
            if p.parent == fixtures_dir and (fixtures_dir / "memory_tools" / p.name).exists():
                continue
            data = json.loads(p.read_text())
            if data.get("phase") != "memory_tool_selection":
                continue
            if names and p.stem not in names:
                continue
            fixtures.append(data)

    return fixtures


def print_results(results: list[FixtureResult], verbose: bool = False) -> None:
    """Print test results in a summary table."""
    width = 70
    print(f"\n{'=' * width}")
    print("MEMORY TOOL SELECTION TEST RESULTS")
    print(f"{'=' * width}")

    exact = 0
    acceptable = 0
    wrong = 0
    missing = 0

    for result in results:
        print(f"\n  {result.name}: {result.scenario[:60]}")
        print(f"    Expected: {result.expected_tool}")
        actual = ", ".join(result.actual_tools) if result.actual_tools else "(none)"
        print(f"    Got:      {actual}")

        for vtype, msg in result.validations:
            icon = {
                "exact": "✓",
                "acceptable": "≈",
                "wrong_tool": "✗",
                "missing_args": "△",
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

        if verbose:
            if result.bot_reply:
                print(f"    Reply: {result.bot_reply[:150]}")
            for tc in result.tool_call_details:
                args_preview = json.dumps(tc.get("arguments", {}), ensure_ascii=False)
                if len(args_preview) > 100:
                    args_preview = args_preview[:97] + "..."
                print(f"    Call: {tc.get('tool_name', '?')}({args_preview})")

    total = exact + acceptable + wrong + missing
    passed = exact + acceptable
    print(f"\n{'=' * width}")
    passed_str = (
        f"Results: {passed}/{total} passed ({exact} exact, {acceptable} acceptable, {wrong} wrong, {missing} missing)"
    )
    print(passed_str)
    if wrong == 0 and missing == 0:
        print("ALL PASSED ✓")
    else:
        print("SOME FAILED ✗")
    print(f"{'=' * width}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test memory tool selection via FileChannel")
    parser.add_argument("fixtures", nargs="*", help="Specific fixture names to run (default: all)")
    parser.add_argument("--timeout", type=float, default=120, help="Seconds to wait for bot response per fixture")
    parser.add_argument("--timeout", type=float, default=60, help="Seconds to wait for bot response per fixture")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show bot reply and tool args")
    parser.add_argument("--tool-names-only", action="store_true", help="Only validate tool names, not arguments")
    parser.add_argument("--list", action="store_true", help="List available fixtures and exit")
    args = parser.parse_args()

    fixtures_dir = Path(__file__).resolve().parent / "fixtures"

    if args.list:
        for f in load_fixtures(fixtures_dir):
            print(f"  {f['name']}: {f['scenario'][:60]}")
        return

    session_id = find_active_session(args.sessions_dir)
    if session_id is None:
        print("ERROR: No active FileChannel session found. Is the bot running?", file=sys.stderr)
        print("  Start the bot with: python -m nanobot.main --config config.yaml", file=sys.stderr)
        sys.exit(1)

    print(f"Using session: {session_id}", file=sys.stderr)

    fixture_names = args.fixtures if args.fixtures else None
    fixtures = load_fixtures(fixtures_dir, fixture_names)
    if not fixtures:
        print("ERROR: No memory_tool_selection fixtures found.", file=sys.stderr)
        sys.exit(1)

    print(f"Running {len(fixtures)} fixtures...\n", file=sys.stderr)

    events = read_events(args.sessions_dir, session_id)
    event_offset = len(events)

    results: list[FixtureResult] = []

    for fixture in fixtures:
        name = fixture["name"]
        scenario = fixture["scenario"]
        expected_tool_calls = fixture.get("expected_tool_calls", [])
        acceptable_alternatives = fixture.get("acceptable_alternatives", [])

        print("  Resetting state...", file=sys.stderr)
        send_message(args.sessions_dir, session_id, "/reset", user_id="eval")

        try:
            wait_for_turn_complete(args.sessions_dir, session_id, event_offset, timeout=args.timeout)
            events = read_events(args.sessions_dir, session_id)
            event_offset = len(events)
        except TimeoutError:
            print(f"  WARNING: Reset timed out for {name}, continuing anyway", file=sys.stderr)
            events = read_events(args.sessions_dir, session_id)
            event_offset = len(events)

        print(f'  Testing: {name} - "{scenario[:50]}..."', file=sys.stderr)
        send_message(args.sessions_dir, session_id, scenario, user_id="eval")

        try:
            new_events = wait_for_turn_complete(args.sessions_dir, session_id, event_offset, timeout=args.timeout)
        except TimeoutError:
            print(f"  ERROR: Timed out waiting for response to: {scenario[:50]}", file=sys.stderr)
            results.append(
                FixtureResult(
                    name=name,
                    scenario=scenario,
                    expected_tool=expected_tool_calls[0]["tool_name"] if expected_tool_calls else "?",
                    actual_tools=[],
                    validations=[(ValidationResult.MISSING_ARGS, "Timed out waiting for response")],
                    bot_reply="",
                    tool_call_details=[],
                )
            )
            events = read_events(args.sessions_dir, session_id)
            event_offset = len(events)
            continue

        tool_calls = extract_tool_calls(new_events)
        bot_reply = extract_bot_reply(new_events)

        validations = validate_tool_calls(
            tool_calls,
            expected_tool_calls,
            acceptable_alternatives,
            tool_names_only=args.tool_names_only,
        )

        actual_tools = [tc["tool_name"] for tc in tool_calls]
        expected_tool = expected_tool_calls[0]["tool_name"] if expected_tool_calls else "?"

        results.append(
            FixtureResult(
                name=name,
                scenario=scenario,
                expected_tool=expected_tool,
                actual_tools=actual_tools,
                validations=validations,
                bot_reply=bot_reply,
                tool_call_details=tool_calls,
            )
        )

        events = read_events(args.sessions_dir, session_id)
        event_offset = len(events)

        time.sleep(3)

    print_results(results, verbose=args.verbose)

    has_failures = any(
        vtype in (ValidationResult.WRONG_TOOL, ValidationResult.MISSING_ARGS)
        for result in results
        for vtype, _ in result.validations
    )
    sys.exit(1 if has_failures else 0)


if __name__ == "__main__":
    main()
