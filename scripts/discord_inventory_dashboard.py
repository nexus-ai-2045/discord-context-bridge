#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import discord_bot_route_preflight
from discord_context_bridge.core import audit_event_store, load_events
from route_timing_log import read_entries, summarize_entries


TEXT_EVENT_SUFFIXES = {".jsonl", ".ndjson", ".txt"}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ogg", ".mp3", ".mp4", ".mov", ".wav"}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)


def parse_ahead_behind(raw: str) -> tuple[int, int]:
    parts = raw.strip().split()
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def current_upstream() -> str | None:
    completed = run_command(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    upstream = completed.stdout.strip()
    return upstream if completed.returncode == 0 and upstream else None


def ensure_github_account_boundary() -> str | None:
    completed = run_command([sys.executable, "scripts/gh_guard.py", "--account-only", "--json"])
    if completed.returncode == 0:
        return None
    return "gh_account_boundary_failed"


def load_open_prs() -> tuple[list[dict[str, Any]], str | None]:
    account_error = ensure_github_account_boundary()
    if account_error:
        return [], account_error
    completed = run_command(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,headRefName,mergeStateStatus,isDraft,url",
            "--limit",
            "50",
        ]
    )
    if completed.returncode != 0:
        return [], "gh_pr_list_failed"
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return [], "gh_pr_list_invalid_json"
    return payload if isinstance(payload, list) else [], None


