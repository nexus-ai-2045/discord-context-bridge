from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path


SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("discord_webhook_url", re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+", re.IGNORECASE)),
    ("discord_token_like", re.compile(r"\b(?:mfa\.[A-Za-z0-9_-]+|[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27})\b")),
    ("discord_profile_path", re.compile(r"(?:Library/Application Support/Discord|\\AppData\\Roaming\\discord)", re.IGNORECASE)),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="現在見えている Discord 本文を stdout に出す public-safe adapter"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="テストや smoke 用に読み込む可視テキストファイル")
    source.add_argument("--from-clipboard", action="store_true", help="local clipboard から可視テキストを読む")
    source.add_argument("--source-command", help="可視テキストを出力する private command を呼び出す")
    source.add_argument("--macos-accessibility", action="store_true", help="macOS Accessibility で Discord の前面ウィンドウから読む")
    parser.add_argument("--clipboard-command", default="pbpaste", help="clipboard 取得 command。既定は pbpaste")
    parser.add_argument("--process-name", default="Discord", help="macOS Accessibility で読む process 名")
    parser.add_argument("--min-chars", type=int, default=1, help="空読み扱いにする最小文字数")
    parser.add_argument("--allow-unsafe", action="store_true", help="安全監査を通さず stdout へ出す。通常は使わない")
    return parser


def run_command(command: str) -> str:
    completed = subprocess.run(
        shlex.split(command),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {command!r} stderr={completed.stderr.strip()}")
    return completed.stdout


def read_macos_accessibility(process_name: str) -> str:
    script = f'''
tell application "System Events"
  if not (exists process "{process_name}") then error "process not found: {process_name}"
  tell process "{process_name}"
    set frontmost to true
    delay 0.1
    try
      set focusedText to value of focused UI element as text
      if focusedText is not "" then return focusedText
    end try
    try
      set windowText to value of every static text of front window as text
      return windowText
    end try
  end tell
end tell
'''
    return run_command("osascript -e " + shlex.quote(script))


def read_visible_text(args: argparse.Namespace) -> str:
    if args.input:
        return args.input.read_text(encoding="utf-8")
    if args.from_clipboard:
        return run_command(args.clipboard_command)
    if args.source_command:
        return run_command(args.source_command)
    if args.macos_accessibility:
        return read_macos_accessibility(args.process_name)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise RuntimeError("input source が未指定です。--input / --from-clipboard / --source-command / --macos-accessibility のいずれかを指定してください。")


def audit_visible_text(text: str) -> list[str]:
    return [name for name, pattern in SENSITIVE_PATTERNS if pattern.search(text)]


def normalize_visible_text(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = normalize_visible_text(read_visible_text(args))
        if len(text.strip()) < args.min_chars:
            print("Discord 可視テキストが空です。Discord の対象スレッドを表示してから再実行してください。", file=sys.stderr)
            return 2
        issues = audit_visible_text(text)
        if issues and not args.allow_unsafe:
            print("安全監査に失敗したため stdout へ出しません。", file=sys.stderr)
            print("issues: " + ", ".join(issues), file=sys.stderr)
            return 2
        print(text, end="")
        return 0
    except Exception as exc:
        print(f"Discord 可視テキストの取得に失敗しました: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
