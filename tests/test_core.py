from pathlib import Path
import tomllib

import pytest

from discord_context_bridge import (
    DisabledCapability,
    audit_event_store,
    fast_briefing,
    import_visible_text,
    load_events,
    parse_visible_text,
    review_reply_intent,
    send_message,
)
from discord_context_bridge.cli import build_parser
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge import mcp_server

FIXTURE = Path(__file__).parent / "fixtures" / "visible_text.txt"
RICH_COPY_FIXTURE = Path(__file__).parent / "fixtures" / "discord_rich_copy.txt"
ROOT = Path(__file__).resolve().parents[1]


def test_parse_visible_text_supports_copied_blocks():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))

    assert [event.author_label for event in events] == ["member-a", "member-b", "member-c"]
    assert events[0].text_snippet == "Can you clarify the premise before we reply?"
    assert events[2].text_snippet == "I think the reply should mention the audience."
    assert all(event.actions_allowed == ["read"] for event in events)


def test_parse_visible_text_supports_discord_rich_copy_metadata():
    events = parse_visible_text(RICH_COPY_FIXTURE.read_text(encoding="utf-8"))

    assert [event.author_label for event in events] == ["member-a", "member-b", "member-c"]
    assert events[0].text_snippet == "前提を確認してから返事したいです。 公開時期の話ですよね。"
    assert events[1].text_snippet == "そうです。相手に伝わる言い方にしたいです。"
    assert events[2].text_snippet == "返信前に文脈を揃えましょう。"


def test_import_visible_text_appends_and_deduplicates(tmp_path):
    store = tmp_path / "events.ndjson"

    first = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)
    second = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    assert first["parsed"] == 3
    assert first["appended"] == 3
    assert first["language"] == "ja"
    assert second["duplicate"] == 3
    assert len(load_events(store)) == 3


def test_import_visible_text_dry_run_previews_without_writing(tmp_path):
    store = tmp_path / "events.ndjson"

    result = import_visible_text(RICH_COPY_FIXTURE.read_text(encoding="utf-8"), path=store, dry_run=True)

    assert result["dry_run"] is True
    assert result["parsed"] == 3
    assert result["appended"] == 0
    assert result["preview"][0]["author_label"] == "member-a"
    assert result["briefing"]["event_count"] == 3
    assert not store.exists()


def test_audit_event_store_reports_safe_empty_store(tmp_path):
    report = audit_event_store(tmp_path / "events.ndjson")

    assert report["safe_for_tunnel"] is True
    assert report["event_count"] == 0
    assert report["issues"] == []


def test_audit_event_store_flags_private_identifiers(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(
        "\n".join(
            [
                "member-a: webhook is https://discord.com/api/webhooks/123456789012345678/token",
                "member-b: user id 987654321098765432 should not be stored",
                "member-c: local path /Users/example/Library/Application Support/Discord",
            ]
        ),
        path=store,
    )

    report = audit_event_store(store)

    assert report["safe_for_tunnel"] is False
    assert report["event_count"] == 3
    assert [issue["kind"] for issue in report["issues"]] == [
        "discord_webhook_url",
        "discord_snowflake_id",
        "local_absolute_path",
    ]


def test_fast_briefing_and_reply_review(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    briefing = fast_briefing(load_events(store))
    review = review_reply_intent(
        "I understand this is about launch timing and my reply will ask for the premise.",
        load_events(store),
    )

    assert briefing["event_count"] == 3
    assert briefing["language"] == "ja"
    assert review["language"] == "ja"
    assert review["ok_to_reply"] == "likely_ok"


def test_send_message_is_disabled():
    with pytest.raises(DisabledCapability):
        send_message("hello")


def test_user_facing_docs_declare_japanese_default():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    checklist = (ROOT / "PUBLIC_RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "基本言語は日本語" in readme
    assert "Language Boundary" in checklist
    assert "CLI / API のユーザー向けメッセージが日本語" in checklist


def test_package_version_tracks_current_release():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert metadata["project"]["version"] == "0.4.0"
    assert "## 0.4.0 - 2026-06-19" in changelog
    assert "audit-store" in changelog


def test_user_facing_runtime_messages_are_japanese():
    gap = review_reply_intent("", [])

    assert gap["language"] == "ja"
    assert gap["missing_knowledge"] == ["共有前提", "返信対象"]
    assert gap["suggested_correction"] == "直近の文脈はまだ取り込まれていません。"

    with pytest.raises(DisabledCapability, match="Discord への送信機能"):
        send_message("hello")


def test_cli_help_uses_japanese_user_facing_text():
    help_text = build_parser().format_help()

    assert "可視会話テキスト" in help_text
    assert "直近文脈" in help_text
    assert "audit-store" in help_text


def test_cli_audit_store_returns_nonzero_when_unsafe(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text("member-a: https://discord.com/api/webhooks/123456789012345678/token", path=store)

    result = cli_main(["--store", str(store), "audit-store"])
    output = capsys.readouterr().out

    assert result == 2
    assert '"safe_for_tunnel": false' in output


def test_mcp_dependency_error_is_japanese(monkeypatch):
    def missing_fastmcp():
        raise RuntimeError(mcp_server.MCP_DEPENDENCY_HELP)

    monkeypatch.setattr(mcp_server, "_load_fastmcp", missing_fastmcp)

    with pytest.raises(RuntimeError, match="MCP server を使うには"):
        mcp_server.build_server()


def test_mcp_server_registers_context_tools(monkeypatch, tmp_path):
    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings
            self.tools = {}

        def tool(self):
            def register(func):
                self.tools[func.__name__] = func
                return func

            return register

        def run(self):
            raise AssertionError("unit test should not start stdio server")

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    server = mcp_server.build_server(store=tmp_path / "events.ndjson")

    assert server.name == "discord-context-bridge"
    assert server.settings["host"] == "127.0.0.1"
    assert server.settings["port"] == 8000
    assert server.settings["streamable_http_path"] == "/mcp"
    assert sorted(server.tools) == [
        "audit_event_store_before_tunnel",
        "get_fast_briefing",
        "import_visible_discord_text",
        "review_reply_before_send",
    ]

    imported = server.tools["import_visible_discord_text"]("member-a: 前提を確認したいです", dry_run=True)
    audit = server.tools["audit_event_store_before_tunnel"]()
    review = server.tools["review_reply_before_send"]("前提を確認してから返信します")

    assert imported["language"] == "ja"
    assert imported["dry_run"] is True
    assert audit["safe_for_tunnel"] is True
    assert review["language"] == "ja"


def test_http_mcp_entrypoint_uses_streamable_http(monkeypatch, tmp_path):
    calls = []

    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings

        def tool(self):
            def register(func):
                return func

            return register

        def run(self, **kwargs):
            calls.append((self, kwargs))

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    result = mcp_server.main_http(
        [
            "--store",
            str(tmp_path / "events.ndjson"),
            "--host",
            "0.0.0.0",
            "--port",
            "8787",
            "--path",
            "/mcp",
        ]
    )

    assert result == 0
    server, run_kwargs = calls[0]
    assert server.settings["host"] == "0.0.0.0"
    assert server.settings["port"] == 8787
    assert server.settings["streamable_http_path"] == "/mcp"
    assert run_kwargs == {"transport": "streamable-http", "mount_path": "/mcp"}
    assert "streamable HTTP MCP server" in mcp_server.build_http_parser().format_help()
