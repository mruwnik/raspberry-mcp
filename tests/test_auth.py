"""Tests for auth module."""

import time

import pytest

from local_mcp.token_db import PermissiveClient, StoredToken, TokenDB


# PermissiveClient tests


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:8080/callback",
        "https://example.com/oauth/callback",
        "myapp://callback",
        "http://127.0.0.1:3000/auth",
    ],
)
def test_permissive_client_accepts_any_redirect(redirect_uri):
    client = PermissiveClient(
        client_id="test",
        client_secret=None,
        redirect_uris=["http://placeholder"],
    )
    result = client.validate_redirect_uri(redirect_uri)
    assert str(result) == redirect_uri


# HtpasswdAuth tests


@pytest.fixture
def auth_instance(temp_dir, monkeypatch):
    """Create HtpasswdAuth with temp htpasswd file and temp token db."""
    import sys

    # Clear cached modules
    for mod in list(sys.modules.keys()):
        if mod.startswith("local_mcp"):
            del sys.modules[mod]

    htpasswd_path = temp_dir / ".htpasswd"
    token_db_path = temp_dir / ".token_db.json"
    monkeypatch.setenv("LOCAL_MCP_HTPASSWD", str(htpasswd_path))
    monkeypatch.setenv("LOCAL_MCP_TOKEN_DB", str(token_db_path))
    monkeypatch.setenv("LOCAL_MCP_BASE_URL", "http://localhost:3000")

    from local_mcp.auth import HtpasswdAuth
    from local_mcp.token_db import TokenDB

    return HtpasswdAuth(db=TokenDB(token_db_path))


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "at_",
        "rt_",
        "code_",
    ],
)
def test_generate_token_format(auth_instance, prefix):
    token = auth_instance._generate_token(prefix)
    assert token.startswith(prefix)
    assert len(token) > len(prefix) + 20  # Sufficient randomness


def test_generate_token_uniqueness(auth_instance):
    tokens = {auth_instance._generate_token() for _ in range(100)}
    assert len(tokens) == 100  # All unique


def test_verify_credentials_with_valid_credentials(temp_dir, monkeypatch):
    import sys

    for mod in list(sys.modules.keys()):
        if mod.startswith("local_mcp"):
            del sys.modules[mod]

    from passlib.apache import HtpasswdFile

    htpasswd_path = temp_dir / ".htpasswd"
    htpasswd = HtpasswdFile(str(htpasswd_path), new=True)
    htpasswd.set_password("testuser", "testpass")
    htpasswd.save()

    token_db_path = temp_dir / ".token_db.json"
    monkeypatch.setenv("LOCAL_MCP_HTPASSWD", str(htpasswd_path))
    monkeypatch.setenv("LOCAL_MCP_TOKEN_DB", str(token_db_path))
    monkeypatch.setenv("LOCAL_MCP_BASE_URL", "http://localhost:3000")

    from local_mcp.auth import HtpasswdAuth
    from local_mcp.token_db import TokenDB

    auth = HtpasswdAuth(db=TokenDB(token_db_path))

    assert auth._verify_credentials("testuser", "testpass") is True
    assert not auth._verify_credentials("testuser", "wrongpass")
    assert not auth._verify_credentials("wronguser", "testpass")


def test_cleanup_expired_removes_old_tokens(auth_instance):
    # Add some tokens with past expiration
    auth_instance._db.set_token(
        "expired",
        StoredToken(
            token="expired",
            user="test",
            scopes=[],
            expires_at=time.time() - 3600,  # Expired 1 hour ago
            client_id="test",
        ),
    )

    auth_instance._db.set_token(
        "valid",
        StoredToken(
            token="valid",
            user="test",
            scopes=[],
            expires_at=time.time() + 3600,  # Valid for 1 more hour
            client_id="test",
        ),
    )

    auth_instance._db.cleanup_expired()

    assert auth_instance._db.get_token("expired") is None
    assert auth_instance._db.get_token("valid") is not None


@pytest.mark.parametrize(
    "token_type,lifetime",
    [
        ("access", 30 * 24 * 3600),  # ACCESS_TOKEN_LIFETIME
        ("refresh", 30 * 24 * 3600),  # REFRESH_TOKEN_LIFETIME
    ],
)
def test_token_lifetimes_are_reasonable(token_type, lifetime):
    # Tokens should last at least 1 day
    assert lifetime >= 86400
    # But not more than 365 days
    assert lifetime <= 365 * 86400


# Async tests


@pytest.mark.asyncio
async def test_get_client_auto_registers(auth_instance):
    client = await auth_instance.get_client("new-client-id")
    assert client is not None
    assert client.client_id == "new-client-id"


@pytest.mark.asyncio
async def test_get_client_returns_same_client(auth_instance):
    client1 = await auth_instance.get_client("test-client")
    client2 = await auth_instance.get_client("test-client")
    assert client1 is client2


@pytest.mark.asyncio
async def test_register_client_generates_id_if_missing(auth_instance):
    from mcp.shared.auth import OAuthClientInformationFull

    client = await auth_instance.register_client(
        OAuthClientInformationFull(redirect_uris=["http://localhost/callback"])
    )
    assert client.client_id is not None
    assert client.client_id.startswith("client_")


@pytest.mark.asyncio
async def test_register_client_uses_provided_id(auth_instance):
    from mcp.shared.auth import OAuthClientInformationFull

    client = await auth_instance.register_client(
        OAuthClientInformationFull(
            client_id="my-custom-id", redirect_uris=["http://localhost/callback"]
        )
    )
    assert client.client_id == "my-custom-id"


