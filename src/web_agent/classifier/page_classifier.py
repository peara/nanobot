from __future__ import annotations

from ..cleaners import clean_dom_to_text
from ..config import ARTICLE_URL_HINTS, DASHBOARD_HINTS, LISTING_URL_HINTS, PRODUCT_HINTS, PROFILE_HINTS
from ..utils import parse_html, word_count


def classify_page(url: str, html: str) -> str:
    soup = parse_html(html or "")
    lowered_url = url.lower()
    lowered_text = clean_dom_to_text(soup).lower()

    if any(hint in lowered_url for hint in LISTING_URL_HINTS):
        return "listing"
    if any(hint in lowered_text for hint in PRODUCT_HINTS):
        return "product"
    if any(hint in lowered_text for hint in DASHBOARD_HINTS):
        return "dashboard"
    if any(hint in lowered_text for hint in PROFILE_HINTS):
        return "profile"

    article_nodes = len(soup.select("article"))
    forms = len(soup.select("form"))
    buttons = len(soup.select("button, [role='button'], input[type='submit']"))
    links = soup.select("a[href]")
    headline_count = len(soup.select("h1, h2, h3"))
    paragraphs = len(soup.select("p"))
    repeated_cards = len(soup.select("li, article, .card, .item, .result"))
    link_text = " ".join(node.get_text(" ", strip=True) for node in links[:80])
    link_words = word_count(link_text)
    body_words = max(1, word_count(clean_dom_to_text(soup)))
    link_density = link_words / body_words

    if article_nodes >= 1 and paragraphs >= 5:
        return "article"
    if headline_count >= 6 and len(links) >= 12 and repeated_cards >= 8:
        return "listing"
    if len(links) >= 20 and link_density > 0.35:
        return "listing"
    if forms >= 2 and buttons >= 5:
        return "dashboard"
    if any(hint in lowered_url for hint in ARTICLE_URL_HINTS) and paragraphs >= 3:
        return "article"
    return "unknown"


def page_type_order(initial_page_type: str) -> list[str]:
    ordered = [initial_page_type, "article", "listing", "product", "profile", "dashboard", "unknown"]
    return list(dict.fromkeys(item for item in ordered if item))
