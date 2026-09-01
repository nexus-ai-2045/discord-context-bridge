import json

from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.grounding_gate import build_context_grounding_gate


def candidate(**overrides):
    value = {
        "message_period": {
            "start": "2026-08-01T05:00:00+00:00",
            "end": "2026-08-01T07:00:00+00:00",
            "source_role": "message_event",
        },
        "semantic_anchors": ["anonymous-reply-anchor"],
        "thread_purpose": "anonymous-owner-topic",
        "ranking_basis": ["semantic_anchor", "thread_purpose"],
    }
    value.update(overrides)
    return value


def test_capture_time_cannot_stand_in_for_message_period():
    gate = build_context_grounding_gate({
        "intent": "channel_recommendation",
        "acquisition_completion_gate": {"summary_ready": True},
        "channel_candidates": [candidate(
            message_period=None,
            captured_at="2026-08-01T07:01:00+00:00",
            message_period_source="captured_at",
        )],
    })
    assert gate["ready"] is False
    assert "capture_time_used_as_message_time" in gate["reason_codes"]


def test_partial_or_heuristic_only_context_cannot_rank_channels():
    gate = build_context_grounding_gate({
        "intent": "channel_recommendation",
        "acquisition_completion_gate": {"summary_ready": False},
        "channel_candidates": [candidate(ranking_basis=["keyword", "temperature"])],
    })
    assert gate["ready"] is False
    assert "partial_context" in gate["reason_codes"]
    assert "heuristic_only_ranking" in gate["reason_codes"]


def test_missing_semantic_anchor_or_purpose_fails_closed():
    gate = build_context_grounding_gate({
        "intent": "channel_recommendation",
        "acquisition_completion_gate": {"summary_ready": True},
        "channel_candidates": [candidate(semantic_anchors=[], thread_purpose="")],
    })
    assert gate["ready"] is False
    assert gate["reason_codes"] == ["semantic_anchor_missing"]


def test_media_metadata_cannot_be_presented_as_personal_reaction():
    gate = build_context_grounding_gate({
        "intent": "draft",
        "claims": [{"claim_type": "media_reaction", "source_role": "metadata_only"}],
    })
    assert gate["ready"] is False
    assert gate["reason_codes"] == ["claim_source_role_mismatch"]


def test_user_observed_thread_owner_fact_allows_minimal_draft():
    gate = build_context_grounding_gate({
        "intent": "draft",
        "claims": [{"claim_type": "thread_owner", "source_role": "user_observed"}],
    })
    assert gate["ready"] is True
    assert gate["reason_codes"] == []


def test_cli_evaluates_grounding_contract(tmp_path, capsys):
    contract = tmp_path / "grounding.json"
    contract.write_text(json.dumps({
        "intent": "draft",
        "claims": [{"claim_type": "thread_owner", "source_role": "user_observed"}],
    }), encoding="utf-8")
    assert cli_main(["context-grounding-gate", "--input", str(contract)]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
