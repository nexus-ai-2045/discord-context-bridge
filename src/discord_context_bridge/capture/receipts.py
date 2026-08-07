"""Metadata-only operational receipts bound to a durable capture id."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from discord_context_bridge.acquisition_gate import validate_full_capture_receipt
from .store import CaptureCheckpointStore, CaptureStoreError


_SAFE_CONSUMERS = {"context_acquisition"}
_SAFE_BROWSER_ROUTES = {"chrome_extension", "in_app_browser", "desktop_accessibility"}
_SAFE_BROWSER_STATES = {
    "connected",
    "tab_inventory_ok",
    "claim_ok",
    "ready",
    "blocked_extension_ui",
    "extension_unavailable",
    "auth_required",
    "external_mutation_stop",
    "unknown",
}
_SAFE_BROWSER_ERRORS = {
    "none",
    "popup_open",
    "tab_inventory_failed",
    "claim_failed",
    "navigation_failed",
    "unknown",
}
_SAFE_HOLD_REASONS = {
    "human_review_required",
    "source_evidence_missing",
    "privacy_boundary_unresolved",
    "adapter_unavailable",
    "source_evidence_mismatch",
}
_SAFE_POINTER = re.compile(r"^absorbed-dialogue:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FULL_GATE_METADATA_FIELDS = (
    "status",
    "full_capture_confirmed",
    "capture_id",
    "boundaries",
    "counts",
    "counts_consistent",
    "attachments_consistent",
    "unresolved_gap_count",
    "blockers",
    "raw_text_returned",
    "participant_names_returned",
    "url_output",
    "path_output",
    "outbound_actions",
    "message_period",
)
_BOUNDARY_FIELDS = ("oldest_reached", "latest_reached", "capture_stable_after_rescan")
_COUNT_FIELDS = (
    "messages", "raw_records", "markdown_messages", "ledger_messages",
    "attachments_discovered", "attachments_saved", "attachments_manifested",
)
_SCHEMA_VERSION = "1.0"
_RECORDED_BY = "discord-context-bridge"
_MAX_BROWSER_OBSERVATIONS = 256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def closeout_correlation_digest(closeout: Mapping[str, Any]) -> str:
    """Create a content-free correlation id from the verified closeout state."""

    fields = {
        key: closeout.get(key)
        for key in (
            "schema",
            "closeout_status",
            "external_action_state",
            "human_sent_observed",
            "human_reviewed",
            "observed_text_status",
            "unread_check_status",
            "unread_signal_count",
            "outbound_actions",
        )
    }
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _load_adapter_receipt(path: Path | None, *, max_bytes: int = 1_000_000) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        if path.stat().st_size > max_bytes:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def persist_strict_full_capture_receipt(
    store: CaptureCheckpointStore,
    capture_id: str,
    gate: Mapping[str, Any],
    *,
    consumer: str,
) -> dict[str, Any]:
    """Persist only a successful canonical gate, bound to its sole consumer."""

    if consumer not in _SAFE_CONSUMERS:
        raise CaptureStoreError("unsupported full capture receipt consumer")
    validation = validate_full_capture_receipt(gate)
    if not validation["valid"] or validation["capture_id"] != capture_id:
        raise CaptureStoreError("strict full capture gate is not independently complete")
    projected = {key: gate[key] for key in _FULL_GATE_METADATA_FIELDS if key in gate}
    projected["boundaries"] = {
        key: gate["boundaries"].get(key) for key in _BOUNDARY_FIELDS
    }
    projected["counts"] = {key: gate["counts"].get(key) for key in _COUNT_FIELDS}
    if isinstance(gate.get("message_period"), Mapping):
        projected["message_period"] = {
            key: gate["message_period"].get(key) for key in ("start", "end")
        }
    receipt = {
        **projected,
        "schema": "dcb-strict-full-capture-receipt.v1",
        "schema_version": _SCHEMA_VERSION,
        "source_gate_schema": gate.get("schema"),
        "consumer_binding": consumer,
        "recorded_at": _now(),
        "recorded_by": _RECORDED_BY,
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
    with store.transition_lock(capture_id):
        existing = store.load_full_capture_receipt(capture_id, consumer=consumer)
        if existing is not None:
            comparable = {key: value for key, value in existing.items() if key != "recorded_at"}
            candidate = {key: value for key, value in receipt.items() if key != "recorded_at"}
            if comparable != candidate:
                raise CaptureStoreError("full capture receipt is immutable")
            return existing
        return store.save_receipt(store.full_capture_receipt_path(capture_id), receipt)


def persist_browser_route_observation(
    store: CaptureCheckpointStore,
    capture_id: str,
    *,
    route: str,
    observed_state: str,
    error_code: str = "none",
) -> dict[str, Any]:
    """Append a normalized browser-route observation without private UI text."""

    normalized_route = _normalize(route)
    normalized_state = _normalize(observed_state)
    normalized_error = _normalize(error_code) or "none"
    if normalized_route not in _SAFE_BROWSER_ROUTES:
        normalized_route = "unknown"
    if normalized_state not in _SAFE_BROWSER_STATES:
        normalized_state = "unknown"
    if normalized_error not in _SAFE_BROWSER_ERRORS:
        normalized_error = "unknown"
    with store.transition_lock(capture_id):
        existing = store.load_browser_route_receipt(capture_id)
        observations = list((existing or {}).get("observations") or [])
        if len(observations) >= _MAX_BROWSER_OBSERVATIONS:
            raise CaptureStoreError("browser route observation limit reached")
        observations.append(
            {
                "sequence": len(observations) + 1,
                "route": normalized_route,
                "state": normalized_state,
                "error_code": normalized_error,
                "observed_at": _now(),
            }
        )
        receipt = {
            "schema": "dcb-browser-route-observation-receipt.v1",
            "schema_version": _SCHEMA_VERSION,
            "capture_id": capture_id,
            "route": normalized_route,
            "latest_state": normalized_state,
            "observations": observations,
            "recorded_at": _now(),
            "recorded_by": _RECORDED_BY,
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        }
        return store.save_receipt(store.browser_route_receipt_path(capture_id), receipt)


def persist_learning_handoff_receipt(
    store: CaptureCheckpointStore,
    capture_id: str,
    closeout: Mapping[str, Any],
    *,
    adapter_receipt_path: Path | None = None,
    hold_reason: str = "",
) -> dict[str, Any]:
    """Persist adapter evidence or an explicit hold; never trust a status flag alone."""

    closeout_valid = (
        closeout.get("schema") == "discord_post_send_closeout_packet.v1"
        and closeout.get("closeout_status") == "closed"
        and closeout.get("human_sent_observed") is True
        and closeout.get("human_reviewed") is True
        and closeout.get("outbound_actions") == "disabled"
    )
    if not closeout_valid:
        raise CaptureStoreError("post-send closeout evidence is not complete")
    adapter_receipt = _load_adapter_receipt(adapter_receipt_path)
    hold = _normalize(hold_reason)
    if hold and hold not in _SAFE_HOLD_REASONS:
        raise CaptureStoreError("learning handoff hold reason is invalid")
    expected_correlation = closeout_correlation_digest(closeout)
    pointer = str((adapter_receipt or {}).get("evidence_pointer") or "").strip()
    adapter_valid = bool(
        adapter_receipt
        and adapter_receipt.get("schema") == "absorbed_dialogue_learning_receipt.v1"
        and adapter_receipt.get("schema_version") == _SCHEMA_VERSION
        and adapter_receipt.get("capture_id") == capture_id
        and adapter_receipt.get("closeout_correlation_digest") == expected_correlation
        and adapter_receipt.get("status") == "completed"
        and adapter_receipt.get("recorded_by") == "absorbed-dialogue-router"
        and isinstance(adapter_receipt.get("recorded_at"), str)
        and adapter_receipt.get("raw_text_returned") is False
        and adapter_receipt.get("outbound_actions") == "disabled"
        and _SAFE_POINTER.fullmatch(pointer)
    )
    if hold:
        final_hold = hold
    elif adapter_receipt is None:
        final_hold = "source_evidence_missing"
    elif not adapter_valid:
        final_hold = "source_evidence_mismatch"
    else:
        final_hold = "adapter_contract_unavailable"
    receipt = {
        "schema": "dcb-learning-handoff-receipt.v1",
        "schema_version": _SCHEMA_VERSION,
        "capture_id": capture_id,
        "adapter": "absorbed-dialogue-router",
        "closeout_correlation_digest": expected_correlation,
        "status": "held",
        "completion_confirmed": False,
        "evidence_pointer_output": "omitted" if adapter_valid else "not_provided",
        "evidence_pointer_digest": sha256(pointer.encode("utf-8")).hexdigest() if adapter_valid else "",
        "hold_reason": final_hold,
        "recorded_at": _now(),
        "recorded_by": _RECORDED_BY,
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
    with store.transition_lock(capture_id):
        existing = store.load_learning_handoff_receipt(capture_id)
        if existing is not None:
            comparable = {key: value for key, value in existing.items() if key != "recorded_at"}
            candidate = {key: value for key, value in receipt.items() if key != "recorded_at"}
            if comparable != candidate:
                raise CaptureStoreError("learning handoff receipt is immutable")
            return existing
        return store.save_receipt(store.learning_handoff_receipt_path(capture_id), receipt)
