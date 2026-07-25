#!/usr/bin/env python3
"""ADR-0162 Phase 1 W5: 構造化済み capture ファイルを単一 ledger へ ingest する CLI。

対応フォーマット (Phase 1 は構造化済み変種のみ):

- `dcb.raw_capture.v1`
- `dcb.visible_message_record.v1`
- `dcb.incremental_visible_message.v1`

`--input` に JSON または NDJSON ファイルを渡すと、検証してメッセージ単位で
`text-snapshots.ndjson` (既定) へ ingest する。拡張子が `.ndjson` の場合、
または単一 JSON として parse できない場合は行単位 parse にフォールバックし、
複数行 (= 複数メッセージ) を 1 バッチとして ingest する (R1)。

`--url` を指定すると、レコードが持つ `target_key` と `sha256(url)[:16]` の
一致を検証し、不一致なら書き込まず error で停止する (R3)。

既定は dry-run。`--apply` を付けた時だけ書き込む。stdout は metadata-only。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.core import DEFAULT_TEXT_SNAPSHOT_STORE, stable_text_hash  # noqa: E402
from discord_context_bridge.ingest import ingest_capture  # noqa: E402
from discord_context_bridge.target_registry import DEFAULT_TARGET_REGISTRY_STORE  # noqa: E402


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="構造化済み capture ファイルを text-snapshots.ndjson へ ingest する。"
    )
    parser.add_argument("--input", type=Path, required=True, help="ingest する JSON / NDJSON ファイル")
    parser.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE)
    parser.add_argument("--registry-store", type=Path, default=DEFAULT_TARGET_REGISTRY_STORE)
    parser.add_argument(
        "--url",
        default="",
        help="レコードの target_key が sha256(url)[:16] と一致するか検証する (不一致は error)",
    )
    parser.add_argument("--apply", action="store_true", help="実際に ledger へ追記する (既定は dry-run)")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def _parse_ndjson_lines(text: str) -> list[dict[str, Any]] | None:
    records: list[dict[str, Any]] = []
    # `str.splitlines()` は U+2028/U+0085 等の Unicode 行区切りも分割対象にし、
    # body_text にそれらの文字が含まれる実データで JSON レコードを途中で
    # 分断してしまう。NDJSON は "\n" 区切りの契約なので明示的に "\n" だけで
    # 区切る (実 capture artifact での実測起因の修正)。
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(loaded, dict):
            return None
        records.append(loaded)
    return records if records else None


def _read_payload(path: Path) -> dict[str, Any] | list[dict[str, Any]] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if path.suffix.lower() == ".ndjson":
        return _parse_ndjson_lines(text)

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return _parse_ndjson_lines(text)

    if isinstance(loaded, dict):
        return loaded
    if isinstance(loaded, list) and loaded and all(isinstance(item, dict) for item in loaded):
        return loaded
    return None


def _extract_target_key_field(payload: dict[str, Any] | list[dict[str, Any]]) -> str:
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if isinstance(row, dict):
            value = str(row.get("target_key") or "").strip()
            if value:
                return value
    return ""


def _unreadable_result(apply: bool) -> dict[str, Any]:
    return {
        "schema": "dcb.ingest_result.v1",
        "ok": False,
        "reason": "input_unreadable",
        "adapter": "unknown",
        "dry_run": not apply,
        "events_appended": 0,
        "duplicates": 0,
        "target_key": None,
        "outbound_actions": "disabled",
    }


def _url_mismatch_result(apply: bool) -> dict[str, Any]:
    return {
        "schema": "dcb.ingest_result.v1",
        "ok": False,
        "reason": "target_key_url_mismatch",
        "adapter": "unknown",
        "dry_run": not apply,
        "events_appended": 0,
        "duplicates": 0,
        "target_key": None,
        "outbound_actions": "disabled",
    }


def build_result(
    *,
    input_path: Path,
    snapshot_store: Path,
    registry_store: Path,
    apply: bool,
    url: str = "",
) -> dict[str, Any]:
    payload = _read_payload(input_path)
    if payload is None:
        return _unreadable_result(apply)

    if url.strip():
        expected_target_key = stable_text_hash(url.strip())
        record_target_key = _extract_target_key_field(payload)
        if record_target_key and record_target_key != expected_target_key:
            return _url_mismatch_result(apply)

    return ingest_capture(
        payload,
        snapshot_store=snapshot_store,
        registry_store=registry_store,
        apply=apply,
        source_ref=input_path.name,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_result(
        input_path=args.input,
        snapshot_store=args.snapshot_store,
        registry_store=args.registry_store,
        apply=args.apply,
        url=args.url,
    )
    if args.json:
        print(_json(result))
    else:
        status = "成功" if result["ok"] else "失敗"
        mode = "dry-run" if result.get("dry_run", True) else "apply"
        print(f"ingest-capture ({mode}): {status}")
        print(_json(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
