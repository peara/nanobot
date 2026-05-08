from __future__ import annotations

from dataclasses import dataclass

from nanobot.scripts.executor import ScriptExecutor
from nanobot.scripts.registry import ScriptRegistry
from nanobot.scripts.repair import ScriptRepairService


@dataclass
class NanoScriptRuntime:
    registry: ScriptRegistry
    executor: ScriptExecutor
    repair: ScriptRepairService


_RUNTIMES: dict[tuple[str, bool], NanoScriptRuntime] = {}


def get_runtime(db_path: str, headless: bool) -> NanoScriptRuntime:
    key = (db_path, headless)
    runtime = _RUNTIMES.get(key)
    if runtime is not None:
        return runtime

    registry = ScriptRegistry(db_path)
    executor = ScriptExecutor(registry, headless=headless)
    repair = ScriptRepairService(registry, executor)
    runtime = NanoScriptRuntime(registry=registry, executor=executor, repair=repair)
    _RUNTIMES[key] = runtime
    return runtime
