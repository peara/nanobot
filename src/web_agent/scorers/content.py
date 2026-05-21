from __future__ import annotations

import math
import re

from ..utils import normalize_whitespace, word_count

ENGLISH_STOPWORDS = frozenset(
    {
        "the",
        "be",
        "to",
        "of",
        "and",
        "a",
        "in",
        "that",
        "have",
        "i",
        "it",
        "for",
        "not",
        "on",
        "with",
        "he",
        "as",
        "you",
        "do",
        "at",
        "this",
        "but",
        "his",
        "by",
        "from",
        "they",
        "we",
        "say",
        "her",
        "she",
        "or",
        "an",
        "will",
        "my",
        "one",
        "all",
        "would",
        "there",
        "their",
        "what",
        "so",
        "up",
        "out",
        "if",
        "about",
        "who",
        "get",
        "which",
        "go",
        "me",
        "when",
        "make",
        "can",
        "like",
        "time",
        "no",
        "just",
        "him",
        "know",
        "take",
        "people",
        "into",
        "year",
        "your",
        "good",
        "some",
        "could",
        "them",
        "see",
        "other",
        "than",
        "then",
        "now",
        "look",
        "only",
        "come",
        "its",
        "over",
        "think",
        "also",
        "back",
        "after",
        "use",
        "two",
        "how",
        "our",
        "work",
        "first",
        "well",
        "way",
        "even",
        "new",
        "want",
        "because",
        "any",
        "these",
        "give",
        "day",
        "most",
        "us",
        "was",
        "were",
        "been",
        "has",
        "had",
        "did",
        "are",
        "is",
        "am",
        "does",
        "should",
        "might",
        "may",
        "must",
        "shall",
        "need",
        "ought",
    }
)

_CSS_PATTERNS = [
    re.compile(r"\{[^}]*:[^}]*;"),  # CSS declarations
    re.compile(r"\.[\w-]+\{"),  # class selectors
    re.compile(r"@media"),  # @media queries
    re.compile(r"!important"),  # !important
    re.compile(r"position\s*:\s*(?:absolute|relative|fixed|sticky)"),
    re.compile(r"display\s*:\s*(?:none|block|flex|grid|inline)"),
    re.compile(r"(?:margin|padding|font-size|background-color)\s*:\s*[\d.]+(?:px|rem|em)"),
]

_JS_PATTERNS = [
    re.compile(r"function\s*\("),
    re.compile(r"\b(?:var|let|const)\s+\w+\s*="),
    re.compile(r"\bconsole\.\w+\("),
    re.compile(r"\bdocument\.\w+\("),
    re.compile(r"\bwindow\.\w+\("),
    re.compile(r"\baddEventListener\("),
    re.compile(r"\btypeof\s+\w+"),
]

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_SENTENCE_ENDER_RE = re.compile(r"[.!?](?:\s|$)")


def looks_like_code(text: str) -> bool:
    prefix = text[:2000]
    css_matches = sum(1 for p in _CSS_PATTERNS if p.search(prefix))
    js_matches = sum(1 for p in _JS_PATTERNS if p.search(prefix))
    return css_matches >= 3 or js_matches >= 3


def stopword_density(text: str) -> float:
    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return 0.0
    return sum(1 for w in words if w in ENGLISH_STOPWORDS) / len(words)


def symbol_ratio(text: str) -> float:
    if not text:
        return 0.0
    allowed_re = re.compile(r"[a-zA-Z\s.,!?;:'\"\-\(\)]")
    allowed = sum(1 for ch in text if allowed_re.match(ch))
    return 1.0 - allowed / len(text)


def _prose_ratio(text: str) -> float:
    words = word_count(text)
    sentence_enders = len(_SENTENCE_ENDER_RE.findall(text))
    return sentence_enders / max(words, 1)


def _link_ratio(text: str) -> float:
    words = word_count(text)
    link_like_tokens = len(re.findall(r"https?://|www\.|/[A-Za-z0-9_-]+", text))
    return min(1.0, link_like_tokens / max(words, 1) * 10)


def score_content(text: str) -> float:
    cleaned = normalize_whitespace(text)
    if not cleaned:
        return 0.0

    words = word_count(cleaned)
    if words == 0:
        return 0.0

    sd = stopword_density(cleaned)

    if looks_like_code(cleaned) and sd < 0.20:
        return 0.0

    if sd < 0.15:
        return 0.05

    # Primary signal: stopword density (square root to reward medium values)
    stopword_density_factor = min(1.0, max(0.0, sd)) ** 0.5

    # Length: logarithmic scaling
    length_score = min(1.0, math.log(max(words, 1)) / math.log(500))

    # Symbol ratio: low symbols = more prose-like
    sym_ratio = symbol_ratio(cleaned)

    # Prose ratio: sentence-ending punctuation per word
    pr = _prose_ratio(cleaned)
    prose_ratio_scaled = min(1.0, pr * 15)

    # Link density as multiplicative penalty (not bonus)
    lr = _link_ratio(cleaned)
    if lr > 0.5:
        link_factor = 0.0
    elif lr < 0.1:
        link_factor = 1.0
    else:
        link_factor = 1.0 - min(lr * 2, 1.0)

    raw_score = (
        0.45 * stopword_density_factor
        + 0.20 * length_score
        + 0.15 * (1.0 - sym_ratio)
        + 0.10 * prose_ratio_scaled
        + 0.10 * link_factor
    )
    return max(0.0, min(1.0, raw_score))
