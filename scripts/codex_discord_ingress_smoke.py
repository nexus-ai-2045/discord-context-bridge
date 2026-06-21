#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import urlparse


DISCORD_HOSTS = {"discord.com", "www.discord.com", "canary.discord.com", "ptb.discord.com"}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def classify_discord_location(url: str, title: str = "") -> dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    path_parts = [part for part in parsed.path.split("/") if part]
    is_discord = host in DISCORD_HOSTS
    is_app_route = is_discord and len(path_parts) >= 1 and path_parts[0] in {"channels", "app"}
    has_channel_route = is_discord and len(path_parts) >= 3 and path_parts[0] == "channels"
    has_thread_like_route = is_discord and len(path_parts) >= 4 and path_parts[0] == "channels"
    return {
        "is_discord": is_discord,
        "is_app_route": is_app_route,
        "has_channel_route": has_channel_route,
        "has_thread_like_route": has_thread_like_route,
        "host_label": "discord" if is_discord else "other",
        "title_available": bool(title.strip()),
        "url_output": "omitted",
        "title_output": "omitted",
        "raw_identifiers_output": "omitted",
    }


def next_action_for_state(*, chrome_opened: bool, location: dict[str, Any], human_state: str, confirm_decision: str) -> str:
    if not chrome_opened:
        return "Codex が Chrome で Discord を開いてから、人間が目的の場所へ移動します。"
    if not location["is_discord"]:
        return "Chrome で Discord を開いてください。"
    if human_state != "ready":
        return "人間が Chrome 上で目的のサーバー、チャンネル、スレッドへ移動するまで待ちます。"
    if confirm_decision == "approved":
        return "このChrome状態を入口として13工程へ接続します。"
    if confirm_decision == "rejected":
        return "対象が違うため、Chrome 上で移動し直してください。"
    return "Codex が現在のChrome状態を要約し、人間に「ここで開始してよいか」を確認します。"


def build_ingress_payload(
    *,
    chrome_opened: bool,
    current_url: str,
    current_title: str = "",
    human_state: str = "navigating",
    confirm_decision: str = "pending",
) -> dict[str, Any]:
    location = classify_discord_location(current_url, current_title)
    allowed_human_states = {"navigating", "ready"}
    allowed_decisions = {"pending", "approved", "rejected"}
    state_valid = human_state in allowed_human_states and confirm_decision in allowed_decisions
    ready_for_bridge = (
        state_valid
        and chrome_opened
        and location["is_discord"]
        and human_state == "ready"
        and confirm_decision == "approved"
    )
    blocked_stage = None
    if not ready_for_bridge:
        if not state_valid:
            blocked_stage = "invalid_ingress_state"
        elif not chrome_opened:
            blocked_stage = "chrome_not_opened"
        elif not location["is_discord"]:
            blocked_stage = "discord_not_open"
        elif human_state != "ready":
            blocked_stage = "human_navigation_pending"
        elif confirm_decision != "approved":
            blocked_stage = "human_confirmation_pending" if confirm_decision == "pending" else "human_rejected_target"
    steps = [
        {"name": "01_trigger_discord_check", "ok": True, "trigger": "codex_discord_check"},
        {"name": "02_open_chrome_discord", "ok": chrome_opened, "url_output": "omitted"},
        {"name": "03_human_navigates_in_chrome", "ok": human_state == "ready", "human_state": human_state},
        {"name": "04_observe_current_chrome_state", "ok": location["is_discord"], **location},
        {
            "name": "05_confirm_target_with_human",
            "ok": confirm_decision == "approved",
            "decision": confirm_decision,
            "options": ["approved", "pending", "rejected"],
        },
        {
            "name": "06_connect_to_13_step_bridge",
            "ok": ready_for_bridge,
            "bridge_ready": ready_for_bridge,
            "raw_text_output": "omitted",
        },
    ]
    return {
        "schema": "codex_discord_ingress_smoke.v1",
        "language": "ja",
        "ok": ready_for_bridge,
        "stage": "ready_for_bridge" if ready_for_bridge else "blocked",
        "blocked_stage": blocked_stage,
        "next_action": next_action_for_state(
            chrome_opened=chrome_opened,
            location=location,
            human_state=human_state,
            confirm_decision=confirm_decision,
        ),
        "steps": steps,
        "safety_boundary": {
            "manual_text_copy_required": False,
            "discord_app_required": False,
            "discord_send_reaction_edit_delete": "disabled",
            "raw_url_output": "omitted",
            "raw_title_output": "omitted",
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "outbound_actions": "disabled",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex/Chrome から始まる Discord ingress 契約を本文なしで確認する。")
    parser.add_argument("--chrome-opened", action="store_true", help="Codex が Chrome で Discord を開いた状態として扱う")
    parser.add_argument("--current-url", default="https://discord.com/channels/@me", help="現在タブURL。出力には表示しない")
    parser.add_argument("--current-title", default="Discord", help="現在タブtitle。出力には表示しない")
    parser.add_argument("--human-state", choices=["navigating", "ready"], default="navigating")
    parser.add_argument("--confirm-decision", choices=["pending", "approved", "rejected"], default="pending")
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Codex Discord ingress smoke")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"stage: {payload['stage']}")
    if payload.get("blocked_stage"):
        print(f"blocked_stage: {payload['blocked_stage']}")
    print(f"next_action: {payload['next_action']}")
    for item in payload["steps"]:
        status = "pass" if item["ok"] else "wait"
        print(f"- {item['name']}: {status}")
    print("manual_text_copy_required: false")
    print("discord_app_required: false")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_ingress_payload(
        chrome_opened=args.chrome_opened,
        current_url=args.current_url,
        current_title=args.current_title,
        human_state=args.human_state,
        confirm_decision=args.confirm_decision,
    )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
