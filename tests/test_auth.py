"""Tests for auth module."""

import time

import pytest

from local_mcp.auth import HtpasswdAuth, PermissiveClient


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
    """Create HtpasswdAuth with temp htpasswd file."""
    import sys

    # Clear cached modules
    for mod in list(sys.modules.keys()):
        if mod.startswith("local_mcp"):
            del sys.modules[mod]

    htpasswd_path = temp_dir / ".htpasswd"
    monkeypatch.setenv("LOCAL_MCP_HTPASSWD", str(htpasswd_path))
    monkeypatch.setenv("LOCAL_MCP_BASE_URL", "http://localhost:3000")

    from local_mcp.auth import HtpasswdAuth

    return HtpasswdAuth()


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


def test_verify_user_with_valid_credentials(temp_dir, monkeypatch):
    import sys

    for mod in list(sys.modules.keys()):
        if mod.startswith("local_mcp"):
            del sys.modules[mod]

    from passlib.apache import HtpasswdFile

    htpasswd_path = temp_dir / ".htpasswd"
    htpasswd = HtpasswdFile(str(htpasswd_path), new=True)
    htpasswd.set_password("testuser", "testpass")
    htpasswd.save()

    monkeypatch.setenv("LOCAL_MCP_HTPASSWD", str(htpasswd_path))
    monkeypatch.setenv("LOCAL_MCP_BASE_URL", "http://localhost:3000")

    from local_mcp.auth import HtpasswdAuth

    auth = HtpasswdAuth()

    assert auth._verify_user("testuser", "testpass") is True
    assert not auth._verify_user("testuser", "wrongpass")
    assert not auth._verify_user("wronguser", "testpass")


def test_cleanup_expired_removes_old_tokens(auth_instance):
    from local_mcp.auth import StoredToken

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
        ("access", HtpasswdAuth.ACCESS_TOKEN_LIFETIME),
        ("refresh", HtpasswdAuth.REFRESH_TOKEN_LIFETIME),
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
    from local_mcp.auth import StoredToken

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
    result = await auth_instance.load_refresh_token("nonexistent")
    assert result is None
