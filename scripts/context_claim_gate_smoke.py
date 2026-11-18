#!/usr/bin/env python3
"""Smoke the mandatory DCB user-visible claim gate."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta

import context_claim_gate


def _evidence(schema: str = "dcb.parallel-run-operational-closeout.v1") -> dict[str, object]:
    return {
        "schema": schema,
        "status": "full",
        "terminal_state": "full_closed",
        "full_capture_confirmed": True,
        "persistence_confirmed": True,
        "blockers": [],
        "evidence_observed_at": datetime.now(UTC).isoformat(),
        "outbound_actions": "disabled",
    }


def _run(claim: str, evidence: dict[str, object], *, understood: bool = False) -> tuple[int, dict[str, object]]:
    arguments = [
        "--run-dir",
        "fixture-run",
        "--completeness-db",
        "fixture.sqlite3",
        "--parent-target-key",
        "fixture-parent",
        "--claim",
        claim,
        "--max-age-seconds",
        "60",
        "--json",
    ]
    if understood:
        arguments.append("--understanding-confirmed")
    output = io.StringIO()
    original_evaluate = context_claim_gate.evaluate_legacy_parallel_run_from_store
    original_persist = context_claim_gate.persist_legacy_parallel_closeout
    context_claim_gate.evaluate_legacy_parallel_run_from_store = lambda *_args, **_kwargs: dict(evidence)
    context_claim_gate.persist_legacy_parallel_closeout = lambda *_args, **_kwargs: dict(evidence)
    try:
        with redirect_stdout(output):
            exit_code = context_claim_gate.main(arguments)
    finally:
        context_claim_gate.evaluate_legacy_parallel_run_from_store = original_evaluate
        context_claim_gate.persist_legacy_parallel_closeout = original_persist
    return exit_code, json.loads(output.getvalue())


def run_smoke() -> dict[str, object]:
    now = datetime.now(UTC)
    projection_exit, projection_report = _run("complete", _evidence("chronica.projection.v1"))
    stale_evidence = _evidence()
    stale_evidence["evidence_observed_at"] = (now - timedelta(days=2)).isoformat()
    stale_exit, stale_report = _run("current", stale_evidence)
    understood_exit, understood_report = _run("understood", _evidence(), understood=True)
    projection_blocked = projection_exit == 2 and projection_report["allowed"] is False
    stale_blocked = stale_exit == 2 and stale_report["allowed"] is False
    canonical_allowed = understood_exit == 0 and understood_report["allowed"] is True
    return {
        "schema": "dcb.context-claim-gate-smoke.v1",
        "ok": projection_blocked and stale_blocked and canonical_allowed,
        "projection_bypass_blocked": projection_blocked,
        "stale_claim_blocked": stale_blocked,
        "canonical_fresh_understanding_allowed": canonical_allowed,
        "raw_text_returned": False,
        "participant_names_returned": False,
        "identifiers_returned": False,
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_smoke()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print("context claim gate smoke: " + ("OK" if report["ok"] else "NG"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
