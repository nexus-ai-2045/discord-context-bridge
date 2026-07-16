#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.process_runner import minimal_child_env, run_process


PASS_STATES = {"passed", "skipped_not_applicable"}
ISSUE_STATES = {
    "partial_evidence",
    "code_repair_required",
    "environment_blocked",
    "external_dependency_blocked",
    "human_review_required",
}
TRANSIENT_STAGES = {"timeout", "temporary_io", "rate_limited_retryable"}


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    layer: str
    command: tuple[str, ...]
    timeout_seconds: float
    enabled: bool = True
    skip_reason: str = ""
    expects_json: bool = True


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    failure_stage: str | None
    elapsed_seconds: float


def build_case_specs(
    *,
    python_executable: str,
    url: str | None,
    include_desktop_cache: bool,
    include_ops_fast: bool = False,
) -> list[CaseSpec]:
    py = python_executable
    specs = [
        CaseSpec(
            "fixture_13_step",
            "synthetic_e2e",
            (py, "scripts/fixture_13_step_e2e.py", "--understanding-confirmed", "--json"),
            30,
        ),
        CaseSpec(
            "route_fixture_e2e",
            "synthetic_e2e",
            (
                py,
                "scripts/e2e_discord_route_check.py",
                "--input",
                "tests/fixtures/discord_rich_copy.txt",
                "--understanding-confirmed",
                "--json",
            ),
            30,
        ),
        CaseSpec(
            "send_boundary_pdca",
            "safety_contract",
            (py, "scripts/send_pdca_preflight_smoke.py", "--json"),
            30,
        ),
        CaseSpec(
            "chrome_bundle_bootstrap",
            "runtime_bootstrap",
            (py, "scripts/codex_chrome_bundle_smoke.py", "--json"),
            20,
        ),
        CaseSpec(
            "ops_fast",
            "local_regression",
            (py, "scripts/ops_check.py", "--profile", "fast"),
            90,
            enabled=include_ops_fast,
            skip_reason="nested_orchestrator_not_requested" if not include_ops_fast else "",
            expects_json=False,
        ),
        CaseSpec(
            "ssot_projection",
            "publication_gate",
            (py, "scripts/verify_ssot_projection.py", "--json"),
            30,
        ),
        CaseSpec(
            "cache_inventory",
            "local_evidence",
            (
                py,
                "-m",
                "discord_context_bridge.cli",
                "cache-inventory",
                "--url",
                url or "",
                "--json",
            ),
            30,
            enabled=bool(url),
            skip_reason="url_not_supplied" if not url else "",
        ),
        CaseSpec(
            "desktop_cache_probe",
            "local_evidence",
            (
                py,
                "-m",
                "discord_context_bridge.cli",
                "desktop-cache-probe",
                "--url",
                url or "",
                "--json",
            ),
            120,
            enabled=bool(url) and include_desktop_cache,
            skip_reason=(
                "url_not_supplied" if not url else "desktop_cache_scan_not_requested"
            ),
        ),
    ]
    return specs


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _failure_signal(payload: dict[str, Any] | None, result: ExecutionResult) -> str:
    if result.failure_stage:
        return result.failure_stage
    if "ModuleNotFoundError" in result.stderr or "No module named" in result.stderr:
        return "dependency_missing"
    if not payload:
        return "command_failed" if result.returncode else "none"
    checks = payload.get("checks") or {}
    if isinstance(checks, dict):
        provenance = checks.get("provenance") or {}
        if isinstance(provenance, dict) and provenance.get("status") == "error":
            return "projection_provenance_invalid"
    for key in ("failure_stage", "blocked_stage", "reason", "state", "stage"):
        value = payload.get(key)
        if value:
            return str(value)
    return "command_failed" if result.returncode else "none"


