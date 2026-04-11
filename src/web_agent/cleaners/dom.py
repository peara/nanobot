from __future__ import annotations

from typing import Any

from ..config import NON_CONTENT_SELECTORS
from ..utils import normalize_text_block, parse_html


def clean_dom_to_text(soup: Any) -> str:
    soup = parse_html(str(soup))
    for selector in NON_CONTENT_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    return normalize_text_block(soup.get_text("\n", strip=True))


def clean_html_fragment(fragment: str) -> str:
    soup = parse_html(fragment or "")
    return clean_dom_to_text(soup)
