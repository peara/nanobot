from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc(dt: str | None) -> datetime | None:
    if not dt:
        return None
    return datetime.fromisoformat(dt).astimezone(timezone.utc)


VALID_TRIGGER_MODES = {"always", "pattern", "intelligent"}


@dataclass
class Skill:
    """Represents a user-defined skill that can be activated based on trigger conditions.

    Skills inject domain-specific instructions into agent context, distinct from plans
    which are task-specific execution recipes. Skills are expertise/knowledge ("how to think"),
    while plans are task decomposition ("what to do").

    Attributes:
        id: Unique identifier (auto-incremented from SQLite)
        name: Unique skill name identifier (e.g., "debug", "web-search")
        description: Brief description (~100 tokens) of when to use this skill
        instructions: Full skill content (~1000-5000 tokens) with detailed instructions
        trigger_mode: How this skill is activated - "always", "pattern", or "intelligent"
        trigger_patterns: Regex patterns for "pattern" mode matching
        tools_allowlist: Optional list of allowed tool names for this skill
        priority: Higher priority skills are included first (for conflict resolution)
        is_active: Whether this skill is currently active
        created_at: Timestamp of skill creation
        updated_at: Timestamp of last update
        hit_count: Number of times this skill was matched and injected
        last_hit_at: Timestamp of the last time this skill was matched
    """

    id: int
    name: str
    description: str
    instructions: str
    trigger_mode: str = "pattern"
    trigger_patterns: list[str] = field(default_factory=list)
    tools_allowlist: list[str] | None = None
    priority: int = 0
    is_active: bool = True
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    hit_count: int = 0
    last_hit_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.trigger_mode not in VALID_TRIGGER_MODES:
            raise ValueError(f"Invalid trigger_mode '{self.trigger_mode}'. Must be one of: {VALID_TRIGGER_MODES}")

    def as_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "trigger_mode": self.trigger_mode,
            "trigger_patterns": self.trigger_patterns,
            "tools_allowlist": self.tools_allowlist,
            "priority": self.priority,
            "is_active": self.is_active,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "hit_count": self.hit_count,
            "last_hit_at": iso(self.last_hit_at),
        }
        return result

    @classmethod
    def from_row(cls, row: tuple) -> "Skill":
        def parse_json_list(value: str | None) -> list[str]:
            if not value:
                return []
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except json.JSONDecodeError:
                pass
            return []

        def parse_json_list_or_none(value: str | None) -> list[str] | None:
            if not value:
                return None
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except json.JSONDecodeError:
                pass
            return None

        return cls(
            id=int(row[0]),
            name=str(row[1]),
            description=str(row[2]),
            instructions=str(row[3]),
            trigger_mode=str(row[4] or "pattern"),
            trigger_patterns=parse_json_list(row[5]),
            tools_allowlist=parse_json_list_or_none(row[6]),
            priority=int(row[7] or 0),
            is_active=bool(row[8]),
            created_at=parse_utc(row[9]) or utc_now(),
            updated_at=parse_utc(row[10]) or utc_now(),
            hit_count=int(row[11] or 0),
            last_hit_at=parse_utc(row[12]),
        )

    def matches_pattern(self, text: str) -> bool:
        if self.trigger_mode != "pattern" or not self.trigger_patterns:
            return False

        for pattern in self.trigger_patterns:
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    return True
            except re.error:
                # Invalid regex pattern, skip
                continue
        return False