def build_operational_status() -> dict[str, Any]:
    status = run_command(["git", "status", "--short"]).stdout.strip()
    branch = run_command(["git", "branch", "--show-current"]).stdout.strip()
    origin_main = run_command(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"])
    origin_main_ahead, origin_main_behind = parse_ahead_behind(origin_main.stdout)
    upstream = current_upstream()
    if upstream:
        upstream_counts = run_command(["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
        branch_ahead, branch_behind = parse_ahead_behind(upstream_counts.stdout)
    else:
        branch_ahead, branch_behind = origin_main_ahead, origin_main_behind
    open_prs, pr_error = load_open_prs()

    residual: list[dict[str, Any]] = []
    broken: list[str] = []
    blocked: list[str] = []
    done = [
        "public-safe inventory",
        "metadata-only store/timing summary",
        "secret/path/raw-text omission",
    ]
    next_actions: list[str] = []

    if status:
        residual.append({"code": "working_tree_dirty", "label": "未commitの差分があります。"})
        next_actions.append("差分を確認し、必要なものだけ commit / push します。")
    if branch_behind:
        residual.append({"code": "branch_behind_upstream", "label": f"local branch は upstream から {branch_behind} commit 遅れています。"})
        next_actions.append("local branch を upstream に同期します。")
    if branch_ahead:
        residual.append({"code": "local_commits_unpushed", "label": f"local branch は upstream より {branch_ahead} commit 進んでいます。"})
        next_actions.append("GitHub account と remote owner を確認して push します。")
    if open_prs:
        residual.append({"code": "open_pull_requests", "label": f"open PR が {len(open_prs)} 件あります。"})
        next_actions.append("open PR の CI / merge state を確認し、merge または close 判断をします。")
    if pr_error:
        blocked.append("GitHub PR 状態を取得できませんでした。")
        residual.append({"code": pr_error, "label": "GitHub PR 状態の確認が未完了です。"})
        next_actions.append("gh auth / network を確認して PR 状態を再測します。")

    if residual:
        state = "attention_required"
        now_label = "残務があります。"
    elif blocked:
        state = "blocked"
        now_label = "確認不能な残務があります。"
    else:
        state = "done"
        now_label = "ローカル観測範囲では残務ゼロです。"

    return {
        "schema": "discord_inventory_operational_status.v1",
        "now": {
            "state": state,
            "label": now_label,
            "branch": branch,
            "working_tree": "clean" if not status else "dirty",
            "branch_upstream": upstream or "",
            "branch_upstream_ahead": branch_ahead,
            "branch_upstream_behind": branch_behind,
            "origin_main_ahead": origin_main_ahead,
            "origin_main_behind": origin_main_behind,
        },
        "done": done,
        "broken": broken,
        "blocked": blocked,
        "next": sorted(set(next_actions)),
        "github": {
            "open_pr_count": len(open_prs),
            "open_prs": [
                {
                    "number": item.get("number"),
                    "head_ref": item.get("headRefName"),
                    "merge_state": item.get("mergeStateStatus"),
                    "is_draft": item.get("isDraft"),
                    "title": item.get("title"),
                    "url": item.get("url"),
                }
                for item in open_prs
            ],
            "error": pr_error,
        },
        "residual": residual,
        "residual_count": len(residual),
        "safety_boundary": {
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "token_output": "omitted",
            "local_paths_output": "omitted",
            "outbound_actions": "disabled",
        },
    }


def count_suffixes(root: Path, suffixes: set[str]) -> dict[str, int]:
    counts = {suffix: 0 for suffix in sorted(suffixes)}
    if not root.exists():
        return counts
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in counts:
            counts[path.suffix.lower()] += 1
    return counts


def compact_nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {suffix: count for suffix, count in counts.items() if count}


def inbox_inventory(channel_dir: Path) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    inbox_dir = channel_dir / "inbox"
    text_counts = compact_nonzero(count_suffixes(inbox_dir, TEXT_EVENT_SUFFIXES))
    media_counts = compact_nonzero(count_suffixes(inbox_dir, MEDIA_SUFFIXES))
    text_event_candidates = sum(text_counts.values())
    media_inbox_count = sum(media_counts.values())
    return {
        "inbox_present": inbox_dir.exists(),
        "preflight_ok": bool(preflight["ok"]),
        "text_event_candidates": text_event_candidates,
        "text_event_suffix_counts": text_counts,
        "media_inbox_count": media_inbox_count,
        "media_suffix_counts": media_counts,
        "file_names_output": "omitted",
        "text_output": "omitted",
        "next": "pipe_text_event_to_main_route" if text_event_candidates else "wait_for_or_provide_private_text_event",
    }


def store_inventory(tmp_dir: Path, *, limit: int) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    for path in sorted(tmp_dir.glob("dcb-*.ndjson"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            events = load_events(path)
            audit = audit_event_store(path)
        except Exception as exc:
            stores.append(
                {
                    "name": path.name,
                    "readable": False,
                    "reason": type(exc).__name__,
                    "text_output": "omitted",
                }
            )
            continue
        channels = sorted({event.channel_label for event in events})
        guilds = sorted({event.guild_label for event in events})
        stores.append(
            {
                "name": path.name,
                "readable": True,
                "event_count": len(events),
                "guild_count": len(guilds),
                "channel_count": len(channels),
                "channel_labels_sample": channels[:3],
                "safe_for_tunnel": bool(audit.get("safe_for_tunnel")),
                "issue_count": int(audit.get("issue_count", 0)),
                "text_output": "omitted",
                "participant_names_output": "omitted",
            }
        )
        if len(stores) >= limit:
            break
    return stores


def timing_inventory(tmp_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for path in sorted(tmp_dir.glob("dcb-route-timing*.jsonl")):
        summary = summarize_entries(read_entries(path))
        summaries.append(
            {
                "name": path.name,
                "entry_count": summary["entry_count"],
                "routes": summary["routes"],
                "text_output": "omitted",
            }
        )
    return summaries


def build_inventory(channel_dir: Path, tmp_dir: Path, *, store_limit: int) -> dict[str, Any]:
    stores = store_inventory(tmp_dir, limit=store_limit)
    unsafe_stores = [store["name"] for store in stores if store.get("readable") and not store.get("safe_for_tunnel")]
    operational = build_operational_status()
    return {
        "schema": "discord_inventory_dashboard.v1",
        "ok": operational["residual_count"] == 0 and not operational["blocked"],
        "operational_status": operational,
        "channel_dir": {
            "configured": channel_dir.exists(),
            "path_output": "omitted",
        },
        "inbox": inbox_inventory(channel_dir),
        "stores": {
            "tmp_dir": "omitted",
            "shown_count": len(stores),
            "unsafe_store_count": len(unsafe_stores),
            "unsafe_store_names": unsafe_stores[:10],
            "items": stores,
        },
        "timing_logs": timing_inventory(tmp_dir),
        "safety_boundary": {
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "token_output": "omitted",
            "snowflake_values_output": "omitted",
            "inbox_file_names_output": "omitted",
            "outbound_actions": "disabled",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord 受信箱・既取得store・速度ログを本文なしで棚卸しする。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--tmp-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--store-limit", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    inbox = payload["inbox"]
    stores = payload["stores"]
    operational = payload["operational_status"]
    print("Discord inventory dashboard")
    print(f"now: {operational['now']['state']} - {operational['now']['label']}")
    print(f"residual_count: {operational['residual_count']}")
    print(f"text_event_candidates: {inbox['text_event_candidates']}")
    print(f"media_inbox_count: {inbox['media_inbox_count']}")
    print(f"stores_shown: {stores['shown_count']}")
    print(f"unsafe_store_count: {stores['unsafe_store_count']}")
    print(f"timing_logs: {len(payload['timing_logs'])}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_inventory(args.channel_dir, args.tmp_dir, store_limit=args.store_limit)
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
