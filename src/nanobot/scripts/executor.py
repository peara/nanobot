from __future__ import annotations

import asyncio
import time
from typing import Any

from nanobot.browser.execution_budget import BudgetExceededError, ExecutionBudget
from nanobot.browser.safe_browser import SafeBrowser
from nanobot.browser.selector_resolver import SelectorResolver
from nanobot.scripts.models import ErrorType, ExecutionMetrics, ExecutionOutcome, RuntimeBudgetLimits
from nanobot.scripts.output_validator import validate_output
from nanobot.scripts.registry import ScriptRegistry
from nanobot.scripts.schemas import validate_data_against_schema
from nanobot.scripts.validator import NanoScriptAstValidator
from web_agent.browser.interactor import BrowserInteractor


class ScriptExecutor:
    def __init__(
        self,
        registry: ScriptRegistry,
        *,
        headless: bool = True,
        budget_limits: RuntimeBudgetLimits | None = None,
    ) -> None:
        self.registry = registry
        self.headless = headless
        self.budget_limits = budget_limits or RuntimeBudgetLimits()

    async def invoke(
        self,
        script_id: str,
        params: dict[str, Any],
        version_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.registry.get_script_version(script_id, version_id=version_id)
        if record is None:
            return {
                "status": "failed",
                "confidence": 0.0,
                "result": None,
                "error": {
                    "type": ErrorType.VERSION_NOT_FOUND if version_id else ErrorType.SCRIPT_NOT_FOUND,
                    "message": "Script/version not found",
                },
            }

        param_errors = validate_data_against_schema(params, record.params_schema)
        if param_errors:
            return self._persist_and_payload(
                script_id=record.script_id,
                version_id=record.version_id,
                params=params,
                outcome=ExecutionOutcome(
                    status="failed",
                    confidence=0.0,
                    result=None,
                    error_type=ErrorType.PARAMS_VALIDATION_ERROR,
                    error_message="; ".join(param_errors),
                ),
                duration_ms=0,
                metrics=ExecutionMetrics(),
            )

        ast_result = NanoScriptAstValidator().validate(record.code)
        if not ast_result.ok:
            return self._persist_and_payload(
                script_id=record.script_id,
                version_id=record.version_id,
                params=params,
                outcome=ExecutionOutcome(
                    status="failed",
                    confidence=0.0,
                    result=None,
                    error_type=ErrorType.AST_VALIDATION_ERROR,
                    error_message="; ".join(ast_result.errors),
                ),
                duration_ms=0,
                metrics=ExecutionMetrics(),
            )

        started = time.monotonic()
        traces: list[dict[str, Any]] = []
        budget = ExecutionBudget(self.budget_limits)

        def add_trace(step: dict[str, Any]) -> None:
            traces.append({"step_index": len(traces) + 1, **step})

        def selector_stats(selector_key: str, selector: str, success: bool) -> None:
            self.registry.update_selector_stat(record.script_id, selector_key, selector, success)

        try:
            async with BrowserInteractor(headless=self.headless) as interactor:
                loop = asyncio.get_running_loop()
                resolver = SelectorResolver(record.selector_manifest, budget, add_trace, selector_stats)

                def run_coro(coro: Any) -> Any:
                    future = asyncio.run_coroutine_threadsafe(coro, loop)
                    return future.result(timeout=self.budget_limits.max_execution_time_ms / 1000)

                safe_browser = SafeBrowser(interactor, resolver, budget, run_coro, add_trace)
                runner = _build_script_runner(record.code)
                raw_result = await asyncio.wait_for(
                    asyncio.to_thread(runner, safe_browser, params),
                    timeout=self.budget_limits.max_execution_time_ms / 1000,
                )
        except asyncio.TimeoutError:
            add_trace({"action": "timeout", "status": "failed", "error": ErrorType.TIMEOUT})
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._persist_and_payload(
                script_id=record.script_id,
                version_id=record.version_id,
                params=params,
                outcome=ExecutionOutcome(
                    status="failed",
                    confidence=0.0,
                    result=None,
                    error_type=ErrorType.TIMEOUT,
                    error_message="Execution timed out",
                    traces=[],
                ),
                duration_ms=duration_ms,
                metrics=ExecutionMetrics(
                    dom_query_count=budget.dom_queries,
                    page_count=budget.page_navigations,
                    click_count=budget.clicks,
                ),
                traces=traces,
            )
        except BudgetExceededError as exc:
            add_trace({"action": "budget_error", "status": "failed", "error": exc.error_type})
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._persist_and_payload(
                script_id=record.script_id,
                version_id=record.version_id,
                params=params,
                outcome=ExecutionOutcome(
                    status="failed",
                    confidence=0.0,
                    result=None,
                    error_type=exc.error_type,
                    error_message=str(exc),
                ),
                duration_ms=duration_ms,
                metrics=ExecutionMetrics(
                    dom_query_count=budget.dom_queries,
                    page_count=budget.page_navigations,
                    click_count=budget.clicks,
                ),
                traces=traces,
            )
        except Exception as exc:  # pylint: disable=broad-except
            add_trace({"action": "runtime_error", "status": "failed", "error": str(exc)})
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._persist_and_payload(
                script_id=record.script_id,
                version_id=record.version_id,
                params=params,
                outcome=ExecutionOutcome(
                    status="failed",
                    confidence=0.0,
                    result=None,
                    error_type=ErrorType.SCRIPT_RUNTIME_ERROR,
                    error_message=str(exc),
                ),
                duration_ms=duration_ms,
                metrics=ExecutionMetrics(
                    dom_query_count=budget.dom_queries,
                    page_count=budget.page_navigations,
                    click_count=budget.clicks,
                ),
                traces=traces,
            )

        if not isinstance(raw_result, dict):
            duration_ms = int((time.monotonic() - started) * 1000)
            return self._persist_and_payload(
                script_id=record.script_id,
                version_id=record.version_id,
                params=params,
                outcome=ExecutionOutcome(
                    status="failed",
                    confidence=0.0,
                    result=None,
                    error_type=ErrorType.OUTPUT_VALIDATION_ERROR,
                    error_message="script must return an object",
                ),
                duration_ms=duration_ms,
                metrics=ExecutionMetrics(
                    dom_query_count=budget.dom_queries,
                    page_count=budget.page_navigations,
                    click_count=budget.clicks,
                ),
                traces=traces,
            )

        output_item_count = _count_items(raw_result)
        if output_item_count > self.budget_limits.max_output_items:
            duration_ms = int((time.monotonic() - started) * 1000)
            add_trace(
                {
                    "action": "output_limit",
                    "status": "failed",
                    "error": ErrorType.OUTPUT_LIMIT_EXCEEDED,
                }
            )
            return self._persist_and_payload(
                script_id=record.script_id,
                version_id=record.version_id,
                params=params,
                outcome=ExecutionOutcome(
                    status="failed",
                    confidence=0.0,
                    result=None,
                    error_type=ErrorType.OUTPUT_LIMIT_EXCEEDED,
                    error_message="Output item count exceeded budget",
                ),
                duration_ms=duration_ms,
                metrics=ExecutionMetrics(
                    dom_query_count=budget.dom_queries,
                    page_count=budget.page_navigations,
                    click_count=budget.clicks,
                    output_item_count=output_item_count,
                ),
                traces=traces,
            )

        primary_selector_used = not any(
            trace.get("action") == "selector_fallback_attempt" and trace.get("status") == "success" for trace in traces
        )
        validation = validate_output(
            raw_result,
            record.output_schema,
            record.validation_rules,
            used_primary_selectors=primary_selector_used,
            historical_success_rate=self.registry.script_success_rate(record.script_id),
            recent_failure_rate=self.registry.recent_failure_rate(record.script_id),
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        if validation.schema_errors:
            status = "failed"
            error_type = ErrorType.OUTPUT_VALIDATION_ERROR
            error_message = "; ".join(validation.schema_errors)
        else:
            status = validation.status
            error_type = None
            error_message = None
            if validation.warnings:
                add_trace(
                    {
                        "action": "validation_warning",
                        "status": "warning",
                        "error": ",".join(validation.warnings),
                    }
                )

        outcome = ExecutionOutcome(
            status=status,
            confidence=validation.confidence,
            result=raw_result,
            error_type=error_type,
            error_message=error_message,
            warnings=validation.warnings,
        )

        return self._persist_and_payload(
            script_id=record.script_id,
            version_id=record.version_id,
            params=params,
            outcome=outcome,
            duration_ms=duration_ms,
            metrics=ExecutionMetrics(
                dom_query_count=budget.dom_queries,
                page_count=budget.page_navigations,
                click_count=budget.clicks,
                output_item_count=output_item_count,
            ),
            traces=traces,
        )

    def _persist_and_payload(
        self,
        *,
        script_id: str,
        version_id: str,
        params: dict[str, Any],
        outcome: ExecutionOutcome,
        duration_ms: int,
        metrics: ExecutionMetrics,
        traces: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        execution_id = self.registry.create_execution(
            script_id=script_id,
            version_id=version_id,
            params=params,
            status=outcome.status,
            result=outcome.result,
            error_type=outcome.error_type,
            error_message=outcome.error_message,
            duration_ms=duration_ms,
            dom_query_count=metrics.dom_query_count,
            page_count=metrics.page_count,
            click_count=metrics.click_count,
            output_item_count=metrics.output_item_count,
            confidence=outcome.confidence,
        )
        if traces:
            self.registry.save_execution_traces(execution_id, traces)

        return {
            "status": outcome.status,
            "confidence": outcome.confidence,
            "result": outcome.result,
            "execution_id": execution_id,
            "error": (
                {"type": outcome.error_type, "message": outcome.error_message}
                if outcome.error_type or outcome.error_message
                else None
            ),
            "warnings": outcome.warnings,
        }


def _build_script_runner(code: str):
    safe_builtins = {
        "len": len,
        "range": range,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "set": set,
        "enumerate": enumerate,
        "min": min,
        "max": max,
        "sum": sum,
    }
    globals_dict: dict[str, Any] = {"__builtins__": safe_builtins}
    locals_dict: dict[str, Any] = {}
    compiled = compile(code, "<nanoscript>", "exec")
    exec(compiled, globals_dict, locals_dict)
    fn = locals_dict.get("script")
    if fn is None:
        raise RuntimeError("No script() function found")

    def _runner(browser: Any, params: dict[str, Any]) -> dict[str, Any]:
        return fn(browser, params)

    return _runner


def _count_items(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return sum(_count_items(item) for item in value.values())
    return 0
