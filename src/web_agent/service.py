from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .cache import Cache
from .config import DEFAULT_QUALITY_THRESHOLD
from .dependencies import INTERACT_REQUIRED, READ_REQUIRED, SNAPSHOT_REQUIRED, missing_dependencies
from .models import FetchResult, FlowState
from .output_utils import build_output_stem, ensure_outputs_dir, save_json_output
from .utils import extract_title_from_html, word_count


class DomainChromeCache:
    """Per-domain cache of navigation chrome (repeated items/links).

    On first call to a domain, all items/links are stored as the chrome baseline.
    On subsequent calls, items/links matching the baseline are split out as "chrome"
    and excluded from the main payload (kept in debug fields for recovery).
    """

    def __init__(self, cache: Cache | None = None) -> None:
        self._cache = cache if cache is not None else Cache(max_entries=50)

    @staticmethod
    def domain_from_url(url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc.lower()

    def split_chrome(
        self, domain: str, items: list[dict], links: list[dict]
    ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        """Split items/links into (content_items, chrome_items, content_links, chrome_links).

        First call for a domain stores the baseline and returns everything as content.
        Subsequent calls split matching entries into chrome.
        """
        baseline = self._cache.get(domain)
        if baseline is None:
            self._cache.set(domain, {"items": list(items), "links": list(links)})
            return items, [], links, []

        baseline_item_keys = {(i.get("title", ""), i.get("link", "")) for i in baseline["items"]}
        baseline_link_keys = {(lk.get("text", ""), lk.get("href", "")) for lk in baseline["links"]}

        content_items: list[dict] = []
        chrome_items: list[dict] = []
        for item in items:
            key = (item.get("title", ""), item.get("link", ""))
            if key in baseline_item_keys:
                chrome_items.append(item)
            else:
                content_items.append(item)

        content_links: list[dict] = []
        chrome_links: list[dict] = []
        for link in links:
            key = (link.get("text", ""), link.get("href", ""))
            if key in baseline_link_keys:
                chrome_links.append(link)
            else:
                content_links.append(link)

        return content_items, chrome_items, content_links, chrome_links

    def get_baseline(self, domain: str) -> dict[str, list] | None:
        return self._cache.get(domain)

    def clear(self, domain: str | None = None) -> None:
        if domain is None:
            self._cache.clear()
        else:
            self._cache.delete(domain)

    @property
    def cached_domains(self) -> list[str]:
        return self._cache.keys()


class WebAgentTool:
    def __init__(
        self,
        quality_threshold: float = DEFAULT_QUALITY_THRESHOLD,
        headless: bool = True,
        chrome_cache: DomainChromeCache | None = None,
    ) -> None:
        self.quality_threshold = quality_threshold
        self.headless = headless
        self.chrome_cache = chrome_cache

    async def read(self, url: str) -> dict[str, Any]:
        if missing := missing_dependencies(READ_REQUIRED):
            return self._missing_dependency_payload("read", url, missing)
        flow = await self.run_read_flow(url)
        return self._final_payload(flow, actions_taken=[])

    async def snapshot(self, url: str) -> dict[str, Any]:
        if missing := missing_dependencies(SNAPSHOT_REQUIRED):
            return self._missing_dependency_payload("snapshot", url, missing)
        from .browser import BrowserInteractor, BrowserUnavailableError

        try:
            async with BrowserInteractor(headless=self.headless) as browser:
                await browser.open(url)
                snapshot = await browser.snapshot()
            return asdict(snapshot)
        except BrowserUnavailableError as exc:
            return {
                "url": url,
                "title": "",
                "visible_text": "",
                "buttons": [],
                "links": [],
                "inputs": [],
                "candidate_actions": [],
                "error": f"browser_unavailable: {exc}",
            }

    async def interact(self, url: str, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if missing := missing_dependencies(INTERACT_REQUIRED):
            return self._missing_dependency_payload("interact", url, missing)
        from .browser import BrowserInteractor, BrowserUnavailableError

        actions_taken: list[str] = []
        snapshots: list[dict[str, Any]] = []
        outputs_dir = ensure_outputs_dir()
        stem = build_output_stem(url)
        screenshot_path = outputs_dir / f"{stem}.png"
        background_tabs: list[dict[str, str]] = []
        try:
            async with BrowserInteractor(headless=self.headless) as browser:
                await browser.open(url)
                snapshots.append(asdict(await browser.snapshot()))
                for step in steps or []:
                    action_name = step.get("action")
                    if action_name == "click":
                        actions_taken.append(await browser.click(step["target"]))
                    elif action_name == "type":
                        actions_taken.append(await browser.type(step["target"], step["text"]))
                    elif action_name == "select":
                        actions_taken.append(await browser.select(step["target"], step["value"]))
                    elif action_name == "scroll":
                        actions_taken.append(await browser.scroll(step.get("amount"), step.get("until_text")))
                    elif action_name == "wait_for":
                        await browser.wait_for(
                            selector=step.get("selector"),
                            text=step.get("text"),
                            network_idle=bool(step.get("network_idle")),
                        )
                        actions_taken.append("wait_for")
                    elif action_name == "switch_tab":
                        actions_taken.append(await browser.switch_tab(step["index"]))
                    elif action_name == "snapshot":
                        actions_taken.append("snapshot")
                    elif action_name == "screenshot":
                        await browser.screenshot(screenshot_path)
                        actions_taken.append("screenshot")
                    else:
                        raise ValueError(f"Unsupported action: {action_name}")
                    snapshots.append(asdict(await browser.snapshot()))
                expansion_actions = await browser.maybe_expand_content()
                actions_taken.extend(expansion_actions)
                if expansion_actions:
                    snapshots.append(asdict(await browser.snapshot()))
                html = await browser.extract_html()
                final_url = browser.page.url if browser.page else url
                title = await browser.page.title() if browser.page else url
                background_tabs = browser.background_tabs
                await browser.screenshot(screenshot_path)
        except BrowserUnavailableError as exc:
            return {
                "url": url,
                "page_type": "unknown",
                "title": "",
                "content": "",
                "links": [],
                "items": [],
                "actions_taken": actions_taken,
                "quality_score": 0.0,
                "strategy": "playwright",
                "fallback_used": True,
                "screenshot_path": None,
                "background_tabs": [],
                "error": f"browser_unavailable: {exc}",
            }
        flow = await self.run_read_flow(final_url, starting_html=html, starting_title=title, force_browser_fetch=False)
        # Build compact step summary and final interactive elements from snapshots
        # instead of sending full interaction_snapshots (which bloats the LLM context)
        final_snapshot = snapshots[-1] if snapshots else {}
        compact_steps: list[dict[str, str]] = []
        for snap in snapshots:
            compact_steps.append({"url": snap.get("url", ""), "title": snap.get("title", "")})
        payload = self._final_payload(flow, actions_taken=actions_taken)
        payload["step_urls"] = compact_steps
        payload["buttons"] = final_snapshot.get("buttons", [])
        payload["inputs"] = final_snapshot.get("inputs", [])
        payload["candidate_actions"] = final_snapshot.get("candidate_actions", [])
        payload["background_tabs"] = background_tabs
        payload["screenshot_path"] = str(screenshot_path)
        return payload

    async def run_read_flow(
        self,
        url: str,
        *,
        starting_html: str | None = None,
        starting_title: str | None = None,
        force_browser_fetch: bool = False,
    ) -> FlowState:
        steps: list[dict[str, Any]] = []
        if starting_html is not None:
            fetch = FetchResult(
                strategy="browser_seed",
                url=url,
                final_url=url,
                status_code=200,
                html=starting_html,
                title=starting_title or extract_title_from_html(starting_html),
                used_browser=True,
                weak_content=False,
                errors=[],
            )
        else:
            from .fetchers import fetch_http, should_escalate_fetch

            fetch = await fetch_http(url)

        fetch.weak_content = False if starting_html is not None else should_escalate_fetch(fetch)
        steps.append(
            {
                "phase": "fetch",
                "strategy": fetch.strategy,
                "page_type": None,
                "quality_score": 0.0,
                "decision": "retry_or_escalate" if fetch.weak_content or force_browser_fetch else "continue",
                "details": {
                    "status_code": fetch.status_code,
                    "used_browser": fetch.used_browser,
                    "errors": fetch.errors,
                },
            }
        )

        from .classifier import classify_page

        page_type = classify_page(fetch.final_url, fetch.html)
        steps.append(
            {
                "phase": "classify",
                "strategy": "heuristic_classifier",
                "page_type": page_type,
                "quality_score": 0.0,
                "decision": "continue",
                "details": {},
            }
        )

        best_result = await self.run_extraction_cycle(url, fetch.html, fetch.final_url, page_type, steps)
        fallback_used = bool(starting_html is not None and fetch.used_browser)

        if force_browser_fetch or fetch.weak_content or not self.is_acceptable(best_result):
            fallback_used = True
            from .fetchers import fetch_browser, should_escalate_fetch

            browser_fetch = await fetch_browser(url, headless=self.headless)
            browser_fetch.weak_content = should_escalate_fetch(browser_fetch)
            steps.append(
                {
                    "phase": "retry_or_escalate",
                    "strategy": browser_fetch.strategy,
                    "page_type": None,
                    "quality_score": 0.0,
                    "decision": "continue",
                    "details": {
                        "status_code": browser_fetch.status_code,
                        "used_browser": browser_fetch.used_browser,
                        "errors": browser_fetch.errors,
                    },
                }
            )
            browser_page_type = classify_page(browser_fetch.final_url, browser_fetch.html)
            steps.append(
                {
                    "phase": "classify",
                    "strategy": "heuristic_classifier",
                    "page_type": browser_page_type,
                    "quality_score": 0.0,
                    "decision": "continue",
                    "details": {"source": "browser"},
                }
            )
            browser_best = await self.run_extraction_cycle(
                url, browser_fetch.html, browser_fetch.final_url, browser_page_type, steps
            )
            best_result = max(
                filter(None, [best_result, browser_best]), key=lambda item: item.quality_score, default=None
            )
            if browser_best and (best_result is browser_best):
                fetch = browser_fetch

        steps.append(
            {
                "phase": "finalize",
                "strategy": best_result.strategy if best_result else "best_effort",
                "page_type": best_result.page_type if best_result else page_type,
                "quality_score": best_result.quality_score if best_result else 0.0,
                "decision": "accept" if self.is_acceptable(best_result) else "best_effort",
                "details": {},
            }
        )
        return FlowState(
            fetch=fetch,
            page_type=page_type,
            steps=steps,
            best_result=best_result,
            fallback_used=fallback_used,
        )

    async def run_extraction_cycle(
        self,
        source_url: str,
        html: str,
        final_url: str,
        initial_page_type: str,
        steps: list[dict[str, Any]],
    ):
        from .classifier import page_type_order
        from .cleaners import clean_html_fragment
        from .extractors import crawl4ai_extract, extract_links, run_extraction_strategy

        best = None
        crawl4ai_result: tuple[str, str] | None = None
        links = extract_links(html, final_url)
        visible_text = clean_html_fragment(html)
        for page_type in page_type_order(initial_page_type):
            strategies = ["selector", "heuristic", "readability", "metadata"]
            if page_type == "listing":
                strategies = ["listing", "selector", "heuristic", "readability", "metadata"]
            strategies.append("crawl4ai")
            for strategy in strategies:
                if strategy == "crawl4ai" and crawl4ai_result is None:
                    crawl4ai_result = await crawl4ai_extract(source_url)
                result = await run_extraction_strategy(
                    strategy=strategy,
                    html=html,
                    source_url=source_url,
                    final_url=final_url,
                    page_type=page_type,
                    quality_threshold=self.quality_threshold,
                    links=links,
                    visible_text=visible_text,
                    crawl4ai_result=crawl4ai_result,
                )
                if result is None:
                    continue
                steps.append(
                    {
                        "phase": result.phase,
                        "strategy": result.strategy,
                        "page_type": result.page_type,
                        "quality_score": result.quality_score,
                        "decision": result.decision,
                        "details": {"notes": result.notes},
                    }
                )
                if best is None or result.quality_score > best.quality_score:
                    best = result
                if self.is_acceptable(result):
                    return result
        return best

    def is_acceptable(self, result) -> bool:
        return bool(result and result.quality_score >= self.quality_threshold and word_count(result.content) >= 60)

    def _final_payload(self, flow: FlowState, actions_taken: list[str]) -> dict[str, Any]:
        result = flow.best_result
        title = (
            result.markdown.splitlines()[0].lstrip("# ").strip() if result else flow.fetch.title or flow.fetch.final_url
        )
        content = result.content if result else ""
        ok, error, message, warnings = self._evaluate_read_result(flow, result, title, content)

        all_items = result.items if result else []
        all_links = result.links if result else []

        # Trimmed: removed markdown (duplicates content), flow_steps (debugging trace),
        # and visible_text (duplicates content) from payload to reduce LLM context bloat.
        # Re-enable if needed for debugging.
        _markdown = result.markdown if result else f"# {title}\n\n## Content\n{content.strip()}".strip()
        _flow_steps = flow.steps
        _visible_text = result.visible_text if result else ""

        # Domain chrome dedup: split repeated nav/header items and links
        result_items: list[dict] = all_items
        result_links: list[dict] = all_links
        chrome_omitted: dict[str, Any] | None = None
        if self.chrome_cache and result:
            domain = self.chrome_cache.domain_from_url(flow.fetch.final_url)
            content_items, chrome_items, content_links, chrome_links = self.chrome_cache.split_chrome(
                domain, all_items, all_links
            )
            if chrome_items or chrome_links:
                chrome_omitted = {
                    "items": len(chrome_items),
                    "links": len(chrome_links),
                    "domain": domain,
                    "retrieve_with": "web__domain_chrome",
                }
                result_items = content_items
                result_links = content_links

        payload: dict[str, Any] = {
            "ok": ok,
            "error": error,
            "message": message,
            "warnings": warnings,
            "requested_url": flow.fetch.url,
            "url": flow.fetch.final_url,
            "status_code": flow.fetch.status_code,
            "redirected": flow.fetch.final_url != flow.fetch.url,
            "page_type": result.page_type if result else flow.page_type,
            "title": title,
            "content": content,
            "links": result_links,
            "items": result_items,
            "actions_taken": actions_taken,
            "quality_score": result.quality_score if result else 0.0,
            "strategy": result.strategy if result else "best_effort",
            "fallback_used": flow.fallback_used,
            "_markdown": _markdown,
            "_flow_steps": _flow_steps,
            "_visible_text": _visible_text,
            "_items_all": all_items,
            "_links_all": all_links,
        }
        if chrome_omitted is not None:
            payload["chrome_omitted"] = chrome_omitted
        return payload

    @staticmethod
    def _missing_dependency_payload(operation: str, url: str, missing: list[str]) -> dict[str, Any]:
        packages = ", ".join(missing)
        return {
            "ok": False,
            "url": url,
            "operation": operation,
            "error": "missing_dependencies",
            "message": f"Missing dependencies for web_agent {operation}: {packages}",
            "missing_dependencies": missing,
        }

    def _evaluate_read_result(
        self,
        flow: FlowState,
        result: Any,
        title: str,
        content: str,
    ) -> tuple[bool, str | None, str | None, list[str]]:
        warnings: list[str] = []
        status_code = flow.fetch.status_code
        final_url = flow.fetch.final_url
        requested_url = flow.fetch.url
        has_content = self._has_meaningful_content(content)

        if status_code is not None and status_code >= 400:
            warnings.append(f"http_status_{status_code}")
        if self._looks_like_error_page(final_url, title, content, status_code):
            warnings.append("error_page")
        if self._is_redirect_mismatch(requested_url, final_url):
            warnings.append("redirect_mismatch")
        if result and self._looks_like_navigation_listing(requested_url, result, content):
            warnings.append("navigation_heavy_listing")

        if (
            status_code is not None
            and 200 <= status_code < 300
            and has_content
            and "error_page" not in warnings
            and "redirect_mismatch" not in warnings
            and "navigation_heavy_listing" not in warnings
        ):
            return True, None, None, warnings

        if not warnings:
            return True, None, None, []

        has_http_error = any(item.startswith("http_status_") for item in warnings)
        if has_http_error or "error_page" in warnings:
            return False, "page_not_found", "The requested page resolved to a 404 or error page.", warnings
        if "redirect_mismatch" in warnings:
            return False, "redirect_mismatch", "The requested page redirected to different content.", warnings
        return (
            False,
            "content_not_relevant",
            "The page content appears generic or not specific to the requested URL.",
            warnings,
        )

    @staticmethod
    def _looks_like_error_page(final_url: str, title: str, content: str, status_code: int | None) -> bool:
        if status_code is not None and status_code >= 400:
            return True
        lowered_url = final_url.lower()
        lowered_title = title.lower()
        lowered_content = content.lower()
        title_markers = ("404", "410", "500", "not found", "error", "page not found")
        content_markers = (
            "404 not found",
            "410 gone",
            "500 internal server error",
            "requested url was not found",
            "page not found",
        )
        if "/404" in lowered_url or lowered_url.endswith("404.html"):
            return True
        title_has_marker = any(marker in lowered_title for marker in title_markers)
        content_has_strong_marker = any(marker in lowered_content for marker in content_markers)
        if title_has_marker and content_has_strong_marker:
            return True
        if title_has_marker and not WebAgentTool._has_meaningful_content(content):
            return True
        return False

    @staticmethod
    def _has_meaningful_content(content: str) -> bool:
        cleaned = " ".join(part.strip() for part in content.splitlines() if part.strip())
        if not cleaned:
            return False
        if word_count(cleaned) >= 40:
            return True
        return len(cleaned) >= 280

    @staticmethod
    def _url_tokens(url: str) -> set[str]:
        path = unquote(urlparse(url).path).lower()
        tokens = re.split(r"[^0-9a-z]+", path)
        return {token for token in tokens if len(token) >= 3 and not token.isdigit() and token != "html"}

    def _is_redirect_mismatch(self, requested_url: str, final_url: str) -> bool:
        if requested_url == final_url:
            return False
        requested_tokens = self._url_tokens(requested_url)
        final_tokens = self._url_tokens(final_url)
        if not requested_tokens or not final_tokens:
            return False
        overlap = len(requested_tokens & final_tokens) / len(requested_tokens)
        return overlap < 0.35

    @staticmethod
    def _looks_like_specific_article_url(url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(".html") and (bool(re.search(r"\d", path)) or path.count("-") >= 6)

    def _looks_like_navigation_listing(self, requested_url: str, result: Any, content: str) -> bool:
        if not self._looks_like_specific_article_url(requested_url):
            return False
        if str(getattr(result, "page_type", "")) != "listing":
            return False
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return False
        short_line_ratio = sum(1 for line in lines if word_count(line) <= 5) / len(lines)
        bare_url_count = len(re.findall(r"https?://", content))
        return short_line_ratio >= 0.45 or bare_url_count >= 8


def load_steps(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_result_payload(command: str, url: str, payload: dict[str, Any]) -> dict[str, str]:
    outputs_dir = ensure_outputs_dir()
    stem = build_output_stem(url)
    json_path = save_json_output(payload, stem=f"{stem}-{command}", output_dir=outputs_dir)
    markdown_path = outputs_dir / f"{stem}-{command}.md"
    markdown_path.write_text(payload.get("_markdown", payload.get("markdown", "")), encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(markdown_path)}


async def run_command(args: argparse.Namespace) -> dict[str, Any]:
    tool = WebAgentTool(quality_threshold=args.quality_threshold, headless=not args.show_browser)
    if args.command == "read":
        payload = await tool.read(args.url)
    elif args.command == "snapshot":
        payload = await tool.snapshot(args.url)
    elif args.command == "interact":
        payload = await tool.interact(args.url, steps=load_steps(args.steps))
    else:
        raise ValueError(f"Unsupported command: {args.command}")
    if args.command in {"read", "interact"}:
        payload["saved_outputs"] = save_result_payload(args.command, args.url, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw-style web agent tool.")
    parser.add_argument("command", choices=("read", "interact", "snapshot"))
    parser.add_argument("url")
    parser.add_argument("--steps", help="Path to a JSON file describing interaction steps.")
    parser.add_argument("--show-browser", action="store_true", help="Show the Playwright browser window.")
    parser.add_argument("--quality-threshold", type=float, default=DEFAULT_QUALITY_THRESHOLD)
    return parser.parse_args()
