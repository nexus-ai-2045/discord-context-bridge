from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from read_visible_discord_text import audit_visible_text, normalize_visible_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="private screenshot/OCR command から Discord 可視本文を読む public-safe runner"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="OCR する既存画像。実画像は commit しない")
    source.add_argument("--screenshot-command", help="{image} placeholder に画像 path を渡す private capture command")
    parser.add_argument("--ocr-command", required=True, help="{image} placeholder に画像 path を渡す OCR command")
    parser.add_argument("--timeout", type=float, default=20.0, help="各 command の最大秒数")
    parser.add_argument("--min-chars", type=int, default=1, help="空読み扱いにする最小文字数")
    parser.add_argument(
        "--allow-unsafe",
        action="store_true",
        help="安全監査を通さず stdout へ出す。DCB_ALLOW_UNSAFE_OUTPUT=1 が必要です。",
    )
    return parser


def format_command(command_template: str, *, image: Path) -> list[str]:
    if "{image}" not in command_template:
        raise RuntimeError("command template には {image} placeholder が必要です。")
    return shlex.split(command_template.format(image=str(image)))


def safe_command_error(exc: Exception) -> str:
    text = str(exc)
    if audit_visible_text(text):
        return "安全監査により詳細を省略しました。"
    if "timed out" in text:
        return "timeout"
    if "placeholder" in text:
        return "placeholder_missing"
    return "command_failed"


def run_private_command(command_template: str, *, image: Path, timeout: float) -> str:
    command = format_command(command_template, image=image)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout:g}s") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: stderr={completed.stderr.strip()}")
    return completed.stdout


def read_screenshot_ocr_text(
    *,
    image: Path | None,
    screenshot_command: str | None,
    ocr_command: str,
    timeout: float,
) -> str:
    if image is not None:
        return run_private_command(ocr_command, image=image, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix="dcb-ocr-") as tmpdir:
        capture_path = Path(tmpdir) / "capture.png"
        assert screenshot_command is not None
        run_private_command(screenshot_command, image=capture_path, timeout=timeout)
        return run_private_command(ocr_command, image=capture_path, timeout=timeout)


def unsafe_bypass_enabled(allow_unsafe: bool) -> bool:
    return allow_unsafe and os.environ.get("DCB_ALLOW_UNSAFE_OUTPUT") == "1"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = normalize_visible_text(
            read_screenshot_ocr_text(
                image=args.image,
                screenshot_command=args.screenshot_command,
                ocr_command=args.ocr_command,
                timeout=args.timeout,
            )
        )
        if len(text.strip()) < args.min_chars:
            print("OCR 結果が空です。対象範囲または OCR command を確認してください。", file=sys.stderr)
            return 2
        issues = audit_visible_text(text)
        if issues and not unsafe_bypass_enabled(args.allow_unsafe):
            print("安全監査に失敗したため stdout へ出しません。", file=sys.stderr)
            print("issues: " + ", ".join(issues), file=sys.stderr)
            return 2
        print(text, end="")
        return 0
    except Exception as exc:
        print(f"screenshot/OCR 取得に失敗しました: {safe_command_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
