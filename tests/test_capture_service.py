from __future__ import annotations

import json
from hashlib import sha256

import pytest

from discord_context_bridge.capture.service import (
    advance_persisted_capture,
    start_capture_loop,
)
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    SequenceConflictError,
)
from discord_context_bridge.cli import main


def test_start_and_advance_are_metadata_only_and_durable(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    status = start_capture_loop(
        store,
        "private-discord-url",
        "chrome_extension",
        "latest-message",
        tag_context={"scope": "thread_only", "refresh_check": True},
    )
    capture_id = status["capture_id"]

    assert status["raw_text_returned"] is False
    assert "private-discord-url" not in str(status)
    advanced = advance_persisted_capture(
        store,
        capture_id,
        "visible-snapshot-1",
        "visible_snapshot_saved",
        expected_sequence=0,
    )

    assert advanced["state"] == "route_preflight"
    assert len(store.load_events(capture_id)) == 1
    assert len(store.load_checkpoint(capture_id)["checkpoints"]) == 1
    duplicate = advance_persisted_capture(
        store,
        capture_id,
        "visible-snapshot-1",
        "visible_snapshot_saved",
        expected_sequence=0,
    )
    assert duplicate == advanced
    assert len(store.load_events(capture_id)) == 1


def test_stale_transition_fails_closed(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    status = start_capture_loop(
        store, "private-target", "in_app_browser", "latest-message"
    )
    capture_id = status["capture_id"]
    advance_persisted_capture(
        store,
        capture_id,
        "visible-snapshot-1",
        "visible_snapshot_saved",
        expected_sequence=0,
    )

    with pytest.raises(SequenceConflictError):
        advance_persisted_capture(
            store,
            capture_id,
            "visible-snapshot-2",
            "visible_snapshot_saved",
            expected_sequence=0,
        )


def test_duplicate_event_recovers_checkpoint_after_ledger_first_crash(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    status = start_capture_loop(
        store, "private-target", "saved_artifacts", "latest-message"
    )
    capture_id = status["capture_id"]
    store.append_event(
        {
            "schema": "dcb-capture-event.v1",
            "event_id": "route-ready-1",
            "capture_id": capture_id,
            "sequence": 1,
            "event": "route_ready",
            "semantic_event_digest": sha256(
                json.dumps(
                    {"type": "route_ready"},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "state": "traversing_to_oldest",
            "blocker": None,
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        },
        expected_sequence=0,
    )

    recovered = advance_persisted_capture(
        store,
        capture_id,
        "route-ready-1",
        "route_ready",
        expected_sequence=0,
    )

    assert recovered["state"] == "traversing_to_oldest"
    assert len(store.load_checkpoint(capture_id)["checkpoints"]) == 1


def test_capture_loop_cli_start_is_metadata_only(tmp_path, capsys) -> None:
    code = main(
        [
            "capture-loop",
            "start",
            "--store-root",
            str(tmp_path),
            "--target-key",
            "private-discord-url",
            "--route",
            "saved_artifacts",
            "--upper-watermark",
            "latest-message",
            "--scope",
            "thread_only",
            "--refresh-check",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert "private-discord-url" not in output
    assert '"outbound_actions": "disabled"' in output
    assert '"thread-only"' in output


def test_capture_loop_cli_does_not_echo_invalid_event(tmp_path, capsys) -> None:
    status = start_capture_loop(
        CaptureCheckpointStore(tmp_path),
        "private-target",
        "in_app_browser",
        "latest-message",
    )
    code = main(
        [
            "capture-loop",
            "advance",
            "--store-root",
            str(tmp_path),
            "--capture-id",
            status["capture_id"],
            "--event",
            "private-discord-url",
            "--event-id",
            "invalid-event-1",
            "--expected-sequence",
            "0",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert "private-discord-url" not in output
    assert '"reason": "capture_loop_operation_failed"' in output
