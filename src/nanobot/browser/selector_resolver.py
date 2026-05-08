from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

from nanobot.browser.execution_budget import ExecutionBudget

TraceFn = Callable[[dict[str, Any]], None]
SelectorStatFn = Callable[[str, str, bool], None]


class SelectorResolver:
    def __init__(
        self,
        selector_manifest: dict[str, list[str]],
        budget: ExecutionBudget,
        trace: TraceFn,
        selector_stats: SelectorStatFn,
    ) -> None:
        self.selector_manifest = selector_manifest
        self.budget = budget
        self.trace = trace
        self.selector_stats = selector_stats

    async def find(self, context: Page | Locator, selector_key: str, url: str | None) -> Locator | None:
        selectors = self.selector_manifest.get(selector_key, [])
        for index, selector in enumerate(selectors):
            self.budget.consume_dom_query()
            locator = context.locator(selector)
            try:
                count = await locator.count()
            except Exception as exc:  # pylint: disable=broad-except
                self.selector_stats(selector_key, selector, False)
                self.trace(
                    {
                        "action": "selector_fallback_attempt",
                        "selector_key": selector_key,
                        "selector_used": selector,
                        "url": url,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue
            if count > 0:
                self.selector_stats(selector_key, selector, True)
                self.trace(
                    {
                        "action": "find",
                        "selector_key": selector_key,
                        "selector_used": selector,
                        "url": url,
                        "status": "success",
                    }
                )
                if index > 0:
                    self.trace(
                        {
                            "action": "selector_fallback_attempt",
                            "selector_key": selector_key,
                            "selector_used": selector,
                            "url": url,
                            "status": "success",
                        }
                    )
                return locator.first
            self.selector_stats(selector_key, selector, False)
            self.trace(
                {
                    "action": "selector_fallback_attempt",
                    "selector_key": selector_key,
                    "selector_used": selector,
                    "url": url,
                    "status": "failed",
                }
            )

        self.trace(
            {
                "action": "find",
                "selector_key": selector_key,
                "url": url,
                "status": "failed",
                "error": "selector_not_found",
            }
        )
        return None

    async def find_all(self, context: Page | Locator, selector_key: str, url: str | None) -> list[Locator]:
        selectors = self.selector_manifest.get(selector_key, [])
        for index, selector in enumerate(selectors):
            self.budget.consume_dom_query()
            locator = context.locator(selector)
            try:
                count = await locator.count()
            except Exception as exc:  # pylint: disable=broad-except
                self.selector_stats(selector_key, selector, False)
                self.trace(
                    {
                        "action": "selector_fallback_attempt",
                        "selector_key": selector_key,
                        "selector_used": selector,
                        "url": url,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue
            if count > 0:
                self.selector_stats(selector_key, selector, True)
                self.trace(
                    {
                        "action": "find_all",
                        "selector_key": selector_key,
                        "selector_used": selector,
                        "url": url,
                        "status": "success",
                    }
                )
                if index > 0:
                    self.trace(
                        {
                            "action": "selector_fallback_attempt",
                            "selector_key": selector_key,
                            "selector_used": selector,
                            "url": url,
                            "status": "success",
                        }
                    )
                return [locator.nth(i) for i in range(count)]
            self.selector_stats(selector_key, selector, False)
            self.trace(
                {
                    "action": "selector_fallback_attempt",
                    "selector_key": selector_key,
                    "selector_used": selector,
                    "url": url,
                    "status": "failed",
                }
            )

        self.trace(
            {
                "action": "find_all",
                "selector_key": selector_key,
                "url": url,
                "status": "success",
            }
        )
        return []
