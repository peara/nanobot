from __future__ import annotations

import logging

from nanobot.evaluator.runner import LearningEvaluator


class TestEvalLogger:
    """Tests for evaluator file logger."""

    def test_eval_logger_name(self) -> None:
        log = logging.getLogger("nanobot.evaluator.io")
        assert log.name == "nanobot.evaluator.io"

    def test_eval_logger_does_not_propagate_when_configured(self) -> None:
        log = logging.getLogger("nanobot.evaluator.io")
        assert isinstance(log, logging.Logger)


class TestSummarizeToolTrace:
    """Tests for LearningEvaluator._summarize_tool_trace."""

    def test_empty_trace(self) -> None:
        result = LearningEvaluator._summarize_tool_trace([])
        assert result == ""

    def test_basic_entry(self) -> None:
        trace = [
            {
                "name": "web__snapshot_page",
                "args": {"url": "https://example.com"},
                "result_preview": '{"ok": true, "title": "Example"}',
            }
        ]
        result = LearningEvaluator._summarize_tool_trace(trace)
        assert "web__snapshot_page" in result
        assert "https://example.com" in result
        assert '{"ok": true' in result

    def test_skips_scratchpad_write(self) -> None:
        trace = [
            {
                "name": "session__scratchpad_write",
                "args": {"mode": "append", "context": "The search URL returned 404"},
                "result_preview": '{"ok": true}',
            },
            {
                "name": "web__snapshot_page",
                "args": {"url": "https://example.com"},
                "result_preview": '{"ok": true}',
            },
        ]
        result = LearningEvaluator._summarize_tool_trace(trace)
        assert "scratchpad_write" not in result
        assert "web__snapshot_page" in result

    def test_full_args_not_truncated(self) -> None:
        long_url = "https://example.com/search?q=" + "a" * 200
        trace = [
            {
                "name": "web__snapshot_page",
                "args": {"url": long_url},
                "result_preview": "ok",
            }
        ]
        result = LearningEvaluator._summarize_tool_trace(trace)
        assert long_url in result

    def test_result_preview_200_chars(self) -> None:
        preview = "x" * 300
        trace = [
            {
                "name": "web__read_page",
                "args": {"url": "https://example.com"},
                "result_preview": preview,
            }
        ]
        result = LearningEvaluator._summarize_tool_trace(trace)
        # Should show first 200 chars of preview, not 60
        assert "x" * 200 in result
        # Should NOT show char 201
        assert "x" * 201 not in result

    def test_all_entries_shown_no_limit(self) -> None:
        trace = []
        for i in range(30):
            trace.append(
                {
                    "name": f"tool_{i}",
                    "args": {"key": f"val_{i}"},
                    "result_preview": f"result_{i}",
                }
            )
        result = LearningEvaluator._summarize_tool_trace(trace)
        # First entry should be present
        assert "tool_0" in result
        # Last entry should be present (no "and N more" truncation)
        assert "tool_29" in result
        assert "... and" not in result

    def test_failed_tool_shown(self) -> None:
        trace = [
            {
                "name": "web__interact_page",
                "args": {
                    "steps": [{"action": "click", "target": "button[type='submit']"}],
                    "url": "https://example.com",
                },
                "result_preview": '{"ok": false, "error": "Could not resolve target"}',
            }
        ]
        result = LearningEvaluator._summarize_tool_trace(trace)
        assert "Could not resolve target" in result
        assert "button[type='submit']" in result

    def test_multiline_preview_flattened(self) -> None:
        trace = [
            {
                "name": "web__snapshot_page",
                "args": {"url": "https://example.com"},
                "result_preview": "line1\nline2\nline3",
            }
        ]
        result = LearningEvaluator._summarize_tool_trace(trace)
        assert "\n" not in result.split("->")[1]


class TestSummarizeScratchpad:
    """Tests for LearningEvaluator._summarize_scratchpad."""

    def test_empty_scratchpad(self) -> None:
        result = LearningEvaluator._summarize_scratchpad({})
        assert result == ""

    def test_basic_fields(self) -> None:
        scratchpad = {
            "goal": "Find lenses on Yahoo Auctions",
            "current_step": "Searching for Minolta 58mm",
            "next_step": "Verify seller ratings",
        }
        result = LearningEvaluator._summarize_scratchpad(scratchpad)
        assert "goal:" in result
        assert "Find lenses on Yahoo Auctions" in result
        assert "current_step:" in result
        assert "next_step:" in result

    def test_context_included(self) -> None:
        scratchpad = {
            "context": "The direct search URL returned a 404. Trying alternative approach.",
        }
        result = LearningEvaluator._summarize_scratchpad(scratchpad)
        assert "context:" in result
        assert "404" in result

    def test_known_facts_all_shown(self) -> None:
        facts = [f"fact_{i}" for i in range(10)]
        scratchpad = {"known_facts": facts}
        result = LearningEvaluator._summarize_scratchpad(scratchpad)
        # All 10 facts should appear, not just 5
        for i in range(10):
            assert f"fact_{i}" in result
        assert "(10 items)" in result

    def test_tool_journal_all_shown(self) -> None:
        journal = [f"entry_{i}" for i in range(8)]
        scratchpad = {"tool_journal": journal}
        result = LearningEvaluator._summarize_scratchpad(scratchpad)
        # All 8 entries should appear, not just 5
        for i in range(8):
            assert f"entry_{i}" in result
        assert "(8 items)" in result

    def test_no_truncation_on_values(self) -> None:
        long_fact = "a" * 200
        scratchpad = {"known_facts": [long_fact]}
        result = LearningEvaluator._summarize_scratchpad(scratchpad)
        assert long_fact in result

    def test_tool_journal_full_entry(self) -> None:
        long_entry = "web__snapshot_page: https://auctions.yahoo.co.jp/search?page_not_found -> 404 Not Found"
        scratchpad = {"tool_journal": [long_entry]}
        result = LearningEvaluator._summarize_scratchpad(scratchpad)
        assert "page_not_found" in result
        assert "404 Not Found" in result

    def test_combined_fields(self) -> None:
        scratchpad = {
            "goal": "Find lenses",
            "context": "URL format changed from /search to /search/search",
            "current_step": "Using correct URL format",
            "next_step": "Verify listings",
            "known_facts": ["Yahoo Auctions URL changed", "search/search is the new format"],
            "tool_journal": [
                "web__snapshot_page: /search?p=... -> 404",
                "web__interact_page: /search/search?p=... -> success",
            ],
        }
        result = LearningEvaluator._summarize_scratchpad(scratchpad)
        assert "context:" in result
        assert "/search/search" in result
        assert "known_facts (2 items)" in result
        assert "tool_journal (2 items)" in result