def classify_case(spec: CaseSpec, result: ExecutionResult) -> dict[str, Any]:
    payload = _parse_json(result.stdout) if spec.expects_json else None
    signal = _failure_signal(payload, result)
    if spec.expects_json and result.returncode == 0 and payload is None:
        signal = "invalid_json_output"
    normalized = signal.casefold()

    payload_reports_failure = bool(
        payload
        and (
            payload.get("ok") is False
            or str(payload.get("overall") or "").casefold()
            in {"error", "failed", "blocked"}
        )
    )
    if result.returncode == 0 and not (spec.expects_json and payload is None) and not payload_reports_failure:
        state = str((payload or {}).get("state") or "").casefold()
        if state in {"snapshot_missing", "reference_only", "partial", "partial_scan"}:
            classification = "partial_evidence"
            next_action = "不足証拠だけを次のread-only取得で補います。"
        else:
            classification = "passed"
            next_action = "次のcaseへ進みます。"
    elif any(term in normalized for term in ("projection", "provenance", "human_review", "content_mismatch")):
        classification = "human_review_required"
        next_action = "人間レビュー後にSSOTをcommitし、生成物を再作成して再実行します。"
    elif any(term in normalized for term in ("auth", "permission", "approval", "credential", "login")):
        classification = "human_review_required"
        next_action = "認証・権限・承認は自動変更せず、人間操作後に再実行します。"
    elif any(term in normalized for term in ("rate_limit", "external", "service_unavailable")):
        classification = "external_dependency_blocked"
        next_action = "外部状態が回復した後に、このcaseだけ再実行します。"
    elif any(term in normalized for term in ("node_missing", "bundle_missing", "extension_unavailable", "dependency_missing")):
        classification = "environment_blocked"
        next_action = "不足runtimeを直した後に、このcaseだけ再実行します。"
    else:
        classification = "code_repair_required"
        next_action = "最小failing checkとして実装修正へ戻し、修正後にこのcaseだけ再実行します。"

    return {
        "case_id": spec.case_id,
        "layer": spec.layer,
        "classification": classification,
        "failure_signal": signal,
        "retryable": signal in TRANSIENT_STAGES,
        "next_action": next_action,
        "returncode": result.returncode,
        "elapsed_ms": max(0, int(result.elapsed_seconds * 1000)),
        "command_output": "omitted",
        "local_paths_output": "omitted",
    }


def execute_case(spec: CaseSpec) -> ExecutionResult:
    started = time.monotonic()
    env = minimal_child_env()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    completed = run_process(
        list(spec.command),
        cwd=ROOT,
        env=env,
        timeout=spec.timeout_seconds,
    )
    return ExecutionResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        failure_stage=completed.failure_stage,
        elapsed_seconds=time.monotonic() - started,
    )


def run_case(
    spec: CaseSpec,
    *,
    executor: Callable[[CaseSpec], ExecutionResult] = execute_case,
    max_retries: int = 1,
) -> dict[str, Any]:
    if not spec.enabled:
        return {
            "case_id": spec.case_id,
            "layer": spec.layer,
            "classification": "skipped_not_applicable",
            "failure_signal": spec.skip_reason,
            "attempt_count": 0,
            "retryable": False,
            "next_action": "必要になった時だけ明示指定して実行します。",
            "command_output": "omitted",
            "local_paths_output": "omitted",
        }

    attempts = 0
    classified: dict[str, Any] = {}
    while attempts <= max_retries:
        attempts += 1
        classified = classify_case(spec, executor(spec))
        if classified["classification"] == "passed" or not classified["retryable"]:
            break
    return {**classified, "attempt_count": attempts}


def compare_previous(current: list[dict[str, Any]], previous: dict[str, Any] | None) -> dict[str, list[str]]:
    previous_cases = {
        str(case.get("case_id")): str(case.get("classification"))
        for case in ((previous or {}).get("cases") or [])
        if isinstance(case, dict)
    }
    current_cases = {str(case["case_id"]): str(case["classification"]) for case in current}
    resolved = sorted(
        case_id
        for case_id, old_state in previous_cases.items()
        if old_state in ISSUE_STATES and current_cases.get(case_id) in PASS_STATES
    )
    persisting = sorted(
        case_id
        for case_id, state in current_cases.items()
        if state in ISSUE_STATES and previous_cases.get(case_id) in ISSUE_STATES
    )
    new_issues = sorted(
        case_id
        for case_id, state in current_cases.items()
        if state in ISSUE_STATES and previous_cases.get(case_id) not in ISSUE_STATES
    )
    return {"resolved": resolved, "persisting": persisting, "new_issues": new_issues}


