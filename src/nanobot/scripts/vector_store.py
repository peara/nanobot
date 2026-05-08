from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from nanobot.storage.sqlite import NanoScriptSqlite


@dataclass
class SearchResult:
    script_id: str
    score: float


class ScriptVectorStore:
    """MVP vector store abstraction with keyword similarity fallback."""

    def __init__(self, sqlite: NanoScriptSqlite) -> None:
        self.sqlite = sqlite

    def upsert(self, script_id: str, text: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.sqlite.connect() as conn:
            conn.execute(
                """
                INSERT INTO script_embeddings(script_id, embedding, embedding_text, updated_at)
                VALUES(?, NULL, ?, ?)
                ON CONFLICT(script_id) DO UPDATE SET
                    embedding_text = excluded.embedding_text,
                    updated_at = excluded.updated_at
                """,
                (script_id, text, now),
            )

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        query_tokens = _tokens(query)
        with self.sqlite.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.description, COALESCE(s.domain, ''), COALESCE(s.task_type, ''),
                       COALESCE(e.embedding_text, '')
                FROM scripts s
                LEFT JOIN script_embeddings e ON e.script_id = s.id
                WHERE s.status = 'active'
                """
            ).fetchall()

        scored: list[SearchResult] = []
        for row in rows:
            text = " ".join([str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5])]).strip()
            score = _score_text(query_tokens, text)
            if score > 0:
                scored.append(SearchResult(script_id=str(row[0]), score=score))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}


def _score_text(query_tokens: set[str], text: str) -> float:
    if not text:
        return 0.0
    text_tokens = _tokens(text)
    if not query_tokens or not text_tokens:
        return 0.0
    intersection = query_tokens.intersection(text_tokens)
    union = query_tokens.union(text_tokens)
    jaccard = len(intersection) / max(1, len(union))

    # slight boost when whole query appears in text
    text_lower = text.lower()
    query_phrase = " ".join(sorted(query_tokens))
    boost = 0.1 if query_phrase and query_phrase in text_lower else 0.0
    return min(1.0, jaccard + boost)
