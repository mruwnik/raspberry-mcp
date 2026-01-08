"""JSON-backed token database for OAuth state persistence."""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl


class PermissiveClient(OAuthClientInformationFull):
    """Client that accepts any redirect URI (for simple local auth)."""

    def validate_redirect_uri(self, redirect_uri: str) -> AnyUrl:
        """Accept any redirect URI."""
        return AnyUrl(redirect_uri)


@dataclass
class StoredToken:
    """Stored token information."""

    token: str
    user: str
    scopes: list[str]
    expires_at: float
    client_id: str


@dataclass
class StoredAuthCode:
    """Authorization code for OAuth flow."""

    code: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    code_challenge: str | None
    expires_at: float
    user: str
    redirect_uri_provided_explicitly: bool = True


@dataclass
class PendingAuth:
    """Pending authorization request waiting for login."""

    client_id: str
    redirect_uri: str
    scopes: list[str]
    state: str | None
    code_challenge: str | None
    expires_at: float
    redirect_uri_provided_explicitly: bool = True


class TokenDB:
    """JSON-backed token database."""

    def __init__(self, db_path: Path):
        self._path = db_path
        self._tokens: dict[str, StoredToken] = {}
        self._auth_codes: dict[str, StoredAuthCode] = {}
        self._refresh_tokens: dict[str, StoredToken] = {}
        self._clients: dict[str, PermissiveClient] = {}
        self._pending_auths: dict[str, PendingAuth] = {}
        self._load()

    def _load(self) -> None:
        """Load state from JSON file."""
        if not self._path.exists():
            return

        try:
            data = json.loads(self._path.read_text())
            self._tokens = {
                k: StoredToken(**v) for k, v in data.get("tokens", {}).items()
            }
            self._auth_codes = {
                k: StoredAuthCode(**v) for k, v in data.get("auth_codes", {}).items()
            }
            self._refresh_tokens = {
                k: StoredToken(**v) for k, v in data.get("refresh_tokens", {}).items()
            }
            self._pending_auths = {
                k: PendingAuth(**v) for k, v in data.get("pending_auths", {}).items()
            }
            for client_id, client_data in data.get("clients", {}).items():
                self._clients[client_id] = PermissiveClient.model_validate(client_data)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass  # Start fresh on corruption

    def _save(self) -> None:
        """Persist state to JSON file."""
        data: dict[str, Any] = {
            "tokens": {k: asdict(v) for k, v in self._tokens.items()},
            "auth_codes": {k: asdict(v) for k, v in self._auth_codes.items()},
            "refresh_tokens": {k: asdict(v) for k, v in self._refresh_tokens.items()},
            "pending_auths": {k: asdict(v) for k, v in self._pending_auths.items()},
            "clients": {k: v.model_dump(mode="json") for k, v in self._clients.items()},
        }
        self._path.write_text(json.dumps(data, indent=2))

    def cleanup_expired(self) -> None:
        """Remove expired tokens and codes."""
        now = time.time()
        self._tokens = {k: v for k, v in self._tokens.items() if v.expires_at > now}
        self._auth_codes = {
            k: v for k, v in self._auth_codes.items() if v.expires_at > now
        }
        self._refresh_tokens = {
            k: v for k, v in self._refresh_tokens.items() if v.expires_at > now
        }
        self._pending_auths = {
            k: v for k, v in self._pending_auths.items() if v.expires_at > now
        }
        self._save()

    # Access tokens
    def get_token(self, token: str) -> StoredToken | None:
        return self._tokens.get(token)

    def set_token(self, token: str, data: StoredToken) -> None:
        self._tokens[token] = data
        self._save()

    def delete_token(self, token: str) -> None:
        self._tokens.pop(token, None)
        self._save()

    # Auth codes
    def get_auth_code(self, code: str) -> StoredAuthCode | None:
        return self._auth_codes.get(code)

    def set_auth_code(self, code: str, data: StoredAuthCode) -> None:
        self._auth_codes[code] = data
        self._save()

    def pop_auth_code(self, code: str) -> StoredAuthCode | None:
        result = self._auth_codes.pop(code, None)
        self._save()
        return result

    # Refresh tokens
    def get_refresh_token(self, token: str) -> StoredToken | None:
        return self._refresh_tokens.get(token)

    def set_refresh_token(self, token: str, data: StoredToken) -> None:
        self._refresh_tokens[token] = data
        self._save()

    def pop_refresh_token(self, token: str) -> StoredToken | None:
        result = self._refresh_tokens.pop(token, None)
        self._save()
        return result

    def delete_refresh_token(self, token: str) -> None:
        self._refresh_tokens.pop(token, None)
        self._save()

    # Clients
    def get_client(self, client_id: str) -> PermissiveClient | None:
        return self._clients.get(client_id)

    def set_client(self, client_id: str, client: PermissiveClient) -> None:
        self._clients[client_id] = client
        self._save()

    # Pending auths
    def get_pending_auth(self, pending_id: str) -> PendingAuth | None:
        return self._pending_auths.get(pending_id)

    def set_pending_auth(self, pending_id: str, data: PendingAuth) -> None:
        self._pending_auths[pending_id] = data
        self._save()

    def pop_pending_auth(self, pending_id: str) -> PendingAuth | None:
        result = self._pending_auths.pop(pending_id, None)
        self._save()
        return result
