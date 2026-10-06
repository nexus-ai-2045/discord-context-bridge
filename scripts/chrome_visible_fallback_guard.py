#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any
from urllib.parse import urlparse


DISCORD_HOSTS = {"discord.com", "www.discord.com", "canary.discord.com", "ptb.discord.com"}
CLAIM_FIELDS = ("providerTabId", "title", "url")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _path_parts(url: str) -> list[str]:
    parsed = urlparse(url or "")
    return [part for part in parsed.path.split("/") if part]


def _route_key(url: str) -> tuple[str | None, str | None, str | None]:
    parts = _path_parts(url)
    if len(parts) >= 3 and parts[0] == "channels":
        thread = parts[3] if len(parts) >= 4 else None
        return parts[1], parts[2], thread
    return None, None, None


def _is_discord_channel(url: str) -> bool:
    parsed = urlparse(url or "")
    parts = _path_parts(url)
    return (
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() in DISCORD_HOSTS
        and len(parts) >= 3
        and parts[0] == "channels"
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
        "title_available": bool(str(tab.get("title") or "").strip()),
        "url_output": "omitted",
        "raw_identifiers_output": "omitted",
        "tab_group_available": bool(tab.get("tabGroup")),
        "preferred_tab_group": tab.get("tabGroup") == "Codex",
        "last_opened_available": bool(tab.get("lastOpened")),
        "is_discord_channel": _is_discord_channel(str(tab.get("url") or "")),
        "same_guild": guild is not None and guild == target_guild,
        "same_channel": channel is not None and channel == target_channel,
        "same_thread": (target_thread is None and thread is None) or (thread is not None and thread == target_thread),
    }


def _sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        1 if candidate["same_guild"] else 0,
        1 if candidate["preferred_tab_group"] else 0,
        1 if candidate["tab_group_available"] else 0,
        -int(candidate["candidate_index"]),
    )


