from __future__ import annotations

import asyncio
import inspect
import json
import time
from types import MappingProxyType
from typing import Any

from playwright.async_api import Page

from nanobot.web_scripts.models import WebScript
from nanobot.web_scripts.validator import NanoScriptValidationError, NanoScriptValidator
from web_agent.browser import BrowserInteractor

SAFE_BUILTINS = MappingProxyType(
    {
        "__import__": None,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "Exception": Exception,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
    }
)
RESERVED_RESPONSE_KEYS = {"answer", "answer_template", "message_to_user", "summary"}


class NanoScriptRuntimeError(RuntimeError):
    pass


class NanoScriptInvalidResultError(ValueError):
    pass


class NanoScriptRunner:
    def __init__(self, *, headless: bool = True, validator: NanoScriptValidator | None = None) -> None:
        self.headless = headless
        self._validator = validator if validator is not None else NanoScriptValidator()

    async def run(
        self,
        script: WebScript,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        started = time.monotonic()

        async def _run_inner() -> dict[str, Any]:
            script_func = self._compile(script.code)
            async with BrowserInteractor(headless=self.headless) as browser:
                if browser.page is None:
                    raise NanoScriptRuntimeError("Browser page was not initialized")
                result = await script_func(browser.page, dict(params or {}))
                self._validate_result(result)
                final_url = browser.page.url if browser.page is not None else ""
                return {
                    "ok": True,
                    "script": script.name,
                    "data": result,
                    "metadata": {
                        "final_url": final_url,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                }

        return await asyncio.wait_for(_run_inner(), timeout=timeout_seconds)

    def _compile(self, code: str) -> Any:
        validation = self._validator.validate(code)
        namespace: dict[str, Any] = {
            "__builtins__": dict(SAFE_BUILTINS) | {"__import__": self._safe_import},
            "Any": Any,
            "Page": Page,
        }
        exec(compile(validation.tree, "<nanobot-web-script>", "exec"), namespace)
        script_func = namespace.get("script")
        if script_func is None or not inspect.iscoroutinefunction(script_func):
            raise NanoScriptValidationError("NanoScript requires async def script(page, params)")
        return script_func

    def _safe_import(
        self,
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        del globals, locals
        if level:
            raise ImportError("Relative imports are not allowed")
        if name not in {"__future__", "datetime", "json", "re", "typing", "urllib.parse"}:
            raise ImportError(f"Import is not allowed: {name}")
        return __import__(name, globals=None, locals=None, fromlist=fromlist, level=0)

    def _validate_result(self, result: Any) -> None:
        if not isinstance(result, dict):
            raise NanoScriptInvalidResultError("script must return a dict")
        reserved_keys = RESERVED_RESPONSE_KEYS.intersection(result)
        if reserved_keys:
            raise NanoScriptInvalidResultError(
                "script returned response-oriented keys; scripts must return extracted data only: "
                + ", ".join(sorted(reserved_keys))
            )
        try:
            json.dumps(result, ensure_ascii=True)
        except TypeError as exc:
            raise NanoScriptInvalidResultError("script result must be JSON-serializable") from exc
