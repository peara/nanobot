from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nanobot.skills.models import Skill
from nanobot.vector_store import COLLECTION_SKILLS, VectorStore

if TYPE_CHECKING:
    from mem0 import Memory

logger = logging.getLogger(__name__)


class SkillMem0Store:
    """Manages skill embeddings in mem0 for semantic search.

    Uses a separate Qdrant collection (nanobot_skills) from user memories
    (nanobot_memories), loaded from the same config.mem0.yaml file.
    """

    def __init__(self, vector_store: VectorStore) -> None:
        self._memories: Memory = vector_store.get_collection(COLLECTION_SKILLS)

    AGENT_ID = "nanobot"

    def store_skill(self, skill: Skill) -> None:
        text = f"{skill.name}: {skill.description}"
        self._memories.add(
            [{"role": "user", "content": text}],
            agent_id=self.AGENT_ID,
            metadata={"skill_name": skill.name, "skill_id": skill.id},
        )
        logger.debug("Stored skill in mem0: %s", skill.name)

    def remove_skill(self, skill_name: str) -> None:
        self._memories.delete(agent_id=self.AGENT_ID, metadata={"skill_name": skill_name})
        logger.debug("Removed skill from mem0: %s", skill_name)

    def search_skills(self, query: str, limit: int = 3) -> list[str]:
        results = self._memories.search(query=query, agent_id=self.AGENT_ID, limit=limit)
        if isinstance(results, dict):
            results = results.get("results", [])
        skill_names: list[str] = []
        for result in results:
            if isinstance(result, dict):
                metadata = result.get("metadata", {})
                name = metadata.get("skill_name")
                if name:
                    skill_names.append(name)
        return skill_names
