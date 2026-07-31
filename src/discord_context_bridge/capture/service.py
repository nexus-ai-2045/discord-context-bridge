"""Durable application service for metadata-only capture LOOP transitions."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Mapping

from .loop import (
    advance_capture_loop,
    build_capture_status_projection,
    new_capture_loop,
)
from .store import CaptureCheckpointStore, SequenceConflictError
from .virtual_scroll import merge_capture_window, new_virtual_scroll_coverage
from .message_ledger import (
    append_message_event,
    build_capture_projections,
    new_message_ledger,
)


def _event_digest(event: str | Mapping[str, Any]) -> str:
    payload = dict(event) if isinstance(event, Mapping) else {"type": str(event)}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def start_capture_loop(
    store: CaptureCheckpointStore,
    target_key: str,
    route: str,
    upper_watermark: str,
    **options: Any,
) -> dict[str, Any]:
    """Create and persist a run without returning its private target key."""

    run = new_capture_loop(target_key, route, upper_watermark, **options)
    with store.transition_lock(run["capture_id"]):
        existing = store.load_checkpoint(run["capture_id"])
        if existing is not None:
            return build_capture_status_projection(existing)
        store.save_checkpoint(run, expected_sequence=0)
        return build_capture_status_projection(run)


def _coverage_projection(coverage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "window_count": len(coverage.get("windows") or []),
        "unique_message_count": int(coverage.get("unique_message_count") or 0),
        "duplicate_observation_count": int(
            coverage.get("duplicate_observation_count") or 0
        ),
        "edited_message_count": int(coverage.get("edited_message_count") or 0),
        "invalid_observation_count": int(
            coverage.get("invalid_observation_count") or 0
        ),
        "gap_count": int(coverage.get("gap_count") or 0),
        "coverage_connected": bool(coverage.get("coverage_connected")),
        "oldest_reached": bool(coverage.get("oldest_reached")),
        "latest_reached": bool(coverage.get("latest_reached")),
        "stable_scan_passes": int(coverage.get("stable_scan_passes") or 0),
        "final_pass_new_message_count": coverage.get(
            "final_pass_new_message_count"
        ),
        "capture_stable_after_rescan": bool(
            coverage.get("capture_stable_after_rescan")
        ),
        "sources": list(coverage.get("sources") or []),
        "blockers": list(coverage.get("blockers") or []),
        "cache_first_applied": bool(coverage.get("cache_first_applied")),
        "initial_cache_message_count": int(
            coverage.get("initial_cache_message_count") or 0
        ),
        "intake_order": list(coverage.get("intake_order") or []),
    }


def read_capture_loop_status(
    store: CaptureCheckpointStore,
    capture_id: str,
) -> dict[str, Any]:
    run = store.load_checkpoint(capture_id)
    if run is None:
        raise SequenceConflictError("capture checkpoint does not exist")
    result = build_capture_status_projection(run)
    coverage = store.load_coverage(capture_id)
    result["coverage"] = _coverage_projection(
        coverage or new_virtual_scroll_coverage(capture_id)
    )
    return result


def merge_persisted_capture_window(
    store: CaptureCheckpointStore,
    capture_id: str,
    observation: Mapping[str, Any],
    *,
    expected_window_count: int,
) -> dict[str, Any]:
    """Merge one DOM/cache window under the capture transition lock."""

    with store.transition_lock(capture_id):
        run = store.load_checkpoint(capture_id)
        if run is None:
            raise SequenceConflictError("capture checkpoint does not exist")
        current = store.load_coverage(capture_id)
        coverage = current or new_virtual_scroll_coverage(capture_id)
        current_count = len(coverage["windows"])
        if current_count != expected_window_count:
            raise SequenceConflictError(
                "coverage window count conflict: "
                f"expected {expected_window_count}, found {current_count}"
            )
        updated = merge_capture_window(coverage, observation)
        store.save_coverage(
            updated,
            expected_window_count=expected_window_count,
        )
        result = build_capture_status_projection(run)
        result["coverage"] = _coverage_projection(updated)
        return result


def merge_capture_windows_cache_first(
    store: CaptureCheckpointStore,
    capture_id: str,
    observations: list[Mapping[str, Any]],
    *,
    expected_window_count: int,
) -> dict[str, Any]:
    """Persist a batch with local cache observations before live browser windows."""

    cache_sources = {
        "background_cache",
        "discord_desktop_cache",
        "saved_cache",
        "saved_snapshot",
    }
    ordered = sorted(
        enumerate(observations),
        key=lambda item: (
            0 if str(item[1].get("source") or "") in cache_sources else 1,
            item[0],
        ),
    )
    with store.transition_lock(capture_id):
        run = store.load_checkpoint(capture_id)
        if run is None:
            raise SequenceConflictError("capture checkpoint does not exist")
        current = store.load_coverage(capture_id)
        coverage = current or new_virtual_scroll_coverage(capture_id)
        current_count = len(coverage["windows"])
        if current_count != expected_window_count:
            raise SequenceConflictError(
                "coverage window count conflict: "
                f"expected {expected_window_count}, found {current_count}"
            )

        intake_order: list[str] = []
        cache_message_count = 0
        for _, observation in ordered:
            source = str(observation.get("source") or "")
            coverage = merge_capture_window(coverage, observation)
            intake_order.append(source)
            if source in cache_sources:
                cache_message_count = coverage["unique_message_count"]

        coverage["cache_first_applied"] = True
        coverage["initial_cache_message_count"] = cache_message_count
        coverage["intake_order"] = intake_order
        store.save_coverage(
            coverage,
            expected_window_count=expected_window_count,
        )
        result = build_capture_status_projection(run)
        result["coverage"] = _coverage_projection(coverage)
        return result


def append_persisted_message_event(
    store: CaptureCheckpointStore,
    capture_id: str,
    event: Mapping[str, Any],
    *,
    expected_sequence: int,
) -> dict[str, Any]:
    """Append canonical message data under the capture transition lock."""

    with store.transition_lock(capture_id):
        run = store.load_checkpoint(capture_id)
        if run is None:
            raise SequenceConflictError("capture checkpoint does not exist")
        ledger = store.load_message_ledger(capture_id) or new_message_ledger(
            capture_id,
            target_key=str(run["target_digest"]),
            upper_watermark=str(run["upper_watermark_digest"]),
        )
        if len(ledger["events"]) != expected_sequence:
            raise SequenceConflictError(
                "message ledger sequence conflict: "
                f"expected {expected_sequence}, found {len(ledger['events'])}"
            )
        updated = append_message_event(ledger, event)
        store.save_message_ledger(updated, expected_sequence=expected_sequence)
        return {
            "capture_id": capture_id,
            "message_event_sequence": len(updated["events"]),
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        }


def rebuild_persisted_capture_projections(
    store: CaptureCheckpointStore,
    capture_id: str,
    *,
    oldest_reached: bool,
    latest_reached: bool,
    stable_scan_digests: list[str],
    saved_attachment_ids: list[str],
    upper_watermark_reached: bool,
    unresolved_gap_count: int,
    pending_retry_count: int,
    attachment_inventory_complete: bool,
) -> dict[str, Any]:
    """Rebuild all views in memory; projection state is never persisted."""

    ledger = store.load_message_ledger(capture_id)
    if ledger is None:
        raise SequenceConflictError("message ledger does not exist")
    return build_capture_projections(
        ledger,
        oldest_reached=oldest_reached,
        latest_reached=latest_reached,
        stable_scan_digests=stable_scan_digests,
        saved_attachment_ids=saved_attachment_ids,
        upper_watermark_reached=upper_watermark_reached,
        unresolved_gap_count=unresolved_gap_count,
        pending_retry_count=pending_retry_count,
        attachment_inventory_complete=attachment_inventory_complete,
    )


def advance_persisted_capture(
    store: CaptureCheckpointStore,
    capture_id: str,
    event_id: str,
    event: str | Mapping[str, Any],
    *,
    expected_sequence: int,
) -> dict[str, Any]:
    """Append an idempotent event, then advance its recoverable checkpoint."""

    run = store.load_checkpoint(capture_id)
    if run is None:
        raise SequenceConflictError("capture checkpoint does not exist")
    event_name = str(event.get("type") if isinstance(event, Mapping) else event)
    semantic_digest = _event_digest(event)
    with store.transition_lock(capture_id):
        run = store.load_checkpoint(capture_id)
        if run is None:
            raise SequenceConflictError("capture checkpoint does not exist")
        for existing in store.load_events(capture_id):
            if existing["event_id"] == event_id:
                if existing.get("event") != event_name:
                    raise SequenceConflictError("event id is bound to another event")
                if existing.get("semantic_event_digest") != semantic_digest:
                    raise SequenceConflictError("event id payload does not match")
                current_sequence = len(run["checkpoints"])
                event_sequence = existing["sequence"]
                if current_sequence == event_sequence:
                    return build_capture_status_projection(run)
                if current_sequence != event_sequence - 1:
                    raise SequenceConflictError(
                        "ledger and checkpoint sequences cannot be recovered"
                    )
                recovered = advance_capture_loop(run, event)
                if (
                    len(recovered["checkpoints"]) != event_sequence
                    or recovered["state"] != existing.get("state")
                    or recovered.get("blocker") != existing.get("blocker")
                ):
                    raise SequenceConflictError(
                        "ledger event does not match recoverable checkpoint"
                    )
                store.save_checkpoint(recovered, expected_sequence=current_sequence)
                return build_capture_status_projection(recovered)
        current_sequence = len(run["checkpoints"])
        if current_sequence != expected_sequence:
            raise SequenceConflictError(
                f"checkpoint sequence conflict: expected {expected_sequence}, "
                f"found {current_sequence}"
            )

        updated = advance_capture_loop(run, event)
        next_sequence = len(updated["checkpoints"])
        ledger_event = {
            "schema": "dcb-capture-event.v1",
            "event_id": event_id,
            "capture_id": capture_id,
            "sequence": next_sequence,
            "event": event_name,
            "semantic_event_digest": semantic_digest,
            "state": updated["state"],
            "blocker": updated.get("blocker"),
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        }
        store.append_event(ledger_event, expected_sequence=expected_sequence)
        store.save_checkpoint(updated, expected_sequence=expected_sequence)
        return build_capture_status_projection(updated)
