from __future__ import annotations

from nanobot.scripts.registry import ScriptRegistry

CODE = """
def script(browser, params):
    browser.goto(params[\"url\"])
    return {\"issues\": []}
"""

PARAMS_SCHEMA = {
    "type": "object",
    "required": ["url"],
    "properties": {"url": {"type": "string"}},
}

OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["issues"],
    "properties": {"issues": {"type": "array", "items": {"type": "object"}}},
}

MANIFEST = {"issue_row": [".row"]}


def test_search_returns_relevant_scripts_with_scores(tmp_path) -> None:
    registry = ScriptRegistry(str(tmp_path / "scripts.db"))

    script_id, version_id = registry.create_script(
        name="Extract GitHub Issues",
        description="Extract issues with pagination",
        domain="github.com",
        task_type="extraction",
        code=CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        embedding_text="get issues from github repository with pagination",
        created_by="test",
    )

    candidates = registry.search_scripts(
        "get issues from github repository",
        {"url": "https://github.com/org/repo/issues"},
        limit=5,
    )

    assert candidates
    assert candidates[0].script_id == script_id
    assert candidates[0].version_id == version_id
    assert candidates[0].score > 0.0
