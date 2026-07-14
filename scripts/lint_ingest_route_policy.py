#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs" / "operating-contract.md"
DEFAULT_GENERATED_SKILLS = ROOT / "dist" / "skills"
DEFAULT_LOCAL_CLAUDE_SKILL = Path.home() / ".claude" / "skills" / "discord-context-bridge" / "SKILL.md"

REQUIRED_PHRASES = (
    "Playwright",
    "既定経路にしない",
    "cic",
    "Discord Desktop cache",
    "macOS Accessibility",
    "ai-party",
    "外部 MCP",
    "自動探索しない",
    "Computer Use 的な画面操作",
    "SendKeys",
    "AppActivate",
    "スクショ取得",
    "Chromeを勝手に開く",
    "ユーザーの明示許可なしに実行しない",
    "no_unapproved_visible_ui_automation",
    "no_ocr_for_dcb_text_intake",
    "no_clipboard_without_explicit_clipboard_request",
    "raw本文は private artifact / local store に保存する",
    "visible output には raw本文を貼らず",
)


def check_file(path: Path, *, required_phrases: tuple[str, ...] = REQUIRED_PHRASES) -> dict[str, Any]:
    if not path.exists():
        return {"status": "error", "reason": "missing", "path": str(path), "read_only": True}
    text = path.read_text(encoding="utf-8")
    missing = [phrase for phrase in required_phrases if phrase not in text]
    if missing:
        return {
            "status": "error",
            "reason": "required_phrase_missing",
            "path": str(path),
            "missing": missing,
            "read_only": True,
        }
    return {"status": "ok", "reason": "policy_present", "path": str(path), "read_only": True}


def lint_ingest_route_policy(
    *,
    contract: Path = DEFAULT_CONTRACT,
    generated_skills_dir: Path = DEFAULT_GENERATED_SKILLS,
    local_claude_skill: Path = DEFAULT_LOCAL_CLAUDE_SKILL,
    include_local_claude_skill: bool = True,
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {"contract": check_file(contract)}
    for skill in sorted(generated_skills_dir.glob("*/SKILL.md")):
        checks[f"generated:{skill.parent.name}"] = check_file(skill)
    if include_local_claude_skill:
        checks["local:claude-code"] = check_file(local_claude_skill)
    if not any(key.startswith("generated:") for key in checks):
        checks["generated"] = {
            "status": "error",
            "reason": "no_generated_skills",
            "path": str(generated_skills_dir),
            "read_only": True,
        }
    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "ok"
    return {
        "schema": "discord_context_bridge_ingest_route_policy_lint.v1",
        "overall": overall,
        "checks": checks,
        "read_only": True,
        "policy": "Playwright and unapproved visible UI automation are not default ingest routes for Discord context bridge.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discord 文脈取得で Playwright を既定経路にしない運用を read-only で確認する"
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--generated-skills-dir", type=Path, default=DEFAULT_GENERATED_SKILLS)
    parser.add_argument("--local-claude-skill", type=Path, default=DEFAULT_LOCAL_CLAUDE_SKILL)
    parser.add_argument("--skip-local-claude-skill", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = lint_ingest_route_policy(
        contract=args.contract,
        generated_skills_dir=args.generated_skills_dir,
        local_claude_skill=args.local_claude_skill,
        include_local_claude_skill=not args.skip_local_claude_skill,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall={report['overall']}")
        for name, item in report["checks"].items():
            print(f"{name}={item['status']} {item.get('reason', '')}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
