from __future__ import annotations

import json
import struct
from pathlib import Path

from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.core import load_text_snapshots, target_key_for_url
from discord_context_bridge.desktop_cache import (
    FLAG_HAS_KEY_SHA256,
    SIMPLE_FINAL_MAGIC,
    SIMPLE_HEADER,
    SIMPLE_INITIAL_MAGIC,
    SIMPLE_VERSION,
)
from discord_context_bridge.desktop_cache_records import export_desktop_cache_records
from discord_context_bridge.ingest import ingest_capture


FIXTURE = Path(__file__).parent / "fixtures" / "desktop_cache_messages.json"
GUILD_ID = "11111111111111111"
CHANNEL_ID = "21111111111111111"
THREAD_ID = "31111111111111111"
ORPHAN_CHANNEL_ID = "41111111111111111"
SECOND_GUILD_ID = "12222222222222222"
SECOND_CHANNEL_ID = "91111111111111111"
CHANNEL_URL = f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}"
THREAD_URL = f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/threads/{THREAD_ID}"
SECOND_URL = f"https://discord.com/channels/{SECOND_GUILD_ID}/{SECOND_CHANNEL_ID}"


def _write_entry(path: Path, *, key: str, payload: object, date: str = "") -> None:
    key_bytes = key.encode("utf-8")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    metadata = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
    if date:
        metadata += f"date: {date}\r\n".encode("ascii")
    header = SIMPLE_HEADER.pack(SIMPLE_INITIAL_MAGIC, SIMPLE_VERSION, len(key_bytes), 1, 0)
    stream_one_eof = struct.pack("<QIIII", SIMPLE_FINAL_MAGIC, 0, 0, 0, 0)
    stream_zero_eof = struct.pack("<QIIII", SIMPLE_FINAL_MAGIC, FLAG_HAS_KEY_SHA256, 0, len(metadata), 0)
    path.write_bytes(header + key_bytes + body + stream_one_eof + metadata + (b"x" * 32) + stream_zero_eof)


def _build_cache(tmp_path: Path) -> Path:
    cache_root = tmp_path / "discord" / "Cache" / "Cache_Data"
    cache_root.mkdir(parents=True)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for entry in fixture["entries"]:
        _write_entry(cache_root / entry["name"], key=entry["key"], payload=entry["payload"], date=entry["date"])
    (cache_root / "index").write_bytes(b"not an entry")
    (cache_root / "broken_0").write_bytes(b"too short")
    return cache_root


def _records_by_url(out_dir: Path) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for path in sorted((out_dir / "records").glob("*.ndjson")):
        for line in path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                row = json.loads(line)
                grouped.setdefault(row["url"], []).append(row)
    return grouped


