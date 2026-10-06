from __future__ import annotations

import json

import pytest

from discord_context_bridge.core import (
    build_bridge_intake,
    canonical_event_hash,
    load_text_snapshots,
    snapshot_visible_text,
)


GUILD = "11111111111111111"
CHANNEL_A = "22222222222222222"
CHANNEL_B = "33333333333333333"
URL_A = f"https://discord.com/channels/{GUILD}/{CHANNEL_A}"
URL_B = f"https://discord.com/channels/{GUILD}/{CHANNEL_B}"


def test_bridge_intake_appends_to_each_stream_in_shared_multi_target_store(tmp_path):
    store = tmp_path / "text-snapshots.ndjson"

    first_a = build_bridge_intake(url=URL_A, text="private-a-1", snapshot_store=store)
    first_b = build_bridge_intake(url=URL_B, text="private-b-1", snapshot_store=store)
    second_a = build_bridge_intake(url=URL_A, text="private-a-2", snapshot_store=store)
    second_b = build_bridge_intake(url=URL_B, text="private-b-2", snapshot_store=store)

    assert all(
        result["snapshot"]["saved"]
        for result in (first_a, first_b, second_a, second_b)
    )
    records = load_text_snapshots(store)
    by_url = {
        url: [record for record in records if record.get("url") == url]
        for url in (URL_A, URL_B)
    }
    for stream in by_url.values():
        assert [record["stream_sequence"] for record in stream] == [1, 2]
        assert stream[1]["previous_event_hash"] == stream[0]["event_hash"]
        assert stream[1]["expected_previous_stream_sequence"] == 1


def test_invalid_other_stream_is_isolated_but_its_own_append_fails_closed(tmp_path):
    store = tmp_path / "text-snapshots.ndjson"
    snapshot_visible_text(text="private-a-1", url=URL_A, path=store)
    snapshot_visible_text(text="private-b-1", url=URL_B, path=store)
    records = load_text_snapshots(store)
    records[1]["event_id"] = "tampered"
    records[1]["event_hash"] = canonical_event_hash(records[1])
    store.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )

    appended_a = snapshot_visible_text(text="private-a-2", url=URL_A, path=store)
    assert appended_a["saved"] is True

    with pytest.raises(ValueError, match="snapshot_store_main_invalid"):
        snapshot_visible_text(text="private-b-2", url=URL_B, path=store)
