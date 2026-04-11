from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FetchResult:
    strategy: str
    url: str
    final_url: str
    status_code: int | None
    html: str
    title: str
    used_browser: bool
    weak_content: bool
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExtractionResult:
    phase: str
    strategy: str
    page_type: str
    content: str
    markdown: str
    items: list[dict[str, str]]
    links: list[dict[str, str]]
    visible_text: str
    quality_score: float
    decision: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SnapshotResult:
    url: str
    title: str
    visible_text: str
    buttons: list[dict[str, str]]
    links: list[dict[str, str]]
    inputs: list[dict[str, str]]
    candidate_actions: list[str]


@dataclass(slots=True)
class FlowState:
    fetch: FetchResult
    page_type: str
    steps: list[dict[str, Any]]
    best_result: ExtractionResult | None
    fallback_used: bool
