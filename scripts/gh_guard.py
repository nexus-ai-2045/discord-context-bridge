from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OWNER = "nexus-ai-2045"
EXPECTED_REPOSITORY = "discord-context-bridge"
EXPECTED_GIT_USER_NAME = "nexus-ai-2045"
EXPECTED_GIT_USER_EMAIL = "273569186+nexus-ai-2045@users.noreply.github.com"
DISALLOWED_IDENTITIES = ("lm93TRQN5WSL", "say_yas", "sayyas", "tamagoe@gmail.com")


def parse_github_owner(remote_url: str) -> str | None:
    parsed = parse_github_remote(remote_url)
    return parsed[0] if parsed else None


def parse_github_remote(remote_url: str) -> tuple[str, str] | None:
    patterns = [
        r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
        r"https?://[^/]*github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, remote_url.strip())
        if match:
            return match.group("owner"), match.group("repo")
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


def get_git_config(key: str) -> str:
    completed = run(["git", "config", "--get", key])
    return completed.stdout.strip() if completed.returncode == 0 else ""


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


def contains_disallowed_identity(*values: str) -> list[str]:
    joined = "\n".join(values).casefold()
    return [identity for identity in DISALLOWED_IDENTITIES if identity.casefold() in joined]


def build_report(
    remote: str,
    *,
    switch: bool,
    account_only: bool = False,
    expected_owner: str = EXPECTED_OWNER,
    expected_repository: str = EXPECTED_REPOSITORY,
) -> dict[str, Any]:
    remote_url = get_remote_url(remote)
    parsed_remote = parse_github_remote(remote_url)
    if not parsed_remote:
        raise SystemExit("GitHub remote owner を判定できません: " + remote_url)
    owner, repository = parsed_remote
    before, auth_status_ok_before, _ = get_active_gh_account()
    switched = False
    if before != owner and switch:
        switch_gh_account(owner)
        switched = True
    after, auth_status_ok_after, _ = get_active_gh_account()
    git_user_name = get_git_config("user.name")
    git_user_email = get_git_config("user.email")
    account_matches = after == owner
    owner_matches = owner == expected_owner
    repository_matches = repository == expected_repository
    git_author_matches = git_user_name == EXPECTED_GIT_USER_NAME and git_user_email == EXPECTED_GIT_USER_EMAIL
    disallowed = contains_disallowed_identity(remote_url, before, after, git_user_name, git_user_email)
    ok = account_matches and owner_matches and repository_matches and not disallowed
    if not account_only:
        ok = ok and auth_status_ok_after and git_author_matches
    return {
        "language": "ja",
        "remote": remote,
        "remote_url": remote_url,
        "expected_owner": expected_owner,
        "expected_repository": expected_repository,
        "actual_owner": owner,
        "actual_repository": repository,
        "active_account_before": before,
        "active_account_after": after,
        "git_user_name": git_user_name,
        "git_user_email": git_user_email,
        "auth_status_ok_before": auth_status_ok_before,
        "auth_status_ok_after": auth_status_ok_after,
        "owner_matches": owner_matches,
        "repository_matches": repository_matches,
        "git_author_matches": git_author_matches,
        "disallowed_identities": disallowed,
        "switched": switched,
        "ok": ok,
        "message": "GitHub / git 名義は Discord Context Bridge の公開先と一致しています。" if ok else "GitHub / git 名義または repository が Discord Context Bridge の公開先と一致していません。",
        "auth_message": "GitHub token は利用可能です。" if auth_status_ok_after else "GitHub token の利用確認が通っていません。",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GitHub remote owner と gh active account を照合する")
    parser.add_argument("--remote", default="origin", help="確認する git remote")
    parser.add_argument("--expected-owner", default=EXPECTED_OWNER, help="期待する GitHub owner")
    parser.add_argument("--expected-repository", default=EXPECTED_REPOSITORY, help="期待する repository name")
    parser.add_argument("--switch", action="store_true", help="不一致の場合に gh auth switch で remote owner へ切り替える")
    parser.add_argument("--account-only", action="store_true", help="token 利用可否ではなく active account の一致だけを見る")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        args.remote,
        switch=args.switch,
        account_only=args.account_only,
        expected_owner=args.expected_owner,
        expected_repository=args.expected_repository,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(report["message"])
        print(report["auth_message"])
        print(f"remote owner: {report['actual_owner']}")
        print(f"repository: {report['actual_repository']}")
        print(f"active account: {report['active_account_after']}")
        print(f"git author: {report['git_user_name']} <{report['git_user_email']}>")
        if report["disallowed_identities"]:
            print("禁止名義が見つかりました: " + ", ".join(report["disallowed_identities"]))
        if report["switched"]:
            print("gh active account を切り替えました。")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
