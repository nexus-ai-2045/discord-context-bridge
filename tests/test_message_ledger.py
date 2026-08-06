from __future__ import annotations

import pytest

from discord_context_bridge.capture.message_ledger import (
    append_message_event,
    build_capture_projections,
    build_strict_full_capture_evidence_from_projections,
    new_message_ledger,
)
from discord_context_bridge.full_capture import evaluate_full_capture
from discord_context_bridge.capture.service import (
    append_persisted_message_event,
    merge_persisted_capture_window,
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
    # full_candidate は候補。最終 full は既存 evaluate_full_capture のみ。
    assert rebuilt["full_capture_gate"]["schema"] == "discord_full_capture_completion_gate.v1"
    assert rebuilt["full_capture_gate"]["full_capture_confirmed"] is True
    assert rebuilt["full_capture_gate"]["full_candidate"] is True
    assert not (tmp_path / "projections").exists()


def test_ledger_full_candidate_is_not_a_second_full_ssot() -> None:
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
        upper_watermark_reached=True,
        unresolved_gap_count=0,
        pending_retry_count=0,
        attachment_inventory_complete=True,
    )
    assert projections["evidence"]["full_candidate"] is True
    # derived evidence alone is untrusted by evaluate_full_capture
    direct = evaluate_full_capture(projections["evidence"])
    assert direct["full_capture_confirmed"] is False
    assert "untrusted_evidence_producer" in direct["blockers"]
    # bridge through reconcile producer is the only allowed path
    bridged = build_strict_full_capture_evidence_from_projections(
        projections,
        route="chrome_extension",
    )
    gate = evaluate_full_capture(bridged)
    assert gate["full_capture_confirmed"] is True
    assert bridged["evidence_producer"] == "discord_context_bridge.capture.reconcile.v1"


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


def test_window_observation_is_automatically_appended_to_message_ledger(
    tmp_path,
) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "message-2",
    )

    result = merge_persisted_capture_window(
        store,
        started["capture_id"],
        {
            "window_id": "window-1",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [
                {"message_id": "message-1", "content_hash": "hash-1"},
                {"message_id": "message-2", "content_hash": "hash-2"},
            ],
        },
        expected_window_count=0,
    )

    ledger = store.load_message_ledger(started["capture_id"])
    assert ledger is not None
    assert [event["message_id"] for event in ledger["events"]] == [
        "message-1",
        "message-2",
    ]
    assert result["message_event_sequence"] == 2
    assert result["raw_text_returned"] is False


def test_ledger_projection_uses_overlapping_window_order_not_first_seen(
    tmp_path,
) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "message-3",
    )
    capture_id = started["capture_id"]

    merge_persisted_capture_window(
        store,
        capture_id,
        {
            "window_id": "later-window",
            "source": "saved_cache",
            "direction": "toward_latest",
            "messages": [
                {"message_id": "message-2", "content_hash": "hash-2"},
                {"message_id": "message-3", "content_hash": "hash-3"},
            ],
        },
        expected_window_count=0,
    )
    merge_persisted_capture_window(
        store,
        capture_id,
        {
            "window_id": "earlier-window",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [
                {"message_id": "message-1", "content_hash": "hash-1"},
                {"message_id": "message-2", "content_hash": "hash-2"},
            ],
        },
        expected_window_count=1,
    )

    rebuilt = rebuild_persisted_capture_projections(
        store,
        capture_id,
        oldest_reached=True,
        latest_reached=True,
        stable_scan_digests=["same", "same"],
        saved_attachment_ids=[],
        upper_watermark_reached=True,
        unresolved_gap_count=0,
        pending_retry_count=0,
        attachment_inventory_complete=True,
    )

    assert rebuilt["raw"]["message_ids"] == [
        "message-1",
        "message-2",
        "message-3",
    ]


def test_window_observation_rejects_non_list_attachment_ids(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "message-1",
    )

    with pytest.raises(ValueError, match="attachment_ids"):
        merge_persisted_capture_window(
            store,
            started["capture_id"],
            {
                "window_id": "window-1",
                "source": "chrome_visible_dom",
                "direction": "toward_latest",
                "messages": [
                    {
                        "message_id": "message-1",
                        "content_hash": "hash-1",
                        "attachment_ids": "abc",
                    }
                ],
            },
            expected_window_count=0,
        )
    assert store.load_message_ledger(started["capture_id"]) is None
    assert store.load_coverage(started["capture_id"]) is None


def test_conflicting_window_order_fails_closed() -> None:
    ledger = new_message_ledger(
        "capture-1",
        target_key="private-target",
        upper_watermark="message-2",
    )
    for event in (
        {
            **_observed("event-1", 1, "message-1", "hash-1"),
            "window_id": "window-a",
            "window_index": 0,
        },
        {
            **_observed("event-2", 2, "message-2", "hash-2"),
            "window_id": "window-a",
            "window_index": 1,
        },
        {
            **_observed("event-3", 3, "message-2", "hash-2"),
            "window_id": "window-b",
            "window_index": 0,
        },
        {
            **_observed("event-4", 4, "message-1", "hash-1"),
            "window_id": "window-b",
            "window_index": 1,
        },
    ):
        ledger = append_message_event(ledger, event)

    projections = build_capture_projections(
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

    assert projections["evidence"]["message_order_conflict"] is True
    assert projections["evidence"]["ordered_message_digest_equal"] is False
    assert projections["evidence"]["full_candidate"] is False


def test_persisted_projection_uses_measured_gap_even_if_caller_reports_zero(
    tmp_path,
) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "message-4",
    )
    capture_id = started["capture_id"]
    for expected_count, observation in enumerate(
        (
            {
                "window_id": "window-a",
                "source": "chrome_visible_dom",
                "direction": "toward_latest",
                "messages": [
                    {"message_id": "message-1", "content_hash": "hash-1"},
                    {"message_id": "message-2", "content_hash": "hash-2"},
                ],
            },
            {
                "window_id": "window-b",
                "source": "chrome_visible_dom",
                "direction": "toward_latest",
                "messages": [
                    {"message_id": "message-3", "content_hash": "hash-3"},
                    {"message_id": "message-4", "content_hash": "hash-4"},
                ],
            },
        )
    ):
        merge_persisted_capture_window(
            store,
            capture_id,
            observation,
            expected_window_count=expected_count,
        )

    rebuilt = rebuild_persisted_capture_projections(
        store,
        capture_id,
        oldest_reached=True,
        latest_reached=True,
        stable_scan_digests=["same", "same"],
        saved_attachment_ids=[],
        upper_watermark_reached=True,
        unresolved_gap_count=0,
        pending_retry_count=0,
        attachment_inventory_complete=True,
    )

    assert rebuilt["evidence"]["unresolved_gap_count"] == 1
    assert rebuilt["evidence"]["full_candidate"] is False
