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
