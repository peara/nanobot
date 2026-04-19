from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright

from ..config import BLOCKED_ACTION_PATTERNS, EXPANSION_LABELS, MAX_VISIBLE_TEXT_CHARS
from ..models import SnapshotResult
from ..utils import is_selector_target, normalize_text_block, normalize_whitespace

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Playwright


class SafeActionError(RuntimeError):
    pass


class BrowserUnavailableError(RuntimeError):
    pass


class BrowserInteractor:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> BrowserInteractor:
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
        except PlaywrightError as exc:
            await self._playwright.stop()
            self._playwright = None
            raise BrowserUnavailableError(str(exc)) from exc
        self._context = await self._browser.new_context()
        self.page = await self._context.new_page()
        self.page.set_default_timeout(12000)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def open(self, url: str) -> None:
        assert self.page is not None
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await self.dismiss_overlays()

    async def dismiss_overlays(self) -> None:
        assert self.page is not None
        # Common overlay dismiss labels (English + Japanese)
        for label in ("Accept", "I agree", "Close", "Dismiss", "Got it", "あとで", "閉じる", "同意する", "×"):
            locator = self.page.get_by_role("button", name=re.compile(re.escape(label), re.I))
            try:
                if await locator.count():
                    await locator.first.click(timeout=1500)
            except PlaywrightError:
                continue

    async def wait_for(self, selector: str | None = None, text: str | None = None, network_idle: bool = False) -> None:
        assert self.page is not None
        if selector:
            await self.page.wait_for_selector(selector, timeout=12000)
            return
        if text:
            await self.page.get_by_text(text, exact=False).first.wait_for(timeout=12000)
            return
        if network_idle:
            await self.page.wait_for_load_state("networkidle", timeout=12000)
            return
        await self.page.wait_for_timeout(1000)

    async def resolve_target(self, target: str):
        assert self.page is not None
        target = target.strip()
        locators = []
        if is_selector_target(target):
            locators.append(self.page.locator(target))
        escaped = re.escape(target)
        locators.extend(
            [
                self.page.get_by_role("button", name=re.compile(escaped, re.I)),
                self.page.get_by_role("link", name=re.compile(escaped, re.I)),
                self.page.get_by_label(re.compile(escaped, re.I)),
                self.page.get_by_placeholder(re.compile(escaped, re.I)),
                self.page.get_by_text(re.compile(escaped, re.I)),
                self.page.locator(f"[aria-label*='{target}' i]"),
                self.page.locator(f"[placeholder*='{target}' i]"),
                self.page.locator(f"text={target}"),
            ]
        )
        for locator in locators:
            try:
                count = await locator.count()
            except PlaywrightError:
                continue
            if count:
                return locator.first
        raise LookupError(f"Could not resolve target: {target}")

    async def click(self, target: str) -> str:
        locator = await self.resolve_target(target)
        await self.ensure_safe(locator, target)
        await locator.click()
        return f"click:{target}"

    async def type(self, target: str, text: str) -> str:
        locator = await self.resolve_target(target)
        await locator.fill(text)
        return f"type:{target}"

    async def select(self, target: str, value: str) -> str:
        locator = await self.resolve_target(target)
        await self.ensure_safe(locator, target)
        await locator.select_option(value=value)
        return f"select:{target}"

    async def scroll(self, amount: int | None = None, until_text: str | None = None) -> str:
        assert self.page is not None
        if until_text:
            for _ in range(8):
                if await self.page.get_by_text(until_text, exact=False).count():
                    break
                await self.page.mouse.wheel(0, 900)
                await self.page.wait_for_timeout(400)
            return f"scroll:until_text:{until_text}"
        delta = amount or 1000
        await self.page.mouse.wheel(0, delta)
        await self.page.wait_for_timeout(350)
        return f"scroll:{delta}"

    async def extract_html(self) -> str:
        assert self.page is not None
        return await self.page.content()

    async def snapshot(self) -> SnapshotResult:
        assert self.page is not None
        title = await self.page.title()
        visible_text = normalize_text_block(await self.page.locator("body").inner_text())[:MAX_VISIBLE_TEXT_CHARS]
        buttons = await self._collect_buttons()
        links = await self._collect_links()
        inputs = await self._collect_inputs()
        candidate_actions = self._candidate_actions(buttons, links)
        return SnapshotResult(
            url=self.page.url,
            title=title,
            visible_text=visible_text,
            buttons=buttons,
            links=links,
            inputs=inputs,
            candidate_actions=candidate_actions,
        )

    async def screenshot(self, output_path: Path) -> str:
        assert self.page is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(output_path), full_page=True)
        return str(output_path)

    async def maybe_expand_content(self) -> list[str]:
        assert self.page is not None
        actions: list[str] = []
        for label in EXPANSION_LABELS:
            try:
                locator = await self.resolve_target(label)
                await self.ensure_safe(locator, label)
                await locator.click(timeout=1500)
                await self.page.wait_for_timeout(500)
                actions.append(f"click:{label}")
            except (LookupError, PlaywrightError, SafeActionError):
                continue
        return actions

    async def ensure_safe(self, locator, target: str) -> None:
        text = normalize_whitespace((await locator.inner_text()) if hasattr(locator, "inner_text") else target).lower()
        target_text = normalize_whitespace(target).lower()
        combined = f"{text} {target_text}".strip()
        if any(pattern in combined for pattern in BLOCKED_ACTION_PATTERNS):
            raise SafeActionError(f"Blocked unsafe action: {target}")

    async def _collect_buttons(self) -> list[dict[str, str]]:
        assert self.page is not None
        return await self.page.locator(
            "button, [role='button'], input[type='button'], input[type='submit']"
        ).evaluate_all(
            """
            elements => elements.slice(0, 30).map((el) => ({
              text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(),
              type: el.getAttribute('type') || '',
              aria_label: el.getAttribute('aria-label') || ''
            })).filter((item) => item.text || item.aria_label)
            """
        )

    async def _collect_links(self) -> list[dict[str, str]]:
        assert self.page is not None
        return await self.page.locator("a[href]").evaluate_all(
            """
            elements => elements.slice(0, 50).map((el) => ({
              text: (el.innerText || el.textContent || '').trim(),
              href: el.href || ''
            })).filter((item) => item.href)
            """
        )

    async def _collect_inputs(self) -> list[dict[str, str]]:
        assert self.page is not None
        return await self.page.locator("input, textarea, select").evaluate_all(
            """
            elements => elements.slice(0, 20).map((el) => ({
              name: el.getAttribute('name') || '',
              type: el.getAttribute('type') || el.tagName.toLowerCase(),
              placeholder: el.getAttribute('placeholder') || '',
              label: el.getAttribute('aria-label') || ''
            }))
            """
        )

    def _candidate_actions(self, buttons: list[dict[str, str]], links: list[dict[str, str]]) -> list[str]:
        actions: list[str] = []
        for entry in buttons:
            label = normalize_whitespace(entry.get("text") or entry.get("aria_label") or "")
            if not label:
                continue
            lowered = label.lower()
            if any(pattern in lowered for pattern in BLOCKED_ACTION_PATTERNS):
                continue
            if any(hint in lowered for hint in EXPANSION_LABELS + ("login", "search", "filter", "sort")):
                actions.append(f"click:{label}")
        for entry in links[:10]:
            text = normalize_whitespace(entry.get("text", ""))
            if text and any(hint in text.lower() for hint in ("next", "more", "continue")):
                actions.append(f"click:{text}")
        return list(dict.fromkeys(actions))[:15]
