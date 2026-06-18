from pathlib import Path
import tomllib

import pytest

from discord_context_bridge import (
    DisabledCapability,
    fast_briefing,
    import_visible_text,
    load_events,
    parse_visible_text,
    review_reply_intent,
    send_message,
)
from discord_context_bridge.cli import build_parser
from discord_context_bridge import mcp_server

FIXTURE = Path(__file__).parent / "fixtures" / "visible_text.txt"
ROOT = Path(__file__).resolve().parents[1]


def test_parse_visible_text_supports_copied_blocks():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))

    assert [event.author_label for event in events] == ["member-a", "member-b", "member-c"]
    assert events[0].text_snippet == "Can you clarify the premise before we reply?"
    assert events[2].text_snippet == "I think the reply should mention the audience."
    assert all(event.actions_allowed == ["read"] for event in events)


def test_import_visible_text_appends_and_deduplicates(tmp_path):
    store = tmp_path / "events.ndjson"

    first = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)
    second = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    assert first["parsed"] == 3
    assert first["appended"] == 3
    assert first["language"] == "ja"
    assert second["duplicate"] == 3
    assert len(load_events(store)) == 3


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


def test_package_version_tracks_http_mcp_release():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert metadata["project"]["version"] == "0.2.0"
    assert "## 0.2.0 - 2026-06-18" in changelog
    assert "streamable HTTP MCP" in changelog


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
        "get_fast_briefing",
        "import_visible_discord_text",
        "review_reply_before_send",
    ]

    imported = server.tools["import_visible_discord_text"]("member-a: 前提を確認したいです")
    review = server.tools["review_reply_before_send"]("前提を確認してから返信します")

    assert imported["language"] == "ja"
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