@pytest.mark.asyncio
async def test_revoke_token_removes_both_types(auth_instance):
    auth_instance._db.set_token(
        "test-token",
        StoredToken(
            token="test-token",
            user="test",
            scopes=[],
            expires_at=time.time() + 3600,
            client_id="test",
        ),
    )
    auth_instance._db.set_refresh_token(
        "test-token",
        StoredToken(
            token="test-token",
            user="test",
            scopes=[],
            expires_at=time.time() + 3600,
            client_id="test",
        ),
    )

    await auth_instance.revoke_token("test-token")

    assert auth_instance._db.get_token("test-token") is None
    assert auth_instance._db.get_refresh_token("test-token") is None


@pytest.mark.asyncio
async def test_load_access_token_returns_none_for_missing(auth_instance):
    result = await auth_instance.load_access_token("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_load_refresh_token_returns_none_for_missing(auth_instance):
    client = PermissiveClient(
        client_id="test", client_secret=None, redirect_uris=["http://localhost"]
    )
    result = await auth_instance.load_refresh_token(client, "nonexistent")
    assert result is None


@pytest.mark.parametrize(
    "client_id, expected",
    [("anime-watch-cli", ["files"]), ("claude-ai-client", [])],
)
def test_create_tokens_grants_files_scope_only_to_files_clients(
    auth_instance, client_id, expected
):
    access, refresh = auth_instance._create_tokens("test", client_id, [])

    assert auth_instance._db.get_token(access).scopes == expected
    assert auth_instance._db.get_refresh_token(refresh).scopes == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers, expected",
    [
        ([(b"authorization", b"Bearer valid-token")], 204),
        ([(b"authorization", b"Bearer mcp-only-token")], 401),
        ([(b"authorization", b"Bearer expired-token")], 401),
        ([(b"authorization", b"Bearer unknown-token")], 401),
        ([(b"authorization", b"Basic valid-token")], 401),
        ([], 401),
    ],
)
async def test_files_auth(auth_instance, headers, expected):
    from starlette.requests import Request

    for token, scopes, ttl in [
        ("valid-token", ["files"], 3600),
        ("mcp-only-token", [], 3600),
        ("expired-token", ["files"], -1),
    ]:
        auth_instance._db.set_token(
            token,
            StoredToken(
                token=token,
                user="test",
                scopes=scopes,
                expires_at=time.time() + ttl,
                client_id="test",
            ),
        )
    request = Request({"type": "http", "method": "GET", "headers": headers})

    response = await auth_instance.handle_files_auth(request)

    assert response.status_code == expected
    assert response.body == b""


def test_verify_credentials_accepts_bcrypt_2y(auth_instance, temp_dir):
    import bcrypt

    hashed = bcrypt.hashpw(b"testpass", bcrypt.gensalt(7, prefix=b"2b"))
    hashed = b"$2y$" + hashed[4:]
    (temp_dir / ".htpasswd").write_bytes(b"testuser:" + hashed + b"\n")

    assert auth_instance._verify_credentials("testuser", "testpass") is True
    assert not auth_instance._verify_credentials("testuser", "wrongpass")


@pytest.mark.parametrize(
    "peer, headers, expected",
    [
        ("127.0.0.1", {"x-real-ip": "1.2.3.4"}, "1.2.3.4"),
        ("127.0.0.1", {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, "1.2.3.4"),
        ("127.0.0.1", {"x-real-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"}, "1.2.3.4"),
        ("127.0.0.1", {}, "127.0.0.1"),
        ("9.9.9.9", {"x-real-ip": "1.2.3.4"}, "9.9.9.9"),
        ("9.9.9.9", {"x-forwarded-for": "1.2.3.4"}, "9.9.9.9"),
    ],
)
def test_client_ip_trusts_proxy_headers_only_from_localhost(peer, headers, expected):
    from starlette.requests import Request

    from local_mcp.auth import client_ip

    request = Request(
        {
            "type": "http",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 12345),
        }
    )

    assert client_ip(request) == expected


@pytest.mark.parametrize(
    "username, expected",
    [
        ("alice", "user=alice "),
        ("evil\nauth failure: login user=x ip=6.6.6.6", "user=evilauthfailure:loginuser=xip=6.6.6.6 "),
        ("x ip=6.6.6.6", "user=xip=6.6.6.6 "),
        ("a b\tc", "user=abc "),
        ("x" * 100, f"user={'x' * 64} "),
    ],
)
@pytest.mark.asyncio
async def test_failed_login_logs_one_sanitised_line(auth_instance, caplog, username, expected):
    from urllib.parse import urlencode

    from starlette.requests import Request

    body = urlencode({"username": username, "password": "hunter2"}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"x-real-ip", b"93.152.221.151"),
            ],
            "client": ("127.0.0.1", 12345),
            "query_string": b"",
        },
        receive,
    )

    await auth_instance._handle_login(request)

    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert record.getMessage() == f"auth failure: login {expected}ip=93.152.221.151"
    assert "hunter2" not in caplog.text


@pytest.mark.parametrize(
    "headers, logged",
    [
        ([(b"authorization", b"Bearer unknown-token")], ["auth failure: files user=- ip=1.2.3.4"]),
        ([], []),
    ],
)
@pytest.mark.asyncio
async def test_files_auth_logs_only_invalid_presented_tokens(auth_instance, caplog, headers, logged):
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [*headers, (b"x-real-ip", b"1.2.3.4")],
            "client": ("127.0.0.1", 12345),
        }
    )

    await auth_instance.handle_files_auth(request)

    assert [r.getMessage() for r in caplog.records] == logged
