"""Thin composition of independent context-claim gates."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .capture_claim import evaluate_capture_claim
from .freshness_claim import evaluate_freshness_claim
from .understanding_claim import evaluate_understanding_claim

CLAIMS = ("complete", "current", "understood")


def evaluate_context_claims(
    evidence: Mapping[str, Any],
    *,
    understanding_confirmed: bool = False,
    now: datetime | None = None,
    max_age_seconds: int = 86_400,
) -> dict[str, Any]:
    capture = evaluate_capture_claim(evidence)
    freshness = evaluate_freshness_claim(
        evidence.get("evidence_observed_at"),
        now=now,
        max_age_seconds=max_age_seconds,
    )
    understanding = evaluate_understanding_claim(
        capture,
        freshness,
        understanding_confirmed=understanding_confirmed,
    )
    current_blockers = capture["blockers"] + freshness["blockers"]
    current_allowed = capture["allowed"] and freshness["allowed"]
    current_unknown = "unknown" in {capture["state"], freshness["state"]}
    return {
        "schema": "dcb.context-claim-gate.v1",
        "stages": {
            "capture": capture,
            "freshness": freshness,
            "understanding": understanding,
        },
        "claims": {
            "complete": capture,
            "current": {
                "stage": "current",
                "state": "allowed" if current_allowed else "unknown" if current_unknown else "blocked",
                "allowed": current_allowed,
                "blockers": current_blockers,
            },
            "understood": understanding,
        },
        "raw_text_returned": False,
        "participant_names_returned": False,
        "identifiers_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }
