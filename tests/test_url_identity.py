from discord_context_bridge.url_identity import classify_discord_url
from discord_context_bridge.cli import main as cli_main


def test_two_id_url_is_structurally_ambiguous_until_resolved():
    result = classify_discord_url("https://discord.com/channels/1/20")

    assert result["valid"] is True
    assert result["structural_shape"] == "guild_channel_target"
    assert result["resolved_kind"] == "unknown"
    assert result["resolution"]["state"] == "unresolved"
    assert result["ids"]["channel_id_present"] is True
    assert "20" not in str(result)


def test_resolver_evidence_can_identify_forum_parent_without_changing_shape():
    result = classify_discord_url(
        "https://discord.com/channels/1/20",
        resolved_kind="forum_parent",
        evidence_source="discord_rest_api",
        evidence_observed_at="2026-07-28T10:00:00+09:00",
    )

    assert result["structural_shape"] == "guild_channel_target"
    assert result["resolved_kind"] == "forum_parent"
    assert result["resolution"]["state"] == "resolved"
    assert result["resolution"]["evidence_source"] == "discord_rest_api"


def test_three_id_url_is_structurally_nested_but_not_assumed_thread():
    result = classify_discord_url("https://discord.com/channels/1/20/30")

    assert result["structural_shape"] == "nested_target"
    assert result["resolved_kind"] == "unknown"
    assert result["ids"]["nested_id_present"] is True


def test_message_resolution_requires_four_id_shape():
    result = classify_discord_url(
        "https://discord.com/channels/1/20",
        resolved_kind="message",
        evidence_source="manual",
        evidence_observed_at="2026-07-28T10:00:00+09:00",
    )

    assert result["valid"] is False
    assert "resolved_kind_shape_mismatch" in result["blockers"]


def test_cli_classification_does_not_echo_url(capsys):
    url = "https://discord.com/channels/1/20"
    exit_code = cli_main(["classify-discord-url", "--url", url, "--json"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert url not in output
    assert '"structural_shape": "guild_channel_target"' in output
