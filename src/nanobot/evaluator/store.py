from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QualityAssessment:
    """Phase 1 output: quality score and learning gate."""

    quality_score: int
    quality_reason: str
    has_learnings: bool
    confidence: str


# JSON Schema for structured output (OpenAI-compatible)
QUALITY_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "quality_assessment",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "quality_score": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Quality score from 1 (failed) to 5 (excellent)",
                },
                "quality_reason": {
                    "type": "string",
                    "description": "Brief explanation of the quality score (1-2 sentences)",
                },
                "has_learnings": {
                    "type": "boolean",
                    "description": "True if there are learnings worth extracting for skill creation",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Confidence in the assessment",
                },
            },
            "required": ["quality_score", "quality_reason", "has_learnings", "confidence"],
            "additionalProperties": False,
        },
    },
}


def parse_quality_assessment(response: dict[str, Any]) -> QualityAssessment:
    """Parse LLM response dict into QualityAssessment."""
    score = int(response["quality_score"])
    if not 1 <= score <= 5:
        raise ValueError(f"quality_score must be 1-5, got {score}")

    confidence = str(response["confidence"])
    if confidence not in ("high", "medium", "low"):
        raise ValueError(f"confidence must be high/medium/low, got {confidence}")

    return QualityAssessment(
        quality_score=score,
        quality_reason=str(response["quality_reason"]),
        has_learnings=bool(response["has_learnings"]),
        confidence=confidence,
    )


def parse_quality_from_json(content: str) -> QualityAssessment:
    """Parse JSON string from LLM response into QualityAssessment."""
    data = json.loads(content)
    return parse_quality_assessment(data)


@dataclass(frozen=True)
class LearningItem:
    """A single learning extracted from a conversation turn."""

    category: str  # "user_preference" | "workflow_pattern" | "constraint"
    observation: str
    direction: str  # "create_skill" | "update_skill" | "deprecate_skill"
    evidence: str
    confidence: str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class LearningExtraction:
    """Phase 2 output: extracted learnings with directions for skill lifecycle."""

    learnings: list[LearningItem]


LEARNING_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "learning_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "learnings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ["user_preference", "workflow_pattern", "constraint"],
                                "description": "Type of learning",
                            },
                            "observation": {
                                "type": "string",
                                "description": "What was learned (raw text)",
                            },
                            "direction": {
                                "type": "string",
                                "enum": ["create_skill", "update_skill", "deprecate_skill"],
                                "description": "What should happen with this learning",
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Quote or reference from conversation supporting this learning",
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "Confidence in this learning",
                            },
                        },
                        "required": ["category", "observation", "direction", "evidence", "confidence"],
                        "additionalProperties": False,
                    },
                    "description": "List of learnings extracted from the conversation",
                },
            },
            "required": ["learnings"],
            "additionalProperties": False,
        },
    },
}


def parse_learning_item(item: dict[str, Any]) -> LearningItem:
    """Parse a single learning item dict into LearningItem."""
    category = str(item["category"])
    if category not in ("user_preference", "workflow_pattern", "constraint"):
        raise ValueError(f"invalid category: {category}")

    direction = str(item["direction"])
    if direction not in ("create_skill", "update_skill", "deprecate_skill"):
        raise ValueError(f"invalid direction: {direction}")

    confidence = str(item["confidence"])
    if confidence not in ("high", "medium", "low"):
        raise ValueError(f"invalid confidence: {confidence}")

    return LearningItem(
        category=category,
        observation=str(item["observation"]),
        direction=direction,
        evidence=str(item["evidence"]),
        confidence=confidence,
    )


def parse_learning_extraction(response: dict[str, Any]) -> LearningExtraction:
    """Parse LLM response dict into LearningExtraction."""
    raw_learnings = response.get("learnings", [])
    learnings = [parse_learning_item(item) for item in raw_learnings]
    return LearningExtraction(learnings=learnings)


def parse_learning_from_json(content: str) -> LearningExtraction:
    """Parse JSON string from LLM response into LearningExtraction."""
    data = json.loads(content)
    return parse_learning_extraction(data)


@dataclass(frozen=True)
class SkillOperation:
    """Phase 3 output: a skill lifecycle operation to create, update, deprecate, or skip."""

    action: str  # "create" | "update" | "deprecate" | "skip"
    name: str
    description: str
    instructions: str
    trigger_mode: str  # "always" | "pattern" | "intelligent"
    source_confidence: str  # "high" | "medium" | "low"
    reason: str
    tools_allowlist: list[str] | None = None


@dataclass
class EvaluationResult:
    """Complete evaluation result with quality assessment and skill decisions."""

    quality: QualityAssessment
    decisions: list[SkillOperation] = field(default_factory=list)


SKILL_LIFECYCLE_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "skill_lifecycle",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["create", "update", "deprecate", "skip"],
                                "description": "Whether to create, update, deprecate, or skip",
                            },
                            "name": {
                                "type": "string",
                                "description": "Skill name",
                            },
                            "description": {
                                "type": "string",
                                "description": "Skill description",
                            },
                            "instructions": {
                                "type": "string",
                                "description": "Skill instructions",
                            },
                            "trigger_mode": {
                                "type": "string",
                                "enum": ["always", "pattern", "intelligent"],
                                "description": "How this skill should be triggered",
                            },
                            "tools_allowlist": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Tool name patterns this skill should make available "
                                    "(fnmatch wildcards, e.g. ['web__*', 'playwright__*']). "
                                    "Null or empty means core tools only."
                                ),
                            },
                            "source_confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "Confidence in the source material",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Reason for this operation",
                            },
                        },
                        "required": [
                            "action",
                            "name",
                            "description",
                            "instructions",
                            "trigger_mode",
                            "tools_allowlist",
                            "source_confidence",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                    "description": "List of skill lifecycle operations",
                },
            },
            "required": ["operations"],
            "additionalProperties": False,
        },
    },
}


def parse_skill_operation(item: dict[str, Any]) -> SkillOperation:
    """Parse a single skill operation dict into SkillOperation."""
    action = str(item["action"])
    if action not in ("create", "update", "deprecate", "skip"):
        raise ValueError(f"invalid action: {action}")

    trigger_mode = str(item["trigger_mode"])
    if trigger_mode not in ("always", "pattern", "intelligent"):
        raise ValueError(f"invalid trigger_mode: {trigger_mode}")

    source_confidence = str(item["source_confidence"])
    if source_confidence not in ("high", "medium", "low"):
        raise ValueError(f"invalid source_confidence: {source_confidence}")

    tools_allowlist = item.get("tools_allowlist")
    if tools_allowlist is not None and not isinstance(tools_allowlist, list):
        tools_allowlist = [str(tools_allowlist)]

    return SkillOperation(
        action=action,
        name=str(item["name"]),
        description=str(item["description"]),
        instructions=str(item["instructions"]),
        trigger_mode=trigger_mode,
        tools_allowlist=tools_allowlist,
        source_confidence=source_confidence,
        reason=str(item["reason"]),
    )


def parse_lifecycle_from_json(content: str) -> list[SkillOperation]:
    """Parse JSON string from LLM response into list of SkillOperation."""
    stripped = content.strip()
    if not stripped:
        return []
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
        if not stripped:
            return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw_operations = data.get("operations", [])
    return [parse_skill_operation(item) for item in raw_operations]
