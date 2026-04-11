from __future__ import annotations

import importlib.util

PACKAGE_NAMES = {
    "bs4": "beautifulsoup4",
    "crawl4ai": "crawl4ai",
    "httpx": "httpx",
    "playwright": "playwright",
    "readability": "readability-lxml",
    "selectolax": "selectolax",
    "trafilatura": "trafilatura",
}

READ_REQUIRED = ("bs4", "httpx")
SNAPSHOT_REQUIRED = ("playwright",)
INTERACT_REQUIRED = ("bs4", "httpx", "playwright")
READ_OPTIONAL = ("trafilatura", "readability", "selectolax", "crawl4ai")


def missing_dependencies(modules: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for module_name in modules:
        if importlib.util.find_spec(module_name) is None:
            missing.append(PACKAGE_NAMES.get(module_name, module_name))
    return missing


def capabilities() -> dict[str, object]:
    read_missing = missing_dependencies(READ_REQUIRED)
    snapshot_missing = missing_dependencies(SNAPSHOT_REQUIRED)
    interact_missing = missing_dependencies(INTERACT_REQUIRED)
    optional_missing = missing_dependencies(READ_OPTIONAL)
    return {
        "read_ready": len(read_missing) == 0,
        "snapshot_ready": len(snapshot_missing) == 0,
        "interact_ready": len(interact_missing) == 0,
        "missing_read_dependencies": read_missing,
        "missing_snapshot_dependencies": snapshot_missing,
        "missing_interact_dependencies": interact_missing,
        "optional_read_strategies": {
            "readability": "trafilatura" not in optional_missing and "readability-lxml" not in optional_missing,
            "heuristic": "selectolax" not in optional_missing,
            "crawl4ai": "crawl4ai" not in optional_missing,
        },
    }
