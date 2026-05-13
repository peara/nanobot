from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


@dataclass
class WebScript:
    id: int
    name: str
    description: str
    code: str
    params_schema: dict[str, Any] = field(default_factory=dict)
    result_schema: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    is_active: bool = True
    vector_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def as_dict(self, *, include_code: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "params_schema": self.params_schema,
            "result_schema": self.result_schema,
            "tags": self.tags,
            "is_active": self.is_active,
            "vector_id": self.vector_id,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }
        if include_code:
            result["code"] = self.code
        return result

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "WebScript":
        return cls(
            id=int(row[0]),
            name=str(row[1]),
            description=str(row[2]),
            code=str(row[3]),
            params_schema=_json_dict(row[4]),
            result_schema=_json_dict(row[5]),
            tags=_json_list(row[6]),
            is_active=bool(row[7]),
            vector_id=str(row[8]) if row[8] else None,
            created_at=parse_utc(row[9]) or utc_now(),
            updated_at=parse_utc(row[10]) or utc_now(),
        )


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return parsed
    return {}


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []
