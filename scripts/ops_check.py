from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    elapsed: float
    command: list[str]
    output: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="実装後の運用チェックを並列で実行する")
    parser.add_argument("--port", type=int, default=8025, help="local smoke で使う port")
    parser.add_argument("--http", action="store_true", help="HTTP MCP 起動スモークも含める")
    parser.add_argument("--gh", action="store_true", help="GitHub account と remote owner の一致も確認する")
    parser.add_argument("--gh-switch", action="store_true", help="GitHub account 不一致時に remote owner へ切り替える")
    parser.add_argument("--gh-account-only", action="store_true", help="GitHub token ではなく account 一致だけを確認する")
    parser.add_argument("--fail-fast", action="store_true", help="失敗した時点で終了する")
    return parser.parse_args()


def run_command(name: str, command: list[str], *, env: dict[str, str] | None = None) -> CheckResult:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return CheckResult(
        name=name,
        ok=completed.returncode == 0,
        elapsed=time.perf_counter() - started,
        command=command,
        output=output,
    )


def run_secret_scan() -> CheckResult:
    pattern = (
        r"(DISCORD_(BOT_TOKEN|WEBHOOK_URL)|"
        r"discord(app)?\.com/api/webhooks|"
        "Author" + r"ization|"
        "Bear" + r"er |"
        r"mfa\.|"
        r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27})"
    )
    result = run_command("秘密情報スキャン", ["rg", "-n", pattern, "."])
    allowed_prefixes = (
        "./PUBLIC_RELEASE_CHECKLIST.md:",
        "./SECURITY.md:",
        "./scripts/read_visible_discord_text.py:",
        "./tests/test_core.py:",
    )
    unexpected = []
    for line in result.output.splitlines():
        normalized = line.replace("\\", "/") if line.startswith(".\\") else line
        if line.strip() and not normalized.startswith(allowed_prefixes):
            unexpected.append(line)
    output = result.output
    ok = result.ok and not unexpected
    if unexpected:
        output += "\n想定外の一致:\n" + "\n".join(unexpected)
    return CheckResult(result.name, ok, result.elapsed, result.command, output)


def build_checks(args: argparse.Namespace) -> dict[str, Callable[[], CheckResult]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    smoke_command = [
        sys.executable,
        "scripts/local_smoke.py",
        "--reset",
        "--port",
        str(args.port),
    ]
    if not args.http:
        smoke_command.append("--skip-http")
    checks = {
        "テスト": lambda: run_command("テスト", [sys.executable, "-m", "pytest", "tests", "-q"], env=env),
        "compile": lambda: run_command("compile", [sys.executable, "-m", "compileall", "src", "tests", "scripts"]),
        "差分チェック": lambda: run_command("差分チェック", ["git", "diff", "--check"]),
        "秘密情報スキャン": run_secret_scan,
        "13工程fixture E2E": lambda: run_command(
            "13工程fixture E2E",
            [sys.executable, "scripts/fixture_13_step_e2e.py", "--json"],
            env=env,
        ),
        "ローカルスモーク": lambda: run_command("ローカルスモーク", smoke_command, env=env),
    }
    if args.gh:
        gh_command = [sys.executable, "scripts/gh_guard.py"]
        if args.gh_switch:
            gh_command.append("--switch")
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
