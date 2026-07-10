#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge import (  # noqa: E402
    DEFAULT_REST_BACKFILL_MANIFEST,
    DEFAULT_REST_BACKFILL_RAW_OUTPUT,
    load_bot_token_from_provider,
    plan_discord_url_read,
    rest_backfill_config_safety,
    write_rest_backfill_capture,
)


API_BASE = "https://discord.com/api/v10"


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def read_fixture_messages(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        loaded = json.loads(stripped)
        return [item for item in loaded if isinstance(item, dict)]
    messages: list[dict[str, Any]] = []
    for line in stripped.splitlines():
        if line.strip():
            loaded = json.loads(line)
            if isinstance(loaded, dict):
                messages.append(loaded)
    return messages


def blocked_payload(reason: str, *, retry_after: float | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "discord_rest_backfill.v1",
        "ok": False,
        "state": "blocked",
        "failure_stage": reason,
        "blockers": [reason],
        "rate_limit": {
            "status": "rate_limited_retryable" if reason == "rate_limited_retryable" else "not_rate_limited",
            "retryable": reason == "rate_limited_retryable",
        },
        "token_output": "omitted",
        "token_provider": "omitted",
        "cookie_output": "omitted",
        "raw_text_returned": False,
        "participant_names_returned": False,
        "snowflake_values_returned": False,
        "path_output": "omitted",
        "outbound_actions": "disabled",
        "send_capability": "disabled",
    }
    if retry_after is not None:
        payload["rate_limit"]["retry_after_seconds"] = retry_after
    return payload


def classify_http_error(error: urllib.error.HTTPError) -> dict[str, Any]:
    if error.code == 429:
        retry_after = None
        try:
            body = error.read().decode("utf-8")
            loaded = json.loads(body)
            retry_after = float(loaded.get("retry_after")) if isinstance(loaded, dict) and loaded.get("retry_after") else None
        except Exception:
            retry_after = None
        return blocked_payload("rate_limited_retryable", retry_after=retry_after)
    if error.code in {401, 403}:
        return blocked_payload("permission_denied")
    if error.code == 404:
        return blocked_payload("source_not_found")
    return blocked_payload("rest_backfill_failed")


def fetch_discord_messages(*, channel_id: str, token: str, limit: int, page_size: int, sleep_seconds: float) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    messages: list[dict[str, Any]] = []
    before = ""
    remaining = max(0, limit)
    while remaining > 0:
        current_limit = min(100, max(1, page_size), remaining)
        query = {"limit": str(current_limit)}
        if before:
            query["before"] = before
        url = f"{API_BASE}/channels/{urllib.parse.quote(channel_id)}/messages?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={
                "Author" + "ization": f"Bot {token}",
                "Accept": "application/json",
                "User-Agent": "discord-context-bridge-readonly/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                page = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return messages, classify_http_error(error)
        if not isinstance(page, list) or not page:
            break
        page_messages = [item for item in page if isinstance(item, dict)]
        messages.extend(page_messages)
        remaining -= len(page_messages)
        before = str(page_messages[-1].get("id") or "")
        if len(page_messages) < current_limit or not before:
            break
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return messages, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord REST backfill を read-only で private artifact に保存する。")
    parser.add_argument("--url", required=True, help="対象 Discord URL。出力には表示しません")
    parser.add_argument("--limit", type=int, default=100, help="最大取得件数")
    parser.add_argument("--page-size", type=int, default=100, help="Discord API 1 request あたりの件数")
    parser.add_argument("--sleep-seconds", type=float, default=0.25, help="ページ間 sleep 秒")
    parser.add_argument("--fixture-input", type=Path, help="テスト用 JSON/JSONL。token は不要です")
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_REST_BACKFILL_RAW_OUTPUT, help="private raw JSONL 保存先。出力には表示しません")
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_REST_BACKFILL_MANIFEST, help="metadata-only manifest 保存先。出力には表示しません")
    parser.add_argument("--full-thread-confirmed", action="store_true", help="取得範囲が対象全文であると確認済みの場合だけ指定")
    parser.add_argument("--json", action="store_true", help="互換用。常に JSON を出力します")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    route = plan_discord_url_read(args.url)
    if not route.get("ok_to_open"):
        print(_json(blocked_payload("discord_channel_url_required")))
        return 2

    config_safety = rest_backfill_config_safety(" ".join(str(part) for part in [args.fixture_input or "", args.raw_output, args.manifest_output]))
    if not config_safety["ok"]:
        payload = blocked_payload("unsafe_backfill_config")
        payload["config_safety"] = config_safety
        print(_json(payload))
        return 2

    warnings: list[str] = []
    rate_limit_status = "not_rate_limited"
    if args.fixture_input:
        messages = read_fixture_messages(args.fixture_input)
        warnings.append("fixture_input_used")
    else:
        token_result = load_bot_token_from_provider()
        if not token_result.ok:
            payload = blocked_payload(token_result.failure_stage or "bot_token_missing")
            payload["credential_provider"] = token_result.public_status()
            print(_json(payload))
            return 2
        channel_id = str(route.get("message_or_thread_id") or route.get("channel_id") or "")
        messages, blocked = fetch_discord_messages(
            channel_id=channel_id,
            token=token_result.token,
            limit=args.limit,
            page_size=args.page_size,
            sleep_seconds=args.sleep_seconds,
        )
        if blocked:
            print(_json(blocked))
            return 2
        warnings.append("live_discord_api_used_read_only")

    if not messages:
        payload = blocked_payload("source_empty")
        print(_json(payload))
        return 2

    payload = write_rest_backfill_capture(
        url=args.url,
        messages=messages,
        raw_output=args.raw_output,
        manifest_output=args.manifest_output,
        full_thread_confirmed=args.full_thread_confirmed,
        rate_limit_status=rate_limit_status,
        warnings=warnings,
    )
    payload["schema"] = "discord_rest_backfill.v1"
    payload["route"] = "rest_backfill"
    payload["token_output"] = "omitted"
    payload["credential_provider"] = token_result.public_status() if not args.fixture_input else {
        "schema": "discord_bot_token_provider.v1",
        "ok": True,
        "provider": "fixture",
        "token_set": False,
        "value_returned": False,
        "token_output": "omitted",
        "command_output": "omitted",
    }
    print(_json(payload))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
