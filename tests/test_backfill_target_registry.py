import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import backfill_target_registry as backfill  # noqa: E402

from discord_context_bridge.target_registry import load_target_registry, resolve_target  # noqa: E402


def _write_ndjson(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _build_store(root: Path) -> None:
    _write_ndjson(
        root / "text-snapshots.ndjson",
        [
            {
                "target_key": "aaa0000000000001",
                "url": "https://discord.com/channels/1/2/3",
                "title": "planning",
            },
            {
                "target_key": "aaa0000000000002",
                "url": "",
                "title": "no-url-title",
            },
            {
                "target_key": "",
                "url": "https://discord.com/channels/9/9/9",
                "title": "unresolvable-because-no-target-key",
            },
        ],
    )
    _write_json(
        root / "manifests" / "cap1.json",
        {
            "schema": "dcb.capture_manifest.v1",
            "source_url_hash": "b" * 64,
        },
    )
    _write_json(
        root / "raw" / "cap1.json",
        {
            "schema": "dcb.raw_capture.v1",
            "source_url": "https://discord.com/channels/4/5/6",
        },
    )
    _write_json(
        root / "captures" / "closeout.json",
        {
            "nested": {"target_key": "c" * 16},
        },
    )
    _write_json(root / "captures" / "unreadable.json", None)
    (root / "captures" / "unreadable.json").write_text("not-json{", encoding="utf-8")


def test_dry_run_reports_counts_without_writing_registry(tmp_path):
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    registry_path = store_root / "targets.ndjson"

    report = backfill.build_report(store_root=store_root, registry_store=registry_path, apply=False)

    assert report["ok"] is True
    assert report["dry_run"] is True
    assert report["candidates_total"] >= 4
    assert report["skipped_unresolvable"] >= 1
    assert not registry_path.exists()


def test_apply_writes_registry_entries(tmp_path):
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    registry_path = store_root / "targets.ndjson"

    report = backfill.build_report(store_root=store_root, registry_store=registry_path, apply=True)

    assert report["ok"] is True
    assert report["dry_run"] is False
    assert report["registered"] > 0
    entries = load_target_registry(registry_path)
    assert entries
    assert all(entry["source"] == "backfill" for entry in entries)

    resolved = resolve_target("aaa0000000000001", registry_path)
    assert resolved is not None
    assert resolved["url"] == "https://discord.com/channels/1/2/3"

    resolved_no_url = resolve_target("aaa0000000000002", registry_path)
    assert resolved_no_url is not None
    assert resolved_no_url["url"] is None
    assert resolved_no_url["channel_label"] == "no-url-title"


def test_apply_is_idempotent_on_second_run(tmp_path):
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    registry_path = store_root / "targets.ndjson"

    backfill.build_report(store_root=store_root, registry_store=registry_path, apply=True)
    first_count = len(load_target_registry(registry_path))

    backfill.build_report(store_root=store_root, registry_store=registry_path, apply=True)
    second_count = len(load_target_registry(registry_path))

    assert second_count == first_count


def test_manifest_source_url_hash_registers_source_url_hash_64_scheme(tmp_path):
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    registry_path = store_root / "targets.ndjson"

    backfill.build_report(store_root=store_root, registry_store=registry_path, apply=True)
    resolved = resolve_target(("b" * 64)[:16], registry_path)
    assert resolved is not None
    assert resolved["key_scheme"] == "source_url_hash_64"
    assert resolved["url"] is None


def test_json_output_never_contains_raw_urls(tmp_path, capsys):
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    registry_path = store_root / "targets.ndjson"

    rc = backfill.main(
        [
            "--store-root",
            str(store_root),
            "--registry-store",
            str(registry_path),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    assert "discord.com" not in output
    payload = json.loads(output)
    assert payload["dry_run"] is True
