"""外部の非公開snapshot競合を、取得時刻を保った観測eventとして取り込む。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from discord_context_bridge.external_snapshot_import import import_external_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="外部snapshotの非公開取込。既定は書込みなしの検査です。")
    parser.add_argument("--snapshot-root", type=Path, required=True, help="既存の正本ルート")
    parser.add_argument("--source-file", type=Path, required=True, help="非公開の競合NDJSON")
    parser.add_argument("--target-url", required=True, help="検証済み対象URL。出力には表示しません")
    parser.add_argument("--expected-source-sha256", required=True, help="事前照合した入力SHA-256")
    parser.add_argument("--expected-store-sha256", help="dry-runで確認した正本SHA-256")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="検証後にstageから正本へ反映する")
    mode.add_argument("--dry-run", action="store_true", help="検査のみ。正本へ書き込みません")
    parser.add_argument("--json", action="store_true", help="秘密情報を含まない検査結果")
    args = parser.parse_args(argv)
    result = import_external_snapshot(
        snapshot_root=args.snapshot_root, source_file=args.source_file,
        target_url=args.target_url, expected_source_sha256=args.expected_source_sha256,
        expected_store_sha256=args.expected_store_sha256, apply=args.apply,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("外部snapshot取込検査")
        print(f"状態: {result['status']}")
        print(f"追記件数: {result['events_appended']}")
        print("本文・URL・識別子・保存パスは非表示。外部送信は無効です。")
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
