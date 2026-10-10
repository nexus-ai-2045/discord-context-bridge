#!/usr/bin/env python3
"""Authorize one user-visible DCB claim from canonical closeout evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.capture.parallel_closeout import (
    evaluate_legacy_parallel_run_from_store,
    persist_legacy_parallel_closeout,
)
from discord_context_bridge.claims import CLAIMS, evaluate_context_claims


def _blocked(reason: str, claim: str, *, state: str = "unknown") -> dict[str, object]:
    return {
        "schema": "dcb.context-claim-gate.v1",
        "claim": claim,
        "state": state,
        "allowed": False,
        "blockers": [reason],
        "stages": {},
        "raw_text_returned": False,
        "participant_names_returned": False,
        "identifiers_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DCB user-visible claim gate")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--completeness-db", type=Path, required=True)
    parser.add_argument("--parent-target-key", required=True)
    parser.add_argument("--claim", choices=CLAIMS, required=True)
    parser.add_argument("--max-age-seconds", type=int, default=86_400)
    parser.add_argument("--understanding-confirmed", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evidence = evaluate_legacy_parallel_run_from_store(
            args.run_dir,
            completeness_db=args.completeness_db,
            parent_target_key=args.parent_target_key,
        )
        if evidence.get("full_capture_confirmed") is True:
            evidence = persist_legacy_parallel_closeout(
                args.run_dir,
                completeness_db=args.completeness_db,
                parent_target_key=args.parent_target_key,
            )
    except (OSError, ValueError):
        report = _blocked("canonical_closeout_unavailable", args.claim)
    else:
        policy = evaluate_context_claims(
            evidence,
            understanding_confirmed=args.understanding_confirmed,
            max_age_seconds=args.max_age_seconds,
        )
        decision = policy["claims"][args.claim]
        report = {
            **_blocked("context_claim_blocked", args.claim, state=str(decision["state"])),
            "allowed": decision["allowed"],
            "blockers": decision["blockers"],
            "stages": policy["stages"],
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["allowed"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
