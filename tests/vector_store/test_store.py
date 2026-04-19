from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nanobot.vector_store import (
    COLLECTION_MEMORIES,
    COLLECTION_SKILLS,
    ConfigLoadError,
    ConfigNotFoundError,
    VectorStore,
)


class TestVectorStoreInit:
    def test_init_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = str(Path(tmpdir) / "nonexistent.yaml")
            with pytest.raises(ConfigNotFoundError) as exc_info:
                VectorStore(config_path)
            assert config_path in str(exc_info.value)

    def test_init_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "invalid.yaml"
            config_path.write_text("invalid: yaml: content: [[[")
            with pytest.raises(ConfigLoadError):
                VectorStore(str(config_path))

    def test_init_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("""
llm:
  provider: openai
  config:
    model: gpt-4
vector_store:
  provider: qdrant
  config:
    path: ./data/test
    collection_name: test
""")
            vs = VectorStore(str(config_path))
            assert vs._source == f"config_file:{config_path}"


class TestVectorStoreCollection:
    def test_get_collection_caches_instance(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("llm: {provider: test}")

        vs = VectorStore(str(config_path))
        assert not vs.has_collection("test_collection")

        config = vs._clone_config_with_collection("test_collection")
        assert "collection_name" in config["vector_store"]["config"]
        assert vs.has_collection("test_collection") is False

    def test_collection_name_prefix(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("llm: {provider: test}")

        vs = VectorStore(str(config_path))
        config = vs._clone_config_with_collection("memories")

        assert config["vector_store"]["config"]["collection_name"] == "nanobot_memories"

        config2 = vs._clone_config_with_collection("skills")
        assert config2["vector_store"]["config"]["collection_name"] == "nanobot_skills"


class TestConstants:
    def test_collection_constants(self) -> None:
        assert COLLECTION_MEMORIES == "memories"
        assert COLLECTION_SKILLS == "skills"
