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

MANIFEST = {"issue_row": [".row"], "issue_title": ["a.title"], "next_button": ["a.next"]}


def test_versioning_candidate_promote_and_rollback(tmp_path) -> None:
    db_path = str(tmp_path / "scripts.db")
    registry = ScriptRegistry(db_path)

    script_id, v1 = registry.create_script(
        name="Extract Issues",
        description="Extract issues",
        domain="github.com",
        task_type="extraction",
        code=CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        embedding_text="github issues extraction",
        created_by="test",
    )

    current = registry.get_script_version(script_id)
    assert current is not None
    assert current.version_id == v1

    v2 = registry.create_candidate_version(
        script_id,
        code=CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        changelog="candidate",
        created_by="repair",
    )

    current_after_candidate = registry.get_script_version(script_id)
    assert current_after_candidate is not None
    assert current_after_candidate.version_id == v1

    registry.mark_version_failed(v2)
    current_after_failed = registry.get_script_version(script_id)
    assert current_after_failed is not None
    assert current_after_failed.version_id == v1

    v3 = registry.create_candidate_version(
        script_id,
        code=CODE,
        params_schema=PARAMS_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        selector_manifest=MANIFEST,
        validation_rules=[],
        changelog="candidate2",
        created_by="repair",
    )
    registry.promote_version(script_id, v3)

    current_after_promote = registry.get_script_version(script_id)
    assert current_after_promote is not None
    assert current_after_promote.version_id == v3

    registry.create_execution(
        script_id=script_id,
        version_id=v1,
        params={"url": "https://github.com/org/repo/issues"},
        status="success",
        result={"issues": [{"title": "a", "url": "https://example.com"}]},
        error_type=None,
        error_message=None,
        duration_ms=1,
        dom_query_count=1,
        page_count=1,
        click_count=0,
        output_item_count=1,
        confidence=0.9,
    )
    registry.create_execution(
        script_id=script_id,
        version_id=v3,
        params={"url": "https://github.com/org/repo/issues"},
        status="failed",
        result=None,
        error_type="SCRIPT_RUNTIME_ERROR",
        error_message="boom",
        duration_ms=1,
        dom_query_count=1,
        page_count=1,
        click_count=0,
        output_item_count=0,
        confidence=0.0,
    )

    rollback_version = registry.rollback_to_best(script_id)
    assert rollback_version == v1

    current_after_rollback = registry.get_script_version(script_id)
    assert current_after_rollback is not None
    assert current_after_rollback.version_id == v1
