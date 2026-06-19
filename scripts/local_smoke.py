from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.core import audit_context_store, audit_event_store, context_passport_from_text, guide_reply_from_text, import_visible_text, load_events, review_reply_intent, upsert_context_document
from discord_context_bridge.cli import read_clipboard_text, read_command_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="local 実運用前スモークをまとめて実行する")
    parser.add_argument("--store", type=Path, default=Path("/tmp/dcb-local-smoke.ndjson"))
    parser.add_argument("--context-store", type=Path, default=Path("/tmp/dcb-context-library-smoke.json"))
    parser.add_argument("--input", type=Path, default=ROOT / "tests/fixtures/discord_rich_copy.txt")
    parser.add_argument("--from-clipboard", action="store_true", help="--input ではなく local clipboard から可視テキストを読む")
    parser.add_argument("--source-command", help="--input ではなく local observer command から可視テキストを読む")
    parser.add_argument("--clipboard-command", default="pbpaste", help="clipboard 取得に使う local command")
    parser.add_argument("--guild", default="local-smoke")
    parser.add_argument("--channel", default="general")
    parser.add_argument("--server-context", type=Path, default=ROOT / "tests/fixtures/server_context.txt")
    parser.add_argument("--channel-context", type=Path, default=ROOT / "tests/fixtures/channel_context.txt")
    parser.add_argument("--thread-context", type=Path, default=ROOT / "tests/fixtures/thread_context_rules.txt")
    parser.add_argument("--draft", default="公開時期の前提を確認してから返信します。")
    parser.add_argument("--port", type=int, default=8017)
    parser.add_argument("--reset", action="store_true", help="既存の smoke store を消してから実行する")
    parser.add_argument("--skip-http", action="store_true", help="HTTP MCP 起動スモークを省略する")
    parser.add_argument("--json-output", action="store_true", help="確認用に生 JSON を表示する")
    return parser.parse_args()


def print_result(label: str, payload: dict, *, json_output: bool = False) -> None:
    labels = {
        "import": "取り込み",
        "audit": "安全監査",
        "review": "返信前レビュー",
        "guide": "返信ガイド",
        "passport": "文脈パスポート",
        "context_library": "文脈庫",
        "context_audit": "文脈庫監査",
        "http_mcp": "HTTP MCP確認",
    }
    print(f"\n== {labels.get(label, label)} ==")
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if label == "import":
        print(payload.get("message", "取り込みを確認しました。"))
        print(f"解析件数: {payload['parsed']} / 追加: {payload['appended']} / 重複: {payload['duplicate']}")
        for index, event in enumerate(payload.get("preview", [])[:3], start=1):
            print(f"{index}. {event['author_label']}: {event['text_snippet']}")
        print(f"保存先: {payload['store']}")
        return
    if label == "audit":
        print(payload.get("message", "安全監査を確認しました。"))
        print(payload.get("safe_for_tunnel_label", "監査結果を確認してください。"))
        print(f"イベント数: {payload['event_count']} / 要確認: {payload['issue_count']}")
        print(f"保存先: {payload['store']}")
        return
    if label == "review":
        print(payload.get("message", "返信前レビューを確認しました。"))
        print(payload.get("ok_to_reply_label", "返信可否を確認してください。"))
        print(payload.get("alignment_label", "文脈照合結果を確認してください。"))
        print(payload.get("missing_knowledge_label", "不足前提を確認してください。"))
        meaning = payload.get("likely_counterparty_meaning")
        if meaning:
            print(f"相手側の文脈: {meaning}")
        return
    if label == "guide":
        print(payload.get("message", "返信ガイドを確認しました。"))
        print(f"解析件数: {payload['parsed']}")
        print(f"相手側の文脈: {payload['counterparty_context']}")
        for action in payload["next_actions"]:
            print(f"- {action}")
        print(payload["send_capability_label"])
        return
    if label == "passport":
        print(payload.get("message", "文脈パスポートを確認しました。"))
        print(payload.get("external_context_used_label", "明示文脈の使用状況を確認してください。"))
        print(payload.get("context_sources_label", "文脈ソースを確認してください。"))
        print(payload.get("thread_purpose_label", "目的を確認してください。"))
        print(payload.get("conversation_flow_label", "流れを確認してください。"))
        print(payload.get("rule_notes_label", "ルール注意を確認してください。"))
        print(payload.get("people_temperature_label", "温度感を確認してください。"))
        print(payload["send_capability_label"])
        return
    if label == "context_library":
        print(payload.get("message", "文脈庫を確認しました。"))
        print(f"保存先: {payload['context_store']}")
        print(f"kind: {payload['entry']['kind']} / key: {payload['entry']['key']}")
        return
    if label == "context_audit":
        print(payload.get("message", "文脈庫監査を確認しました。"))
        print(payload.get("safe_for_tunnel_label", "監査結果を確認してください。"))
        print(f"文脈数: {payload['entry_count']} / 要確認: {payload['issue_count']}")
        return
    if label == "http_mcp":
        print(payload.get("message", "HTTP MCP の起動確認を実行しました。"))
        print(payload.get("initialize_label", "初期化結果を確認してください。"))
        print(payload.get("tools_label", "MCP tool の一覧を確認してください。"))
        print(f"port: {payload['port']}")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def post_mcp(port: int, payload: dict, session_id: str | None = None) -> tuple[dict[str, str], str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    req = request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=5) as response:
        return dict(response.headers), response.read().decode("utf-8")


