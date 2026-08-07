from __future__ import annotations

import hashlib
import json
from pathlib import Path

from discord_context_bridge.cli import main
from discord_context_bridge.knowledge_projection import export_knowledge_projection


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


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


def test_projection_rejects_japanese_discord_timestamps_as_people(tmp_path):
    """日本語 Discord UI の日時区切りを人物候補にしない。

    実運用の Knowledge Wiki で人物候補 395 件中 286 件 (72%) が日時文字列だった。
    原因は日時行が TIMESTAMP_METADATA_RE に一致せず COLON_MESSAGE_RE へ落ち、
    時刻のコロンが「発言者: 本文」の区切りと誤認されること
    (例: `2026年6月30日火曜日 22:32` -> 人物 `2026年6月30日火曜日 22`)。
    fixture は実データで観測した表記をそのまま使う。
    """
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    # 日本語 Discord UI も英語 UI と同じく `発言者 -> 日時 -> 本文` の順で並ぶ。
    # 日時の書式だけが異なり、実データで観測した 6 種類を全て含める。
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text=(
            "member-a\n2026年6月30日火曜日 22:32\n本文A\n"
            "member-b\n昨日 20:15\n本文B\n"
            "member-c\n8月3日(月)21:30\n本文C\n"
            "member-d\n火 7月 28日 · 22:10\n本文D\n"
            "member-e\n2026/06/30 22:32\n本文E\n"
            "member-f\n6/19(金) 21:05\n本文F"
        ),
        content_hash="ja-discord-timestamps",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    pages = list((output_root / "People").glob("*.generated.md"))
    titles = sorted(
        line.split("title:", 1)[1].strip().strip('"')
        for path in pages
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("title:")
    )

    assert titles == [f"member-{suffix}" for suffix in "abcdef"], titles
    assert result["projected_person_count"] == 6


def test_projection_rejects_consecutive_timestamp_lines_as_people(tmp_path):
    """日時行が連続しても発言者名にならない (レビュー指摘の回帰固定)。

    「次行が日時なら現在行を発言者にする」lookahead は、現在行自体が日時の時に
    日時を発言者として採用してしまう。日時判定をループ先頭へ置くことで塞ぐ。
    """
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="2026年6月30日火曜日 22:32\n昨日 20:15\nmember-a\n8月3日(月)21:30\n本文A",
        content_hash="consecutive-timestamps",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    titles = [
        line.split("title:", 1)[1].strip().strip('"')
        for path in (output_root / "People").glob("*.generated.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("title:")
    ]
    assert titles == ["member-a"], titles
    assert result["projected_person_count"] == 1


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

    assert result["review_item_count"] == 2
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


def test_projection_consumes_all_structured_message_observations(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    for sequence, author, text in (
        (1, "Alice", "first #topic"),
        (2, "Bob", "second #topic"),
    ):
        record = {
            "event_type": "message_observation",
            "target_key": "target",
            "stream_id": "target",
            "stream_sequence": sequence,
            "message_id": str(sequence),
            "author_label": author,
            "text": text,
            "captured_at": f"2026-07-31T0{sequence}:00:00+00:00",
            "source": "structured",
            "content_hash": str(sequence),
        }
        with snapshot_store.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    result = export_knowledge_projection(
        snapshot_store=snapshot_store, output_root=output_root
    )

    assert result["projected_event_count"] == 2
    assert result["projected_person_count"] == 2


def test_structured_duplicate_text_messages_get_distinct_observation_ids(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    for sequence, message_id in ((1, "message-a"), (2, "message-b")):
        record = {
            "event_type": "message_observation",
            "target_key": "target",
            "stream_id": "target",
            "stream_sequence": sequence,
            "message_id": message_id,
            "author_label": "Alice",
            "text": "same body",
            "captured_at": f"2026-07-31T0{sequence}:00:00+00:00",
            "source": "structured",
            "content_hash": "same-content-hash",
        }
        with snapshot_store.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    export_knowledge_projection(
        snapshot_store=snapshot_store, output_root=output_root
    )

    review = (output_root / "Review Queue.generated.md").read_text(
        encoding="utf-8"
    )
    observation_ids = {
        token.strip("`")
        for line in review.splitlines()
        for token in line.split()
        if token.startswith("`observation-")
    }
    assert len(observation_ids) == 2


def test_projection_missing_ledger_fails_without_removing_pages(tmp_path):
    output_root = tmp_path / "Knowledge Wiki"
    existing = output_root / "People" / "person-old.generated.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\ndcb_knowledge_generated: true\n---\n", encoding="utf-8"
    )

    result = export_knowledge_projection(
        snapshot_store=tmp_path / "missing.ndjson",
        output_root=output_root,
    )

    assert result["ok"] is False
    assert result["reason"] == "snapshot_store_missing"
    assert existing.exists()


def test_projection_normalizes_links_topics_times_and_unicode_people(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="김철수: first #release.",
        content_hash="one",
        target_key="one",
    )
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="a]]oops|x: second #release",
        content_hash="two",
        target_key="two",
    )
    records = [
        json.loads(line)
        for line in snapshot_store.read_text(encoding="utf-8").splitlines()
    ]
    records[0]["captured_at"] = "2026-07-31T10:00:00+09:00"
    records[1]["captured_at"] = "2026-07-31T02:00:00+00:00"
    snapshot_store.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store, output_root=output_root
    )
    top = (output_root / "Knowledge TOP.generated.md").read_text(encoding="utf-8")
    topic = next((output_root / "Topics").glob("*.generated.md")).read_text(
        encoding="utf-8"
    )

    assert result["projected_person_count"] == 2
    assert result["projected_topic_count"] == 1
    assert "a]]oops|x]]" not in top
    assert 'recorded_at: "2026-07-31T02:00:00+00:00"' in topic


