from __future__ import annotations

import socket
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest

from nanobot.skills import Skill, SkillMatcher, SkillStore, SkillVectorStore
from nanobot.vector_store import COLLECTION_SKILLS, VectorStore


def _require_reachable_endpoint(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        pytest.skip(f"Invalid integration endpoint: {url}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex((host, port)) != 0:
            pytest.skip(f"Integration endpoint is unavailable: {url}")


class TestSkillVectorStore:
    def test_store_skill_adds_to_vector_store(self) -> None:
        mock_vs = MagicMock(spec=VectorStore)
        mock_vs.add_text.return_value = "point-123"

        mem0_store = SkillVectorStore(mock_vs)

        skill = Skill(
            id=1,
            name="debug-skill",
            description="Help with debugging",
            instructions="Debug instructions",
            trigger_mode="intelligent",
        )
        mem0_store.store_skill(skill)

        mock_vs.add_text.assert_called_once()
        call_args = mock_vs.add_text.call_args
        assert call_args[0][0] == COLLECTION_SKILLS
        assert "debug-skill" in call_args[0][1]
        assert call_args[1]["metadata"]["skill_name"] == "debug-skill"

    def test_remove_skill_deletes_from_vector_store(self) -> None:
        mock_vs = MagicMock(spec=VectorStore)

        mem0_store = SkillVectorStore(mock_vs)
        mem0_store.remove_skill("old-skill")

        mock_vs.delete_text.assert_called_once_with(COLLECTION_SKILLS, {"skill_name": "old-skill"})

    def test_search_skills_returns_skill_names(self) -> None:
        mock_vs = MagicMock(spec=VectorStore)
        mock_vs.search_text.return_value = [
            {"id": "1", "score": 0.95, "metadata": {"skill_name": "debug-skill"}},
            {"id": "2", "score": 0.85, "metadata": {"skill_name": "web-skill"}},
        ]

        mem0_store = SkillVectorStore(mock_vs)

        results = mem0_store.search_skills("I have a bug", limit=3)

        assert len(results) == 2
        assert results[0] == "debug-skill"
        assert results[1] == "web-skill"

        mock_vs.search_text.assert_called_once()
        call_args = mock_vs.search_text.call_args
        assert call_args[0][0] == COLLECTION_SKILLS
        assert call_args[0][1] == "I have a bug"
        assert call_args[1]["limit"] == 3

    def test_search_skills_handles_empty_results(self) -> None:
        mock_vs = MagicMock(spec=VectorStore)
        mock_vs.search_text.return_value = []

        mem0_store = SkillVectorStore(mock_vs)

        results = mem0_store.search_skills("unknown topic", limit=5)

        assert len(results) == 0

    def test_search_skills_returns_empty_on_backend_error(self) -> None:
        mock_vs = MagicMock(spec=VectorStore)
        mock_vs.search_text.side_effect = RuntimeError("backend_down")

        mem0_store = SkillVectorStore(mock_vs)

        results = mem0_store.search_skills("any query", limit=5)

        assert results == []


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


class TestSkillMem0Integration:
    @pytest.mark.integration
    def test_store_skill_and_search_skills_with_real_mem0(self) -> None:
        import yaml

        from nanobot.skills import SkillVectorStore
        from nanobot.vector_store import VectorStore

        with tempfile.TemporaryDirectory() as tmpdir:
            base_mem0_path = Path(tmpdir) / "qdrant"
            base_config_path = Path(tmpdir) / "config.mem0.yaml"

            config = {
                "llm": {
                    "provider": "lmstudio",
                    "config": {
                        "model": "google/gemma-4-31b",
                        "lmstudio_base_url": "http://192.168.1.7:1234/v1",
                        "api_key": "lm-studio",
                        "temperature": 0.1,
                        "max_tokens": 1200,
                        "lmstudio_response_format": {
                            "type": "json_schema",
                            "json_schema": {"type": "object", "schema": {}},
                        },
                    },
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": "mxbai-embed-large",
                        "openai_base_url": "http://192.168.1.7:1234/v1",
                        "api_key": "lm-studio",
                    },
                },
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": str(base_mem0_path),
                        "collection_name": "nanobot_memories",
                        "embedding_model_dims": 1024,
                    },
                },
            }
            _require_reachable_endpoint(str(config["embedder"]["config"]["openai_base_url"]))
            base_config_path.write_text(yaml.dump(config), encoding="utf-8")

            vs = VectorStore(str(base_config_path))
            mem0_store = SkillVectorStore(vs)

            skill = Skill(
                id=1,
                name="yahoo-lens-hunter",
                description="Hunt for camera lens deals on Yahoo auction",
                instructions="Search Yahoo auction for good camera lens deals",
                trigger_mode="intelligent",
            )
            mem0_store.store_skill(skill)

            results = mem0_store.search_skills("I need a camera lens", limit=3)
            assert "yahoo-lens-hunter" in results, f"Expected 'yahoo-lens-hunter' in results, got {results}"

            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="yahoo-lens-hunter",
                description="Hunt for camera lens deals on Yahoo auction",
                instructions="Search Yahoo auction for good camera lens deals",
                trigger_mode="intelligent",
                is_active=True,
            )

            matcher = SkillMatcher(store, mem0_store=mem0_store)
            matched = matcher.find_by_intelligent("I need a camera lens")
            assert len(matched) == 1
            assert matched[0].name == "yahoo-lens-hunter"

    @pytest.mark.persistence
    def test_skill_survives_vectorstore_restart(self) -> None:
        import yaml

        from nanobot.skills import SkillVectorStore
        from nanobot.vector_store import VectorStore

        with tempfile.TemporaryDirectory() as tmpdir:
            base_mem0_path = Path(tmpdir) / "qdrant"
            base_config_path = Path(tmpdir) / "config.mem0.yaml"

            config = {
                "llm": {
                    "provider": "lmstudio",
                    "config": {
                        "model": "google/gemma-4-31b",
                        "lmstudio_base_url": "http://192.168.1.7:1234/v1",
                        "api_key": "lm-studio",
                        "temperature": 0.1,
                        "max_tokens": 1200,
                        "lmstudio_response_format": {
                            "type": "json_schema",
                            "json_schema": {"type": "object", "schema": {}},
                        },
                    },
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": "mxbai-embed-large",
                        "openai_base_url": "http://192.168.1.7:1234/v1",
                        "api_key": "lm-studio",
                    },
                },
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": str(base_mem0_path),
                        "collection_name": "nanobot_memories",
                        "embedding_model_dims": 1024,
                    },
                },
            }
            _require_reachable_endpoint(str(config["embedder"]["config"]["openai_base_url"]))
            base_config_path.write_text(yaml.dump(config), encoding="utf-8")

            vs1 = VectorStore(str(base_config_path))
            mem0_store1 = SkillVectorStore(vs1)

            skill = Skill(
                id=1,
                name="yahoo-lens-hunter",
                description="Hunt for camera lens deals on Yahoo auction",
                instructions="Search Yahoo auction for good camera lens deals",
                trigger_mode="intelligent",
            )
            mem0_store1.store_skill(skill)

            results1 = mem0_store1.search_skills("camera lens", limit=3)
            assert "yahoo-lens-hunter" in results1, f"Before restart: expected 'yahoo-lens-hunter', got {results1}"

            del vs1
            del mem0_store1

            vs2 = VectorStore(str(base_config_path))
            mem0_store2 = SkillVectorStore(vs2)

            results2 = mem0_store2.search_skills("camera lens", limit=3)
            assert "yahoo-lens-hunter" in results2, f"After restart: expected 'yahoo-lens-hunter', got {results2}"

            store = SkillStore(str(Path(tmpdir) / "skills.db"))
            store.create(
                name="yahoo-lens-hunter",
                description="Hunt for camera lens deals on Yahoo auction",
                instructions="Search Yahoo auction for good camera lens deals",
                trigger_mode="intelligent",
                is_active=True,
            )
            matcher = SkillMatcher(store, mem0_store=mem0_store2)
            matched = matcher.find_by_intelligent("camera lens")
            assert len(matched) == 1
            assert matched[0].name == "yahoo-lens-hunter"
