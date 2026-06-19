from __future__ import annotations

import argparse
import json
import os
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
    source.add_argument(
        "--macos-accessibility-auto",
        action="store_true",
        help="macOS Accessibility の focused / full 取得を短い timeout で順に試す",
    )
    source.add_argument("--list-macos-windows", action="store_true", help="macOS Accessibility で Discord window 候補名だけを表示する")
    source.add_argument("--probe-macos-accessibility", action="store_true", help="本文を出さずに macOS Accessibility の候補別取得可否を確認する")
    parser.add_argument("--json", action="store_true", help="probe / list 系の結果を機械可読 JSON で出す")
    parser.add_argument("--clipboard-command", default="pbpaste", help="clipboard 取得 command。既定は pbpaste")
    parser.add_argument("--process-name", default="Discord", help="macOS Accessibility で読む process 名")
    parser.add_argument(
        "--window-name-contains",
        default="",
        help="macOS Accessibility で読む window 名の一部。未指定なら前面 window",
    )
    parser.add_argument("--window-index", type=int, default=0, help="macOS Accessibility で読む window 番号。0 の場合は名前 hint または前面 window")
    parser.add_argument("--probe-limit", type=int, default=4, help="macOS Accessibility probe で試す最大 window 数")
    parser.add_argument("--min-chars", type=int, default=1, help="空読み扱いにする最小文字数")
    parser.add_argument("--timeout", type=float, default=15.0, help="local command / Accessibility 取得の最大秒数")
    parser.add_argument("--focused-only", action="store_true", help="macOS Accessibility で focused element だけを読む。重い window 全走査を避ける")
    parser.add_argument("--no-focus", action="store_true", help="macOS Accessibility で Discord を前面化せずに読む")
    parser.add_argument("--show-window-names", action="store_true", help="window 診断で raw window 名も出す。通常は使わない")
    parser.add_argument(
        "--allow-unsafe",
        action="store_true",
        help="安全監査を通さず stdout へ出す。DCB_ALLOW_UNSAFE_OUTPUT=1 が必要です。",
    )
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
        reason = safe_probe_reason(RuntimeError(completed.stderr.strip()))
        raise RuntimeError(f"command failed with exit code {completed.returncode}: reason={reason}")
    return completed.stdout


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_macos_accessibility_script(
    process_name: str,
    window_name_contains: str = "",
    *,
    window_index: int = 0,
    focused_only: bool = False,
    focus_app: bool = True,
) -> str:
    process_literal = applescript_string(process_name)
    window_hint_literal = applescript_string(window_name_contains)
    window_index_literal = str(max(window_index, 0))
    focused_only_literal = "true" if focused_only else "false"
    focus_app_literal = "true" if focus_app else "false"
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
set shouldFocusApp to {focus_app_literal}

tell application "System Events"
  if not (exists process {process_literal}) then error "process not found: " & {process_literal}
  tell process {process_literal}
    set AppleScript's text item delimiters to linefeed
    if shouldFocusApp then
      set frontmost to true
      delay 0.1
    end if
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
      if shouldFocusApp then
        set targetWindow to front window
      else
        if (count of windows) is less than 1 then error "window not found without focus"
        set targetWindow to window 1
      end if
    end if
    if shouldFocusApp then
      try
        set focusedElement to value of attribute "AXFocusedUIElement"
        set focusedTexts to {{}}
        set focusedTexts to my collectText(focusedElement, focusedTexts)
        if focusedTexts is not {{}} then return focusedTexts as text
      end try
      if focusedOnly then error "focused element text not found"
    else if focusedOnly then
      error "focused-only requires app focus"
    end if
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
    focus_app: bool = True,
) -> str:
    script = build_macos_accessibility_script(
        process_name,
        window_name_contains,
        window_index=window_index,
        focused_only=focused_only,
        focus_app=focus_app,
    )
    return run_command("osascript -e " + shlex.quote(script), timeout=timeout)


