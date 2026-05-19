#!/usr/bin/env python3
# ruff: noqa: I001, E402, E501
"""Seed nanobot skills into the database.

Creates predefined skills with their instructions, trigger modes, and tool
allowlists. Skips skills that already exist (by name). For skills with
trigger_mode="intelligent", also indexes them into the vector store if available.

Usage:
    uv run python scripts/seed_skills.py [--config config.yaml] [--force]

    --config   Path to nanobot config (default: config.yaml)
    --force    Delete existing seed skills before re-creating them
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path so nanobot imports work
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from nanobot.config import load_config
from nanobot.skills import SkillStore
from nanobot.skills.models import Skill


SEED_SKILLS: list[dict] = [
    {
        "name": "web_research",
        "description": (
            "Search the web, read web pages, and create reusable browser extraction scripts. "
            "Activate when the user asks to find information online, look something up, "
            "browse a website, search for prices, check facts, or compare items from web sources."
        ),
        # ruff: noqa: E501 — instructions stored as-is in DB, line wrapping would change content
        "instructions": """\
Web research workflow — follow these rules when using web tools:

1. Search first, read second. Use web__search_web to find candidate URLs, then web__read_page to extract content from the most promising results.

2. Prefer existing scripts. Before creating a new script, use web__search_scripts to check if a reusable extractor already exists. If one matches, invoke it with web__invoke_script and appropriate params.

3. NanoScript format for web__create_script. Scripts must contain exactly one top-level async function:
   async def script(page, params):
       # extraction logic
       return {"items": [...], "metadata": {...}}

   - Python only. Never include JavaScript markers: const, let, =>, document.querySelector, Array.from.
   - Return structured data only (items/metadata). Never include answer templates, formatting rules, or language instructions.
   - Include params_schema and result_schema when inferable.
   - If web__create_script returns invalid_script_language or invalid_script, do NOT retry — switch to web__read_page or web__invoke_script.
   - When invoking web__invoke_script, params must be a JSON object, e.g. {"limit": 10}. Never pass params as a plain string.

4. Interactive browsing. Use web__snapshot_page first to discover element identifiers (buttons, links, inputs), then web__interact_page with step sequences for click, type, scroll, wait_for, and switch_tab actions.

5. Present data promptly. If a web tool already returned usable extracted data in this turn, present it directly. Do not claim the data was lost or re-run extraction.

6. Reusable artifacts boundary:
   - Web Script = executable extractor returning structured data (use web__create_script).
   - Skill = reusable workflow/policy for routing, formatting, language, and response strategy.
   - Never store formatting or answer templates inside a web script.
   - Never store executable scraping logic inside a skill when a web script is the appropriate extractor layer.
   - If the user asks to save a reusable procedure: pure extractor → script; routing/formatting → skill; both → create script first, then skill that references it.

7. Domain chrome. Use web__domain_chrome to retrieve cached navigation elements (headers, links) for a previously visited domain, avoiding re-extraction of boilerplate UI.""",
        "trigger_mode": "intelligent",
        "trigger_patterns": [
            "search|lookup|look up|find info|browse|web|internet|url|website|"
            "price|check online|online|search for|find out|research|search web|"
            "google|bing|duckduckgo|yahoo|auction|shopping|buy|compare|review"
        ],
        "tools_allowlist": [
            "web__*",
        ],
        "priority": 5,
    },
]


def seed_skills(config_path: str, force: bool = False) -> None:
    config = load_config(config_path)
    store = SkillStore(config.skill_db_path)

    vector_store = None
    if config.mem0_config_path and Path(config.mem0_config_path).exists():
        try:
            from nanobot.skills.skill_vector_store import SkillVectorStore
            from nanobot.vector_store import VectorStore

            vs = VectorStore(config.mem0_config_path)
            vector_store = SkillVectorStore(vs)
        except Exception as exc:
            print(f"  [WARN] Vector store not available, skipping indexing: {exc}")

    created = 0
    skipped = 0
    indexed = 0

    for skill_def in SEED_SKILLS:
        name = skill_def["name"]
        existing = store.get_by_name(name)

        if existing is not None:
            if force:
                print(f"  [--force] Deleting existing skill: {name}")
                store.delete_by_name(name)
            else:
                print(f"  [SKIP] Skill already exists: {name}")
                skipped += 1
                continue

        skill: Skill = store.create(
            name=name,
            description=skill_def["description"],
            instructions=skill_def["instructions"],
            trigger_mode=skill_def["trigger_mode"],
            trigger_patterns=skill_def.get("trigger_patterns"),
            tools_allowlist=skill_def.get("tools_allowlist"),
            priority=skill_def.get("priority", 0),
            is_active=True,
        )
        created += 1
        print(f"  [OK] Created: {name} (trigger={skill.trigger_mode}, priority={skill.priority})")

        if skill.trigger_mode == "intelligent" and vector_store is not None:
            try:
                vector_store.store_skill(skill)
                indexed += 1
                print(f"  [OK] Indexed: {name}")
            except Exception as exc:
                print(f"  [WARN] Failed to index {name}: {exc}")

    print(f"\nSeeded {created} skills, skipped {skipped}, indexed {indexed}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed nanobot skills into the database")
    parser.add_argument("--config", default=str(repo_root / "config.yaml"), help="Path to config.yaml")
    parser.add_argument("--force", action="store_true", help="Delete existing seed skills before re-creating")
    args = parser.parse_args()

    config_path = args.config
    if not Path(config_path).exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    print(f"Seeding skills from {config_path}...")
    seed_skills(config_path, force=args.force)


if __name__ == "__main__":
    main()
