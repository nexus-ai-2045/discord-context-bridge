"""Pure gate for freshness claims."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def evaluate_freshness_claim(
    observed_at: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 86_400,
    future_tolerance_seconds: int = 300,
) -> dict[str, Any]:
    observed = _parse(observed_at)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    blockers: list[str] = []
    if observed is None:
        blockers.append("freshness_timestamp_invalid")
    elif max_age_seconds < 0 or future_tolerance_seconds < 0:
        blockers.append("freshness_policy_invalid")
    else:
        age = (current - observed).total_seconds()
        if age > max_age_seconds:
            blockers.append("freshness_expired")
        if age < -future_tolerance_seconds:
            blockers.append("freshness_timestamp_in_future")
    unknown = {"freshness_timestamp_invalid", "freshness_policy_invalid"}
    state = "allowed" if not blockers else "unknown" if unknown.intersection(blockers) else "blocked"
    return {"stage": "freshness", "state": state, "allowed": not blockers, "blockers": blockers}
