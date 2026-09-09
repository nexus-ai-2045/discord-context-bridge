"""Discordのスレッド棚卸しを読取専用で行い、公開出力をメタデータに限定する。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


ARCHIVE_SCOPES = ("public", "private", "joined_private")
CANONICAL_SCOPE_ROUTES = {
    "active_filtered": "GET /guilds/{guild_id}/threads/active",
    "archived_public": "GET /channels/{channel_id}/threads/archived/public",
    "archived_private": "GET /channels/{channel_id}/threads/archived/private",
}
_MAX_RESPONSE_BYTES = 10_000_000


class ArchiveInventoryError(ValueError):
    """棚卸し証拠が不正、または終端へ収束しない場合の例外。"""


def _cursor_for(scope: str, thread: Mapping[str, Any]) -> str:
    if scope == "joined_private":
        value = thread.get("id")
    else:
        metadata = thread.get("thread_metadata")
        value = metadata.get("archive_timestamp") if isinstance(metadata, Mapping) else None
    if not isinstance(value, str) or not value:
        raise ArchiveInventoryError("archive_cursor_missing")
    return value


def enumerate_archive_pages(
    *,
    scope: str,
    fetch_page: Callable[[str | None], Mapping[str, Any]],
    max_pages: int,
) -> dict[str, Any]:
    """Discordの単一アーカイブscopeをcursor終端まで列挙する。"""

    if scope not in ARCHIVE_SCOPES:
        raise ArchiveInventoryError("archive_scope_invalid")
    if max_pages < 1:
        raise ArchiveInventoryError("archive_max_pages_invalid")

    cursor: str | None = None
    seen_cursors: set[str] = set()
    threads: dict[str, dict[str, Any]] = {}
    pages = 0
    exhausted = False

    while pages < max_pages:
        payload = fetch_page(cursor)
        if not isinstance(payload, Mapping):
            raise ArchiveInventoryError("archive_response_invalid")
        page_threads = payload.get("threads")
        has_more = payload.get("has_more")
        if not isinstance(page_threads, Sequence) or isinstance(page_threads, (str, bytes)):
            raise ArchiveInventoryError("archive_threads_invalid")
        if not isinstance(has_more, bool):
            raise ArchiveInventoryError("archive_has_more_invalid")

        normalized: list[dict[str, Any]] = []
        for item in page_threads:
            if not isinstance(item, Mapping):
                raise ArchiveInventoryError("archive_thread_invalid")
            thread_id = item.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise ArchiveInventoryError("archive_thread_id_missing")
            record = dict(item)
            threads[thread_id] = record
            normalized.append(record)
        pages += 1

        if not has_more:
            exhausted = True
            break
        if not normalized:
            raise ArchiveInventoryError("archive_pagination_empty_page")
        next_cursor = _cursor_for(scope, normalized[-1])
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise ArchiveInventoryError("archive_pagination_cursor_loop")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    blockers = [] if exhausted else ["archive_pagination_page_limit_reached"]
    return {
        "scope": scope,
        "pages": pages,
        "thread_count": len(threads),
        "pagination_exhausted": exhausted,
        "terminal_reached": exhausted,
        "terminal_cursor": cursor,
        "blockers": blockers,
        "threads": list(threads.values()),
    }


def build_public_report(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """ID、名称、URL、pathを含まない公開用メタデータを作る。"""

    scopes: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for result in results:
        scope = str(result.get("scope") or "")
        scope_blockers = [str(item) for item in result.get("blockers") or []]
        scopes[scope] = {
            "page_count": int(result.get("pages") or 0),
            "thread_count": int(result.get("thread_count") or 0),
            "pagination_exhausted": result.get("pagination_exhausted") is True,
        }
        blockers.extend(scope_blockers)
    complete = bool(scopes) and all(
        item["pagination_exhausted"] for item in scopes.values()
    )
    return {
        "schema": "dcb.archived-thread-inventory.v1",
        "status": "complete" if complete else "partial",
        "scopes": scopes,
        "pagination_exhausted": complete,
        "blockers": sorted(set(blockers)),
        "raw_text_returned": False,
        "participant_names_returned": False,
        "identifiers_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }


def write_private_inventory(
    path: Path,
    results: Sequence[Mapping[str, Any]],
    *,
    parent_target_key: str,
) -> None:
    """privateなスレッドメタデータをmode 0600でatomic保存する。"""

    if not parent_target_key.strip():
        raise ArchiveInventoryError("inventory_binding_required")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "dcb.archived-thread-inventory-private.v1",
        "parent_target_key": parent_target_key,
        "scopes": {
            str(result.get("scope")): {
                "pagination_exhausted": result.get("pagination_exhausted") is True,
                "threads": list(result.get("threads") or []),
            }
            for result in results
        },
        "outbound_actions": "disabled",
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise ArchiveInventoryError("archive_inventory_too_large")
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


def _trusted_thread_fields(
    item: Mapping[str, Any],
    *,
    parent_target_key: str,
    require_parent_match: bool,
) -> tuple[str, bool] | None:
    """APIメタデータから親binding済みIDとlocked状態だけを取り出す。"""

    thread_id = item.get("id")
    parent_id = item.get("parent_id")
    metadata = item.get("thread_metadata")
    if not isinstance(thread_id, str) or not thread_id:
        raise ArchiveInventoryError("inventory_thread_id_missing")
    if require_parent_match and parent_id != parent_target_key:
        return None
    if parent_id is not None and parent_id != parent_target_key:
        raise ArchiveInventoryError("inventory_thread_parent_binding_mismatch")
    if not isinstance(metadata, Mapping) or not isinstance(metadata.get("locked"), bool):
        raise ArchiveInventoryError("inventory_locked_boolean_required")
    return thread_id, bool(metadata["locked"])


def build_active_filtered_result(
    payload: Mapping[str, Any], *, parent_target_key: str
) -> dict[str, Any]:
    """guild全体のactive thread応答を指定親だけへ絞り込む。"""

    raw_threads = payload.get("threads")
    if not isinstance(raw_threads, Sequence) or isinstance(raw_threads, (str, bytes)):
        raise ArchiveInventoryError("active_threads_invalid")
    threads: list[dict[str, Any]] = []
    filtered_out_count = 0
    for item in raw_threads:
        if not isinstance(item, Mapping):
            raise ArchiveInventoryError("active_thread_invalid")
        trusted = _trusted_thread_fields(
            item,
            parent_target_key=parent_target_key,
            require_parent_match=True,
        )
        if trusted is None:
            filtered_out_count += 1
            continue
        threads.append(dict(item))
    return {
        "scope": "active_filtered",
        "pages": 1,
        "thread_count": len(threads),
        "pagination_exhausted": True,
        "terminal_reached": True,
        "terminal_cursor": None,
        "active_parent_filter_applied": True,
        "filtered_out_count": filtered_out_count,
        "blockers": [],
        "threads": threads,
    }


def build_scope_receipts_inventory(
    *,
    parent_target_key: str,
    parent_kind: str,
    scan_id: str,
    observed_at: str,
    active_filtered: Mapping[str, Any],
    archived_results: Sequence[Mapping[str, Any]],
    private_authorization_confirmed: bool,
) -> dict[str, Any]:
    """既存列挙結果をparent completenessのscope receipt正本へ変換する。"""

    if parent_kind not in {"announcement", "forum", "media", "text"}:
        raise ArchiveInventoryError("parent_kind_invalid")
    if not parent_target_key or not scan_id or not observed_at:
        raise ArchiveInventoryError("inventory_binding_required")
    archived = {str(result.get("scope") or ""): result for result in archived_results}
    required_archive = (
        ("public",)
        if parent_kind in {"announcement", "forum", "media"}
        else ("public", "private")
    )
    if any(scope not in archived for scope in required_archive):
        raise ArchiveInventoryError("inventory_required_scope_missing")

    source_results: dict[str, Mapping[str, Any]] = {"active_filtered": active_filtered}
    source_results.update(
        {f"archived_{scope}": archived[scope] for scope in required_archive}
    )
    receipts: dict[str, dict[str, Any]] = {}
    all_ids: list[str] = []
    locked_ids: list[str] = []
    for scope, result in source_results.items():
        threads = result.get("threads")
        if not isinstance(threads, Sequence) or isinstance(threads, (str, bytes)):
            raise ArchiveInventoryError("inventory_threads_invalid")
        scope_ids: list[str] = []
        scope_locked = 0
        for item in threads:
            if not isinstance(item, Mapping):
                raise ArchiveInventoryError("inventory_thread_invalid")
            trusted = _trusted_thread_fields(
                item,
                parent_target_key=parent_target_key,
                require_parent_match=scope == "active_filtered",
            )
            if trusted is None:
                continue
            thread_id, locked = trusted
            if thread_id in scope_ids:
                raise ArchiveInventoryError("duplicate_thread_id")
            scope_ids.append(thread_id)
            if locked:
                scope_locked += 1
                locked_ids.append(thread_id)
        if any(thread_id in all_ids for thread_id in scope_ids):
            raise ArchiveInventoryError("thread_scope_overlap")
        all_ids.extend(scope_ids)
        terminal_reached = result.get("terminal_reached") is True
        receipt = {
            "route": CANONICAL_SCOPE_ROUTES[scope],
            "parent_target_key": parent_target_key,
            "active_parent_filter_applied": scope == "active_filtered",
            "page_count": int(result.get("pages") or 0),
            "terminal_reached": terminal_reached,
            "terminal_cursor": result.get("terminal_cursor"),
            "locked_count": scope_locked,
            "thread_ids": scope_ids,
        }
        if scope == "archived_private":
            receipt["authorization"] = {
                "capability": "manage_threads",
                "confirmed": private_authorization_confirmed,
            }
        receipts[scope] = receipt

    pagination_exhausted = all(
        receipt["terminal_reached"] for receipt in receipts.values()
    ) and (
        parent_kind != "text" or private_authorization_confirmed
    )
    return {
        "schema": "dcb.parent-thread-scope-receipts.v1",
        "parent_target_key": parent_target_key,
        "parent_kind": parent_kind,
        "scan_id": scan_id,
        "observed_at": observed_at,
        "scope_receipts": receipts,
        # 旧consumer向けの読み取り互換。正本はscope_receipts。
        "thread_ids": sorted(all_ids),
        "scopes": {
            "active": receipts["active_filtered"]["terminal_reached"],
            "archived_public": receipts["archived_public"]["terminal_reached"],
            "archived_private": (
                receipts.get("archived_private", {}).get("terminal_reached", True)
            ),
        },
        "pagination_exhausted": pagination_exhausted,
        "locked_count": len(set(locked_ids)),
        "outbound_actions": "disabled",
    }


def write_private_scope_receipts(path: Path, payload: Mapping[str, Any]) -> None:
    """scope receipt正本をmode 0600でatomic保存する。"""

    if payload.get("schema") != "dcb.parent-thread-scope-receipts.v1":
        raise ArchiveInventoryError("scope_receipts_schema_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n").encode()
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise ArchiveInventoryError("scope_receipts_too_large")
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
