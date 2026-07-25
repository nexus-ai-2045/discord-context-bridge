import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import ingest_capture  # noqa: E402

from discord_context_bridge.core import load_text_snapshots, stable_text_hash  # noqa: E402


PAYLOAD = {
    "schema": "dcb.visible_message_record.v1",
    "target_key": "706e9c00f5c017de",
    "capture_id": "0bd43a4cf7bb02be87ee48ec",
    "ordinal": 0,
    "message_id": "m-1",
    "author_label": "Alice",
    "visible_timestamp": "12:00",
    "body_text": "PRIVATE BODY should not appear on stdout",
    "attachments": [],
    "captured_at": "2026-01-01T00:00:00Z",
    "outbound_actions": "disabled",
}


def test_dry_run_cli_reports_without_writing(tmp_path, capsys):
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert not snapshot_store.exists()
    assert "PRIVATE BODY" not in output
    assert "discord.com" not in output


def test_apply_cli_writes_ledger(tmp_path, capsys):
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--apply",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["events_appended"] == 1
    records = load_text_snapshots(snapshot_store)
    assert len(records) == 1
    assert records[0]["text"] == "PRIVATE BODY should not appear on stdout"
    assert "PRIVATE BODY" not in output


def test_unreadable_input_returns_nonzero(tmp_path, capsys):
    input_path = tmp_path / "missing.json"
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["reason"] == "input_unreadable"


def test_unsupported_schema_returns_nonzero(tmp_path, capsys):
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps({"schema": "not.supported.v1"}), encoding="utf-8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["reason"] == "adapter_not_implemented"


def _ndjson_row(index: int) -> dict:
    return {
        "schema": "dcb.incremental_visible_message.v1",
        "stream_id": "01a13365dcd97a21-to-latest-4afd4aa0",
        "ordinal": index,
        "message_id": f"m-{index}",
        "visible_timestamp": f"2026-07-16T04:57:{index:02d}.000Z",
        "author_label": "system-event",
        "body_text": f"line body {index}",
        "links": [],
        "attachments": [],
        "captured_at": "2026-07-23T14:09:09.633Z",
        "outbound_actions": "disabled",
    }


def test_ndjson_extension_is_parsed_line_by_line(tmp_path, capsys):
    """R1: 拡張子 .ndjson は行単位 parse で複数メッセージを 1 ingest にまとめる。"""
    input_path = tmp_path / "messages.raw.ndjson"
    lines = "\n".join(json.dumps(_ndjson_row(i)) for i in range(5))
    input_path.write_text(lines + "\n", encoding="utf-8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["events_pending"] == 5


def test_whole_file_parse_failure_falls_back_to_ndjson_lines(tmp_path, capsys):
    """R1: 拡張子が .json でも、単一 JSON として parse できない時は行単位 parse へ fallback する。"""
    input_path = tmp_path / "messages.raw.json"
    lines = "\n".join(json.dumps(_ndjson_row(i)) for i in range(3))
    input_path.write_text(lines + "\n", encoding="utf-8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["events_pending"] == 3


def test_non_utf8_input_returns_input_unreadable(tmp_path, capsys):
    """H5: 非 UTF-8 入力でも UnicodeDecodeError を捕捉して input_unreadable にする。"""
    input_path = tmp_path / "capture.ndjson"
    input_path.write_bytes(b"\xff\xfe\x00\x01not-utf8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["reason"] == "input_unreadable"


def test_url_option_matching_record_target_key_succeeds(tmp_path, capsys):
    """R3: --url 指定時、sha256(url)[:16] とレコード target_key が一致すれば通す。"""
    url = "https://discord.com/channels/1/2/3"
    matching_payload = dict(PAYLOAD, target_key=stable_text_hash(url))
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(matching_payload), encoding="utf-8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--url",
            url,
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert url not in json.dumps(payload)


def test_url_option_mismatching_record_target_key_stops_with_error(tmp_path, capsys):
    """R3: --url と レコード target_key が不一致なら error で停止する。"""
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    rc = ingest_capture.main(
        [
            "--input",
            str(input_path),
            "--snapshot-store",
            str(snapshot_store),
            "--registry-store",
            str(registry_store),
            "--url",
            "https://discord.com/channels/9/9/9",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["reason"] == "target_key_url_mismatch"
    assert not snapshot_store.exists()
