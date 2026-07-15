from pathlib import Path
import json

import pytest
from jsonschema import ValidationError

from discord_context_bridge.site_adapter_runtime import MAX_INPUT_BYTES, MAX_MESSAGES, build_capture, build_manifest, load_adapter_definition, select_adapter, validate_artifact


URL = "https://discord.com/channels/1/2/3"


def test_select_adapter_accepts_discord_channel_url():
    assert select_adapter(URL)["adapter_id"] == "discord.v1"


def test_runtime_loads_and_validates_adapter_ssot(tmp_path):
    definition = load_adapter_definition()
    assert definition["outbound_actions"] == "disabled"
    bad = tmp_path / "discord.v1.json"
    bad.write_text('{"adapter_id":"discord.v2"}', encoding="utf-8")
    with pytest.raises(ValueError, match="definition drift"):
        load_adapter_definition(definition_path=bad)


def test_url_patterns_are_derived_from_index(tmp_path):
    source_index = json.loads((Path(__file__).parents[1] / "site_adapters/index.json").read_text(encoding="utf-8"))
    source_index["adapters"][0]["url_patterns"] = ["https://example.test/thread/*"]
    index = tmp_path / "index.json"
    index.write_text(json.dumps(source_index), encoding="utf-8")
    definition = Path(__file__).parents[1] / "site_adapters/discord.v1.json"
    assert select_adapter("https://example.test/thread/1", index_path=index, definition_path=definition)["adapter_version"] == "1"


@pytest.mark.parametrize("field,mutation", [
    ("required_extraction_fields", []),
    ("capture_order", []),
])
def test_contract_mutation_is_rejected(tmp_path, field, mutation):
    original = json.loads((Path(__file__).parents[1] / "site_adapters/discord.v1.json").read_text(encoding="utf-8"))
    original[field] = mutation
    path = tmp_path / "definition.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="definition drift"):
        load_adapter_definition(definition_path=path)


def test_select_adapter_rejects_unknown_url():
    with pytest.raises(ValueError, match="adapter"):
        select_adapter("https://example.com/thread/1")


def test_structured_capture_preserves_messages_privately():
    capture = build_capture(URL, structured={"messages": [{"author_label": "Alice", "body_text": "secret"}]})
    assert capture["capture_method"] == "structured_dom"
    assert capture["messages"][0]["body_text"] == "secret"


def test_body_text_is_explicit_fallback():
    capture = build_capture(URL, body_text="visible fallback")
    assert capture["capture_method"] == "body_inner_text"
    assert capture["messages"][0]["extraction_confidence"] == "low"


def test_zero_hit_and_missing_fields_are_drift():
    capture = build_capture(URL, structured={"selector_hits": 0, "messages": [{}]})
    assert "message_container_zero_hits" in capture["drift"]["items"]
    assert "timestamp_or_author_or_body_missing_repeatedly" in capture["drift"]["items"]


def test_unmapped_attachment_is_drift():
    capture = build_capture(URL, structured={"messages": [], "unattributed_cache_candidates": ["safe-ref"]})
    assert "attachment_url_unmapped_to_message" in capture["drift"]["items"]


def test_all_adopted_drift_signals_are_reported():
    capture = build_capture(URL, structured={
        "messages": [{"body_text": ""}], "body_text_present": True,
        "missing_field_streak": 2, "selector_hit_rate_drop": True,
        "viewport_bottom_confirmed": False, "failed_primary_selectors": 2,
    })
    assert {
        "structured_dom_empty_but_body_text_present",
        "timestamp_or_author_or_body_missing_repeatedly",
        "selector_hit_rate_drop", "viewport_bottom_unconfirmed",
        "multiple_primary_selectors_failed",
    } <= set(capture["drift"]["items"])


def test_caller_supplied_viewport_confirmation_cannot_claim_full_capture():
    message = {"body_text": "x", "author_label": "a", "visible_timestamp": "t"}
    absent = build_capture(URL, structured={"messages": [message]})
    confirmed = build_capture(URL, structured={"messages": [message], "viewport_bottom_confirmed": True})
    assert absent["viewport_bottom_confirmed"] is None
    assert confirmed["viewport_bottom_confirmed"] is True
    assert build_manifest(absent, raw_json="raw/x.json")["capture_state"] == "partial"
    assert build_manifest(confirmed, raw_json="raw/x.json")["capture_state"] == "partial"
    assert build_manifest(confirmed, raw_json="raw/x.json")["status"] == "partial"


