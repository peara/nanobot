from __future__ import annotations

import pytest

from web_agent.service import WebAgentTool

REDDIT_HOT_URL = "https://www.reddit.com/r/LocalLLaMA/hot/"
MAX_CONTENT_CHARS = 30_000

pytestmark = pytest.mark.integration


@pytest.fixture
def tool() -> WebAgentTool:
    return WebAgentTool(quality_threshold=0.48, headless=True)


async def test_reddit_hot_page_content_is_not_css(tool: WebAgentTool) -> None:
    """Regression: heuristic_extract once returned CSS variable blocks because
    it did not strip <style> tags. Content must be human-readable text, not
    stylesheet debris."""
    result = await tool.read(REDDIT_HOT_URL)

    assert result["ok"] is True, f"Expected ok=True, got error: {result.get('error')}"
    content: str = result.get("content", "")

    css_prefixes = (".theme-light", ":root{", "--rem", ".css-", "@media")
    for prefix in css_prefixes:
        assert not content.lstrip().startswith(prefix), (
            f"Content starts with CSS prefix '{prefix}' — extraction returned raw stylesheet"
        )

    assert len(content) > 200, f"Content too short ({len(content)} chars) — likely failed extraction"

    # Regression guard: 103K CSS dump once blew up the LLM context
    assert len(content) < MAX_CONTENT_CHARS, (
        f"Content is {len(content)} chars — exceeded {MAX_CONTENT_CHARS} char limit"
    )

    word_count = len(content.split())
    assert word_count > 50, f"Content has only {word_count} words — likely not real article text"


async def test_reddit_hot_page_has_links(tool: WebAgentTool) -> None:
    """Reddit listings should extract structured links."""
    result = await tool.read(REDDIT_HOT_URL)

    assert result["ok"] is True
    assert len(result.get("links", [])) >= 1, "Expected at least 1 link from Reddit page"


async def test_reddit_hot_page_quality_score(tool: WebAgentTool) -> None:
    """Extracted content should meet minimum quality threshold."""
    result = await tool.read(REDDIT_HOT_URL)

    assert result["ok"] is True
    quality_score = result.get("quality_score", 0.0)
    assert quality_score >= 0.3, f"Quality score {quality_score} is very low — extraction probably failed"
