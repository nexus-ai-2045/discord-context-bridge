#!/usr/bin/env python3
"""Discord正式APIのGETだけでスレッド棚卸しを行う。"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge import (
    load_bot_token_from_provider,
    plan_discord_url_read,
)
from discord_context_bridge.archive_inventory import (
    ARCHIVE_SCOPES,
    ArchiveInventoryError,
    build_active_filtered_result,
    build_public_report,
    build_scope_receipts_inventory,
    enumerate_archive_pages,
    write_private_inventory,
    write_private_scope_receipts,
)
from discord_context_bridge.cli import JapaneseArgumentParser

API_BASE = "https://discord.com/api/v10"
DEFAULT_OUTPUT = Path(".local/discord-context-bridge/archived-thread-inventory.json")
PARENT_KIND_BY_CHANNEL_TYPE = {
    0: "text",
    5: "announcement",
    15: "forum",
    16: "media",
}
PUBLIC_REASON_CODES = {
    "active_fixture_scope_missing",
    "archive_arguments_invalid",
    "archive_fixture_exhausted",
    "archive_fixture_invalid",
    "archive_fixture_scope_missing",
    "archive_inventory_failed",
    "archive_inventory_too_large",
    "archive_max_pages_invalid",
    "archive_pagination_cursor_loop",
    "archive_pagination_empty_page",
    "archive_pagination_page_limit_reached",
    "archive_response_invalid",
    "archive_scope_invalid",
    "archive_scope_not_applicable",
    "archive_thread_id_missing",
    "archive_thread_invalid",
    "archive_threads_invalid",
    "bot_token_missing",
    "channel_metadata_invalid",
    "channel_guild_binding_mismatch",
    "channel_type_not_supported",
    "discord_parent_channel_url_required",
    "inventory_binding_required",
    "inventory_locked_boolean_required",
    "inventory_required_scope_missing",
    "inventory_thread_parent_binding_mismatch",
    "parent_kind_mismatch",
    "permission_denied",
    "rate_limited_retryable",
    "source_not_found",
    "thread_scope_overlap",
}


def _public_reason(error: BaseException) -> str:
    reason = str(error)
    return reason if reason in PUBLIC_REASON_CODES else "archive_inventory_failed"


def _trusted_parent_kind(
    payload: Mapping[str, Any], channel_id: str, guild_id: str
) -> str:
    """Discord channel metadataのtypeをparent kindへ変換する。"""

    if payload.get("id") != channel_id:
        raise ArchiveInventoryError("channel_metadata_invalid")
    if payload.get("guild_id") != guild_id:
        raise ArchiveInventoryError("channel_guild_binding_mismatch")
    channel_type = payload.get("type")
    if not isinstance(channel_type, int) or isinstance(channel_type, bool):
        raise ArchiveInventoryError("channel_metadata_invalid")
    parent_kind = PARENT_KIND_BY_CHANNEL_TYPE.get(channel_type)
    if parent_kind is None:
        raise ArchiveInventoryError("channel_type_not_supported")
    return parent_kind


def _route(scope: str, channel_id: str) -> str:
    quoted = urllib.parse.quote(channel_id, safe="")
    if scope == "public":
        return f"/channels/{quoted}/threads/archived/public"
    if scope == "private":
        return f"/channels/{quoted}/threads/archived/private"
    return f"/channels/{quoted}/users/@me/threads/archived/private"


def _request_json(
    *,
    route: str,
    token: str,
    query: Mapping[str, str],
    sleep_seconds: float,
    max_rate_limit_retries: int,
) -> Mapping[str, Any]:
    retries = 0
    while True:
        request = urllib.request.Request(
            API_BASE + route + "?" + urllib.parse.urlencode(query),
            headers={
                "Author" + "ization": f"Bot {token}",
                "Accept": "application/json",
                "User-Agent": "discord-context-bridge-readonly/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429:
                if retries >= max_rate_limit_retries:
                    raise ArchiveInventoryError("rate_limited_retryable") from error
                retries += 1
                retry_after = 0.0
                try:
                    rate_payload = json.loads(error.read().decode("utf-8"))
                    retry_after = float(rate_payload.get("retry_after") or 0)
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    retry_after = 0.0
                time.sleep(max(0.0, retry_after))
                continue
            if error.code in {401, 403}:
                raise ArchiveInventoryError("permission_denied") from error
            if error.code == 404:
                raise ArchiveInventoryError("source_not_found") from error
            raise ArchiveInventoryError("archive_rest_failed") from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ArchiveInventoryError("archive_rest_failed") from error
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        if not isinstance(payload, Mapping):
            raise ArchiveInventoryError("archive_response_invalid")
        return payload


def _fetcher(
    *,
    scope: str,
    channel_id: str,
    token: str,
    page_size: int,
    sleep_seconds: float,
    max_rate_limit_retries: int = 1,
):
    def fetch(cursor: str | None) -> Mapping[str, Any]:
        query = {"limit": str(page_size)}
        if cursor:
            query["before"] = cursor
        return _request_json(
            route=_route(scope, channel_id),
            token=token,
            query=query,
            sleep_seconds=sleep_seconds,
            max_rate_limit_retries=max_rate_limit_retries,
        )

    return fetch


def _fixture_fetcher(pages: list[object]):
    index = 0

    def fetch(_cursor: str | None) -> Mapping[str, Any]:
        nonlocal index
        if index >= len(pages) or not isinstance(pages[index], Mapping):
            raise ArchiveInventoryError("archive_fixture_exhausted")
        payload = pages[index]
        index += 1
        return payload

    return fetch


def build_parser() -> argparse.ArgumentParser:
    parser = JapaneseArgumentParser(description="DiscordスレッドをGETだけで棚卸しします")
    parser.add_argument("--url", required=True, help="親チャンネルURL。出力には表示しません")
    parser.add_argument("--scope", action="append", choices=ARCHIVE_SCOPES)
    parser.add_argument("--parent-kind", choices=("announcement", "forum", "media", "text"), help="指定時はparent completeness用scope receiptを作ります")
    parser.add_argument("--scan-id", help="privateな走査ID。未指定時は自動生成します")
    parser.add_argument("--observed-at", help="観測時刻。未指定時は現在UTCです")
    parser.add_argument("--page-size", type=int, default=100, help="1回の最大取得件数")
    parser.add_argument("--max-pages", type=int, default=1000, help="scopeごとの最大ページ数")
    parser.add_argument("--sleep-seconds", type=float, default=0.25, help="API呼出し後の待機秒数")
    parser.add_argument("--max-rate-limit-retries", type=int, default=1, help="429の最大再試行回数")
    parser.add_argument("--fixture-input", type=Path, help="テスト用fixture JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="private JSON保存先")
    parser.add_argument("--json", action="store_true", help="メタデータだけをJSON出力します")
    return parser


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "schema": "dcb.archived-thread-inventory.v1",
        "status": "blocked",
        "pagination_exhausted": False,
        "blockers": [reason],
        "raw_text_returned": False,
        "participant_names_returned": False,
        "identifiers_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    route = plan_discord_url_read(args.url)
    if not route.get("ok_to_open") or route.get("message_or_thread_id"):
        print(json.dumps(_blocked("discord_parent_channel_url_required"), ensure_ascii=False, sort_keys=True))
        return 2
    channel_id = str(route.get("channel_id") or "")
    guild_id = str(route.get("guild_id") or "")
    if args.parent_kind:
        canonical_archive_scopes = ("public", "private") if args.parent_kind == "text" else ("public",)
        scopes = tuple(dict.fromkeys(args.scope or canonical_archive_scopes))
        if any(scope not in canonical_archive_scopes for scope in scopes):
            print(json.dumps(_blocked("archive_scope_not_applicable"), ensure_ascii=False, sort_keys=True))
            return 2
    else:
        scopes = tuple(dict.fromkeys(args.scope or ARCHIVE_SCOPES))
    if (
        not 1 <= args.page_size <= 100
        or args.max_pages < 1
        or args.sleep_seconds < 0
        or args.max_rate_limit_retries < 0
    ):
        print(json.dumps(_blocked("archive_arguments_invalid"), ensure_ascii=False, sort_keys=True))
        return 2

    fixture: Mapping[str, Any] | None = None
    token = ""
    if args.fixture_input:
        try:
            loaded = json.loads(args.fixture_input.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            print(json.dumps(_blocked("archive_fixture_invalid"), ensure_ascii=False, sort_keys=True))
            return 2
        if not isinstance(loaded, Mapping):
            print(json.dumps(_blocked("archive_fixture_invalid"), ensure_ascii=False, sort_keys=True))
            return 2
        fixture = loaded
    else:
        credential = load_bot_token_from_provider()
        if not credential.ok:
            print(json.dumps(_blocked(credential.failure_stage or "bot_token_missing"), ensure_ascii=False, sort_keys=True))
            return 2
        token = credential.token

    results: list[dict[str, Any]] = []
    try:
        active_payload: Mapping[str, Any] | None = None
        if args.parent_kind:
            if fixture is not None:
                channel_metadata = fixture.get("channel_metadata")
                if not isinstance(channel_metadata, Mapping):
                    raise ArchiveInventoryError("channel_metadata_invalid")
                raw_active = fixture.get("active_filtered")
                if isinstance(raw_active, list) and raw_active and isinstance(raw_active[0], Mapping):
                    active_payload = raw_active[0]
                elif isinstance(raw_active, Mapping):
                    active_payload = raw_active
                else:
                    raise ArchiveInventoryError("active_fixture_scope_missing")
            else:
                channel_metadata = _request_json(
                    route=f"/channels/{urllib.parse.quote(channel_id, safe='')}",
                    token=token,
                    query={},
                    sleep_seconds=args.sleep_seconds,
                    max_rate_limit_retries=args.max_rate_limit_retries,
                )
            trusted_parent_kind = _trusted_parent_kind(
                channel_metadata, channel_id, guild_id
            )
            if trusted_parent_kind != args.parent_kind:
                raise ArchiveInventoryError("parent_kind_mismatch")
            if fixture is None:
                active_payload = _request_json(
                    route=f"/guilds/{urllib.parse.quote(guild_id, safe='')}/threads/active",
                    token=token,
                    query={},
                    sleep_seconds=args.sleep_seconds,
                    max_rate_limit_retries=args.max_rate_limit_retries,
                )
        for scope in scopes:
            if fixture is not None:
                pages = fixture.get(scope)
                if not isinstance(pages, list):
                    raise ArchiveInventoryError("archive_fixture_scope_missing")
                fetch = _fixture_fetcher(pages)
            else:
                fetch = _fetcher(
                    scope=scope,
                    channel_id=channel_id,
                    token=token,
                    page_size=args.page_size,
                    sleep_seconds=args.sleep_seconds,
                    max_rate_limit_retries=args.max_rate_limit_retries,
                )
            results.append(
                enumerate_archive_pages(scope=scope, fetch_page=fetch, max_pages=args.max_pages)
            )
        if args.parent_kind:
            assert active_payload is not None
            active_filtered = build_active_filtered_result(
                active_payload, parent_target_key=channel_id
            )
            private_receipt = build_scope_receipts_inventory(
                parent_target_key=channel_id,
                parent_kind=args.parent_kind,
                scan_id=args.scan_id or str(uuid.uuid4()),
                observed_at=args.observed_at or datetime.now(UTC).isoformat(),
                active_filtered=active_filtered,
                archived_results=results,
                # 正式private archive routeの200応答を権限確認証拠とする。
                private_authorization_confirmed=args.parent_kind != "text" or fixture is None or fixture.get("manage_threads_confirmed") is True,
            )
            write_private_scope_receipts(args.output, private_receipt)
            report = {
                "schema": "dcb.parent-thread-scope-receipts-public.v1",
                "status": "complete" if private_receipt["pagination_exhausted"] else "partial",
                "scope_count": len(private_receipt["scope_receipts"]),
                "thread_count": len(private_receipt["thread_ids"]),
                "locked_count": private_receipt["locked_count"],
                "pagination_exhausted": private_receipt["pagination_exhausted"],
                "raw_text_returned": False,
                "participant_names_returned": False,
                "identifiers_returned": False,
                "url_output": "omitted",
                "path_output": "omitted",
                "outbound_actions": "disabled",
            }
        else:
            write_private_inventory(args.output, results, parent_target_key=channel_id)
            report = build_public_report(results)
    except ArchiveInventoryError as error:
        report = _blocked(_public_reason(error))
    except (OSError, UnicodeError, json.JSONDecodeError):
        report = _blocked("archive_inventory_failed")

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("pagination_exhausted") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
