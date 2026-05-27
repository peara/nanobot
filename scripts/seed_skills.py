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
    {
        "name": "reddit_explorer",
        "description": (
            "Browse and search Reddit for posts, comments, and subreddit info. "
            "Activate when the user mentions Reddit, asks about a subreddit, "
            "wants to find discussions, or wants to explore community opinions on a topic."
        ),
        "instructions": """\
Reddit browsing workflow — follow these rules when using Reddit tools:

1. Check connectivity first. Use reddit__reddit_health to verify the Reddit API is reachable, especially if this is the first Reddit request in the conversation.

2. Get subreddit info before diving in. Use reddit__reddit_get_subreddit to check that a subreddit exists and understand its topic, rules (from description), and size before fetching posts.

3. Browse then read. Use reddit__reddit_get_posts to list posts from a subreddit, then reddit__reddit_get_post to read specific posts with comments.
   - Sort options: hot (default), new, top, rising.
   - For top posts, specify time_filter: hour, day, week (default), month, year, all.

4. Search strategically. Use reddit__reddit_search to find posts across Reddit or within a subreddit.
   - Narrow by subreddit when the user specifies a community.
   - Sort by relevance (default), hot, top, new, or comments depending on what the user needs.

5. Subreddit names without /r/. Always pass subreddit names without the /r/ prefix (use "python" not "/r/python" or "r/python"). Strip the prefix if the user provides it.

6. Present data as-is. Post bodies and comments are already truncated by the server. Do not re-truncate or summarize unless the user asks. Present titles, scores, and comment counts directly.

7. Handle errors gracefully. Check the "ok" field in every response. If ok is False, report the error to the user (not_found, forbidden, rate_limited) and suggest alternatives.

8. Rate limit awareness. Unauthenticated Reddit access allows ~10-60 requests/minute. If you encounter rate limiting, wait and retry rather than spamming requests.""",
        "trigger_mode": "intelligent",
        "trigger_patterns": [
            "reddit|r/subreddit|/r/|subreddit|sub post|thread on|"
            "discussion on|community opinion|what does.*think|forum post"
        ],
        "tools_allowlist": [
            "reddit__*",
        ],
        "priority": 5,
    },
    {
        "name": "memory_lifecycle",
        "description": (
            "Memory management workflow — how to save, update, search, and delete memories correctly. "
            "Always active to prevent data loss from incorrect memory operations."
        ),
        "instructions": """\
Memory lifecycle workflow — follow these rules when using memory tools:

1. Search before saving. Before saving new information, call memory__search to check if related memories already exist. This prevents duplicates and lets you update existing memories instead of creating redundant ones.

2. Save specific data, not summaries. When saving facts, listings, prices, IDs, dates, or any concrete data, include all details in the save text. Vague interests and specific data are different memories and should coexist. Example: "Interested in Minolta lenses" (vague preference) and "Minolta 85mm f/1.7 listing v1230026332, ¥52,250, EX condition" (specific data) are both valid and should be saved as separate memories.

3. Update instead of re-save. When information changes (a listing expired, a price changed, a preference shifted), use memory__update with the memory_id to replace the old content. Search first to find the memory_id, then update it. Do NOT save a duplicate with the new information.

4. Delete outdated memories. When a memory is no longer relevant (task completed, listing sold, event passed), use memory__delete with the memory_id to remove it. Keeping stale information degrades future search results.

5. memory__list for overview. Use memory__list to see all memories for a user when you need a comprehensive view — e.g., checking what preferences exist before making recommendations.

6. Each save is a separate memory. If you have multiple distinct facts to save (e.g., 5 different auction listings), save each one as a separate memory__save call. This makes them individually searchable and updateable. Do NOT bundle unrelated facts into one large memory.

7. Never silently drop information. If memory__save returns empty results or an error, that is a failure — do not proceed as if the save succeeded. Report the issue to the user.""",
        "trigger_mode": "always",
        "trigger_patterns": [],
        "tools_allowlist": None,
        "priority": 10,
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
