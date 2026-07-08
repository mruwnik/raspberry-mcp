"""Stateless tools/call over streamable HTTP with a bearer token."""

import json
import sys
import time

import pytest
from starlette.testclient import TestClient


def _purge_local_mcp_modules():
    """Drop cached local_mcp modules so settings re-read the environment."""
    for name in [m for m in sys.modules if m.startswith("local_mcp")]:
        del sys.modules[name]


@pytest.fixture
def client_and_token(temp_dir, monkeypatch):
    """Server app in stateless mode, backed by a temp token DB with one token."""
    monkeypatch.setenv("ANIME_BASE_PATH", str(temp_dir))
    monkeypatch.setenv("LOCAL_MCP_TOKEN_DB", str(temp_dir / ".token_db.json"))
    monkeypatch.setenv("LOCAL_MCP_HTPASSWD", str(temp_dir / ".htpasswd"))

    # Dirs/files the anime tools expect under ANIME_BASE_PATH
    (temp_dir / "stalled").mkdir()
    (temp_dir / ".watch" / "start").mkdir(parents=True)
    (temp_dir / ".anime_history").touch()

    from local_mcp.token_db import StoredToken, TokenDB

    db = TokenDB(temp_dir / ".token_db.json")
    db.set_token(
        "test-token",
        StoredToken(
            token="test-token",
            user="tester",
            scopes=["user"],
            expires_at=time.time() + 3600,
            client_id="test",
        ),
    )

    # Rebuild the server stack against the patched environment
    _purge_local_mcp_modules()
    import local_mcp.server as server

    app = server.mcp.http_app(
        transport="streamable-http",
        path="/mcp",
        middleware=server.custom_middleware,
        stateless_http=True,
    )
    with TestClient(app) as tc:
        yield tc, "test-token"

    # Don't leak modules built against the temp env into later tests
    _purge_local_mcp_modules()


def _parse_sse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"No data line in: {text!r}")


def test_sessionless_tools_call_with_bearer(client_and_token):
    tc, token = client_and_token
    resp = tc.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "anime_library", "arguments": {}}},
    )
    assert resp.status_code == 200
    body = _parse_sse(resp.text)
    assert body["id"] == 1
    assert "result" in body


def test_sessionless_call_without_token_rejected(client_and_token):
    tc, _ = client_and_token
    resp = tc.post(
        "/mcp",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "anime_library", "arguments": {}}},
    )
    assert resp.status_code in (401, 403)
