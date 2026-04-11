from __future__ import annotations

from web_agent.models import ExtractionResult, FetchResult, FlowState
from web_agent.service import WebAgentTool


def _build_result(*, page_type: str, content: str, title: str | None = None) -> ExtractionResult:
    resolved_title = title or "Example Title"
    return ExtractionResult(
        phase="extract",
        strategy="readability",
        page_type=page_type,
        content=content,
        markdown=f"# {resolved_title}\n\n## Content\n{content}",
        items=[],
        links=[],
        visible_text=content,
        quality_score=0.8,
        decision="accept",
        notes=[],
    )


def test_final_payload_marks_404_as_not_found() -> None:
    tool = WebAgentTool()
    flow = FlowState(
        fetch=FetchResult(
            strategy="httpx",
            url="https://example.com/missing",
            final_url="https://example.com/404.html",
            status_code=404,
            html="",
            title="404 Not Found",
            used_browser=False,
            weak_content=True,
            errors=[],
        ),
        page_type="unknown",
        steps=[],
        best_result=_build_result(page_type="listing", content="404 Not Found"),
        fallback_used=False,
    )

    payload = tool._final_payload(flow, actions_taken=[])

    assert payload["ok"] is False
    assert payload["error"] == "page_not_found"
    assert "http_status_404" in payload["warnings"]


def test_final_payload_marks_redirect_mismatch_as_failure() -> None:
    tool = WebAgentTool()
    content = "Ho tro khach hang\nhttps://example.com/support\nThong tin cua hang"
    flow = FlowState(
        fetch=FetchResult(
            strategy="httpx",
            url="https://example.com/gia-ban-le-xang-dau.html",
            final_url="https://example.com/thong-tin-khach-hang.html",
            status_code=200,
            html="",
            title="Thong tin ho tro khach hang",
            used_browser=False,
            weak_content=False,
            errors=[],
        ),
        page_type="listing",
        steps=[],
        best_result=_build_result(page_type="listing", content=content, title="Thong tin ho tro khach hang"),
        fallback_used=False,
    )

    payload = tool._final_payload(flow, actions_taken=[])

    assert payload["ok"] is False
    assert payload["error"] == "redirect_mismatch"
    assert "redirect_mismatch" in payload["warnings"]


def test_final_payload_marks_navigation_heavy_article_mismatch() -> None:
    tool = WebAgentTool()
    content = "\n".join(
        [
            "Gioi thieu",
            "https://example.com/gioi-thieu",
            "Linh vuc hoat dong",
            "https://example.com/linh-vuc-hoat-dong",
            "Kinh doanh xang dau",
            "https://example.com/kinh-doanh-xang-dau",
            "Van tai xang dau",
            "https://example.com/van-tai-xang-dau",
            "Nha dau tu",
            "https://example.com/nha-dau-tu",
        ]
    )
    flow = FlowState(
        fetch=FetchResult(
            strategy="httpx",
            url="https://example.com/petrolimex-dieu-chinh-gia-xang-dau-tu-15-gio-30-phut-ngay-09-4-2026.html",
            final_url="https://example.com/petrolimex-dieu-chinh-gia-xang-dau-tu-15-gio-30-phut-ngay-09-4-2026.html",
            status_code=200,
            html="",
            title="Petrolimex dieu chinh gia xang dau",
            used_browser=False,
            weak_content=False,
            errors=[],
        ),
        page_type="listing",
        steps=[],
        best_result=_build_result(page_type="listing", content=content, title="Petrolimex dieu chinh gia xang dau"),
        fallback_used=False,
    )

    payload = tool._final_payload(flow, actions_taken=[])

    assert payload["ok"] is False
    assert payload["error"] == "content_not_relevant"
    assert "navigation_heavy_listing" in payload["warnings"]
