"""Tests for TokenDB JSON persistence."""

import time

import pytest

from local_mcp.token_db import (
    PendingAuth,
    PermissiveClient,
    StoredAuthCode,
    StoredToken,
    TokenDB,
)


@pytest.fixture
def db(temp_dir):
    """Create a TokenDB with a temp file."""
    return TokenDB(temp_dir / ".token_db.json")


def test_db_creates_file_on_write(temp_dir):
    db_path = temp_dir / ".token_db.json"
    assert not db_path.exists()

    db = TokenDB(db_path)
    db.set_token("tok", StoredToken("tok", "user", [], time.time() + 3600, "client"))

    assert db_path.exists()


def test_db_persists_tokens_across_instances(temp_dir):
    db_path = temp_dir / ".token_db.json"

    db1 = TokenDB(db_path)
    db1.set_token("tok", StoredToken("tok", "user", ["read"], time.time() + 3600, "c1"))

    db2 = TokenDB(db_path)
    token = db2.get_token("tok")

    assert token is not None
    assert token.user == "user"
    assert token.scopes == ["read"]
    assert token.client_id == "c1"


def test_db_persists_clients_across_instances(temp_dir):
    db_path = temp_dir / ".token_db.json"

    db1 = TokenDB(db_path)
    db1.set_client(
        "client1",
        PermissiveClient(
            client_id="client1",
            client_secret="secret123",
            redirect_uris=["http://localhost/cb"],
            token_endpoint_auth_method="client_secret_basic",
        ),
    )

    db2 = TokenDB(db_path)
    client = db2.get_client("client1")

    assert client is not None
    assert client.client_secret == "secret123"
    assert client.token_endpoint_auth_method == "client_secret_basic"


def test_db_persists_auth_codes(temp_dir):
    db_path = temp_dir / ".token_db.json"

    db1 = TokenDB(db_path)
    db1.set_auth_code(
        "code1",
        StoredAuthCode(
            code="code1",
            client_id="c1",
            redirect_uri="http://localhost/cb",
            scopes=["read"],
            code_challenge="challenge",
            expires_at=time.time() + 600,
            user="testuser",
            redirect_uri_provided_explicitly=True,
        ),
    )

    db2 = TokenDB(db_path)
    code = db2.get_auth_code("code1")

    assert code is not None
    assert code.user == "testuser"
    assert code.code_challenge == "challenge"


def test_db_persists_pending_auths(temp_dir):
    db_path = temp_dir / ".token_db.json"

    db1 = TokenDB(db_path)
    db1.set_pending_auth(
        "pending1",
        PendingAuth(
            client_id="c1",
            redirect_uri="http://localhost/cb",
            scopes=["write"],
            state="state123",
            code_challenge="challenge",
            expires_at=time.time() + 600,
            redirect_uri_provided_explicitly=False,
        ),
    )

    db2 = TokenDB(db_path)
    pending = db2.get_pending_auth("pending1")

    assert pending is not None
    assert pending.state == "state123"
    assert pending.redirect_uri_provided_explicitly is False


def test_pop_removes_and_returns(db):
    db.set_auth_code(
        "code1",
        StoredAuthCode(
            code="code1",
            client_id="c1",
            redirect_uri="http://localhost",
            scopes=[],
            code_challenge=None,
            expires_at=time.time() + 600,
            user="user",
        ),
    )

    code = db.pop_auth_code("code1")
    assert code is not None
    assert code.code == "code1"
    assert db.get_auth_code("code1") is None


def test_cleanup_removes_expired(db):
    now = time.time()
    db.set_token("expired", StoredToken("expired", "u", [], now - 100, "c"))
    db.set_token("valid", StoredToken("valid", "u", [], now + 3600, "c"))
    db.set_refresh_token(
        "rt_expired", StoredToken("rt_expired", "u", [], now - 100, "c")
    )
    db.set_auth_code(
        "code_expired",
        StoredAuthCode("code_expired", "c", "http://x", [], None, now - 100, "u"),
    )
    db.set_pending_auth(
        "pending_expired",
        PendingAuth("c", "http://x", [], None, None, now - 100),
    )

    db.cleanup_expired()

    assert db.get_token("expired") is None
    assert db.get_token("valid") is not None
    assert db.get_refresh_token("rt_expired") is None
    assert db.get_auth_code("code_expired") is None
    assert db.get_pending_auth("pending_expired") is None


def test_db_handles_missing_file(temp_dir):
    db_path = temp_dir / "nonexistent.json"
    db = TokenDB(db_path)
    assert db.get_token("anything") is None


def test_db_handles_corrupted_file(temp_dir):
    db_path = temp_dir / ".token_db.json"
    db_path.write_text("not valid json {{{")

    db = TokenDB(db_path)
    assert db.get_token("anything") is None  # Starts fresh
