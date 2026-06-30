#!/usr/bin/env python3
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


def run(command: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)


def check(status: str, value: str | None = None, detail: str | None = None) -> dict[str, str | None]:
    payload: dict[str, str | None] = {"status": status}
    if value is not None:
        payload["value"] = value
    if detail is not None:
        payload["detail"] = detail
    return payload


def parse_github_remote(remote_url: str) -> tuple[str | None, str | None]:
    patterns = [
        r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
        r"https?://[^/]*github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, remote_url.strip())
        if match:
            return match.group("owner"), match.group("repo")
    return None, None


def gh_login() -> tuple[str | None, str | None]:
    completed = run(["gh", "api", "user", "--jq", ".login"])
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip(), None
    return None, (completed.stderr or "gh api user failed").strip()


def default_branch(owner: str, repository: str) -> tuple[str, str | None]:
    completed = run(
        [
            "gh",
            "repo",
            "view",
            f"{owner}/{repository}",
            "--json",
            "defaultBranchRef",
            "--jq",
            ".defaultBranchRef.name",
        ]
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip(), None
    return "main", (completed.stderr or "default branch lookup failed").strip()


def rev_parse(ref: str) -> str | None:
    completed = run(["git", "rev-parse", "--verify", ref])
    return completed.stdout.strip() if completed.returncode == 0 else None


def build_report(remote: str = "origin", *, fetch: bool = False) -> dict[str, Any]:
    checks: dict[str, dict[str, str | None]] = {}
    if fetch:
        fetched = run(["git", "fetch", remote, "--prune"])
        checks["fetch"] = check("ok" if fetched.returncode == 0 else "error", remote, fetched.stderr.strip() or None)

    remote_result = run(["git", "remote", "get-url", remote])
    remote_url = remote_result.stdout.strip()
    owner, repository = parse_github_remote(remote_url)
    checks["remote"] = check("ok" if owner and repository else "error", remote_url or None)
    checks["target_repository"] = check(
        "ok" if owner == EXPECTED_OWNER and repository == EXPECTED_REPOSITORY else "error",
        f"{owner}/{repository}" if owner and repository else None,
        f"expected {EXPECTED_OWNER}/{EXPECTED_REPOSITORY}",
    )

    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    checks["branch"] = check("ok" if branch and branch not in {"main", "master"} else "error", branch or None)

    status = run(["git", "status", "--short"]).stdout.strip()
    checks["working_tree"] = check("ok" if not status else "error", "clean" if not status else status)

    login, login_error = gh_login()
    checks["gh_login"] = check("ok" if login == EXPECTED_OWNER else "error", login, login_error)

    base_name = "main"
    if owner and repository:
        base_name, branch_error = default_branch(owner, repository)
        checks["default_branch"] = check("ok" if not branch_error else "warning", base_name, branch_error)
    else:
        checks["default_branch"] = check("error", None)

    base_ref = f"{remote}/{base_name}"
    base_sha = rev_parse(base_ref)
    head_sha = rev_parse("HEAD")
    if base_sha and head_sha:
        ancestor = run(["git", "merge-base", "--is-ancestor", base_ref, "HEAD"])
        checks["pr_base_selection"] = check(
            "ok" if ancestor.returncode == 0 else "error",
            base_ref,
            None
            if ancestor.returncode == 0
            else "PR元ブランチが現在の基準ブランチを含んでいません。基準ブランチからclean branchを作り、レビュー対象の変更だけを再適用してください。",
        )
    else:
        checks["pr_base_selection"] = check("error", base_ref, "base or HEAD revision could not be resolved")

    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "warning" if any(item["status"] == "warning" for item in checks.values()) else "ok"
    return {
        "schema": "discord_context_bridge_pr_readiness.v1",
        "overall": overall,
        "expected_repository": f"{EXPECTED_OWNER}/{EXPECTED_REPOSITORY}",
        "remote": remote,
        "branch": branch,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discord Context Bridge 専用の PR readiness preflight")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--fetch", action="store_true", help="確認前に git fetch --prune を実行する")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.remote, fetch=args.fetch)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall={report['overall']}")
        for name, item in report["checks"].items():
            print(f"{name}={item['status']} {item.get('value', '')}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
