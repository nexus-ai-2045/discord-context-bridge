from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from discord_context_bridge.live_verification import (
    LiveVerificationError,
    normalize_expected_target,
)

DISCORD_HOSTS = {"discord.com", "www.discord.com", "canary.discord.com", "ptb.discord.com"}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _path_parts(url: str) -> list[str]:
    parsed = urlparse(url or "")
    return [part for part in parsed.path.split("/") if part]


def _route_key(url: str) -> tuple[str | None, str | None, str | None]:
    if not isinstance(url, str):
        return None, None, None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None, None, None
    if parsed.scheme != "https" or parsed.netloc.casefold() not in DISCORD_HOSTS:
        return None, None, None
    try:
        target = normalize_expected_target(parsed._replace(netloc="discord.com").geturl())
    except LiveVerificationError:
        return None, None, None
    parts = _path_parts(url)
    # メッセージへの移動要求も、別メッセージのタブと同一視しない。
    message = parts[-1] if len(parts) in {4, 6} else None
    return target["guild_id"], target["channel_id"], message


def _is_discord_channel(url: str) -> bool:
    return all(_route_key(url)[:2])


def _has_claim_handle(tab: dict[str, Any]) -> bool:
    return any(
        (isinstance(value, str) and bool(value.strip()))
        or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)
        for value in (tab.get("providerTabId"), tab.get("id"))
    )


def _safe_tab(
    tab: dict[str, Any],
    *,
    target_key: tuple[str | None, str | None, str | None],
    index: int,
) -> dict[str, Any]:
    guild, channel, thread = _route_key(str(tab.get("url") or ""))
    target_guild, target_channel, target_thread = target_key
    return {
        "candidate_index": index,
        "claim_handle_present": _has_claim_handle(tab),
        "title_available": bool(str(tab.get("title") or "").strip()),
        "title_used_for_matching": False,
        "url_output": "omitted",
        "raw_identifiers_output": "omitted",
        "tab_group": "codex" if tab.get("tabGroup") == "Codex" else "other" if tab.get("tabGroup") else "none",
        "last_opened_available": bool(tab.get("lastOpened")),
        "is_discord_channel": _is_discord_channel(str(tab.get("url") or "")),
        "same_guild": guild is not None and guild == target_guild,
        "same_channel": channel is not None and channel == target_channel,
        "same_thread": (target_thread is None and thread is None) or (thread is not None and thread == target_thread),
        "matching_basis": "canonical_url_route",
    }


def _sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        1 if candidate["same_guild"] else 0,
        1 if candidate["tab_group"] == "codex" else 0,
        1 if candidate["tab_group"] != "none" else 0,
        -int(candidate["candidate_index"]),
    )


