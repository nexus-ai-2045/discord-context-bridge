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

import discord_inventory_dashboard
from discord_context_bridge.process_runner import ProcessResult, run_process


GOAL = "discord-context-bridge repo residual zero operational guarantee"


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def run_command(command: list[str], *, timeout: float = 30) -> ProcessResult:
    return run_process(command, cwd=ROOT, timeout=timeout)


def compact_output(text: str, *, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def requirement(name: str, ok: bool, evidence: str, blocker: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": "ok" if ok else "blocked",
        "evidence": evidence,
    }
    if blocker and not ok:
        payload["blocker"] = blocker
    return payload


def run_smoke() -> dict[str, Any]:
    completed = run_command([sys.executable, "scripts/ops_check.py"], timeout=180)
    return {
        "command": "python scripts/ops_check.py",
        "exit_code": completed.returncode,
        "ok": completed.returncode == 0,
        "stdout": compact_output(completed.stdout),
        "stderr": compact_output(completed.stderr),
    }


def build_goal_status(*, run_ops_smoke: bool = False, store_limit: int = 30) -> dict[str, Any]:
    dashboard = discord_inventory_dashboard.build_inventory(
        discord_inventory_dashboard.discord_bot_route_preflight.DEFAULT_CHANNEL_DIR,
        Path("/tmp"),
        store_limit=store_limit,
    )
    operational = dashboard["operational_status"]
    now = operational["now"]
    github = operational["github"]
    residual = operational["residual"]
    smoke = run_smoke() if run_ops_smoke else None

    requirements = [
        requirement(
            "repo_sync_clean",
            now["working_tree"] == "clean"
            and now["origin_main_ahead"] == 0
            and now["origin_main_behind"] == 0,
            "discord_inventory_dashboard.operational_status.now",
            "working tree / origin/main sync is not clean",
        ),
        requirement(
            "no_open_pr",
            github["open_pr_count"] == 0 and github["error"] is None,
            "discord_inventory_dashboard.operational_status.github",
            "open PR or GitHub PR lookup error remains",
        ),
        requirement(
            "metadata_only_safety_boundary",
            dashboard["safety_boundary"]["raw_discord_text_output"] == "omitted"
            and dashboard["safety_boundary"]["outbound_actions"] == "disabled",
            "discord_inventory_dashboard.safety_boundary",
            "raw Discord text or outbound action boundary is not safe",
        ),
    ]
    if smoke is not None:
        requirements.append(
            requirement(
                "ops_smoke",
                bool(smoke["ok"]),
                "python scripts/ops_check.py",
                "ops_check failed",
            )
        )

    ok = dashboard["ok"] and all(item["status"] == "ok" for item in requirements)
    return {
        "schema": "discord_context_bridge_repo_goal_status.v1",
        "goal": GOAL,
        "ok": ok,
        "state": "done" if ok else "attention_required",
        "done_when": [
            "working tree is clean",
            "local branch is synchronized with origin/main",
            "open PR count is zero",
            "dashboard residual_count is zero",
            "raw Discord text, participant names, tokens, local paths, and outbound actions remain omitted/disabled",
            "ops_check succeeds when --run-smoke is used",
        ],
        "requirements": requirements,
        "dashboard": {
            "ok": dashboard["ok"],
            "state": now["state"],
            "residual_count": operational["residual_count"],
            "residual": residual,
            "next": operational["next"],
        },
        "smoke": smoke or {"requested": False},
        "external_actions_performed": False,
        "safety_boundary": dashboard["safety_boundary"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord Context Bridge repo の残務ゼロゴール状態を確認する。")
    parser.add_argument("--run-smoke", action="store_true", help="ops_check.py も実行して証跡に含める。")
    parser.add_argument("--store-limit", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Discord Context Bridge repo goal status")
    print(f"state: {payload['state']}")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"residual_count: {payload['dashboard']['residual_count']}")
    print(f"external_actions_performed: {str(payload['external_actions_performed']).lower()}")
    for item in payload["requirements"]:
        print(f"- {item['name']}: {item['status']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_goal_status(run_ops_smoke=args.run_smoke, store_limit=args.store_limit)
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
