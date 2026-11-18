"""Discord URL の構造と、外部証拠による解決種別を分離する。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit
from typing import Any


_URL_RE = re.compile(
    r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/"
    r"([^/?#]+)(?:/([^/?#]+))?(?:/([^/?#]+))?(?:/([^/?#]+))?/?(?:[?#].*)?$"
)
_KINDS = {"unknown", "channel", "forum_parent", "thread", "message"}


def parse_guild_channel_url(url: str) -> tuple[str, str] | None:
    """共有host契約で親棚卸し用のguild/channelを抽出する。種別は別途検証する。"""
    match = _URL_RE.fullmatch(url.strip())
    if match is None:
        return None
    guild, channel, nested, message = match.groups()
    if nested is not None or message is not None:
        return None
    if any(value is None or re.fullmatch(r"[0-9]+", value) is None for value in (guild, channel)):
        return None
    return guild, channel


def parse_effective_channel_url(url: str) -> tuple[str, str] | None:
    channel_identity = parse_guild_channel_url(url)
    if channel_identity is not None:
        return channel_identity
    match = _URL_RE.fullmatch(url.strip())
    if match is None:
        return None
    guild, parent, marker, thread = match.groups()
    if marker != "threads" or any(
        value is None or re.fullmatch(r"[0-9]+", value) is None
        for value in (guild, parent, thread)
    ):
        return None
    return guild, thread


def parse_discord_target_url(url: str) -> tuple[str, ...] | None:
    """Return the exact target path using the shared Discord host/path parser.

    Preserve message/nested IDs; a channel-only observation does not establish
    a requested message. Explicit /threads/ paths share effective channel IDs.
    """
    match = _URL_RE.fullmatch(url.strip())
    if match is None:
        # Explicit thread-message URLs append a message to the existing
        # /guild/parent/threads/thread route. Reuse that parser for host and
        # thread identity validation rather than adding another host policy.
        try:
            parsed = urlsplit(url.strip())
        except ValueError:
            return None
        path_parts = parsed.path.rstrip("/").split("/")
        if len(path_parts) != 7 or path_parts[1] != "channels":
            return None
        message = path_parts[-1]
        if re.fullmatch(r"[0-9]+", message) is None:
            return None
        thread_url = urlunsplit((parsed.scheme, parsed.netloc, "/".join(path_parts[:-1]), "", ""))
        effective = parse_effective_channel_url(thread_url)
        return (*effective, message) if effective is not None else None
    parts = tuple(part for part in match.groups() if part is not None)
    if len(parts) < 2:
        return None
    if len(parts) == 4 and parts[2] == "threads":
        effective = parse_effective_channel_url(url)
        return effective
    if parts[0] != "@me" and re.fullmatch(r"[0-9]+", parts[0]) is None:
        return None
    if any(re.fullmatch(r"[0-9]+", part) is None for part in parts[1:]):
        return None
    return parts


def classify_discord_url(
    url: str,
    *,
    resolved_kind: str = "unknown",
    evidence_source: str = "",
    evidence_observed_at: str = "",
) -> dict[str, Any]:
    """URL path の形だけで Discord object type を断定しない分類を返す。

    Discord の 2-ID URL は通常 channel を指すが、その channel が text channel
    なのか forum parent なのかは URL 文字列だけでは分からない。3-ID URL も、
    parent/thread の関係を API/DOM/保存済みメタデータで解決するまで nested target
    として扱う。
    """

    normalized_kind = resolved_kind.strip().lower() or "unknown"
    match = _URL_RE.fullmatch(url.strip())
    blockers: list[str] = []
    if not match:
        return {
            "language": "ja",
            "schema": "discord_url_identity.v1",
            "valid": False,
            "structural_shape": "invalid",
            "resolved_kind": "unknown",
            "resolution": {"state": "unresolved", "evidence_source": "none"},
            "ids": {
                "guild_id_present": False,
                "channel_id_present": False,
                "nested_id_present": False,
                "message_id_present": False,
            },
            "blockers": ["discord_channel_url_required"],
            "url_output": "omitted",
        }

    parts = match.groups()
    count = sum(part is not None for part in parts)
    shape = {
        1: "guild_only",
        2: "guild_channel_target",
        3: "nested_target",
        4: "nested_message_target",
    }.get(count, "invalid")
    if count < 2:
        blockers.append("discord_channel_url_required")
    if normalized_kind not in _KINDS:
        blockers.append("unsupported_resolved_kind")
        normalized_kind = "unknown"
    expected_counts = {
        "channel": {2},
        "forum_parent": {2},
        "thread": {3},
        # Normal Discord message URLs are 3-ID (/guild/channel/message).
        # 4-ID remains accepted for nested/message targets when evidence resolves them.
        "message": {3, 4},
        "unknown": {1, 2, 3, 4},
    }
    if count not in expected_counts[normalized_kind]:
        blockers.append("resolved_kind_shape_mismatch")
    if normalized_kind != "unknown":
        if not evidence_source.strip():
            blockers.append("resolution_evidence_missing")
        if not evidence_observed_at.strip():
            blockers.append("resolution_observed_at_missing")

    resolved = normalized_kind != "unknown" and not blockers
    return {
        "language": "ja",
        "schema": "discord_url_identity.v1",
        "valid": count >= 2 and not blockers,
        "structural_shape": shape,
        "resolved_kind": normalized_kind if resolved else "unknown",
        "resolution": {
            "state": "resolved" if resolved else "unresolved",
            "evidence_source": evidence_source.strip() if resolved else "none",
            "evidence_observed_at": evidence_observed_at.strip() if resolved else "",
        },
        "ids": {
            "guild_id_present": count >= 1,
            "channel_id_present": count >= 2,
            "nested_id_present": count >= 3,
            "message_id_present": count >= 4,
        },
        "blockers": blockers,
        "url_output": "omitted",
    }
