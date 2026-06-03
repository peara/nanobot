#!/usr/bin/env python3
# ruff: noqa: I001, E402, E501
"""Sync prompts.db with DEFAULT_PROMPTS from nanobot.prompts.defaults.

The DB is the runtime source of truth for what the bot actually uses.
``defaults.py`` is the upstream template that defines the initial/baseline
content. Drift happens whenever you edit ``defaults.py`` without also
updating the live DB — the bot keeps using the stale DB content.

This script reconciles the two: for every prompt in DEFAULT_PROMPTS, it
compares the active row in the DB with the content in ``defaults.py``
and offers to save the new content as a new version when they differ.

Usage:
    uv run python scripts/prompt_sync.py [--config config.yaml] [options]

    --config PATH    Path to nanobot config (default: config.yaml)
    --dry-run        Show what would change without writing to the DB
    --prompt NAME    Sync only this prompt (default: all)
    --force          Skip confirmation prompt in non-TTY mode
    --verbose        Print full diff instead of a one-line summary
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

# Ensure project root is on sys.path so nanobot imports work
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from nanobot.config import load_config
from nanobot.prompts.defaults import DEFAULT_PROMPTS
from nanobot.prompts.store import PromptStore


def _load_db_path(config_path: str) -> str:
    config = load_config(config_path)
    return config.prompt_db_path


def _diff(name: str, current: str, new: str) -> str:
    """Return a unified diff between current (DB) and new (defaults)."""
    diff_lines = difflib.unified_diff(
        current.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"{name} (current in DB)",
        tofile=f"{name} (from defaults.py)",
        n=3,
    )
    return "".join(diff_lines)


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        reply = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return reply in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync prompts.db with DEFAULT_PROMPTS from defaults.py",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to nanobot config (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to the DB",
    )
    parser.add_argument(
        "--prompt",
        metavar="NAME",
        help="Sync only this prompt (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt (for non-TTY / scripts)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full diff instead of one-line summary per prompt",
    )
    args = parser.parse_args()

    db_path = _load_db_path(args.config)
    print(f"DB path: {db_path}")
    if args.dry_run:
        print("(dry-run: no changes will be written)")

    # Ensure the DB exists before opening it — otherwise PromptStore would
    # create one. We want a clear "no DB" message for the operator.
    if not Path(db_path).exists():
        print(f"error: prompt DB not found at {db_path}", file=sys.stderr)
        print("hint: start the bot once to create the DB, then re-run.", file=sys.stderr)
        return 1

    store = PromptStore(db_path, seed_defaults=False)

    # Collect candidates.
    candidates: list[tuple[str, str, str, str]] = []
    """(name, role, current_content, new_content)"""

    names = [args.prompt] if args.prompt else list(DEFAULT_PROMPTS)
    if args.prompt and args.prompt not in DEFAULT_PROMPTS:
        print(f"error: '{args.prompt}' is not in DEFAULT_PROMPTS", file=sys.stderr)
        print(f"known: {', '.join(sorted(DEFAULT_PROMPTS))}", file=sys.stderr)
        return 1

    for name in names:
        new_content, role, _variables = DEFAULT_PROMPTS[name]
        active = store.get_active(name)
        if active is None:
            # Not in DB yet — would be inserted on next bot start (via seed),
            # but the operator asked to sync, so report it.
            print(f"[new]   {name}: not in DB; will be inserted on next bot start")
            continue
        if active.content == new_content:
            print(f"[skip]  {name}: already in sync (v{active.version}, {len(active.content)} chars)")
            continue
        candidates.append((name, role, active.content, new_content))

    if not candidates:
        print("\nNothing to sync.")
        return 0

    print(f"\n{len(candidates)} prompt(s) out of sync:")
    for name, _role, current, new in candidates:
        print(
            f"  - {name}: DB has v{store.get_active(name).version} ({len(current)} chars), "
            f"defaults has {len(new)} chars"
        )
        if args.verbose:
            print(_diff(name, current, new))

    if args.dry_run:
        print("\n(dry-run: no changes written)")
        return 0

    if not _confirm("\nApply? This saves a new version for each prompt above.", args.force):
        print("Aborted.")
        return 130  # conventional "interrupted" code

    for name, role, _current, new_content in candidates:
        result = store.save(name, new_content, role, [])
        print(f"[saved] {name}: v{result.version} ({len(result.content)} chars)")
    print(f"\nDone. {len(candidates)} prompt(s) updated. Bot picks up on next render — no restart needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
