"""Pure resumable state machine for autonomous full Discord capture."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any, Mapping


_VERSION = "dcb-full-capture-orchestrator.v1"
_SUPPORTED_ROUTES = {
    "in_app_browser",
    "chrome_extension",
    "rest_backfill",
    "saved_artifacts",
    "discord_desktop_accessibility",
}

_TRANSITIONS = {
    ("received", "visible_snapshot_saved"): "route_preflight",
    ("route_preflight", "route_ready"): "traversing_to_oldest",
    ("traversing_to_oldest", "oldest_reached"): "traversing_to_latest",
    ("traversing_to_latest", "latest_reached"): "attachment_inventory",
    ("attachment_inventory", "attachment_inventory_complete"): "downloading_attachments",
    ("downloading_attachments", "attachments_saved"): "reconciling",
    ("reconciling", "reconciled"): "stable_rescan",
    ("stable_rescan", "stable_rescan_complete"): "gate_evaluating",
    ("gate_evaluating", "gate_partial"): "stable_rescan",
}
_TERMINAL_STATES = {"full_closed", "blocked_closed"}


def _digest(*parts: str) -> str:
    framed = "".join(f"{len(part)}:{part}" for part in parts)
    return sha256(framed.encode("utf-8")).hexdigest()


def new_capture_run(
    target_key: str,
    route: str,
    upper_watermark: str,
    *,
    retry_budget: int = 3,
) -> dict[str, Any]:
    """Create a deterministic run envelope safe to persist and resume."""
    if route not in _SUPPORTED_ROUTES:
        raise ValueError(f"unsupported capture route: {route}")
    if retry_budget < 0:
        raise ValueError("retry_budget must be non-negative")
    identity = _digest(_VERSION, target_key, route, upper_watermark)
    return {
        "schema": _VERSION,
        "capture_id": identity,
        "idempotency_key": _digest("idempotency", identity),
        "target_digest": _digest("target", target_key),
        "upper_watermark_digest": _digest("watermark", upper_watermark),
        "route": route,
        "state": "received",
        "resume_state": None,
        "blocker": None,
        "automatic_fallback_attempted": False,
        "retry": {"budget": retry_budget, "used": 0},
        "lanes": {
            "immediate_visible": {
                "status": "pending",
                "terminal_for_full_capture": False,
            },
            "background_full": {
                "status": "running",
                "persistent": True,
            },
        },
        "checkpoints": [],
    }


def advance_capture_run(run: dict[str, Any], event: str | Mapping[str, Any]) -> dict[str, Any]:
    """Apply one event without mutating the persisted input envelope."""
    updated = deepcopy(run)
    event_data = dict(event) if isinstance(event, Mapping) else {}
    event_name = str(event_data.get("type") or event)
    state = updated["state"]
    if state in _TERMINAL_STATES:
        raise ValueError(f"cannot advance terminal state: {state}")

    if event_name in {"auth_required", "human_approval_required"}:
        updated["resume_state"] = state
        updated["state"] = (
            "paused_auth" if event_name == "auth_required" else "paused_human_approval"
        )
        updated["blocker"] = event_name
        updated["automatic_fallback_attempted"] = False
    elif event_name in {"auth_resolved", "human_approval_granted"}:
        expected = (
            "paused_auth" if event_name == "auth_resolved" else "paused_human_approval"
        )
        if state != expected or not updated.get("resume_state"):
            raise ValueError(f"invalid transition: {state} + {event_name}")
        if event_name == "human_approval_granted" and (
            event_data.get("capture_id") != updated["capture_id"]
            or not event_data.get("approved_action")
        ):
            raise ValueError("human approval is not scoped to this capture/action")
        updated["state"] = updated["resume_state"]
        updated["resume_state"] = None
        updated["blocker"] = None
    elif event_name == "retryable_failure":
        retry = updated["retry"]
        retry["used"] += 1
        if retry["used"] > retry["budget"]:
            updated["state"] = "blocked_closed"
            updated["blocker"] = "retry_budget_exhausted"
            updated["lanes"]["background_full"]["status"] = "blocked"
        else:
            updated["resume_state"] = state
            updated["state"] = "retry_wait"
            updated["blocker"] = "retryable_failure"
    elif event_name == "retry_due":
        if state != "retry_wait" or not updated.get("resume_state"):
            raise ValueError(f"invalid transition: {state} + {event_name}")
        updated["state"] = updated["resume_state"]
        updated["resume_state"] = None
        updated["blocker"] = None
    else:
        if state == "received" and event_name == "route_ready" and updated["route"] in {
            "rest_backfill", "saved_artifacts"
        }:
            next_state = "traversing_to_oldest"
        elif state == "gate_evaluating" and event_name == "gate_full":
            gate = event_data.get("gate")
            if not isinstance(gate, Mapping) or not (
                gate.get("status") == "full"
                and gate.get("full_capture_confirmed") is True
                and event_data.get("capture_id") == updated["capture_id"]
                and gate.get("outbound_actions") == "disabled"
            ):
                raise ValueError("gate_full requires a bound strict full-capture result")
            next_state = "full_closed"
            updated["gate_evidence_digest"] = _digest(
                "gate", updated["capture_id"], str(sorted(gate.items()))
            )
        else:
            next_state = _TRANSITIONS.get((state, event_name))
        if next_state is None:
            raise ValueError(f"invalid transition: {state} + {event_name}")
        updated["state"] = next_state

    if event_name == "visible_snapshot_saved":
        updated["lanes"]["immediate_visible"]["status"] = "saved"
    if updated["state"] == "full_closed":
        updated["lanes"]["background_full"]["status"] = "full"

    updated["checkpoints"].append(
        {
            "sequence": len(updated["checkpoints"]) + 1,
            "event": event_name,
            "state": updated["state"],
        }
    )
    return updated