def test_text_messages_become_visible_message_records(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    out_dir = tmp_path / "out"

    report = export_desktop_cache_records(cache_root=cache_root, out_dir=out_dir)

    assert report["ok"] is True
    grouped = _records_by_url(out_dir)
    channel_rows = grouped[CHANNEL_URL]
    assert [row["message_id"] for row in channel_rows] == ["51111111111111111", "51111111111111112"]
    first = channel_rows[0]
    assert first["schema"] == "dcb.visible_message_record.v1"
    assert first["target_key"] == target_key_for_url(CHANNEL_URL)
    assert first["body_text"] == "fixture root body"
    assert first["author_label"] == "Alice"
    assert first["visible_timestamp"] == "2026-09-01T00:00:00.000000+00:00"
    assert first["ordinal"] == 0
    assert first["source"] == "discord_desktop_cache"
    assert first["outbound_actions"] == "disabled"
    # global_name が無い時は username を使う
    assert channel_rows[1]["author_label"] == "bob"
    # 同一 target の行は captured_at を揃える (ingest は先頭行の captured_at を採用する)
    assert {row["captured_at"] for row in channel_rows} == {"2026-09-07T10:00:00+00:00"}


def test_empty_body_messages_are_excluded_with_reason_counts(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    out_dir = tmp_path / "out"

    report = export_desktop_cache_records(cache_root=cache_root, out_dir=out_dir)

    emitted_ids = {row["message_id"] for rows in _records_by_url(out_dir).values() for row in rows}
    assert "51111111111111113" not in emitted_ids
    assert "51111111111111114" not in emitted_ids
    assert report["exclusions"] == {
        "dm_channel": 1,
        "empty_body_attachment_only": 1,
        "server_unknown": 1,
        "system_message_type": 1,
    }
    assert report["messages"]["unique_seen"] == 8
    assert report["messages"]["emitted"] == 4
    assert report["messages"]["excluded"] == 4


def test_channels_without_server_are_reported_as_server_unknown(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    out_dir = tmp_path / "out"

    report = export_desktop_cache_records(cache_root=cache_root, out_dir=out_dir)

    urls = set(_records_by_url(out_dir))
    assert not any(ORPHAN_CHANNEL_ID in url for url in urls)
    assert report["server_unknown"] == {"channel_count": 1, "message_count": 1}
    channel_map = json.loads((out_dir / "channel-map.json").read_text(encoding="utf-8"))
    unknown = [row for row in channel_map["channels"] if row["status"] == "server_unknown"]
    assert [row["channel_id"] for row in unknown] == [ORPHAN_CHANNEL_ID]
    # server 別件数は server_unknown と混ぜない
    assert sum(server["message_count"] for server in report["servers"]) == 4
    assert len(report["servers"]) == 2


def test_snapshot_store_urls_resolve_unknown_channels(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    hint = tmp_path / "text-snapshots.ndjson"
    hint.write_text(
        json.dumps({"url": f"https://discord.com/channels/{SECOND_GUILD_ID}/{ORPHAN_CHANNEL_ID}", "text": "x"})
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    report = export_desktop_cache_records(cache_root=cache_root, out_dir=out_dir, snapshot_store_hint=hint)

    assert report["server_unknown"] == {"channel_count": 0, "message_count": 0}
    assert f"https://discord.com/channels/{SECOND_GUILD_ID}/{ORPHAN_CHANNEL_ID}" in _records_by_url(out_dir)


def test_thread_and_stream_key_mapping_and_target_key_match_url(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    out_dir = tmp_path / "out"

    export_desktop_cache_records(cache_root=cache_root, out_dir=out_dir)

    grouped = _records_by_url(out_dir)
    assert set(grouped) == {CHANNEL_URL, THREAD_URL, SECOND_URL}
    for url, rows in grouped.items():
        assert {row["target_key"] for row in rows} == {target_key_for_url(url)}
        path = out_dir / "records" / f"{target_key_for_url(url)}.ndjson"
        assert path.is_file()
    # 同一 message_id が複数 cache entry にある時は編集後の版を採る
    assert [row["body_text"] for row in grouped[SECOND_URL]] == ["fixture second guild body (edited)"]


def test_output_is_idempotent_for_same_input(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    first_dir = tmp_path / "out1"
    second_dir = tmp_path / "out2"

    first = export_desktop_cache_records(cache_root=cache_root, out_dir=first_dir)
    second = export_desktop_cache_records(cache_root=cache_root, out_dir=second_dir)
    again = export_desktop_cache_records(cache_root=cache_root, out_dir=first_dir)

    assert first == second == again
    first_files = sorted(p.relative_to(first_dir) for p in first_dir.rglob("*") if p.is_file())
    second_files = sorted(p.relative_to(second_dir) for p in second_dir.rglob("*") if p.is_file())
    assert first_files == second_files
    for relative in first_files:
        assert (first_dir / relative).read_bytes() == (second_dir / relative).read_bytes()


def test_dm_channels_are_opt_in(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    out_dir = tmp_path / "out"

    report = export_desktop_cache_records(cache_root=cache_root, out_dir=out_dir, include_dm=True)

    assert "https://discord.com/channels/@me/81111111111111111" in _records_by_url(out_dir)
    assert "dm_channel" not in report["exclusions"]
    assert report["dm"] == {"channel_count": 1, "message_count": 1, "included": True}


def test_output_files_ingest_through_existing_ingest_path(tmp_path: Path) -> None:
    cache_root = _build_cache(tmp_path)
    out_dir = tmp_path / "out"
    export_desktop_cache_records(cache_root=cache_root, out_dir=out_dir)
    snapshot_store = tmp_path / "s1.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    path = out_dir / "records" / f"{target_key_for_url(CHANNEL_URL)}.ndjson"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    dry = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=False)
    assert dry["ok"] is True
    assert dry["events_pending"] == 2
    assert dry["target_key"] == target_key_for_url(CHANNEL_URL)
    assert not snapshot_store.exists()

    applied = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    assert applied["events_appended"] == 2
    stored = load_text_snapshots(snapshot_store)
    assert [record["message_id"] for record in stored] == ["51111111111111111", "51111111111111112"]
    assert stored[0]["acquisition_context"]["source_kind"] == "cache"

    rerun = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=False)
    assert rerun["duplicates"] == 2


def test_cli_writes_records_and_prints_metadata_only(tmp_path: Path, capsys) -> None:
    cache_root = _build_cache(tmp_path)
    out_dir = tmp_path / "out"

    result = cli_main(
        [
            "desktop-cache-export-records",
            "--cache-data-dir",
            str(cache_root),
            "--out-dir",
            str(out_dir),
            "--json",
        ]
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert result == 0
    assert payload["messages"]["emitted"] == 4
    assert payload["raw_text_returned"] is False
    assert "fixture" not in rendered
    assert "Alice" not in rendered
    assert GUILD_ID not in rendered
    assert CHANNEL_ID not in rendered
    assert str(tmp_path) not in rendered
    assert (out_dir / "report.json").is_file()
    # --snapshot-store-hint 未指定時は既定 S1 を暗黙に読まない
    assert payload["channel_mapping"]["snapshot_store_hint_used"] is False


def test_missing_cache_root_is_reported_without_writing(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"

    report = export_desktop_cache_records(cache_root=tmp_path / "missing", out_dir=out_dir)

    assert report["ok"] is False
    assert report["state"] == "cache_missing"
    assert not out_dir.exists()
