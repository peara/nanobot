from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.prompts import PromptStore, PromptVariableError, extract_variables


@pytest.fixture
def prompt_store(tmp_path: Path) -> PromptStore:
    db_path = str(tmp_path / "prompts.db")
    return PromptStore(db_path, seed_defaults=False)


@pytest.fixture
def seeded_store(tmp_path: Path) -> PromptStore:
    db_path = str(tmp_path / "prompts.db")
    return PromptStore(db_path, seed_defaults=True)


class TestExtractVariables:
    def test_extract_single_variable(self) -> None:
        assert extract_variables("Hello {name}") == ["name"]

    def test_extract_multiple_variables(self) -> None:
        result = extract_variables("Hello {name}, welcome to {location}")
        assert result == ["location", "name"]

    def test_extract_repeated_variable(self) -> None:
        assert extract_variables("Hello {name}, goodbye {name}") == ["name"]

    def test_extract_no_variables(self) -> None:
        assert extract_variables("Hello world") == []

    def test_extract_complex_names(self) -> None:
        result = extract_variables("{assistant_name} at {current_time}")
        assert result == ["assistant_name", "current_time"]


class TestPromptStoreSave:
    def test_create_prompt(self, prompt_store: PromptStore) -> None:
        prompt = prompt_store.save("test_prompt", "Hello {name}", "orchestrator")
        assert prompt.id > 0
        assert prompt.name == "test_prompt"
        assert prompt.content == "Hello {name}"
        assert prompt.role == "orchestrator"
        assert prompt.variables == ["name"]
        assert prompt.version == 1
        assert prompt.is_active is True

    def test_update_prompt_version(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "Hello {name}", "orchestrator")
        prompt = prompt_store.save("test", "Hello {name}!", "orchestrator")
        assert prompt.version == 2

    def test_save_empty_content_raises(self, prompt_store: PromptStore) -> None:
        with pytest.raises(ValueError, match="empty"):
            prompt_store.save("test", "", "orchestrator")

    def test_save_whitespace_content_raises(self, prompt_store: PromptStore) -> None:
        with pytest.raises(ValueError, match="empty"):
            prompt_store.save("test", "   ", "orchestrator")


class TestPromptStoreGetActive:
    def test_get_active(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "Hello {name}", "orchestrator")
        prompt = prompt_store.get_active("test")
        assert prompt is not None
        assert prompt.name == "test"

    def test_get_active_not_found(self, prompt_store: PromptStore) -> None:
        assert prompt_store.get_active("nonexistent") is None

    def test_get_active_after_deactivate(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "Hello", "orchestrator")
        prompt_store.deactivate("test")
        assert prompt_store.get_active("test") is None


class TestPromptStoreRender:
    def test_render_with_variables(self, prompt_store: PromptStore) -> None:
        prompt_store.save("greeting", "Hello {name}!", "orchestrator")
        result = prompt_store.render("greeting", name="Alice")
        assert result == "Hello Alice!"

    def test_render_multiple_variables(self, prompt_store: PromptStore) -> None:
        prompt_store.save("msg", "Hello {name} from {location}", "orchestrator")
        result = prompt_store.render("msg", name="Alice", location="Paris")
        assert result == "Hello Alice from Paris"

    def test_render_missing_variable_error(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "Hello {name} and {location}", "orchestrator")
        with pytest.raises(PromptVariableError) as exc:
            prompt_store.render("test", name="Alice")
        assert "location" in str(exc.value)

    def test_render_unknown_prompt(self, prompt_store: PromptStore) -> None:
        with pytest.raises(KeyError):
            prompt_store.render("nonexistent")

    def test_render_no_variables(self, prompt_store: PromptStore) -> None:
        prompt_store.save("static", "Hello world", "orchestrator")
        result = prompt_store.render("static")
        assert result == "Hello world"


class TestPromptStoreListAll:
    def test_list_all_no_filter(self, prompt_store: PromptStore) -> None:
        prompt_store.save("a", "Content A", "orchestrator")
        prompt_store.save("b", "Content B", "planner")
        prompts = prompt_store.list_all()
        assert len(prompts) == 2

    def test_list_all_filtered_by_role(self, prompt_store: PromptStore) -> None:
        prompt_store.save("a", "Content A", "orchestrator")
        prompt_store.save("b", "Content B", "planner")
        prompts = prompt_store.list_all(role="orchestrator")
        assert len(prompts) == 1
        assert prompts[0].name == "a"

    def test_list_all_empty(self, prompt_store: PromptStore) -> None:
        assert prompt_store.list_all() == []


