from __future__ import annotations

from unittest.mock import MagicMock

from nanobot.vector_store import COLLECTION_WEB_SCRIPTS, VectorStore
from nanobot.web_scripts import WebScript, WebScriptVectorStore


def test_web_script_vector_store_indexes_script_metadata() -> None:
    mock_vs = MagicMock(spec=VectorStore)
    mock_vs.add_text.return_value = "vector-123"

    index = WebScriptVectorStore(mock_vs)
    script = WebScript(
        id=7,
        name="github_issues_extract",
        description="Extract GitHub issue rows",
        code="async def script(page, params): return {}",
        params_schema={"required": ["url"]},
        result_schema={"properties": {"items": {"type": "array"}}},
        tags=["github", "issues"],
    )

    vector_id = index.store_script(script)

    assert vector_id == "vector-123"
    mock_vs.ensure_collection.assert_called_once_with(COLLECTION_WEB_SCRIPTS)
    mock_vs.delete_text.assert_called_once_with(COLLECTION_WEB_SCRIPTS, {"script_name": "github_issues_extract"})
    mock_vs.add_text.assert_called_once()
    assert mock_vs.add_text.call_args.args[0] == COLLECTION_WEB_SCRIPTS
    assert "Extract GitHub issue rows" in mock_vs.add_text.call_args.args[1]
    assert mock_vs.add_text.call_args.kwargs["metadata"]["script_name"] == "github_issues_extract"


def test_web_script_vector_store_search_returns_names() -> None:
    mock_vs = MagicMock(spec=VectorStore)
    mock_vs.search_text.return_value = [
        {"metadata": {"script_name": "github_issues_extract"}},
        {"metadata": {"script_name": "auction_extract"}},
    ]

    index = WebScriptVectorStore(mock_vs)

    assert index.search_scripts("github issues") == ["github_issues_extract", "auction_extract"]
    mock_vs.search_text.assert_called_once_with(COLLECTION_WEB_SCRIPTS, "github issues", limit=5)
