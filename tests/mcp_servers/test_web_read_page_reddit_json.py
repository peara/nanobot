from __future__ import annotations

import httpx
import pytest

from web_agent.service import WebAgentTool

REDDIT_HOT_URL = "https://www.reddit.com/r/LocalLLaMA/hot/"
REDDIT_HOT_JSON_URL = "https://www.reddit.com/r/LocalLLaMA/hot.json"
REDDIT_HOT_RSS_URL = "https://www.reddit.com/r/LocalLLaMA/hot/.rss"

pytestmark = pytest.mark.integration

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def test_reddit_json_endpoint_accessible_via_httpx() -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers={"User-Agent": UA}) as client:
        response = await client.get(REDDIT_HOT_JSON_URL)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["kind"] == "Listing", f"Expected kind=Listing, got {data.get('kind')}"
    children = data["data"]["children"]
    assert len(children) >= 1, "Expected at least 1 post in listing"


async def test_reddit_rss_endpoint_accessible_via_httpx() -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers={"User-Agent": UA}) as client:
        response = await client.get(REDDIT_HOT_RSS_URL)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    body = response.text
    assert "<feed" in body.lower() or "<rss" in body.lower(), "Expected RSS/Atom XML feed"


async def test_reddit_hot_html_accessible_via_httpx() -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers={"User-Agent": UA}) as client:
        response = await client.get(REDDIT_HOT_URL)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    html = response.text
    assert len(html) > 1000, f"HTML too short ({len(html)} chars) — likely blocked or empty"


@pytest.fixture
def tool() -> WebAgentTool:
    return WebAgentTool(quality_threshold=0.48, headless=True)


async def test_reddit_json_via_read_page(tool: WebAgentTool) -> None:
    result = await tool.read(REDDIT_HOT_JSON_URL)

    assert result["ok"] is True, f"Expected ok=True, got: {result.get('error')} — {result.get('message')}"
    assert result.get("fallback_used") is False, "JSON endpoint should not need browser fallback"
    content: str = result.get("content", "")
    assert len(content) > 100, f"Content too short ({len(content)} chars)"
    assert "Listing" in content or "children" in content, "Content should contain Reddit listing data"


async def test_reddit_html_via_read_page_detects_block_page(tool: WebAgentTool) -> None:
    result = await tool.read(REDDIT_HOT_URL)

    content: str = result.get("content", "")
    blocked_markers = ("you've been blocked", "blocked by network security", "network security")
    is_blocked = any(marker in content.lower() for marker in blocked_markers)

    if is_blocked:
        assert result.get("fallback_used") is True, "Blocked page should have triggered browser fallback"