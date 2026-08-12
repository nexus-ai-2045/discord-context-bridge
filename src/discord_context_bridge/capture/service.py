"""Durable application service for metadata-only capture LOOP transitions."""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .loop import (
    advance_capture_loop,
    build_capture_status_projection,
    new_capture_loop,
)
from .store import (
    CaptureCheckpointStore,
    SequenceConflictError,
    canonical_capture_digest,
)
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


def _new_attachment_save_ledger(capture_id: str) -> dict[str, Any]:
    return {
        "schema": "dcb-private-attachment-save-ledger.v1",
        "capture_id": capture_id,
        "records": [],
        "tip_hash": canonical_capture_digest([]),
        "seal": None,
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def _validated_private_ref(value: str) -> str:
    if (
        not value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("attachment private ref is invalid")
    return value


def _managed_object_path(
    store: CaptureCheckpointStore, capture_id: str, managed_ref: str
) -> Path:
    prefix = f"attachment-objects/{capture_id}/"
    if not managed_ref.startswith(prefix):
        raise ValueError("managed attachment ref is invalid")
    relative = _validated_private_ref(managed_ref)
    path = store.root.joinpath(*relative.split("/"))
    _assert_managed_path_contained(store.root, path)
    return path


def _is_link_or_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
    return path.is_symlink() or bool(attributes & 0x400)


def _assert_managed_path_contained(root: Path, path: Path) -> None:
    root_absolute = root.absolute()
    try:
        relative = path.absolute().relative_to(root_absolute)
    except ValueError as error:
        raise ValueError("managed attachment path escapes store root") from error
    current = root_absolute
    if _is_link_or_reparse(current):
        raise ValueError("managed attachment root is a link or reparse point")
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link_or_reparse(current):
            raise ValueError("managed attachment path contains a link or reparse point")
    resolved_root = root_absolute.resolve(strict=False)
    resolved_path = path.absolute().resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("managed attachment path escapes resolved store root") from error


def _read_verified_object(path: Path, *, max_bytes: int) -> tuple[bytes, str, int]:
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        content = handle.read(max_bytes + 1)
        after = os.fstat(handle.fileno())
    if len(content) > max_bytes:
        raise ValueError("attachment object is too large")
    if before.st_size != after.st_size or after.st_size != len(content):
        raise ValueError("attachment object changed while hashing")
    return content, sha256(content).hexdigest(), len(content)


def _verify_managed_record(
    store: CaptureCheckpointStore,
    capture_id: str,
    record: Mapping[str, Any],
    *,
    max_bytes: int = 100_000_000,
) -> None:
    _, object_hash, size = _read_verified_object(
        _managed_object_path(store, capture_id, str(record.get("managed_ref") or "")),
        max_bytes=max_bytes,
    )
    if object_hash != record.get("sha256") or size != record.get("size"):
        raise ValueError("managed attachment object does not match ledger")


def record_persisted_attachment_save(
    store: CaptureCheckpointStore,
    capture_id: str,
    attachment_id: str,
    object_file: Path,
    private_ref: str,
    *,
    expected_sequence: int,
    max_bytes: int = 100_000_000,
) -> dict[str, Any]:
    """Hash one saved object and CAS-append metadata without exposing its path."""

    if not attachment_id:
        raise ValueError("attachment save identity is invalid")
    private_ref = _validated_private_ref(private_ref)
    content, object_hash, object_size = _read_verified_object(
        object_file, max_bytes=max_bytes
    )
    managed_ref = f"attachment-objects/{capture_id}/{private_ref}"
    with store.transition_lock(capture_id):
        message_ledger = store.load_message_ledger(capture_id)
        if message_ledger is None:
            raise SequenceConflictError("message ledger does not exist")
        discovered = {
            str(item)
            for event in message_ledger["events"]
            for item in list(event.get("attachment_ids") or [])
        }
        if attachment_id not in discovered:
            raise SequenceConflictError("attachment id was not discovered")
        ledger = store.load_attachment_save_ledger(capture_id) or _new_attachment_save_ledger(
            capture_id
        )
        records = list(ledger["records"])
        existing = next(
            (item for item in records if item.get("attachment_id") == attachment_id), None
        )
        comparable = {
            "attachment_id": attachment_id,
            "sha256": object_hash,
            "size": object_size,
            "managed_ref": managed_ref,
        }
        if existing is not None:
            if {key: existing.get(key) for key in comparable} != comparable:
                raise SequenceConflictError("attachment save record is immutable")
            return {
                "capture_id": capture_id,
                "attachment_sequence": len(records),
                "idempotent": True,
                "raw_text_returned": False,
                "path_output": "omitted",
                "outbound_actions": "disabled",
            }
        if ledger.get("seal") is not None or len(records) != expected_sequence:
            raise SequenceConflictError("attachment save ledger is sealed or stale")
        destination = _managed_object_path(store, capture_id, managed_ref)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_managed_path_contained(store.root, destination)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        record = {"sequence": len(records) + 1, **comparable}
        records.append(record)
        updated = {
            **ledger,
            "records": records,
            "tip_hash": canonical_capture_digest(records),
        }
        store.save_attachment_save_ledger(updated, expected_sequence=expected_sequence)
        return {
            "capture_id": capture_id,
            "attachment_sequence": len(records),
            "idempotent": False,
            "raw_text_returned": False,
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }


def seal_persisted_attachment_inventory(
    store: CaptureCheckpointStore,
    capture_id: str,
    *,
    expected_sequence: int,
) -> dict[str, Any]:
    """Seal all discovered attachment saves to current durable capture evidence."""

    with store.transition_lock(capture_id):
        message_ledger = store.load_message_ledger(capture_id)
        coverage = store.load_coverage(capture_id)
        if message_ledger is None or coverage is None:
            raise SequenceConflictError("durable capture evidence does not exist")
        ledger = store.load_attachment_save_ledger(capture_id) or _new_attachment_save_ledger(
            capture_id
        )
        records = list(ledger["records"])
        if len(records) != expected_sequence:
            raise SequenceConflictError("attachment save ledger sequence conflict")
        for record in records:
            _verify_managed_record(store, capture_id, record)
        discovered = sorted(
            {
                str(item)
                for event in message_ledger["events"]
                for item in list(event.get("attachment_ids") or [])
            }
        )
        if discovered != sorted(str(item["attachment_id"]) for item in records):
            raise SequenceConflictError("attachment inventory is incomplete")
        seal = {
            "message_sequence": len(message_ledger["events"]),
            "message_tip_hash": canonical_capture_digest(message_ledger["events"]),
            "coverage_digest": canonical_capture_digest(coverage),
            "window_count": len(coverage["windows"]),
            "attachment_tip_hash": str(ledger["tip_hash"]),
        }
        if ledger.get("seal") is not None and ledger["seal"] != seal:
            raise SequenceConflictError("attachment inventory seal is stale")
        updated = {**ledger, "seal": seal}
        store.save_attachment_save_ledger(updated, expected_sequence=expected_sequence)
        return {
            "capture_id": capture_id,
            "attachment_sequence": len(records),
            "sealed": True,
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        }


def verified_persisted_attachment_ids(
    store: CaptureCheckpointStore,
    capture_id: str,
    ledger: Mapping[str, Any],
) -> list[str] | None:
    """Return managed attachment IDs only when every object still matches."""

    try:
        for record in list(ledger.get("records") or []):
            _verify_managed_record(store, capture_id, record)
    except (OSError, ValueError):
        return None
    return [str(item.get("attachment_id") or "") for item in ledger.get("records") or []]


def _append_window_events(
    ledger: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Append metadata-only message observations in their window-local order."""

    updated = dict(ledger)
    window_id = str(observation.get("window_id") or "").strip()
    source = str(observation.get("source") or "").strip()
    messages = observation.get("messages")
    if not isinstance(messages, list):
        return updated
    for window_index, item in enumerate(messages):
        if not isinstance(item, Mapping):
            continue
        message_id = str(item.get("message_id") or "").strip()
        content_hash = str(item.get("content_hash") or "").strip()
        if not message_id or not content_hash:
            continue
        identity = {
            "window_id": window_id,
            "window_index": window_index,
            "message_id": message_id,
            "content_hash": content_hash,
            "source": source,
        }
        event = {
            "event_id": _event_digest(identity),
            "sequence": len(updated["events"]) + 1,
            "type": "message_observed",
            **identity,
            "attachment_ids": item.get("attachment_ids", []),
        }
        if item.get("content_ref"):
            event["content_ref"] = str(item["content_ref"])
        existing = next(
            (
                candidate
                for candidate in updated["events"]
                if candidate.get("event_id") == event["event_id"]
            ),
            None,
        )
        updated = append_message_event(updated, existing or event)
    return updated


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
        ledger = store.load_message_ledger(capture_id) or new_message_ledger(
            capture_id,
            target_key=str(run["target_digest"]),
            upper_watermark=str(run["upper_watermark_digest"]),
        )
        current_sequence = len(ledger["events"])
        updated_ledger = _append_window_events(ledger, observation)
        store.invalidate_full_capture_receipt(capture_id)
        store.save_message_ledger(
            updated_ledger,
            expected_sequence=current_sequence,
        )
        store.save_coverage(
            updated,
            expected_window_count=expected_window_count,
        )
        result = build_capture_status_projection(run)
        result["coverage"] = _coverage_projection(updated)
        result["message_event_sequence"] = len(updated_ledger["events"])
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
        ledger = store.load_message_ledger(capture_id) or new_message_ledger(
            capture_id,
            target_key=str(run["target_digest"]),
            upper_watermark=str(run["upper_watermark_digest"]),
        )
        current_sequence = len(ledger["events"])
        for _, observation in ordered:
            source = str(observation.get("source") or "")
            coverage = merge_capture_window(coverage, observation)
            ledger = _append_window_events(ledger, observation)
            intake_order.append(source)
            if source in cache_sources:
                cache_message_count = coverage["unique_message_count"]

        coverage["cache_first_applied"] = True
        coverage["initial_cache_message_count"] = cache_message_count
        coverage["intake_order"] = intake_order
        store.invalidate_full_capture_receipt(capture_id)
        store.save_message_ledger(ledger, expected_sequence=current_sequence)
        store.save_coverage(
            coverage,
            expected_window_count=expected_window_count,
        )
        result = build_capture_status_projection(run)
        result["coverage"] = _coverage_projection(coverage)
        result["message_event_sequence"] = len(ledger["events"])
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
        store.invalidate_full_capture_receipt(capture_id)
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
    """Rebuild all views in memory; projection state is never persisted.

    Full confirmation is delegated to ``evaluate_full_capture``. Ledger
    ``full_candidate`` alone never confirms full capture.
    """

    from discord_context_bridge.full_capture import evaluate_full_capture

    from .message_ledger import build_strict_full_capture_evidence_from_projections

    ledger = store.load_message_ledger(capture_id)
    if ledger is None:
        raise SequenceConflictError("message ledger does not exist")
    coverage = store.load_coverage(capture_id)
    measured_gap_count = int(coverage.get("gap_count") or 0) if coverage else 0
    projections = build_capture_projections(
        ledger,
        oldest_reached=oldest_reached,
        latest_reached=latest_reached,
        stable_scan_digests=stable_scan_digests,
        saved_attachment_ids=saved_attachment_ids,
        upper_watermark_reached=upper_watermark_reached,
        unresolved_gap_count=max(int(unresolved_gap_count), measured_gap_count),
        pending_retry_count=pending_retry_count,
        attachment_inventory_complete=attachment_inventory_complete,
    )
    run = store.load_checkpoint(capture_id)
    route = str((run or {}).get("route") or "unknown")
    strict_evidence = build_strict_full_capture_evidence_from_projections(
        projections,
        route=route,
    )
    gate = evaluate_full_capture(strict_evidence)
    return {
        **projections,
        "full_capture_gate": {
            "schema": gate.get("schema"),
            "status": gate.get("status"),
            "full_capture_confirmed": bool(gate.get("full_capture_confirmed")),
            "blockers": list(gate.get("blockers") or []),
            "counts_consistent": bool(gate.get("counts_consistent")),
            "attachments_consistent": bool(gate.get("attachments_consistent")),
            "full_candidate": bool(projections.get("evidence", {}).get("full_candidate")),
            "outbound_actions": "disabled",
            "raw_text_returned": False,
        },
    }


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
                store.invalidate_full_capture_receipt(capture_id)
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
        store.invalidate_full_capture_receipt(capture_id)
        store.append_event(ledger_event, expected_sequence=expected_sequence)
        store.save_checkpoint(updated, expected_sequence=expected_sequence)
        return build_capture_status_projection(updated)
