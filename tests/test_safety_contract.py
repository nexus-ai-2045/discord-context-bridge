import json

import pytest

from discord_context_bridge import (
    DisabledCapability,
    build_latest_snapshot_report,
    build_review_artifact_markdown,
    load_text_snapshots,
    parse_visible_text,
    review_reply_intent,
    send_message,
    snapshot_visible_text,
)


def test_send_message_is_disabled():
    with pytest.raises(DisabledCapability, match="Discord への送信機能"):
        send_message("hello")


def test_snapshot_visible_text_appends_duplicate_observations(tmp_path):
    store = tmp_path / "text-snapshots.ndjson"
    kwargs = {
        "text": "member-a: visible hello",
        "url": "https://discord.com/channels/1/2/3",
        "path": store,
    }

    first = snapshot_visible_text(**kwargs)
    second = snapshot_visible_text(**kwargs)
    records = load_text_snapshots(store)

    assert first["duplicate_content"] is False
    assert second["duplicate_content"] is True
    assert second["snapshot_count_for_target"] == 2
    assert len(records) == 2
    assert records[1]["stream_sequence"] == 2
    assert records[1]["previous_content_hash"] == records[0]["content_hash"]
    assert records[1]["previous_event_hash"] == records[0]["event_hash"]


def test_latest_snapshot_report_is_metadata_only_by_default(tmp_path):
    store = tmp_path / "text-snapshots.ndjson"
    private_text = "member-a: private launch wording should not leak"
    private_url = "https://discord.com/channels/1/2/3"
    snapshot_visible_text(text=private_text, url=private_url, path=store)

    report = build_latest_snapshot_report(path=store)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["raw_text_returned"] is False
    assert report["participant_names_returned"] is False
    assert report["local_paths_returned"] is False
    assert private_text not in encoded
    assert private_url not in encoded
    assert str(store) not in encoded


def test_review_artifact_redacts_private_values():
    events = parse_visible_text("member-a: 公開時期の前提を確認します。")
    review = review_reply_intent("前提を確認してから返します。", events, understanding_confirmed=True)
    artifact = build_review_artifact_markdown(
        "Webhook: https://discord.com/api/webhooks/123456789012345678/token at C:\\Users\\example\\secret",
        review,
    )

    assert "webhooks/123456789012345678" not in artifact
    assert "C:\\Users\\example" not in artifact
    assert "[discord webhook omitted]" in artifact
    assert "[local path omitted]" in artifact
