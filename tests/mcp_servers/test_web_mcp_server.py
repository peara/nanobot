from __future__ import annotations

import asyncio
from typing import Any

from nanobot.mcp_servers.web import server


def test_web_health_reports_defaults_and_capabilities() -> None:
    payload = server.web_health()

    assert payload["ok"] is True
    assert "defaults" in payload
    assert "capabilities" in payload
    assert "headless" in payload["defaults"]
    assert "read_ready" in payload["capabilities"]


def test_read_page_uses_tool_and_persists_outputs(monkeypatch) -> None:
    events: dict[str, Any] = {}

    class _FakeTool:
        async def read(self, url: str) -> dict[str, Any]:
            return {
                "ok": True,
                "url": url,
                "title": "Example",
                "markdown": "# Example",
            }

    def _fake_build_tool(*, quality_threshold: float | None, headless: bool | None) -> _FakeTool:
        events["build"] = {"quality_threshold": quality_threshold, "headless": headless}
        return _FakeTool()

    def _fake_save_result_payload(command: str, url: str, payload: dict[str, Any]) -> dict[str, str]:
        events["save"] = {"command": command, "url": url, "payload": payload}
        return {"json_path": "/tmp/read.json", "markdown_path": "/tmp/read.md"}

    monkeypatch.setattr(server, "_build_tool", _fake_build_tool)
    monkeypatch.setattr(server, "save_result_payload", _fake_save_result_payload)

    payload = asyncio.run(
        server.read_page(
            "https://example.com",
            quality_threshold=0.7,
            headless=False,
            save_outputs=True,
        )
    )

    assert payload["ok"] is True
    assert payload["saved_outputs"]["json_path"] == "/tmp/read.json"
    assert events["build"] == {"quality_threshold": 0.7, "headless": False}
    assert events["save"]["command"] == "read"


def test_search_google_web_returns_search_payload(monkeypatch) -> None:
    async def _fake_search_google(query: str, *, limit: int, language: str) -> dict[str, Any]:
        return {
            "ok": True,
            "query": query,
            "results": [{"title": "Example", "url": "https://example.com", "snippet": "result"}],
            "result_count": 1,
            "language": language,
            "limit": limit,
        }

    monkeypatch.setattr(server, "search_google", _fake_search_google)

    payload = asyncio.run(server.search_google_web("gia xang viet nam", limit=3, language="vi"))

    assert payload["ok"] is True
    assert payload["query"] == "gia xang viet nam"
    assert payload["results"][0]["url"] == "https://example.com"


def test_search_web_returns_search_payload(monkeypatch) -> None:
    async def _fake_search_web(
        query: str,
        *,
        limit: int,
        language: str,
        domains: list[str] | None,
        freshness: str | None,
        provider: str,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "query": query,
            "provider": provider,
            "results": [{"title": "Example", "url": "https://example.com", "snippet": "result"}],
            "result_count": 1,
            "language": language,
            "limit": limit,
            "domains": domains,
            "freshness": freshness,
        }

    monkeypatch.setattr(server, "search_web_impl", _fake_search_web)

    payload = asyncio.run(
        server.search_web(
            "gia vang viet nam",
            limit=4,
            language="vi",
            domains=["sjc.com.vn"],
            freshness="day",
            provider="exa",
        )
    )

    assert payload["ok"] is True
    assert payload["query"] == "gia vang viet nam"
    assert payload["provider"] == "exa"
    assert payload["results"][0]["url"] == "https://example.com"


def test_snapshot_page_normalizes_payload_without_ok(monkeypatch) -> None:
    class _FakeTool:
        async def snapshot(self, url: str) -> dict[str, Any]:
            return {
                "url": url,
                "title": "Snapshot",
                "visible_text": "Visible",
            }

    monkeypatch.setattr(server, "_build_tool", lambda **_: _FakeTool())

    payload = asyncio.run(server.snapshot_page("https://example.com"))

    assert payload["ok"] is True
    assert payload["title"] == "Snapshot"


