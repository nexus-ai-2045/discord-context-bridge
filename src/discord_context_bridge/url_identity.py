"""Discord URL の構造と、外部証拠による解決種別を分離する。"""

from __future__ import annotations

import re
from typing import Any


_URL_RE = re.compile(
    r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/"
    r"([^/?#]+)(?:/([^/?#]+))?(?:/([^/?#]+))?(?:/([^/?#]+))?/?(?:[?#].*)?$"
)
_KINDS = {"unknown", "channel", "forum_parent", "thread", "message"}


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
        "message": {4},
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
