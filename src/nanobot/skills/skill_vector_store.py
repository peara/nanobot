from __future__ import annotations

import logging

from nanobot.skills.models import Skill
from nanobot.skills.score_filter import ScoreFilter, ThresholdFilter
from nanobot.vector_store import COLLECTION_SKILLS, VectorStore

SKILL_RETRIEVAL_PROMPT = "Represent this sentence for searching relevant passages: "

logger = logging.getLogger(__name__)


class SkillVectorStore:
    """Stores and retrieves skill embeddings for semantic search."""

    def __init__(
        self,
        vector_store: VectorStore,
        score_filter: ScoreFilter | None = None,
        use_retrieval_prompt: bool = True,
    ) -> None:
        self._store = vector_store
        self._store.ensure_collection(COLLECTION_SKILLS)
        self._score_filter = score_filter or ThresholdFilter()
        self._use_retrieval_prompt = use_retrieval_prompt

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
        search_query = self._build_query(query)
        results = self._store.search_text(COLLECTION_SKILLS, search_query, limit=limit)
        filtered = self._score_filter.filter_results(results)
        logger.debug(
            "search_skills: %d/%d results after filter (%s, prompt=%s)",
            len(filtered),
            len(results),
            type(self._score_filter).__name__,
            self._use_retrieval_prompt,
        )
        skill_names: list[str] = []
        for result in filtered:
            metadata = result.get("metadata", {})
            name = metadata.get("skill_name")
            if name:
                skill_names.append(name)
        return skill_names

    def search_skills_raw(self, query: str, limit: int = 3) -> list[dict]:
        search_query = self._build_query(query)
        return self._store.search_text(COLLECTION_SKILLS, search_query, limit=limit)

    def _build_query(self, query: str) -> str:
        return SKILL_RETRIEVAL_PROMPT + query if self._use_retrieval_prompt else query
