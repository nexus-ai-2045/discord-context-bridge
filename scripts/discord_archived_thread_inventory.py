#!/usr/bin/env python3
"""Enumerate Discord archived threads through GET-only official API routes."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge import load_bot_token_from_provider, plan_discord_url_read  # noqa: E402
from discord_context_bridge.archive_inventory import (  # noqa: E402
    ARCHIVE_SCOPES,
    ArchiveInventoryError,
    build_public_report,
    enumerate_archive_pages,
    write_private_inventory,
)

API_BASE = "https://discord.com/api/v10"
DEFAULT_OUTPUT = Path(".local/discord-context-bridge/archived-thread-inventory.json")


def _route(scope: str, channel_id: str) -> str:
    quoted = urllib.parse.quote(channel_id, safe="")
    if scope == "public":
        return f"/channels/{quoted}/threads/archived/public"
    if scope == "private":
        return f"/channels/{quoted}/threads/archived/private"
    return f"/channels/{quoted}/users/@me/threads/archived/private"


def _fetcher(*, scope: str, channel_id: str, token: str, page_size: int, sleep_seconds: float):
    def fetch(cursor: str | None) -> Mapping[str, Any]:
        query = {"limit": str(page_size)}
        if cursor:
            query["before"] = cursor
        request = urllib.request.Request(
            API_BASE + _route(scope, channel_id) + "?" + urllib.parse.urlencode(query),
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
                raise ArchiveInventoryError("rate_limited_retryable") from error
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
    parser = argparse.ArgumentParser(description="Discord archived thread inventory (GET only)")
    parser.add_argument("--url", required=True, help="parent channel URL; omitted from output")
    parser.add_argument("--scope", action="append", choices=ARCHIVE_SCOPES)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--fixture-input", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
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
    scopes = tuple(dict.fromkeys(args.scope or ARCHIVE_SCOPES))
    if not 1 <= args.page_size <= 100 or args.max_pages < 1 or args.sleep_seconds < 0:
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

    results = []
    try:
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
                )
            results.append(
                enumerate_archive_pages(scope=scope, fetch_page=fetch, max_pages=args.max_pages)
            )
        write_private_inventory(args.output, results)
        report = build_public_report(results)
    except (ArchiveInventoryError, OSError, UnicodeError, json.JSONDecodeError) as error:
        report = _blocked(str(error) or "archive_inventory_failed")

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("pagination_exhausted") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