def build_report(cases: list[dict[str, Any]], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for case in cases:
        state = str(case["classification"])
        counts[state] = counts.get(state, 0) + 1
    if counts.get("code_repair_required"):
        overall = "failed"
    elif any(counts.get(key) for key in ("human_review_required", "environment_blocked", "external_dependency_blocked")):
        overall = "blocked"
    elif counts.get("partial_evidence"):
        overall = "attention"
    else:
        overall = "passed"
    return {
        "schema": "discord_context_bridge_pdca_e2e_inventory.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "pdca": {
            "plan": "安全なlocal/fixture/read-only E2Eを棚卸しする",
            "do": f"{len(cases)} casesをbounded実行",
            "check": counts,
            "act": "各caseのnext_actionだけを実行し、同じ失敗経路を反復しない",
        },
        "delta": compare_previous(cases, previous),
        "cases": cases,
        "safety": {
            "discord_write": "disabled",
            "settings_change": "disabled",
            "automatic_code_repair": "disabled",
            "raw_output": "omitted",
            "local_paths_output": "omitted",
        },
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def _read_previous(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="失敗を反復せず、DCB E2EをPDCA分類するmetadata-only runner。")
    parser.add_argument("--url", help="対象Discord URL。出力には表示しません")
    parser.add_argument("--include-desktop-cache", action="store_true", help="時間のかかるDesktop cache実走査を含める")
    parser.add_argument("--include-ops-fast", action="store_true", help="重複する並列ops orchestratorも明示的に含める")
    parser.add_argument("--only", action="append", default=[], help="指定caseだけ実行。複数指定可")
    parser.add_argument("--max-retries", type=int, choices=[0, 1], default=1, help="timeout等の一時失敗だけ再試行する上限")
    parser.add_argument("--previous-report", type=Path, help="前回結果との解消/継続/新規差分を出す")
    parser.add_argument("--output", type=Path, help="metadata-only reportをatomic保存する")
    parser.add_argument("--dry-run", action="store_true", help="case一覧だけ返し、実行しない")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    specs = build_case_specs(
        python_executable=sys.executable,
        url=args.url,
        include_desktop_cache=args.include_desktop_cache,
        include_ops_fast=args.include_ops_fast,
    )
    if args.only:
        selected = set(args.only)
        known = {spec.case_id for spec in specs}
        unknown = selected - known
        if unknown:
            print(json.dumps({"ok": False, "reason": "unknown_case", "unknown_count": len(unknown)}))
            return 2
        specs = [spec for spec in specs if spec.case_id in selected]
    if args.dry_run:
        cases = [
            {
                "case_id": spec.case_id,
                "layer": spec.layer,
                "enabled": spec.enabled,
                "skip_reason": spec.skip_reason,
                "command_output": "omitted",
            }
            for spec in specs
        ]
        report = {
            "schema": "discord_context_bridge_pdca_e2e_inventory_plan.v1",
            "ok": True,
            "case_count": len(cases),
            "cases": cases,
            "outbound_actions": "disabled",
        }
    else:
        cases = [run_case(spec, max_retries=args.max_retries) for spec in specs]
        report = build_report(cases, previous=_read_previous(args.previous_report))
    if args.output:
        _write_report(args.output, report)
        report["report_saved"] = True
        report["report_path_output"] = "omitted"
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"overall: {report.get('overall', 'plan')}")
        for case in report["cases"]:
            print(f"- {case['case_id']}: {case.get('classification', 'planned')}")
        print("raw_output: omitted")
        print("outbound_actions: disabled")
    return 0 if report.get("overall", "passed") in {"passed", "attention"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
