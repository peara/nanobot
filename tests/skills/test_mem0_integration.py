from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanobot.skills import Skill, SkillMatcher, SkillMem0Store, SkillStore
from nanobot.vector_store import VectorStore


class TestSkillMem0Store:
    def test_store_skill_adds_to_mem0(self) -> None:
        mock_memory = MagicMock()
        mock_memory.add.return_value = {"id": "mem1"}

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("llm: {provider: test}")

            with patch.object(VectorStore, "get_collection", return_value=mock_memory):
                vs = VectorStore(str(config_path))
                mem0_store = SkillMem0Store(vs)

                skill = Skill(
                    id=1,
                    name="debug-skill",
                    description="Help with debugging",
                    instructions="Debug instructions",
                    trigger_mode="intelligent",
                )
                mem0_store.store_skill(skill)

                mock_memory.add.assert_called_once()
                call_args = mock_memory.add.call_args
                assert len(call_args[0][0]) == 1
                assert call_args[0][0][0]["role"] == "user"
                assert "debug-skill" in call_args[0][0][0]["content"]
                assert call_args[1]["metadata"]["skill_name"] == "debug-skill"

    def test_remove_skill_deletes_from_mem0(self) -> None:
        mock_memory = MagicMock()
        mock_memory.search.return_value = {
            "results": [
                {"id": "mem-1", "memory": "old-skill: desc", "metadata": {"skill_name": "old-skill"}},
                {"id": "mem-2", "memory": "other-skill: desc", "metadata": {"skill_name": "other-skill"}},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("llm: {provider: test}")

            with patch.object(VectorStore, "get_collection", return_value=mock_memory):
                vs = VectorStore(str(config_path))
                mem0_store = SkillMem0Store(vs)

                mem0_store.remove_skill("old-skill")

                mock_memory.delete.assert_called_once_with("mem-1")

    def test_search_skills_returns_skill_names(self) -> None:
        mock_memory = MagicMock()
        mock_memory.search.return_value = {
            "results": [
                {"id": "1", "text": "debug-skill: debugging help", "metadata": {"skill_name": "debug-skill"}},
                {"id": "2", "text": "web-skill: web search", "metadata": {"skill_name": "web-skill"}},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("llm: {provider: test}")

            with patch.object(VectorStore, "get_collection", return_value=mock_memory):
                vs = VectorStore(str(config_path))
                mem0_store = SkillMem0Store(vs)

                results = mem0_store.search_skills("I have a bug", limit=3)

                assert len(results) == 2
                assert results[0] == "debug-skill"
                assert results[1] == "web-skill"

                mock_memory.search.assert_called_once()
                call_args = mock_memory.search.call_args
                assert call_args[1]["query"] == "I have a bug"
                assert call_args[1]["limit"] == 3

    def test_search_skills_handles_empty_results(self) -> None:
        mock_memory = MagicMock()
        mock_memory.search.return_value = {"results": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("llm: {provider: test}")

            with patch.object(VectorStore, "get_collection", return_value=mock_memory):
                vs = VectorStore(str(config_path))
                mem0_store = SkillMem0Store(vs)

                results = mem0_store.search_skills("unknown topic", limit=5)

                assert len(results) == 0

    def test_search_skills_handles_dict_response(self) -> None:
        mock_memory = MagicMock()
        mock_memory.search.return_value = []

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("llm: {provider: test}")

            with patch.object(VectorStore, "get_collection", return_value=mock_memory):
                vs = VectorStore(str(config_path))
                mem0_store = SkillMem0Store(vs)

                results = mem0_store.search_skills("test", limit=2)

                assert len(results) == 0


class TestSkillMatcherIntelligent:
    def test_find_by_intelligent_with_mem0(self) -> None:
        mock_mem0 = MagicMock()
        mock_mem0.search_skills.return_value = ["debug-skill", "error-skill"]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="debug-skill", description="Debug", instructions="Debug", trigger_mode="intelligent")
            store.create(name="error-skill", description="Error", instructions="Error", trigger_mode="intelligent")
            store.create(
                name="inactive-skill",
                description="Inactive",
                instructions="Inactive",
                trigger_mode="intelligent",
                is_active=False,
            )

            matcher = SkillMatcher(store, mem0_store=mock_mem0)

            skills = matcher.find_by_intelligent("I have an error in my code")

            assert len(skills) == 2
            assert skills[0].name == "debug-skill"
            assert skills[1].name == "error-skill"

            mock_mem0.search_skills.assert_called_once_with("I have an error in my code", limit=5)

    def test_find_by_intelligent_filters_inactive(self) -> None:
        mock_mem0 = MagicMock()
        mock_mem0.search_skills.return_value = ["active-skill", "inactive-skill"]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="active-skill", description="Active", instructions="Active", trigger_mode="intelligent")
            store.create(
                name="inactive-skill",
                description="Inactive",
                instructions="Inactive",
                trigger_mode="intelligent",
                is_active=False,
            )

            matcher = SkillMatcher(store, mem0_store=mock_mem0)

            skills = matcher.find_by_intelligent("test query")

            assert len(skills) == 1
            assert skills[0].name == "active-skill"

    def test_find_by_intelligent_returns_empty_without_mem0(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            matcher = SkillMatcher(store, mem0_store=None)

            skills = matcher.find_by_intelligent("any query")

            assert len(skills) == 0

    def test_find_relevant_skills_includes_intelligent(self) -> None:
        mock_mem0 = MagicMock()
        mock_mem0.search_skills.return_value = ["ai-skill"]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="always-skill", description="Always", instructions="Always", trigger_mode="always", priority=5
            )
            store.create(
                name="pattern-skill",
                description="Pattern",
                instructions="Pattern",
                trigger_mode="pattern",
                trigger_patterns=["test"],
                priority=3,
            )
            store.create(name="ai-skill", description="AI", instructions="AI", trigger_mode="intelligent", priority=10)

            matcher = SkillMatcher(store, mem0_store=mock_mem0)

            skills = matcher.find_relevant_skills("test query about AI")

            skill_names = [s.name for s in skills]
            assert "always-skill" in skill_names
            assert "pattern-skill" in skill_names
            assert "ai-skill" in skill_names

    def test_find_relevant_skills_deduplicates(self) -> None:
        mock_mem0 = MagicMock()
        mock_mem0.search_skills.return_value = ["always-skill"]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(name="always-skill", description="Always", instructions="Always", trigger_mode="always")

            matcher = SkillMatcher(store, mem0_store=mock_mem0)

            skills = matcher.find_relevant_skills("any query")

            assert len(skills) == 1
            assert skills[0].name == "always-skill"
