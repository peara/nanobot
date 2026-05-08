from __future__ import annotations

import time

from nanobot.scripts.models import ErrorType, RuntimeBudgetLimits


class BudgetExceededError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class ExecutionBudget:
    def __init__(self, limits: RuntimeBudgetLimits | None = None) -> None:
        self.limits = limits or RuntimeBudgetLimits()
        self.start_time = time.monotonic()
        self.dom_queries = 0
        self.page_navigations = 0
        self.clicks = 0
        self.loop_counts: dict[str, int] = {}

    def check_time(self) -> None:
        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
        if elapsed_ms > self.limits.max_execution_time_ms:
            raise BudgetExceededError(ErrorType.TIMEOUT, "Execution exceeded max_execution_time_ms")

    def consume_dom_query(self) -> None:
        self.check_time()
        self.dom_queries += 1
        if self.dom_queries > self.limits.max_dom_queries:
            raise BudgetExceededError(
                ErrorType.DOM_QUERY_BUDGET_EXCEEDED,
                "DOM query budget exceeded",
            )

    def consume_navigation(self) -> None:
        self.check_time()
        self.page_navigations += 1
        if self.page_navigations > self.limits.max_page_navigation:
            raise BudgetExceededError(
                ErrorType.NAVIGATION_BUDGET_EXCEEDED,
                "Navigation budget exceeded",
            )

    def consume_click(self) -> None:
        self.check_time()
        self.clicks += 1
        if self.clicks > self.limits.max_clicks:
            raise BudgetExceededError(
                ErrorType.CLICK_BUDGET_EXCEEDED,
                "Click budget exceeded",
            )

    def loop_guard(self, name: str, max_iterations: int = 20) -> bool:
        self.check_time()
        allowed = min(max_iterations, self.limits.max_loop_iterations)
        value = self.loop_counts.get(name, 0) + 1
        self.loop_counts[name] = value
        if value > allowed:
            raise BudgetExceededError(
                ErrorType.LOOP_GUARD_EXCEEDED,
                f"Loop '{name}' exceeded max iterations ({allowed})",
            )
        return True
