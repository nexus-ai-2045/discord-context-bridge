#!/usr/bin/env python3
"""Trusted base-side static audit for the mandatory context-claim gate."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

RUNTIMES = ("codex", "claude-code", "grok", "antigravity")
CLAIM_COMMAND = "scripts/context_claim_gate.py --run-dir"


def _source(root: Path, path: Path, *, max_bytes: int = 1_000_000) -> str | None:
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return None
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _defines(root: Path, path: Path, function: str) -> bool:
    source = _source(root, path)
    if source is None:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name == function for node in tree.body)


def verify(head_root: Path) -> dict[str, object]:
    claims = head_root / "src" / "discord_context_bridge" / "claims"
    required_functions = {
        "capture": (claims / "capture_claim.py", "evaluate_capture_claim"),
        "freshness": (claims / "freshness_claim.py", "evaluate_freshness_claim"),
        "understanding": (claims / "understanding_claim.py", "evaluate_understanding_claim"),
        "composition": (claims / "policy.py", "evaluate_context_claims"),
    }
    checks: dict[str, bool] = {
        name: _defines(head_root, path, function)
        for name, (path, function) in required_functions.items()
    }

    command = _source(head_root, head_root / "scripts" / "context_claim_gate.py") or ""
    checks["canonical_recheck"] = all(
        token in command
        for token in (
            "evaluate_legacy_parallel_run_from_store(",
            "persist_legacy_parallel_closeout(",
            "--run-dir",
            "--completeness-db",
            "--parent-target-key",
        )
    ) and "--evidence" not in command

    policy = _source(head_root, claims / "policy.py") or ""
    checks["small_gate_composition"] = all(
        token in policy
        for token in (
            "evaluate_capture_claim(",
            "evaluate_freshness_claim(",
            "evaluate_understanding_claim(",
        )
    )

    manifest = _source(head_root, head_root / "capability" / "manifest.yaml") or ""
    contract = _source(head_root, head_root / "docs" / "operating-contract.md") or ""
    ops_check = _source(head_root, head_root / "scripts" / "ops_check.py") or ""
    checks["manifest"] = CLAIM_COMMAND in manifest
    checks["contract"] = "mandatory_context_claim_gate" in contract
    checks["ops_check"] = all(
        token in ops_check
        for token in ("context claim gate smoke", "context claim gate trusted audit")
    )
    for runtime in RUNTIMES:
        skill = _source(head_root, head_root / "dist" / "skills" / runtime / "SKILL.md") or ""
        checks[f"runtime:{runtime}"] = CLAIM_COMMAND in skill

    workflow = _source(
        head_root,
        head_root / ".github" / "workflows" / "context-claim-gate-trusted.yml",
    ) or ""
    checks["trusted_workflow"] = all(
        token in workflow
        for token in ("pull_request_target", "verify_context_claim_gate_head.py", "permissions:", "contents: read")
    )
    ok = all(checks.values())
    return {
        "schema": "dcb.context-claim-gate-trusted-audit.v1",
        "status": "pass" if ok else "blocked",
        "ok": ok,
        "checks": checks,
        "candidate_executed": False,
        "outbound_actions": "disabled",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head-root", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = verify(args.head_root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print("context claim gate trusted audit: " + report["status"])
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