def decide_visible_fallback(
    *,
    target_url: str,
    open_tabs: list[dict[str, Any]],
    binding_token: str | None = None,
) -> dict[str, Any]:
    target_key = _route_key(target_url)
    if not _is_discord_channel(target_url):
        return {
            "schema": "chrome_visible_fallback_guard.v2",
            "ok": False,
            "decision": "stop",
            "reason": "target_not_discord_channel_route",
            "raw_output": "omitted",
            "outbound_actions": "disabled",
        }
    if not isinstance(open_tabs, list) or any(not isinstance(tab, dict) for tab in open_tabs):
        return {
            "schema": "chrome_visible_fallback_guard.v2",
            "ok": False,
            "decision": "stop",
            "reason": "open_tabs_snapshot_invalid",
            "raw_output": "omitted",
            "outbound_actions": "disabled",
        }
    snapshot_binding = {
        "target_digest": _digest(target_url),
        "binding_token_kind": "caller_supplied_opaque_binding_only",
        "binding_token_digest": _digest(binding_token) if binding_token else None,
        "open_tabs_digest": _digest(
            [{field: tab.get(field) for field in CLAIM_FIELDS} for tab in open_tabs]
        ),
    }
    if not binding_token:
        return {
            "schema": "chrome_visible_fallback_guard.v2",
            "ok": False,
            "decision": "stop",
            "reason": "open_tabs_binding_token_missing",
            "snapshot_binding": snapshot_binding,
            "raw_output": "omitted",
            "outbound_actions": "disabled",
        }
    discord_tabs = [tab for tab in open_tabs if _is_discord_channel(str(tab.get("url") or ""))]
    safe_candidates = [
        _safe_tab(tab, target_key=target_key, index=index)
        for index, tab in enumerate(discord_tabs)
    ]

    exact = [
        candidate
        for candidate in safe_candidates
        if candidate["same_guild"] and candidate["same_channel"] and candidate["same_thread"]
    ]
    same_guild = [candidate for candidate in safe_candidates if candidate["same_guild"]]
    reusable = sorted(safe_candidates, key=_sort_key, reverse=True)
    selected = exact[0] if exact else (same_guild[0] if same_guild else (reusable[0] if reusable else None))
    selected_tab = None if selected is None else discord_tabs[int(selected["candidate_index"])]

    if selected_tab is not None and any(not str(selected_tab.get(field) or "").strip() for field in CLAIM_FIELDS):
        return {
            "schema": "chrome_visible_fallback_guard.v2",
            "ok": False,
            "decision": "stop",
            "reason": "selected_claim_object_missing_required_fields",
            "snapshot_binding": snapshot_binding,
            "claim_binding": {"required_fields": list(CLAIM_FIELDS), "bound": False},
            "raw_output": "omitted",
            "outbound_actions": "disabled",
        }
    selected_claim_digest = (
        _digest({field: selected_tab[field] for field in CLAIM_FIELDS})
        if selected_tab is not None
        else None
    )
    snapshot_binding["selected_claim_object_digest"] = selected_claim_digest

    if exact:
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
        "schema": "chrome_visible_fallback_guard.v2",
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
            "candidates": safe_candidates,
        },
        "snapshot_binding": snapshot_binding,
        "claim_binding": {
            "required_fields": list(CLAIM_FIELDS),
            "bound": selected_tab is not None,
            "binding_token_digest": snapshot_binding["binding_token_digest"],
            "open_tabs_digest": snapshot_binding["open_tabs_digest"],
            "claim_object_digest": selected_claim_digest,
            "provider_tab_id_digest": _digest(selected_tab["providerTabId"]) if selected_tab else None,
            "title_digest": _digest(selected_tab["title"]) if selected_tab else None,
            "url_digest": _digest(selected_tab["url"]) if selected_tab else None,
        },
        "chrome_action_policy": {
            "must_call_open_tabs_first": True,
            "prefer_claim_existing_tab": True,
            "ok_to_claim_existing_tab": selected is not None,
            "navigate_after_claim": navigate_after_claim,
            "ok_to_open_new_tab": selected is None,
            "open_new_tab_only_when_no_reusable_discord_tab": True,
            "post_open_fresh_open_tabs_and_guard_rerun_required": selected is None,
            "live_stage_observation_allowed": selected is not None,
            "goto": "forbidden_target_already_exact" if exact else "allowed_only_after_claim",
            "reload": "forbidden_target_already_exact" if exact else "forbidden",
            "raw_text_output": "omitted",
            "outbound_actions": "disabled",
        },
        "next_action": next_action,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chrome visible fallback の既存タブ再利用 gate。")
    parser.add_argument("--target-url", required=True, help="対象Discord URL。出力には表示しません。")
    parser.add_argument("--open-tabs-json", required=True, help="browser.user.openTabs() 相当のJSON file。")
    parser.add_argument(
        "--binding-token",
        required=True,
        help="openTabs objectとclaimを同一receipt内で結ぶopaque token。freshness証明ではありません。",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Chrome visible fallback guard")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"decision: {payload['decision']}")
    print(f"reason: {payload['reason']}")
    if not payload["ok"]:
        print("raw_url_output: omitted")
        print("raw_identifiers_output: omitted")
        print("outbound_actions: disabled")
        return
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
    try:
        tabs = json.loads(open(args.open_tabs_json, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        payload = {
            "schema": "chrome_visible_fallback_guard.v2",
            "ok": False,
            "decision": "stop",
            "reason": "open_tabs_snapshot_unreadable",
            "raw_output": "omitted",
            "local_paths_output": "omitted",
            "outbound_actions": "disabled",
        }
        print(_json(payload) if args.json else "ok: false\nreason: open_tabs_snapshot_unreadable\noutbound_actions: disabled")
        return 2
    if not isinstance(tabs, list):
        payload = {
            "schema": "chrome_visible_fallback_guard.v2",
            "ok": False,
            "decision": "stop",
            "reason": "open_tabs_snapshot_invalid",
            "raw_output": "omitted",
            "local_paths_output": "omitted",
            "outbound_actions": "disabled",
        }
        print(_json(payload) if args.json else "ok: false\nreason: open_tabs_snapshot_invalid\noutbound_actions: disabled")
        return 2
    payload = decide_visible_fallback(
        target_url=args.target_url,
        open_tabs=tabs,
        binding_token=args.binding_token,
    )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
