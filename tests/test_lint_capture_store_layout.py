import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import lint_capture_store_layout as lint  # noqa: E402


def _write_ndjson(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _build_clean_store(root: Path) -> None:
    _write_ndjson(
        root / "text-snapshots.ndjson",
        [{"schema": "discord_context_bridge_text_snapshot_observation.v1", "target_key": "a" * 16}],
    )
    _write_ndjson(
        root / "targets.ndjson",
        [{"schema": "dcb.target_registry_entry.v1", "target_key": "a" * 16}],
    )
    _write_json(
        root / "manifests" / "cap1.json",
        {"schema": "dcb.capture_manifest.v1", "capture_id": "x" * 24},
    )
    _write_json(
        root / "raw" / "cap1.json",
        {"schema": "dcb.raw_capture.v1", "source_url": "https://discord.com/channels/1/2/3"},
    )


def test_clean_layout_has_no_violations(tmp_path):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is True
    assert report["violations"] == []


def test_disallowed_top_level_file_is_a_violation(tmp_path):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    (store_root / "review-registry.json").write_text("{}", encoding="utf-8")

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is False
    kinds = {violation["kind"] for violation in report["violations"]}
    assert "disallowed_path" in kinds
    paths = {violation["path"] for violation in report["violations"]}
    assert "review-registry.json" in paths
    assert str(store_root) not in json.dumps(report)


def test_missing_schema_key_is_a_violation(tmp_path):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    _write_ndjson(store_root / "targets.ndjson", [{"schema_version": 1, "target_key": "b" * 16}])

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is False
    kinds = {violation["kind"] for violation in report["violations"]}
    assert "missing_schema_key" in kinds


def test_unknown_schema_value_is_a_violation(tmp_path):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    _write_json(store_root / "manifests" / "cap2.json", {"schema": "totally.unknown.v1"})

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is False
    kinds = {violation["kind"] for violation in report["violations"]}
    assert "unknown_schema_value" in kinds


def test_write_baseline_suppresses_known_violations(tmp_path):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    (store_root / "legacy-file.json").write_text("{}", encoding="utf-8")
    baseline_path = tmp_path / "lint-baseline.json"

    written = lint.build_report(store_root=store_root, baseline_path=baseline_path, write_baseline=True)
    assert written["ok"] is True
    assert baseline_path.exists()

    rerun = lint.build_report(store_root=store_root, baseline_path=baseline_path)
    assert rerun["ok"] is True
    assert rerun["new_violations"] == []
    assert rerun["known_violation_count"] >= 1


def test_new_violation_beyond_baseline_still_fails(tmp_path):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    (store_root / "legacy-file.json").write_text("{}", encoding="utf-8")
    baseline_path = tmp_path / "lint-baseline.json"
    lint.build_report(store_root=store_root, baseline_path=baseline_path, write_baseline=True)

    (store_root / "another-stray-file.json").write_text("{}", encoding="utf-8")
    rerun = lint.build_report(store_root=store_root, baseline_path=baseline_path)

    assert rerun["ok"] is False
    new_paths = {violation["path"] for violation in rerun["new_violations"]}
    assert "another-stray-file.json" in new_paths
    assert "legacy-file.json" not in new_paths


def test_cli_json_output_has_no_absolute_paths(tmp_path, capsys):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)

    rc = lint.main(["--store-root", str(store_root), "--json"])
    output = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(output)
    assert payload["ok"] is True
    assert str(store_root) not in output
