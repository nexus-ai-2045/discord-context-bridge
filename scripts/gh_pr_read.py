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

from discord_context_bridge.process_runner import ProcessResult, run_process


def run_command(command: list[str]) -> ProcessResult:
    return run_process(command, cwd=ROOT, timeout=30)


def ensure_account_boundary() -> dict[str, Any]:
    command = [sys.executable, "scripts/gh_guard.py", "--account-only", "--json"]
    completed = run_command(command)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {}
    if completed.returncode != 0:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "error": "gh_account_boundary_failed",
                    "message": "GitHub active account が repository owner と一致していないため、PR 読み取りを止めました。",
                    "guard": payload,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    return payload


def load_prs(limit: int) -> list[dict[str, Any]]:
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
            str(limit),
        ]
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or completed.stdout.strip())
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise SystemExit("gh pr list returned non-list JSON")
    return payload


def load_pr(number: str) -> dict[str, Any]:
    completed = run_command(
        [
            "gh",
            "pr",
            "view",
            number,
            "--json",
            "number,title,url,state,isDraft,mergeStateStatus,headRefName,baseRefName,additions,deletions,changedFiles,statusCheckRollup",
        ]
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or completed.stdout.strip())
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise SystemExit("gh pr view returned non-object JSON")
    return payload


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    guard = ensure_account_boundary()
    if args.command == "list":
        return {
            "ok": True,
            "schema": "gh_pr_read.v1",
            "command": "list",
            "guard": {
                "actual_owner": guard.get("actual_owner"),
                "actual_repository": guard.get("actual_repository"),
                "active_account": guard.get("active_account_after"),
                "ok": guard.get("ok"),
            },
            "prs": load_prs(args.limit),
        }
    return {
        "ok": True,
        "schema": "gh_pr_read.v1",
        "command": "view",
        "guard": {
            "actual_owner": guard.get("actual_owner"),
            "actual_repository": guard.get("actual_repository"),
            "active_account": guard.get("active_account_after"),
            "ok": guard.get("ok"),
        },
        "pr": load_pr(args.number),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GitHub PR を account guard 通過後に読み取る")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="open PR 一覧を取得する")
    list_parser.add_argument("--limit", type=int, default=50)
    view_parser = subparsers.add_parser("view", help="PR 詳細を取得する")
    view_parser.add_argument("number")
    return parser.parse_args()


def main() -> int:
    print(json.dumps(build_payload(parse_args()), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
