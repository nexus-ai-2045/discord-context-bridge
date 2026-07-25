#!/usr/bin/env python3
"""ADR-0162 Phase 1 W5: 構造化済み capture ファイルを単一 ledger へ ingest する CLI。

対応フォーマット (Phase 1 は構造化済み変種のみ):

- `dcb.raw_capture.v1`
- `dcb.visible_message_record.v1`
- `dcb.incremental_visible_message.v1`

`--input` に JSON ファイルを渡すと、検証してメッセージ単位で
`text-snapshots.ndjson` (既定) へ ingest する。既定は dry-run。
`--apply` を付けた時だけ書き込む。stdout は metadata-only。
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

from discord_context_bridge.core import DEFAULT_TEXT_SNAPSHOT_STORE  # noqa: E402
from discord_context_bridge.ingest import ingest_capture  # noqa: E402
from discord_context_bridge.target_registry import DEFAULT_TARGET_REGISTRY_STORE  # noqa: E402


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="構造化済み capture ファイルを text-snapshots.ndjson へ ingest する。"
    )
    parser.add_argument("--input", type=Path, required=True, help="ingest する JSON ファイル")
    parser.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE)
    parser.add_argument("--registry-store", type=Path, default=DEFAULT_TARGET_REGISTRY_STORE)
    parser.add_argument("--apply", action="store_true", help="実際に ledger へ追記する (既定は dry-run)")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def _read_payload(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def build_result(
    *,
    input_path: Path,
    snapshot_store: Path,
    registry_store: Path,
    apply: bool,
) -> dict[str, Any]:
    payload = _read_payload(input_path)
    if payload is None:
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
