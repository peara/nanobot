from __future__ import annotations

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..browser.interactor import BrowserInteractor, BrowserUnavailableError
from ..models import FetchResult
from ..utils import extract_title_from_html


async def fetch_browser(url: str, *, headless: bool) -> FetchResult:
    errors: list[str] = []
    try:
        async with BrowserInteractor(headless=headless) as browser:
            await browser.open(url)
            await browser.wait_for(network_idle=True)
            await browser.scroll(1200)
            html = await browser.extract_html()
            title = await browser.page.title() if browser.page else extract_title_from_html(html)
            return FetchResult(
                strategy="playwright",
                url=url,
                final_url=browser.page.url if browser.page else url,
                status_code=200,
                html=html,
                title=title,
                used_browser=True,
                weak_content=False,
                errors=errors,
            )
    except (BrowserUnavailableError, PlaywrightError, PlaywrightTimeoutError) as exc:
        errors.append(str(exc))
    return FetchResult(
        strategy="playwright",
        url=url,
        final_url=url,
        status_code=None,
        html="",
        title="",
        used_browser=True,
        weak_content=True,
        errors=errors or ["browser_fetch_failed"],
    )