def decide_visible_fallback(*, target_url: str, open_tabs: list[dict[str, Any]]) -> dict[str, Any]:
    target_key = _route_key(target_url)
    invalid_inventory = not isinstance(open_tabs, list) or any(not isinstance(tab, dict) for tab in open_tabs)
    if invalid_inventory:
        open_tabs = []
    discord_tabs = [tab for tab in open_tabs if _is_discord_channel(str(tab.get("url") or ""))]
    safe_candidates = [
        _safe_tab(tab, target_key=target_key, index=index)
        for index, tab in enumerate(open_tabs)
        if _is_discord_channel(str(tab.get("url") or ""))
    ]

    exact = [
        candidate
        for candidate in safe_candidates
        if candidate["same_guild"] and candidate["same_channel"] and candidate["same_thread"]
    ]
    same_guild = [candidate for candidate in safe_candidates if candidate["same_guild"]]
    reusable = sorted(
        (candidate for candidate in safe_candidates if candidate["claim_handle_present"]),
        key=_sort_key, reverse=True,
    )
    claimable_exact = [candidate for candidate in reusable if candidate in exact]
    selected = claimable_exact[0] if claimable_exact else (reusable[0] if reusable else None)

    if invalid_inventory or not all(target_key[:2]) or (safe_candidates and selected is None):
        decision = "blocked"
        reason = "invalid_target_url" if not all(target_key[:2]) else "claim_handle_missing"
        if invalid_inventory:
            reason = "invalid_tab_inventory"
        next_action = "対象URLと現在のタブ識別子を再確認してください。ブラウザ操作は行いません。"
        ok = False
        navigate_after_claim = False
        selected = None
    elif claimable_exact:
        decision = "claim_existing_target_tab"
        reason = "target_tab_already_open"
        next_action = "既存の対象Discordタブをclaimして読み取りを続けます。新規タブは開きません。"
        ok = True
        navigate_after_claim = False
    elif selected:
        decision = "claim_existing_discord_tab_then_navigate"
        reason = "target_absent_but_reusable_discord_tab_exists"
        next_action = "既存Discordタブをclaimし、そのタブを対象URLへ移動して読み取りを続けます。新規タブは開きません。"
        ok = True
        navigate_after_claim = True
    else:
        decision = "open_new_tab_in_existing_window"
        reason = "no_reusable_discord_tabs"
        next_action = "再利用できるDiscordタブがないため、既存Chromeウィンドウ内で新規タブを開いて対象URLへ移動します。"
        ok = True
        navigate_after_claim = False

    return {
        "schema": "chrome_visible_fallback_guard.v1",
        "ok": ok,
        "decision": decision,
        "reason": reason,
        "target": {
            "is_discord_channel_route": all(target_key[:2]),
            "raw_url_output": "omitted",
            "raw_identifiers_output": "omitted",
        },
        "inventory": {
            "open_tab_count": len(open_tabs),
            "discord_channel_tab_count": len(discord_tabs),
            "matching_target_tab_count": len(exact),
            "same_guild_tab_count": len(same_guild),
            "selected_candidate_index": None if selected is None else selected["candidate_index"],
            "selected_tab_group": None if selected is None else selected.get("tab_group"),
            "selected_claim_handle_present": bool(selected and selected.get("claim_handle_present")),
            "candidates": safe_candidates,
        },
        "chrome_action_policy": {
            "must_call_open_tabs_first": True,
            "prefer_claim_existing_tab": True,
            "canonical_identity": "url_route",
            "claim_handle": "providerTabId_or_id_from_current_open_tabs",
            "tab_group_used_only_as_tiebreaker": True,
            "title_used_for_matching": False,
            "ok_to_claim_existing_tab": ok and selected is not None,
            "navigate_after_claim": navigate_after_claim,
            "ok_to_open_new_tab": ok and selected is None,
            "open_new_tab_only_when_no_reusable_discord_tab": True,
            "raw_text_output": "omitted",
            "outbound_actions": "disabled",
        },
        "next_action": next_action,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chrome visible fallback の既存タブ再利用 gate。")
    parser.add_argument("--target-url", required=True, help="対象Discord URL。出力には表示しません。")
    parser.add_argument("--open-tabs-json", required=True, help="browser.user.openTabs() 相当のJSON file。")
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Chrome visible fallback guard")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"decision: {payload['decision']}")
    print(f"reason: {payload['reason']}")
    print(f"discord_channel_tab_count: {payload['inventory']['discord_channel_tab_count']}")
    print(f"matching_target_tab_count: {payload['inventory']['matching_target_tab_count']}")
    print(f"ok_to_claim_existing_tab: {str(payload['chrome_action_policy']['ok_to_claim_existing_tab']).lower()}")
    print(f"ok_to_open_new_tab: {str(payload['chrome_action_policy']['ok_to_open_new_tab']).lower()}")
    print(f"next_action: {payload['next_action']}")
    print("raw_url_output: omitted")
    print("raw_identifiers_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tabs = json.loads(Path(args.open_tabs_json).read_text(encoding="utf-8"))
    if not isinstance(tabs, list):
        raise SystemExit("open-tabs JSON must be a list")
    payload = decide_visible_fallback(target_url=args.target_url, open_tabs=tabs)
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
