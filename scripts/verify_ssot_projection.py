#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_runtime_skills


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATTERNS = (
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"discord(app)?\.com/api/webhooks", re.IGNORECASE),
    re.compile(r"\b(DISCORD_(BOT_TOKEN|WEBHOOK_URL)|Authorization|Bearer )\b"),
    re.compile(r"\bmfa\.", re.IGNORECASE),
)


def check(status: str, value: str | None = None, detail: str | None = None) -> dict[str, str | None]:
    payload: dict[str, str | None] = {"status": status}
    if value is not None:
        payload["value"] = value
    if detail is not None:
        payload["detail"] = detail
    return payload


def _extract_field(text: str, field: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(field)}:\s*(.+?)\s*$", text)
    return match.group(1) if match else None


def _existing_ssot_commit(output_dir: Path, runtimes: list[str]) -> str | None:
    values = []
    for runtime in runtimes:
        path = output_dir / runtime / "SKILL.md"
        if path.exists():
            value = _extract_field(path.read_text(encoding="utf-8"), "ssot_commit")
            if value:
                values.append(value)
    return values[0] if values and len(set(values)) == 1 else None


def verify_projection(
    *,
    manifest_path: Path = export_runtime_skills.DEFAULT_MANIFEST,
    contract_path: Path = export_runtime_skills.DEFAULT_CONTRACT,
    output_dir: Path = export_runtime_skills.DEFAULT_OUTPUT_DIR,
    ssot_commit: str | None = None,
) -> dict[str, Any]:
    checks: dict[str, dict[str, str | None]] = {}
    manifest = export_runtime_skills.load_manifest(manifest_path)
    runtimes = list(manifest["runtime_targets"])
    checks["manifest"] = check(
        "ok"
        if manifest.get("schema") == "discord_context_bridge_capability_manifest.v1"
        and manifest.get("ssot_repo") == "nexus-ai-2045/discord-context-bridge"
        else "error",
        str(manifest_path),
    )
    if not contract_path.exists():
        checks["contract"] = check("error", str(contract_path), "contract file is missing")
    else:
        contract_text = contract_path.read_text(encoding="utf-8")
        required_boundary = "この public core は Discord への send / 自動返信 / reaction / delete / edit を直接実行しない"
        checks["contract"] = check(
            "ok" if required_boundary in contract_text and "auto-send-preflight" in contract_text else "error",
            str(contract_path),
            None
            if required_boundary in contract_text and "auto-send-preflight" in contract_text
            else "required public-core send boundary is missing",
        )

    effective_commit = ssot_commit or _existing_ssot_commit(output_dir, runtimes) or export_runtime_skills.git_commit()
    with tempfile.TemporaryDirectory() as tmp:
        expected_dir = Path(tmp) / "skills"
        export_runtime_skills.export_runtime_skills(
            manifest_path=manifest_path,
            contract_path=contract_path,
            output_dir=expected_dir,
            ssot_commit=effective_commit,
            generated_at="VERIFY",
        )
        mismatches: list[str] = []
        privacy_hits: list[str] = []
        for runtime in runtimes:
            relative = Path(runtime) / "SKILL.md"
            actual = output_dir / relative
            expected = expected_dir / relative
            # report に載せる path は OS に依らず POSIX 区切りで固定する (Windows の \ 混入防止)
            relative_label = relative.as_posix()
            if not actual.exists():
                mismatches.append(f"{relative_label}:missing")
                continue
            actual_text = re.sub(r"(?m)^generated_at: .+$", "generated_at: VERIFY", actual.read_text(encoding="utf-8"))
            if actual_text != expected.read_text(encoding="utf-8"):
                mismatches.append(relative_label)
            for pattern in PRIVATE_PATTERNS:
                if pattern.search(actual_text):
                    privacy_hits.append(f"{relative_label}:{pattern.pattern}")
        checks["generated_files"] = check(
            "ok" if not mismatches else "error",
            f"{len(runtimes)} runtime skills",
            ", ".join(mismatches) if mismatches else None,
        )
        checks["privacy_scan"] = check(
            "ok" if not privacy_hits else "error",
            "generated skills",
            ", ".join(privacy_hits) if privacy_hits else None,
        )
    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "ok"
    return {
        "schema": "discord_context_bridge_ssot_projection_verification.v1",
        "overall": overall,
        "checks": checks,
        "runtime_targets": runtimes,
        "ssot_commit": effective_commit,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SSOT から生成された runtime skill の整合性を確認する")
    parser.add_argument("--manifest", type=Path, default=export_runtime_skills.DEFAULT_MANIFEST)
    parser.add_argument("--contract", type=Path, default=export_runtime_skills.DEFAULT_CONTRACT)
    parser.add_argument("--output-dir", type=Path, default=export_runtime_skills.DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ssot-commit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = verify_projection(
        manifest_path=args.manifest,
        contract_path=args.contract,
        output_dir=args.output_dir,
        ssot_commit=args.ssot_commit,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall={report['overall']}")
        for name, item in report["checks"].items():
            print(f"{name}={item['status']} {item.get('value', '')}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
