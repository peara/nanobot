from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class PromptVariableError(Exception):
    """Raised when required template variable is missing."""


VARIABLE_PATTERN = re.compile(r"\{(\w+)\}")


def extract_variables(content: str) -> list[str]:
    """Extract all {variable} placeholders from template content."""
    return sorted(set(VARIABLE_PATTERN.findall(content)))


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


@dataclass
class Prompt:
    id: int
    name: str
    content: str
    role: str
    variables: list[str] = field(default_factory=list)
    is_active: bool = True
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "role": self.role,
            "variables": self.variables,
            "is_active": self.is_active,
            "version": self.version,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }

    @classmethod
    def from_row(cls, row: tuple) -> Prompt:
        """Create Prompt from database row."""
        variables: list[str] = []
        variables_json = row[4]
        if variables_json:
            try:
                parsed = json.loads(variables_json)
                if isinstance(parsed, list):
                    variables = [str(v) for v in parsed]
            except json.JSONDecodeError:
                pass

        return cls(
            id=int(row[0]),
            name=str(row[1]),
            content=str(row[2]),
            role=str(row[3]),
            variables=variables,
            is_active=bool(row[5]),
            version=int(row[6]),
            created_at=parse_utc(row[7]) or utc_now(),
            updated_at=parse_utc(row[8]) or utc_now(),
        )
