#!/usr/bin/env python3
"""同じ論理スレッドの別表記へ番号を継いだ観測を、原行不変の訂正eventで束縛する。既定は書込みなしの検証。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from discord_context_bridge.core import acknowledge_alias_continuations  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-store", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="検証後に訂正eventを追記する")
    args = parser.parse_args()
    try:
        result = acknowledge_alias_continuations(args.snapshot_store, dry_run=not args.apply)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        print(json.dumps({
            "saved": False,
            "status": "blocked",
            "reason": "alias_continuation_evidence_invalid",
            "outbound_actions": "disabled",
        }, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
