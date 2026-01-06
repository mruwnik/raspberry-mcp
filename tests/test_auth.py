"""Tests for auth module."""

import time

import pytest

from local_mcp.auth import HtpasswdAuth, PermissiveClient


# PermissiveClient tests

@pytest.mark.parametrize("redirect_uri", [
    "http://localhost:8080/callback",
    "https://example.com/oauth/callback",
    "myapp://callback",
    "http://127.0.0.1:3000/auth",
])
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


def test_auth_creates_htpasswd_if_missing(auth_instance, temp_dir):
    htpasswd_path = temp_dir / ".htpasswd"
    assert htpasswd_path.exists()
    content = htpasswd_path.read_text()
    assert "admin:" in content


def test_auth_generates_random_password(temp_dir, monkeypatch):
    """Each new htpasswd file should have a different password."""
    import sys
    for mod in list(sys.modules.keys()):
        if mod.startswith("local_mcp"):
            del sys.modules[mod]

    htpasswd1 = temp_dir / "htpasswd1"
    monkeypatch.setenv("LOCAL_MCP_HTPASSWD", str(htpasswd1))
    monkeypatch.setenv("LOCAL_MCP_BASE_URL", "http://localhost:3000")

    from local_mcp.auth import HtpasswdAuth
    auth1 = HtpasswdAuth()
    content1 = htpasswd1.read_text()

    # Clear and create another
    for mod in list(sys.modules.keys()):
        if mod.startswith("local_mcp"):
            del sys.modules[mod]

    htpasswd2 = temp_dir / "htpasswd2"
    monkeypatch.setenv("LOCAL_MCP_HTPASSWD", str(htpasswd2))

    from local_mcp.auth import HtpasswdAuth as HtpasswdAuth2
    auth2 = HtpasswdAuth2()
    content2 = htpasswd2.read_text()

    # Password hashes should be different
    assert content1 != content2


@pytest.mark.parametrize("prefix", [
    "",
    "at_",
    "rt_",
    "code_",
])
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
    # Add some tokens with past expiration
    auth_instance._tokens["expired"] = type("Token", (), {
        "token": "expired",
        "user": "test",
        "scopes": [],
        "expires_at": time.time() - 3600,  # Expired 1 hour ago
        "client_id": "test",
    })()

    auth_instance._tokens["valid"] = type("Token", (), {
        "token": "valid",
        "user": "test",
        "scopes": [],
        "expires_at": time.time() + 3600,  # Valid for 1 more hour
        "client_id": "test",
    })()

    auth_instance._cleanup_expired()

    assert "expired" not in auth_instance._tokens
    assert "valid" in auth_instance._tokens


@pytest.mark.parametrize("token_type,lifetime", [
    ("access", HtpasswdAuth.ACCESS_TOKEN_LIFETIME),
    ("refresh", HtpasswdAuth.REFRESH_TOKEN_LIFETIME),
])
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
    client = await auth_instance.register_client({})
    assert client.client_id is not None
    assert client.client_id.startswith("client_")


@pytest.mark.asyncio
async def test_register_client_uses_provided_id(auth_instance):
    client = await auth_instance.register_client({"client_id": "my-custom-id"})
    assert client.client_id == "my-custom-id"


@pytest.mark.asyncio
async def test_revoke_token_removes_both_types(auth_instance):
    auth_instance._tokens["test-token"] = type("Token", (), {
        "token": "test-token",
        "expires_at": time.time() + 3600,
    })()
    auth_instance._refresh_tokens["test-token"] = type("Token", (), {
        "token": "test-token",
        "expires_at": time.time() + 3600,
    })()

    await auth_instance.revoke_token("test-token")

    assert "test-token" not in auth_instance._tokens
    assert "test-token" not in auth_instance._refresh_tokens


@pytest.mark.asyncio
async def test_load_access_token_returns_none_for_missing(auth_instance):
    result = await auth_instance.load_access_token("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_load_refresh_token_returns_none_for_missing(auth_instance):
    result = await auth_instance.load_refresh_token("nonexistent")
    assert result is None
