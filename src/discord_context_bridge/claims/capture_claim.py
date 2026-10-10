"""Pure gate for canonical, persisted full-capture claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def evaluate_capture_claim(evidence: Mapping[str, Any]) -> dict[str, Any]:
    checks = (
        (evidence.get("schema") == "dcb.parallel-run-operational-closeout.v1", "canonical_closeout_required"),
        (evidence.get("status") == "full", "capture_status_not_full"),
        (evidence.get("terminal_state") == "full_closed", "capture_not_full_closed"),
        (evidence.get("full_capture_confirmed") is True, "full_capture_not_confirmed"),
        (evidence.get("persistence_confirmed") is True, "persistence_not_confirmed"),
        (not evidence.get("blockers"), "capture_blockers_present"),
        (evidence.get("outbound_actions") == "disabled", "outbound_boundary_invalid"),
    )
    blockers = [reason for passed, reason in checks if not passed]
    state = "allowed" if not blockers else "unknown" if "canonical_closeout_required" in blockers else "blocked"
    return {"stage": "capture", "state": state, "allowed": not blockers, "blockers": blockers}
