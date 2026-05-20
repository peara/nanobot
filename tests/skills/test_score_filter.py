from __future__ import annotations

import pytest

from nanobot.skills.score_filter import CutoffFilter, RatioFilter, ThresholdFilter


class TestThresholdFilter:
    def test_passes_all_results(self) -> None:
        f = ThresholdFilter()
        results = [
            {"id": "1", "score": 0.9, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.3, "metadata": {"skill_name": "b"}},
            {"id": "3", "score": 0.1, "metadata": {"skill_name": "c"}},
        ]
        assert f.filter_results(results) == results

    def test_passes_empty_list(self) -> None:
        f = ThresholdFilter()
        assert f.filter_results([]) == []

    def test_passes_single_result(self) -> None:
        f = ThresholdFilter()
        results = [{"id": "1", "score": 0.01, "metadata": {"skill_name": "a"}}]
        assert f.filter_results(results) == results


class TestCutoffFilter:
    def test_filters_below_threshold(self) -> None:
        f = CutoffFilter(min_score=0.5)
        results = [
            {"id": "1", "score": 0.9, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.5, "metadata": {"skill_name": "b"}},
            {"id": "3", "score": 0.49, "metadata": {"skill_name": "c"}},
            {"id": "4", "score": 0.1, "metadata": {"skill_name": "d"}},
        ]
        filtered = f.filter_results(results)
        assert len(filtered) == 2
        assert filtered[0]["metadata"]["skill_name"] == "a"
        assert filtered[1]["metadata"]["skill_name"] == "b"

    def test_filters_everything_when_all_below(self) -> None:
        f = CutoffFilter(min_score=0.8)
        results = [
            {"id": "1", "score": 0.3, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.5, "metadata": {"skill_name": "b"}},
        ]
        assert f.filter_results(results) == []

    def test_passes_everything_when_all_above(self) -> None:
        f = CutoffFilter(min_score=0.0)
        results = [
            {"id": "1", "score": 0.3, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.9, "metadata": {"skill_name": "b"}},
        ]
        assert f.filter_results(results) == results

    def test_empty_list(self) -> None:
        f = CutoffFilter(min_score=0.5)
        assert f.filter_results([]) == []

    def test_rejects_invalid_min_score(self) -> None:
        with pytest.raises(ValueError):
            CutoffFilter(min_score=-0.1)
        with pytest.raises(ValueError):
            CutoffFilter(min_score=1.5)

    def test_boundary_exact_threshold(self) -> None:
        f = CutoffFilter(min_score=0.5)
        results = [{"id": "1", "score": 0.5, "metadata": {"skill_name": "a"}}]
        assert len(f.filter_results(results)) == 1

    def test_missing_score_key(self) -> None:
        f = CutoffFilter(min_score=0.5)
        results = [
            {"id": "1", "metadata": {"skill_name": "a"}},
        ]
        assert f.filter_results(results) == []


class TestRatioFilter:
    def test_filters_by_ratio_and_floor(self) -> None:
        f = RatioFilter(min_top_ratio=0.7, min_score=0.45)
        results = [
            {"id": "1", "score": 0.80, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.56, "metadata": {"skill_name": "b"}},
            {"id": "3", "score": 0.44, "metadata": {"skill_name": "c"}},
            {"id": "4", "score": 0.30, "metadata": {"skill_name": "d"}},
        ]
        filtered = f.filter_results(results)
        names = [r["metadata"]["skill_name"] for r in filtered]
        assert names == ["a", "b"]

    def test_floor_kicks_in_when_top_is_low(self) -> None:
        f = RatioFilter(min_top_ratio=0.7, min_score=0.45)
        results = [
            {"id": "1", "score": 0.42, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.38, "metadata": {"skill_name": "b"}},
        ]
        filtered = f.filter_results(results)
        assert len(filtered) == 0

    def test_floor_allows_high_scoring_results(self) -> None:
        f = RatioFilter(min_top_ratio=0.7, min_score=0.45)
        results = [
            {"id": "1", "score": 0.75, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.53, "metadata": {"skill_name": "b"}},
        ]
        filtered = f.filter_results(results)
        assert len(filtered) == 2

    def test_ratio_strict_with_high_top(self) -> None:
        f = RatioFilter(min_top_ratio=0.7, min_score=0.45)
        results = [
            {"id": "1", "score": 0.90, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.63, "metadata": {"skill_name": "b"}},
            {"id": "3", "score": 0.44, "metadata": {"skill_name": "c"}},
        ]
        filtered = f.filter_results(results)
        names = [r["metadata"]["skill_name"] for r in filtered]
        assert names == ["a", "b"]

    def test_empty_list(self) -> None:
        f = RatioFilter()
        assert f.filter_results([]) == []

    def test_single_result_above_floor(self) -> None:
        f = RatioFilter(min_top_ratio=0.7, min_score=0.45)
        results = [{"id": "1", "score": 0.60, "metadata": {"skill_name": "a"}}]
        filtered = f.filter_results(results)
        assert len(filtered) == 1

    def test_single_result_below_floor(self) -> None:
        f = RatioFilter(min_top_ratio=0.7, min_score=0.45)
        results = [{"id": "1", "score": 0.40, "metadata": {"skill_name": "a"}}]
        filtered = f.filter_results(results)
        assert len(filtered) == 0

    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            RatioFilter(min_top_ratio=-0.1)
        with pytest.raises(ValueError):
            RatioFilter(min_top_ratio=1.5)
        with pytest.raises(ValueError):
            RatioFilter(min_score=-0.1)
        with pytest.raises(ValueError):
            RatioFilter(min_score=1.5)

    def test_ratio_one_keeps_only_top(self) -> None:
        f = RatioFilter(min_top_ratio=1.0, min_score=0.0)
        results = [
            {"id": "1", "score": 0.80, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.79, "metadata": {"skill_name": "b"}},
        ]
        filtered = f.filter_results(results)
        assert len(filtered) == 1
        assert filtered[0]["metadata"]["skill_name"] == "a"

    def test_zero_min_score_allows_all_above_ratio(self) -> None:
        f = RatioFilter(min_top_ratio=0.5, min_score=0.0)
        results = [
            {"id": "1", "score": 0.80, "metadata": {"skill_name": "a"}},
            {"id": "2", "score": 0.40, "metadata": {"skill_name": "b"}},
            {"id": "3", "score": 0.20, "metadata": {"skill_name": "c"}},
        ]
        filtered = f.filter_results(results)
        names = [r["metadata"]["skill_name"] for r in filtered]
        assert names == ["a", "b"]

    def test_missing_score_treated_as_zero(self) -> None:
        f = RatioFilter(min_top_ratio=0.7, min_score=0.45)
        results = [
            {"id": "1", "score": 0.90, "metadata": {"skill_name": "a"}},
            {"id": "2", "metadata": {"skill_name": "b"}},
        ]
        filtered = f.filter_results(results)
        assert len(filtered) == 1
        assert filtered[0]["metadata"]["skill_name"] == "a"
