import json

import pytest

from discord_context_bridge import core


@pytest.mark.parametrize("binding", ["stream_only", "target_with_upstream"])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_snapshot_history_and_counts_follow_canonical_stream_identity(tmp_path, binding, mixed, changed):
    url = "https://example.invalid/history-fixture"
    target = core.stable_text_hash(url)
    original_text = "synthetic original"
    raw = {
        "schema": "dcb.visible_message_record.v1",
        "stream_id": target if binding == "stream_only" else "upstream-stream",
        "stream_sequence": 800,
        "content_hash": core.stable_text_hash(original_text),
        "text": original_text,
    }
    if binding != "stream_only":
        raw["target_key"] = target
    rows = [raw]
    if mixed:
        rows.append({
            "schema": "dcb.visible_message_record.v1",
            "target_key": "different-target", "stream_id": target,
            "content_hash": core.stable_text_hash("different stream content"),
        })
    path = tmp_path / "snapshots.ndjson"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    before = path.read_bytes()
    result = core.snapshot_visible_text(
        text="synthetic edit" if changed else original_text, url=url, path=path,
    )
    assert result["previous_content_hash"] == raw["content_hash"]
    assert result["changed"] is changed
    assert result["duplicate_content"] is not changed
    assert result["snapshot_count_for_target"] == 2
    assert result["observation_index_for_target"] == 2
    persisted = core.load_text_snapshots(path)
    assert path.read_bytes().startswith(before)
    assert persisted[-1]["previous_content_hash"] == raw["content_hash"]
    assert persisted[-1]["stream_sequence"] == 2
    assert persisted[-1]["changed"] is changed
    assert persisted[-1]["duplicate_content"] is not changed
    assert core._validate_text_snapshot_chain(persisted)[target][0] == 2
