from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from nanobot.mcp_tools.search_scripts import search_scripts
from nanobot.scripts.models import SearchCandidate


class _FakeRegistry:
    def search_scripts(self, query: str, params: dict[str, Any], limit: int) -> list[SearchCandidate]:
        del query, params, limit
        return [
            SearchCandidate(
                script_id="scr_1",
                version_id="ver_1",
                score=0.91,
                reason="semantic=0.90, params=0.00",
            )
        ]

    def get_script_version(self, script_id: str, version_id: str | None = None) -> Any:
        del script_id, version_id
        return SimpleNamespace(
            script_name="Extract GitHub Issues",
            description="Extract issue titles and links from a GitHub issues page.",
            domain="github.com",
            task_type="extraction",
            params_schema={
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string", "format": "uri"}},
            },
            output_schema={
                "type": "object",
                "required": ["issues"],
                "properties": {"issues": {"type": "array"}},
            },
        )


def test_search_scripts_returns_callable_contract() -> None:
    runtime = SimpleNamespace(registry=_FakeRegistry())

    payload = search_scripts(runtime, {"query": "github issues workflow", "params": {}, "limit": 5})

    candidate = payload["candidates"][0]
    assert candidate["script_id"] == "scr_1"
    assert candidate["version_id"] == "ver_1"
    assert candidate["name"] == "Extract GitHub Issues"
    assert candidate["params_schema"]["required"] == ["url"]
    assert candidate["output_schema"]["required"] == ["issues"]
    assert candidate["required_params"] == ["url"]
    assert candidate["missing_params"] == ["url"]
    assert candidate["invoke_example"] == {
        "tool": "web__invoke_script",
        "arguments": {
            "script_id": "scr_1",
            "version_id": "ver_1",
            "params": {"url": "https://example.com"},
        },
    }


def test_search_scripts_invoke_example_reuses_supplied_params() -> None:
    runtime = SimpleNamespace(registry=_FakeRegistry())

    payload = search_scripts(
        runtime,
        {
            "query": "github issues workflow",
            "params": {"url": "https://github.com/microsoft/playwright/issues"},
            "limit": 5,
        },
    )

    candidate = payload["candidates"][0]
    assert candidate["missing_params"] == []
    assert candidate["invoke_example"]["arguments"]["params"] == {
        "url": "https://github.com/microsoft/playwright/issues"
    }