class TestPromptStoreSetActive:
    def test_set_active_reactivates_after_deactivate(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "Version 1", "orchestrator")
        prompt_store.deactivate("test")
        assert prompt_store.get_active("test") is None

        result = prompt_store.set_active("test", 1)
        assert result is not None
        assert result.content == "Version 1"

        active = prompt_store.get_active("test")
        assert active is not None
        assert active.content == "Version 1"

    def test_set_active_not_found(self, prompt_store: PromptStore) -> None:
        assert prompt_store.set_active("nonexistent", 1) is None

    def test_set_activates_deactivated_prompt(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "Hello", "orchestrator")
        prompt_store.deactivate("test")
        prompt_store.save("test", "Hello updated", "orchestrator")

        prompt_store.deactivate("test")
        assert prompt_store.get_active("test") is None

        result = prompt_store.set_active("test", 2)
        assert result is not None


class TestPromptStoreDeactivate:
    def test_deactivate(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "Hello", "orchestrator")
        assert prompt_store.deactivate("test") is True
        assert prompt_store.get_active("test") is None

    def test_deactivate_not_found(self, prompt_store: PromptStore) -> None:
        assert prompt_store.deactivate("nonexistent") is False


class TestPromptStoreSeedDefaults:
    def test_seed_defaults_new_db(self, seeded_store: PromptStore) -> None:
        prompt = seeded_store.get_active("orchestrator_main")
        assert prompt is not None
        assert "orchestrator" == prompt.role
        assert len(prompt.variables) == 1

    def test_orchestrator_prompt_defines_script_skill_boundary(self, seeded_store: PromptStore) -> None:
        prompt = seeded_store.get_active("orchestrator_main")
        assert prompt is not None
        content = prompt.content

        assert "Reusable artifacts boundary" in content
        assert "Web Script = executable extractor returning structured data" in content
        assert "Skill = reusable workflow/policy" in content
        assert "Never store formatting, language, bullet-count policy" in content
        assert "Treat empty params_schema as unspecified/flexible" in content

    def test_seed_defaults_idempotent(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "prompts.db")
        store1 = PromptStore(db_path, seed_defaults=True)
        count1 = len(store1.list_all())

        store2 = PromptStore(db_path, seed_defaults=True)
        count2 = len(store2.list_all())

        assert count1 == count2

    def test_all_defaults_seeded(self, seeded_store: PromptStore) -> None:
        prompts = seeded_store.list_all()
        names = {p.name for p in prompts}
        expected = {
            "orchestrator_main",
            "orchestrator_main_time",
            "orchestrator_user_context",
            "subagent_default",
            "subagent_scheduled",
            "subagent_time",
            "plan_brief_extractor",
            "plan_execution_agent",
            "plan_recovery",
            "scratchpad_system",
            "scratchpad_next_instruction",
            "scratchpad_user",
            "skill_instructions",
            "finalize_response",
            "tool_call_limit_finalize",
            "quality_assessment",
            "learning_extraction",
            "skill_lifecycle",
        }
        assert names == expected


