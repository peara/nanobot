from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ScoreFilter(ABC):
    """Filters vector search results by score.

    Subclasses implement different strategies for deciding which results
    to keep based on their cosine similarity scores. This allows swapping
    filtering behavior by replacing one class (e.g. ThresholdFilter →
    RatioFilter) without changing any calling code.
    """

    @abstractmethod
    def filter_results(
        self,
        results: list[dict],
    ) -> list[dict]:
        """Return only results that pass the score filter.

        Args:
            results: List of dicts with at least 'score' and 'metadata' keys,
                     as returned by VectorStore.search_text().

        Returns:
            Filtered list, same format, preserving order.
        """
        ...


class ThresholdFilter(ScoreFilter):
    """Baseline filter: return all results unconditionally.

    Preserves the original behavior where every result from the vector
    store is accepted regardless of score. Useful as a control baseline
    and for backward compatibility.
    """

    def filter_results(self, results: list[dict]) -> list[dict]:
        return list(results)


class CutoffFilter(ScoreFilter):
    """Filter results by an absolute minimum cosine similarity score.

    Drops any result whose score is below ``min_score``. Simple and
    predictable, but can be too aggressive when the top match itself
    has a low score (meaning nothing in the collection is relevant).
    """

    def __init__(self, min_score: float = 0.5) -> None:
        if not 0.0 <= min_score <= 1.0:
            raise ValueError(f"min_score must be between 0.0 and 1.0, got {min_score}")
        self.min_score = min_score

    def filter_results(self, results: list[dict]) -> list[dict]:
        return [r for r in results if r.get("score", 0.0) >= self.min_score]


class RatioFilter(ScoreFilter):
    """Filter by ratio to the top score plus an absolute floor.

    Keeps results that satisfy BOTH conditions:
      1. score >= top_score * min_top_ratio  (relative threshold)
      2. score >= min_score                   (absolute floor)

    The relative threshold adapts to the score distribution: if the top
    match is strong (0.75), results below 0.75*0.7=0.525 are dropped.
    If the top match is weak (0.42), the floor of 0.45 kicks in and
    drops everything — correctly signalling "nothing is relevant."

    Recommended defaults based on empirical testing with mxbai-embed-large:
      min_top_ratio=0.7, min_score=0.45
    """

    def __init__(self, min_top_ratio: float = 0.7, min_score: float = 0.45) -> None:
        if not 0.0 <= min_top_ratio <= 1.0:
            raise ValueError(f"min_top_ratio must be between 0.0 and 1.0, got {min_top_ratio}")
        if not 0.0 <= min_score <= 1.0:
            raise ValueError(f"min_score must be between 0.0 and 1.0, got {min_score}")
        self.min_top_ratio = min_top_ratio
        self.min_score = min_score

    def filter_results(self, results: list[dict]) -> list[dict]:
        if not results:
            return []

        top_score = max(r.get("score", 0.0) for r in results)
        threshold = max(top_score * self.min_top_ratio, self.min_score)

        filtered = [r for r in results if r.get("score", 0.0) >= threshold]
        if filtered != results:
            logger.debug(
                "RatioFilter: %d/%d results kept (top=%.3f, threshold=%.3f, min_top_ratio=%.2f, min_score=%.2f)",
                len(filtered),
                len(results),
                top_score,
                threshold,
                self.min_top_ratio,
                self.min_score,
            )
        return filtered
