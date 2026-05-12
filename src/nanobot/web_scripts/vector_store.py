from __future__ import annotations

import json
import logging

from nanobot.vector_store import COLLECTION_WEB_SCRIPTS, VectorStore
from nanobot.web_scripts.models import WebScript

logger = logging.getLogger(__name__)


class WebScriptVectorStore:
    """Semantic index for browser data extraction scripts."""

    def __init__(self, vector_store: VectorStore) -> None:
        self._store = vector_store
        self._store.ensure_collection(COLLECTION_WEB_SCRIPTS)

    def store_script(self, script: WebScript) -> str:
        self._store.delete_text(COLLECTION_WEB_SCRIPTS, {"script_name": script.name})
        text = self._script_text(script)
        vector_id = self._store.add_text(
            COLLECTION_WEB_SCRIPTS,
            text,
            metadata={"script_name": script.name, "script_id": script.id},
        )
        logger.debug("Stored web script embedding: %s", script.name)
        return vector_id

    def search_scripts(self, query: str, limit: int = 5) -> list[str]:
        results = self._store.search_text(COLLECTION_WEB_SCRIPTS, query, limit=limit)
        names: list[str] = []
        for result in results:
            metadata = result.get("metadata", {})
            name = metadata.get("script_name")
            if name:
                names.append(str(name))
        return names

    def _script_text(self, script: WebScript) -> str:
        return "\n".join(
            [
                f"Name: {script.name}",
                f"Description: {script.description}",
                f"Tags: {', '.join(script.tags)}",
                f"Params schema: {json.dumps(script.params_schema, ensure_ascii=True, sort_keys=True)}",
                f"Result schema: {json.dumps(script.result_schema, ensure_ascii=True, sort_keys=True)}",
            ]
        )
