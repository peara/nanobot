from __future__ import annotations

import contextlib
import os
import warnings
from pathlib import Path

from ..cleaners import clean_html_fragment
from ..config import ARTICLE_SELECTORS, LISTING_SELECTORS, MAX_ITEMS, MAX_LINKS
from ..models import ExtractionResult
from ..scorers import score_content
from ..utils import (
    canonicalize_url,
    dedupe_lines,
    extract_title_from_html,
    normalize_text_block,
    normalize_whitespace,
    parse_html,
    word_count,
)


def selectors_for_page_type(page_type: str) -> tuple[str, ...]:
    if page_type == "listing":
        return LISTING_SELECTORS
    return ARTICLE_SELECTORS


def extract_links(html: str, base_url: str, limit: int = MAX_LINKS) -> list[dict[str, str]]:
    soup = parse_html(html or "")
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = canonicalize_url(anchor.get("href", ""), base_url)
        text = normalize_whitespace(anchor.get_text(" ", strip=True))
        if not href or href in seen:
            continue
        seen.add(href)
        results.append({"text": text, "href": href})
        if len(results) >= limit:
            break
    return results


def extract_listing_items(html: str, base_url: str) -> list[dict[str, str]]:
    soup = parse_html(html or "")
    candidates = soup.select("article, li, .card, .item, .result, .post, .story")
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in candidates:
        link = node.select_one("a[href]")
        if not link:
            continue
        href = canonicalize_url(link.get("href", ""), base_url)
        title = normalize_whitespace(
            link.get_text(" ", strip=True) or (node.select_one("h1, h2, h3, h4") or node).get_text(" ", strip=True)
        )
        description = normalize_whitespace(
            " ".join(part.get_text(" ", strip=True) for part in node.select("p, .description, .summary, .excerpt")[:2])
        )
        if not href or not title or href in seen:
            continue
        seen.add(href)
        items.append({"title": title, "link": href, "description": description})
        if len(items) >= MAX_ITEMS:
            break
    return items


def markdown_for_listing(items: list[dict[str, str]], title: str) -> str:
    lines = [f"# {title}", "", "## Items", ""]
    for item in items:
        lines.append(f"- [{item['title']}]({item['link']})")
        if item["description"]:
            lines.append(f"  {item['description']}")
    return "\n".join(lines).strip()


def build_markdown(title: str, content: str, items: list[dict[str, str]] | None = None) -> str:
    if items:
        return markdown_for_listing(items, title)
    return f"# {title}\n\n## Content\n{content.strip()}".strip()


def selector_extract(html: str, page_type: str) -> str:
    soup = parse_html(html or "")
    fragments: list[str] = []
    for selector in selectors_for_page_type(page_type):
        for node in soup.select(selector):
            text = clean_html_fragment(str(node))
            if word_count(text) >= 40:
                fragments.append(text)
        if fragments:
            break
    return dedupe_lines("\n\n".join(fragments))


def heuristic_extract(html: str) -> str:
    try:
        from selectolax.parser import HTMLParser
    except ModuleNotFoundError:
        return ""

    parser = HTMLParser(html or "<html></html>")
    best_text = ""
    best_score = -1.0
    for node in parser.css("article, main, section, div"):
        text = normalize_whitespace(node.text(separator=" ", strip=True))
        words = word_count(text)
        if words < 40:
            continue
        links = node.css("a")
        link_text = " ".join(normalize_whitespace(link.text(separator=" ", strip=True)) for link in links)
        link_words = word_count(link_text)
        link_density = link_words / max(words, 1)
        paragraph_count = len(node.css("p"))
        heading_count = len(node.css("h1, h2, h3"))
        score = words * (1 - min(0.9, link_density)) + paragraph_count * 18 + heading_count * 8
        if score > best_score:
            best_score = score
            best_text = text
    return dedupe_lines(best_text)


