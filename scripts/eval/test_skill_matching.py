#!/usr/bin/env python3
"""Test skill matching quality against a real Qdrant + embedding instance.

Validates that intelligent (vector) skill matching returns relevant skills
and does NOT return unrelated skills for common queries. Reports actual
cosine similarity scores so thresholds can be calibrated from real data.

Requires:
  - A running Qdrant instance (or local path in config.yaml)
  - An embedding endpoint (LM Studio, Ollama, etc.) in config.mem0.yaml

Usage:
    uv run python scripts/eval/test_skill_matching.py
    uv run python scripts/eval/test_skill_matching.py web_research_exact web_research_broad
    uv run python scripts/eval/test_skill_matching.py --seed
    uv run python scripts/eval/test_skill_matching.py --verbose
    uv run python scripts/eval/test_skill_matching.py --list
    uv run python scripts/eval/test_skill_matching.py --min-score 0.5 --min-top-ratio 0.6
    uv run python scripts/eval/test_skill_matching.py --live
    uv run python scripts/eval/test_skill_matching.py --live --query 'search for camera lens prices'
    uv run python scripts/eval/test_skill_matching.py --live --score-filter ratio
    uv run python scripts/eval/test_skill_matching.py --live --no-prompt
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "skill_matching"
CORPUS_FILE = FIXTURES_DIR / "skills_corpus.json"


def load_corpus() -> list[dict[str, Any]]:
    if not CORPUS_FILE.exists():
        print(f"ERROR: No skills corpus at {CORPUS_FILE}", file=sys.stderr)
        sys.exit(1)
    return json.loads(CORPUS_FILE.read_text())


class ValidationResult(Enum):
    EXACT = "exact"
    MISSING = "missing"
    UNEXPECTED = "unexpected"
    SCORE_TOO_LOW = "score_too_low"
    SCORE_OK = "score_ok"


@dataclass
class FixtureResult:
    name: str
    query: str
    expected_skills: list[str]
    unexpected_skills: list[str]
    matched_skills: list[tuple[str, float]]
    validations: list[tuple[ValidationResult, str]]
    raw_results: list[dict[str, Any]]


@dataclass
class ScoreReport:
    queries: list[dict[str, Any]] = field(default_factory=list)

    def add_query(self, name: str, query: str, results: list[dict[str, Any]]) -> None:
        self.queries.append({"fixture": name, "query": query, "results": results})


def seed_skills(db_path: str, force: bool = False) -> None:
    from nanobot.skills import SkillStore

    corpus = load_corpus()
    store = SkillStore(db_path)
    for skill_def in corpus:
        name = skill_def["name"]
        existing = store.get_by_name(name)
        if existing is not None:
            if force:
                store.delete_by_name(name)
            else:
                print(f"  [SKIP] Skill already exists: {name}")
                continue
        store.create(
            name=name,
            description=skill_def["description"],
            instructions=skill_def["instructions"],
            trigger_mode=skill_def["trigger_mode"],
            trigger_patterns=skill_def.get("trigger_patterns"),
            tools_allowlist=skill_def.get("tools_allowlist"),
            priority=skill_def.get("priority", 0),
            is_active=True,
        )
        print(f"  [OK] Created: {name}")


def seed_vector_store(config_path: str, skill_db_path: str) -> None:
    from nanobot.skills import SkillStore, SkillVectorStore
    from nanobot.vector_store import VectorStore

    vs = VectorStore(config_path)
    mem0_store = SkillVectorStore(vs)
    store = SkillStore(skill_db_path)
    skills = store.list_all()
    intelligent_skills = [s for s in skills if s.trigger_mode == "intelligent"]

    if not intelligent_skills:
        print("No intelligent skills to index.")
        return

    print(f"Indexing {len(intelligent_skills)} intelligent skills...")
    for skill in intelligent_skills:
        try:
            mem0_store.remove_skill(skill.name)
        except Exception:
            pass
        mem0_store.store_skill(skill)
        print(f"  [OK] Indexed: {skill.name}")


def setup_test_env(
    config_path: str,
    corpus: list[dict[str, Any]],
    tmpdir: str,
) -> tuple[str, str]:
    """Create a temp SkillStore + VectorStore seeded with the corpus.

    Returns (skill_db_path, mem0_config_path) for use in fixture mode.
    """
    import yaml

    from nanobot.skills import SkillStore, SkillVectorStore
    from nanobot.vector_store import VectorStore

    skill_db_path = str(Path(tmpdir) / "skills.db")
    store = SkillStore(skill_db_path)
    for skill_def in corpus:
        store.create(
            name=skill_def["name"],
            description=skill_def["description"],
            instructions=skill_def["instructions"],
            trigger_mode=skill_def["trigger_mode"],
            trigger_patterns=skill_def.get("trigger_patterns"),
            tools_allowlist=skill_def.get("tools_allowlist"),
            priority=skill_def.get("priority", 0),
            is_active=True,
        )
    print(f"Seeded {len(corpus)} skills into {skill_db_path}")

    base_config = yaml.safe_load(Path(config_path).read_text())
    qdrant_path = str(Path(tmpdir) / "qdrant_data")
    Path(qdrant_path).mkdir(parents=True, exist_ok=True)

    base_config["vector_store"]["config"]["path"] = qdrant_path
    base_config["vector_store"]["config"]["collection_name"] = "nanobot_skills"

    mem0_config_path = str(Path(tmpdir) / "config.mem0.yaml")
    Path(mem0_config_path).write_text(yaml.dump(base_config, default_flow_style=False), encoding="utf-8")

    vs = VectorStore(mem0_config_path)
    mem0_store = SkillVectorStore(vs)
    skills = store.list_all()
    intelligent_skills = [s for s in skills if s.trigger_mode == "intelligent"]
    for skill in intelligent_skills:
        try:
            mem0_store.remove_skill(skill.name)
        except Exception:
            pass
        mem0_store.store_skill(skill)
        print(f"  [OK] Indexed: {skill.name}")

    return skill_db_path, mem0_config_path


def load_fixtures(names: list[str] | None = None) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    if not FIXTURES_DIR.exists():
        print(f"WARNING: No fixtures directory at {FIXTURES_DIR}", file=sys.stderr)
        return fixtures
    for p in sorted(FIXTURES_DIR.glob("*.json")):
        data = json.loads(p.read_text())
        if names and p.stem not in names:
            continue
        fixtures.append(data)
    return fixtures


def run_intelligent_match(
    query: str,
    vector_store: Any,
    limit: int = 10,
    use_retrieval_prompt: bool = True,
    score_filter: Any | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    from nanobot.skills import SkillVectorStore

    mem0_store = SkillVectorStore(
        vector_store,
        use_retrieval_prompt=use_retrieval_prompt,
        score_filter=score_filter,
    )
    raw_results = mem0_store.search_skills_raw(query, limit=limit)
    filtered_names = mem0_store.search_skills(query, limit=limit)

    return raw_results, filtered_names


def run_pattern_match(query: str, skill_store: Any) -> list[str]:
    from nanobot.skills import SkillMatcher

    matcher = SkillMatcher(skill_store)
    skills = matcher.find_by_pattern(query)
    return [s.name for s in skills]


def validate_fixture(
    fixture: dict[str, Any],
    raw_results: list[dict[str, Any]],
    filtered_skill_names: list[str] | None = None,
    min_score: float = 0.0,
    min_top_ratio: float = 0.0,
) -> list[tuple[ValidationResult, str]]:
    validations: list[tuple[ValidationResult, str]] = []
    expected_skills = fixture.get("expected_skills", [])
    unexpected_skills = fixture.get("unexpected_skills", [])
    fixture_min_score = fixture.get("min_expected_score", 0.0)

    result_map: dict[str, float] = {}
    for r in raw_results:
        metadata = r.get("metadata", {})
        name = metadata.get("skill_name", "")
        score = r.get("score", 0.0)
        if name:
            result_map[name] = score

    check_names = set(filtered_skill_names) if filtered_skill_names is not None else set(result_map.keys())

    effective_min = max(min_score, fixture_min_score)
    if min_top_ratio > 0 and result_map:
        top_score = max(result_map.values())
        ratio_threshold = top_score * min_top_ratio
    else:
        top_score = max(result_map.values()) if result_map else 0.0
        ratio_threshold = 0.0

    for skill_name in expected_skills:
        if skill_name not in result_map:
            validations.append((ValidationResult.MISSING, f"Expected skill '{skill_name}' not found in results"))
        else:
            score = result_map[skill_name]
            if score < effective_min:
                validations.append(
                    (
                        ValidationResult.SCORE_TOO_LOW,
                        f"Expected '{skill_name}' score {score:.4f} < min {effective_min:.4f}",
                    )
                )
            elif min_top_ratio > 0 and score < ratio_threshold:
                validations.append(
                    (
                        ValidationResult.SCORE_TOO_LOW,
                        f"Expected '{skill_name}' score {score:.4f} < ratio threshold {ratio_threshold:.4f} "
                        f"(top={top_score:.4f} * {min_top_ratio})",
                    )
                )
            else:
                validations.append((ValidationResult.EXACT, f"Expected '{skill_name}' matched with score {score:.4f}"))

    for skill_name in unexpected_skills:
        if skill_name in check_names:
            score = result_map[skill_name]
            validations.append(
                (ValidationResult.UNEXPECTED, f"Unexpected skill '{skill_name}' matched with score {score:.4f}")
            )
        else:
            validations.append((ValidationResult.EXACT, f"Unexpected skill '{skill_name}' correctly not matched"))

    return validations


def print_results(results: list[FixtureResult], verbose: bool = False) -> None:
    width = 80
    print(f"\n{'=' * width}")
    print("SKILL MATCHING TEST RESULTS")
    print(f"{'=' * width}")

    exact = missing = unexpected = score_low = 0

    for result in results:
        print(f'\n  {result.name}: "{result.query[:60]}"')
        print(f"    Expected: {result.expected_skills}")
        print(f"    Unexpected: {result.unexpected_skills}")
        if result.matched_skills:
            names = [f"{n} ({s:.4f})" for n, s in result.matched_skills]
            print(f"    Matched: {', '.join(names)}")
        else:
            print("    Matched: (none)")

        for vtype, msg in result.validations:
            icon = {
                "exact": "\u2713",
                "missing": "\u2717",
                "unexpected": "!",
                "score_too_low": "\u25b3",
                "score_ok": "\u2713",
            }[vtype.value]
            print(f"    {icon} [{vtype.value}] {msg}")

            if vtype in (ValidationResult.EXACT, ValidationResult.SCORE_OK):
                exact += 1
            elif vtype == ValidationResult.MISSING:
                missing += 1
            elif vtype == ValidationResult.UNEXPECTED:
                unexpected += 1
            elif vtype == ValidationResult.SCORE_TOO_LOW:
                score_low += 1

        if verbose and result.raw_results:
            print(f"    --- Raw results ({len(result.raw_results)}) ---")
            for r in sorted(result.raw_results, key=lambda x: x.get("score", 0.0), reverse=True):
                metadata = r.get("metadata", {})
                name = metadata.get("skill_name", "?")
                score = r.get("score", 0.0)
                print(f"      {name}: {score:.6f}")

    total = exact + missing + unexpected + score_low
    passed = exact
    print(f"\n{'=' * width}")
    print(
        f"Results: {passed}/{total} passed "
        f"({exact} exact, {missing} missing, {unexpected} unexpected, {score_low} score_too_low)"
    )
    if unexpected > 0:
        print("FAILED: Unexpected skills matched — threshold needs tuning")
    elif missing > 0:
        print("FAILED: Expected skills not matched")
    else:
        print("ALL PASSED \u2713")
    print(f"{'=' * width}")


def print_score_report(report: ScoreReport) -> None:
    width = 80
    print(f"\n{'=' * width}")
    print("SCORE DISTRIBUTION REPORT")
    print(f"{'=' * width}")

    for entry in report.queries:
        print(f'\n  [{entry["fixture"]}] "{entry["query"][:70]}"')
        results = entry["results"]
        if not results:
            print("    (no results)")
            continue
        scores = [r.get("score", 0.0) for r in results]
        top = max(scores) if scores else 0
        bottom = min(scores) if scores else 0
        spread = top - bottom if scores else 0

        print(f"    Top:    {top:.6f}")
        print(f"    Bottom: {bottom:.6f}")
        print(f"    Spread: {spread:.6f}")
        print(f"    Count:  {len(results)}")

        for r in sorted(results, key=lambda x: x.get("score", 0.0), reverse=True):
            metadata = r.get("metadata", {})
            name = metadata.get("skill_name", "?")
            score = r.get("score", 0.0)
            ratio = score / top if top > 0 else 0
            print(f"      {name:30s}  score={score:.6f}  ratio={ratio:.3f}")

    all_scores: list[float] = []
    for entry in report.queries:
        for r in entry["results"]:
            all_scores.append(r.get("score", 0.0))

    if all_scores:
        print(f"\n{'=' * width}")
        print("AGGREGATE STATISTICS")
        print(f"  Total scores:  {len(all_scores)}")
        print(f"  Global max:    {max(all_scores):.6f}")
        print(f"  Global min:    {min(all_scores):.6f}")
        print(f"  Global mean:    {sum(all_scores) / len(all_scores):.6f}")
        print(f"{'=' * width}")


def run_live(config: dict[str, Any], args: argparse.Namespace, score_filter: Any) -> None:
    """Pull skills from real DB and last user message from context store, then run match."""
    import sqlite3

    from nanobot.skills import SkillStore
    from nanobot.vector_store import VectorStore

    db_path = args.db_path or config.skill_db_path
    mem0_config_path = config.mem0_config_path

    if not mem0_config_path:
        print("ERROR: No mem0_config_path in config.", file=sys.stderr)
        sys.exit(1)

    mem0_path = Path(mem0_config_path)
    if not mem0_path.exists():
        print(f"ERROR: mem0 config not found: {mem0_path}", file=sys.stderr)
        sys.exit(1)

    skill_store = SkillStore(db_path)
    vector_store = VectorStore(str(mem0_path))

    all_skills = skill_store.list_all()
    if not all_skills:
        print("No skills in database.", file=sys.stderr)
        sys.exit(1)

    print(f"\nSkills in database ({len(all_skills)}):")
    print("-" * 80)
    for s in all_skills:
        desc_preview = s.description[:60] + "..." if len(s.description) > 60 else s.description
        print(f"  {s.name:30s}  trigger={s.trigger_mode:12s}  priority={s.priority}  active={s.is_active}")
        print(f"  {'':30s}  desc={desc_preview}")
    print()

    intelligent_skills = [s for s in all_skills if s.trigger_mode == "intelligent"]
    print(f"Intelligent skills (indexed in vector store): {len(intelligent_skills)}")
    for s in intelligent_skills:
        print(f"  {s.name}: {s.description[:70]}")
    print()

    last_msg = None
    context_db = Path(config.database_path)
    if context_db.exists():
        try:
            from nanobot.context_store import ContextStore

            ctx = ContextStore(str(context_db))
            msg = ctx.get("chat", "*", "last_user_message")
            if msg and isinstance(msg, dict):
                last_msg = msg.get("text", "")
        except Exception:
            with sqlite3.connect(str(context_db)) as conn:
                row = conn.execute(
                    "SELECT value_json FROM contexts WHERE key = 'last_user_message' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row:
                    import json

                    data = json.loads(row[0])
                    last_msg = data.get("text", "")

    queries: list[str] = []
    if last_msg:
        print(f'Last user message:\n  "{last_msg[:100]}{"..." if len(last_msg) > 100 else ""}"')
        queries.append(last_msg)
    else:
        print("No last user message found in context store.")

    if args.query:
        queries.append(args.query)

    if not queries:
        print("\nNo queries to test. Use --live --query 'your message' to test a specific message.", file=sys.stderr)
        sys.exit(0)

    print(f"\nRunning {len(queries)} live queries...\n")
    print("=" * 80)

    report = ScoreReport()

    for i, query in enumerate(queries):
        label = f"live_query_{i + 1}"
        if len(queries) == 1 and last_msg and query == last_msg:
            label = "last_user_message"

        print(f'\n  Query: "{query[:80]}{"..." if len(query) > 80 else ""}"')

        pattern_matches = run_pattern_match(query, skill_store)
        if pattern_matches:
            print(f"  Pattern matches: {pattern_matches}")

        raw_results, filtered_skills = run_intelligent_match(
            query, vector_store, limit=args.limit, use_retrieval_prompt=not args.no_prompt, score_filter=score_filter
        )

        if not raw_results:
            print("  No vector results returned.")
        else:
            top_score = max(r.get("score", 0.0) for r in raw_results)
            print(f"  Vector results ({len(raw_results)}):")
            for r in sorted(raw_results, key=lambda x: x.get("score", 0.0), reverse=True):
                metadata = r.get("metadata", {})
                name = metadata.get("skill_name", "?")
                score = r.get("score", 0.0)
                ratio = score / top_score if top_score > 0 else 0
                marker = " *" if name in filtered_skills else ""
                print(f"    {name:30s}  score={score:.6f}  ratio={ratio:.3f}{marker}")
            if filtered_skills:
                print(f"\n  Filtered matches ({len(filtered_skills)}): {filtered_skills}")

        report.add_query(label, query, raw_results)
        print()

    if len(queries) > 1:
        print_score_report(report)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test skill matching against real embeddings")
    parser.add_argument("fixtures", nargs="*", help="Specific fixture names to run (default: all)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.yaml")
    parser.add_argument("--list", action="store_true", help="List available fixtures and exit")
    parser.add_argument("--seed", action="store_true", help="Re-seed skills (clear + recreate + reindex)")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use real DB skills and last user message instead of fixtures",
    )
    parser.add_argument("--query", type=str, default=None, help="Additional query to test with --live mode")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full score details")
    parser.add_argument("--limit", type=int, default=10, help="Max vector search results per query (default: 10)")
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum absolute cosine similarity to count as match",
    )
    parser.add_argument(
        "--min-top-ratio", type=float, default=0.0, help="Minimum ratio to top score to count as match (e.g., 0.6)"
    )
    parser.add_argument("--db-path", default=None, help="Override skill DB path (default: from config)")
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Disable mxbai-embed-large retrieval prompt prefix on queries",
    )
    parser.add_argument(
        "--score-filter",
        choices=["threshold", "cutoff", "ratio"],
        default="threshold",
        help="Score filter strategy: threshold (no filter), cutoff (absolute min), ratio (min_top_ratio + min_score)",
    )
    args = parser.parse_args()

    from nanobot.config import load_config
    from nanobot.skills.score_filter import CutoffFilter, RatioFilter, ThresholdFilter

    if args.score_filter == "cutoff":
        score_filter = CutoffFilter(min_score=args.min_score)
    elif args.score_filter == "ratio":
        score_filter = RatioFilter(min_top_ratio=args.min_top_ratio or 0.7, min_score=args.min_score or 0.45)
    else:
        score_filter = ThresholdFilter()

    config = load_config(args.config)

    if args.seed:
        db_path = args.db_path or config.skill_db_path
        mem0_config_path = config.mem0_config_path
        if not mem0_config_path:
            print("ERROR: No mem0_config_path in config.", file=sys.stderr)
            sys.exit(1)
        mem0_path = Path(mem0_config_path)
        if not mem0_path.exists():
            print(f"ERROR: mem0 config not found: {mem0_path}", file=sys.stderr)
            sys.exit(1)
        print("Seeding skills...")
        seed_skills(db_path, force=True)
        print("Indexing into Qdrant...")
        seed_vector_store(str(mem0_path), db_path)

    if args.live:
        run_live(config, args, score_filter)
        return

    if args.list:
        print("Available fixtures:")
        for f in load_fixtures():
            if isinstance(f, list) or "skills" in f:
                continue
            print(f"  {f['name']}: {f.get('description', '')[:60]}")
        return

    corpus = load_corpus()
    fixture_names = args.fixtures if args.fixtures else None
    fixtures = [f for f in load_fixtures(fixture_names) if "query" in f]

    if not fixtures:
        if FIXTURES_DIR.exists():
            print(f"No fixtures found in {FIXTURES_DIR}", file=sys.stderr)
        else:
            print(f"No fixtures directory: {FIXTURES_DIR}", file=sys.stderr)
            print("Create it with: mkdir -p scripts/eval/fixtures/skill_matching", file=sys.stderr)
        sys.exit(1)

    import tempfile

    from nanobot.skills import SkillStore
    from nanobot.vector_store import VectorStore

    mem0_config_path = config.mem0_config_path
    if not mem0_config_path:
        print("ERROR: No mem0_config_path in config. Required for vector search.", file=sys.stderr)
        sys.exit(1)
    mem0_path = Path(mem0_config_path)
    if not mem0_path.exists():
        print(f"ERROR: mem0 config not found: {mem0_path}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Setting up test environment in {tmpdir}...")
        skill_db_path, tmp_mem0_config = setup_test_env(str(mem0_path), corpus, tmpdir)

        skill_store = SkillStore(skill_db_path)
        vector_store = VectorStore(tmp_mem0_config)

        print(f"Running {len(fixtures)} fixtures with {len(corpus)} corpus skills...\n", file=sys.stderr)

        results: list[FixtureResult] = []
        report = ScoreReport()

        for fixture in fixtures:
            name = fixture["name"]
            query = fixture["query"]
            expected_skills = fixture.get("expected_skills", [])
            unexpected_skills = fixture.get("unexpected_skills", [])

            print(f'  Testing: {name} - "{query[:50]}..."', file=sys.stderr)

            raw_results, filtered_skills = run_intelligent_match(
                query,
                vector_store,
                limit=args.limit,
                use_retrieval_prompt=not args.no_prompt,
                score_filter=score_filter,
            )

            pattern_matches = run_pattern_match(query, skill_store)
            if pattern_matches and args.verbose:
                print(f"    Pattern matches: {pattern_matches}", file=sys.stderr)

            matched_skills: list[tuple[str, float]] = []
            for skill_name in filtered_skills:
                score = next(
                    (r.get("score", 0.0) for r in raw_results if r.get("metadata", {}).get("skill_name") == skill_name),
                    0.0,
                )
                matched_skills.append((skill_name, score))

            validations = validate_fixture(
                fixture,
                raw_results,
                filtered_skill_names=filtered_skills,
                min_score=args.min_score,
                min_top_ratio=args.min_top_ratio,
            )

            results.append(
                FixtureResult(
                    name=name,
                    query=query,
                    expected_skills=expected_skills,
                    unexpected_skills=unexpected_skills,
                    matched_skills=matched_skills,
                    validations=validations,
                    raw_results=raw_results,
                )
            )
            report.add_query(name, query, raw_results)

        print_results(results, verbose=args.verbose)
        print_score_report(report)

        has_unexpected = any(
            vtype == ValidationResult.UNEXPECTED for result in results for vtype, _ in result.validations
        )
        sys.exit(1 if has_unexpected else 0)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
