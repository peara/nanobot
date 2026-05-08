from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from nanobot.browser.execution_budget import ExecutionBudget
from nanobot.browser.safe_element import SafeElement
from nanobot.browser.selector_resolver import SelectorResolver


class SafeBrowser:
    def __init__(
        self,
        interactor: Any,
        resolver: SelectorResolver,
        budget: ExecutionBudget,
        run_coro: Callable[[Any], Any],
        trace: Callable[[dict[str, Any]], None],
    ) -> None:
        self._interactor = interactor
        self._resolver = resolver
        self._budget = budget
        self._run_coro = run_coro
        self._trace = trace

    def goto(self, url: str) -> None:
        self._budget.consume_navigation()
        self._run_coro(self._interactor.open(url))
        self._trace({"action": "goto", "url": self.current_url(), "status": "success"})

    def find(self, key: str) -> SafeElement | None:
        page = self._interactor.page
        if page is None:
            return None
        locator = self._run_coro(self._resolver.find(page, key, self.current_url()))
        if locator is None:
            return None
        return SafeElement(locator, self._resolver, self._budget, self._run_coro, self._trace, self.current_url)

    def find_all(self, key: str) -> list[SafeElement]:
        page = self._interactor.page
        if page is None:
            return []
        locators = self._run_coro(self._resolver.find_all(page, key, self.current_url()))
        return [
            SafeElement(locator, self._resolver, self._budget, self._run_coro, self._trace, self.current_url)
            for locator in locators
        ]

    def wait_for_load(self) -> None:
        page = self._interactor.page
        if page is None:
            return
        self._budget.check_time()
        self._run_coro(page.wait_for_load_state("domcontentloaded", timeout=10000))
        self._trace({"action": "wait_for_load", "url": self.current_url(), "status": "success"})

    def wait_for(self, key: str, timeout_ms: int = 5000) -> bool:
        page = self._interactor.page
        if page is None:
            return False
        self._budget.check_time()
        selectors = self._resolver.selector_manifest.get(key, [])
        for selector in selectors:
            self._budget.consume_dom_query()
            try:
                self._run_coro(page.wait_for_selector(selector, timeout=timeout_ms))
                self._trace(
                    {
                        "action": "wait_for",
                        "selector_key": key,
                        "selector_used": selector,
                        "url": self.current_url(),
                        "status": "success",
                    }
                )
                return True
            except Exception as exc:  # pylint: disable=broad-except
                self._trace(
                    {
                        "action": "selector_fallback_attempt",
                        "selector_key": key,
                        "selector_used": selector,
                        "url": self.current_url(),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        self._trace(
            {
                "action": "wait_for",
                "selector_key": key,
                "url": self.current_url(),
                "status": "failed",
                "error": "timeout",
            }
        )
        return False

    def loop_guard(self, name: str, max_iterations: int = 20) -> bool:
        try:
            return self._budget.loop_guard(name, max_iterations=max_iterations)
        except Exception as exc:  # pylint: disable=broad-except
            self._trace(
                {
                    "action": "loop_guard",
                    "url": self.current_url(),
                    "status": "failed",
                    "error": str(exc),
                }
            )
            raise

    def current_url(self) -> str:
        page = self._interactor.page
        if page is None:
            return ""
        return str(page.url)

    def snapshot(self) -> dict[str, Any]:
        self._budget.check_time()
        snap = self._run_coro(self._interactor.snapshot())
        self._trace({"action": "snapshot", "url": self.current_url(), "status": "success"})
        if hasattr(snap, "__dataclass_fields__"):
            return asdict(snap)
        if isinstance(snap, dict):
            return snap
        return {"snapshot": str(snap)}
