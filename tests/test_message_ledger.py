from __future__ import annotations

from discord_context_bridge.capture.message_ledger import (
    append_message_event,
    build_capture_projections,
    new_message_ledger,
)
from discord_context_bridge.capture.service import (
    append_persisted_message_event,
    rebuild_persisted_capture_projections,
    start_capture_loop,
)
from discord_context_bridge.capture.store import CaptureCheckpointStore


def _observed(
    event_id: str,
    sequence: int,
    message_id: str,
    content_hash: str,
    *,
    attachment_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "sequence": sequence,
        "type": "message_observed",
        "message_id": message_id,
        "content_hash": content_hash,
        "attachment_ids": attachment_ids or [],
        "source": "chrome_visible_dom",
    }


def test_single_event_ledger_rebuilds_all_capture_projections() -> None:
    ledger = new_message_ledger(
        "capture-1",
        target_key="private-target",
        upper_watermark="message-2",
    )
    ledger = append_message_event(
        ledger,
        _observed("event-1", 1, "message-1", "hash-1"),
    )
    ledger = append_message_event(
        ledger,
        _observed(
            "event-2",
            2,
            "message-2",
            "hash-2",
            attachment_ids=["attachment-1"],
        ),
    )
    ledger = append_message_event(
        ledger,
        _observed("event-3", 3, "message-1", "hash-1-edited"),
    )

    projections = build_capture_projections(
        ledger,
        oldest_reached=True,
        latest_reached=True,
        stable_scan_digests=["scan-digest", "scan-digest"],
        saved_attachment_ids=["attachment-1"],
        upper_watermark_reached=True,
        unresolved_gap_count=0,
        pending_retry_count=0,
        attachment_inventory_complete=True,
    )

    assert projections["normalized"]["message_count"] == 2
    assert projections["normalized"]["edited_message_count"] == 1
    assert projections["raw"]["message_ids"] == ["message-1", "message-2"]
    assert projections["markdown"]["message_ids"] == ["message-1", "message-2"]
    assert projections["attachment_manifest"]["attachment_ids"] == [
        "attachment-1"
    ]
    assert projections["evidence"]["message_id_sets_equal"] is True
    assert projections["evidence"]["ordered_message_digest_equal"] is True
    assert projections["evidence"]["capture_stable_after_rescan"] is True
    assert projections["evidence"]["full_candidate"] is True


def test_projection_rebuild_is_deterministic_and_does_not_mutate_ledger() -> None:
    ledger = new_message_ledger(
        "capture-1",
        target_key="private-target",
        upper_watermark="message-2",
    )
    ledger = append_message_event(
        ledger,
        _observed("event-1", 1, "message-1", "hash-1"),
    )
    ledger = append_message_event(
        ledger,
        _observed("event-2", 2, "message-2", "hash-2"),
    )

    first = build_capture_projections(
        ledger,
        oldest_reached=True,
        latest_reached=True,
        stable_scan_digests=["same", "same"],
        saved_attachment_ids=[],
        upper_watermark_reached=True,
        unresolved_gap_count=0,
        pending_retry_count=0,
        attachment_inventory_complete=True,
    )
    second = build_capture_projections(
        ledger,
        oldest_reached=True,
        latest_reached=True,
        stable_scan_digests=["same", "same"],
        saved_attachment_ids=[],
        upper_watermark_reached=True,
        unresolved_gap_count=0,
        pending_retry_count=0,
        attachment_inventory_complete=True,
    )

    assert first == second
    assert ledger["events"][-1]["event_id"] == "event-2"


def test_event_ledger_rejects_sequence_gaps_and_event_id_rebinding() -> None:
    ledger = new_message_ledger(
        "capture-1",
        target_key="private-target",
        upper_watermark="message-1",
    )
    ledger = append_message_event(
        ledger,
        _observed("event-1", 1, "message-1", "hash-1"),
    )

    try:
        append_message_event(
            ledger,
            _observed("event-2", 3, "message-2", "hash-2"),
        )
    except ValueError as exc:
        assert "sequence" in str(exc)
    else:
        raise AssertionError("sequence gaps must fail")

    try:
        append_message_event(
            ledger,
            _observed("event-1", 2, "message-2", "hash-2"),
        )
    except ValueError as exc:
        assert "event_id" in str(exc)
    else:
        raise AssertionError("event id rebinding must fail")


def test_exact_event_replay_is_idempotent() -> None:
    ledger = new_message_ledger(
        "capture-1",
        target_key="private-target",
        upper_watermark="message-1",
    )
    event = _observed("event-1", 1, "message-1", "hash-1")
    first = append_message_event(ledger, event)
    replayed = append_message_event(first, event)

    assert replayed == first
    assert len(replayed["events"]) == 1


def test_persisted_capture_rebuilds_views_without_storing_projection_state(
    tmp_path,
) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "message-2",
    )
    append_persisted_message_event(
        store,
        started["capture_id"],
        _observed("event-1", 1, "message-1", "hash-1"),
        expected_sequence=0,
    )
    append_persisted_message_event(
        store,
        started["capture_id"],
        _observed("event-2", 2, "message-2", "hash-2"),
        expected_sequence=1,
    )

    rebuilt = rebuild_persisted_capture_projections(
        store,
        started["capture_id"],
        oldest_reached=True,
        latest_reached=True,
        stable_scan_digests=["same", "same"],
        saved_attachment_ids=[],
        upper_watermark_reached=True,
        unresolved_gap_count=0,
        pending_retry_count=0,
        attachment_inventory_complete=True,
    )

    assert store.load_message_ledger(started["capture_id"]) is not None
    assert rebuilt["normalized"]["message_count"] == 2
    assert rebuilt["evidence"]["full_candidate"] is True
    assert not (tmp_path / "projections").exists()


def test_full_candidate_fails_closed_on_coverage_or_attachment_uncertainty() -> None:
    ledger = new_message_ledger(
        "capture-1",
        target_key="private-target",
        upper_watermark="message-1",
    )
    ledger = append_message_event(
        ledger,
        _observed("event-1", 1, "message-1", "hash-1"),
    )

    projections = build_capture_projections(
        ledger,
        oldest_reached=True,
        latest_reached=True,
        stable_scan_digests=["same", "same"],
        saved_attachment_ids=[],
        upper_watermark_reached=False,
        unresolved_gap_count=1,
        pending_retry_count=1,
        attachment_inventory_complete=False,
    )

    assert projections["evidence"]["full_candidate"] is False
    assert projections["evidence"]["upper_watermark_reached"] is False
    assert projections["evidence"]["unresolved_gap_count"] == 1
    assert projections["evidence"]["pending_retry_count"] == 1
    assert projections["evidence"]["attachment_inventory_complete"] is False
