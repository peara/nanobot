from __future__ import annotations

from dataclasses import asdict, dataclass, field
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


@dataclass
class PlanBrief:
    goal: str
    constraints: list[str] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanBrief":
        return cls(
            goal=str(data.get("goal", "")),
            constraints=list(data.get("constraints", [])),
            required_inputs=list(data.get("required_inputs", [])),
            risk_flags=list(data.get("risk_flags", [])),
            notes=str(data.get("notes", "")),
        )


@dataclass
class Plan:
    id: int
    name: str
    goal: str
    constraints: list[str] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] | None = None
    notes: str = ""
    source_type: str = ""
    source_scope: str = ""
    version: int = 1
    success_count: int = 0
    failure_count: int = 0
    last_run_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "constraints": self.constraints,
            "required_inputs": self.required_inputs,
            "risk_flags": self.risk_flags,
            "steps": self.steps,
            "notes": self.notes,
            "source_type": self.source_type,
            "source_scope": self.source_scope,
            "version": self.version,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_run_at": iso(self.last_run_at),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }
        return result

    @classmethod
    def from_row(cls, row: tuple) -> "Plan":
        """Create Plan from database row."""
        import json

        def parse_json_list(value: str | None) -> list[Any]:
            if not value:
                return []
            try:
                return list(json.loads(value))
            except json.JSONDecodeError:
                return []

        def parse_json_dict_list(value: str | None) -> list[dict[str, Any]] | None:
            if not value:
                return None
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
            return None

        return cls(
            id=int(row[0]),
            name=str(row[1]),
            goal=str(row[2]),
            constraints=parse_json_list(row[3]),
            required_inputs=parse_json_list(row[4]),
            risk_flags=parse_json_list(row[5]),
            steps=parse_json_dict_list(row[6]),
            notes=str(row[7] or ""),
            source_type=str(row[8] or ""),
            source_scope=str(row[9] or ""),
            version=int(row[10] or 1),
            success_count=int(row[11] or 0),
            failure_count=int(row[12] or 0),
            last_run_at=parse_utc(row[13]),
            created_at=parse_utc(row[14]) or utc_now(),
            updated_at=parse_utc(row[15]) or utc_now(),
        )
