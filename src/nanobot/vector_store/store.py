from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

from nanobot.vector_store.exceptions import ConfigLoadError, ConfigNotFoundError

try:
    from mem0 import Memory
except ImportError as exc:
    raise RuntimeError("mem0ai is not installed. Install dependencies: uv add mem0ai") from exc

logger = logging.getLogger(__name__)

COLLECTION_MEMORIES = "memories"
COLLECTION_SKILLS = "skills"


class VectorStore:
    """Multi-collection vector store using mem0/qdrant.

    Provides a shared vector store infrastructure with multiple collections:
    - "memories": User long-term memories
    - "skills": Skill embeddings for semantic matching
    - Future collections as needed

    Each collection is stored as a separate Qdrant collection (nanobot_{name}).
    """

    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._config = self._load_config(config_path)
        self._instances: dict[str, Memory] = {}
        self._source = f"config_file:{config_path}"
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

    def _clone_config_with_collection(self, name: str) -> dict[str, Any]:
        config = copy.deepcopy(self._config)

        if "vector_store" not in config:
            config["vector_store"] = {"provider": "qdrant", "config": {}}
        if "config" not in config["vector_store"]:
            config["vector_store"]["config"] = {}

        config["vector_store"]["config"]["collection_name"] = f"nanobot_{name}"
        return config

    def get_collection(self, name: str) -> Memory:
        if name not in self._instances:
            config = self._clone_config_with_collection(name)
            self._instances[name] = Memory.from_config(config)
            logger.debug("Created mem0 collection: nanobot_%s", name)
        return self._instances[name]

    def has_collection(self, name: str) -> bool:
        return name in self._instances

    def health_check(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "mem0",
            "config_source": self._source,
            "collections": list(self._instances.keys()),
        }
