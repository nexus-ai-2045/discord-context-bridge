#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_runtime_skills


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATTERNS = (
    re.compile(r"discord(app)?\.com/api/webhooks", re.IGNORECASE),
    re.compile(r"\b(DISCORD_(BOT_TOKEN|WEBHOOK_URL)|Authorization|Bearer )\b"),
    re.compile(r"\bmfa\.", re.IGNORECASE),
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_field(text: str, field: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(field)}:\s*(.+?)\s*$", text)
    return match.group(1) if match else None


def parse_target(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--target は runtime=/path/to/SKILL.md の形で指定してください。")
    runtime, path = value.split("=", 1)
    runtime = runtime.strip()
    if not runtime or not path.strip():
        raise argparse.ArgumentTypeError("--target は runtime=/path/to/SKILL.md の形で指定してください。")
    return runtime, Path(path).expanduser()


def _check(status: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status}
    payload.update(fields)
    return payload


def lint_runtime_skill_sync(
    *,
    targets: dict[str, Path],
    generated_dir: Path = export_runtime_skills.DEFAULT_OUTPUT_DIR,
    manifest_path: Path = export_runtime_skills.DEFAULT_MANIFEST,
    allow_missing: bool = False,
) -> dict[str, Any]:
    manifest = export_runtime_skills.load_manifest(manifest_path)
    allowed_runtimes = set(manifest["runtime_targets"])
    checks: dict[str, dict[str, Any]] = {}
    for runtime, target in sorted(targets.items()):
        if runtime not in allowed_runtimes:
            checks[runtime] = _check(
                "error",
                reason="unknown_runtime",
                target=str(target),
                allowed=sorted(allowed_runtimes),
                read_only=True,
            )
            continue
        expected = generated_dir / runtime / "SKILL.md"
        if not expected.exists():
            checks[runtime] = _check("error", reason="generated_skill_missing", expected=str(expected), read_only=True)
            continue
        if not target.exists():
            checks[runtime] = _check(
                "warning" if allow_missing else "error",
                reason="target_missing",
                target=str(target),
                expected=str(expected),
                read_only=True,
            )
            continue
        expected_text = expected.read_text(encoding="utf-8")
        target_text = target.read_text(encoding="utf-8")
        privacy_hits = [pattern.pattern for pattern in PRIVATE_PATTERNS if pattern.search(target_text)]
        expected_hash = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        target_hash = hashlib.sha256(target_text.encode("utf-8")).hexdigest()
        if privacy_hits:
            checks[runtime] = _check(
                "error",
                reason="privacy_pattern_detected",
                target=str(target),
                privacy_hits=privacy_hits,
                read_only=True,
            )
            continue
        if expected_text != target_text:
            checks[runtime] = _check(
                "error",
                reason="content_mismatch",
                target=str(target),
                expected=str(expected),
                expected_sha256=expected_hash,
                target_sha256=target_hash,
                expected_ssot_commit=extract_field(expected_text, "ssot_commit"),
                target_ssot_commit=extract_field(target_text, "ssot_commit"),
                expected_manifest_checksum=extract_field(expected_text, "manifest_checksum"),
                target_manifest_checksum=extract_field(target_text, "manifest_checksum"),
                read_only=True,
            )
            continue
        checks[runtime] = _check(
            "ok",
            reason="in_sync",
            target=str(target),
            expected=str(expected),
            sha256=target_hash,
            ssot_commit=extract_field(target_text, "ssot_commit"),
            manifest_checksum=extract_field(target_text, "manifest_checksum"),
            contract_checksum=extract_field(target_text, "contract_checksum"),
            read_only=True,
        )
    if not checks:
        checks["targets"] = _check("error", reason="no_targets", read_only=True)
    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "ok"
    return {
        "schema": "discord_context_bridge_runtime_skill_sync_lint.v1",
        "overall": overall,
        "checks": checks,
        "generated_dir": str(generated_dir),
        "read_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="runtime skill directory と SSOT 生成物の同期を read-only で確認する")
    parser.add_argument(
        "--target",
        action="append",
        type=parse_target,
        default=[],
        metavar="runtime=/path/to/SKILL.md",
        help="確認対象。例: codex=~/.codex/skills/discord-context-bridge/SKILL.md",
    )
    parser.add_argument("--generated-dir", type=Path, default=export_runtime_skills.DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=export_runtime_skills.DEFAULT_MANIFEST)
    parser.add_argument("--allow-missing", action="store_true", help="存在しない target を warning 扱いにする")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = lint_runtime_skill_sync(
        targets=dict(args.target),
        generated_dir=args.generated_dir,
        manifest_path=args.manifest,
        allow_missing=args.allow_missing,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall={report['overall']}")
        for runtime, item in report["checks"].items():
            print(f"{runtime}={item['status']} {item.get('reason', '')}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
