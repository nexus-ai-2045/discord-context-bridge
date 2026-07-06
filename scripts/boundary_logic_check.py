#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FORBIDDEN_ACTIONS = {
    "press_enter_to_send",
    "click_send_button",
    "send_message",
    "react",
    "edit",
    "delete",
}


def check(status: str, detail: str | None = None, value: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status}
    if detail is not None:
        payload["detail"] = detail
    if value is not None:
        payload["value"] = value
    return payload


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_python(path: Path) -> ast.Module:
    return ast.parse(read_text(path), filename=str(path))


def function_source(module: ast.Module, source: str, name: str) -> str:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def string_tuple_assignment(module: ast.Module, name: str) -> set[str]:
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
            return set()
        values: set[str] = set()
        for item in node.value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                values.add(item.value)
        return values
    return set()


def has_all(text: str, terms: list[str]) -> bool:
    return all(term in text for term in terms)


def discord_send_boundary_checks(root: Path = ROOT) -> dict[str, Any]:
    core_path = root / "src" / "discord_context_bridge" / "core.py"
    checks: dict[str, dict[str, Any]] = {}
    if not core_path.exists():
        return {
            "overall": "error",
            "checks": {"core_file": check("error", f"missing: {core_path}")},
        }

    source = read_text(core_path)
    module = parse_python(core_path)
    forbidden_actions = string_tuple_assignment(module, "DISCORD_FILL_ONLY_FORBIDDEN_ACTIONS")
    missing_actions = sorted(REQUIRED_FORBIDDEN_ACTIONS - forbidden_actions)
    checks["forbidden_send_actions"] = check(
        "ok" if not missing_actions else "error",
        None if not missing_actions else "fill-only forbidden action set is incomplete",
        sorted(forbidden_actions),
    )

    fill_guard = function_source(module, source, "_enforce_discord_fill_only_guard")
    checks["fill_only_runtime_guard"] = check(
        "ok"
        if has_all(
            fill_guard,
            [
                "DISCORD_FILL_ONLY_FORBIDDEN_ACTIONS",
                "chrome_extension_fill_only",
                "stop_before_send_button",
                "none_until_human_send",
                "human_send_required",
                "send_capability",
                "outbound_actions",
                "raise RuntimeError",
            ],
        )
        else "error",
        None if fill_guard else "_enforce_discord_fill_only_guard is missing",
    )

    staging = function_source(module, source, "build_discord_send_staging_packet")
    checks["staging_packet_is_fill_only"] = check(
        "ok"
        if has_all(
            staging,
            [
                "discord_fill_only_guard.v1",
                "chrome_extension_fill_only",
                "stop_before_send_button",
                "none_until_human_send",
                "human_send_required",
                '"send_capability": "disabled"',
                '"outbound_actions": "disabled"',
                '"raw_discord_text_output": "omitted"',
                "_enforce_discord_fill_only_guard",
            ],
        )
        else "error",
        None if staging else "build_discord_send_staging_packet is missing",
    )

    dry_run = function_source(module, source, "verify_chrome_extension_fill_only_dry_run")
    checks["chrome_fill_dry_run_guard"] = check(
        "ok"
        if has_all(
            dry_run,
            [
                "latest_target_snapshot_confirmed",
                "socket_pre_send",
                "draft_matches_copy_block",
                '"required_stop": "stop_before_send_button"',
                '"outbound_actions": "disabled"',
                '"send_capability": "disabled"',
            ],
        )
        else "error",
        None if dry_run else "verify_chrome_extension_fill_only_dry_run is missing",
    )

    closeout = function_source(module, source, "build_discord_post_send_closeout_packet")
    checks["post_send_closeout_is_metadata_only"] = check(
        "ok"
        if has_all(
            closeout,
            [
                '"observed_message_id_output": "omitted"',
                '"observed_url_output": "omitted"',
                '"text_returned": False',
                '"raw_discord_text_output": "omitted"',
                '"outbound_actions": "disabled"',
                '"send_capability": "disabled"',
            ],
        )
        else "error",
        None if closeout else "build_discord_post_send_closeout_packet is missing",
    )

    send_message = function_source(module, source, "send_message")
    checks["send_api_disabled"] = check(
        "ok" if "raise DisabledCapability" in send_message else "error",
        None if send_message else "send_message is missing",
    )

    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "ok"
    return {"overall": overall, "checks": checks}


def public_private_artifact_boundary_checks(root: Path = ROOT) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    pr_scope_path = root / "scripts" / "pr_scope_guard.py"
    ssot_path = root / "scripts" / "verify_ssot_projection.py"
    readme_path = root / "README.md"
    process_path = root / "PROCESS_BOUNDARY.md"
    pr_template_path = root / ".github" / "pull_request_template.md"

    pr_scope = read_text(pr_scope_path) if pr_scope_path.exists() else ""
    checks["pr_scope_guard_private_paths"] = check(
        "ok"
        if has_all(
            pr_scope,
            [
                "DEFAULT_RETAIN_ONLY_PATTERNS",
                "SENSITIVE_PATH_PATTERNS",
                "retain_only_paths",
                "sensitive_path_mentions",
                "unexpected_new_review_surface",
            ],
        )
        else "error",
        None if pr_scope else "scripts/pr_scope_guard.py is missing",
    )

    ssot = read_text(ssot_path) if ssot_path.exists() else ""
    checks["ssot_projection_privacy_scan"] = check(
        "ok" if has_all(ssot, ["PRIVATE_PATTERNS", "privacy_scan", "Discord への send"]) else "error",
        None if ssot else "scripts/verify_ssot_projection.py is missing",
    )

    docs_blob = "\n".join(read_text(path) for path in (readme_path, process_path) if path.exists())
    checks["docs_state_public_private_boundary"] = check(
        "ok"
        if has_all(
            docs_blob,
            [
                "raw Discord text",
                "local path",
                "send_message()",
                "pr_scope_guard.py",
                "outbound_actions",
            ],
        )
        else "error",
        "README.md / PROCESS_BOUNDARY.md must describe raw/private and no-send boundaries",
    )

    pr_template = read_text(pr_template_path) if pr_template_path.exists() else ""
    checks["pr_template_requires_boundary_review"] = check(
        "ok"
        if has_all(
            pr_template,
            [
                "Discord 送信",
                "reaction",
                "raw Discord 本文",
                "secret",
                "local path",
                "pr_scope_guard.py",
                "boundary_logic_check.py",
            ],
        )
        else "error",
        None if pr_template else ".github/pull_request_template.md is missing",
    )

    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "ok"
    return {"overall": overall, "checks": checks}


def account_boundary_checks(root: Path = ROOT) -> dict[str, Any]:
    preflight_path = root / "scripts" / "pr_readiness_preflight.py"
    checks: dict[str, dict[str, Any]] = {}
    preflight = read_text(preflight_path) if preflight_path.exists() else ""
    checks["pr_readiness_uses_gh_guard"] = check(
        "ok"
        if has_all(
            preflight,
            [
                "GH_GUARD_PATH",
                "account_boundary_report",
                "checks[\"account_boundary\"]",
                "--gh-switch",
                "nexus-ai-2045",
                "discord-context-bridge",
            ],
        )
        else "error",
        None if preflight else "scripts/pr_readiness_preflight.py is missing",
    )
    overall = "error" if any(item["status"] == "error" for item in checks.values()) else "ok"
    return {"overall": overall, "checks": checks}


def build_report(root: Path = ROOT) -> dict[str, Any]:
    groups = {
        "discord_send_boundary": discord_send_boundary_checks(root),
        "public_private_artifact_boundary": public_private_artifact_boundary_checks(root),
        "account_boundary": account_boundary_checks(root),
    }
    overall = "error" if any(group["overall"] == "error" for group in groups.values()) else "ok"
    return {
        "schema": "discord_context_bridge_boundary_logic_check.v1",
        "overall": overall,
        "groups": groups,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="運用境界が記憶ではなく実行可能なロジックとして残っているか確認する。")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall={report['overall']}")
        for group_name, group in report["groups"].items():
            print(f"{group_name}={group['overall']}")
            for check_name, item in group["checks"].items():
                print(f"  {check_name}={item['status']}")
    return 0 if report["overall"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
