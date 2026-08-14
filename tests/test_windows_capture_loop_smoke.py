from __future__ import annotations

import json

from discord_context_bridge.capture.service import (
    merge_persisted_capture_window,
    start_capture_loop,
)
from discord_context_bridge.capture.store import CaptureCheckpointStore
from discord_context_bridge.cli import main


def test_no_attachment_capture_loop_is_metadata_only_and_persists_receipt(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "discord_context_bridge.capture.store._secure_store_ops_supported",
        lambda: False,
    )
    store = CaptureCheckpointStore(tmp_path)
    private_target = r"C:\\DCB\\private\\discord-target"
    capture_id = start_capture_loop(
        store,
        private_target,
        "saved_artifacts",
        "message-2",
    )["capture_id"]

    advance_code = main(
        [
            "capture-loop",
            "advance",
            "--store-root",
            str(tmp_path),
            "--capture-id",
            capture_id,
            "--event",
            "visible_snapshot_saved",
            "--event-id",
            "windows-smoke-visible-1",
            "--expected-sequence",
            "0",
            "--json",
        ]
    )
    advance_output = capsys.readouterr().out
    assert advance_code == 0
    assert private_target not in advance_output
    assert str(tmp_path) not in advance_output

    status_code = main(
        [
            "capture-loop",
            "status",
            "--store-root",
            str(tmp_path),
            "--capture-id",
            capture_id,
            "--json",
        ]
    )
    status_output = capsys.readouterr().out
    assert status_code == 0
    assert private_target not in status_output
    assert str(tmp_path) not in status_output

    for index in (1, 2):
        merge_persisted_capture_window(
            store,
            capture_id,
            {
                "window_id": f"window-{index}",
                "source": "chrome_visible_dom",
                "direction": "toward_latest",
                "scan_pass": index,
                "oldest_reached": True,
                "latest_reached": True,
                "messages": [
                    {"message_id": "message-1", "content_hash": "hash-1"},
                    {"message_id": "message-2", "content_hash": "hash-2"},
                ],
            },
            expected_window_count=index - 1,
        )

    code = main(
        [
            "capture-loop",
            "reconcile",
            "--store-root",
            str(tmp_path),
            "--capture-id",
            capture_id,
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["status"] == "full"
    assert payload["receipt_persisted"] is True
    assert private_target not in output
    assert str(tmp_path) not in output
    assert store.load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    ) is not None
