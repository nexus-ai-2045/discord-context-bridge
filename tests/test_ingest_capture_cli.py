import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import ingest_capture  # noqa: E402

from discord_context_bridge.core import load_text_snapshots  # noqa: E402


PAYLOAD = {
    "schema": "dcb.visible_message_record.v1",
    "url": "https://discord.com/channels/1/2/3",
    "title": "general",
    "source": "chrome_dom_visible_range",
    "message": {
        "message_id": "m-1",
        "author_label": "Alice",
        "visible_timestamp": "12:00",
        "body_text": "PRIVATE BODY should not appear on stdout",
    },
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
