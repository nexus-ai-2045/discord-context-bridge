from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.process_runner import minimal_child_env, run_process


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    elapsed: float
    command: list[str]
    output: str
    failure_stage: str | None = None
    output_truncated: bool = False


DEFAULT_COMMAND_TIMEOUT: float | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="実装後の運用チェックを並列で実行する")
    parser.add_argument(
        "--profile",
        choices=["fast", "full", "release"],
        default="full",
        help="fast は通常開発用の軽量ゲート、full は従来相当、release は GitHub account確認も含める",
    )
    parser.add_argument("--port", type=int, default=8025, help="local smoke で使う port")
    parser.add_argument("--http", action="store_true", help="HTTP MCP 起動スモークも含める")
    parser.add_argument("--skip-http", action="store_true", help="HTTP MCP 起動スモークを省略する（既定動作の明示用）")
    parser.add_argument("--gh", action="store_true", help="GitHub account と remote owner の一致も確認する")
    parser.add_argument("--gh-account-only", action="store_true", help="GitHub token ではなく account 一致だけを確認する")
    parser.add_argument("--fail-fast", action="store_true", help="失敗した時点で終了する")
    return parser.parse_args(argv)


def run_command(
    name: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> CheckResult:
    started = time.perf_counter()
    completed = run_process(
        command,
        cwd=ROOT,
        env=env,
        timeout=DEFAULT_COMMAND_TIMEOUT if timeout is None else timeout,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return CheckResult(
        name=name,
        ok=completed.returncode == 0,
        elapsed=time.perf_counter() - started,
        command=command,
        output=output,
        failure_stage=completed.failure_stage,
        output_truncated=completed.output_truncated,
    )


def run_compile_check() -> CheckResult:
    cache_root = Path(tempfile.mkdtemp(prefix="dcb-pycache-"))
    try:
        return run_command(
            "compile",
            [sys.executable, "-m", "compileall", "src", "tests", "scripts"],
            env={"PYTHONPYCACHEPREFIX": str(cache_root)},
        )
    finally:
        shutil.rmtree(cache_root, ignore_errors=True)


def run_secret_scan() -> CheckResult:
    credential_pattern = (
        r"(DISCORD_(BOT_TOKEN|WEBHOOK_URL)|"
        r"discord(app)?\.com/api/webhooks|"
        "Author" + r"ization|"
        "Bear" + r"er |"
        r"mfa\.|"
        r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27})"
    )
    personal_path_pattern = (
        r"(C:\\\\Users\\\\[^\\\\\s]+|"
        r"C:\\Users\\[^\\\s]+|"
        r"C:/Users/[^/\s]+|"
        r"/Users/[^/\s]+)"
    )
    pattern = f"{credential_pattern}|{personal_path_pattern}"
    result = run_command("秘密情報スキャン", ["rg", "-n", pattern, "."])
    allowed_prefixes = (
        "./PUBLIC_RELEASE_CHECKLIST.md:",
        "./SECURITY.md:",
        "./src/discord_context_bridge/core.py:",
        "./scripts/lint_runtime_skill_sync.py:",
        "./scripts/ops_check.py:",
        "./scripts/pr_scope_guard.py:",
        "./scripts/read_visible_discord_text.py:",
        "./scripts/verify_ssot_projection.py:",
        "./tests/test_core.py:",
    )
    unexpected = []
    for line in result.output.splitlines():
        normalized = line.replace("\\", "/") if line.startswith(".\\") else line
        if any(
            safe in line
            for safe in (
                "C:\\Users\\example",
                "C:\\\\Users\\\\example",
                "C:/Users/example",
                "/Users/example",
                "/Users/Shared",
            )
        ):
            continue
        if line.strip() and not normalized.startswith(allowed_prefixes):
            unexpected.append(line)
    output = result.output
    ok = result.ok and not unexpected
    if unexpected:
        output += "\n想定外の一致:\n" + "\n".join(unexpected)
    return CheckResult(result.name, ok, result.elapsed, result.command, output)


