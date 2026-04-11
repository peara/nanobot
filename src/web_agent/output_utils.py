from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def ensure_outputs_dir() -> Path:
    output_dir = Path("./data/web_agent")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def build_output_stem(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or "page"
    path = parsed.path.strip("/") or "root"
    raw = f"{host}-{path}"
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-._")
    return stem or "page"


def save_json_output(payload: dict[str, Any], *, stem: str, output_dir: Path) -> Path:
    path = output_dir / f"{stem}.json"
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return path
