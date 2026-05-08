from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
            qdrant_path = Path(tmpdir) / "qdrant"
            config_path.write_text(f"""
llm:
  provider: openai
  config:
    model: gpt-4
embedder:
  provider: openai
  config:
    model: text-embedding-3-small
vector_store:
  provider: qdrant
  config:
    path: {qdrant_path}
    collection_name: test
    embedding_model_dims: 1536
""")

            mock_embedder = MagicMock()
            with patch("nanobot.vector_store.store.EmbedderFactory.create", return_value=mock_embedder):
                vs = VectorStore(str(config_path))
                assert vs._source == f"config_file:{config_path}"

    def test_init_requires_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("""
llm:
  provider: openai
embedder:
  provider: openai
vector_store:
  provider: qdrant
  config:
    collection_name: test
""")
            mock_embedder = MagicMock()
            with patch("nanobot.vector_store.store.EmbedderFactory.create", return_value=mock_embedder):
                with pytest.raises(ValueError, match="path is required"):
                    VectorStore(str(config_path))


class TestVectorStoreCollection:
    def test_collection_name_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            qdrant_path = Path(tmpdir) / "qdrant"
            config_path.write_text(f"""
llm:
  provider: openai
embedder:
  provider: openai
  config:
    model: text-embedding-3-small
vector_store:
  provider: qdrant
  config:
    path: {qdrant_path}
    collection_name: test
    embedding_model_dims: 1536
""")
            mock_embedder = MagicMock()
            with patch("nanobot.vector_store.store.EmbedderFactory.create", return_value=mock_embedder):
                vs = VectorStore(str(config_path))

                assert vs._get_collection_name("memories") == "nanobot_memories"
                assert vs._get_collection_name("skills") == "nanobot_skills"

    def test_search_text_creates_collection_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            qdrant_path = Path(tmpdir) / "qdrant"
            config_path.write_text(f"""
llm:
  provider: openai
embedder:
  provider: openai
  config:
    model: text-embedding-3-small
vector_store:
  provider: qdrant
  config:
    path: {qdrant_path}
    collection_name: test
    embedding_model_dims: 1536
""")
            mock_embedder = MagicMock()
            mock_embedder.embed.return_value = [0.0] * 1536
            with patch("nanobot.vector_store.store.EmbedderFactory.create", return_value=mock_embedder):
                vs = VectorStore(str(config_path))
                assert vs.has_collection(COLLECTION_SKILLS) is False

                results = vs.search_text(COLLECTION_SKILLS, "hello", limit=3)

                assert results == []
                assert vs.has_collection(COLLECTION_SKILLS) is True


class TestConstants:
    def test_collection_constants(self) -> None:
        assert COLLECTION_MEMORIES == "memories"
        assert COLLECTION_SKILLS == "skills"
