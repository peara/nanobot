from __future__ import annotations

import re

from ..utils import meaningful_sentence_count, normalize_whitespace, word_count


def score_content(text: str) -> float:
    cleaned = normalize_whitespace(text)
    if not cleaned:
        return 0.0

    words = word_count(cleaned)
    if words == 0:
        return 0.0

    unique_ratio = len({token.lower() for token in re.findall(r"\b\w+\b", cleaned)}) / words
    link_like_tokens = len(re.findall(r"https?://|www\.|/[A-Za-z0-9_-]+", cleaned))
    link_ratio = min(1.0, link_like_tokens / max(words, 1) * 10)
    repeated_lines_penalty = 0.0
    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    if lines:
        repeated_lines_penalty = 1.0 - (len(set(line.lower() for line in lines)) / len(lines))

    sentence_score = min(1.0, meaningful_sentence_count(cleaned) / max(words / 40, 1))
    length_score = min(1.0, words / 500)
    uniqueness_score = min(1.0, unique_ratio * 1.4)
    repetition_penalty = min(0.45, repeated_lines_penalty * 0.6)
    score = (
        0.42 * length_score
        + 0.24 * sentence_score
        + 0.20 * uniqueness_score
        + 0.14 * (1.0 - link_ratio)
        - repetition_penalty
    )
    return max(0.0, min(1.0, score))
