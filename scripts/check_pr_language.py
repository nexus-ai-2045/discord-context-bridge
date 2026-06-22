#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


JAPANESE_RE = re.compile(r"[一-龥ぁ-んァ-ン]")
REQUIRED_BODY_HEADINGS = ("## 概要", "## 検証", "## 境界")
ENGLISH_DEFAULT_HEADINGS = ("## Summary", "## Validation", "## Notes")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def japanese_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    japanese = [char for char in letters if JAPANESE_RE.search(char)]
    return len(japanese) / len(letters)


def load_pr_with_gh(pr_number_or_url: str) -> tuple[str, str]:
    completed = subprocess.run(
        ["gh", "pr", "view", pr_number_or_url, "--json", "title,body"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError("gh pr view に失敗しました。")
    payload = json.loads(completed.stdout)
    return str(payload.get("title") or ""), str(payload.get("body") or "")


def validate_pr_language(title: str, body: str) -> list[str]:
    issues: list[str] = []
    visible = f"{title}\n{body}"

    if not JAPANESE_RE.search(title):
        issues.append("title_has_no_japanese")
    if not JAPANESE_RE.search(body):
        issues.append("body_has_no_japanese")
    for heading in REQUIRED_BODY_HEADINGS:
        if heading not in body:
            issues.append(f"missing_heading:{heading}")
    for heading in ENGLISH_DEFAULT_HEADINGS:
        if heading in body:
            issues.append(f"english_default_heading:{heading}")
    if japanese_ratio(visible) < 0.15:
        issues.append("japanese_ratio_too_low")
    return issues


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PR title/body が日本語既定に沿うか確認する。")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", help="gh pr view で確認する PR 番号または URL")
    source.add_argument("--body-file", type=Path, help="PR body markdown file")
    parser.add_argument("--title", help="PR title。--body-file の場合は必須")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.pr:
            title, body = load_pr_with_gh(args.pr)
        else:
            if not args.title:
                raise RuntimeError("--body-file には --title が必要です。")
            title = args.title
            body = args.body_file.read_text(encoding="utf-8")
        issues = validate_pr_language(title, body)
    except Exception as exc:
        print(_json({"ok": False, "issues": ["pr_language_check_failed"], "reason": str(exc)}))
        return 2

    print(
        _json(
            {
                "ok": not issues,
                "issue_count": len(issues),
                "issues": issues,
                "title_has_japanese": bool(JAPANESE_RE.search(title)),
                "body_japanese_ratio": round(japanese_ratio(body), 3),
            }
        )
    )
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
