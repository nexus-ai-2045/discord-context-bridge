from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


API_BASE = "https://discord.com/api/v10"
LIVE_VERIFICATION_RECEIPT = "live-verification.json"
LIVE_VERIFICATION_SCHEMA = "dcb.discord-bot-live-verification.v2"
MAX_RECEIPT_BYTES = 64 * 1024
MAX_RECEIPT_VALIDITY = timedelta(hours=24)
SUPPORTED_TARGET_CHANNEL_TYPES = frozenset({0, 5, 10, 11, 12, 15, 16})
MESSAGE_HISTORY_CHANNEL_TYPES = frozenset({0, 5, 10, 11, 12})
CHANNEL_TYPE_CLASS = {
    0: "text",
    5: "announcement",
    10: "announcement_thread",
    11: "public_thread",
    12: "private_thread",
    15: "forum",
    16: "media",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TARGET_URL_RE = re.compile(
    r"^https://discord\.com/channels/(?P<guild>\d{1,20})/(?P<channel>\d{1,20})"
    r"(?:/threads/(?P<thread>\d{1,20}))?(?:/(?P<message>\d{1,20}))?/?(?:[?#].*)?$"
)


class LiveVerificationError(ValueError):
    """live verificationを安全なreason codeで停止する。"""


def normalize_expected_target(url: str) -> dict[str, str]:
    """Discord URLを対象guild/channelへ正規化する。"""

    match = _TARGET_URL_RE.fullmatch(url.strip())
    if not match:
        raise LiveVerificationError("expected_target_url_invalid")
    return {
        "guild_id": match.group("guild"),
        "channel_id": match.group("thread") or match.group("channel"),
    }


def credential_binding_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def target_binding_sha256(target: Mapping[str, str], channel_type: int) -> str:
    canonical = json.dumps(
        {
            "channel_id": target["channel_id"],
            "channel_type": channel_type,
            "guild_id": target["guild_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _signed_fields(payload: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "receipt_mac_sha256"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _receipt_mac(token: str, payload: Mapping[str, Any]) -> str:
    return hmac.new(token.encode("utf-8"), _signed_fields(payload), hashlib.sha256).hexdigest()


def _request_json(route: str, token: str) -> Any:
    request = urllib.request.Request(
        API_BASE + route,
        headers={
            "Author" + "ization": f"Bot {token}",
            "Accept": "application/json",
            "User-Agent": "discord-context-bridge-readonly/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise LiveVerificationError("rate_limited_retryable") from error
        if error.code in {401, 403}:
            raise LiveVerificationError("permission_denied") from error
        if error.code == 404:
            raise LiveVerificationError("source_not_found") from error
        raise LiveVerificationError("live_verification_api_failed") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LiveVerificationError("live_verification_api_failed") from error


def verify_live_target(
    *,
    expected_url: str,
    token: str,
    fetch_json: Callable[[str, str], Any] = _request_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    """同一processの3つのGETで対象accessを実測する。"""

    target = normalize_expected_target(expected_url)
    identity = fetch_json("/users/@me", token)
    if (
        not isinstance(identity, Mapping)
        or identity.get("bot") is not True
        or not isinstance(identity.get("id"), str)
        or not identity["id"]
    ):
        raise LiveVerificationError("bot_identity_not_verified")
    guild_id = urllib.parse.quote(target["guild_id"], safe="")
    guild = fetch_json(f"/guilds/{guild_id}", token)
    if not isinstance(guild, Mapping) or guild.get("id") != target["guild_id"]:
        raise LiveVerificationError("target_guild_membership_not_verified")
    channel_id = urllib.parse.quote(target["channel_id"], safe="")
    channel = fetch_json(f"/channels/{channel_id}", token)
    if (
        not isinstance(channel, Mapping)
        or channel.get("id") != target["channel_id"]
        or channel.get("guild_id") != target["guild_id"]
        or not isinstance(channel.get("type"), int)
        or isinstance(channel.get("type"), bool)
    ):
        raise LiveVerificationError("target_channel_access_not_verified")
    if channel["type"] not in SUPPORTED_TARGET_CHANNEL_TYPES:
        raise LiveVerificationError("target_channel_type_not_supported")
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload: dict[str, Any] = {
        "schema": LIVE_VERIFICATION_SCHEMA,
        "verified_at": observed.isoformat(),
        "expires_at": (observed + timedelta(hours=1)).isoformat(),
        "bot_identity_verified": True,
        "target_guild_membership_verified": True,
        "target_channel_access_verified": True,
        "bot_identity_binding_sha256": hashlib.sha256(
            identity["id"].encode("utf-8")
        ).hexdigest(),
        "credential_binding_sha256": credential_binding_sha256(token),
        "target_binding_sha256": target_binding_sha256(target, channel["type"]),
        "target_channel_type": channel["type"],
        "request_methods": ["GET", "GET", "GET"],
        "outbound_actions": "disabled",
    }
    payload["receipt_mac_sha256"] = _receipt_mac(token, payload)
    return payload


def create_live_verification_receipt(
    *,
    expected_url: str,
    token: str,
    fetch_json: Callable[[str, str], Any] = _request_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    """互換用。監査cacheに保存できるlive実測結果を作る。"""

    return verify_live_target(
        expected_url=expected_url, token=token, fetch_json=fetch_json, now=now
    )


def _write_private_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    """署名済みreceiptをmode 0600でatomic保存する。"""

    if payload.get("schema") != LIVE_VERIFICATION_SCHEMA:
        raise LiveVerificationError("live_verification_receipt_invalid")
    encoded = (json.dumps(dict(payload), sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise LiveVerificationError("live_verification_receipt_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def produce_live_verification_receipt(
    *,
    expected_url: str,
    token: str,
    path: Path,
    fetch_json: Callable[[str, str], Any] = _request_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    """live実測とatomic保存を一体化した唯一producer。"""

    payload = verify_live_target(
        expected_url=expected_url,
        token=token,
        fetch_json=fetch_json,
        now=now,
    )
    _write_private_receipt(path, payload)
    return payload


def read_private_receipt(path: Path) -> tuple[dict[str, Any] | None, str]:
    """symlinkを追わずprivate receiptをbounded読取する。"""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return None, "invalid_file"
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600:
        return None, "mode_0600_required"
    if metadata.st_size > MAX_RECEIPT_BYTES:
        return None, "too_large"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            current = os.fstat(handle.fileno())
            if not stat.S_ISREG(current.st_mode):
                return None, "invalid_file"
            if os.name != "nt" and stat.S_IMODE(current.st_mode) != 0o600:
                return None, "mode_0600_required"
            text = handle.read(MAX_RECEIPT_BYTES + 1)
    except (OSError, UnicodeError):
        return None, "invalid"
    if len(text.encode("utf-8")) > MAX_RECEIPT_BYTES:
        return None, "too_large"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "invalid"
    return (payload, "loaded") if isinstance(payload, dict) else (None, "invalid")


def verify_saved_receipt(
    *,
    payload: Mapping[str, Any],
    expected_url: str,
    token: str,
    now: datetime | None = None,
) -> str:
    """保存receiptを現在credentialとexpected targetへ再結合する。"""

    if payload.get("schema") != LIVE_VERIFICATION_SCHEMA:
        return "invalid"
    if any(
        payload.get(field) is not True
        for field in (
            "bot_identity_verified",
            "target_guild_membership_verified",
            "target_channel_access_verified",
        )
    ):
        return "verification_incomplete"
    if payload.get("request_methods") != ["GET", "GET", "GET"]:
        return "request_method_evidence_invalid"
    channel_type = payload.get("target_channel_type")
    if not isinstance(channel_type, int) or isinstance(channel_type, bool):
        return "target_binding_invalid"
    if channel_type not in SUPPORTED_TARGET_CHANNEL_TYPES:
        return "target_channel_type_not_supported"
    try:
        target = normalize_expected_target(expected_url)
    except LiveVerificationError:
        return "expected_target_url_invalid"
    expected_target = target_binding_sha256(target, channel_type)
    if not hmac.compare_digest(
        str(payload.get("target_binding_sha256") or ""), expected_target
    ):
        return "target_mismatch"
    expected_credential = credential_binding_sha256(token)
    if not hmac.compare_digest(
        str(payload.get("credential_binding_sha256") or ""), expected_credential
    ):
        return "credential_mismatch"
    for field in ("bot_identity_binding_sha256", "receipt_mac_sha256"):
        value = payload.get(field)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            return "signature_invalid"
    expected_mac = _receipt_mac(token, payload)
    if not hmac.compare_digest(payload["receipt_mac_sha256"], expected_mac):
        return "signature_invalid"
    verified_at = payload.get("verified_at")
    expires_at = payload.get("expires_at")
    if not isinstance(verified_at, str) or not isinstance(expires_at, str):
        return "expiry_invalid"
    try:
        observed = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return "expiry_invalid"
    if observed.tzinfo is None or expiry.tzinfo is None:
        return "expiry_invalid"
    observed = observed.astimezone(timezone.utc)
    expiry = expiry.astimezone(timezone.utc)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expiry <= current:
        return "expired"
    if observed > current or expiry <= observed or expiry - observed > MAX_RECEIPT_VALIDITY:
        return "validity_window_invalid"
    return "verified"
