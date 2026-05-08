from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


def rank_script_candidate(
    *,
    semantic_similarity: float,
    script_domain: str | None,
    params: dict[str, Any] | None,
    success_rate: float,
    updated_at: str,
    params_schema: dict[str, Any],
) -> tuple[float, str]:
    domain_match = _domain_match(script_domain, params or {})
    freshness = _freshness_score(updated_at)
    param_compatibility = _param_compatibility(params_schema, params or {})

    final_score = (
        semantic_similarity * 0.45
        + domain_match * 0.20
        + success_rate * 0.20
        + freshness * 0.10
        + param_compatibility * 0.05
    )
    reason = (
        f"semantic={semantic_similarity:.2f}, domain={domain_match:.2f}, "
        f"success={success_rate:.2f}, freshness={freshness:.2f}, params={param_compatibility:.2f}"
    )
    return final_score, reason


def _domain_match(script_domain: str | None, params: dict[str, Any]) -> float:
    url = str(params.get("url", ""))
    if not script_domain or not url:
        return 0.0
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not host:
        return 0.0
    if host == script_domain.lower() or host.endswith(f".{script_domain.lower()}"):
        return 1.0
    return 0.0


def _freshness_score(updated_at: str) -> float:
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return 0.0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - updated).total_seconds() / 86400.0)
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.7
    if age_days <= 90:
        return 0.4
    return 0.1


def _param_compatibility(schema: dict[str, Any], params: dict[str, Any]) -> float:
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not required:
        return 1.0
    hit = sum(1 for key in required if key in params)
    return hit / max(1, len(required))
