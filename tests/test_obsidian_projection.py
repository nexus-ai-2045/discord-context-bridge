from __future__ import annotations

import json
from pathlib import Path

from discord_context_bridge.cli import main
from discord_context_bridge.obsidian_projection import export_obsidian_projection


def _write_snapshot(path: Path, *, sequence: int, text: str, content_hash: str) -> None:
    record = {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "target_key": "safe-target-a",
        "stream_id": "safe-target-a",
        "stream_sequence": sequence,
        "captured_at": "2026-07-16T10:00:00+09:00",
        "source": "visible_text",
        "content_hash": content_hash,
        "text": text,
        "private_local_only": True,
        "outbound_actions": "disabled",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_export_uses_latest_snapshot_and_creates_obsidian_views(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Discord Context"
    _write_snapshot(
        snapshot_store, sequence=1, text="member-a: 古い本文", content_hash="old"
    )
    _write_snapshot(
        snapshot_store, sequence=2, text="member-a: 新しい本文", content_hash="new"
    )

    result = export_obsidian_projection(
        snapshot_store=snapshot_store, output_root=output_root
    )

    channel_root = output_root / "Channels" / "target-safe-target-a"
    month_note = next(channel_root.glob("* 2026-07.md"))
    rendered = month_note.read_text(encoding="utf-8")
    assert result["source_record_count"] == 2
    assert result["projected_target_count"] == 1
    assert result["sqlite_used"] is False
    assert "新しい本文" in rendered
    assert "古い本文" not in rendered
    assert "^msg-" in rendered
    assert (output_root / "Discord会話マップ.md").exists()
    assert (output_root / "Views" / "Discord取得一覧.base").exists()
    assert next(channel_root.glob("* 概要.md")).exists()


def test_export_is_incremental_and_preserves_human_notes(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Discord Context"
    _write_snapshot(
        snapshot_store, sequence=1, text="member-a: 本文", content_hash="same"
    )

    first = export_obsidian_projection(
        snapshot_store=snapshot_store, output_root=output_root
    )
    notes = next((output_root / "Channels" / "target-safe-target-a").glob("* メモ.md"))
    notes.write_text("# 人間が書いたメモ\n", encoding="utf-8")
    second = export_obsidian_projection(
        snapshot_store=snapshot_store, output_root=output_root
    )

    assert first["written_file_count"] >= 4
    assert second["human_notes_preserved"] is True
    assert notes.read_text(encoding="utf-8") == "# 人間が書いたメモ\n"
    assert second["unchanged_file_count"] >= 3


def test_export_migrates_legacy_months_without_losing_history(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Discord Context"
    channel_root = output_root / "Channels" / "target-safe-target-a"
    channel_root.mkdir(parents=True)
    legacy_month = channel_root / "2026-06.md"
    legacy_month.write_text(
        "---\ndcb_generated: true\n---\n\n# 過去ログ\n",
        encoding="utf-8",
    )
    human_file = channel_root / "2026-05.md"
    human_file.write_text("# 人間が作ったファイル\n", encoding="utf-8")
    _write_snapshot(
        snapshot_store, sequence=1, text="member-a: 最新本文", content_hash="new"
    )

    export_obsidian_projection(snapshot_store=snapshot_store, output_root=output_root)

    migrated = list(channel_root.glob("* 2026-06.md"))
    assert len(migrated) == 1
    assert "# 過去ログ" in migrated[0].read_text(encoding="utf-8")
    assert not legacy_month.exists()
    assert human_file.read_text(encoding="utf-8") == "# 人間が作ったファイル\n"


def test_export_preserves_months_from_an_old_display_label(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Discord Context"
    channel_root = output_root / "Channels" / "target-safe-target-a"
    channel_root.mkdir(parents=True)
    old_label_month = channel_root / "旧ラベル 2026-06.md"
    old_label_month.write_text(
        "---\ndcb_generated: true\n---\n\n# 旧ラベルの過去ログ\n",
        encoding="utf-8",
    )
    _write_snapshot(
        snapshot_store, sequence=1, text="member-a: 最新本文", content_hash="new"
    )

    export_obsidian_projection(snapshot_store=snapshot_store, output_root=output_root)

    assert old_label_month.exists()
    index = next(channel_root.glob("* 概要.md")).read_text(encoding="utf-8")
    assert "[[旧ラベル 2026-06]]" in index


def test_export_preserves_human_created_legacy_base(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Discord Context"
    legacy_base = output_root / "Views" / "Channels.base"
    legacy_base.parent.mkdir(parents=True)
    legacy_base.write_text("views:\n  - name: 人間が作ったビュー\n", encoding="utf-8")
    _write_snapshot(
        snapshot_store, sequence=1, text="member-a: 本文", content_hash="same"
    )

    export_obsidian_projection(snapshot_store=snapshot_store, output_root=output_root)

    assert (
        legacy_base.read_text(encoding="utf-8")
        == "views:\n  - name: 人間が作ったビュー\n"
    )


def test_export_sanitizes_obsidian_link_syntax_from_labels(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Discord Context"
    _write_snapshot(
        snapshot_store, sequence=1, text="member-a: 本文", content_hash="same"
    )
    record = json.loads(snapshot_store.read_text(encoding="utf-8"))
    record["title"] = "[相談] #重要 ^block"
    snapshot_store.write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    export_obsidian_projection(snapshot_store=snapshot_store, output_root=output_root)

    generated_names = [path.name for path in output_root.rglob("*.md")]
    assert all(
        not any(character in name for character in "[]#^") for name in generated_names
    )


def test_export_cli_returns_metadata_only_json(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    output_root = tmp_path / "Discord Context"
    secret_text = "member-a: private local sentence"
    _write_snapshot(snapshot_store, sequence=1, text=secret_text, content_hash="secret")

    code = main(
        [
            "export-obsidian",
            "--snapshot-store",
            str(snapshot_store),
            "--output-root",
            str(output_root),
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert secret_text not in output
    assert str(snapshot_store) not in output
    assert str(output_root) not in output
    assert '"paths_returned": false' in output
