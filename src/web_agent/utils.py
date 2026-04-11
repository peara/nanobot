from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from .config import JS_HEAVY_HINTS


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_text_block(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def meaningful_sentence_count(text: str) -> int:
    sentences = re.split(r"(?<=[.!?])\s+", normalize_whitespace(text))
    return sum(1 for sentence in sentences if word_count(sentence) >= 5)


def dedupe_lines(text: str) -> str:
    seen: set[str] = set()
    kept: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        key = normalize_whitespace(line).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(line)
    return "\n".join(kept).strip()


def canonicalize_url(href: str, base_url: str) -> str:
    if not href:
        return ""
    return urljoin(base_url, href.strip())


def parse_html(html: str) -> Any:
    try:
        from bs4 import BeautifulSoup, FeatureNotFound
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency: beautifulsoup4. Install project web-agent dependencies.") from exc
    try:
        return BeautifulSoup(html or "", "lxml")
    except FeatureNotFound:
        return BeautifulSoup(html or "", "html.parser")


def is_probably_js_heavy(html: str) -> bool:
    lowered = (html or "").lower()
    script_count = lowered.count("<script")
    body_words = word_count(parse_html(html or "").get_text(" ", strip=True))
    return body_words < 120 and (script_count >= 8 or any(hint in lowered for hint in JS_HEAVY_HINTS))


def is_selector_target(target: str) -> bool:
    return bool(re.search(r"[#.\[\]>:=]|^[a-zA-Z][a-zA-Z0-9_-]*(\s+[a-zA-Z][a-zA-Z0-9_-]*)*$", target))


def extract_title_from_html(html: str) -> str:
    soup = parse_html(html or "")
    for selector, attr in (
        ("meta[property='og:title']", "content"),
        ("meta[name='twitter:title']", "content"),
    ):
        node = soup.select_one(selector)
        if node:
            value = normalize_whitespace(node.get(attr, ""))
            if value:
                return value
    if soup.title:
        return normalize_whitespace(soup.title.get_text(" ", strip=True))
    header = soup.select_one("h1")
    return normalize_whitespace(header.get_text(" ", strip=True) if header else "")
