"""Tests for skills module."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from nanobot.skills import VALID_TRIGGER_MODES, Skill, SkillMatcher, SkillStore


class TestSkillModel:
    def test_skill_creation(self) -> None:
        skill = Skill(
            id=1,
            name="test-skill",
            description="A test skill",
            instructions="Do something",
            trigger_mode="pattern",
            trigger_patterns=["test"],
        )
        assert skill.name == "test-skill"
        assert skill.trigger_mode == "pattern"
        assert skill.trigger_patterns == ["test"]

    def test_skill_invalid_trigger_mode(self) -> None:
        with pytest.raises(ValueError, match="Invalid trigger_mode"):
            Skill(
                id=1,
                name="test",
                description="Test",
                instructions="Test",
                trigger_mode="invalid",
            )

    def test_skill_matches_pattern_case_insensitive(self) -> None:
        skill = Skill(
            id=1,
            name="test",
            description="Test",
            instructions="Test",
            trigger_mode="pattern",
            trigger_patterns=["debug|error"],
        )
        assert skill.matches_pattern("I have a DEBUG issue")
        assert skill.matches_pattern("There was an ERROR")
        assert not skill.matches_pattern("I need help")

    def test_skill_matches_pattern_invalid_regex_skipped(self) -> None:
        skill = Skill(
            id=1,
            name="test",
            description="Test",
            instructions="Test",
            trigger_mode="pattern",
            trigger_patterns=["[invalid", "valid"],
        )
        # Should not raise, just skip invalid regex
        assert skill.matches_pattern("this is valid text")
        assert not skill.matches_pattern("no match here")

    def test_skill_from_row(self) -> None:
        import json

        row = (
            1,
            "debug",
            "Debug skill",
            "Debug instructions",
            "pattern",
            json.dumps(["debug|error"]),
            json.dumps(["tool1", "tool2"]),
            5,
            1,
            "2025-01-01T00:00:00+00:00",
            "2025-01-01T00:00:00+00:00",
            3,
            "2025-06-01T12:00:00+00:00",
        )
        skill = Skill.from_row(row)
        assert skill.id == 1
        assert skill.name == "debug"
        assert skill.trigger_patterns == ["debug|error"]
        assert skill.tools_allowlist == ["tool1", "tool2"]
        assert skill.priority == 5
        assert skill.hit_count == 3
        assert skill.last_hit_at is not None
        assert skill.last_hit_at.year == 2025


class TestSkillStore:
    def test_create_and_get_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            skill = store.create(
                name="debug",
                description="Debug skill",
                instructions="Debug instructions here",
                trigger_mode="always",
            )
            assert skill.id == 1
            assert skill.name == "debug"

            retrieved = store.get(skill.id)
            assert retrieved is not None
            assert retrieved.name == "debug"

    def test_get_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="unique-skill",
                description="Unique",
                instructions="Test",
            )
            skill = store.get_by_name("unique-skill")
            assert skill is not None
            assert skill.name == "unique-skill"

    def test_list_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="skill-a", description="A", instructions="A", priority=1)
            store.create(name="skill-b", description="B", instructions="B", priority=2)
            store.create(
                name="skill-c",
                description="C",
                instructions="C",
                priority=0,
                is_active=False,
            )

            active = store.list_active()
            assert len(active) == 2
            # Sorted by priority DESC, then name ASC
            assert active[0].name == "skill-b"
            assert active[1].name == "skill-a"

    def test_update_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            skill = store.create(name="test", description="Old", instructions="Old")

            updated = store.update(skill.id, description="New")
            assert updated is not None
            assert updated.description == "New"

    def test_delete_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            skill = store.create(name="to-delete", description="X", instructions="X")

            assert store.delete(skill.id)
            assert store.get(skill.id) is None

    def test_unique_name_constraint(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="duplicate", description="X", instructions="X")
            with pytest.raises(sqlite3.IntegrityError):
                store.create(name="duplicate", description="Y", instructions="Y")


class TestSkillMatcher:
    def test_find_always_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="always-skill",
                description="Always active",
                instructions="Always",
                trigger_mode="always",
            )
            store.create(
                name="pattern-skill",
                description="Pattern",
                instructions="Pattern",
                trigger_mode="pattern",
                trigger_patterns=["test"],
            )

            matcher = SkillMatcher(store)
            always_skills = matcher.find_always_skills()
            assert len(always_skills) == 1
            assert always_skills[0].name == "always-skill"

    def test_find_by_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="debug-skill",
                description="Debug",
                instructions="Debug",
                trigger_mode="pattern",
                trigger_patterns=["debug|error|issue"],
            )
            store.create(
                name="web-skill",
                description="Web",
                instructions="Web",
                trigger_mode="pattern",
                trigger_patterns=["search|web|internet"],
            )

            matcher = SkillMatcher(store)
            matches = matcher.find_by_pattern("I have a debug issue")
            assert len(matches) == 1
            assert matches[0].name == "debug-skill"

    def test_find_relevant_skills_combines_always_and_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="always-skill",
                description="Always",
                instructions="Always",
                trigger_mode="always",
                priority=1,
            )
            store.create(
                name="pattern-skill",
                description="Pattern",
                instructions="Pattern",
                trigger_mode="pattern",
                trigger_patterns=["test"],
                priority=2,
            )

            matcher = SkillMatcher(store)
            relevant = matcher.find_relevant_skills("this is a test")
            assert len(relevant) == 2
            # Sorted by priority DESC
            assert relevant[0].name == "pattern-skill"
            assert relevant[1].name == "always-skill"

    def test_max_skills_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            for i in range(10):
                store.create(
                    name=f"skill-{i}",
                    description=f"Skill {i}",
                    instructions=f"Skill {i}",
                    trigger_mode="always",
                )

            matcher = SkillMatcher(store, max_skills=3)
            relevant = matcher.find_relevant_skills("test")
            assert len(relevant) == 3


class TestValidTriggerModes:
    def test_valid_trigger_modes(self) -> None:
        assert VALID_TRIGGER_MODES == {"always", "pattern", "intelligent"}


class TestSkillStoreToolsAllowlist:
    """Tests for tools_allowlist handling in SkillStore.update().

    Key invariant: passing tools_allowlist=[] to update() must NOT wipe
    an existing allowlist. Empty list means "no opinion" (skip the update),
    not "restrict to zero tools".
    """

    def test_update_preserves_tools_allowlist_on_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            skill = store.create(
                name="web-skill",
                description="Web skill",
                instructions="Search the web",
                trigger_mode="intelligent",
                tools_allowlist=["web__*"],
            )
            assert skill.tools_allowlist == ["web__*"]

            updated = store.update(skill.id, description="Updated description", tools_allowlist=[])
            assert updated is not None
            assert updated.tools_allowlist == ["web__*"]

    def test_update_applies_explicit_tools_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            skill = store.create(
                name="web-skill",
                description="Web skill",
                instructions="Search the web",
                trigger_mode="intelligent",
                tools_allowlist=["web__*"],
            )
            assert skill.tools_allowlist == ["web__*"]

            updated = store.update(skill.id, tools_allowlist=["web__*", "reddit__*"])
            assert updated is not None
            assert updated.tools_allowlist == ["web__*", "reddit__*"]

    def test_update_without_tools_allowlist_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            skill = store.create(
                name="web-skill",
                description="Web skill",
                instructions="Search the web",
                trigger_mode="intelligent",
                tools_allowlist=["web__*"],
            )

            updated = store.update(skill.id, description="Changed")
            assert updated is not None
            assert updated.tools_allowlist == ["web__*"]

    def test_create_with_empty_tools_allowlist_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            # tools_allowlist=[] on create should also mean "no opinion" → stored as None
            skill = store.create(
                name="basic-skill",
                description="Basic",
                instructions="Do stuff",
                tools_allowlist=[],
            )
            # Empty allowlist on create should store None
            assert skill.tools_allowlist is None


class TestSkillHitTracking:
    def test_skill_default_hit_count_is_zero(self) -> None:
        skill = Skill(
            id=1,
            name="test",
            description="Test",
            instructions="Test",
            trigger_mode="pattern",
        )
        assert skill.hit_count == 0

    def test_skill_default_last_hit_at_is_none(self) -> None:
        skill = Skill(
            id=1,
            name="test",
            description="Test",
            instructions="Test",
            trigger_mode="pattern",
        )
        assert skill.last_hit_at is None

    def test_create_skill_has_default_hit_count_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="test", description="Test", instructions="Test")
            skill = store.get_by_name("test")
            assert skill is not None
            assert skill.hit_count == 0
            assert skill.last_hit_at is None

    def test_increment_hit_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="test", description="Test", instructions="Test")
            store.increment_hit_count("test")
            store.increment_hit_count("test")
            skill = store.get_by_name("test")
            assert skill is not None
            assert skill.hit_count == 2

    def test_increment_hit_count_updates_last_hit_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="test", description="Test", instructions="Test")
            skill_before = store.get_by_name("test")
            assert skill_before is not None
            assert skill_before.last_hit_at is None

            store.increment_hit_count("test")
            skill_after = store.get_by_name("test")
            assert skill_after is not None
            assert skill_after.last_hit_at is not None
            assert isinstance(skill_after.last_hit_at, datetime)

    def test_increment_hit_count_nonexistent_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.increment_hit_count("nonexistent")

    def test_migration_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "skills.db")
            SkillStore(db_path)
            SkillStore(db_path)

    def test_migration_adds_columns_to_existing_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "skills.db")
            # Create a DB with the OLD schema (no hit_count, no last_hit_at)
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    trigger_mode TEXT NOT NULL DEFAULT 'pattern',
                    trigger_patterns_json TEXT,
                    tools_allowlist_json TEXT,
                    priority INTEGER DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO skills (name, description, instructions, trigger_mode, created_at, updated_at) "
                + "VALUES ('old-skill', 'Old', 'Old instructions', 'pattern', "
                + "'2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')"
            )
            conn.commit()
            conn.close()

            # Now open with SkillStore — migration should add columns
            store = SkillStore(db_path)
            skill = store.get_by_name("old-skill")
            assert skill is not None
            assert skill.hit_count == 0
            assert skill.last_hit_at is None

            conn2 = sqlite3.connect(db_path)
            columns = {row[1] for row in conn2.execute("PRAGMA table_info(skills)").fetchall()}
            conn2.close()
            assert "hit_count" in columns
            assert "last_hit_at" in columns


class TestSkillMatcherHitTracking:
    """Tests that find_relevant_skills increments hit_count and updates last_hit_at."""

    def test_find_relevant_skills_records_hit_for_always_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="always-skill",
                description="Always active",
                instructions="Always",
                trigger_mode="always",
            )
            matcher = SkillMatcher(store)

            matcher.find_relevant_skills("any text")

            skill = store.get_by_name("always-skill")
            assert skill is not None
            assert skill.hit_count == 1
            assert skill.last_hit_at is not None

    def test_find_relevant_skills_records_hit_for_pattern_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="debug-skill",
                description="Debug",
                instructions="Debug",
                trigger_mode="pattern",
                trigger_patterns=["debug|error"],
            )
            matcher = SkillMatcher(store)

            matcher.find_relevant_skills("I have a debug issue")

            skill = store.get_by_name("debug-skill")
            assert skill is not None
            assert skill.hit_count == 1
            assert skill.last_hit_at is not None

    def test_find_relevant_skills_no_match_no_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="unrelated-skill",
                description="Unrelated",
                instructions="Unrelated",
                trigger_mode="pattern",
                trigger_patterns=["python|code"],
            )
            matcher = SkillMatcher(store)

            matcher.find_relevant_skills("cooking recipe", include_always=False)

            skill = store.get_by_name("unrelated-skill")
            assert skill is not None
            assert skill.hit_count == 0
            assert skill.last_hit_at is None

    def test_find_relevant_skills_multiple_calls_accumulate_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="always-a",
                description="A",
                instructions="A",
                trigger_mode="always",
            )
            store.create(
                name="always-b",
                description="B",
                instructions="B",
                trigger_mode="always",
            )
            matcher = SkillMatcher(store)

            for _ in range(3):
                matcher.find_relevant_skills("any text")

            skill_a = store.get_by_name("always-a")
            skill_b = store.get_by_name("always-b")
            assert skill_a is not None
            assert skill_b is not None
            assert skill_a.hit_count == 3
            assert skill_b.hit_count == 3
