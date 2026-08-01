from __future__ import annotations

import json

from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.capture.virtual_scroll import (
    merge_capture_window,
    new_virtual_scroll_coverage,
)
from discord_context_bridge.capture.service import (
    merge_capture_windows_cache_first,
    merge_persisted_capture_window,
    read_capture_loop_status,
    start_capture_loop,
)
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    SequenceConflictError,
)


def _message(message_id: str, text_hash: str) -> dict[str, str]:
    return {
        "message_id": message_id,
        "content_hash": text_hash,
    }


def test_overlapping_virtual_windows_are_deduplicated_by_message_id() -> None:
    coverage = new_virtual_scroll_coverage("capture-1")

    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "window-top",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [
                _message("100", "h100"),
                _message("200", "h200"),
                _message("300", "h300"),
            ],
        },
    )
    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "window-middle",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [
                _message("300", "h300"),
                _message("400", "h400"),
                _message("500", "h500"),
            ],
        },
    )

    assert coverage["unique_message_count"] == 5
    assert coverage["duplicate_observation_count"] == 1
    assert coverage["gap_count"] == 0
    assert coverage["ordered_message_ids"] == ["100", "200", "300", "400", "500"]


def test_cache_and_dom_windows_share_the_same_message_identity_space() -> None:
    coverage = new_virtual_scroll_coverage("capture-1")
    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "cache-window",
            "source": "saved_cache",
            "direction": "toward_latest",
            "messages": [
                _message("100", "h100"),
                _message("200", "h200"),
            ],
        },
    )
    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "dom-window",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [
                _message("200", "h200"),
                _message("300", "h300"),
            ],
        },
    )

    assert coverage["unique_message_count"] == 3
    assert coverage["sources"] == ["chrome_visible_dom", "saved_cache"]
    assert coverage["duplicate_observation_count"] == 1
    assert coverage["gap_count"] == 0


def test_edited_message_keeps_one_identity_and_multiple_versions() -> None:
    coverage = new_virtual_scroll_coverage("capture-1")
    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "before-edit",
            "source": "saved_cache",
            "direction": "toward_latest",
            "messages": [_message("100", "old-hash")],
        },
    )
    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "after-edit",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [_message("100", "new-hash")],
        },
    )

    assert coverage["unique_message_count"] == 1
    assert coverage["messages"]["100"]["version_hashes"] == [
        "old-hash",
        "new-hash",
    ]
    assert coverage["edited_message_count"] == 1


def test_disjoint_windows_are_a_gap_until_a_bridge_window_arrives() -> None:
    coverage = new_virtual_scroll_coverage("capture-1")
    for window in (
        {
            "window_id": "top",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [_message("100", "h100"), _message("200", "h200")],
        },
        {
            "window_id": "bottom",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [_message("400", "h400"), _message("500", "h500")],
        },
    ):
        coverage = merge_capture_window(coverage, window)

    assert coverage["gap_count"] == 1
    assert coverage["coverage_connected"] is False

    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "bridge",
            "source": "background_cache",
            "direction": "toward_latest",
            "messages": [
                _message("200", "h200"),
                _message("300", "h300"),
                _message("400", "h400"),
            ],
        },
    )
    assert coverage["gap_count"] == 0
    assert coverage["coverage_connected"] is True


def test_stable_rescan_requires_two_complete_passes_with_no_new_ids() -> None:
    coverage = new_virtual_scroll_coverage("capture-1")
    complete = {
        "source": "chrome_visible_dom",
        "direction": "toward_latest",
        "oldest_reached": True,
        "latest_reached": True,
        "messages": [_message("100", "h100"), _message("200", "h200")],
    }

    coverage = merge_capture_window(
        coverage,
        {**complete, "window_id": "pass-1", "scan_pass": 1},
    )
    assert coverage["capture_stable_after_rescan"] is False

    coverage = merge_capture_window(
        coverage,
        {**complete, "window_id": "pass-2", "scan_pass": 2},
    )
    assert coverage["capture_stable_after_rescan"] is True
    assert coverage["stable_scan_passes"] == 2
    assert coverage["final_pass_new_message_count"] == 0


def test_stable_rescan_aggregates_multiple_windows_per_pass() -> None:
    coverage = new_virtual_scroll_coverage("capture-1")
    observations = [
        {
            "window_id": "p1-top",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "scan_pass": 1,
            "oldest_reached": True,
            "messages": [_message("100", "h100"), _message("200", "h200")],
        },
        {
            "window_id": "p1-bottom",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "scan_pass": 1,
            "latest_reached": True,
            "messages": [_message("200", "h200"), _message("300", "h300")],
        },
        {
            "window_id": "p2-top",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "scan_pass": 2,
            "oldest_reached": True,
            "messages": [_message("100", "h100"), _message("200", "h200")],
        },
        {
            "window_id": "p2-bottom",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "scan_pass": 2,
            "latest_reached": True,
            "messages": [_message("200", "h200"), _message("300", "h300")],
        },
    ]
    for observation in observations:
        coverage = merge_capture_window(coverage, observation)

    assert coverage["stable_scan_passes"] == 2
    assert coverage["final_pass_new_message_count"] == 0
    assert coverage["capture_stable_after_rescan"] is True


