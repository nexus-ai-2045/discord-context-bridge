from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import chrome_live_read_stage_guard


STAGES = ("transport", "open_tabs", "claim", "goto", "dom", "evaluate", "virtual_scroll")


def _observation(*, exact: bool = False) -> dict:
    target = "sha256:target"
    return {
        "guard_schema": "chrome_visible_fallback_guard.v2",
        "guard_decision": "claim_existing_target_tab" if exact else "claim_existing_discord_tab_then_navigate",
        "target_digest": target,
        "snapshot_binding": {
            "target_digest": target,
            "binding_token_digest": "sha256:binding",
            "open_tabs_digest": "sha256:tabs",
            "selected_claim_object_digest": "sha256:claim",
        },
        "claim_binding": {
            "binding_token_digest": "sha256:binding",
            "open_tabs_digest": "sha256:tabs",
            "claim_object_digest": "sha256:claim",
            "provider_tab_id_digest": "sha256:tab",
            "title_digest": "sha256:title",
            "url_digest": "sha256:url",
        },
        "target_already_exact": exact,
        "stages": [
            {
                "stage": stage,
                "attempts": [{"status": "passed", "target_digest": target, "elapsed_ms": 5}],
            }
            for stage in STAGES
        ],
    }


def test_classifier_accepts_bounded_same_target_single_retry_and_is_metadata_only() -> None:
    observation = _observation()
    observation["stages"][4]["attempts"] = [
        {"status": "timeout", "target_digest": "sha256:target", "elapsed_ms": 50},
        {"status": "passed", "target_digest": "sha256:target", "elapsed_ms": 5},
    ]
    payload = chrome_live_read_stage_guard.classify_observation(observation, timeout_ms=50)
    assert payload["ok"] is True
    assert payload["contract_valid"] is True
    assert payload["live_read_verified"] is False
    assert payload["reason"] == "observation_contract_valid_untrusted_provenance"
    assert payload["stage_classification"]["dom"] == "passed_after_retry"
    assert payload["retry_policy"] == "one_retry_same_target_only"
    assert payload["outbound_actions"] == "disabled"
    assert "sha256:target" not in json.dumps(payload)


def test_classifier_fails_closed_on_stale_claim_or_target_switch() -> None:
    stale = _observation()
    stale["claim_binding"]["open_tabs_digest"] = "sha256:stale"
    assert chrome_live_read_stage_guard.classify_observation(stale)["reason"] == "claim_snapshot_mismatch"

    wrong_object = _observation()
    wrong_object["claim_binding"]["claim_object_digest"] = "sha256:other-claim"
    assert chrome_live_read_stage_guard.classify_observation(wrong_object)["reason"] == "claim_object_mismatch"

    switched = _observation()
    switched["stages"][0]["attempts"] = [
        {"status": "timeout", "target_digest": "sha256:target", "elapsed_ms": 1000},
        {"status": "passed", "target_digest": "sha256:other", "elapsed_ms": 1},
    ]
    assert chrome_live_read_stage_guard.classify_observation(switched)["reason"] == "retry_target_mismatch"


def test_classifier_forbids_navigation_when_target_is_exact() -> None:
    observation = _observation(exact=True)
    goto = next(stage for stage in observation["stages"] if stage["stage"] == "goto")
    goto["attempts"] = [{"status": "passed", "target_digest": "sha256:target", "elapsed_ms": 1}]
    assert chrome_live_read_stage_guard.classify_observation(observation)["reason"] == "goto_forbidden_target_already_exact"

    goto["attempts"] = [{"status": "not_applicable", "target_digest": "sha256:target", "elapsed_ms": 0}]
    observation["reload_performed"] = True
    assert chrome_live_read_stage_guard.classify_observation(observation)["reason"] == "reload_forbidden_target_already_exact"


def test_classifier_rejects_malformed_duplicate_or_all_not_applicable_stages() -> None:
    malformed = _observation()
    malformed["stages"][0]["attempts"] = [None]
    assert chrome_live_read_stage_guard.classify_observation(malformed)["reason"] == "transport_attempt_invalid"

    duplicate = _observation()
    duplicate["stages"].append(duplicate["stages"][0])
    assert chrome_live_read_stage_guard.classify_observation(duplicate)["reason"] == "stage_sequence_invalid"

    no_live_evidence = _observation()
    for stage in no_live_evidence["stages"]:
        stage["attempts"][0]["status"] = "not_applicable"
    assert chrome_live_read_stage_guard.classify_observation(no_live_evidence)["reason"] == "transport_not_applicable_forbidden"


def test_classifier_enforces_one_retry_for_the_whole_pipeline() -> None:
    observation = _observation()
    for index in (0, 1):
        observation["stages"][index]["attempts"] = [
            {"status": "timeout", "target_digest": "sha256:target", "elapsed_ms": 10},
            {"status": "passed", "target_digest": "sha256:target", "elapsed_ms": 1},
        ]

    payload = chrome_live_read_stage_guard.classify_observation(observation)

    assert payload["ok"] is False
    assert payload["reason"] == "pipeline_retry_limit_exceeded"
    assert payload["live_read_verified"] is False


def test_classifier_requires_guard_rerun_after_opening_new_tab() -> None:
    observation = _observation()
    observation["guard_decision"] = "open_new_tab_in_existing_window"

    payload = chrome_live_read_stage_guard.classify_observation(observation)

    assert payload["ok"] is False
    assert payload["reason"] == "post_open_guard_rerun_required"