def run_context_operating_mode_smoke(env: dict[str, str]) -> CheckResult:
    started = time.perf_counter()
    commands: list[str] = []
    outputs: list[str] = []
    failures: list[str] = []
    for command_name in ("triage-mode", "catchup-mode", "join-thread-mode", "boundary-mode"):
        command = [
            sys.executable,
            "-m",
            "discord_context_bridge.cli",
            command_name,
            "--input",
            "tests/fixtures/discord_rich_copy.txt",
            "--focus",
            "公開時期",
            "--json",
        ]
        commands.append(" ".join(command))
        result = run_command(f"{command_name} smoke", command, env=env)
        outputs.append(f"$ {' '.join(command)}\n{result.output}")
        if not result.ok:
            failures.append(f"{command_name}: command failed")
            continue
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError as exc:
            failures.append(f"{command_name}: invalid JSON: {exc}")
            continue
        if payload.get("schema") != "discord_context_operating_mode.v1":
            failures.append(f"{command_name}: schema mismatch")
        if payload.get("send_capability") != "disabled":
            failures.append(f"{command_name}: send_capability is not disabled")
        boundary = payload.get("safety_boundary") or {}
        if boundary.get("raw_text_returned") is not False:
            failures.append(f"{command_name}: raw_text_returned is not false")
        if boundary.get("participant_names_returned") is not False:
            failures.append(f"{command_name}: participant_names_returned is not false")
    output = "\n".join(outputs)
    if failures:
        output += "\n失敗:\n" + "\n".join(failures)
    return CheckResult(
        "文脈運用モード smoke",
        not failures,
        time.perf_counter() - started,
        ["; ".join(commands)],
        output,
    )


def smoke_store_paths() -> tuple[Path, Path]:
    run_id = f"{os.getpid()}-{time.time_ns()}"
    smoke_dir = Path(tempfile.gettempdir()) / "discord-context-bridge-smoke"
    return (
        smoke_dir / f"dcb-local-smoke-{run_id}.ndjson",
        smoke_dir / f"dcb-context-library-smoke-{run_id}.json",
    )


