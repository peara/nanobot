from __future__ import annotations

import logging
import re
from typing import Any

from detect_secrets.plugins.base import RegexBasedDetector
from detect_secrets.plugins.high_entropy_strings import HighEntropyStringsPlugin
from detect_secrets.plugins.keyword import CONFIG_DENYLIST_REGEX_TO_GROUP, KeywordDetector
from detect_secrets.settings import get_plugins

logger = logging.getLogger(__name__)

_REDACTED = "[REDACTED]"
_MIN_SECRET_LEN = 8
_MAX_CAPTURE_LEN = 4000

_PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z0-9 ]+KEY(?: BLOCK)?-----")
_PEM_END_RE = re.compile(r"-----END [A-Z0-9 ]+KEY(?: BLOCK)?-----")
_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]+KEY(?: BLOCK)?-----.*?(?:-----END [A-Z0-9 ]+KEY(?: BLOCK)?-----|$)",
)

_PLUGINS: list[dict[str, Any]] = [
    {"name": "AWSKeyDetector"},
    {"name": "BasicAuthDetector"},
    {"name": "Base64HighEntropyString", "limit": 4.5},
    {"name": "GitHubTokenDetector"},
    # Hex default 3.0 emits 1-3 char garbage on JSON text; 4.0 misses real hex secrets.
    {"name": "HexHighEntropyString", "limit": 3.5},
    {"name": "JwtTokenDetector"},
    {"name": "KeywordDetector"},
    {"name": "OpenAIDetector"},
    {"name": "PrivateKeyDetector"},
    {"name": "SlackDetector"},
]

_settings_initialized = False


def _init_scanner_settings() -> None:
    """Configure the detect-secrets plugin set once (idempotent)."""
    global _settings_initialized
    if _settings_initialized:
        return
    from detect_secrets.settings import get_settings

    settings = get_settings()
    settings.clear()
    settings.configure_plugins(_PLUGINS)
    # Heuristic filters assume source-code context and suppress real secrets in
    # JSON payloads (verified: is_likely_id_string kills JSON password values).
    # Redaction wants maximum recall; there is no code-context FP cost here.
    settings.filters = {}
    _settings_initialized = True


def _deescape(line: str) -> tuple[str, list[int]]:
    """Remove backslashes before quotes so quote-sensitive detectors work on
    nested JSON (tool-call arguments serialize quotes as backslash-quote).

    Returns (deescaped_line, index_map) where index_map[i] is the position of
    deescaped char i in the original line.
    """
    out: list[str] = []
    idx: list[int] = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] in ('"', "'"):
            i += 1
        out.append(line[i])
        idx.append(i)
        i += 1
    return "".join(out), idx


def _map_span(start: int, end: int, idx: list[int]) -> tuple[int, int]:
    return idx[start], idx[end - 1] + 1


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _apply_spans(line: str, spans: list[tuple[int, int]]) -> str:
    out: list[str] = []
    prev = 0
    for start, end in spans:
        out.append(line[prev:start])
        out.append(_REDACTED)
        prev = end
    out.append(line[prev:])
    return "".join(out)


def _keyword_spans(text: str) -> list[tuple[int, int]]:
    """CONFIG regexes accept quoted AND unquoted assignments; the default
    quotes-required path misses prose values. analyze_string() yields only the
    first match per regex, so search iteratively."""
    spans: list[tuple[int, int]] = []
    for regex, group in CONFIG_DENYLIST_REGEX_TO_GROUP.items():
        pos = 0
        while True:
            match = regex.search(text, pos)
            if not match:
                break
            value = match.group(group)
            start = match.start(group)
            if not value or start < 0:
                pos = match.end()
                continue
            # Greedy captures run to the next quote/comma/EOL; trim to the
            # first token unless quote-terminated (JSON/config style).
            quote_terminated = start > 0 and text[start - 1] in ('"', "'")
            use = value if quote_terminated else (value.split()[0] if value.split() else value)
            if len(use) >= _MIN_SECRET_LEN:
                spans.append((start, start + len(use)))
            pos = start + max(len(use), 1)
    return spans


def _scan_text(text: str) -> list[tuple[int, int]]:
    """Secret spans within a (de-escaped) line."""
    spans: list[tuple[int, int]] = []
    for match in _PEM_BLOCK_RE.finditer(text):
        spans.append((match.start(), match.end()))
    for plugin in get_plugins():
        if isinstance(plugin, RegexBasedDetector):
            # Full-match spans: scan_line's secret_value is only the regex
            # capture group for some plugins (GitHubTokenDetector yields "ghp").
            for regex in plugin.denylist:
                for match in regex.finditer(text):
                    spans.append((match.start(), match.end()))
        elif isinstance(plugin, HighEntropyStringsPlugin):
            candidates = list(plugin.analyze_string(text))
            with plugin.non_quoted_string_regex(is_exact_match=False):
                candidates.extend(plugin.analyze_string(text))
            for value in candidates:
                if (
                    value
                    and len(value) >= _MIN_SECRET_LEN
                    and plugin.calculate_shannon_entropy(value) > plugin.entropy_limit
                ):
                    start = text.find(value)
                    if start >= 0:
                        spans.append((start, start + len(value)))
        elif isinstance(plugin, KeywordDetector):
            spans.extend(_keyword_spans(text))
    return spans


def redact_payload(text: str, limit: int = _MAX_CAPTURE_LEN) -> str:
    """Mask secret-shaped strings in an LLM payload before persisting as
    span attributes. Backed by detect-secrets (gitleaks-rule families: AWS,
    GitHub, Slack, OpenAI, JWT, private keys, Basic auth, keyword assignments,
    high-entropy strings). Best effort, not a guarantee.
    """
    _init_scanner_settings()
    redacted_lines: list[str] = []
    in_pem_block = False
    for line in text.splitlines():
        if in_pem_block:
            redacted_lines.append(_REDACTED)
            if _PEM_END_RE.search(line):
                in_pem_block = False
            continue
        deescaped, idx = _deescape(line)
        spans = [_map_span(start, end, idx) for start, end in _scan_text(deescaped)]
        spans = [(start, end) for start, end in spans if end > start]
        if spans:
            redacted_lines.append(_apply_spans(line, _merge_spans(spans)))
            if _PEM_BEGIN_RE.search(line) and not _PEM_END_RE.search(line):
                in_pem_block = True
        else:
            redacted_lines.append(line)
    masked = "\n".join(redacted_lines)
    if len(masked) > limit:
        masked = f"{masked[:limit]}...[clipped {len(masked) - limit} chars]"
    return masked
