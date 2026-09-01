import json

import pytest

from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.grounding_gate import build_context_grounding_gate
from discord_context_bridge.site_adapter_runtime import MAX_INPUT_BYTES


TARGET = "anonymous-target"


def evidence(receipt_id="evidence-1", role="user_observed", **overrides):
    value = {"schema": "discord_context_grounding_evidence.v1", "receipt_id": receipt_id,
             "source_role": role, "target_key": TARGET}
    value.update(overrides)
    return value


def draft(**overrides):
    value = {"intent": "draft", "target_key": TARGET,
             "evidence_receipts": [evidence()],
             "claims": [{"claim_type": "thread_owner", "evidence_ref": "evidence-1"}]}
    value.update(overrides)
    return value


def candidate(candidate_id, **overrides):
    value = {"candidate_id": candidate_id, "target_key": TARGET,
             "message_period": {"start": "2026-08-01T05:00:00+00:00",
                                "end": "2026-08-01T07:00:00+00:00", "source_role": "message_event"},
             "semantic_anchors": ["anonymous-reply-anchor"], "thread_purpose": "anonymous-owner-topic",
             "ranking_basis": ["semantic_anchor", "thread_purpose"]}
    value.update(overrides)
    return value


def recommendation(**overrides):
    value = {"intent": "channel_recommendation", "target_key": TARGET,
             "evidence_receipts": [], "claims": [],
             "acquisition_completion_gate": {
                 "schema": "discord_context_acquisition_completion_gate.v1", "receipt_id": "acq-1",
                 "target_key": TARGET, "summary_ready": True},
             "channel_candidates": [candidate("candidate-1"), candidate("candidate-2")]}
    value.update(overrides)
    return value


@pytest.mark.parametrize("payload", [None, {}, {"intent": ""}, {"intent": "drafft"}, {"intent": []}, []])
def test_invalid_intent_or_nonobject_is_invalid_contract(payload):
    assert build_context_grounding_gate(payload)["reason_codes"] == ["invalid_contract"]


@pytest.mark.parametrize("claims", [[], ["not-an-object"], [{"claim_type": "unknown", "evidence_ref": "evidence-1"}]])
def test_draft_rejects_empty_mixed_or_unknown_claims(claims):
    gate = build_context_grounding_gate(draft(claims=claims))
    assert gate["ready"] is False
    assert "invalid_contract" in gate["reason_codes"]


def test_self_declared_claim_role_cannot_replace_evidence_receipt():
    payload = draft(evidence_receipts=[], claims=[{
        "claim_type": "thread_owner", "source_role": "user_observed", "evidence_ref": "missing"}])
    assert build_context_grounding_gate(payload)["reason_codes"] == ["claim_source_role_mismatch"]


def test_receipt_role_is_checked_by_claim_type():
    payload = draft(evidence_receipts=[evidence(role="media_verified")])
    assert build_context_grounding_gate(payload)["reason_codes"] == ["claim_source_role_mismatch"]


def test_claim_role_must_match_referenced_receipt_when_present():
    payload = draft(claims=[{"claim_type": "thread_owner", "source_role": "media_verified",
                             "evidence_ref": "evidence-1"}])
    assert build_context_grounding_gate(payload)["reason_codes"] == ["claim_source_role_mismatch"]


def test_metadata_role_receipt_is_invalid_contract():
    gate = build_context_grounding_gate(draft(evidence_receipts=[evidence(role="metadata_only")]))
    assert gate["reason_codes"] == ["invalid_contract"]


def test_valid_user_thread_owner_draft_passes():
    assert build_context_grounding_gate(draft())["ready"] is True


@pytest.mark.parametrize("candidates", [[candidate("only")],
    [candidate("same"), candidate("same")],
    [candidate("one"), candidate("two", target_key="other")],
    [candidate("one"), candidate("two", ranking_basis=["unknown"])],
    [candidate("one"), candidate("two", ranking_basis=[{}])],
    [candidate("one"), candidate("two", semantic_anchors="not-a-list")],
    [candidate("one"), candidate("two", thread_purpose=123)],
])
def test_recommendation_candidate_contract_fails_closed(candidates):
    gate = build_context_grounding_gate(recommendation(channel_candidates=candidates))
    assert gate["ready"] is False
    assert "invalid_contract" in gate["reason_codes"]


def test_capture_time_cannot_stand_in_for_message_period():
    candidates = [candidate("one"), candidate("two", message_period=None,
                  captured_at="2026-08-01T07:01:00+00:00", message_period_source="captured_at")]
    gate = build_context_grounding_gate(recommendation(channel_candidates=candidates))
    assert "capture_time_used_as_message_time" in gate["reason_codes"]


def test_partial_or_heuristic_only_context_cannot_rank_channels():
    payload = recommendation(
        acquisition_completion_gate={"schema": "discord_context_acquisition_completion_gate.v1",
                                     "receipt_id": "acq-1", "target_key": TARGET, "summary_ready": False},
        channel_candidates=[candidate("one", ranking_basis=["keyword", "temperature"]),
                            candidate("two", ranking_basis=["keyword"])])
    gate = build_context_grounding_gate(payload)
    assert "partial_context" in gate["reason_codes"]
    assert "heuristic_only_ranking" in gate["reason_codes"]


def test_acquisition_target_mismatch_is_invalid_contract():
    acquisition = dict(recommendation()["acquisition_completion_gate"], target_key="other")
    gate = build_context_grounding_gate(recommendation(acquisition_completion_gate=acquisition))
    assert "invalid_contract" in gate["reason_codes"]


def test_valid_recommendation_passes():
    assert build_context_grounding_gate(recommendation())["ready"] is True


def test_cli_rejects_nan_as_invalid_contract(tmp_path, capsys):
    contract = tmp_path / "nan.json"
    contract.write_text('{"intent":"draft","value":NaN}', encoding="utf-8")
    assert cli_main(["context-grounding-gate", "--input", str(contract)]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["invalid_contract"]


def test_cli_rejects_oversize_input(tmp_path, capsys):
    contract = tmp_path / "large.json"
    contract.write_bytes(b"x" * (MAX_INPUT_BYTES + 1))
    assert cli_main(["context-grounding-gate", "--input", str(contract)]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["invalid_contract"]


def test_cli_distinguishes_unreadable_input(tmp_path, capsys):
    assert cli_main(["context-grounding-gate", "--input", str(tmp_path / "missing.json")]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["input_unreadable"]


def test_cli_accepts_valid_contract(tmp_path, capsys):
    contract = tmp_path / "grounding.json"
    contract.write_text(json.dumps(draft()), encoding="utf-8")
    assert cli_main(["context-grounding-gate", "--input", str(contract)]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