def build_checks(args: argparse.Namespace) -> dict[str, Callable[[], CheckResult]]:
    global DEFAULT_COMMAND_TIMEOUT
    DEFAULT_COMMAND_TIMEOUT = 20.0 if args.profile == "fast" else 60.0
    env = minimal_child_env()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    smoke_store, context_store = smoke_store_paths()
    smoke_command = [
        sys.executable,
        "scripts/local_smoke.py",
        "--reset",
        "--port",
        str(args.port),
        "--store",
        str(smoke_store),
        "--context-store",
        str(context_store),
    ]
    if not args.http:
        smoke_command.append("--skip-http")
    ingest_route_policy_command = [sys.executable, "scripts/lint_ingest_route_policy.py", "--json"]
    if os.environ.get("CI"):
        ingest_route_policy_command.append("--skip-local-claude-skill")
    all_checks = {
        "テスト": lambda: run_command("テスト", [sys.executable, "-m", "pytest", "tests", "-q"], env=env, timeout=120.0),
        "返信文脈契約": lambda: run_command(
            "返信文脈契約",
            [sys.executable, "-m", "pytest", "tests/test_reply_context_contract.py", "-q"],
            env=env,
        ),
        "compile": run_compile_check,
        "差分チェック": lambda: run_command("差分チェック", ["git", "diff", "--check"]),
        "version整合性": lambda: run_command("version整合性", [sys.executable, "scripts/bump_version.py", "--check"]),
        "秘密情報スキャン": run_secret_scan,
        "Codex ingress smoke": lambda: run_command(
            "Codex ingress smoke",
            [
                sys.executable,
                "scripts/codex_discord_ingress_smoke.py",
                "--chrome-opened",
                "--current-url",
                "https://discord.com/channels/@me/111111111111111111/222222222222222222",
                "--human-state",
                "ready",
                "--confirm-decision",
                "approved",
                "--json",
            ],
            env=env,
        ),
        "13工程fixture E2E": lambda: run_command(
            "13工程fixture E2E",
            [
                sys.executable,
                "scripts/fixture_13_step_e2e.py",
                "--entry-metadata",
                "tests/fixtures/codex_ingress_ready.json",
                "--understanding-confirmed",
                "--json",
            ],
            env=env,
        ),
        "status dashboard": lambda: run_command(
            "status dashboard",
            [sys.executable, "scripts/discord_inventory_dashboard.py", "--json"],
            env=env,
        ),
        "report-latest smoke": lambda: run_command(
            "report-latest smoke",
            [sys.executable, "scripts/report_latest_smoke.py", "--json"],
            env=env,
        ),
        "report-latest schema": lambda: run_command(
            "report-latest schema",
            [sys.executable, "scripts/report_latest_schema_check.py", "--json"],
            env=env,
        ),
        "url-intake-fast-path smoke": lambda: run_command(
            "url-intake-fast-path smoke",
            [sys.executable, "scripts/url_intake_fast_path_smoke.py", "--json"],
            env=env,
        ),
        "discord-url-measure smoke": lambda: run_command(
            "discord-url-measure smoke",
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_discord_url_measure.py",
                "-q",
            ],
            env=env,
        ),
        "SSOT projection": lambda: run_command(
            "SSOT projection",
            [sys.executable, "scripts/verify_ssot_projection.py", "--json"],
            env=env,
        ),
        "boundary logic": lambda: run_command(
            "boundary logic",
            [sys.executable, "scripts/boundary_logic_check.py", "--json"],
            env=env,
        ),
        "文脈運用モード smoke": lambda: run_context_operating_mode_smoke(env),
        "runtime skill sync lint": lambda: run_command(
            "runtime skill sync lint",
            [
                sys.executable,
                "scripts/lint_runtime_skill_sync.py",
                "--target",
                "codex=dist/skills/codex/SKILL.md",
                "--json",
            ],
            env=env,
        ),
        "ingest route policy lint": lambda: run_command(
            "ingest route policy lint",
            ingest_route_policy_command,
            env=env,
        ),
        "ローカルスモーク": lambda: run_command("ローカルスモーク", smoke_command, env=env),
    }
    if args.profile == "fast":
        checks = {
            name: all_checks[name]
            for name in (
                "compile",
                "差分チェック",
                "秘密情報スキャン",
                "返信文脈契約",
                "boundary logic",
                "url-intake-fast-path smoke",
                "discord-url-measure smoke",
                "ローカルスモーク",
            )
        }
    else:
        checks = dict(all_checks)
    if args.profile == "release":
        args.gh = True
    if args.gh:
        gh_command = [sys.executable, "scripts/gh_guard.py"]
        if args.gh_account_only:
            gh_command.append("--account-only")
        checks["GitHub account確認"] = lambda: run_command("GitHub account確認", gh_command, env=env)
    return checks


def summarize_result(result: CheckResult) -> None:
    status = "成功" if result.ok else "失敗"
    print(f"[{status}] {result.name}: {result.elapsed:.2f}s")
    if not result.ok:
        print("command:", " ".join(result.command))
        tail = "\n".join(result.output.splitlines()[-80:])
        print(tail or "(output なし)")


def main() -> int:
    args = parse_args()
    checks = build_checks(args)
    started = time.perf_counter()
    failures: list[CheckResult] = []
    print("運用チェックを並列で開始します。")
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        futures = {executor.submit(check): name for name, check in checks.items()}
        for future in as_completed(futures):
            result = future.result()
            summarize_result(result)
            if not result.ok:
                failures.append(result)
                if args.fail_fast:
                    for pending in futures:
                        pending.cancel()
                    break
    elapsed = time.perf_counter() - started
    if failures:
        print(f"\n未完了: {len(failures)} 件のチェックが失敗しました。合計 {elapsed:.2f}s")
        return 1
    print(f"\n完了: 全チェックが成功しました。合計 {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
