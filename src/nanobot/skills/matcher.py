from __future__ import annotations

from typing import TYPE_CHECKING

from nanobot.skills.models import Skill

if TYPE_CHECKING:
    from nanobot.skills.skill_vector_store import SkillVectorStore
    from nanobot.skills.store import SkillStore


class SkillMatcher:
    """Matches skills based on trigger modes (always, pattern, intelligent).

    The matcher determines which skills should be activated for a given context
    based on their trigger mode configuration.
    """

    def __init__(
        self,
        skill_store: SkillStore,
        mem0_store: SkillVectorStore | None = None,
        max_skills: int = 5,
    ) -> None:
        self._store = skill_store
        self._mem0 = mem0_store
        self._max_skills = max_skills

    def find_always_skills(self) -> list[Skill]:
        all_skills = self._store.list_active()
        return [s for s in all_skills if s.trigger_mode == "always"]

    def find_by_pattern(self, text: str) -> list[Skill]:
        all_skills = self._store.list_active()
        matching: list[Skill] = []

        for skill in all_skills:
            if skill.trigger_mode != "pattern":
                continue
            if skill.matches_pattern(text):
                matching.append(skill)
                if len(matching) >= self._max_skills:
                    break

        return matching

    def find_by_intelligent(self, goal: str) -> list[Skill]:
        if not self._mem0:
            return []
        skill_names = self._mem0.search_skills(goal, limit=self._max_skills)
        skills: list[Skill] = []
        for name in skill_names:
            skill = self._store.get_by_name(name)
            if skill and skill.is_active:
                skills.append(skill)
        return skills

    def find_relevant_skills(self, goal: str, include_always: bool = True) -> list[Skill]:
        seen_names: set[str] = set()
        result: list[Skill] = []

        if include_always:
            for skill in self.find_always_skills():
                if skill.name not in seen_names:
                    seen_names.add(skill.name)
                    result.append(skill)
                    self._store.increment_hit_count(skill.name)

        for skill in self.find_by_pattern(goal):
            if skill.name not in seen_names:
                seen_names.add(skill.name)
                result.append(skill)
                self._store.increment_hit_count(skill.name)

        for skill in self.find_by_intelligent(goal):
            if skill.name not in seen_names:
                seen_names.add(skill.name)
                result.append(skill)
                self._store.increment_hit_count(skill.name)

        result.sort(key=lambda s: s.priority, reverse=True)

        return result[: self._max_skills]

    def get_skill_by_name(self, name: str) -> Skill | None:
        skill = self._store.get_by_name(name)
        if skill and skill.is_active:
            return skill
        return None
