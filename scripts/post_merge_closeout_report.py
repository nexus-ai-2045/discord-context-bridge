#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REPOSITORY = "nexus-ai-2045/discord-context-bridge"


def run(command: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)


def check(status: str, value: str | None = None, detail: str | None = None) -> dict[str, str | None]:
    payload: dict[str, str | None] = {"status": status}
    if value is not None:
        payload["value"] = value
    if detail is not None:
        payload["detail"] = detail
    return payload


def load_pr(pr: str) -> tuple[dict[str, Any] | None, str | None]:
    completed = run(
        [
            "gh",
            "pr",
            "view",
            pr,
            "--repo",
            EXPECTED_REPOSITORY,
            "--json",
            "number,state,isDraft,mergedAt,mergeCommit,url,title,baseRefName,headRefName",
        ]
    )
    if completed.returncode != 0:
        return None, (completed.stderr or "gh pr view failed").strip()
    return json.loads(completed.stdout), None


def rev_parse(ref: str) -> str | None:
    completed = run(["git", "rev-parse", "--verify", ref])
    return completed.stdout.strip() if completed.returncode == 0 else None


def build_report(pr: str, *, fetch: bool = False) -> dict[str, Any]:
    checks: dict[str, dict[str, str | None]] = {}
    if fetch:
        fetched = run(["git", "fetch", "origin", "--prune"])
        checks["fetch"] = check("ok" if fetched.returncode == 0 else "error", "origin", fetched.stderr.strip() or None)

    pr_data, pr_error = load_pr(pr)
    if pr_data:
        merge_commit = pr_data.get("mergeCommit") or {}
        merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
        checks["pr_merged"] = check(
            "ok" if pr_data.get("state") == "MERGED" and merge_oid else "error",
            str(pr_data.get("state")),
            None if pr_data.get("state") == "MERGED" else "PR is not merged",
        )
        checks["merge_commit"] = check("ok" if merge_oid else "error", merge_oid)
    else:
        checks["pr_merged"] = check("error", pr, pr_error)
        checks["merge_commit"] = check("error", None)

    current_branch = run(["git", "branch", "--show-current"]).stdout.strip()
    checks["current_branch"] = check("ok" if current_branch == "main" else "warning", current_branch, None if current_branch == "main" else "post-merge closeout should usually run on main")

    status = run(["git", "status", "--short"]).stdout.strip()
    checks["working_tree"] = check("ok" if not status else "error", "clean" if not status else status)

    local_main = rev_parse("main")
    remote_main = rev_parse("origin/main")
    checks["main_sync"] = check(
        "ok" if local_main and remote_main and local_main == remote_main else "error",
        "main == origin/main" if local_main and remote_main and local_main == remote_main else "main != origin/main",
    )

    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "warning" if any(item["status"] == "warning" for item in checks.values()) else "ok"
    return {
        "schema": "discord_context_bridge_post_merge_closeout.v1",
        "overall": overall,
        "repository": EXPECTED_REPOSITORY,
        "pr": pr_data or {"id": pr},
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discord Context Bridge 専用の post-merge closeout report")
    parser.add_argument("--pr", required=True, help="PR number or URL")
    parser.add_argument("--fetch", action="store_true", help="確認前に git fetch --prune を実行する")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.pr, fetch=args.fetch)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall={report['overall']}")
        for name, item in report["checks"].items():
            print(f"{name}={item['status']} {item.get('value', '')}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
