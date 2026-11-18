#!/usr/bin/env python3
"""Fail when the mandatory context-claim gate is disconnected."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "context_claim_gate.py"
CONTRACT_MARKER = "mandatory_context_claim_gate"
RUNTIMES = ("codex", "claude-code", "grok", "antigravity")


def lint(root: Path = ROOT) -> dict[str, object]:
    required = {
        "manifest": (root / "capability" / "manifest.yaml", TOKEN),
        "contract": (root / "docs" / "operating-contract.md", CONTRACT_MARKER),
        "ops_check": (root / "scripts" / "ops_check.py", "context claim gate smoke"),
        **{
            f"runtime:{runtime}": (root / "dist" / "skills" / runtime / "SKILL.md", TOKEN)
            for runtime in RUNTIMES
        },
    }
    checks: dict[str, dict[str, object]] = {}
    for name, (path, marker) in required.items():
        try:
            present = marker in path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            present = False
        checks[name] = {"ok": present, "reason": "wired" if present else "claim_gate_wiring_missing"}
    ok = all(item["ok"] is True for item in checks.values())
    return {
        "schema": "dcb.context-claim-gate-wiring-lint.v1",
        "ok": ok,
        "checks": checks,
        "outbound_actions": "disabled",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = lint()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print("context claim gate wiring: " + ("OK" if report["ok"] else "NG"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
