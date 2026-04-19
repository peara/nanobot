from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .config import DEFAULT_QUALITY_THRESHOLD
from .dependencies import INTERACT_REQUIRED, READ_REQUIRED, SNAPSHOT_REQUIRED, missing_dependencies
from .models import FetchResult, FlowState
from .output_utils import build_output_stem, ensure_outputs_dir, save_json_output
from .utils import extract_title_from_html, word_count


class WebAgentTool:
    def __init__(self, quality_threshold: float = DEFAULT_QUALITY_THRESHOLD, headless: bool = True):
        self.quality_threshold = quality_threshold
        self.headless = headless

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
                title = await browser.page.title() if browser.page else url
                await browser.screenshot(screenshot_path)
        except BrowserUnavailableError as exc:
            return {
                "url": url,
                "page_type": "unknown",
                "title": "",
                "content": "",
                "visible_text": "",
                "links": [],
                "items": [],
                "actions_taken": actions_taken,
                "quality_score": 0.0,
                "strategy": "playwright",
                "fallback_used": True,
                "flow_steps": [],
                "markdown": "",
                "interaction_snapshots": snapshots,
                "screenshot_path": None,
                "error": f"browser_unavailable: {exc}",
            }
        flow = await self.run_read_flow(url, starting_html=html, starting_title=title, force_browser_fetch=False)
        payload = self._final_payload(flow, actions_taken=actions_taken)
        payload["interaction_snapshots"] = snapshots
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
        if result:
            markdown = result.markdown
        else:
            markdown = f"# {title}\n\n## Content\n{content.strip()}".strip()
        ok, error, message, warnings = self._evaluate_read_result(flow, result, title, content)
        return {
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
            "visible_text": result.visible_text if result else "",
            "links": result.links if result else [],
            "items": result.items if result else [],
            "actions_taken": actions_taken,
            "quality_score": result.quality_score if result else 0.0,
            "strategy": result.strategy if result else "best_effort",
            "fallback_used": flow.fallback_used,
            "flow_steps": flow.steps,
            "markdown": markdown,
        }

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

        if status_code is not None and status_code >= 400:
            warnings.append(f"http_status_{status_code}")
        if self._looks_like_error_page(final_url, title, content):
            warnings.append("error_page")
        if self._is_redirect_mismatch(requested_url, final_url):
            warnings.append("redirect_mismatch")
        if result and self._looks_like_navigation_listing(requested_url, result, content):
            warnings.append("navigation_heavy_listing")

        if not warnings:
            return True, None, None, []

        if "http_status_404" in warnings or "error_page" in warnings:
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
    def _looks_like_error_page(final_url: str, title: str, content: str) -> bool:
        lowered_url = final_url.lower()
        lowered_title = title.lower()
        lowered_content = content.lower()
        markers = (
            "404",
            "not found",
            "trang thông báo lỗi",
            "nội dung này đã bị gỡ",
            "khong ton tai",
            "không tồn tại",
            "requested url was not found",
            "page not found",
        )
        if "/404" in lowered_url or lowered_url.endswith("404.html"):
            return True
        combined = f"{lowered_title}\n{lowered_content}"
        return any(marker in combined for marker in markers)

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
    markdown_path.write_text(payload.get("markdown", ""), encoding="utf-8")
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