def read_macos_accessibility_auto(
    process_name: str,
    window_name_contains: str = "",
    *,
    window_index: int = 0,
    timeout: float | None = None,
    focus_app: bool = True,
) -> str:
    attempts: list[tuple[str, str, int, bool]] = []
    if focus_app:
        attempts.append(("selected focused", window_name_contains, window_index, True))
    attempts.append(("selected full", window_name_contains, window_index, False))
    if focus_app and (window_index or window_name_contains):
        attempts.append(("front focused", "", 0, True))
        attempts.append(("front full", "", 0, False))

    errors: list[str] = []
    for label, hint, index, focused_only in attempts:
        try:
            text = read_macos_accessibility(
                process_name,
                hint,
                window_index=index,
                timeout=timeout,
                focused_only=focused_only,
                focus_app=focus_app,
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        if text.strip():
            return text
        errors.append(f"{label}: empty")
    raise RuntimeError("macOS Accessibility auto fallback failed: " + "; ".join(errors))


def list_macos_windows(process_name: str, *, timeout: float | None = None) -> str:
    script = build_macos_window_list_script(process_name)
    return run_command("osascript -e " + shlex.quote(script), timeout=timeout)


def parse_macos_window_candidates(text: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for line in text.splitlines():
        index_text, _, name = line.partition("\t")
        try:
            index = int(index_text)
        except ValueError:
            continue
        candidates.append((index, name or "(名称なし)"))
    return candidates


def preflight_macos_window_selection(
    process_name: str,
    *,
    window_index: int = 0,
    timeout: float | None = None,
) -> list[tuple[int, str]]:
    candidates = parse_macos_window_candidates(list_macos_windows(process_name, timeout=timeout))
    if not candidates:
        raise RuntimeError("Discord window candidates not found. Discord の対象画面を開いてから再実行してください。")
    if window_index and all(index != window_index for index, _ in candidates):
        raise RuntimeError(
            f"window index not found before scan: {window_index}; candidate_count={len(candidates)}"
        )
    return candidates


def print_macos_window_candidates(text: str, *, show_names: bool = False, match_hint: str = "") -> None:
    print("Discord window candidates:")
    candidates = parse_macos_window_candidates(text)
    if not candidates:
        print("- なし")
        return
    normalized_hint = match_hint.casefold()
    for index, name in candidates:
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


def build_macos_window_list_payload(
    candidates: list[tuple[int, str]],
    *,
    match_hint: str = "",
    reason: str = "",
) -> dict[str, object]:
    normalized_hint = match_hint.casefold()
    payload: dict[str, object] = {
        "schema": "discord_macos_window_list.v1",
        "ok": bool(candidates),
        "candidate_count": len(candidates),
        "windows": [
            {
                "index": index,
                "title_present": name != "(名称なし)",
                "title_length": len(name),
                "hint_match": bool(normalized_hint and normalized_hint in name.casefold()),
            }
            for index, name in candidates
        ],
        "text_output": "omitted",
        "outbound": "disabled",
    }
    if not candidates:
        safe_reason = reason or "not_found"
        payload["failure_stage"] = probe_failure_stage(safe_reason)
        payload["reason"] = safe_reason
    return payload


def probe_macos_accessibility_routes(
    process_name: str,
    *,
    timeout: float | None = None,
    focus_app: bool = True,
    limit: int = 4,
) -> tuple[list[dict[str, object]], bool]:
    candidates = preflight_macos_window_selection(process_name, timeout=timeout)
    rows: list[dict[str, object]] = []
    any_ready = False
    for index, _name in candidates[: max(limit, 0)]:
        row: dict[str, object] = {"index": index, "status": "unknown", "chars": 0, "lines": 0}
        try:
            text = normalize_visible_text(
                read_macos_accessibility_auto(
                    process_name,
                    window_index=index,
                    timeout=timeout,
                    focus_app=focus_app,
                )
            )
        except Exception as exc:
            row["status"] = "error"
            row["reason"] = safe_probe_reason(exc)
        else:
            issues = audit_visible_text(text)
            if issues:
                row["status"] = "unsafe"
                row["issues"] = ",".join(issues)
            elif text.strip():
                row["status"] = "ready"
                row["chars"] = len(text)
                row["lines"] = len(text.splitlines())
                any_ready = True
            else:
                row["status"] = "empty"
        rows.append(row)
    return rows, any_ready


def safe_probe_reason(error: Exception) -> str:
    text = str(error)
    if "安全監査により詳細を省略しました。" in text:
        return "安全監査により詳細を省略しました。"
    if audit_visible_text(text):
        return "安全監査により詳細を省略しました。"
    if "timed out" in text:
        return "timeout"
    if "empty" in text:
        return "empty"
    if "not_found" in text or "not found" in text:
        return "not_found"
    if "permission" in text or "not authorized" in text or "not authorised" in text:
        return "permission"
    if "focused-only" in text or "focused element" in text:
        return "focused_unavailable"
    return "adapter_error"


def unsafe_bypass_enabled(allow_unsafe: bool) -> bool:
    return allow_unsafe and os.environ.get("DCB_ALLOW_UNSAFE_OUTPUT") == "1"


def print_macos_accessibility_probe(rows: list[dict[str, object]], *, focus_app: bool) -> None:
    print("Discord Accessibility route probe:")
    print(f"focus_app: {str(focus_app).lower()}")
    if not rows:
        print("- なし")
        return
    for row in rows:
        detail = f"- {row['index']}: status={row['status']} chars={row['chars']} lines={row['lines']}"
        if "issues" in row:
            detail += f" issues={row['issues']}"
        if "reason" in row:
            detail += f" reason={row['reason']}"
        print(detail)


def probe_failure_stage(reason: str) -> str:
    return {
        "not_found": "dependency_missing",
        "timeout": "timeout",
        "permission": "permission",
        "empty": "ocr_empty",
        "focused_unavailable": "unsupported_screen_state",
    }.get(reason, "capture_failed")


def build_macos_accessibility_probe_payload(
    rows: list[dict[str, object]],
    *,
    focus_app: bool,
    reason: str = "",
) -> dict[str, object]:
    any_ready = any(row.get("status") == "ready" for row in rows)
    payload: dict[str, object] = {
        "schema": "discord_macos_accessibility_probe.v1",
        "ok": any_ready,
        "any_ready": any_ready,
        "focus_app": focus_app,
        "candidate_count": len(rows),
        "rows": rows,
        "text_output": "omitted",
        "outbound": "disabled",
    }
    if not any_ready:
        first_reason = reason
        if not first_reason:
            first_reason = next((str(row.get("reason")) for row in rows if row.get("reason")), "empty")
        payload["failure_stage"] = probe_failure_stage(first_reason)
        payload["reason"] = first_reason
    return payload


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
            focus_app=not args.no_focus,
        )
    if args.macos_accessibility_auto:
        preflight_macos_window_selection(
            args.process_name,
            window_index=args.window_index,
            timeout=args.timeout,
        )
        return read_macos_accessibility_auto(
            args.process_name,
            args.window_name_contains,
            window_index=args.window_index,
            timeout=args.timeout,
            focus_app=not args.no_focus,
        )
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise RuntimeError(
        "input source が未指定です。--input / --from-clipboard / --source-command / "
        "--macos-accessibility / --macos-accessibility-auto のいずれかを指定してください。"
    )


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
        if args.probe_macos_accessibility:
            try:
                rows, any_ready = probe_macos_accessibility_routes(
                    args.process_name,
                    timeout=args.timeout,
                    focus_app=not args.no_focus,
                    limit=args.probe_limit,
                )
            except Exception as exc:
                reason = safe_probe_reason(exc)
                payload = build_macos_accessibility_probe_payload(
                    [],
                    focus_app=not args.no_focus,
                    reason=reason,
                )
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                else:
                    print(f"Discord Accessibility route probe failed: {reason}", file=sys.stderr)
                return 2
            if args.json:
                payload = build_macos_accessibility_probe_payload(rows, focus_app=not args.no_focus)
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                return 0 if payload["ok"] else 2
            print_macos_accessibility_probe(rows, focus_app=not args.no_focus)
            return 0 if any_ready else 2
        if args.list_macos_windows:
            try:
                text = normalize_visible_text(list_macos_windows(args.process_name, timeout=args.timeout))
            except Exception as exc:
                reason = safe_probe_reason(exc)
                if args.json:
                    payload = build_macos_window_list_payload([], match_hint=args.window_name_contains, reason=reason)
                    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                else:
                    print(f"Discord window candidate list failed: {reason}", file=sys.stderr)
                return 2
            if args.show_window_names:
                issues = audit_visible_text(text)
                if issues and not unsafe_bypass_enabled(args.allow_unsafe):
                    print("安全監査に失敗したため window 候補名を stdout へ出しません。", file=sys.stderr)
                    print("issues: " + ", ".join(issues), file=sys.stderr)
                    return 2
            if args.json:
                candidates = parse_macos_window_candidates(text)
                payload = build_macos_window_list_payload(candidates, match_hint=args.window_name_contains)
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                return 0 if payload["ok"] else 2
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
        if issues and not unsafe_bypass_enabled(args.allow_unsafe):
            print("安全監査に失敗したため stdout へ出しません。", file=sys.stderr)
            print("issues: " + ", ".join(issues), file=sys.stderr)
            return 2
        print(text, end="")
        return 0
    except Exception as exc:
        print(f"Discord 可視テキストの取得に失敗しました: {safe_probe_reason(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
