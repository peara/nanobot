from __future__ import annotations

import pytest

from nanobot.web_scripts import WebScriptStore

SCRIPT_CODE = """
async def script(page: Page, params: dict[str, Any]) -> dict[str, Any]:
    return {"items": [], "metadata": {"source": params.get("url")}}
"""


def test_store_create_get_and_schema_round_trip(tmp_path) -> None:
    store = WebScriptStore(str(tmp_path / "web_scripts.db"))

    script = store.create(
        name="github_issues_extract",
        description="Extract GitHub issue rows",
        code=SCRIPT_CODE,
        params_schema={"type": "object", "required": ["url"]},
        result_schema={"type": "object", "properties": {"items": {"type": "array"}}},
        tags=["github", "issues"],
    )

    loaded = store.get_by_name("github_issues_extract")

    assert loaded is not None
    assert loaded.id == script.id
    assert loaded.params_schema["required"] == ["url"]
    assert loaded.result_schema["properties"]["items"]["type"] == "array"
    assert loaded.tags == ["github", "issues"]


def test_store_duplicate_requires_overwrite(tmp_path) -> None:
    store = WebScriptStore(str(tmp_path / "web_scripts.db"))
    store.create(name="repo_extract", description="Extract repos", code=SCRIPT_CODE)

    with pytest.raises(ValueError, match="already exists"):
        store.create(name="repo_extract", description="Extract repos again", code=SCRIPT_CODE)

    updated = store.create(
        name="repo_extract",
        description="Extract repos again",
        code=SCRIPT_CODE,
        tags=["updated"],
        overwrite=True,
    )

    assert updated.description == "Extract repos again"
    assert updated.tags == ["updated"]


def test_store_search_fallback_matches_description_and_tags(tmp_path) -> None:
    store = WebScriptStore(str(tmp_path / "web_scripts.db"))
    store.create(name="github_issues_extract", description="Extract GitHub issue rows", code=SCRIPT_CODE)
    store.create(name="auction_extract", description="Extract auction listings", code=SCRIPT_CODE, tags=["market"])

    github_results = store.search("github")
    tag_results = store.search("market")

    assert [script.name for script in github_results] == ["github_issues_extract"]
    assert [script.name for script in tag_results] == ["auction_extract"]
