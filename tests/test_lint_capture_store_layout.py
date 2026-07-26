import json
import sys
from pathlib import Path

import pytest

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
    (store_root / "another-stray-file.json").write_text("{}", encoding="utf-8")

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is False
    kinds = {violation["kind"] for violation in report["violations"]}
    assert "disallowed_path" in kinds
    paths = {violation["path"] for violation in report["violations"]}
    assert "another-stray-file.json" in paths
    assert str(store_root) not in json.dumps(report)


def test_current_writer_default_outputs_are_allowed(tmp_path):
    """H4: 現行 writer (core.py の DEFAULT_*) が既定で作るパスは violation にしない。"""
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    (store_root / "context-library.json").write_text(
        json.dumps({"schema": "dcb.context_library.v1"}), encoding="utf-8"
    )
    (store_root / "review-registry.json").write_text(
        json.dumps({"schema": "dcb.review_registry.v1"}), encoding="utf-8"
    )
    (store_root / "attachment-ledger.md").write_text("# ledger\n", encoding="utf-8")
    (store_root / "inbox" / "raw" / "rest-backfill.ndjson").parent.mkdir(parents=True, exist_ok=True)
    (store_root / "inbox" / "raw" / "rest-backfill.ndjson").write_text("", encoding="utf-8")

    report = lint.build_report(store_root=store_root, baseline_path=None)

    kinds = {violation["kind"] for violation in report["violations"]}
    assert "disallowed_path" not in kinds


def test_events_ndjson_is_excluded_from_schema_key_check(tmp_path):
    """H4: events.ndjson は ADR-0162 Phase 2/3 移行対象のため schema キー検査から除外する。"""
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    _write_ndjson(store_root / "events.ndjson", [{"no_schema_key": True}])

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is True
    assert report["violations"] == []


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


def test_unknown_schema_value_is_not_reflected_verbatim(tmp_path):
    """C1: 未知 schema 値は verbatim ではなく stable_text_hash の先頭16桁で表す。"""
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    secret_looking_value = "PRIVATE-BODY-TEXT-should-not-leak-into-stdout"
    _write_json(store_root / "manifests" / "cap2.json", {"schema": secret_looking_value})

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is False
    assert secret_looking_value not in json.dumps(report)
    unknown = [v for v in report["violations"] if v["kind"] == "unknown_schema_value"][0]
    assert unknown["detail"] != secret_looking_value
    assert len(unknown["detail"]) == 16


def test_unknown_schema_value_detail_is_stable_for_baseline_dedupe(tmp_path):
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    _write_json(store_root / "manifests" / "cap2.json", {"schema": "totally.unknown.v1"})

    first = lint.build_report(store_root=store_root, baseline_path=None)
    second = lint.build_report(store_root=store_root, baseline_path=None)

    detail_first = [v["detail"] for v in first["violations"] if v["kind"] == "unknown_schema_value"][0]
    detail_second = [v["detail"] for v in second["violations"] if v["kind"] == "unknown_schema_value"][0]
    assert detail_first == detail_second


def test_path_output_self_report_matches_actual_output(tmp_path):
    """C1: path_output の自己申告と実出力を整合させる。violations に相対パスが
    実際に出ている時は "omitted" と自己申告しない。"""
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    (store_root / "another-stray-file.json").write_text("{}", encoding="utf-8")

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["violations"]
    assert report["path_output"] != "omitted"

    written = lint.build_report(store_root=store_root, baseline_path=tmp_path / "baseline.json", write_baseline=True)
    assert "violations" not in written
    assert written["path_output"] == "omitted"


def test_ndjson_with_unicode_line_separator_in_value_is_not_split(tmp_path):
    """M6 系: `str.splitlines()` は U+2028/U+0085/U+2029 等も分割対象にし、値にそれらを
    含む有効な NDJSON 行を途中で分断して unreadable 扱いにしてしまう。ingest_capture.py の
    `_read_payload` / `load_target_registry` と同じ `\\n` 区切りだけの分割に揃える。"""
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    label = "before midtail end"
    _write_ndjson(
        store_root / "targets.ndjson",
        [{"schema": "dcb.target_registry_entry.v1", "target_key": "a" * 16, "channel_label": label}],
    )

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is True
    assert report["violations"] == []


def test_symlink_files_are_excluded_from_scan(tmp_path):
    """M6: symlink は走査対象から除外する (site_adapter_store.py の判定に合わせる)。"""
    store_root = tmp_path / "store"
    _build_clean_store(store_root)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schema": "totally.unknown.v1"}), encoding="utf-8")
    link = store_root / "manifests" / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")

    report = lint.build_report(store_root=store_root, baseline_path=None)

    assert report["ok"] is True
    assert report["violations"] == []
