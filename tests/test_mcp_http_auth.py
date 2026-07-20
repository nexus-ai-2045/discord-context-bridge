"""HTTP MCP transport の認証境界テスト。

- token 未設定 + opt-out なし → 起動拒否 (fail-closed)
- token 設定時 → BearerAuthASGI でラップされ、不一致 request は 401
"""

import asyncio

import pytest

from discord_context_bridge import mcp_server


class FakeFastMCP:
    def __init__(self, name, **settings):
        self.name = name
        self.settings = settings

    def tool(self):
        def register(func):
            return func

        return register

    def streamable_http_app(self):
        async def inner_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        return inner_app

    def run(self, **kwargs):  # pragma: no cover - 認証テストでは未使用
        raise AssertionError("token 設定時は server.run ではなく ASGI app 経路を使うこと")


def test_http_mcp_refuses_to_start_without_auth_token(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)
    monkeypatch.delenv(mcp_server.DEFAULT_HTTP_AUTH_TOKEN_ENV, raising=False)

    with pytest.raises(SystemExit, match="認証必須"):
        mcp_server.main_http(
            ["--store", str(tmp_path / "events.ndjson")],
            run=lambda app: None,
        )


def test_http_mcp_conflicting_store_flags_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    with pytest.raises(SystemExit, match="同時に指定できません"):
        mcp_server.main_http(
            [
                "--store",
                str(tmp_path / "events.ndjson"),
                "--require-safe-store",
                "--allow-unsafe-store",
            ],
            run=lambda app: None,
        )


def _asgi_http_scope(authorization: str | None) -> dict:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("utf-8")))
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}


async def _call_app(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_http_mcp_with_token_wraps_app_and_enforces_bearer(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)
    monkeypatch.setenv(mcp_server.DEFAULT_HTTP_AUTH_TOKEN_ENV, "test-secret-token")
    launched = []

    result = mcp_server.main_http(
        ["--store", str(tmp_path / "events.ndjson")],
        run=lambda app: launched.append(app),
    )

    assert result == 0
    assert len(launched) == 1
    app = launched[0]
    assert isinstance(app, mcp_server.BearerAuthASGI)

    authorized = asyncio.run(_call_app(app, _asgi_http_scope("Bearer test-secret-token")))
    assert authorized[0]["status"] == 200

    for bad in (None, "Bearer wrong-token", "test-secret-token", "Basic dXNlcg=="):
        denied = asyncio.run(_call_app(app, _asgi_http_scope(bad)))
        assert denied[0]["status"] == 401, f"認証なし/不一致が通過: {bad!r}"
        assert b"unauthorized" in denied[1]["body"]


def test_bearer_auth_asgi_passes_lifespan_and_rejects_websocket():
    inner_calls = []

    async def inner_app(scope, receive, send):
        inner_calls.append(scope["type"])

    app = mcp_server.BearerAuthASGI(inner_app, "token")

    asyncio.run(_call_app(app, {"type": "lifespan"}))
    assert inner_calls == ["lifespan"]

    sent = asyncio.run(_call_app(app, {"type": "websocket", "headers": []}))
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert inner_calls == ["lifespan"]
