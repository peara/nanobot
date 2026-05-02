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
        assert len(prompt.variables) == 3

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
            "subagent_default",
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
