import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_discord_site_adapter_declares_read_only_boundary():
    adapter = load_json("site_adapters/discord.v1.json")

    assert adapter["schema"] == "dcb.site_adapter.v1"
    assert adapter["adapter_id"] == "discord.v1"
    assert adapter["read_only"] is True
    assert adapter["outbound_actions"] == "disabled"
    assert adapter["safety"]["external_share_allowed"] is False
    assert adapter["safety"]["human_review_required_before_publication"] is True


def test_discord_site_adapter_keeps_dom_snapshot_fallback_order_explicit():
    adapter = load_json("site_adapters/discord.v1.json")
    methods = [item["method"] for item in adapter["capture_order"]]

    assert methods == [
        "structured_dom",
        "body_inner_text",
        "message_container_text",
    ]
    assert "message_container_zero_hits" in adapter["drift_checks"]
    assert "structured_dom_empty_but_body_text_present" in adapter["drift_checks"]


def test_site_adapter_index_points_to_discord_v1():
    index = load_json("site_adapters/index.json")

    assert index["schema"] == "dcb.site_adapters.index.v1"
    assert index["adapters"][0]["adapter_id"] == "discord.v1"
    assert index["adapters"][0]["outbound_actions"] == "disabled"


def test_site_adapter_storage_schemas_are_present_and_private_by_default():
    adapter = load_json("site_adapters/discord.v1.json")
    raw_schema = load_json(adapter["storage"]["raw_json_schema"])
    manifest_schema = load_json(adapter["storage"]["manifest_schema"])

    assert raw_schema["properties"]["safety"]["properties"]["outbound_actions"]["const"] == "disabled"
    assert raw_schema["properties"]["safety"]["properties"]["external_share_allowed"]["const"] is False
    assert manifest_schema["properties"]["review_required"]["const"] is True
    assert manifest_schema["properties"]["outbound_actions"]["const"] == "disabled"
