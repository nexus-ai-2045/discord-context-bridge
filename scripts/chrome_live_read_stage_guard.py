#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


STAGES = ("transport", "open_tabs", "claim", "goto", "dom", "evaluate", "virtual_scroll")
TERMINAL_SUCCESS = {"passed", "not_applicable"}
REQUIRED_LIVE_STAGES = {"transport", "open_tabs", "claim", "dom", "evaluate"}


def _result(ok: bool, reason: str, classifications: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "schema": "chrome_read_observation_contract_guard.v1",
        "ok": ok,
        "contract_valid": ok,
        "live_read_verified": False,
        "verification_level": "untrusted_observation_contract_only",
        "reason": reason,
        "stage_classification": classifications or {},
        "retry_policy": "one_retry_same_target_only",
        "raw_output": "omitted",
        "private_identifiers_output": "omitted",
        "local_paths_output": "omitted",
        "outbound_actions": "disabled",
    }


def classify_observation(observation: dict[str, Any], *, timeout_ms: int = 30_000) -> dict[str, Any]:
    if not isinstance(observation, dict) or timeout_ms <= 0:
        return _result(False, "observation_contract_invalid")
    target_digest = observation.get("target_digest")
    snapshot = observation.get("snapshot_binding") or {}
    claim = observation.get("claim_binding") or {}
    if observation.get("guard_schema") != "chrome_visible_fallback_guard.v2":
        return _result(False, "guard_receipt_schema_mismatch")
    decision = observation.get("guard_decision")
    if decision == "open_new_tab_in_existing_window":
        return _result(False, "post_open_guard_rerun_required")
    if decision not in {
        "claim_existing_target_tab",
        "claim_existing_discord_tab_then_navigate",
    }:
        return _result(False, "guard_decision_invalid")
    if not target_digest:
        return _result(False, "target_digest_missing")
    if not isinstance(snapshot, dict) or not isinstance(claim, dict):
        return _result(False, "binding_contract_invalid")
    required_snapshot = (
        "binding_token_digest",
        "open_tabs_digest",
        "selected_claim_object_digest",
        "target_digest",
    )
    if any(not snapshot.get(key) for key in required_snapshot):
        return _result(False, "snapshot_binding_missing")
    required_claim = (
        "binding_token_digest",
        "open_tabs_digest",
        "claim_object_digest",
        "provider_tab_id_digest",
        "title_digest",
        "url_digest",
    )
    if any(not claim.get(key) for key in required_claim):
        return _result(False, "claim_binding_missing")
    if any(claim.get(key) != snapshot.get(key) for key in required_snapshot[:2]):
        return _result(False, "claim_snapshot_mismatch")
    if claim.get("claim_object_digest") != snapshot.get("selected_claim_object_digest"):
        return _result(False, "claim_object_mismatch")
    if snapshot.get("target_digest") != target_digest:
        return _result(False, "guard_target_mismatch")

    stages = observation.get("stages")
    if not isinstance(stages, list) or [item.get("stage") if isinstance(item, dict) else None for item in stages] != list(STAGES):
        return _result(False, "stage_sequence_invalid")
    by_stage = {item["stage"]: item for item in stages}
    classifications: dict[str, str] = {}
    retry_count = 0
    for stage in STAGES:
        item = by_stage.get(stage)
        if item is None:
            return _result(False, f"{stage}_observation_missing", classifications)
        attempts = item.get("attempts") or []
        if not isinstance(attempts, list) or not attempts:
            return _result(False, f"{stage}_attempt_missing", classifications)
        if len(attempts) > 2:
            return _result(False, f"{stage}_retry_limit_exceeded", classifications)
        retry_count += len(attempts) - 1
        if retry_count > 1:
            return _result(False, "pipeline_retry_limit_exceeded", classifications)
        if any(not isinstance(attempt, dict) for attempt in attempts):
            return _result(False, f"{stage}_attempt_invalid", classifications)
        if any(attempt.get("target_digest") != target_digest for attempt in attempts):
            return _result(False, "retry_target_mismatch", classifications)
        for attempt in attempts:
            elapsed = attempt.get("elapsed_ms")
            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
                return _result(False, f"{stage}_elapsed_invalid", classifications)
            if elapsed > timeout_ms:
                return _result(False, f"{stage}_timeout_exceeded", classifications)
        if len(attempts) == 2 and attempts[0].get("status") != "timeout":
            return _result(False, f"{stage}_retry_without_timeout", classifications)
        final_status = attempts[-1].get("status")
        if final_status not in TERMINAL_SUCCESS:
            return _result(False, f"{stage}_{final_status or 'status_missing'}", classifications)
        if final_status == "not_applicable" and stage in REQUIRED_LIVE_STAGES:
            return _result(False, f"{stage}_not_applicable_forbidden", classifications)
        classifications[stage] = "passed_after_retry" if len(attempts) == 2 else str(final_status)

    target_already_exact = decision == "claim_existing_target_tab"
    if bool(observation.get("target_already_exact")) != target_already_exact:
        return _result(False, "exact_target_claim_mismatch", classifications)
    if target_already_exact:
        goto_attempts = by_stage["goto"].get("attempts") or []
        if any(attempt.get("status") != "not_applicable" for attempt in goto_attempts):
            return _result(False, "goto_forbidden_target_already_exact", classifications)
        if observation.get("reload_performed"):
            return _result(False, "reload_forbidden_target_already_exact", classifications)
    elif classifications["goto"] == "not_applicable":
        return _result(False, "goto_required_for_non_exact_target", classifications)
    return _result(True, "observation_contract_valid_untrusted_provenance", classifications)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chrome read observationのmetadata-only構造を検証する。実ブラウザ成功の証明にはしない。"
    )
    parser.add_argument("--observation-json", required=True, type=Path)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        observation = json.loads(args.observation_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = _result(False, "observation_unreadable")
    else:
        payload = classify_observation(observation, timeout_ms=args.timeout_ms)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
