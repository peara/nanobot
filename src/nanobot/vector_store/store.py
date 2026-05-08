from __future__ import annotations

import copy
import logging
import uuid
from pathlib import Path
from typing import Any

import yaml

from nanobot.vector_store.exceptions import ConfigLoadError, ConfigNotFoundError

try:
    from mem0 import Memory
    from mem0.utils.factory import EmbedderFactory
except ImportError as exc:
    raise RuntimeError("mem0ai is not installed. Install dependencies: uv add mem0ai") from exc

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct
except ImportError as exc:
    raise RuntimeError("qdrant_client is not installed. Install dependencies: uv add qdrant-client") from exc

logger = logging.getLogger(__name__)

COLLECTION_MEMORIES = "memories"
COLLECTION_SKILLS = "skills"


class VectorStore:
    """Manages vector storage using Qdrant with shared client and embedder."""

    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._config = self._load_config(config_path)
        self._source = f"config_file:{config_path}"
        self._qdrant_client: QdrantClient = self._init_qdrant_client()
        self._embedder: Any = self._init_embedder()
        self._memories_instance: Memory | None = None
        logger.info("VectorStore initialized from %s", config_path)

    def _load_config(self, config_path: str) -> dict[str, Any]:
        path = Path(config_path)
        if not path.exists():
            raise ConfigNotFoundError(config_path)

        try:
            with open(path, encoding="utf-8") as fh:
                config = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigLoadError(config_path, str(exc)) from exc

        return config

    def _init_qdrant_client(self) -> QdrantClient:
        vs_config = self._config.get("vector_store", {}).get("config", {})
        path = vs_config.get("path")
        if not path:
            raise ValueError("vector_store.config.path is required")

        client = QdrantClient(path=path)
        logger.debug("Created shared QdrantClient for path=%s", path)
        return client

    def _init_embedder(self) -> Any:
        embedder_config = self._config.get("embedder", {})
        provider = embedder_config.get("provider")
        config = embedder_config.get("config", {})

        embedder = EmbedderFactory.create(provider, config, self._config.get("vector_store"))
        logger.debug("Created embedder: provider=%s", provider)
        return embedder

    def _get_collection_name(self, name: str) -> str:
        return f"nanobot_{name}"

    def ensure_collection(self, name: str) -> None:
        collection_name = self._get_collection_name(name)
        vs_config = self._config.get("vector_store", {}).get("config", {})
        dims = vs_config.get("embedding_model_dims", 1024)

        collections = self._qdrant_client.get_collections().collections
        existing = {c.name for c in collections}

        if collection_name not in existing:
            from qdrant_client.models import Distance, VectorParams

            self._qdrant_client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
            )
            logger.debug("Created Qdrant collection: %s", collection_name)

    def get_collection(self, name: str) -> Memory:
        if self._memories_instance is None:
            config = copy.deepcopy(self._config)
            config["vector_store"]["config"]["collection_name"] = self._get_collection_name(name)
            config["vector_store"]["config"]["client"] = self._qdrant_client
            self._memories_instance = Memory.from_config(config)
            logger.debug("Created mem0 Memory instance for collection: %s", name)
        return self._memories_instance

    def add_text(self, collection: str, text: str, metadata: dict[str, Any] | None = None) -> str:
        embedding = self._embedder.embed(text, "add")
        point_id = str(uuid.uuid4())

        payload = (metadata or {}).copy()
        payload["data"] = text

        self._qdrant_client.upsert(
            collection_name=self._get_collection_name(collection),
            points=[PointStruct(id=point_id, vector=embedding, payload=payload)],
        )

        logger.debug("Added text to collection %s: id=%s", collection, point_id)
        return point_id

    def search_text(
        self, collection: str, query: str, limit: int = 5, filter_metadata: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.ensure_collection(collection)
        embedding = self._embedder.embed(query, "search")

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        query_filter: Filter | None = None
        if filter_metadata:
            conditions: list[FieldCondition] = [
                FieldCondition(key=k, match=MatchValue(value=v)) for k, v in filter_metadata.items()
            ]
            if conditions:
                query_filter = Filter(must=conditions)  # type: ignore[arg-type]

        results = self._qdrant_client.query_points(
            collection_name=self._get_collection_name(collection),
            query=embedding,
            limit=limit,
            query_filter=query_filter,
        )

        return [{"id": str(r.id), "score": r.score, "metadata": r.payload or {}} for r in results.points]

    def delete_text(self, collection: str, filter_metadata: dict[str, Any]) -> int:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions: list[FieldCondition] = [
            FieldCondition(key=k, match=MatchValue(value=v)) for k, v in filter_metadata.items()
        ]
        if not conditions:
            return 0

        result = self._qdrant_client.delete(
            collection_name=self._get_collection_name(collection),
            points_selector=Filter(must=conditions),  # type: ignore[arg-type]
        )
        logger.debug("Deleted from collection %s: filter=%s", collection, filter_metadata)
        return result.operation_id if result.operation_id is not None else 0

    def has_collection(self, name: str) -> bool:
        collection_name = self._get_collection_name(name)
        collections = self._qdrant_client.get_collections().collections
        return collection_name in {c.name for c in collections}

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "qdrant",
            "config_source": self._source,
            "collections": [self._get_collection_name(n) for n in (COLLECTION_MEMORIES, COLLECTION_SKILLS)],
        }
