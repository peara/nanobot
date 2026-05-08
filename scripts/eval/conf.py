from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EVAL_LOG = PROJECT_ROOT / "data" / "evaluator.log"
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

QUALITY_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "quality_assessment",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "quality_score": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                },
                "quality_reason": {
                    "type": "string",
                },
                "has_learnings": {
                    "type": "boolean",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                },
            },
            "required": ["quality_score", "quality_reason", "has_learnings", "confidence"],
            "additionalProperties": False,
        },
    },
}

LEARNING_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "learning_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "learnings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "direction": {
                                "type": "string",
                                "enum": ["create_skill", "update_skill", "user_preference", "constraint"],
                            },
                            "observation": {"type": "string"},
                            "evidence": {"type": "string"},
                            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        },
                        "required": ["category", "direction", "observation", "evidence", "confidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["learnings"],
            "additionalProperties": False,
        },
    },
}

MEMORY_TOOL_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "memory_tool_selection",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "tool_calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool_name": {"type": "string"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            },
                        },
                        "required": ["tool_name", "arguments"],
                        "additionalProperties": False,
                    },
                },
                "reasoning": {"type": "string"},
            },
            "required": ["tool_calls", "reasoning"],
            "additionalProperties": False,
        },
    },
}

PHASE_SCHEMAS = {
    "quality_assessment": QUALITY_ASSESSMENT_SCHEMA,
    "learning_extraction": LEARNING_EXTRACTION_SCHEMA,
    "memory_tool_selection": MEMORY_TOOL_SELECTION_SCHEMA,
    "memory_tool_production": None,
}


def load_prompt_from_defaults(phase: str) -> str:
    if phase == "memory_tool_selection":
        prompt_path = PROMPTS_DIR / "memory_tool_selection.txt"
        if prompt_path.exists():
            return prompt_path.read_text()
        raise ValueError(f"Prompt file not found: {prompt_path}")
    prompt_name = {
        "quality_assessment": "QUALITY_ASSESSMENT_PROMPT",
        "learning_extraction": "LEARNING_EXTRACTION_PROMPT",
    }[phase]
    defaults_path = PROJECT_ROOT / "src" / "nanobot" / "prompts" / "defaults.py"
    content = defaults_path.read_text()
    match = re.search(rf'{prompt_name}\s*=\s*"""(.*?)"""', content, re.DOTALL)
    if not match:
        raise ValueError(f"Could not find {prompt_name} in {defaults_path}")
    return match.group(1)


def load_prompt_from_file(path: Path) -> str:
    return path.read_text()


def list_fixtures(phase: str | None = None) -> list[str]:
    """List fixture names, optionally filtered by phase. Searches flat files and phase subdirectories."""
    if not FIXTURES_DIR.exists():
        return []
    names: list[str] = []
    for p in FIXTURES_DIR.glob("*.json"):
        data = json.loads(p.read_text())
        if phase is None or data.get("phase") == phase:
            names.append(p.stem)
    for p in FIXTURES_DIR.rglob("*.json"):
        if p.parent == FIXTURES_DIR:
            continue
        data = json.loads(p.read_text())
        if phase is None or data.get("phase") == phase:
            names.append(p.stem)
    return sorted(set(names))


def load_fixture(name: str) -> dict[str, Any]:
    """Load a fixture by name, searching flat files then subdirectories."""
    flat_path = FIXTURES_DIR / f"{name}.json"
    if flat_path.exists():
        return json.loads(flat_path.read_text())
    for p in FIXTURES_DIR.rglob(f"{name}.json"):
        if p != flat_path:
            return json.loads(p.read_text())
    raise FileNotFoundError(f"Fixture not found: {name}")


def list_prompts() -> list[str]:
    if not PROMPTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.txt"))


def load_config() -> dict[str, Any]:
    try:
        import yaml

        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f)
    except ImportError:
        content = CONFIG_FILE.read_text()
        base_url_match = re.search(r"base_url:\s*[\"']?(.+?)[\"']?\s*$", content, re.MULTILINE)
        api_key_match = re.search(r"api_key:\s*[\"']?(.+?)[\"']?\s*$", content, re.MULTILINE)
        model_match = re.search(r"model:\s*[\"']?(.+?)[\"']?\s*$", content, re.MULTILINE)
        return {
            "model": {
                "base_url": base_url_match.group(1) if base_url_match else "http://localhost:1234/v1",
                "api_key": api_key_match.group(1) if api_key_match else "ollama",
                "model": model_match.group(1) if model_match else "google/gemma-4-31b",
            }
        }


def extract_log_entries(phase: str | None = None) -> list[dict[str, str]]:
    if not EVAL_LOG.exists():
        return []
    log_text = EVAL_LOG.read_text()
    if phase:
        pattern = re.compile(
            rf"phase={phase}\n--- INPUT ---\n(.*?)\n--- RESPONSE ---\n(.*?)\n--- END ---",
            re.DOTALL,
        )
    else:
        pattern = re.compile(
            r"phase=(\w+)\n--- INPUT ---\n(.*?)\n--- RESPONSE ---\n(.*?)\n--- END ---",
            re.DOTALL,
        )
    entries = []
    for match in pattern.finditer(log_text):
        if phase:
            entries.append({"phase": phase, "input": match.group(1), "response": match.group(2)})
        else:
            entries.append({"phase": match.group(1), "input": match.group(2), "response": match.group(3)})
    return entries


async def call_llm(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
    response_format: dict[str, Any],
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": 30000,
        "response_format": response_format,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
