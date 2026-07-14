#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_runtime_skill_sync
import verify_ssot_projection


def _has_symlink_component(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def sync_runtime_skills(
    *,
    targets: dict[str, Path],
    generated_dir: Path = lint_runtime_skill_sync.export_runtime_skills.DEFAULT_OUTPUT_DIR,
    apply: bool = False,
) -> dict[str, Any]:
    if not targets:
        return {
            "schema": "discord_context_bridge_runtime_skill_sync.v1",
            "overall": "error",
            "reason": "no_targets",
            "applied": False,
        }
    if apply and len(targets) != 1:
        return {
            "schema": "discord_context_bridge_runtime_skill_sync.v1",
            "overall": "error",
            "reason": "apply_requires_single_target",
            "target_count": len(targets),
            "applied": False,
        }
    manifest = lint_runtime_skill_sync.export_runtime_skills.load_manifest(
        lint_runtime_skill_sync.export_runtime_skills.DEFAULT_MANIFEST
    )
    allowed_runtimes = set(manifest["runtime_targets"])
    invalid_runtimes = sorted(runtime for runtime in targets if runtime not in allowed_runtimes)
    if invalid_runtimes:
        return {
            "schema": "discord_context_bridge_runtime_skill_sync.v1",
            "overall": "error",
            "reason": "invalid_runtime",
            "runtimes": invalid_runtimes,
            "allowed_runtimes": sorted(allowed_runtimes),
            "applied": False,
        }
    unsafe_targets = [
        str(target)
        for target in targets.values()
        if target.name != "SKILL.md"
        or target.parent.name != "discord-context-bridge"
        or _has_symlink_component(target)
        or (target.exists() and not target.is_file())
    ]
    if unsafe_targets:
        return {
            "schema": "discord_context_bridge_runtime_skill_sync.v1",
            "overall": "error",
            "reason": "unsafe_target",
            "targets": unsafe_targets,
            "applied": False,
        }
    missing_generated = sorted(
        runtime
        for runtime in targets
        if not (generated_dir / runtime / "SKILL.md").is_file()
    )
    if missing_generated:
        return {
            "schema": "discord_context_bridge_runtime_skill_sync.v1",
            "overall": "error",
            "reason": "generated_skill_missing",
            "runtimes": missing_generated,
            "applied": False,
        }
    projection = verify_ssot_projection.verify_projection(output_dir=generated_dir)
    if projection["overall"] != "ok":
        return {
            "schema": "discord_context_bridge_runtime_skill_sync.v1",
            "overall": "error",
            "reason": "projection_invalid",
            "projection": projection,
            "applied": False,
        }
    before = lint_runtime_skill_sync.lint_runtime_skill_sync(
        targets=targets,
        generated_dir=generated_dir,
    )
    if not apply or before["overall"] == "ok":
        return {
            "schema": "discord_context_bridge_runtime_skill_sync.v1",
            "overall": before["overall"],
            "mode": "check" if not apply else "apply",
            "before": before,
            "applied": False,
        }
    changed: list[str] = []
    for runtime, target in sorted(targets.items()):
        expected = generated_dir / runtime / "SKILL.md"
        if not expected.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
            try:
                shutil.copyfile(expected, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            return {
                "schema": "discord_context_bridge_runtime_skill_sync.v1",
                "overall": "error",
                "reason": "apply_failed",
                "target": str(target),
                "error_type": type(exc).__name__,
                "applied": False,
            }
        changed.append(str(target))
    after = lint_runtime_skill_sync.lint_runtime_skill_sync(
        targets=targets,
        generated_dir=generated_dir,
    )
    return {
        "schema": "discord_context_bridge_runtime_skill_sync.v1",
        "overall": after["overall"],
        "mode": "apply",
        "before": before,
        "after": after,
        "changed": changed,
        "applied": bool(changed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="検証済み dist runtime skill を明示 target へ同期する（既定は read-only）"
    )
    parser.add_argument(
        "--target",
        action="append",
        type=lint_runtime_skill_sync.parse_target,
        default=[],
        metavar="runtime=/path/to/SKILL.md",
        help="同期対象。例: claude-code=~/.claude/skills/discord-context-bridge/SKILL.md",
    )
    parser.add_argument("--generated-dir", type=Path, default=lint_runtime_skill_sync.export_runtime_skills.DEFAULT_OUTPUT_DIR)
    parser.add_argument("--apply", action="store_true", help="検証成功後に target を atomic replace する")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = sync_runtime_skills(
        targets=dict(args.target),
        generated_dir=args.generated_dir,
        apply=args.apply,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall={report['overall']} mode={report.get('mode', 'check')} applied={report['applied']}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
