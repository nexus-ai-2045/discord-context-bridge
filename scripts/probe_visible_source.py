from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.cli import safe_command_failure_reason


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discord 可視本文 adapter の実機準備を本文なしで確認する probe pack"
    )
    parser.add_argument("--source-command", help="live smoke で読む local adapter command")
    parser.add_argument("--ocr-command", help="{image} placeholder を受ける OCR command。fixture で検証する")
    parser.add_argument("--ax-probe", action="store_true", help="macOS Accessibility probe を本文なしで実行する")
    parser.add_argument("--ax-timeout", type=float, default=3.0, help="Accessibility probe の timeout 秒")
    parser.add_argument("--ax-limit", type=int, default=3, help="Accessibility probe の window 候補数")
    parser.add_argument("--source-timeout", type=float, default=20.0, help="source command の timeout 秒")
    parser.add_argument("--json", action="store_true", help="JSON summary を出力する")
    return parser


def command_result(
    name: str,
    command: list[str],
    *,
    ok_codes: set[int] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    ok_codes = ok_codes or {0}
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "fail", "reason": "timeout"}
    except OSError:
        return {"name": name, "status": "fail", "reason": "not_found"}

    status = "pass" if completed.returncode in ok_codes else "fail"
    result: dict[str, Any] = {
        "name": name,
        "status": status,
        "exit_code": completed.returncode,
    }
    if status != "pass":
        result["reason"] = safe_command_failure_reason(completed.stderr)
    return result


def dependency_checks() -> list[dict[str, Any]]:
    checks = [
        command_result("python", [sys.executable, "--version"]),
        command_result("screencapture", ["/usr/sbin/screencapture", "-h"], ok_codes={0, 1}),
    ]
    tesseract = command_result("tesseract", ["tesseract", "--version"])
    if tesseract["status"] != "pass":
        tesseract["status"] = "warn"
    checks.append(tesseract)
    return checks


def ocr_fixture_check(ocr_command: str) -> dict[str, Any]:
    fixture = ROOT / "tests" / "fixtures" / "discord_rich_copy.txt"
    return command_result(
        "ocr_fixture",
        [
            sys.executable,
            "scripts/read_screenshot_ocr_text.py",
            "--image",
            str(fixture),
            "--ocr-command",
            ocr_command,
            "--min-chars",
            "1",
        ],
    )


def live_smoke_check(source_command: str, *, source_timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dcb-probe-") as tmpdir:
        store = Path(tmpdir) / "events.ndjson"
        return command_result(
            "live_ops_smoke",
            [
                sys.executable,
                "scripts/live_ops_smoke.py",
                "--reset",
                "--json",
                "--store",
                str(store),
                "--source-command",
                source_command,
                "--source-timeout",
                str(source_timeout),
            ],
            timeout=source_timeout + 5,
        )


def ax_probe_check(*, timeout: float, limit: int) -> dict[str, Any]:
    result = command_result(
        "macos_accessibility_probe",
        [
            sys.executable,
            "scripts/read_visible_discord_text.py",
            "--probe-macos-accessibility",
            "--no-focus",
            "--probe-limit",
            str(limit),
            "--timeout",
            str(timeout),
        ],
        ok_codes={0, 2},
        timeout=(timeout * max(limit, 1)) + 5,
    )
    if result["status"] == "fail":
        result["status"] = "warn"
    return result


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] == "fail"]
    warned = [check for check in checks if check["status"] == "warn"]
    return {
        "language": "ja",
        "message": "visible source probe pack が完了しました。",
        "overall": "fail" if failed else ("warn" if warned else "pass"),
        "checks": checks,
        "text_output": "omitted",
        "outbound": "disabled",
        "outbound_label": "この probe pack から Discord へ送信しません。",
    }


def print_human(summary: dict[str, Any]) -> None:
    print(summary["message"])
    print(f"overall: {summary['overall']}")
    for check in summary["checks"]:
        line = f"- {check['name']}: {check['status']}"
        if "reason" in check:
            line += f" ({check['reason']})"
        print(line)
    print(summary["outbound_label"])
    print("message text: omitted")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checks = dependency_checks()
    if args.ocr_command:
        checks.append(ocr_fixture_check(args.ocr_command))
    if args.source_command:
        checks.append(live_smoke_check(args.source_command, source_timeout=args.source_timeout))
    if args.ax_probe:
        checks.append(ax_probe_check(timeout=args.ax_timeout, limit=args.ax_limit))

    summary = summarize(checks)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(summary)
    return 0 if summary["overall"] in {"pass", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
