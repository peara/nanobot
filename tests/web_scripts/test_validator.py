from __future__ import annotations

import pytest

from nanobot.web_scripts import NanoScriptValidationError, NanoScriptValidator


def test_validator_accepts_async_extraction_script_with_control_flow() -> None:
    code = """
import json

async def script(page: Page, params: dict[str, Any]) -> dict[str, Any]:
    await page.goto(params["url"])
    items = []
    for row in await page.locator(".row").all():
        title = await row.inner_text()
        if title:
            items.append({"title": title})
    while False:
        items.append({"title": "never"})
    return {"items": [item for item in items], "metadata": {"count": len(items)}}
"""

    result = NanoScriptValidator().validate(code)

    assert result.tree is not None


@pytest.mark.parametrize(
    "code, message",
    [
        (
            """
def script(page, params):
    return {}
""",
            "async def",
        ),
        (
            """
import os

async def script(page, params):
    return {}
""",
            "Blocked import",
        ),
        (
            """
import urllib.request

async def script(page, params):
    return {}
""",
            "Import is not allowed",
        ),
        (
            """
async def script(page, params):
    return open("secret.txt").read()
""",
            "Blocked call",
        ),
        (
            """
async def script(page, params):
    return page.__class__
""",
            "Dunder attributes",
        ),
        (
            """
async def script(page, params):
    return ["not", "dict"]
""",
            "return a dict",
        ),
    ],
)
def test_validator_rejects_unsafe_or_invalid_scripts(code: str, message: str) -> None:
    with pytest.raises(NanoScriptValidationError, match=message):
        NanoScriptValidator().validate(code)
