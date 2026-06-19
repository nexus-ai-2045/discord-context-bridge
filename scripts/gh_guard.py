from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def parse_github_owner(remote_url: str) -> str | None:
    patterns = [
        r"github\.com[:/](?P<owner>[^/\s]+)/[^/\s]+(?:\.git)?$",
        r"https?://[^/]*github\.com/(?P<owner>[^/\s]+)/[^/\s]+(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, remote_url.strip())
        if match:
            return match.group("owner")
    return None


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def get_remote_url(remote: str) -> str:
    completed = run(["git", "remote", "get-url", remote])
    if completed.returncode != 0:
        raise SystemExit("git remote の取得に失敗しました: " + completed.stderr.strip())
    return completed.stdout.strip()


def get_active_gh_account() -> tuple[str, bool, str]:
    completed = run(["gh", "auth", "status"])
    output = completed.stdout + completed.stderr
    account = parse_active_account(output)
    if not account:
        raise SystemExit("gh active account を判定できません: " + output.strip())
    return account, completed.returncode == 0, output.strip()


def parse_active_account(auth_status_output: str) -> str | None:
    current_account: str | None = None
    for raw_line in auth_status_output.splitlines():
        line = raw_line.strip()
        match = re.search(r"(?:Logged in|Failed to log in) to github\.com account (?P<account>\S+)", line)
        if match:
            current_account = match.group("account")
            continue
        if line == "- Active account: true" and current_account:
            return current_account
    return None


def switch_gh_account(owner: str) -> None:
    completed = run(["gh", "auth", "switch", "-u", owner])
    if completed.returncode != 0:
        raise SystemExit("gh account の切り替えに失敗しました: " + completed.stderr.strip())


def build_report(remote: str, *, switch: bool, account_only: bool = False) -> dict[str, Any]:
    remote_url = get_remote_url(remote)
    owner = parse_github_owner(remote_url)
    if not owner:
        raise SystemExit("GitHub remote owner を判定できません: " + remote_url)
    before, auth_status_ok_before, _ = get_active_gh_account()
    switched = False
    if before != owner and switch:
        switch_gh_account(owner)
        switched = True
    after, auth_status_ok_after, _ = get_active_gh_account()
    account_matches = after == owner
    ok = account_matches if account_only else account_matches and auth_status_ok_after
    return {
        "language": "ja",
        "remote": remote,
        "remote_url": remote_url,
        "expected_owner": owner,
        "active_account_before": before,
        "active_account_after": after,
        "auth_status_ok_before": auth_status_ok_before,
        "auth_status_ok_after": auth_status_ok_after,
        "switched": switched,
        "ok": ok,
        "message": "GitHub account は remote owner と一致しています。" if account_matches else "GitHub account が remote owner と一致していません。",
        "auth_message": "GitHub token は利用可能です。" if auth_status_ok_after else "GitHub token の利用確認が通っていません。",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GitHub remote owner と gh active account を照合する")
    parser.add_argument("--remote", default="origin", help="確認する git remote")
    parser.add_argument("--switch", action="store_true", help="不一致の場合に gh auth switch で remote owner へ切り替える")
    parser.add_argument("--account-only", action="store_true", help="token 利用可否ではなく active account の一致だけを見る")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(args.remote, switch=args.switch, account_only=args.account_only)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(report["message"])
        print(report["auth_message"])
        print(f"remote owner: {report['expected_owner']}")
        print(f"active account: {report['active_account_after']}")
        if report["switched"]:
            print("gh active account を切り替えました。")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
