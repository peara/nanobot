from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ErrorType:
    AST_VALIDATION_ERROR = "AST_VALIDATION_ERROR"
    PARAMS_VALIDATION_ERROR = "PARAMS_VALIDATION_ERROR"
    OUTPUT_VALIDATION_ERROR = "OUTPUT_VALIDATION_ERROR"
    SCRIPT_RUNTIME_ERROR = "SCRIPT_RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    DOM_QUERY_BUDGET_EXCEEDED = "DOM_QUERY_BUDGET_EXCEEDED"
    CLICK_BUDGET_EXCEEDED = "CLICK_BUDGET_EXCEEDED"
    NAVIGATION_BUDGET_EXCEEDED = "NAVIGATION_BUDGET_EXCEEDED"
    LOOP_GUARD_EXCEEDED = "LOOP_GUARD_EXCEEDED"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    SCRIPT_NOT_FOUND = "SCRIPT_NOT_FOUND"
    VERSION_NOT_FOUND = "VERSION_NOT_FOUND"
    NO_RELIABLE_SCRIPT_FOUND = "NO_RELIABLE_SCRIPT_FOUND"
    REPAIR_FAILED = "REPAIR_FAILED"


@dataclass
class RuntimeBudgetLimits:
    max_execution_time_ms: int = 30000
    max_loop_iterations: int = 50
    max_dom_queries: int = 500
    max_page_navigation: int = 20
    max_clicks: int = 100
    max_output_items: int = 1000


@dataclass
class TraceStep:
    step_index: int
    action: str
    selector_key: str | None = None
    selector_used: str | None = None
    url: str | None = None
    status: str | None = None
    error: str | None = None
    snapshot_ref: str | None = None


@dataclass
class ExecutionMetrics:
    dom_query_count: int = 0
    page_count: int = 0
    click_count: int = 0
    output_item_count: int = 0


@dataclass
class ExecutionOutcome:
    status: str
    confidence: float
    result: dict[str, Any] | None
    error_type: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    traces: list[TraceStep] = field(default_factory=list)
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)


@dataclass
class SearchCandidate:
    script_id: str
    version_id: str
    score: float
    reason: str


@dataclass
class ScriptVersionRecord:
    script_id: str
    script_name: str
    description: str
    domain: str | None
    task_type: str | None
    version_id: str
    version: int
    code: str
    params_schema: dict[str, Any]
    output_schema: dict[str, Any]
    selector_manifest: dict[str, list[str]]
    validation_rules: list[dict[str, Any]]
    status: str
    current_version_id: str | None