def wait_for_mcp(port: int) -> tuple[str, str]:
    last_error = ""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "local-smoke", "version": "0.1"},
        },
    }
    for _ in range(40):
        try:
            headers, body = post_mcp(port, payload)
            session_id = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
            if session_id and "discord-context-bridge" in body:
                return session_id, body
        except Exception as exc:  # pragma: no cover - smoke diagnostic path
            last_error = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"HTTP MCP initialize に失敗しました: {last_error}")


def run_http_smoke(store: Path, port: int) -> dict:
    mcp_python = os.environ.get("DCB_MCP_PYTHON", sys.executable)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "from discord_context_bridge.mcp_server import main_http; "
        "raise SystemExit(main_http(["
        f"'--store', {str(store)!r}, '--host', '127.0.0.1', '--port', {str(port)!r}, "
        "'--path', '/mcp', '--require-safe-store']))"
    )
    process = subprocess.Popen(
        [mcp_python, "-c", code],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        try:
            session_id, initialize_body = wait_for_mcp(port)
        except Exception as exc:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - smoke diagnostic path
                process.kill()
                process.wait(timeout=5)
            server_output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(
                "HTTP MCP smoke の起動確認に失敗しました。\n"
                f"reason: {exc}\n"
                f"server_output:\n{server_output[-3000:]}"
            ) from exc
        _, tools_body = post_mcp(
            port,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session_id=session_id,
        )
        expected_tools = [
            "import_visible_discord_text",
            "get_fast_briefing",
            "audit_event_store_before_tunnel",
            "review_reply_before_send",
            "guide_reply_from_visible_text",
            "get_context_passport_from_visible_text",
            "upsert_context_library_entry",
            "list_context_library_entries",
            "audit_context_library_before_tunnel",
        ]
        missing = [tool for tool in expected_tools if tool not in tools_body]
        if missing:
            raise RuntimeError(f"HTTP MCP tools/list に不足があります: {missing}")
        return {
            "language": "ja",
            "message": "HTTP MCP の起動確認が完了しました。",
            "port": port,
            "session_id": session_id,
            "initialize_ok": "discord-context-bridge" in initialize_body,
            "initialize_label": "初期化に成功しました。",
            "tools_ok": True,
            "tools_label": "必要な MCP tool が揃っています。",
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - smoke diagnostic path
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    args = parse_args()
    if args.reset and args.store.exists():
        args.store.unlink()
    if args.reset and args.context_store.exists():
        args.context_store.unlink()

    if args.from_clipboard:
        text = read_clipboard_text(args.clipboard_command)
    elif args.source_command:
        text = read_command_text(args.source_command)
    else:
        text = args.input.read_text(encoding="utf-8")
    imported = import_visible_text(text, path=args.store, guild_label=args.guild, channel_label=args.channel)
    print_result("import", imported, json_output=args.json_output)

    audit = audit_event_store(args.store)
    print_result("audit", audit, json_output=args.json_output)
    if not audit["safe_for_tunnel"]:
        print("NG: event store が安全ではないため HTTP MCP smoke を止めます。", file=sys.stderr)
        return 2

    context_library = upsert_context_document(
        "server",
        "local-smoke",
        args.server_context.read_text(encoding="utf-8") if args.server_context else "",
        path=args.context_store,
        source="local-smoke",
    )
    print_result("context_library", context_library, json_output=args.json_output)

    context_audit = audit_context_store(args.context_store)
    print_result("context_audit", context_audit, json_output=args.json_output)
    if not context_audit["safe_for_tunnel"]:
        print("NG: 文脈庫が安全ではないため HTTP MCP smoke を止めます。", file=sys.stderr)
        return 2

    review = review_reply_intent(args.draft, load_events(args.store))
    print_result("review", review, json_output=args.json_output)

    guide = guide_reply_from_text(text, args.draft, guild_label=args.guild, channel_label=args.channel)
    print_result("guide", guide, json_output=args.json_output)

    passport = context_passport_from_text(
        text,
        guild_label=args.guild,
        channel_label=args.channel,
        server_context=args.server_context.read_text(encoding="utf-8") if args.server_context else "",
        channel_context=args.channel_context.read_text(encoding="utf-8") if args.channel_context else "",
        thread_context=args.thread_context.read_text(encoding="utf-8") if args.thread_context else "",
    )
    print_result("passport", passport, json_output=args.json_output)

    if not args.skip_http:
        http = run_http_smoke(args.store, args.port)
        print_result("http_mcp", http, json_output=args.json_output)

    print("\n完了: ローカルスモーク完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