class TestPromptStoreHistory:
    """Design B behavior: save() preserves prior versions, rollback is real."""

    def test_save_preserves_history(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "v1 content", "orchestrator")
        prompt_store.save("test", "v2 content", "orchestrator")
        prompt_store.save("test", "v3 content", "orchestrator")

        versions = prompt_store.list_versions("test")
        assert [v.version for v in versions] == [3, 2, 1]
        assert [v.content for v in versions] == ["v3 content", "v2 content", "v1 content"]

    def test_only_one_active_at_a_time(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "v1", "orchestrator")
        prompt_store.save("test", "v2", "orchestrator")
        prompt_store.save("test", "v3", "orchestrator")

        active = prompt_store.get_active("test")
        assert active is not None
        assert active.version == 3
        assert active.is_active is True

        # Prior versions exist but are inactive.
        for v in prompt_store.list_versions("test"):
            if v.version == 3:
                assert v.is_active is True
            else:
                assert v.is_active is False

    def test_set_active_rolls_back(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "v1 content", "orchestrator")
        prompt_store.save("test", "v2 content", "orchestrator")
        prompt_store.save("test", "v3 content", "orchestrator")

        result = prompt_store.set_active("test", 1)
        assert result is not None
        assert result.content == "v1 content"

        active = prompt_store.get_active("test")
        assert active is not None
        assert active.version == 1
        assert active.content == "v1 content"

    def test_list_versions_newest_first(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "v1", "orchestrator")
        prompt_store.save("test", "v2", "orchestrator")
        prompt_store.save("test", "v3", "orchestrator")

        versions = prompt_store.list_versions("test")
        # Newest first by design — used for displaying history lists.
        assert [v.version for v in versions] == [3, 2, 1]

    def test_list_versions_empty(self, prompt_store: PromptStore) -> None:
        assert prompt_store.list_versions("nonexistent") == []

    def test_get_version_returns_specific(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "v1 content", "orchestrator")
        prompt_store.save("test", "v2 content", "orchestrator")
        prompt_store.save("test", "v3 content", "orchestrator")

        v1 = prompt_store.get_version("test", 1)
        assert v1 is not None
        assert v1.content == "v1 content"
        assert v1.is_active is False  # prior version, not the active one

        v3 = prompt_store.get_version("test", 3)
        assert v3 is not None
        assert v3.content == "v3 content"
        assert v3.is_active is True

    def test_get_version_not_found(self, prompt_store: PromptStore) -> None:
        prompt_store.save("test", "v1", "orchestrator")
        assert prompt_store.get_version("test", 99) is None
        assert prompt_store.get_version("nonexistent", 1) is None

    def test_save_after_rollback_continues_history(self, prompt_store: PromptStore) -> None:
        # Roll back to v1, then save new content — should land at v3 (not v2)
        # because version counter is monotonic across the full history.
        prompt_store.save("test", "v1 content", "orchestrator")
        prompt_store.save("test", "v2 content", "orchestrator")
        prompt_store.set_active("test", 1)
        prompt_store.save("test", "v3 content (after rollback)", "orchestrator")

        versions = prompt_store.list_versions("test")
        assert [v.version for v in versions] == [3, 2, 1]
        assert versions[0].content == "v3 content (after rollback)"
        assert versions[0].is_active is True

    def test_list_all_returns_only_active(self, prompt_store: PromptStore) -> None:
        prompt_store.save("a", "v1", "orchestrator")
        prompt_store.save("a", "v2", "orchestrator")
        prompt_store.save("b", "v1", "planner")
        prompt_store.set_active("a", 1)  # rollback a to v1

        active = prompt_store.list_all()
        # One entry per name, with the active row's content.
        assert len(active) == 2
        by_name = {p.name: p for p in active}
        assert by_name["a"].content == "v1"
        assert by_name["a"].is_active is True
        assert by_name["b"].content == "v1"

    def test_list_all_filtered_by_role_only_active(self, prompt_store: PromptStore) -> None:
        prompt_store.save("a", "v1", "orchestrator")
        prompt_store.save("a", "v2", "orchestrator")
        prompt_store.set_active("a", 1)
        prompt_store.save("b", "v1", "planner")
        prompt_store.save("b", "v2", "planner")

        orch = prompt_store.list_all(role="orchestrator")
        assert len(orch) == 1
        assert orch[0].name == "a"
        assert orch[0].content == "v1"

        plan = prompt_store.list_all(role="planner")
        assert len(plan) == 1
        assert plan[0].name == "b"
        assert plan[0].content == "v2"  # latest, active


class TestPromptStoreMigration:
    """Schema migration from old UNIQUE(name) to new UNIQUE(name, version)."""

    def test_migrates_legacy_unique_on_name(self, tmp_path: Path) -> None:
        import sqlite3

        db_path = str(tmp_path / "legacy.db")

        # Build a pre-migration DB by hand: old schema with UNIQUE on name.
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'orchestrator',
                variables_json TEXT DEFAULT '[]',
                is_active INTEGER NOT NULL DEFAULT 1,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO prompts (name, content, version, created_at, updated_at) "
            "VALUES (?, ?, 1, '2026-01-01', '2026-01-01')",
            ("x", "old content"),
        )
        conn.execute(
            "INSERT INTO prompts (name, content, version, created_at, updated_at) "
            "VALUES (?, ?, 1, '2026-01-01', '2026-01-01')",
            ("y", "another old content"),
        )
        conn.commit()
        conn.close()

        # Opening the store should migrate transparently.
        store = PromptStore(db_path, seed_defaults=False)

        # All rows preserved.
        assert len(store.list_all()) == 2
        assert store.get_active("x") is not None
        assert store.get_active("x").content == "old content"
        assert store.get_active("y") is not None
        assert store.get_active("y").content == "another old content"

        # Now multi-row works.
        store.save("x", "new content", "orchestrator")
        versions = store.list_versions("x")
        assert [v.version for v in versions] == [2, 1]
        assert versions[0].content == "new content"
        assert versions[1].content == "old content"

    def test_migration_idempotent(self, tmp_path: Path) -> None:
        # Opening an already-migrated store twice should not duplicate rows
        # or otherwise corrupt state.
        db_path = str(tmp_path / "m.db")
        store1 = PromptStore(db_path, seed_defaults=False)
        store1.save("a", "v1", "orchestrator")
        store1.save("a", "v2", "orchestrator")

        store2 = PromptStore(db_path, seed_defaults=False)
        versions = store2.list_versions("a")
        assert [v.version for v in versions] == [2, 1]
        assert store2.get_active("a").content == "v2"
