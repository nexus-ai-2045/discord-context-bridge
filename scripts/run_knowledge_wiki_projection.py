from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.knowledge_projection_ops import (
    run_projection,
    verify_projection_receipt,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Knowledge Wiki projection operational runner")
    parser.add_argument("--snapshot-store", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--person-registry", type=Path)
    parser.add_argument("--topic-registry", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify_projection_receipt(args.receipt)
    else:
        result = run_projection(
            snapshot_store=args.snapshot_store,
            output_root=args.output_root,
            receipt_path=args.receipt,
            lock_path=args.lock,
            person_registry=args.person_registry,
            topic_registry=args.topic_registry,
            dry_run=args.dry_run,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
