"""Bounded, metadata-only capture loop helpers.

This module composes the existing capture orchestrator without persistence or
browser side effects.  Callers may persist returned envelopes elsewhere.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .orchestrator import advance_capture_run, new_capture_run


_SCHEMA = "dcb-capture-loop.v1"
_SAFE_SCOPE_TAGS = {
    "dm": "direct-message",
    "server_threads": "server-threads-all",
    "thread_only": "thread-only",
}
_SAFE_ROUTE_TAGS = {
    "in_app_browser": "in-app-browser",
    "chrome_extension": "chrome-visible",
    "rest_backfill": "rest-backfill",
    "saved_artifacts": "saved-artifacts",
    "discord_desktop_accessibility": "desktop-accessibility",
}


def derive_operational_tags(context: Mapping[str, Any]) -> list[str]:
    """Derive tags from a closed allowlist; never echo caller-provided labels."""

    tags: set[str] = set()
    scope_tag = _SAFE_SCOPE_TAGS.get(str(context.get("scope") or ""))
    if scope_tag:
        tags.add(scope_tag)
    route_tag = _SAFE_ROUTE_TAGS.get(str(context.get("route") or ""))
    if route_tag:
        tags.add(route_tag)
    if context.get("refresh_check") is True:
        tags.add("refresh-check")
    if context.get("observed_full") is True:
        tags.add("observed-full")
    return sorted(tags)


def new_capture_loop(
    target_key: str,
    route: str,
    upper_watermark: str,
    *,
    scan_pass_budget: int = 2,
    retry_budget: int = 3,
    tag_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a resumable run with a bounded partial-gate scan loop."""

    if scan_pass_budget <= 0:
        raise ValueError("scan_pass_budget must be positive")
    run = new_capture_run(
        target_key,
        route,
        upper_watermark,
        retry_budget=retry_budget,
    )
    run["loop_schema"] = _SCHEMA
    run["scan_pass"] = {"budget": scan_pass_budget, "used": 0}
    context = dict(tag_context or {})
    context["route"] = route
    run["operational_tags"] = derive_operational_tags(context)
    return run


def advance_capture_loop(
    run: dict[str, Any],
    event: str | Mapping[str, Any],
) -> dict[str, Any]:
    """Advance the orchestrator and close repeated partial scans at the budget."""

    event_data = dict(event) if isinstance(event, Mapping) else {}
    event_name = str(event_data.get("type") or event)
    if event_name != "gate_partial":
        return advance_capture_run(run, event)

    scan_pass = run.get("scan_pass")
    if not isinstance(scan_pass, Mapping):
        raise ValueError("capture loop scan_pass metadata is missing")
    used = int(scan_pass.get("used") or 0) + 1
    budget = int(scan_pass.get("budget") or 0)
    if budget <= 0:
        raise ValueError("capture loop scan_pass budget is invalid")

    if used < budget:
        updated = advance_capture_run(run, event)
        updated["scan_pass"] = {"budget": budget, "used": used}
        return updated

    if run.get("state") != "gate_evaluating":
        return advance_capture_run(run, event)
    updated = deepcopy(run)
    updated["state"] = "blocked_closed"
    updated["blocker"] = "scan_pass_budget_exhausted"
    updated["scan_pass"] = {"budget": budget, "used": used}
    updated["lanes"]["background_full"]["status"] = "blocked"
    updated["checkpoints"].append(
        {
            "sequence": len(updated["checkpoints"]) + 1,
            "event": "gate_partial",
            "state": "blocked_closed",
        }
    )
    return updated


def build_capture_status_projection(run: Mapping[str, Any]) -> dict[str, Any]:
    """Project FDE and LCS status separately without emitting LCS events."""

    state = str(run.get("state") or "unknown")
    if state == "full_closed":
        fde_status, decision = "full", "context_understanding"
        lcs_status = "passport_pending"
    elif state == "blocked_closed":
        fde_status, decision = "blocked", "human_review"
        lcs_status = "capture_blocked"
    elif state.startswith("paused_") or state == "retry_wait":
        fde_status, decision = "paused", "resolve_blocker"
        lcs_status = "capture_pending"
    else:
        fde_status, decision = "in_progress", "continue_capture"
        lcs_status = "capture_pending"
    return {
        "schema": "dcb-capture-status-projection.v1",
        "capture_id": str(run.get("capture_id") or ""),
        "state": state,
        "operational_tags": list(run.get("operational_tags") or []),
        "fde": {
            "status": fde_status,
            "decision": decision,
            "blocker": run.get("blocker"),
        },
        "lcs": {
            "status": lcs_status,
            "event_emitted": False,
            "next_event": (
                "passport_ready" if lcs_status == "passport_pending" else None
            ),
        },
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def validate_observed_full_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate observed-full evidence while forbidding any API-full claim."""

    blockers: list[str] = []
    observed = receipt.get("observed_full")
    api_full = receipt.get("api_full")
    observed_verified = bool(
        isinstance(observed, Mapping) and observed.get("verified") is True
    )
    api_verified = bool(
        isinstance(api_full, Mapping) and api_full.get("verified") is True
    )
    if receipt.get("schema") != "discord_observed_full_closeout.v1":
        blockers.append("invalid_observed_full_schema")
    if not observed_verified:
        blockers.append("observed_full_not_verified")
    if not isinstance(api_full, Mapping) or api_full.get("verified") is not False:
        blockers.append("api_full_must_remain_false")
    if "api_full" in str(receipt.get("state") or "").casefold():
        blockers.append("observed_receipt_claims_api_full")
    if receipt.get("raw_text_returned") is not False:
        blockers.append("raw_text_must_not_be_returned")
    if receipt.get("outbound_actions") != "disabled":
        blockers.append("outbound_actions_must_be_disabled")
    return {
        "schema": "dcb-observed-full-receipt-validation.v1",
        "valid": not blockers,
        "observed_full_verified": observed_verified,
        # Deliberately fixed: an observed-full receipt never proves API full.
        "api_full_verified": False,
        "source_api_full_claimed": api_verified,
        "blockers": blockers,
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
