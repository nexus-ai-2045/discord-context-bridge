#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SCOPE_OUT_PATTERNS = (
    r"4者",
    r"羊",
    r"multi-party",
    r"secret-room",
    r"discord_multi_party",
)
DEFAULT_RETAIN_ONLY_PATTERNS = (
    r"(^|/)extracts/",
    r"(^|/)\.local/",
)
SENSITIVE_PATH_PATTERNS = (
    r"(^|/)extracts/",
    r"(^|/)\.local/",
    r"[A-Za-z]:\\Users\\",
    r"/Users/",
    r"/home/",
)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def run(command: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)


def git_text(args: list[str]) -> str:
    completed = run(["git", *args])
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "git command failed").strip())
    return completed.stdout


def changed_files(base: str, head: str) -> list[dict[str, str]]:
    output = git_text(["diff", "--name-status", f"{base}..{head}"])
    files: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0], "path": parts[-1]})
    return files


def diff_text(base: str, head: str) -> str:
    return git_text(["diff", "--no-ext-diff", "--unified=0", f"{base}..{head}"])


def diff_text_for_paths(base: str, head: str, paths: list[str]) -> str:
    if not paths:
        return ""
    return git_text(["diff", "--no-ext-diff", "--unified=0", f"{base}..{head}", "--", *paths])


def compile_patterns(raw_patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in raw_patterns]


def matching_patterns(text: str, patterns: list[re.Pattern[str]]) -> list[str]:
    return [pattern.pattern for pattern in patterns if pattern.search(text)]


def added_unexpected_surface(files: list[dict[str, str]], allowed_new_paths: set[str]) -> list[str]:
    unexpected: list[str] = []
    for item in files:
        status = item["status"]
        path = item["path"].replace("\\", "/")
        if not status.startswith("A"):
            continue
        if path in allowed_new_paths:
            continue
        if path.startswith(("scripts/", "tests/", "docs/")) or path in {".github/workflows/pr-japanese-gate.yml"}:
            unexpected.append(path)
    return unexpected


def build_report(
    *,
    base: str,
    head: str,
    scope_patterns: list[str],
    retain_only_patterns: list[str],
    max_files: int,
    allowed_new_paths: set[str],
    allowed_scope_paths: set[str],
) -> dict[str, Any]:
    files = changed_files(base, head)
    excluded_patch_paths = allowed_new_paths | allowed_scope_paths
    review_paths = [item["path"] for item in files if item["path"].replace("\\", "/") not in excluded_patch_paths]
    patch = diff_text_for_paths(base, head, review_paths)
    path_blob = "\n".join(item["path"] for item in files)

    scope_regex = compile_patterns(scope_patterns)
    retain_regex = compile_patterns(retain_only_patterns)
    sensitive_regex = compile_patterns(list(SENSITIVE_PATH_PATTERNS))

    checks: dict[str, dict[str, Any]] = {}
    checks["diff_file_count"] = {
        "status": "ok" if len(files) <= max_files else "error",
        "value": len(files),
        "limit": max_files,
    }

    scope_path_hits = matching_patterns(path_blob, scope_regex)
    scope_patch_hits = matching_patterns(patch, scope_regex)
    checks["scope_out_keywords"] = {
        "status": "ok" if not scope_path_hits and not scope_patch_hits else "error",
        "path_patterns": scope_path_hits,
        "patch_patterns": scope_patch_hits,
    }

    retain_hits = matching_patterns(path_blob, retain_regex)
    checks["retain_only_paths"] = {
        "status": "ok" if not retain_hits else "error",
        "patterns": retain_hits,
        "detail": "repo外へ退避するべきローカル保持物がPR差分に入っています。" if retain_hits else None,
    }

    sensitive_hits = matching_patterns(patch, sensitive_regex)
    checks["sensitive_path_mentions"] = {
        "status": "ok" if not sensitive_hits else "error",
        "patterns": sensitive_hits,
    }

    unexpected_new = added_unexpected_surface(files, allowed_new_paths)
    checks["unexpected_new_review_surface"] = {
        "status": "ok" if not unexpected_new else "error",
        "paths": unexpected_new,
        "detail": "回収PRに新規 script/test/docs が入る場合はスコープ明示が必要です。" if unexpected_new else None,
    }

    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "ok"
    return {
        "schema": "discord_context_bridge_pr_scope_guard.v1",
        "overall": overall,
        "base": base,
        "head": head,
        "files": files,
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PR差分にスコープ外の話題やローカル保持物が混ざっていないか確認する。")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--max-files", type=int, default=12)
    parser.add_argument("--scope-pattern", action="append", default=[], help="追加のスコープ外キーワード正規表現")
    parser.add_argument("--retain-only-pattern", action="append", default=[], help="PRに含めずrepo外退避するpath正規表現")
    parser.add_argument("--allow-new-path", action="append", default=[], help="新規追加を許可するpath")
    parser.add_argument("--allow-scope-path", action="append", default=[], help="スコープ外キーワードfixtureを含むためpatch scanから除外するpath")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(
            base=args.base,
            head=args.head,
            scope_patterns=[*DEFAULT_SCOPE_OUT_PATTERNS, *args.scope_pattern],
            retain_only_patterns=[*DEFAULT_RETAIN_ONLY_PATTERNS, *args.retain_only_pattern],
            max_files=args.max_files,
            allowed_new_paths=set(args.allow_new_path),
            allowed_scope_paths=set(args.allow_scope_path),
        )
    except Exception as exc:
        report = {"schema": "discord_context_bridge_pr_scope_guard.v1", "overall": "error", "error": str(exc)}

    if args.json:
        print(_json(report))
    else:
        print(f"overall={report['overall']}")
        for name, item in report.get("checks", {}).items():
            print(f"{name}={item['status']}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
