from __future__ import annotations

import logging

from nanobot.skills.models import Skill
from nanobot.vector_store import COLLECTION_SKILLS, VectorStore

logger = logging.getLogger(__name__)


class SkillVectorStore:
    """Stores and retrieves skill embeddings for semantic search."""

    def __init__(self, vector_store: VectorStore) -> None:
        self._store = vector_store
        self._store.ensure_collection(COLLECTION_SKILLS)

    def store_skill(self, skill: Skill) -> None:
        text = f"{skill.name}: {skill.description}"
        self._store.add_text(
            COLLECTION_SKILLS,
            text,
            metadata={"skill_name": skill.name, "skill_id": skill.id},
        )
        logger.debug("Stored skill embedding: %s", skill.name)

    def remove_skill(self, skill_name: str) -> None:
        self._store.delete_text(COLLECTION_SKILLS, {"skill_name": skill_name})
        logger.debug("Removed skill embedding: %s", skill_name)

    def search_skills(self, query: str, limit: int = 3) -> list[str]:
        try:
            results = self._store.search_text(COLLECTION_SKILLS, query, limit=limit)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Skill semantic search failed query=%s limit=%d", query, limit)
            return []
        skill_names: list[str] = []
        for result in results:
            metadata = result.get("metadata", {})
            name = metadata.get("skill_name")
            if name:
                skill_names.append(name)
        return skill_names
