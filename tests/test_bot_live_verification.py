from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import stat
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from discord_context_bridge.credentials import BOT_TOKEN_ENV, BotTokenLoadResult
from discord_context_bridge.live_verification import (
    LIVE_VERIFICATION_RECEIPT,
    LiveVerificationError,
    SUPPORTED_TARGET_CHANNEL_TYPES,
    _request_json,
    credential_binding_sha256,
    normalize_expected_target,
    produce_live_verification_receipt,
    read_private_receipt,
    target_binding_sha256,
    verify_live_target,
    verify_saved_receipt,
)


EXPECTED_URL = "https://discord.com/channels/1/2"


def _load_script(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fetch(route: str, token: str):
    assert token == "private-token"
    return {
        "/users/@me": {"id": "bot-private", "bot": True},
        "/guilds/1": {"id": "1"},
        "/channels/2": {"id": "2", "guild_id": "1", "type": 0},
    }[route]


def _fetch_with_channel_type(channel_type: int):
    def fetch(route: str, token: str):
        payload = _fetch(route, token)
        if route == "/channels/2":
            return {**payload, "type": channel_type}
        return payload

    return fetch


def _write_channel_token(channel_dir: Path) -> None:
    channel_dir.mkdir()
    env = channel_dir / ".env"
    env.write_text(f"{BOT_TOKEN_ENV}=private-token\n", encoding="utf-8")
    env.chmod(0o600)
    (channel_dir / "access.json").write_text(
        '{"dmPolicy":"allowlist","allowFrom":["safe"]}', encoding="utf-8"
    )


@pytest.mark.parametrize("without_fchmod", [False, True])
def test_unique_producer_and_consumer_bind_current_target(
    tmp_path: Path, monkeypatch, without_fchmod: bool,
) -> None:
    if without_fchmod:
        monkeypatch.delattr(os, "fchmod", raising=False)
    preflight = _load_script("discord_bot_route_preflight")
    channel_dir = tmp_path / "discord"
    _write_channel_token(channel_dir)
    receipt = channel_dir / LIVE_VERIFICATION_RECEIPT
    produce_live_verification_receipt(
        expected_url=EXPECTED_URL,
        token="private-token",
        path=receipt,
        fetch_json=_fetch,
        now=datetime.now(timezone.utc),
    )
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv("DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND", raising=False)

    result = preflight.build_preflight(channel_dir, expected_url=EXPECTED_URL)

    assert result["ok"] is True
    assert result["live_verification"]["status"] == "verified"
    if os.name != "nt":
        assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    rendered = json.dumps(result)
    stored = json.loads(receipt.read_text(encoding="utf-8"))
    for field in (
        "bot_identity_binding_sha256",
        "credential_binding_sha256",
        "target_binding_sha256",
        "receipt_mac_sha256",
    ):
        assert stored[field] not in rendered


def test_consumer_rejects_other_target_and_missing_target(tmp_path: Path, monkeypatch) -> None:
    preflight = _load_script("discord_bot_route_preflight")
    channel_dir = tmp_path / "discord"
    _write_channel_token(channel_dir)
    produce_live_verification_receipt(
        expected_url=EXPECTED_URL,
        token="private-token",
        path=channel_dir / LIVE_VERIFICATION_RECEIPT,
        fetch_json=_fetch,
    )
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv("DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND", raising=False)

    other = preflight.build_preflight(
        channel_dir, expected_url="https://discord.com/channels/1/3"
    )
    unspecified = preflight.build_preflight(channel_dir)

    assert other["ok"] is False
    assert other["live_verification"]["status"] == "target_mismatch"
    assert unspecified["ok"] is False
    assert unspecified["live_verification"]["status"] == "target_not_specified"


def test_consumer_rejects_handwritten_unsigned_receipt(tmp_path: Path, monkeypatch) -> None:
    preflight = _load_script("discord_bot_route_preflight")
    channel_dir = tmp_path / "discord"
    _write_channel_token(channel_dir)
    receipt = channel_dir / LIVE_VERIFICATION_RECEIPT
    receipt.write_text(
        json.dumps(
            {
                "schema": "dcb.discord-bot-live-verification.v2",
                "bot_identity_verified": True,
                "target_guild_membership_verified": True,
                "target_channel_access_verified": True,
                "request_methods": ["GET", "GET", "GET"],
                "target_channel_type": 0,
                "target_binding_sha256": target_binding_sha256(
                    normalize_expected_target(EXPECTED_URL), 0
                ),
                "credential_binding_sha256": credential_binding_sha256(
                    "private-token"
                ),
                "bot_identity_binding_sha256": hashlib.sha256(
                    b"bot-private"
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv("DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND", raising=False)

    result = preflight.build_preflight(channel_dir, expected_url=EXPECTED_URL)

    assert result["ok"] is False
    assert result["live_verification"]["status"] == "signature_invalid"


def test_live_producer_api_denial_does_not_write_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / LIVE_VERIFICATION_RECEIPT

    def denied(_route: str, _token: str):
        raise LiveVerificationError("permission_denied")

    with pytest.raises(LiveVerificationError, match="permission_denied"):
        produce_live_verification_receipt(
            expected_url=EXPECTED_URL,
            token="private-token",
            path=receipt,
            fetch_json=denied,
        )
    assert not receipt.exists()


@pytest.mark.parametrize("channel_type", sorted(SUPPORTED_TARGET_CHANNEL_TYPES))
def test_live_producer_accepts_canonical_supported_channel_types(
    tmp_path: Path, channel_type: int
) -> None:
    payload = produce_live_verification_receipt(
        expected_url=EXPECTED_URL,
        token="private-token",
        path=tmp_path / f"receipt-{channel_type}.json",
        fetch_json=_fetch_with_channel_type(channel_type),
    )

    assert payload["target_channel_type"] == channel_type


@pytest.mark.parametrize("channel_type", [2, 4, 13, 14])
def test_live_producer_rejects_unsupported_channel_types(
    tmp_path: Path, channel_type: int
) -> None:
    receipt = tmp_path / "receipt.json"

    with pytest.raises(LiveVerificationError, match="target_channel_type_not_supported"):
        produce_live_verification_receipt(
            expected_url=EXPECTED_URL,
            token="private-token",
            path=receipt,
            fetch_json=_fetch_with_channel_type(channel_type),
        )

    assert not receipt.exists()


def test_live_consumer_rejects_unsupported_channel_type() -> None:
    payload = verify_live_target(
        expected_url=EXPECTED_URL,
        token="private-token",
        fetch_json=_fetch,
    )
    payload["target_channel_type"] = 2

    assert verify_saved_receipt(
        payload=payload,
        expected_url=EXPECTED_URL,
        token="private-token",
    ) == "target_channel_type_not_supported"


def test_http_permission_denied_is_safe_reason(monkeypatch) -> None:
    def denied(_request, timeout):
        raise urllib.error.HTTPError("private-url", 403, "forbidden", {}, io.BytesIO())

    monkeypatch.setattr(urllib.request, "urlopen", denied)

    with pytest.raises(LiveVerificationError, match="permission_denied"):
        _request_json("/users/@me", "private-token")


@pytest.mark.parametrize("without_fchmod", [False, True])
def test_producer_cli_outputs_only_safe_metadata(
    tmp_path: Path, monkeypatch, capsys, without_fchmod: bool,
) -> None:
    if without_fchmod:
        monkeypatch.delattr(os, "fchmod", raising=False)
    producer = _load_script("discord_bot_live_verify")
    channel_dir = tmp_path / "discord"
    channel_dir.mkdir()
    monkeypatch.setattr(
        producer,
        "load_bot_token_from_provider",
        lambda **_kwargs: BotTokenLoadResult(
            ok=True, provider="test", token="private-token"
        ),
    )
    monkeypatch.setattr(
        producer,
        "produce_live_verification_receipt",
        lambda **kwargs: produce_live_verification_receipt(
            **kwargs, fetch_json=_fetch
        ),
    )

    exit_code = producer.main(
        [
            "--expected-url",
            EXPECTED_URL,
            "--channel-dir",
            str(channel_dir),
            "--json",
        ]
    )
    visible = capsys.readouterr().out
    report = json.loads(visible)

    assert exit_code == 0
    assert report["status"] == "live_verified"
    assert report["request_method"] == "GET_only"
    assert "private-token" not in visible
    assert EXPECTED_URL not in visible
    assert "/users/@me" not in visible
    receipt, read_status = read_private_receipt(channel_dir / LIVE_VERIFICATION_RECEIPT)
    assert read_status == "loaded"
    assert receipt is not None
    assert verify_saved_receipt(
        payload=receipt, expected_url=EXPECTED_URL, token="private-token",
    ) == "verified"


@pytest.mark.parametrize("first_token", ["private-token", "rotated-token"])
def test_backfill_verifies_exact_credential_used_for_fetch(
    tmp_path: Path, monkeypatch, capsys, first_token: str
) -> None:
    preflight = _load_script("discord_bot_route_preflight")
    backfill = _load_script("discord_rest_backfill")
    channel_dir = tmp_path / "discord"
    _write_channel_token(channel_dir)
    produce_live_verification_receipt(
        expected_url=EXPECTED_URL,
        token="private-token",
        path=channel_dir / LIVE_VERIFICATION_RECEIPT,
        fetch_json=_fetch,
    )
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.delenv("DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND", raising=False)
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", str(tmp_path))
    credentials = iter([first_token, "another-token"])
    loads = []
    fetched = []

    def load(**kwargs):
        token = next(credentials)
        loads.append(token)
        return BotTokenLoadResult(ok=True, provider="test", token=token)

    def fetch(**kwargs):
        fetched.append(kwargs["token"])
        return [], None

    monkeypatch.setattr(preflight, "load_bot_token_from_provider", load)
    monkeypatch.setattr(backfill, "load_bot_token_from_provider", load)
    monkeypatch.setattr(backfill, "fetch_discord_messages", fetch)

    assert backfill.main([
        "--url", EXPECTED_URL,
        "--channel-dir", str(channel_dir),
        "--raw-output", str(tmp_path / "raw.ndjson"),
        "--manifest-output", str(tmp_path / "manifest.json"),
    ]) == 2

    assert loads == [first_token]
    assert fetched == ([first_token] if first_token == "private-token" else [])
    assert first_token not in capsys.readouterr().out


@pytest.mark.parametrize("field", ["target_binding_sha256", "credential_binding_sha256"])
@pytest.mark.parametrize("value", ["日本語", [], None, 42])
def test_receipt_rejects_malformed_binding_without_raising(field, value) -> None:
    payload = verify_live_target(
        expected_url=EXPECTED_URL, token="private-token", fetch_json=_fetch,
    )
    payload[field] = value

    assert verify_saved_receipt(
        payload=payload, expected_url=EXPECTED_URL, token="private-token",
    ) != "verified"


@pytest.mark.parametrize(("route", "replacement", "reason"), [
    ("/users/@me", {"id": "user", "bot": False}, "bot_identity_not_verified"),
    ("/guilds/1", {"id": "other"}, "target_guild_membership_not_verified"),
    ("/channels/2", {"id": "2", "guild_id": "other", "type": 0}, "target_channel_access_not_verified"),
])
def test_producer_rejects_mismatched_identity_or_target(
    tmp_path: Path, route: str, replacement: dict, reason: str,
) -> None:
    receipt = tmp_path / LIVE_VERIFICATION_RECEIPT

    def fetch(current_route, token):
        return replacement if current_route == route else _fetch(current_route, token)

    with pytest.raises(LiveVerificationError, match=reason):
        produce_live_verification_receipt(
            expected_url=EXPECTED_URL, token="private-token", path=receipt,
            fetch_json=fetch,
        )

    assert not receipt.exists()


@pytest.mark.parametrize("host", [
    "discord.com", "canary.discord.com", "ptb.discord.com",
    "discordapp.com", "canary.discordapp.com", "ptb.discordapp.com",
])
@pytest.mark.parametrize("path", [
    "/channels/1/2",
    "/channels/1/2/3",
    "/channels/1/9/threads/2",
    "/channels/1/9/threads/2/3",
    "/channels/1/9/threads/2/3/?jump=latest#message",
])
def test_live_receipts_share_canonical_identity_across_host_aliases(host, path) -> None:
    target = f"https://{host}{path}"
    payload = verify_live_target(
        expected_url=target, token="private-token", fetch_json=_fetch,
    )
    canonical_payload = verify_live_target(
        expected_url=EXPECTED_URL, token="private-token", fetch_json=_fetch,
    )

    assert normalize_expected_target(target) == {"guild_id": "1", "channel_id": "2"}
    assert verify_saved_receipt(
        payload=payload, expected_url=EXPECTED_URL, token="private-token",
    ) == "verified"
    assert verify_saved_receipt(
        payload=canonical_payload, expected_url=target, token="private-token",
    ) == "verified"


@pytest.mark.parametrize("target", [
    "http://discord.com/channels/1/2",
    "https://www.discord.com/channels/1/2",
    "https://discord.com.evil.example/channels/1/2",
    "https://evil-discord.com/channels/1/2",
    "https://discordapp.com.evil.example/channels/1/2",
    "https://canary.discord.com.evil.example/channels/1/2",
    "https://user@discord.com/channels/1/2",
    "https://discord.com@evil.example/channels/1/2",
    "https://discord.com:443/channels/1/2",
    "https://discord.com./channels/1/2",
    "https://[discord.com/channels/1/2",
    "https://dis\ncord.com/channels/1/2",
    "https://discord.com/channels/１/2",
    "https://discord.com/channels/1/２",
    "https://discord.com/channels/1/2/３",
    "https://discord.com/channels/1/2/threads/３",
    "https://discord.com/channels/1/2/threads/3/４",
    "https://discord.com/channels/123456789012345678901/2",
    "https://discord.com/channels/1/123456789012345678901",
    "https://discord.com/channels/1/2/threads/123456789012345678901",
    "https://discord.com/channels/1/2/3/4",
    "https://discord.com/channels/1/2/threads/3/4/5",
    "https://discord.com/channels/@me/2",
])
def test_live_receipts_reject_unsupported_target_before_fetch(target) -> None:
    payload = verify_live_target(
        expected_url=EXPECTED_URL, token="private-token", fetch_json=_fetch,
    )

    def forbidden_fetch(*_args):
        pytest.fail("invalid target must not trigger any API request")

    with pytest.raises(LiveVerificationError, match="expected_target_url_invalid"):
        verify_live_target(
            expected_url=target, token="private-token", fetch_json=forbidden_fetch,
        )
    assert verify_saved_receipt(
        payload=payload, expected_url=target, token="private-token",
    ) == "expected_target_url_invalid"