def test_projection_review_count_and_dry_run_stale_removals(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: first #topic",
        content_hash="one",
    )
    export_knowledge_projection(
        snapshot_store=snapshot_store, output_root=output_root
    )
    _append_snapshot(
        snapshot_store,
        sequence=2,
        text="member-b: second #topic",
        content_hash="two",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
        dry_run=True,
    )

    assert result["review_item_count"] == 1
    assert result["planned_stale_generated_file_count"] == 1


def test_export_knowledge_wiki_sanitizes_projection_errors(tmp_path, capsys):
    snapshot_store = tmp_path / "broken.ndjson"
    snapshot_store.write_text("{broken", encoding="utf-8")

    code = main(
        [
            "export-knowledge-wiki",
            "--snapshot-store",
            str(snapshot_store),
            "--output-root",
            str(tmp_path / "private"),
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 2
    assert '"reason": "projection_read_failed"' in output
    assert str(snapshot_store) not in output


def test_projection_merges_only_human_reviewed_person_aliases(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    person_registry = tmp_path / "person-registry.json"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="Alice: target A",
        content_hash="a",
        target_key="target-a",
    )
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="alice-new: target B",
        content_hash="b",
        target_key="target-b",
    )
    person_registry.write_text(
        json.dumps(
            {
                "schema": "dcb.person_registry.v1",
                "people": [
                    {
                        "person_id": "person-alice",
                        "display_label": "Alice",
                        "aliases": [
                            _stable_id(
                                "person", _stable_id("stream", "target-a"), "alice"
                            ),
                            _stable_id(
                                "person",
                                _stable_id("stream", "target-b"),
                                "alice-new",
                            ),
                        ],
                        "reviewed_at": "2026-08-05T10:00:00+09:00",
                        "reviewed_by": "human",
                    }
                ],
                "private_local_only": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
        person_registry=person_registry,
    )

    assert result["projected_person_count"] == 1
    assert result["reviewed_person_alias_count"] == 2
    person = output_root / "People" / "person-alice.generated.md"
    assert person.exists()
    assert person.read_text(encoding="utf-8").count("### ") == 2
    review = (output_root / "Review Queue.generated.md").read_text(
        encoding="utf-8"
    )
    assert "- 未確認の人物候補: 0" in review
    assert result["review_item_count"] == 2


def test_projection_applies_human_reviewed_topic_assignment(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    topic_registry = tmp_path / "topic-registry.json"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 明示話題なし",
        content_hash="plain",
        target_key="target-a",
    )
    observation_id = _stable_id("observation", "target-a", "plain", "1")
    topic_registry.write_text(
        json.dumps(
            {
                "schema": "dcb.topic_assignment_registry.v1",
                "topics": [
                    {"topic_id": "topic-architecture", "label": "Architecture"}
                ],
                "assignments": [
                    {
                        "observation_id": observation_id,
                        "topic_ids": ["topic-architecture"],
                        "reviewed_at": "2026-08-05T10:00:00+09:00",
                        "reviewed_by": "human",
                    }
                ],
                "private_local_only": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
        topic_registry=topic_registry,
    )

    assert result["projected_topic_count"] == 1
    assert result["unclassified_event_count"] == 0
    assert result["reviewed_topic_assignment_count"] == 1
    topic = output_root / "Topics" / "topic-architecture.generated.md"
    assert "明示話題なし" in topic.read_text(encoding="utf-8")


def test_projection_review_queue_exposes_stable_decision_keys(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 明示話題なし",
        content_hash="plain",
        target_key="target-a",
    )

    export_knowledge_projection(
        snapshot_store=snapshot_store,
        output_root=output_root,
    )

    review = (output_root / "Review Queue.generated.md").read_text(
        encoding="utf-8"
    )
    assert "observation-" in review
    assert "person-" in review
    assert "## 話題未分類イベント" in review


def test_export_knowledge_wiki_cli_accepts_private_review_registries(
    tmp_path, capsys
):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Knowledge Wiki"
    person_registry = tmp_path / "person-registry.json"
    topic_registry = tmp_path / "topic-registry.json"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 本文",
        content_hash="plain",
    )
    person_registry.write_text(
        json.dumps(
            {
                "schema": "dcb.person_registry.v1",
                "people": [],
                "private_local_only": True,
            }
        ),
        encoding="utf-8",
    )
    topic_registry.write_text(
        json.dumps(
            {
                "schema": "dcb.topic_assignment_registry.v1",
                "topics": [],
                "assignments": [],
                "private_local_only": True,
            }
        ),
        encoding="utf-8",
    )

    code = main(
        [
            "export-knowledge-wiki",
            "--snapshot-store",
            str(snapshot_store),
            "--output-root",
            str(output_root),
            "--person-registry",
            str(person_registry),
            "--topic-registry",
            str(topic_registry),
            "--dry-run",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert str(person_registry) not in output
    assert str(topic_registry) not in output


def test_projection_rejects_registry_ids_that_escape_output_root(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    _append_snapshot(
        snapshot_store,
        sequence=1,
        text="member-a: 本文",
        content_hash="plain",
    )
    registry = tmp_path / "person-registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "dcb.person_registry.v1",
                "people": [
                    {
                        "person_id": "../escape",
                        "display_label": "member-a",
                        "aliases": ["person-safe"],
                        "reviewed_at": "2026-08-05T10:00:00+09:00",
                        "reviewed_by": "human",
                    }
                ],
                "private_local_only": True,
            }
        ),
        encoding="utf-8",
    )

    try:
        export_knowledge_projection(
            snapshot_store=snapshot_store,
            output_root=tmp_path / "Knowledge Wiki",
            person_registry=registry,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe registry id must be rejected")

    assert not (tmp_path / "escape.generated.md").exists()
