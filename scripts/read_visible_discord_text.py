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
    parser.add_argument(
        "--window-name-contains",
        default="",
        help="macOS Accessibility で読む window 名の一部。未指定なら前面 window",
    )
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


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_macos_accessibility_script(process_name: str, window_name_contains: str = "") -> str:
    process_literal = applescript_string(process_name)
    window_hint_literal = applescript_string(window_name_contains)
    return f'''
on appendText(textValues, candidateText)
  if candidateText is missing value then return textValues
  set candidateText to candidateText as text
  if candidateText is not "" then set end of textValues to candidateText
  return textValues
end appendText

on collectText(uiElement, textValues)
  try
    set textValues to my appendText(textValues, value of uiElement)
  end try
  try
    set textValues to my appendText(textValues, name of uiElement)
  end try
  return textValues
end collectText

set windowNameHint to {window_hint_literal}

tell application "System Events"
  if not (exists process {process_literal}) then error "process not found: " & {process_literal}
  tell process {process_literal}
    set AppleScript's text item delimiters to linefeed
    set frontmost to true
    delay 0.1
    set targetWindow to missing value
    if windowNameHint is not "" then
      repeat with candidateWindow in windows
        try
          set candidateName to name of candidateWindow as text
          if candidateName contains windowNameHint then
            set targetWindow to candidateWindow
            exit repeat
          end if
        end try
      end repeat
      if targetWindow is missing value then error "window not found: " & windowNameHint
    else
      set targetWindow to front window
    end if
    try
      set focusedElement to value of attribute "AXFocusedUIElement"
      set focusedTexts to {{}}
      set focusedTexts to my collectText(focusedElement, focusedTexts)
      if focusedTexts is not {{}} then return focusedTexts as text
    end try
    try
      set windowTexts to {{}}
      repeat with uiElement in entire contents of targetWindow
        set windowTexts to my collectText(uiElement, windowTexts)
      end repeat
      return windowTexts as text
    end try
  end tell
end tell
'''


def read_macos_accessibility(process_name: str, window_name_contains: str = "") -> str:
    script = build_macos_accessibility_script(process_name, window_name_contains)
    return run_command("osascript -e " + shlex.quote(script))


def read_visible_text(args: argparse.Namespace) -> str:
    if args.input:
        return args.input.read_text(encoding="utf-8")
    if args.from_clipboard:
        return run_command(args.clipboard_command)
    if args.source_command:
        return run_command(args.source_command)
    if args.macos_accessibility:
        return read_macos_accessibility(args.process_name, args.window_name_contains)
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
