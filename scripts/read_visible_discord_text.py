from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path


SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("discord_webhook_url", re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+", re.IGNORECASE)),
    ("discord_token", re.compile(r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,})\b")),
    ("discord_profile_path", re.compile(r"(?:Library/Application Support/Discord|\\AppData\\Roaming\\discord)", re.IGNORECASE)),
    ("discord_snowflake_id", re.compile(r"(?<!\d)\d{17,20}(?!\d)")),
    ("local_absolute_path", re.compile(r"(?:/(?:Users|home|private|tmp|var/folders|Volumes)/[^ \n]+|[A-Za-z]:\\[^ \n]+)")),
    ("authorization_header", re.compile(r"\b" + "Author" + r"ization\s*:\s*(?:" + "Bear" + r"er\s+)?[^ \n]+", re.IGNORECASE)),
    ("bearer_token_like", re.compile(r"\b" + "Bear" + r"er\s+[A-Za-z0-9._~+/=-]{10,}\b", re.IGNORECASE)),
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
    source.add_argument("--list-macos-windows", action="store_true", help="macOS Accessibility で Discord window 候補名だけを表示する")
    parser.add_argument("--clipboard-command", default="pbpaste", help="clipboard 取得 command。既定は pbpaste")
    parser.add_argument("--process-name", default="Discord", help="macOS Accessibility で読む process 名")
    parser.add_argument(
        "--window-name-contains",
        default="",
        help="macOS Accessibility で読む window 名の一部。未指定なら前面 window",
    )
    parser.add_argument("--window-index", type=int, default=0, help="macOS Accessibility で読む window 番号。0 の場合は名前 hint または前面 window")
    parser.add_argument("--min-chars", type=int, default=1, help="空読み扱いにする最小文字数")
    parser.add_argument("--timeout", type=float, default=15.0, help="local command / Accessibility 取得の最大秒数")
    parser.add_argument("--focused-only", action="store_true", help="macOS Accessibility で focused element だけを読む。重い window 全走査を避ける")
    parser.add_argument("--show-window-names", action="store_true", help="window 診断で raw window 名も出す。通常は使わない")
    parser.add_argument("--allow-unsafe", action="store_true", help="安全監査を通さず stdout へ出す。通常は使わない")
    return parser


def run_command(command: str, *, timeout: float | None = None) -> str:
    try:
        completed = subprocess.run(
            shlex.split(command),
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


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_macos_accessibility_script(
    process_name: str,
    window_name_contains: str = "",
    *,
    window_index: int = 0,
    focused_only: bool = False,
) -> str:
    process_literal = applescript_string(process_name)
    window_hint_literal = applescript_string(window_name_contains)
    window_index_literal = str(max(window_index, 0))
    focused_only_literal = "true" if focused_only else "false"
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
set targetWindowIndex to {window_index_literal}
set focusedOnly to {focused_only_literal}

tell application "System Events"
  if not (exists process {process_literal}) then error "process not found: " & {process_literal}
  tell process {process_literal}
    set AppleScript's text item delimiters to linefeed
    set frontmost to true
    delay 0.1
    set targetWindow to missing value
    if targetWindowIndex is greater than 0 then
      if (count of windows) is less than targetWindowIndex then error "window index not found: " & targetWindowIndex
      set targetWindow to window targetWindowIndex
    else if windowNameHint is not "" then
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
    if focusedOnly then error "focused element text not found"
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


def build_macos_window_list_script(process_name: str) -> str:
    process_literal = applescript_string(process_name)
    return f'''
tell application "System Events"
  if not (exists process {process_literal}) then error "process not found: " & {process_literal}
  tell process {process_literal}
    set AppleScript's text item delimiters to linefeed
    set windowLines to {{}}
    set windowIndex to 0
    repeat with candidateWindow in windows
      set windowIndex to windowIndex + 1
      set candidateName to ""
      try
        set candidateName to name of candidateWindow as text
      end try
      if candidateName is "" then set candidateName to "(名称なし)"
      set end of windowLines to (windowIndex as text) & tab & candidateName
    end repeat
    return windowLines as text
  end tell
end tell
'''


def read_macos_accessibility(
    process_name: str,
    window_name_contains: str = "",
    *,
    window_index: int = 0,
    timeout: float | None = None,
    focused_only: bool = False,
) -> str:
    script = build_macos_accessibility_script(
        process_name,
        window_name_contains,
        window_index=window_index,
        focused_only=focused_only,
    )
    return run_command("osascript -e " + shlex.quote(script), timeout=timeout)


def list_macos_windows(process_name: str, *, timeout: float | None = None) -> str:
    script = build_macos_window_list_script(process_name)
    return run_command("osascript -e " + shlex.quote(script), timeout=timeout)


def print_macos_window_candidates(text: str, *, show_names: bool = False, match_hint: str = "") -> None:
    print("Discord window candidates:")
    if not text.strip():
        print("- なし")
        return
    normalized_hint = match_hint.casefold()
    for line in text.splitlines():
        index, _, name = line.partition("\t")
        safe_name = name or "(名称なし)"
        matched = bool(normalized_hint and normalized_hint in safe_name.casefold())
        if show_names:
            print(f"- {index}: {safe_name}")
        else:
            title_present = safe_name != "(名称なし)"
            print(
                f"- {index}: title_present={str(title_present).lower()} "
                f"title_length={len(safe_name)} hint_match={str(matched).lower()}"
            )


def read_visible_text(args: argparse.Namespace) -> str:
    if args.input:
        return args.input.read_text(encoding="utf-8")
    if args.from_clipboard:
        return run_command(args.clipboard_command, timeout=args.timeout)
    if args.source_command:
        return run_command(args.source_command, timeout=args.timeout)
    if args.macos_accessibility:
        return read_macos_accessibility(
            args.process_name,
            args.window_name_contains,
            window_index=args.window_index,
            timeout=args.timeout,
            focused_only=args.focused_only,
        )
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
        if args.list_macos_windows:
            text = normalize_visible_text(list_macos_windows(args.process_name, timeout=args.timeout))
            if args.show_window_names:
                issues = audit_visible_text(text)
                if issues and not args.allow_unsafe:
                    print("安全監査に失敗したため window 候補名を stdout へ出しません。", file=sys.stderr)
                    print("issues: " + ", ".join(issues), file=sys.stderr)
                    return 2
            print_macos_window_candidates(
                text,
                show_names=args.show_window_names,
                match_hint=args.window_name_contains,
            )
            return 0
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
