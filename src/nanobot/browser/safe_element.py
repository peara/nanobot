from __future__ import annotations

from typing import Any, Callable

from nanobot.browser.execution_budget import ExecutionBudget
from nanobot.browser.selector_resolver import SelectorResolver


class SafeElement:
    def __init__(
        self,
        locator: Any,
        resolver: SelectorResolver,
        budget: ExecutionBudget,
        run_coro: Callable[[Any], Any],
        trace: Callable[[dict[str, Any]], None],
        current_url: Callable[[], str],
    ) -> None:
        self._locator = locator
        self._resolver = resolver
        self._budget = budget
        self._run_coro = run_coro
        self._trace = trace
        self._current_url = current_url

    def text(self) -> str:
        self._budget.check_time()
        return str(self._run_coro(self._locator.inner_text())).strip()

    def attr(self, name: str) -> str | None:
        self._budget.check_time()
        value = self._run_coro(self._locator.get_attribute(name))
        return str(value) if value is not None else None

    def click(self) -> None:
        self._budget.consume_click()
        self._run_coro(self._locator.click())
        self._trace({"action": "click", "url": self._current_url(), "status": "success"})

    def visible(self) -> bool:
        self._budget.check_time()
        return bool(self._run_coro(self._locator.is_visible()))

    def find(self, key: str) -> SafeElement | None:
        locator = self._run_coro(self._resolver.find(self._locator, key, self._current_url()))
        if locator is None:
            return None
        return SafeElement(locator, self._resolver, self._budget, self._run_coro, self._trace, self._current_url)

    def find_all(self, key: str) -> list[SafeElement]:
        locators = self._run_coro(self._resolver.find_all(self._locator, key, self._current_url()))
        return [
            SafeElement(locator, self._resolver, self._budget, self._run_coro, self._trace, self._current_url)
            for locator in locators
        ]
