from __future__ import annotations

import json
from pathlib import Path

from discord_context_bridge.cli import main
from discord_context_bridge.knowledge_projection import export_knowledge_projection


def _append_snapshot(
    path: Path,
    *,
    sequence: int,
    text: str,
    content_hash: str,
    target_key: str = "safe-target-a",
) -> None:
    record = {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "target_key": target_key,
        "stream_id": target_key,
        "stream_sequence": sequence,
        "captured_at": "2026-07-31T10:00:00+09:00",
        "source": "visible_text",
        "content_hash": content_hash,
        "text": text,
        "private_local_only": True,
        "outbound_actions": "disabled",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_projection_creates_people_topics_timeline_and_top(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 古い発言 #old",
        content_hash="old",
    )
    _append_snapshot(
        snapshot_store,
        sequence=2,
        text=(
            "member-a: イベント台帳について話す #event-sourcing\n"
            "member-b: [[Obsidian]] の話題Wikiを作る"
        ),
        content_hash="new",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    people = list((output_root / "People").glob("*.generated.md"))
    topics = list((output_root / "Topics").glob("*.generated.md"))
    rendered_people = "\n".join(path.read_text(encoding="utf-8") for path in people)
    rendered_topics = "\n".join(path.read_text(encoding="utf-8") for path in topics)
    top = (output_root / "Knowledge TOP.generated.md").read_text(encoding="utf-8")

    assert result["projected_person_count"] == 2
    assert result["projected_topic_count"] == 2
    assert "イベント台帳について話す" in rendered_people
    assert "古い発言" not in rendered_people
    assert "recorded_by: dcb-knowledge-projector" in rendered_people
    assert "event_time: `unknown`" in rendered_people
    assert "source_stream_ref: `stream-" in rendered_people
    assert "event-sourcing" in rendered_topics
    assert "Obsidian" in rendered_topics
    assert "## 人物" in top
    assert "## 話題" in top
    assert (output_root / "Knowledge TOP.md").exists()


def test_projection_uses_timestamp_boundary_instead_of_treating_body_as_author(
    tmp_path,
):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text=(
            "member-a\n"
            "Today at 10:00 AM\n"
            "短い本文\n"
            "#event-sourcing\n"
            "member-b\n"
            "Today at 10:05 AM\n"
            "[[Obsidian]] の本文"
        ),
        content_hash="discord-copy",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    people = list((output_root / "People").glob("*.generated.md"))
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in people)
    assert result["projected_person_count"] == 2
    assert result["projected_event_count"] == 2
    assert "短い本文" in rendered
    assert "[[Obsidian]] の本文" in rendered


def test_projection_rejects_numeric_metadata_and_url_as_people(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text=(
            "2026/07/31\n"
            "Today at 09:59 AM\n"
            "member-a\n"
            "Today at 10:00 AM\n"
            "本文\n"
            "https://example.invalid: リンク"
        ),
        content_hash="metadata",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    assert result["projected_person_count"] == 1
    person = next((output_root / "People").glob("*.generated.md"))
    assert 'title: "member-a"' in person.read_text(encoding="utf-8")


def test_projection_does_not_merge_same_label_across_targets(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="same-label: target A",
        content_hash="a",
        target_key="safe-target-a",
    )
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="same-label: target B",
        content_hash="b",
        target_key="safe-target-b",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    assert result["projected_person_count"] == 2
    assert len(list((output_root / "People").glob("*.generated.md"))) == 2


def test_projection_preserves_human_top_and_person_notes(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 本文 #topic",
        content_hash="same",
    )

    export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )
    human_top = output_root / "Knowledge TOP.md"
    human_top.write_text("# 人間が編集したTOP\n", encoding="utf-8")
    person_notes = next((output_root / "People").glob("*.notes.md"))
    person_notes.write_text("# 人物についての人間メモ\n", encoding="utf-8")

    second = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    assert human_top.read_text(encoding="utf-8") == "# 人間が編集したTOP\n"
    assert (
        person_notes.read_text(encoding="utf-8")
        == "# 人物についての人間メモ\n"
    )
    assert second["human_notes_preserved"] is True


def test_projection_does_not_infer_topics_from_plain_text(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 明示的な話題指定がない本文",
        content_hash="plain",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    assert result["projected_topic_count"] == 0
    assert result["unclassified_event_count"] == 1
    assert list((output_root / "Topics").glob("*.generated.md")) == []


def test_projection_creates_review_queue_and_templater_starters(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 明示話題なしの本文",
        content_hash="review",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    review = (output_root / "Review Queue.generated.md").read_text(
        encoding="utf-8"
    )
    review_home = (output_root / "Review Queue.md").read_text(encoding="utf-8")
    person_template = (
        output_root / "Templates" / "Person Notes.md"
    ).read_text(encoding="utf-8")
    topic_template = (
        output_root / "Templates" / "Topic Notes.md"
    ).read_text(encoding="utf-8")
    decision_template = (
        output_root / "Templates" / "Review Decision.md"
    ).read_text(encoding="utf-8")

    assert result["review_item_count"] == 1
    assert "話題未分類イベント" in review
    assert "人物同一性" in review
    assert "![[Review Queue.generated]]" in review_home
    assert "<% tp.file.title %>" in person_template
    assert "relationship_status:" in person_template
    assert "topic_status:" in topic_template
    assert "decision:" in decision_template
    assert "fact / non_fact / unknown" in decision_template


def test_projection_preserves_human_review_home_and_templates(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 本文",
        content_hash="preserve-review",
    )
    export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )
    review_home = output_root / "Review Queue.md"
    person_template = output_root / "Templates" / "Person Notes.md"
    review_home.write_text("# 人間が編集したレビュー入口\n", encoding="utf-8")
    person_template.write_text("# 人間が編集した人物テンプレート\n", encoding="utf-8")

    export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    assert review_home.read_text(encoding="utf-8") == "# 人間が編集したレビュー入口\n"
    assert (
        person_template.read_text(encoding="utf-8")
        == "# 人間が編集した人物テンプレート\n"
    )


def test_projection_removes_stale_generated_pages_but_preserves_notes(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 最初の本文 #old-topic",
        content_hash="old",
    )
    export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )
    old_topic = next((output_root / "Topics").glob("*.generated.md"))
    old_notes = next((output_root / "Topics").glob("*.notes.md"))
    old_notes.write_text("# 残す人間メモ\n", encoding="utf-8")

    _append_snapshot(
        snapshot_store,
        sequence=2,
        text="member-b: 新しい本文 #new-topic",
        content_hash="new",
    )
    export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    assert not old_topic.exists()
    assert old_notes.read_text(encoding="utf-8") == "# 残す人間メモ\n"
    assert len(list((output_root / "People").glob("*.generated.md"))) == 1
    assert len(list((output_root / "Topics").glob("*.generated.md"))) == 1


def test_export_knowledge_wiki_cli_is_metadata_only(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    private_text = "member-a: private sentence #private-topic"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text=private_text,
        content_hash="private",
    )

    code = main(
        [
            "export-knowledge-wiki",
            "--snapshot-store",
            str(snapshot_store),
            "--output-root",
            str(output_root),
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert private_text not in output
    assert str(snapshot_store) not in output
    assert str(output_root) not in output
    assert '"paths_returned": false' in output


def test_export_knowledge_wiki_dry_run_does_not_create_output(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 本文 #topic",
        content_hash="dry-run",
    )

    code = main(
        [
            "export-knowledge-wiki",
            "--snapshot-store",
            str(snapshot_store),
            "--output-root",
            str(output_root),
            "--dry-run",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert not output_root.exists()
    assert '"dry_run": true' in output
    assert '"planned_file_count": 11' in output
