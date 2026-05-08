from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.mcp_tools.repair_script import repair_script


class _FakeRepair:
    def __init__(self) -> None:
        self.last_call: dict[str, Any] | None = None

    async def repair(self, **kwargs: Any) -> dict[str, Any]:
        self.last_call = kwargs
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_repair_script_converts_javascript_like_code_to_python_fallback() -> None:
    fake_repair = _FakeRepair()
    runtime = SimpleNamespace(repair=fake_repair)
    payload = {
        "script_id": "scr_1",
        "failed_execution_id": "exe_1",
        "patched_code": "var x = 1;\nwhile (browser.loop_guard(10)) { x = x + 1; }\nreturn x;",
        "patched_selector_manifest": {"issue_link": {"selector": "a[data-hovercard-type='issue']"}},
    }

    result = await repair_script(runtime, payload)
    assert result["status"] == "ok"
    assert fake_repair.last_call is not None
    assert fake_repair.last_call["patched_code"].startswith("def script(browser, params):")
    assert fake_repair.last_call["patched_selector_manifest"]["issue_link"] == ["a[data-hovercard-type='issue']"]


@pytest.mark.asyncio
async def test_repair_script_normalizes_loop_guard_lambda() -> None:
    fake_repair = _FakeRepair()
    runtime = SimpleNamespace(repair=fake_repair)
    payload = {
        "script_id": "scr_1",
        "failed_execution_id": "exe_1",
        "patched_code": (
            "def script(browser, params):\n"
            "    while browser.loop_guard(lambda: True):\n"
            "        break\n"
            "    return {'issues': []}"
        ),
    }

    result = await repair_script(runtime, payload)
    assert result["status"] == "ok"
    assert fake_repair.last_call is not None
    assert 'browser.loop_guard("pagination", max_iterations=20)' in fake_repair.last_call["patched_code"]
