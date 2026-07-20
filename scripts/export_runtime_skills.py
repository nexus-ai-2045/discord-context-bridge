#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "capability" / "manifest.yaml"
DEFAULT_CONTRACT = ROOT / "docs" / "operating-contract.md"
DEFAULT_OUTPUT_DIR = ROOT / "dist" / "skills"


def _parse_scalar(value: str) -> str | bool:
    value = value.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    return value.strip('"').strip("'")


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Load the repository-local manifest.

    The repo intentionally avoids adding a YAML dependency. This parser supports
    the small manifest shape used here: top-level scalars, scalar lists, and
    lists of flat maps.
    """
    payload: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            current_item = None
            if ":" not in stripped:
                raise ValueError(f"invalid manifest line: {raw_line}")
            key, value = stripped.split(":", 1)
            if value.strip():
                payload[key] = _parse_scalar(value)
                current_key = None
            else:
                payload[key] = []
                current_key = key
            continue
        if current_key is None:
            raise ValueError(f"nested value without a parent key: {raw_line}")
        if stripped.startswith("- "):
            item = stripped[2:]
            if ":" in item:
                key, value = item.split(":", 1)
                current_item = {key: _parse_scalar(value)}
                payload[current_key].append(current_item)
            else:
                current_item = None
                payload[current_key].append(_parse_scalar(item))
            continue
        if current_item is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_item[key] = _parse_scalar(value)
            continue
        raise ValueError(f"unsupported manifest line: {raw_line}")
    return payload


def _cli_subcommands() -> set[str]:
    src_dir = str(ROOT / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from discord_context_bridge.cli import build_parser

    parser = build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def validate_manifest_commands(manifest: dict[str, Any]) -> list[str]:
    """required/verification commands が実行可能な形で書かれているか検証する。

    - 素の名前は CLI サブコマンドとして build_parser に登録済みであること
    - `python3 scripts/...` 形式は参照先スクリプトが実在すること
    生成 skill を読んだ runtime が存在しないコマンドを叩く drift をここで止める。
    """
    issues: list[str] = []
    known_subcommands = _cli_subcommands()
    for section in ("required_commands", "verification_commands"):
        for item in manifest.get(section, []):
            command = str(item.get("command") or "").strip()
            if not command:
                issues.append(f"{section}: command が空です")
                continue
            tokens = command.split()
            if tokens[0] in {"python", "python3"}:
                if len(tokens) < 2:
                    issues.append(f"{section}: interpreter のみでスクリプト指定がありません: {command}")
                elif not (ROOT / tokens[1]).is_file():
                    issues.append(f"{section}: 参照スクリプトが存在しません: {tokens[1]}")
            elif tokens[0] not in known_subcommands:
                issues.append(
                    f"{section}: CLI 未登録のサブコマンドです: {tokens[0]} "
                    "(独立スクリプトなら 'python3 scripts/<name>.py ...' 形式で書く)"
                )
    return issues


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git_commit() -> str:
    return resolve_git_commit("HEAD")


def resolve_git_commit(value: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"{value}^{{commit}}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    resolved = completed.stdout.strip()
    if completed.returncode != 0 or not resolved:
        raise ValueError(f"ssot_commit を commit に解決できません: {value}")
    return resolved


def git_commit_generated_at(commit: str) -> str:
    """Return a deterministic generation timestamp for a Git commit."""
    completed = subprocess.run(
        ["git", "show", "-s", "--format=%cI", commit],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise ValueError(f"ssot_commit の commit timestamp を取得できません: {commit}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def render_skill(
    *,
    runtime: str,
    manifest: dict[str, Any],
    contract_text: str,
    ssot_commit: str,
    generated_at: str,
) -> str:
    manifest_checksum = sha256_text(canonical_json(manifest))
    contract_checksum = sha256_text(contract_text)
    stoplines = "\n".join(f"- `{item}`" for item in manifest["stoplines"])
    commands = "\n".join(
        f"- `{item['command']}`: {item['purpose']}" for item in manifest["required_commands"]
    )
    verification = "\n".join(
        f"- `{item['command']}`: {item['purpose']}" for item in manifest["verification_commands"]
    )
    return f"""---
name: discord-context-bridge
description: Runtime adapter for the Discord Context Bridge SSOT. Generated for {runtime}; do not edit by hand.
ssot_repo: {manifest["ssot_repo"]}
ssot_commit: {ssot_commit}
manifest_version: {manifest["schema"]}
manifest_checksum: {manifest_checksum}
contract_checksum: {contract_checksum}
generated_at: {generated_at}
runtime_target: {runtime}
---

# Discord Context Bridge ({runtime})

This skill is generated from `{manifest["ssot_repo"]}`. Do not edit this generated file by hand; update `capability/manifest.yaml` or `docs/operating-contract.md`, then run `python3 scripts/export_runtime_skills.py`.

## Contract

{contract_text.strip()}

## Stoplines

{stoplines}

## Commands

{commands}

## Verification

{verification}
"""


def export_runtime_skills(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    contract_path: Path = DEFAULT_CONTRACT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    ssot_commit: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    command_issues = validate_manifest_commands(manifest)
    if command_issues:
        raise ValueError("manifest command validation failed: " + " / ".join(command_issues))
    contract_text = contract_path.read_text(encoding="utf-8")
    ssot_commit = resolve_git_commit(ssot_commit or "HEAD")
    generated_at = generated_at or git_commit_generated_at(ssot_commit)
    changed = False
    generated: list[str] = []
    for runtime in manifest["runtime_targets"]:
        target = output_dir / runtime / "SKILL.md"
        body = render_skill(
            runtime=runtime,
            manifest=manifest,
            contract_text=contract_text,
            ssot_commit=ssot_commit,
            generated_at=generated_at,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != body:
            target.write_text(body, encoding="utf-8")
            changed = True
        generated.append(str(target.relative_to(output_dir)))
    return {
        "schema": "discord_context_bridge_runtime_skill_export.v1",
        "changed": changed,
        "generated_count": len(generated),
        "generated": generated,
        "output_dir": str(output_dir),
        "ssot_commit": ssot_commit,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SSOT から runtime skill を生成する")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ssot-commit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = export_runtime_skills(
        manifest_path=args.manifest,
        contract_path=args.contract,
        output_dir=args.output_dir,
        ssot_commit=args.ssot_commit,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"generated={report['generated_count']} changed={report['changed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