def readability_extract(html: str, url: str) -> str:
    if not normalize_whitespace(html):
        return ""
    try:
        import trafilatura
    except ModuleNotFoundError:
        extracted = None
    else:
        try:
            extracted = trafilatura.extract(
                html,
                url=url,
                include_links=False,
                include_images=False,
                favor_precision=True,
            )
        except Exception:
            extracted = None
    if extracted:
        return dedupe_lines(normalize_text_block(extracted))
    try:
        from readability import Document
    except ModuleNotFoundError:
        return ""
    try:
        doc = Document(html)
        summary_html = doc.summary()
    except Exception:
        return ""
    return dedupe_lines(clean_html_fragment(summary_html))


async def crawl4ai_extract(url: str) -> tuple[str, str]:
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
                from crawl4ai.content_filter_strategy import PruningContentFilter
                from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
            except ModuleNotFoundError:
                return "", ""

            generator = DefaultMarkdownGenerator(content_filter=PruningContentFilter(threshold=0.45))
            config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                markdown_generator=generator,
                wait_until="domcontentloaded",
                page_timeout=45000,
                remove_overlay_elements=True,
                remove_consent_popups=True,
            )
            try:
                async with AsyncWebCrawler(
                    base_directory=str(Path("./data/web_agent/.crawl4ai").resolve())
                ) as crawler:
                    result = await crawler.arun(url, config=config)
            except Exception:
                return "", ""
    markdown_result = getattr(result, "markdown", None)
    markdown = normalize_text_block(
        getattr(markdown_result, "fit_markdown", "") or getattr(markdown_result, "raw_markdown", "") or ""
    )
    html = result.cleaned_html or result.html or ""
    return markdown, html


def metadata_extract(html: str) -> str:
    soup = parse_html(html or "")
    parts: list[str] = []
    for selector, attr in (
        ("meta[property='og:title']", "content"),
        ("meta[name='twitter:title']", "content"),
        ("meta[property='og:description']", "content"),
        ("meta[name='description']", "content"),
    ):
        for node in soup.select(selector):
            value = normalize_whitespace(node.get(attr, ""))
            if value:
                parts.append(value)
    title = normalize_whitespace(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title:
        parts.insert(0, title)
    return dedupe_lines("\n\n".join(parts))


async def run_extraction_strategy(
    *,
    strategy: str,
    html: str,
    source_url: str,
    final_url: str,
    page_type: str,
    quality_threshold: float,
    links: list[dict[str, str]],
    visible_text: str,
    crawl4ai_result: tuple[str, str] | None = None,
) -> ExtractionResult | None:
    items: list[dict[str, str]] = []
    notes: list[str] = []
    if strategy == "selector":
        content = selector_extract(html, page_type)
    elif strategy == "heuristic":
        content = heuristic_extract(html)
    elif strategy == "readability":
        content = readability_extract(html, final_url)
    elif strategy == "metadata":
        content = metadata_extract(html)
    elif strategy == "listing":
        items = extract_listing_items(html, final_url)
        if not items:
            return None
        content = "\n".join(f"{item['title']}\n{item['description']}\n{item['link']}".strip() for item in items)
    elif strategy == "crawl4ai":
        markdown, crawl_html = crawl4ai_result if crawl4ai_result is not None else await crawl4ai_extract(source_url)
        content = markdown
        if crawl_html:
            visible_text = clean_html_fragment(crawl_html)
            if not links:
                links = extract_links(crawl_html, final_url)
        notes.append("llm_markdown")
    else:
        raise ValueError(f"Unsupported strategy: {strategy}")

    content = dedupe_lines(normalize_text_block(content))
    if not content:
        return None

    score = score_content(content)
    if strategy == "listing":
        score = max(score, min(1.0, 0.2 + len(items) * 0.12))
    decision = "accept" if score >= quality_threshold else "retry"
    title = extract_title_from_html(html) or final_url
    markdown = (
        content
        if strategy == "crawl4ai" and content.lstrip().startswith("#")
        else build_markdown(title, content, items or None)
    )
    return ExtractionResult(
        phase="extract",
        strategy=strategy,
        page_type=page_type,
        content=content,
        markdown=markdown,
        items=items,
        links=links,
        visible_text=visible_text,
        quality_score=score,
        decision=decision,
        notes=notes,
    )
