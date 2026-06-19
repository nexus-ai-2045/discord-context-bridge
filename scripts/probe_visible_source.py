from __future__ import annotations

import argparse
import json
import re
import shlex
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

OCR_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_+-]+$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discord 可視本文 adapter の実機準備を本文なしで確認する probe pack"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-command", help="live smoke で読む local adapter command")
    source.add_argument("--ocr-capture-region", help=argparse.SUPPRESS)
    source.add_argument(
        "--capture-profile",
        choices=["macos-screencapture-region"],
        help="既定の capture profile。現在は macOS の範囲指定 screencapture のみ",
    )
    parser.add_argument("--capture-region", help="macos-screencapture-region の範囲。形式は x,y,w,h")
    parser.add_argument("--ocr-command", help="{image} placeholder を受ける OCR command。fixture で検証する")
    parser.add_argument("--ocr-language", default="eng", help="capture profile で使う tesseract language")
    parser.add_argument("--ax-probe", action="store_true", help="macOS Accessibility probe を本文なしで実行する")
    parser.add_argument("--ax-timeout", type=float, default=3.0, help="Accessibility probe の timeout 秒")
    parser.add_argument("--ax-limit", type=int, default=3, help="Accessibility probe の window 候補数")
    parser.add_argument("--source-timeout", type=float, default=20.0, help="source command の timeout 秒")
    parser.add_argument("--min-parsed", type=int, default=1, help="live smoke の成功扱いに必要な最小解析件数")
    parser.add_argument("--json", action="store_true", help="JSON summary を出力する")
    return parser


def command_result(
    name: str,
    command: list[str],
    *,
    ok_codes: set[int] | None = None,
    timeout: float | None = None,
    json_summary: bool = False,
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
    if status == "pass" and json_summary:
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {}
        for key in (
            "parsed",
            "source_ready",
            "gate_verdict",
            "event_count",
            "text_output",
            "outbound",
            "any_ready",
            "candidate_count",
            "failure_stage",
            "reason",
        ):
            if key in payload:
                result[key] = payload[key]
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


def build_ocr_capture_source_command(*, region: str, language: str, timeout: float) -> str:
    if not OCR_LANGUAGE_RE.fullmatch(language):
        raise ValueError("ocr language は tesseract language code のみ指定してください。")
    ocr_command = f"tesseract {{image}} stdout -l {language}"
    return " ".join(
        [
            shlex.quote(sys.executable),
            "scripts/read_screenshot_ocr_text.py",
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            shlex.quote(region),
            "--ocr-command",
            shlex.quote(ocr_command),
            "--timeout",
            shlex.quote(str(timeout)),
        ]
    )


def live_smoke_check(source_command: str, *, source_timeout: float, min_parsed: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dcb-probe-") as tmpdir:
        store = Path(tmpdir) / "events.ndjson"
        result = command_result(
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
                "--min-parsed",
                str(min_parsed),
            ],
            timeout=source_timeout + 5,
            json_summary=True,
        )
    return result


def ax_probe_check(*, timeout: float, limit: int) -> dict[str, Any]:
    result = command_result(
        "macos_accessibility_probe",
        [
            sys.executable,
            "scripts/read_visible_discord_text.py",
            "--probe-macos-accessibility",
            "--json",
            "--no-focus",
            "--probe-limit",
            str(limit),
            "--timeout",
            str(timeout),
        ],
        ok_codes={0, 2},
        timeout=(timeout * max(limit, 1)) + 5,
        json_summary=True,
    )
    if result.get("exit_code") == 2:
        result["status"] = "warn"
        result["reason"] = result.get("failure_stage", result.get("reason", "not_ready"))
    elif result["status"] == "fail":
        result["status"] = "warn"
    return result


def summarize(checks: list[dict[str, Any]], *, capture_profile: dict[str, str] | None = None) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] == "fail"]
    warned = [check for check in checks if check["status"] == "warn"]
    summary = {
        "language": "ja",
        "message": "visible source probe pack が完了しました。",
        "overall": "fail" if failed else ("warn" if warned else "pass"),
        "checks": checks,
        "text_output": "omitted",
        "outbound": "disabled",
        "outbound_label": "この probe pack から Discord へ送信しません。",
    }
    if capture_profile:
        summary["capture_profile"] = capture_profile
    return summary


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
    source_command = args.source_command
    capture_region = args.ocr_capture_region
    capture_profile = None
    if args.capture_region and not args.capture_profile:
        checks.append({"name": "capture_profile", "status": "fail", "reason": "capture_profile_required"})
    if args.capture_profile and not args.capture_region:
        checks.append({"name": "capture_profile", "status": "fail", "reason": "capture_region_required"})
    if args.capture_profile and args.capture_region:
        capture_region = args.capture_region
    if capture_region:
        try:
            source_command = build_ocr_capture_source_command(
                region=capture_region,
                language=args.ocr_language,
                timeout=args.source_timeout,
            )
            capture_profile = {
                "name": "macos-screencapture-region",
                "region": capture_region,
                "image_output": "temporary",
                "text_output": "omitted",
                "outbound": "disabled",
            }
        except ValueError:
            checks.append({"name": "capture_profile", "status": "fail", "reason": "invalid_ocr_language"})
    if source_command:
        checks.append(live_smoke_check(source_command, source_timeout=args.source_timeout, min_parsed=args.min_parsed))
    if args.ax_probe:
        checks.append(ax_probe_check(timeout=args.ax_timeout, limit=args.ax_limit))

    summary = summarize(checks, capture_profile=capture_profile)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(summary)
    return 0 if summary["overall"] in {"pass", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
