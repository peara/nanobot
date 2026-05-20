from __future__ import annotations

import pytest

from nanobot.web_scripts import NanoScriptValidationError, NanoScriptValidator
from nanobot.web_scripts.validator import SAFE_BUILTIN_NAMES


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


class TestUnavailableBuiltinRejection:
    def test_rejects_print(self) -> None:
        code = """
async def script(page, params):
    print("hello")
    return {}
"""
        with pytest.raises(NanoScriptValidationError, match="Name 'print' is not available"):
            NanoScriptValidator().validate(code)

    def test_rejects_abs(self) -> None:
        code = """
async def script(page, params):
    x = abs(-5)
    return {"x": x}
"""
        with pytest.raises(NanoScriptValidationError, match="Name 'abs' is not available"):
            NanoScriptValidator().validate(code)

    def test_rejects_keyerror(self) -> None:
        code = """
async def script(page, params):
    try:
        v = params["missing"]
    except KeyError:
        v = None
    return {"v": v}
"""
        with pytest.raises(NanoScriptValidationError, match="Name 'KeyError' is not available"):
            NanoScriptValidator().validate(code)

    def test_rejects_type(self) -> None:
        code = """
async def script(page, params):
    t = type(params)
    return {"t": str(t)}
"""
        with pytest.raises(NanoScriptValidationError, match="Name 'type' is not available"):
            NanoScriptValidator().validate(code)

    def test_rejects_round(self) -> None:
        code = """
async def script(page, params):
    x = round(3.14, 2)
    return {"x": x}
"""
        with pytest.raises(NanoScriptValidationError, match="Name 'round' is not available"):
            NanoScriptValidator().validate(code)

    def test_error_message_lists_allowed_builtins(self) -> None:
        code = """
async def script(page, params):
    x = round(3.14)
    return {"x": x}
"""
        with pytest.raises(NanoScriptValidationError) as exc_info:
            NanoScriptValidator().validate(code)
        msg = str(exc_info.value)
        for name in sorted(SAFE_BUILTIN_NAMES):
            assert name in msg, f"Expected {name} in error message"

    def test_allows_safe_builtins(self) -> None:
        code = """
async def script(page, params):
    items = [1, 2, 3]
    return {
        "len": len(items),
        "max": max(items),
        "min": min(items),
        "sum": sum(items),
        "sorted": sorted(items, reverse=True),
        "any": any(items),
        "all": all(items),
        "count": int(str(len(items))),
        "has_next": bool(items),
    }
"""
        result = NanoScriptValidator().validate(code)
        assert result.tree is not None

    def test_allows_locally_assigned_names(self) -> None:
        code = """
async def script(page, params):
    data = params.get("data", [])
    items = []
    for row in data:
        values = row.split(",")
        items.append({"values": values})
    return {"items": items}
"""
        result = NanoScriptValidator().validate(code)
        assert result.tree is not None

    def test_allows_loop_variable_as_name(self) -> None:
        code = """
async def script(page, params):
    rows = await page.locator("tr").all()
    items = []
    for row in rows:
        items.append({"row": row})
    return {"items": items}
"""
        result = NanoScriptValidator().validate(code)
        assert result.tree is not None

    def test_allows_import_names(self) -> None:
        code = """
import json
import re

async def script(page, params):
    text = await page.inner_text("body")
    links = re.findall(r'href="([^"]+)"', text)
    return {"links": links}
"""
        result = NanoScriptValidator().validate(code)
        assert result.tree is not None

    def test_allows_runtime_names_any_and_page(self) -> None:
        code = """
async def script(page: Page, params: dict[str, Any]) -> dict[str, Any]:
    return {}
"""
        result = NanoScriptValidator().validate(code)
        assert result.tree is not None

    def test_allows_exception_in_except_handler(self) -> None:
        code = """
async def script(page, params):
    try:
        await page.goto(params.get("url", ""))
    except Exception:
        return {"error": "navigation failed"}
    return {"ok": True}
"""
        result = NanoScriptValidator().validate(code)
        assert result.tree is not None

    def test_allows_comprehension_variable(self) -> None:
        code = """
async def script(page, params):
    rows = await page.locator("tr").all()
    return {"items": [row for row in rows]}
"""
        result = NanoScriptValidator().validate(code)
        assert result.tree is not None

    def test_custom_safe_builtins(self) -> None:
        code = """
async def script(page, params):
    x = abs(-5)
    return {"x": x}
"""
        validator = NanoScriptValidator(safe_builtins=frozenset({"abs"}))
        result = validator.validate(code)
        assert result.tree is not None
