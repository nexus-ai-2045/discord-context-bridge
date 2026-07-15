import json

import pytest
from unittest.mock import patch

from discord_context_bridge.cli import main
from discord_context_bridge.site_adapter_runtime import MAX_INPUT_BYTES


URL = "https://discord.com/channels/1/2/3"


def test_capture_visible_snapshot_fallback_is_public_safe(tmp_path, capsys):
    source = tmp_path / "visible.txt"
    source.write_text("Alice\nPRIVATE BODY", encoding="utf-8")
    rc = main(["capture-visible-snapshot", "--source-url", URL, "--input", str(source), "--output-root", str(tmp_path / ".local"), "--json"])
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert rc == 0
    assert payload["outbound_actions"] == "disabled"
    assert "PRIVATE BODY" not in output
    assert "Alice" not in output
    assert URL not in output
    assert str(tmp_path) not in output


def test_capture_visible_snapshot_structured_and_url_mismatch(tmp_path, capsys):
    source = tmp_path / "structured.json"
    source.write_text(json.dumps({"messages": [{"body_text": "PRIVATE"}]}), encoding="utf-8")
    assert main(["capture-visible-snapshot", "--source-url", URL, "--structured-input", str(source), "--output-root", str(tmp_path / ".local"), "--json"]) == 0
    capsys.readouterr()
    assert main(["capture-visible-snapshot", "--source-url", "https://example.com", "--input", str(source), "--output-root", str(tmp_path / ".local"), "--json"]) == 2
    blocked = capsys.readouterr().out
    assert "example.com" not in blocked and str(tmp_path) not in blocked


def test_malformed_structured_shape_returns_safe_blocked(tmp_path, capsys):
    source = tmp_path / "bad.json"
    source.write_text('{"messages":"PRIVATE BODY"}', encoding="utf-8")
    assert main(["capture-visible-snapshot", "--source-url", URL, "--structured-input", str(source), "--output-root", str(tmp_path / ".local"), "--json"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["capture_state"] == "blocked"
    assert "PRIVATE BODY" not in output and str(tmp_path) not in output


def test_structured_top_level_array_returns_safe_blocked(tmp_path, capsys):
    source = tmp_path / "array.json"
    source.write_text('["PRIVATE BODY"]', encoding="utf-8")
    assert main(["capture-visible-snapshot", "--source-url", URL, "--structured-input", str(source), "--output-root", str(tmp_path / ".local"), "--json"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["capture_state"] == "blocked"
    assert "PRIVATE BODY" not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err


def test_oversized_input_is_blocked_before_reading_and_stays_public_safe(tmp_path, capsys):
    source = tmp_path / "oversized.txt"
    source.write_bytes(b"x" * (MAX_INPUT_BYTES + 1))

    assert main(["capture-visible-snapshot", "--source-url", URL, "--input", str(source), "--output-root", str(tmp_path / ".local"), "--json"]) == 2

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["failure_stage"] == "input_validation"
    assert str(tmp_path) not in captured.out + captured.err


def test_private_persistence_failure_returns_nonzero(tmp_path, capsys):
    source = tmp_path / "input.txt"
    source.write_text("visible", encoding="utf-8")
    blocked = {"capture_state": "blocked", "status": "blocked", "failure_stage": "private_persistence"}
    with patch("discord_context_bridge.cli.store_capture", return_value=blocked):
        assert main(["capture-visible-snapshot", "--source-url", URL, "--input", str(source), "--output-root", str(tmp_path / ".local"), "--json"]) == 2
