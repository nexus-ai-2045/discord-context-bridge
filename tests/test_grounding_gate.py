import hashlib
import json

import pytest

from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.grounding_gate import build_context_grounding_gate
from discord_context_bridge.site_adapter_runtime import MAX_INPUT_BYTES


TARGET = "anonymous-target"


def artifact(tmp_path, name, content=b"verified private evidence"):
    path = tmp_path / name
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def evidence_receipt(tmp_path, *, role="user_observed", issuer="dcb_user_observation", **overrides):
    path, digest = artifact(tmp_path, "evidence.bin")
    value = {"schema": "discord_context_grounding_evidence.v1", "issuer": issuer,
             "receipt_id": "evidence-1", "artifact_id": "artifact-1", "artifact_path": path.name,
             "artifact_sha256": digest, "source_role": role, "target_key": TARGET}
    value.update(overrides)
    return value


def draft(tmp_path, **overrides):
    receipt = evidence_receipt(tmp_path)
    value = {"intent": "draft", "target_key": TARGET,
             "claims": [{"claim_type": "thread_owner", "evidence_ref": "evidence-1",
                         "expected_artifact_sha256": receipt["artifact_sha256"]}]}
    value.update(overrides)
    return value, [receipt]


def candidate(candidate_id, **overrides):
    value = {"candidate_id": candidate_id, "target_key": TARGET,
             "message_period": {"start": "2026-08-01T05:00:00+00:00",
                                "end": "2026-08-01T07:00:00+00:00", "source_role": "message_event"},
             "semantic_anchors": ["anonymous-anchor"], "thread_purpose": "anonymous-purpose",
             "ranking_basis": ["semantic_anchor", "thread_purpose"]}
    value.update(overrides)
    return value


def recommendation(tmp_path, **overrides):
    coverage_path, digest = artifact(tmp_path, "coverage.json", b"canonical coverage")
    contract = {"intent": "channel_recommendation", "target_key": TARGET, "claims": [],
                "acquisition_ref": "acq-1", "expected_acquisition_artifact_sha256": digest,
                "expected_capture_id": "capture-1",
                "channel_candidates": [candidate("candidate-1"), candidate("candidate-2")]}
    acquisition = {"schema": "discord_context_acquisition_completion_gate.v1",
                   "issuer": "dcb_context_acquisition", "receipt_id": "acq-1",
                   "artifact_id": "coverage-1", "artifact_sha256": digest,
                   "coverage_artifact_path": coverage_path.name, "coverage_artifact_sha256": digest,
                   "capture_id": "capture-1", "target_key": TARGET, "summary_ready": True,
                   "candidate_scope": ["candidate-1", "candidate-2"],
                   "message_period": {"start": "2026-08-01T04:00:00+00:00",
                                      "end": "2026-08-01T08:00:00+00:00"}}
    contract.update(overrides.pop("contract", {}))
    acquisition.update(overrides.pop("acquisition", {}))
    return contract, acquisition


def evaluate(contract, receipts, tmp_path, acquisition=None):
    return build_context_grounding_gate(
        contract, trusted_evidence_receipts=receipts, trusted_acquisition_receipt=acquisition,
        trusted_evidence_root=tmp_path, trusted_acquisition_root=tmp_path)


@pytest.mark.parametrize("payload", [None, {}, {"intent": ""}, {"intent": []}, []])
def test_invalid_contract_shapes_fail_closed(payload, tmp_path):
    assert evaluate(payload, [], tmp_path)["reason_codes"] == ["invalid_contract"]


def test_direct_api_without_trusted_receipts_fails(tmp_path):
    contract, _ = draft(tmp_path)
    assert build_context_grounding_gate(contract)["ready"] is False


def test_embedded_self_asserted_receipts_cannot_open_gate(tmp_path):
    contract, receipts = draft(tmp_path)
    contract["evidence_receipts"] = receipts
    assert evaluate(contract, receipts, tmp_path)["reason_codes"] == ["invalid_contract"]


def test_valid_user_thread_owner_draft_reads_back_artifact(tmp_path):
    contract, receipts = draft(tmp_path)
    assert evaluate(contract, receipts, tmp_path)["ready"] is True


@pytest.mark.parametrize("receipt_change", [
    {"issuer": "untrusted"}, {"target_key": "other"}, {"artifact_sha256": "0" * 64},
    {"artifact_path": "missing.bin"}, {"artifact_path": "../outside.bin"},
])
def test_forged_evidence_receipt_fails(tmp_path, receipt_change):
    contract, receipts = draft(tmp_path)
    receipts[0].update(receipt_change)
    assert evaluate(contract, receipts, tmp_path)["ready"] is False


