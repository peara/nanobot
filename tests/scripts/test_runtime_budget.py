from __future__ import annotations

import time

import pytest

from nanobot.browser.execution_budget import BudgetExceededError, ExecutionBudget
from nanobot.scripts.models import ErrorType, RuntimeBudgetLimits


def test_dom_query_budget_exceeded() -> None:
    budget = ExecutionBudget(RuntimeBudgetLimits(max_dom_queries=1))
    budget.consume_dom_query()
    with pytest.raises(BudgetExceededError) as exc:
        budget.consume_dom_query()
    assert exc.value.error_type == ErrorType.DOM_QUERY_BUDGET_EXCEEDED


def test_click_budget_exceeded() -> None:
    budget = ExecutionBudget(RuntimeBudgetLimits(max_clicks=1))
    budget.consume_click()
    with pytest.raises(BudgetExceededError) as exc:
        budget.consume_click()
    assert exc.value.error_type == ErrorType.CLICK_BUDGET_EXCEEDED


def test_loop_guard_exceeded() -> None:
    budget = ExecutionBudget(RuntimeBudgetLimits(max_loop_iterations=2))
    budget.loop_guard("pagination", max_iterations=10)
    budget.loop_guard("pagination", max_iterations=10)
    with pytest.raises(BudgetExceededError) as exc:
        budget.loop_guard("pagination", max_iterations=10)
    assert exc.value.error_type == ErrorType.LOOP_GUARD_EXCEEDED


def test_timeout_exceeded() -> None:
    budget = ExecutionBudget(RuntimeBudgetLimits(max_execution_time_ms=1))
    time.sleep(0.01)
    with pytest.raises(BudgetExceededError) as exc:
        budget.check_time()
    assert exc.value.error_type == ErrorType.TIMEOUT