def test_interact_page_returns_failure_payload_on_exception(monkeypatch) -> None:
    class _FakeTool:
        async def interact(self, url: str, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            del url, steps
            raise RuntimeError("boom")

    monkeypatch.setattr(server, "_build_tool", lambda **_: _FakeTool())

    payload = asyncio.run(server.interact_page("https://example.com", steps=[{"action": "click", "target": "next"}]))

    assert payload["ok"] is False
    assert payload["error"] == "tool_failed"
    assert "boom" in payload["message"]


def test_domain_chrome_returns_stored_baseline() -> None:
    server._chrome_cache.clear()
    server._chrome_cache.split_chrome(
        "example.com",
        [{"title": "Home", "link": "https://example.com/"}],
        [{"text": "Login", "href": "https://example.com/login"}],
    )

    payload = server.domain_chrome(domain="example.com")

    assert payload["ok"] is True
    assert payload["domain"] == "example.com"
    assert len(payload["items"]) == 1
    assert len(payload["links"]) == 1
    server._chrome_cache.clear()


def test_domain_chrome_returns_not_found_for_unknown_domain() -> None:
    server._chrome_cache.clear()

    payload = server.domain_chrome(domain="unknown.com")

    assert payload["ok"] is False
    assert payload["error"] == "not_found"
    server._chrome_cache.clear()


def test_interact_page_applies_chrome_dedup(monkeypatch) -> None:
    server._chrome_cache.clear()

    class _FakeTool:
        def __init__(self, **kwargs: Any) -> None:
            self.chrome_cache = kwargs.get("chrome_cache")

        async def interact(self, url: str, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            from web_agent.models import ExtractionResult, FetchResult, FlowState
            from web_agent.service import WebAgentTool

            result = ExtractionResult(
                phase="extract",
                strategy="listing",
                page_type="listing",
                content="Product details",
                markdown="# Product",
                items=[
                    {"title": "Home", "link": "https://example.com/", "description": ""},
                    {"title": "Product A", "link": "https://example.com/p1", "description": "Great"},
                ],
                links=[
                    {"text": "Home", "href": "https://example.com/"},
                    {"text": "Login", "href": "https://example.com/login"},
                    {"text": "Buy", "href": "https://example.com/buy"},
                ],
                visible_text="Product details",
                quality_score=0.9,
                decision="accept",
                notes=[],
            )
            flow = FlowState(
                fetch=FetchResult(
                    strategy="browser_seed",
                    url=url,
                    final_url=url,
                    status_code=200,
                    html="",
                    title="Product Page",
                    used_browser=True,
                    weak_content=False,
                    errors=[],
                ),
                page_type="listing",
                steps=[],
                best_result=result,
                fallback_used=False,
            )
            real_tool = WebAgentTool(chrome_cache=self.chrome_cache)
            return real_tool._final_payload(flow, actions_taken=[])

    def _fake_build_tool(**kwargs: Any) -> _FakeTool:
        kwargs["chrome_cache"] = server._chrome_cache
        return _FakeTool(**kwargs)

    monkeypatch.setattr(server, "_build_tool", _fake_build_tool)

    payload1 = asyncio.run(server.interact_page("https://example.com/page1"))
    assert "chrome_omitted" not in payload1

    payload2 = asyncio.run(server.interact_page("https://example.com/page2"))
    assert "chrome_omitted" in payload2
    assert payload2["chrome_omitted"]["domain"] == "example.com"
    assert payload2["chrome_omitted"]["items"] == 2
    assert payload2["chrome_omitted"]["links"] == 3

    server._chrome_cache.clear()


def test_create_script_tool(monkeypatch) -> None:
    monkeypatch.setattr(server, "_build_nanoscript_runtime", lambda **_: object())
    monkeypatch.setattr(
        server,
        "create_script_impl",
        lambda runtime, payload: {
            "status": "created",
            "script_id": "scr_1",
            "version_id": "ver_1",
            "name": payload["name"],
            "runtime": runtime is not None,
        },
    )
    payload = server.create_script(
        name="Extract Issues",
        description="desc",
        code="def script(browser, params):\n    return {}",
        params_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        selector_manifest={},
        embedding_text="text",
        created_by="llm",
    )
    assert payload["status"] == "created"
    assert payload["script_id"] == "scr_1"


def test_create_script_tool_defaults_optional_payload_fields(monkeypatch) -> None:
    monkeypatch.setattr(server, "_build_nanoscript_runtime", lambda **_: object())
    monkeypatch.setattr(
        server,
        "create_script_impl",
        lambda runtime, payload: {
            "status": "created",
            "params_schema": payload["params_schema"],
            "output_schema": payload["output_schema"],
            "selector_manifest": payload["selector_manifest"],
            "embedding_text": payload["embedding_text"],
            "created_by": payload["created_by"],
            "runtime": runtime is not None,
        },
    )
    payload = server.create_script(
        name="Extract Issues",
        description="desc",
        code="def script(browser, params):\n    return {}",
    )
    assert payload["status"] == "created"
    assert payload["params_schema"]["type"] == "object"
    assert payload["output_schema"]["type"] == "object"
    assert "issue_row" in payload["selector_manifest"]
    assert payload["embedding_text"] == "Extract Issues: desc"
    assert payload["created_by"] == "llm"


def test_search_scripts_tool(monkeypatch) -> None:
    monkeypatch.setattr(server, "_build_nanoscript_runtime", lambda **_: object())
    monkeypatch.setattr(
        server,
        "search_scripts_impl",
        lambda runtime, payload: {
            "runtime": runtime is not None,
            "candidates": [{"script_id": "scr_1", "version_id": "ver_1", "score": 0.9}],
            "query": payload["query"],
        },
    )
    payload = server.search_scripts("github issues", params={"url": "https://github.com/org/repo/issues"}, limit=3)
    assert payload["candidates"][0]["script_id"] == "scr_1"
    assert payload["query"] == "github issues"


def test_invoke_test_and_repair_tools(monkeypatch) -> None:
    monkeypatch.setattr(server, "_build_nanoscript_runtime", lambda **_: object())

    async def _fake_invoke(runtime, payload):
        del runtime, payload
        return {"status": "success", "confidence": 0.9, "result": {"ok": True}, "execution_id": "exe_1", "error": None}

    async def _fake_test(runtime, payload):
        del runtime, payload
        return {"status": "passed", "cases": [{"status": "success", "confidence": 0.9, "error": None}]}

    async def _fake_repair(runtime, payload):
        del runtime, payload
        return {"status": "candidate_created", "new_version_id": "ver_2", "promoted": False}

    monkeypatch.setattr(server, "invoke_script_impl", _fake_invoke)
    monkeypatch.setattr(server, "test_script_impl", _fake_test)
    monkeypatch.setattr(server, "repair_script_impl", _fake_repair)

    invoke_payload = asyncio.run(server.invoke_script("scr_1", {"url": "https://example.com"}))
    assert invoke_payload["status"] == "success"

    test_payload = asyncio.run(server.test_script("scr_1", "ver_1", [{"params": {"url": "https://example.com"}}]))
    assert test_payload["status"] == "passed"

    repair_payload = asyncio.run(
        server.repair_script("scr_1", "exe_1", "def script(browser, params):\n    return {}", test_cases=[])
    )
    assert repair_payload["status"] == "candidate_created"
