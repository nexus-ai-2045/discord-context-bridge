import json
import sys
from pathlib import Path

import pytest

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


def test_dry_run_reports_would_register_and_already_registered_counts(tmp_path):
    """R4: dry-run でも registry を読み込み would_register / already_registered を数える。"""
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    registry_path = store_root / "targets.ndjson"

    backfill.build_report(store_root=store_root, registry_store=registry_path, apply=True)
    dry_run = backfill.build_report(store_root=store_root, registry_store=registry_path, apply=False)

    assert dry_run["ok"] is True
    assert dry_run["dry_run"] is True
    assert dry_run["already_registered"] >= 1
    assert dry_run["would_register"] == 0
    assert not (registry_path.with_suffix(".ndjson.tmp")).exists()


def test_non_utf8_json_artifact_is_skipped_without_crashing(tmp_path):
    """H5: UnicodeDecodeError を捕捉して skipped 扱いにする (lint 側と同じ流儀)。"""
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    (store_root / "raw" / "binary-garbage.json").write_bytes(b"\xff\xfe\x00\x01not-utf8")
    registry_path = store_root / "targets.ndjson"

    report = backfill.build_report(store_root=store_root, registry_store=registry_path, apply=False)

    assert report["ok"] is True


def test_non_utf8_text_snapshots_ndjson_is_skipped_without_crashing(tmp_path):
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    (store_root / "text-snapshots-binary.ndjson").write_bytes(b"\xff\xfe\x00\x01not-utf8")
    registry_path = store_root / "targets.ndjson"

    report = backfill.build_report(store_root=store_root, registry_store=registry_path, apply=False)

    assert report["ok"] is True


def test_symlinked_json_artifact_is_excluded(tmp_path):
    """M6: symlink 経由のファイルは走査対象から除外する。"""
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schema": "dcb.raw_capture.v1", "source_url": "https://evil.example/x"}), encoding="utf-8")
    link = store_root / "raw" / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    registry_path = store_root / "targets.ndjson"

    report = backfill.build_report(store_root=store_root, registry_store=registry_path, apply=True)

    assert report["ok"] is True
    from discord_context_bridge.target_registry import load_target_registry

    entries = load_target_registry(registry_path)
    assert not any((entry.get("url") or "") == "https://evil.example/x" for entry in entries)


def test_unrelated_json_without_known_schema_is_not_recursively_scanned(tmp_path):
    """M7: schema フィールドを持たない (または既知でない) JSON からは url / target_key を拾わない。"""
    store_root = tmp_path / ".local" / "discord-context-bridge"
    _build_store(store_root)
    (store_root / "captures" / "unrelated.json").parent.mkdir(parents=True, exist_ok=True)
    (store_root / "captures" / "unrelated.json").write_text(
        json.dumps({"nested": {"url": "https://unrelated.example/should-not-be-collected"}}), encoding="utf-8"
    )

    baseline = backfill.build_report(store_root=store_root, registry_store=store_root / "targets-baseline.ndjson", apply=False)
    (store_root / "captures" / "unrelated.json").unlink()
    without_unrelated = backfill.build_report(
        store_root=store_root, registry_store=store_root / "targets-baseline2.ndjson", apply=False
    )

    assert baseline["candidates_total"] == without_unrelated["candidates_total"]
