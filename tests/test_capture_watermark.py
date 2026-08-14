from __future__ import annotations

import json

from discord_context_bridge.capture.service import (
    merge_persisted_capture_window,
    start_capture_loop,
)
from discord_context_bridge.capture.store import CaptureCheckpointStore
from discord_context_bridge.cli import main


def _stable_capture(tmp_path, *, upper_watermark: str, message_ids: list[str]) -> str:
    store = CaptureCheckpointStore(tmp_path)
    capture_id = start_capture_loop(
        store,
        "private-target",
        "saved_artifacts",
        upper_watermark,
    )["capture_id"]
    messages = [
        {"message_id": message_id, "content_hash": f"hash-{message_id}"}
        for message_id in message_ids
    ]
    for scan_pass in (1, 2):
        merge_persisted_capture_window(
            store,
            capture_id,
            {
                "window_id": f"window-{scan_pass}",
                "source": "saved_snapshot",
                "direction": "toward_latest",
                "scan_pass": scan_pass,
                "oldest_reached": True,
                "latest_reached": True,
                "messages": messages,
            },
            expected_window_count=scan_pass - 1,
        )
    return capture_id


def _reconcile(tmp_path, capsys, capture_id: str) -> dict[str, object]:
    assert main(
        [
            "capture-loop",
            "reconcile",
            "--store-root",
            str(tmp_path),
            "--capture-id",
            capture_id,
            "--json",
        ]
    ) == 0
    return json.loads(capsys.readouterr().out)


def test_reconcile_rejects_latest_edge_without_starting_watermark(
    tmp_path, capsys
) -> None:
    capture_id = _stable_capture(
        tmp_path,
        upper_watermark="starting-watermark",
        message_ids=["other-1", "other-2"],
    )

    payload = _reconcile(tmp_path, capsys, capture_id)

    assert payload["status"] == "partial"
    assert payload["full_capture_confirmed"] is False
    assert payload["receipt_persisted"] is False
    assert "upper_watermark_not_reached" in payload["blockers"]


def test_reconcile_accepts_latest_edge_with_starting_watermark(
    tmp_path, capsys
) -> None:
    capture_id = _stable_capture(
        tmp_path,
        upper_watermark="starting-watermark",
        message_ids=["other-1", "starting-watermark"],
    )

    payload = _reconcile(tmp_path, capsys, capture_id)

    assert payload["status"] == "full"
    assert payload["full_capture_confirmed"] is True
    assert payload["receipt_persisted"] is True
