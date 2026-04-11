from __future__ import annotations

import httpx

from ..cleaners import clean_dom_to_text
from ..config import DEFAULT_TIMEOUT_SECONDS
from ..models import FetchResult
from ..utils import extract_title_from_html, is_probably_js_heavy, parse_html, word_count


async def fetch_http(url: str) -> FetchResult:
    errors: list[str] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=DEFAULT_TIMEOUT_SECONDS, headers=headers) as client:
        for attempt in range(2):
            try:
                response = await client.get(url)
                html = response.text
                return FetchResult(
                    strategy="httpx",
                    url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    html=html,
                    title=extract_title_from_html(html),
                    used_browser=False,
                    weak_content=False,
                    errors=errors,
                )
            except httpx.HTTPError as exc:
                errors.append(f"attempt_{attempt + 1}:{exc}")
    return FetchResult(
        strategy="httpx",
        url=url,
        final_url=url,
        status_code=None,
        html="",
        title="",
        used_browser=False,
        weak_content=True,
        errors=errors or ["http_fetch_failed"],
    )


def should_escalate_fetch(fetch: FetchResult) -> bool:
    html = fetch.html or ""
    soup = parse_html(html)
    visible_words = word_count(clean_dom_to_text(soup))
    if fetch.status_code and fetch.status_code >= 400:
        return True
    if len(html) < 1500 or visible_words < 80:
        return True
    if is_probably_js_heavy(html):
        return True
    return False