def test_claim_expected_hash_must_match_receipt(tmp_path):
    contract, receipts = draft(tmp_path)
    contract["claims"][0]["expected_artifact_sha256"] = "0" * 64
    assert evaluate(contract, receipts, tmp_path)["reason_codes"] == ["claim_source_role_mismatch"]


def test_valid_recommendation_reads_back_coverage_artifact(tmp_path):
    contract, acquisition = recommendation(tmp_path)
    assert evaluate(contract, [], tmp_path, acquisition)["ready"] is True


@pytest.mark.parametrize("acquisition_change", [
    {"issuer": "untrusted"}, {"target_key": "other"}, {"capture_id": "other"},
    {"coverage_artifact_sha256": "0" * 64}, {"coverage_artifact_path": "missing.json"},
    {"candidate_scope": ["candidate-2", "candidate-1"]},
])
def test_forged_or_misbound_acquisition_receipt_fails(tmp_path, acquisition_change):
    contract, acquisition = recommendation(tmp_path)
    acquisition.update(acquisition_change)
    assert evaluate(contract, [], tmp_path, acquisition)["ready"] is False


def test_capture_time_and_heuristic_only_ranking_fail(tmp_path):
    contract, acquisition = recommendation(tmp_path)
    contract["channel_candidates"][1].update(
        message_period=None, captured_at="2026-08-01T07:00:00+00:00",
        message_period_source="captured_at", ranking_basis=["keyword"])
    gate = evaluate(contract, [], tmp_path, acquisition)
    assert "capture_time_used_as_message_time" in gate["reason_codes"]
    assert "heuristic_only_ranking" in gate["reason_codes"]


def test_candidate_scope_and_types_fail_closed(tmp_path):
    contract, acquisition = recommendation(tmp_path)
    contract["channel_candidates"][1]["semantic_anchors"] = "not-a-list"
    assert evaluate(contract, [], tmp_path, acquisition)["ready"] is False


def write_cli_inputs(tmp_path, contract, receipts, acquisition=None):
    contract_path, receipts_path = tmp_path / "contract.json", tmp_path / "receipts.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    receipts_path.write_text(json.dumps(receipts), encoding="utf-8")
    args = ["context-grounding-gate", "--input", str(contract_path),
            "--evidence-receipts", str(receipts_path)]
    if acquisition is not None:
        acquisition_path = tmp_path / "acquisition.json"
        acquisition_path.write_text(json.dumps(acquisition), encoding="utf-8")
        args += ["--acquisition-receipt", str(acquisition_path)]
    return args


def test_cli_valid_draft_uses_separate_receipt_file(tmp_path, capsys):
    contract, receipts = draft(tmp_path)
    assert cli_main(write_cli_inputs(tmp_path, contract, receipts)) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True


def test_cli_recommendation_requires_separate_acquisition_file(tmp_path, capsys):
    contract, _ = recommendation(tmp_path)
    assert cli_main(write_cli_inputs(tmp_path, contract, [])) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["invalid_contract"]


def test_cli_valid_recommendation_uses_separate_receipts(tmp_path, capsys):
    contract, acquisition = recommendation(tmp_path)
    assert cli_main(write_cli_inputs(tmp_path, contract, [], acquisition)) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True


@pytest.mark.parametrize("content", ['{"intent":"draft","value":NaN}', "not-json"])
def test_cli_invalid_json_is_invalid_contract(tmp_path, capsys, content):
    contract = tmp_path / "contract.json"
    receipts = tmp_path / "receipts.json"
    contract.write_text(content, encoding="utf-8")
    receipts.write_text("[]", encoding="utf-8")
    assert cli_main(["context-grounding-gate", "--input", str(contract),
                     "--evidence-receipts", str(receipts)]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["invalid_contract"]


def test_cli_rejects_oversize_receipt_file(tmp_path, capsys):
    contract, _ = draft(tmp_path)
    contract_path, receipts_path = tmp_path / "contract.json", tmp_path / "receipts.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    receipts_path.write_bytes(b"x" * (MAX_INPUT_BYTES + 1))
    assert cli_main(["context-grounding-gate", "--input", str(contract_path),
                     "--evidence-receipts", str(receipts_path)]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["invalid_contract"]
