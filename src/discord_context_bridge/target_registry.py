"""ADR-0162 Phase 1: target 台帳モジュール。

target_key と url / server label / channel label / alias の対応を
append-only NDJSON に記録する。既存 `text-snapshots.ndjson` と同じ
append-only ledger 原則 (`docs/operating-contract.md` 参照) に従い、
既存 event を書き換えず、新しい事実は補完 event として追記する。

この台帳は url を含む可能性があるため local-private ファイルとして扱う。
CLI など外部出力向けには `safe_target_label` を使い、url を出さない。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .site_adapter_runtime import validate_artifact

DEFAULT_TARGET_REGISTRY_STORE = Path(".local/discord-context-bridge/targets.ndjson")

TARGET_REGISTRY_ENTRY_SCHEMA_NAME = "dcb_target_registry_entry.v1.schema.json"

VALID_KEY_SCHEMES = {
    "url_hash_16",
    "title_fallback_16",
    "content_hash_24",
    "source_url_hash_64",
    # url/title 無し時の本文由来 fallback identity 専用 (M3)。title_fallback_16
    # とは導出元が異なるため区別する (ingest.py の `_target_identity` 参照)。
    "content_fallback_16",
}
VALID_SOURCES = {"capture", "backfill", "manual"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_target_registry(path: Path = DEFAULT_TARGET_REGISTRY_STORE) -> list[dict[str, Any]]:
    """台帳の全 event 行をそのまま読む (append-only, 未畳み込み)。"""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    # `str.splitlines()` は U+2028/U+2029/U+0085 等の Unicode 行区切りも分割対象にし、
    # label 等にそれらを含む ledger 行を途中で分断する (json.dumps ensure_ascii=False
    # はこれらをエスケープしない)。NDJSON は "\n" 区切りの契約なので "\n" だけで区切る。
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.rstrip("\r")
        if line.strip():
            entries.append(dict(json.loads(line)))
    return entries


def _append(entry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def resolve_target(
    target_key: str, path: Path = DEFAULT_TARGET_REGISTRY_STORE
) -> dict[str, Any] | None:
    """target_key の最新状態へ event を畳み込む。

    aliases は全 event の union。url / server_label / channel_label は、
    非空の値を持つ最後の event を優先する (補完 event による上書きを許す)。
    """
    matching = [entry for entry in load_target_registry(path) if entry.get("target_key") == target_key]
    if not matching:
        return None

    resolved: dict[str, Any] = {}
    aliases: list[str] = []
    for entry in matching:
        for key in (
            "schema",
            "target_key",
            "key_scheme",
            "url",
            "server_label",
            "channel_label",
            "source",
            "source_ref",
        ):
            value = entry.get(key)
            if value:
                resolved[key] = value
        for alias in entry.get("aliases") or []:
            if alias and alias not in aliases:
                aliases.append(alias)
        if "first_seen" not in resolved and entry.get("first_seen"):
            resolved["first_seen"] = entry.get("first_seen")

    resolved["aliases"] = aliases
    resolved.setdefault("schema", "dcb.target_registry_entry.v1")
    resolved.setdefault("target_key", target_key)
    resolved.setdefault("url", None)
    resolved.setdefault("server_label", None)
    resolved.setdefault("channel_label", None)
    resolved.setdefault("source_ref", None)
    return resolved


def register_target(
    *,
    target_key: str,
    key_scheme: str,
    url: str | None = None,
    server_label: str | None = None,
    channel_label: str | None = None,
    aliases: Iterable[str] | None = None,
    source: str = "capture",
    source_ref: str | None = None,
    path: Path = DEFAULT_TARGET_REGISTRY_STORE,
    now: str | None = None,
) -> dict[str, Any]:
    """target_key を台帳へ登録する。

    既存事実は上書きせず追記する。同一 target_key + url が既に登録済みで、
    新しい alias / label 情報がない場合は無意味な重複追記を skip する。
    """
    if key_scheme not in VALID_KEY_SCHEMES:
        raise ValueError(f"unknown key_scheme: {key_scheme!r}")
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown source: {source!r}")

    alias_list = [alias for alias in (aliases or []) if alias]
    existing = resolve_target(target_key, path)
    if existing:
        new_aliases = [alias for alias in alias_list if alias not in (existing.get("aliases") or [])]
        # URL 省略 (url が falsy) は「更新なし」として扱う。呼び出し側が非空の
        # 新しい url を渡した時だけ「URL 変更」と判定する (既存 url を上書きする
        # 意図がない限り、null-URL の補完行を無駄に追記しない)。
        stripped_url = url.strip() if isinstance(url, str) else url
        url_changed = bool(stripped_url) and stripped_url != (existing.get("url") or None)
        has_new_label = bool(
            (server_label and server_label != existing.get("server_label"))
            or (channel_label and channel_label != existing.get("channel_label"))
        )
        if not url_changed and not new_aliases and not has_new_label:
            return {"registered": False, "reason": "already_registered", "target_key": target_key}

    entry = {
        "schema": "dcb.target_registry_entry.v1",
        "target_key": target_key,
        "key_scheme": key_scheme,
        "url": url or None,
        "server_label": server_label or None,
        "channel_label": channel_label or None,
        "aliases": alias_list,
        "first_seen": now or _utc_now(),
        "source": source,
        "source_ref": source_ref or None,
    }
    # append 前に schema validate する (M8)。append-only ledger へ壊れた行を
    # 書き込む前に fail-closed で止める。
    validate_artifact(entry, TARGET_REGISTRY_ENTRY_SCHEMA_NAME)
    _append(entry, path)
    return {"registered": True, "target_key": target_key}


def safe_target_label(target_key: str, path: Path = DEFAULT_TARGET_REGISTRY_STORE) -> str:
    """CLI 出力用途: url を含まない safe label だけを返す。"""
    resolved = resolve_target(target_key, path)
    if not resolved:
        return target_key
    label = resolved.get("channel_label") or resolved.get("server_label")
    if label:
        return str(label)
    return target_key
