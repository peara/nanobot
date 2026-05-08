from __future__ import annotations

from typing import Any

from nanobot.scripts.executor import ScriptExecutor
from nanobot.scripts.models import ErrorType
from nanobot.scripts.registry import ScriptRegistry
from nanobot.scripts.validator import NanoScriptAstValidator


class ScriptRepairService:
    def __init__(self, registry: ScriptRegistry, executor: ScriptExecutor) -> None:
        self.registry = registry
        self.executor = executor

    async def repair(
        self,
        *,
        script_id: str,
        failed_execution_id: str,
        patched_code: str,
        patched_selector_manifest: dict[str, list[str]] | None,
        changelog: str,
        test_cases: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        failed_execution = self.registry.get_execution(failed_execution_id)
        if failed_execution is None:
            return {
                "status": "failed",
                "new_version_id": None,
                "promoted": False,
                "error": {"type": ErrorType.REPAIR_FAILED, "message": "failed_execution_id not found"},
            }

        current = self.registry.get_script_version(script_id)
        if current is None:
            return {
                "status": "failed",
                "new_version_id": None,
                "promoted": False,
                "error": {"type": ErrorType.SCRIPT_NOT_FOUND, "message": "script not found"},
            }

        ast_result = NanoScriptAstValidator().validate(patched_code)
        if not ast_result.ok:
            return {
                "status": "failed",
                "new_version_id": None,
                "promoted": False,
                "error": {
                    "type": ErrorType.AST_VALIDATION_ERROR,
                    "message": "; ".join(ast_result.errors),
                },
            }

        candidate_id = self.registry.create_candidate_version(
            script_id,
            code=patched_code,
            params_schema=current.params_schema,
            output_schema=current.output_schema,
            selector_manifest=patched_selector_manifest or current.selector_manifest,
            validation_rules=current.validation_rules,
            changelog=changelog,
            created_by="repair",
        )

        cases = test_cases or [{"params": failed_execution["params"]}]
        case_results: list[dict[str, Any]] = []
        for case in cases:
            payload = await self.executor.invoke(script_id, case.get("params", {}), version_id=candidate_id)
            case_results.append(payload)

        passed = all(case.get("status") != "failed" for case in case_results)
        avg_confidence = sum(float(case.get("confidence", 0.0)) for case in case_results) / max(1, len(case_results))

        old_confidence = float(failed_execution.get("confidence") or 0.0)
        if passed and avg_confidence >= max(0.6, old_confidence):
            self.registry.promote_version(script_id, candidate_id)
            return {
                "status": "promoted",
                "new_version_id": candidate_id,
                "promoted": True,
                "cases": case_results,
            }

        self.registry.mark_version_failed(candidate_id, status="failed")
        return {
            "status": "failed",
            "new_version_id": candidate_id,
            "promoted": False,
            "cases": case_results,
        }