def test_missing_message_id_blocks_full_coverage_instead_of_hash_deduping() -> None:
    coverage = new_virtual_scroll_coverage("capture-1")
    coverage = merge_capture_window(
        coverage,
        {
            "window_id": "unsafe-window",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [{"message_id": "", "content_hash": "body-only"}],
        },
    )

    assert coverage["unique_message_count"] == 0
    assert coverage["invalid_observation_count"] == 1
    assert "stable_message_id_missing" in coverage["blockers"]
    assert coverage["capture_stable_after_rescan"] is False


def test_persisted_loop_merges_background_cache_and_dom_windows(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "watermark",
    )

    first = merge_persisted_capture_window(
        store,
        started["capture_id"],
        {
            "window_id": "cache-1",
            "source": "background_cache",
            "direction": "toward_latest",
            "messages": [_message("100", "h100"), _message("200", "h200")],
        },
        expected_window_count=0,
    )
    assert first["coverage"]["unique_message_count"] == 2
    assert first["coverage"]["sources"] == ["background_cache"]

    second = merge_persisted_capture_window(
        store,
        started["capture_id"],
        {
            "window_id": "dom-1",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "messages": [_message("200", "h200"), _message("300", "h300")],
        },
        expected_window_count=1,
    )
    assert second["coverage"]["unique_message_count"] == 3
    assert second["coverage"]["gap_count"] == 0

    status = read_capture_loop_status(store, started["capture_id"])
    assert status["coverage"]["unique_message_count"] == 3
    assert status["coverage"]["window_count"] == 2
    assert status["raw_text_returned"] is False


def test_cache_first_batch_ingests_cache_before_browser_regardless_of_arrival_order(
    tmp_path,
) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "watermark",
    )

    result = merge_capture_windows_cache_first(
        store,
        started["capture_id"],
        [
            {
                "window_id": "dom-1",
                "source": "chrome_visible_dom",
                "direction": "toward_latest",
                "messages": [_message("200", "h200"), _message("300", "h300")],
            },
            {
                "window_id": "cache-1",
                "source": "background_cache",
                "direction": "toward_latest",
                "messages": [_message("100", "h100"), _message("200", "h200")],
            },
        ],
        expected_window_count=0,
    )

    assert result["coverage"]["cache_first_applied"] is True
    assert result["coverage"]["initial_cache_message_count"] == 2
    assert result["coverage"]["intake_order"] == [
        "background_cache",
        "chrome_visible_dom",
    ]
    assert result["coverage"]["unique_message_count"] == 3
    assert result["message_event_sequence"] == 4
    ledger = store.load_message_ledger(started["capture_id"])
    assert ledger is not None
    assert [event["source"] for event in ledger["events"]] == [
        "background_cache",
        "background_cache",
        "chrome_visible_dom",
        "chrome_visible_dom",
    ]


def test_persisted_window_merge_rejects_stale_expected_count(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    started = start_capture_loop(
        store,
        "private-target",
        "chrome_extension",
        "watermark",
    )
    observation = {
        "window_id": "cache-1",
        "source": "background_cache",
        "direction": "toward_latest",
        "messages": [_message("100", "h100")],
    }
    merge_persisted_capture_window(
        store,
        started["capture_id"],
        observation,
        expected_window_count=0,
    )

    try:
        merge_persisted_capture_window(
            store,
            started["capture_id"],
            {**observation, "window_id": "cache-2"},
            expected_window_count=0,
        )
    except SequenceConflictError as exc:
        assert "window count conflict" in str(exc)
    else:
        raise AssertionError("stale expected window count must fail")


def test_capture_loop_cli_observe_returns_metadata_only(tmp_path, capsys) -> None:
    store_root = tmp_path / "store"
    started = start_capture_loop(
        CaptureCheckpointStore(store_root),
        "private-target",
        "chrome_extension",
        "watermark",
    )
    window_file = tmp_path / "window.json"
    window_file.write_text(
        json.dumps(
            {
                "window_id": "cache-1",
                "source": "background_cache",
                "direction": "toward_latest",
                "messages": [
                    {
                        "message_id": "private-message-id",
                        "content_hash": "private-content-hash",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    code = cli_main(
        [
            "capture-loop",
            "observe",
            "--store-root",
            str(store_root),
            "--capture-id",
            started["capture_id"],
            "--window-file",
            str(window_file),
            "--expected-window-count",
            "0",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert "private-message-id" not in output
    assert "private-content-hash" not in output
    assert '"window_count": 1' in output
    assert '"unique_message_count": 1' in output
    assert '"message_event_sequence": 1' in output
    assert '"raw_text_returned": false' in output
    ledger = CaptureCheckpointStore(store_root).load_message_ledger(
        started["capture_id"]
    )
    assert ledger is not None
    assert len(ledger["events"]) == 1