def test_structured_capture_rejects_too_many_messages():
    with pytest.raises(ValueError, match="message limit"):
        build_capture(URL, structured={"messages": [{}] * (MAX_MESSAGES + 1)})


def test_structured_capture_rejects_aggregate_payload_over_budget():
    with pytest.raises(ValueError, match="aggregate input limit"):
        build_capture(URL, structured={"messages": [{"body_text": "x" * MAX_INPUT_BYTES}]})


def test_manifest_cannot_be_promoted_by_forged_completion_evidence():
    message = {"body_text": "x", "author_label": "a", "visible_timestamp": "t"}
    capture = build_capture(URL, structured={"messages": [message], "viewport_bottom_confirmed": True})
    capture["completion_evidence"] = {"source": "forged", "trusted": True}
    assert build_manifest(capture, raw_json="raw/x.json")["capture_state"] == "partial"


def test_structured_input_type_is_validated():
    with pytest.raises(ValueError, match="structured"):
        build_capture(URL, structured={"messages": "not-a-list"})


def test_emitted_drift_codes_are_declared_by_ssot():
    definition = load_adapter_definition()
    capture = build_capture(URL, structured={"selector_hits": 0, "messages": [{}]})
    assert set(capture["drift"]["items"]) <= set(definition["drift_checks"])
    fallback = build_capture(URL, body_text="")
    assert set(fallback["drift"]["items"]) <= set(definition["drift_checks"])


@pytest.mark.parametrize("structured", [
    {"messages": [{"attachments": "bad"}]},
    {"messages": [], "unattributed_cache_candidates": "bad"},
    {"messages": [], "unattributed_cache_candidates": [1]},
])
def test_collection_shapes_are_validated(structured):
    with pytest.raises(ValueError, match="list"):
        build_capture(URL, structured=structured)


def test_manifest_is_metadata_only_and_rejects_absolute_paths(tmp_path: Path):
    capture = build_capture(URL, body_text="PRIVATE BODY")
    with pytest.raises(ValueError, match="relative"):
        build_manifest(capture, raw_json=str(tmp_path / "raw.json"))
    manifest = build_manifest(capture, raw_json="raw/id.json")
    assert "PRIVATE BODY" not in str(manifest)
    assert manifest["outbound_actions"] == "disabled"
    assert {"coverage", "freshness", "drift"} <= manifest.keys()
    assert manifest["adapter_version"] == "1"


def test_null_message_fields_remain_missing_and_trigger_drift():
    capture = build_capture(URL, structured={"messages": [{"body_text": None, "author_label": None, "visible_timestamp": None}]})
    assert capture["messages"][0]["body_text"] == ""
    assert "timestamp_or_author_or_body_missing_repeatedly" in capture["drift"]["items"]


def test_invalid_extraction_confidence_is_rejected_early():
    with pytest.raises(ValueError, match="extraction_confidence"):
        build_capture(URL, structured={"messages": [{"extraction_confidence": "PRIVATE"}]})


@pytest.mark.parametrize("value", [None, "2", 1.5, True])
def test_invalid_missing_field_streak_is_rejected(value):
    with pytest.raises(ValueError, match="missing_field_streak"):
        build_capture(URL, structured={"messages": [], "missing_field_streak": value})


@pytest.mark.parametrize("path", ["C:/Users/example/raw.json", r"\\server\share\raw.json"])
def test_manifest_rejects_windows_absolute_artifact_paths(path):
    from pathlib import PurePosixPath
    from unittest.mock import patch

    with patch("discord_context_bridge.site_adapter_runtime.PurePath", PurePosixPath):
        with pytest.raises(ValueError, match="relative"):
            build_manifest(build_capture(URL, body_text="x"), raw_json=path)


def test_manifest_schema_rejects_full_state_when_status_is_partial():
    manifest = build_manifest(build_capture(URL, body_text="x"), raw_json="raw/x.json")
    manifest["capture_state"] = "full"
    with pytest.raises(ValidationError):
        validate_artifact(manifest, "dcb_capture_manifest.v1.schema.json")


def test_packaged_resources_work_without_repository_root(tmp_path, monkeypatch):
    monkeypatch.setattr("discord_context_bridge.site_adapter_runtime.REPO_ROOT", tmp_path / "not-a-checkout")
    monkeypatch.setattr("discord_context_bridge.site_adapter_runtime.INSTALLED_RESOURCE_ROOT", Path(__file__).parents[1])
    assert load_adapter_definition()["adapter_id"] == "discord.v1"
    validate_artifact(build_capture(URL, body_text="x"), "dcb_raw_capture.v1.schema.json")
