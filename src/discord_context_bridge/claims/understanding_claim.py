"""Pure gate for understanding claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def evaluate_understanding_claim(
    capture: Mapping[str, Any],
    freshness: Mapping[str, Any],
    *,
    understanding_confirmed: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    if capture.get("allowed") is not True:
        blockers.append("capture_claim_blocked")
    if freshness.get("allowed") is not True:
        blockers.append("freshness_claim_blocked")
    if understanding_confirmed is not True:
        blockers.append("understanding_not_confirmed")
    dependency_unknown = "unknown" in {capture.get("state"), freshness.get("state")}
    state = "allowed" if not blockers else "unknown" if dependency_unknown else "blocked"
    return {"stage": "understanding", "state": state, "allowed": not blockers, "blockers": blockers}
