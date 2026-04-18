from __future__ import annotations

from typing import TYPE_CHECKING

from nanobot.skills.models import Skill

if TYPE_CHECKING:
    from nanobot.prompts import PromptStore


MAX_SKILL_INSTRUCTIONS_CHARS = 5000
MAX_TOTAL_SKILL_CHARS = 15000


def build_skill_messages(skills: list[Skill], prompts: PromptStore) -> list[dict[str, str]]:
    """Build system messages from a list of skills.

    Follows the scratchpad pattern for injecting context into agent messages.
    Each skill's instructions are rendered via a prompt template.

    Args:
        skills: List of skills to include (should be pre-filtered and sorted)
        prompts: PromptStore for rendering skill templates

    Returns:
        List of system message dicts to prepend to the message list
    """
    if not skills:
        return []

    messages: list[dict[str, str]] = []
    total_chars = 0

    for skill in skills:
        instructions = skill.instructions.strip()
        if not instructions:
            continue

        # Clip individual skill instructions
        clipped_instructions = instructions[:MAX_SKILL_INSTRUCTIONS_CHARS]
        if len(clipped_instructions) < len(instructions):
            clipped_instructions += "\n[truncated]"

        # Check total budget
        if total_chars + len(clipped_instructions) > MAX_TOTAL_SKILL_CHARS:
            break

        try:
            content = prompts.render(
                "skill_instructions",
                skill_name=skill.name,
                skill_description=skill.description,
                skill_instructions=clipped_instructions,
            )
        except KeyError:
            # Fall back to raw instructions if template not found
            content = f"[Skill: {skill.name}]\n{skill.description}\n\n{clipped_instructions}"

        messages.append({"role": "system", "content": content})
        total_chars += len(clipped_instructions)

    return messages


def build_skill_catalog_message(skills: list[Skill]) -> dict[str, str] | None:
    """Build a single system message listing available skills by name and description.

    This is the "Tier 1" skill exposure - showing skill names descriptions (~100 tokens each)
    so the model knows what skills exist without loading full instructions.

    Args:
        skills: List of all active skills (usually from SkillStore.list_active())

    Returns:
        System message dict with skill catalog, or None if no skills
    """
    if not skills:
        return None

    lines = ["Available skills:"]
    for skill in skills:
        desc = skill.description.strip().split("\n")[0][:100]
        if len(skill.description) > 100:
            desc = desc[:97] + "..."
        lines.append(f"- {skill.name}: {desc}")

    content = "\n".join(lines)
    return {"role": "system", "content": content}
