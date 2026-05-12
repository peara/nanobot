from __future__ import annotations

import asyncio
from typing import Any

from nanobot.mcp_servers.web import server

EXTRACTION_SCRIPT = """
async def script(page: Page, params: dict[str, Any]) -> dict[str, Any]:
    await page.goto(params["url"])
    return {
        "items": [{"title": "Example", "url": page.url}],
        "metadata": {"source": page.url}
    }
"""


def _reset_script_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEB_SCRIPT_DB_PATH", str(tmp_path / "web_scripts.db"))
    monkeypatch.delenv("WEB_SCRIPT_VECTOR_CONFIG", raising=False)
    monkeypatch.delenv("MEM0_CONFIG_PATH", raising=False)
    monkeypatch.setattr(server, "_script_store_cache", None)
    monkeypatch.setattr(server, "_script_vector_cache", None)


def test_create_script_persists_valid_script_without_answer_metadata(monkeypatch, tmp_path) -> None:
    _reset_script_state(monkeypatch, tmp_path)

    payload = server.create_script(
        name="github_issues_extract",
        description="Extract GitHub issue rows",
        code=EXTRACTION_SCRIPT,
        params_schema={"type": "object", "required": ["url"]},
        result_schema={"type": "object", "properties": {"items": {"type": "array"}}},
        tags=["github", "issues"],
    )

    assert payload["ok"] is True
    assert payload["vector_indexed"] is False
    assert "code" not in payload["script"]
    assert "answer_template" not in payload["script"]
    assert payload["script"]["params_schema"]["required"] == ["url"]
    assert payload["script"]["result_schema"]["properties"]["items"]["type"] == "array"


def test_create_script_rejects_unsafe_code(monkeypatch, tmp_path) -> None:
    _reset_script_state(monkeypatch, tmp_path)

    payload = server.create_script(
        name="unsafe",
        description="Unsafe extraction",
        code="""
import subprocess

async def script(page, params):
    return {}
""",
    )

    assert payload["ok"] is False
    assert payload["error"] == "invalid_script"


def test_create_script_rejects_response_template_schema(monkeypatch, tmp_path) -> None:
    _reset_script_state(monkeypatch, tmp_path)

    payload = server.create_script(
        name="bad_schema",
        description="Extract data",
        code=EXTRACTION_SCRIPT,
        result_schema={"type": "object", "properties": {"answer_template": {"type": "string"}}},
    )

    assert payload["ok"] is False
    assert payload["error"] == "invalid_script"


def test_create_script_duplicate_requires_overwrite(monkeypatch, tmp_path) -> None:
    _reset_script_state(monkeypatch, tmp_path)
    first = server.create_script(name="repo_extract", description="Extract repos", code=EXTRACTION_SCRIPT)
    duplicate = server.create_script(name="repo_extract", description="Extract repos again", code=EXTRACTION_SCRIPT)
    overwritten = server.create_script(
        name="repo_extract",
        description="Extract repos again",
        code=EXTRACTION_SCRIPT,
        overwrite=True,
    )

    assert first["ok"] is True
    assert duplicate["ok"] is False
    assert overwritten["ok"] is True
    assert overwritten["script"]["description"] == "Extract repos again"


def test_search_scripts_exposes_data_schemas_for_skill_boundary(monkeypatch, tmp_path) -> None:
    _reset_script_state(monkeypatch, tmp_path)
    server.create_script(
        name="github_issues_extract",
        description="Extract GitHub issue rows",
        code=EXTRACTION_SCRIPT,
        params_schema={"type": "object", "required": ["url"]},
        result_schema={"type": "object", "properties": {"items": {"type": "array"}}},
        tags=["github"],
    )

    payload = server.search_scripts("github issues")

    assert payload["ok"] is True
    assert payload["used_vector"] is False
    assert payload["scripts"][0]["name"] == "github_issues_extract"
    assert "code" not in payload["scripts"][0]
    assert "params_schema" in payload["scripts"][0]
    assert "result_schema" in payload["scripts"][0]
    assert "answer_template" not in payload["scripts"][0]


def test_create_script_succeeds_when_vector_config_is_unavailable(monkeypatch, tmp_path) -> None:
    _reset_script_state(monkeypatch, tmp_path)
    monkeypatch.setenv("WEB_SCRIPT_VECTOR_CONFIG", str(tmp_path / "missing-vector.yaml"))

    payload = server.create_script(name="repo_extract", description="Extract repos", code=EXTRACTION_SCRIPT)

    assert payload["ok"] is True
    assert payload["vector_indexed"] is False


def test_invoke_script_returns_structured_data_only(monkeypatch, tmp_path) -> None:
    import nanobot.web_scripts.runner as runner_mod

    _reset_script_state(monkeypatch, tmp_path)
    server.create_script(name="github_issues_extract", description="Extract GitHub issue rows", code=EXTRACTION_SCRIPT)

    class _FakePage:
        url = "about:blank"

        async def goto(self, url: str) -> None:
            self.url = url

    class _FakeBrowser:
        def __init__(self, headless: bool = True) -> None:
            self.headless = headless
            self.page = _FakePage()

        async def __aenter__(self) -> "_FakeBrowser":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    monkeypatch.setattr(runner_mod, "BrowserInteractor", _FakeBrowser)

    payload = asyncio.run(server.invoke_script("github_issues_extract", params={"url": "https://example.com/issues"}))

    assert payload["ok"] is True
    assert payload["script"] == "github_issues_extract"
    assert payload["data"]["items"][0]["url"] == "https://example.com/issues"
    assert payload["metadata"]["final_url"] == "https://example.com/issues"
    assert "answer" not in payload
    assert "summary" not in payload


def test_invoke_script_rejects_response_oriented_result_keys(monkeypatch, tmp_path) -> None:
    import nanobot.web_scripts.runner as runner_mod

    _reset_script_state(monkeypatch, tmp_path)
    server.create_script(
        name="bad_boundary",
        description="Returns answer text instead of data",
        code="""
async def script(page: Page, params: dict[str, Any]) -> dict[str, Any]:
    return {"summary": "Natural language belongs in skills"}
""",
    )

    class _FakeBrowser:
        page = type("_FakePage", (), {"url": "about:blank"})()

        def __init__(self, headless: bool = True) -> None:
            self.headless = headless

        async def __aenter__(self) -> "_FakeBrowser":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    monkeypatch.setattr(runner_mod, "BrowserInteractor", _FakeBrowser)

    payload = asyncio.run(server.invoke_script("bad_boundary"))

    assert payload["ok"] is False
    assert payload["error"] == "invalid_result"


def test_invoke_script_timeout_returns_structured_failure(monkeypatch, tmp_path) -> None:
    import nanobot.web_scripts.runner as runner_mod

    _reset_script_state(monkeypatch, tmp_path)
    server.create_script(
        name="slow_extract",
        description="Slow extraction",
        code="""
async def script(page: Page, params: dict[str, Any]) -> dict[str, Any]:
    await page.wait_forever()
    return {"items": []}
""",
    )

    class _FakePage:
        url = "about:blank"

        async def wait_forever(self) -> None:
            await asyncio.sleep(1)

    class _FakeBrowser:
        def __init__(self, headless: bool = True) -> None:
            self.headless = headless
            self.page = _FakePage()

        async def __aenter__(self) -> "_FakeBrowser":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    monkeypatch.setattr(runner_mod, "BrowserInteractor", _FakeBrowser)

    payload = asyncio.run(server.invoke_script("slow_extract", timeout_seconds=0))

    assert payload["ok"] is False
    assert payload["error"] == "timeout"
