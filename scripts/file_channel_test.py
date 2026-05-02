"""Quick E2E test: send a message via FileChannel and read the response.

Usage:
    uv run python scripts/file_channel_test.py [message]
    uv run python scripts/file_channel_test.py                  # default message
    uv run python scripts/file_channel_test.py "hello bot"      # custom message

Auto-discovers the active session by finding session_start in out/ directory.
If no active session found, writes to a default session_id.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SESSIONS_DIR = "./data/chat/sessions"
DEFAULT_SESSION_ID = "sisyphus_session"
USER_ID = "sisyphus"
DEFAULT_MESSAGE = "search for good minolta 58 1.2 on yahoo auction"


def _find_active_session(out_dir: Path) -> str | None:
    for f in sorted(out_dir.glob("*.jsonl"), reverse=True):
        try:
            first_line = f.read_text(encoding="utf-8").splitlines()[0]
            evt = json.loads(first_line)
            if evt.get("type") == "session_start":
                has_end = any(
                    json.loads(line).get("type") == "session_end"
                    for line in f.read_text(encoding="utf-8").splitlines()[1:]
                    if line.strip()
                )
                if not has_end:
                    return f.stem
        except (json.JSONDecodeError, IndexError):
            continue
    return None


async def main() -> None:
    message = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_MESSAGE

    in_dir = Path(SESSIONS_DIR) / "in"
    out_dir = Path(SESSIONS_DIR) / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    session_id = _find_active_session(out_dir) or DEFAULT_SESSION_ID
    in_file = in_dir / f"{session_id}.jsonl"
    out_file = out_dir / f"{session_id}.jsonl"

    event = {
        "type": "user_message",
        "text": message,
        "user_id": USER_ID,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    with in_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    print(f"[→] Sent: {message}")
    print(f"    Session:  {session_id}")
    print(f"    In file:  {in_file}")
    print(f"    Out file: {out_file}")
    print("    Waiting for response...")

    timeout = 180
    start = asyncio.get_event_loop().time()
    out_offset = out_file.stat().st_size if out_file.exists() else 0
    accumulated: list[dict] = []

    while True:
        elapsed = asyncio.get_event_loop().time() - start
        if elapsed >= timeout:
            print(f"\n[✗] Timeout after {timeout}s")
            sys.exit(1)

        if out_file.exists():
            with out_file.open("r", encoding="utf-8") as f:
                f.seek(out_offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                        accumulated.append(evt)

                        if evt.get("type") == "tool_call":
                            args_preview = json.dumps(evt.get("args", {}), ensure_ascii=False)[:80]
                            print(f"  [🔧] tool_call: {evt.get('tool', '?')} {args_preview}")
                        elif evt.get("type") == "tool_result":
                            preview = evt.get("result_preview", "")[:100]
                            ok = "✓" if evt.get("ok") else "✗"
                            print(f"  [{ok}] tool_result: {preview}")

                        if evt.get("type") == "turn_complete":
                            for e in reversed(accumulated):
                                if e.get("type") == "assistant_message":
                                    print(f"\n[←] Response:\n{e.get('text', '')}")
                                    sys.exit(0)
                            print("\n[✗] turn_complete found but no assistant_message")
                            sys.exit(1)
                    except json.JSONDecodeError:
                        continue
                out_offset = f.tell()

        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
