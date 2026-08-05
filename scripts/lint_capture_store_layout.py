#!/usr/bin/env python3
"""ADR-0162 Phase 1 W4: canonical capture event store layout drift detector。

`.local/discord-context-bridge/` (`--store-root`) が単一 append-only store
契約から外れていないかを read-only で確認する。

per-target shared snapshot bundle は別の store kind であり、この lint の対象外。
`--store-kind shared_snapshot_bundle` が明示された場合は内容を走査せず fail
closed し、既存の `full-capture-gate` へ誘導する。

チェック内容:

(a) 許可リスト外のパス検知。許可: `text-snapshots.ndjson` / `events.ndjson` /
    `messages.ndjson` / `targets.ndjson` / `lint-baseline.json` /
    `context-library.json` / `review-registry.json` /
    `attachment-ledger.md` (現行 writer の既定出力。core.py の DEFAULT_*
    を参照) / `raw/` / `manifests/` / `attachments/by-sha256/` /
    `projections/` / `archive/` / `inbox/` (rest-backfill 系の既定出力)
(b) 許可領域内 JSON / NDJSON の schema キー検査 (`schema` キーが必須。
    `schema_version` やキー無しは violation)
(c) 既知 schema 値のリスト外検知

`archive/` 配下は意図的に旧形式を退避する領域のため schema 検査は行わない。
`events.ndjson` も同様に対象外とする (ADR-0162 Phase 2/3 で単一 ledger へ
統合予定の旧形式のため、現時点では schema キー未整備の行が残り得る)。
symlink はファイル自体・親ディレクトリ共に走査対象から除外する。

baseline: `--write-baseline` で現状の violation を記録し、以後は baseline に
無い新規 violation だけを fail 対象にする (レガシー山を即 fail させないため)。

出力は store-root 相対パスのみで、絶対パス・url・本文は出さない。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.core import stable_text_hash  # noqa: E402

DEFAULT_STORE_ROOT = Path(".local/discord-context-bridge")
DEFAULT_STORE_KIND = "canonical_event_store"
STORE_KINDS = (DEFAULT_STORE_KIND, "shared_snapshot_bundle")

ALLOWED_TOP_LEVEL_FILES = {
    "text-snapshots.ndjson",
    "events.ndjson",
    "messages.ndjson",
    "targets.ndjson",
    "lint-baseline.json",
    # 現行 writer の既定出力 (core.py DEFAULT_CONTEXT_STORE /
    # DEFAULT_REVIEW_STORE / DEFAULT_ATTACHMENT_LEDGER)。
    "context-library.json",
    "review-registry.json",
    "attachment-ledger.md",
}
ALLOWED_TOP_LEVEL_DIRS = {"raw", "manifests", "attachments", "projections", "archive", "inbox"}
# 'attachments' は 'attachments/by-sha256' のみ許可 (2階層目まで見る)
ALLOWED_NESTED_ATTACHMENT_DIR = "by-sha256"
NO_SCHEMA_CHECK_DIRS = {"archive"}
# events.ndjson は ADR-0162 Phase 2/3 で単一 ledger へ移行予定の旧形式。
# schema キー未整備の行が残り得るため、現時点では schema 検査対象外。
# context-library.json は既存 writer (core.py upsert_context_document /
# save_context_library) の entry 設計上そもそも schema キーを持たない。
NO_SCHEMA_CHECK_TOP_LEVEL_FILES = {"events.ndjson", "context-library.json"}

KNOWN_SCHEMA_VALUES = {
    "discord_context_bridge_text_snapshot_observation.v1",
    "dcb.target_registry_entry.v1",
    "dcb.raw_capture.v1",
    "dcb.capture_manifest.v1",
    "dcb.lint_capture_store_layout_baseline.v1",
    # save_review_registry の entry (core.py upsert_review_state)。
    "discord_review_state.v1",
    # 許可されている REST-backfill NDJSON (inbox/raw/) の既存 writer 出力
    # (core.py の REST backfill message ledger)。
    "discord_rest_backfill_message.v1",
}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="canonical append-only capture event store の許可リスト / schema drift を検知する。"
    )
    parser.add_argument("--store-root", type=Path, default=DEFAULT_STORE_ROOT)
    parser.add_argument(
        "--store-kind",
        choices=STORE_KINDS,
        default=DEFAULT_STORE_KIND,
        help="既定は canonical_event_store。shared snapshot bundle は明示指定する",
    )
    parser.add_argument("--baseline", type=Path, default=None, help="既定は <store-root>/lint-baseline.json")
    parser.add_argument("--write-baseline", action="store_true", help="現状の violation を baseline として書く")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def _is_allowed_location(relative_parts: tuple[str, ...]) -> bool:
    if len(relative_parts) == 1:
        return relative_parts[0] in ALLOWED_TOP_LEVEL_FILES
    top = relative_parts[0]
    if top not in ALLOWED_TOP_LEVEL_DIRS:
        return False
    if top == "attachments":
        return len(relative_parts) >= 2 and relative_parts[1] == ALLOWED_NESTED_ATTACHMENT_DIR
    return True


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _load_ndjson_records(path: Path) -> list[Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    records: list[Any] = []
    # `str.splitlines()` は U+2028/U+2029/U+0085 等の Unicode 行区切りも分割対象にし、
    # 値にそれらを含む有効な NDJSON 行を途中で分断してしまう
    # (writer は ensure_ascii=False で書くため生のまま残り得る)。NDJSON は "\n" 区切りの
    # 契約なので、ingest_capture.py の `_read_payload` / `load_target_registry` と同じ
    # "\n" だけの分割にする。
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            return None
    return records


def _schema_violations_for_record(record: Any, *, path: str) -> list[dict[str, str]]:
    if not isinstance(record, dict):
        return [{"path": path, "kind": "missing_schema_key", "detail": "record_not_an_object"}]
    if "schema" not in record:
        detail = "schema_version_present_instead" if "schema_version" in record else "schema_key_missing"
        return [{"path": path, "kind": "missing_schema_key", "detail": detail}]
    schema_value = record["schema"]
    if not isinstance(schema_value, str) or schema_value not in KNOWN_SCHEMA_VALUES:
        # ファイル内容 (schema 値) を verbatim で stdout へ反射しない (C1)。
        # 未知値は stable_text_hash の先頭16桁で表し、baseline 上の dedupe
        # だけ成立させる。
        detail = stable_text_hash(str(schema_value))
        return [{"path": path, "kind": "unknown_schema_value", "detail": detail}]
    return []


def _is_symlink_or_escapes_root(path: Path, store_root: Path, resolved_root: Path) -> bool:
    """symlink ファイル / symlink 経由の親ディレクトリ / root 外への resolve を検知する。

    `site_adapter_store.py` の `_safe_artifact_path` と同じ判定方針。
    """
    if path.is_symlink():
        return True
    relative = path.relative_to(store_root)
    current = store_root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return True
    try:
        return not path.resolve().is_relative_to(resolved_root)
    except OSError:
        return True


def collect_violations(store_root: Path) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    if not store_root.exists():
        return violations

    resolved_root = store_root.resolve()
    for path in sorted(p for p in store_root.rglob("*") if p.is_file()):
        if _is_symlink_or_escapes_root(path, store_root, resolved_root):
            continue
        relative = path.relative_to(store_root)
        relative_posix = relative.as_posix()
        parts = relative.parts

        if not _is_allowed_location(parts):
            violations.append({"path": relative_posix, "kind": "disallowed_path", "detail": "outside_allow_list"})
            continue

        if parts[0] in NO_SCHEMA_CHECK_DIRS or relative_posix in NO_SCHEMA_CHECK_TOP_LEVEL_FILES:
            continue

        suffix = path.suffix.lower()
        if suffix == ".ndjson":
            records = _load_ndjson_records(path)
            if records is None:
                violations.append({"path": relative_posix, "kind": "missing_schema_key", "detail": "unreadable_ndjson"})
                continue
            for record in records:
                violations.extend(_schema_violations_for_record(record, path=relative_posix))
        elif suffix == ".json":
            payload = _load_json(path)
            if payload is None:
                violations.append({"path": relative_posix, "kind": "missing_schema_key", "detail": "unreadable_json"})
                continue
            if isinstance(payload, list):
                # save_context_library / save_review_registry はトップレベル JSON
                # **配列**を書く (核 writer の既存出力形式)。配列全体を単一 record
                # として検査すると常に record_not_an_object 扱いになるため、
                # 配列は要素ごとに検査する。
                for record in payload:
                    violations.extend(_schema_violations_for_record(record, path=relative_posix))
            else:
                violations.extend(_schema_violations_for_record(payload, path=relative_posix))
    return violations


def _violation_key(violation: dict[str, str]) -> tuple[str, str, str]:
    return violation["path"], violation["kind"], violation["detail"]


def _load_baseline(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return []
    entries = payload.get("violations")
    return entries if isinstance(entries, list) else []


def _write_baseline(path: Path, violations: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "dcb.lint_capture_store_layout_baseline.v1",
        "violations": violations,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_report(
    *,
    store_root: Path = DEFAULT_STORE_ROOT,
    baseline_path: Path | None = None,
    write_baseline: bool = False,
    store_kind: str = DEFAULT_STORE_KIND,
) -> dict[str, Any]:
    if store_kind not in STORE_KINDS:
        raise ValueError("unsupported store kind")

    if store_kind == "shared_snapshot_bundle":
        return {
            "schema": "dcb_lint_capture_store_layout_report.v1",
            "ok": False,
            "violation_count": 0,
            "violations": [],
            "known_violation_count": 0,
            "new_violations": [],
            "lint_performed": False,
            "store_kind": "shared_snapshot_bundle",
            "reason": "wrong_gate_for_store_kind",
            "required_gate": "full-capture-gate",
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }

    resolved_baseline_path = baseline_path if baseline_path is not None else (store_root / "lint-baseline.json")
    violations = collect_violations(store_root)

    if write_baseline:
        _write_baseline(resolved_baseline_path, violations)
        return {
            "schema": "dcb_lint_capture_store_layout_report.v1",
            "ok": True,
            "baseline_written": True,
            "violation_count": len(violations),
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }

    baseline = _load_baseline(resolved_baseline_path)
    # set membership だと同一 (path,kind,detail) の violation が baseline に 1 件でも
    # あれば、以後何件同じ tuple の violation が新たに増えても永久に抑止されてしまう
    # (codex review #5)。baseline 側の出現回数までは "known"、それを超えた分だけを
    # "new" として検知する。
    baseline_counts = Counter(_violation_key(entry) for entry in baseline)
    seen_counts: Counter = Counter()
    new_violations: list[dict[str, str]] = []
    known_violations: list[dict[str, str]] = []
    for violation in violations:
        key = _violation_key(violation)
        seen_counts[key] += 1
        if seen_counts[key] <= baseline_counts.get(key, 0):
            known_violations.append(violation)
        else:
            new_violations.append(violation)

    return {
        "schema": "dcb_lint_capture_store_layout_report.v1",
        "ok": not new_violations,
        "violation_count": len(violations),
        "violations": violations,
        "known_violation_count": len(known_violations),
        "new_violations": new_violations,
        # violations には store-root 相対パスを実際に含めるため "omitted" は
        # 実態と食い違う (C1)。絶対パス・url・本文は出さないが、相対パスは
        # 出す、という実際の挙動を自己申告する。
        "path_output": "relative_only",
        "outbound_actions": "disabled",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        store_root=args.store_root,
        baseline_path=args.baseline,
        write_baseline=args.write_baseline,
        store_kind=args.store_kind,
    )
    if args.json:
        print(_json(report))
    else:
        status = "成功" if report["ok"] else "失敗"
        print(f"capture store layout lint: {status}")
        print(_json(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
