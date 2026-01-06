"""htpasswd-based OAuth authentication for local-mcp."""

import html
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull
from passlib.apache import HtpasswdFile
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from local_mcp.settings import HTPASSWD_PATH, SERVER_BASE_URL


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
    user: str  # Authenticated username


@dataclass
class PendingAuth:
    """Pending authorization request waiting for login."""

    client_id: str
    redirect_uri: str
    scopes: list[str]
    state: str | None
    code_challenge: str | None
    expires_at: float


class HtpasswdAuth(OAuthProvider):
    """OAuth provider using htpasswd file for authentication.

    Set htpasswd file path via LOCAL_MCP_HTPASSWD env var (default: .htpasswd).
    Create with: htpasswd -c .htpasswd username
    """

    # Token lifetimes
    ACCESS_TOKEN_LIFETIME = 30 * 24 * 3600  # 30 days
    REFRESH_TOKEN_LIFETIME = 30 * 24 * 3600  # 30 days
    AUTH_CODE_LIFETIME = 600  # 10 minutes
    PENDING_AUTH_LIFETIME = 600  # 10 minutes

    def __init__(self):
        super().__init__(base_url=SERVER_BASE_URL)
        self._htpasswd_path = HTPASSWD_PATH
        self._htpasswd: HtpasswdFile | None = None
        self._load_htpasswd()

        # In-memory storage
        self._tokens: dict[str, StoredToken] = {}
        self._auth_codes: dict[str, StoredAuthCode] = {}
        self._refresh_tokens: dict[str, StoredToken] = {}
        self._clients: dict[str, PermissiveClient] = {}
        self._pending_auths: dict[str, PendingAuth] = {}

    def _load_htpasswd(self) -> None:
        """Load or reload the htpasswd file, creating default if missing."""
        if not self._htpasswd_path.exists():
            # Generate random password and print to stdout once
            password = secrets.token_urlsafe(16)
            self._htpasswd = HtpasswdFile(str(self._htpasswd_path), new=True)
            self._htpasswd.set_password("admin", password)
            self._htpasswd.save()
            print(f"\n{'='*60}")
            print("Created new htpasswd file with default credentials:")
            print(f"  Username: admin")
            print(f"  Password: {password}")
            print(f"  File: {self._htpasswd_path}")
            print(f"{'='*60}\n")
        else:
            self._htpasswd = HtpasswdFile(str(self._htpasswd_path))

    def _generate_token(self, prefix: str = "") -> str:
        """Generate a secure random token."""
        return f"{prefix}{secrets.token_urlsafe(32)}"

    def _verify_user(self, username: str, password: str) -> bool:
        """Verify user credentials against htpasswd file."""
        # Reload htpasswd file to pick up changes
        self._load_htpasswd()

        if not self._htpasswd:
            return False

        return self._htpasswd.check_password(username, password)

    def _cleanup_expired(self) -> None:
        """Remove expired tokens and codes."""
        now = time.time()
        self._tokens = {k: v for k, v in self._tokens.items() if v.expires_at > now}
        self._auth_codes = {k: v for k, v in self._auth_codes.items() if v.expires_at > now}
        self._refresh_tokens = {k: v for k, v in self._refresh_tokens.items() if v.expires_at > now}
        self._pending_auths = {k: v for k, v in self._pending_auths.items() if v.expires_at > now}

    # Custom routes for login

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Get routes including login page."""
        routes = super().get_routes(mcp_path)
        routes.append(Route("/login", endpoint=self._handle_login, methods=["GET", "POST"]))
        return routes

    async def _handle_login(self, request: Request):
        """Handle login page GET/POST."""
        pending_id = request.query_params.get("pending")
        error = None

        if request.method == "POST":
            form = await request.form()
            username = form.get("username", "")
            password = form.get("password", "")
            pending_id = form.get("pending", pending_id)

            if self._verify_user(username, password):
                # Get pending auth
                pending = self._pending_auths.pop(pending_id, None) if pending_id else None
                if not pending:
                    return HTMLResponse("Session expired. Please try again.", status_code=400)

                # Generate auth code
                code = self._generate_token("code_")
                self._auth_codes[code] = StoredAuthCode(
                    code=code,
                    client_id=pending.client_id,
                    redirect_uri=pending.redirect_uri,
                    scopes=pending.scopes,
                    code_challenge=pending.code_challenge,
                    expires_at=time.time() + self.AUTH_CODE_LIFETIME,
                    user=username,
                )

                # Redirect back with code
                redirect_params = {"code": code}
                if pending.state:
                    redirect_params["state"] = pending.state
                separator = "&" if "?" in pending.redirect_uri else "?"
                return RedirectResponse(
                    f"{pending.redirect_uri}{separator}{urlencode(redirect_params)}",
                    status_code=302,
                )
            else:
                error = "Invalid username or password"

        # Show login form
        return self._login_page(pending_id, error)

    def _login_page(self, pending_id: str | None, error: str | None = None) -> HTMLResponse:
        """Generate login page HTML."""
        error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
        pending_field = f'<input type="hidden" name="pending" value="{html.escape(pending_id or "")}">' if pending_id else ""

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Login - Local MCP</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            background: white;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 400px;
        }}
        h1 {{
            margin: 0 0 8px 0;
            color: #1a1a2e;
            font-size: 28px;
            text-align: center;
        }}
        .subtitle {{
            color: #666;
            text-align: center;
            margin-bottom: 30px;
            font-size: 14px;
        }}
        .error {{
            background: #fee;
            color: #c00;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        label {{
            display: block;
            margin-bottom: 6px;
            color: #333;
            font-weight: 500;
            font-size: 14px;
        }}
        input[type="text"], input[type="password"] {{
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            font-size: 16px;
            margin-bottom: 20px;
            transition: border-color 0.2s;
        }}
        input[type="text"]:focus, input[type="password"]:focus {{
            outline: none;
            border-color: #4a90d9;
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: #4a90d9;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }}
        button:hover {{
            background: #357abd;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Local MCP</h1>
        <div class="subtitle">Sign in to continue</div>
        {error_html}
        <form method="POST">
            {pending_field}
            <label for="username">Username</label>
            <input type="text" id="username" name="username" required autofocus>
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>"""
        return HTMLResponse(content=html_content)

    # OAuthAuthorizationServerProvider interface

    async def get_client(self, client_id: str) -> PermissiveClient | None:
        """Get OAuth client info. Auto-register unknown clients."""
        if client_id not in self._clients:
            self._clients[client_id] = PermissiveClient(
                client_id=client_id,
                client_secret=None,
                redirect_uris=["http://localhost/placeholder"],
            )
        return self._clients.get(client_id)

    async def register_client(self, client_info: dict) -> PermissiveClient:
        """Register a new OAuth client."""
        client_id = client_info.get("client_id") or self._generate_token("client_")
        client_secret = self._generate_token("secret_")

        client = PermissiveClient(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=client_info.get("redirect_uris") or ["http://localhost/placeholder"],
            client_name=client_info.get("client_name"),
        )
        self._clients[client_id] = client
        return client

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Return URL to redirect user to for authentication."""
        # Store pending auth and redirect to login
        pending_id = self._generate_token("pending_")
        self._pending_auths[pending_id] = PendingAuth(
            client_id=client.client_id or "",
            redirect_uri=str(params.redirect_uri),
            scopes=params.scopes or [],
            state=params.state,
            code_challenge=params.code_challenge,
            expires_at=time.time() + self.PENDING_AUTH_LIFETIME,
        )

        # Return URL to login page
        base = str(self.base_url).rstrip("/")
        return f"{base}/login?pending={pending_id}"

    async def load_authorization_code(self, code: str) -> AuthorizationCode | None:
        """Load an authorization code."""
        self._cleanup_expired()
        stored = self._auth_codes.get(code)
        if not stored:
            return None

        return AuthorizationCode(
            code=stored.code,
            client_id=stored.client_id,
            redirect_uri=stored.redirect_uri,
            scopes=stored.scopes,
            code_challenge=stored.code_challenge,
            expires_at=stored.expires_at,
        )

    async def exchange_authorization_code(
        self, authorization_code: AuthorizationCode
    ) -> tuple[AccessToken, RefreshToken]:
        """Exchange authorization code for tokens."""
        stored = self._auth_codes.pop(authorization_code.code, None)
        if not stored:
            raise ValueError("Invalid authorization code")

        user = stored.user
        now = time.time()
        access_token_str = self._generate_token("at_")
        refresh_token_str = self._generate_token("rt_")

        self._tokens[access_token_str] = StoredToken(
            token=access_token_str,
            user=user,
            scopes=authorization_code.scopes,
            expires_at=now + self.ACCESS_TOKEN_LIFETIME,
            client_id=authorization_code.client_id,
        )

        self._refresh_tokens[refresh_token_str] = StoredToken(
            token=refresh_token_str,
            user=user,
            scopes=authorization_code.scopes,
            expires_at=now + self.REFRESH_TOKEN_LIFETIME,
            client_id=authorization_code.client_id,
        )

        access_token = AccessToken(
            token=access_token_str,
            client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(now + self.ACCESS_TOKEN_LIFETIME),
            claims={"user": user, "sub": user},
        )

        refresh_token = RefreshToken(
            token=refresh_token_str,
            client_id=authorization_code.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(now + self.REFRESH_TOKEN_LIFETIME),
        )

        return access_token, refresh_token

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load and verify an access token."""
        self._cleanup_expired()
        stored = self._tokens.get(token)
        if not stored:
            return None

        return AccessToken(
            token=stored.token,
            client_id=stored.client_id,
            scopes=stored.scopes,
            expires_at=int(stored.expires_at),
            claims={"user": stored.user, "sub": stored.user},
        )

    async def load_refresh_token(self, token: str) -> RefreshToken | None:
        """Load a refresh token."""
        self._cleanup_expired()
        stored = self._refresh_tokens.get(token)
        if not stored:
            return None

        return RefreshToken(
            token=stored.token,
            client_id=stored.client_id,
            scopes=stored.scopes,
            expires_at=int(stored.expires_at),
        )

    async def exchange_refresh_token(
        self,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> tuple[AccessToken, RefreshToken]:
        """Exchange refresh token for new tokens."""
        stored = self._refresh_tokens.pop(refresh_token.token, None)
        if not stored:
            raise ValueError("Invalid refresh token")

        user = stored.user
        now = time.time()
        access_token_str = self._generate_token("at_")
        new_refresh_token_str = self._generate_token("rt_")

        token_scopes = scopes if scopes else refresh_token.scopes

        self._tokens[access_token_str] = StoredToken(
            token=access_token_str,
            user=user,
            scopes=token_scopes,
            expires_at=now + self.ACCESS_TOKEN_LIFETIME,
            client_id=refresh_token.client_id,
        )

        self._refresh_tokens[new_refresh_token_str] = StoredToken(
            token=new_refresh_token_str,
            user=user,
            scopes=token_scopes,
            expires_at=now + self.REFRESH_TOKEN_LIFETIME,
            client_id=refresh_token.client_id,
        )

        access_token = AccessToken(
            token=access_token_str,
            client_id=refresh_token.client_id,
            scopes=token_scopes,
            expires_at=int(now + self.ACCESS_TOKEN_LIFETIME),
            claims={"user": user, "sub": user},
        )

        new_refresh_token = RefreshToken(
            token=new_refresh_token_str,
            client_id=refresh_token.client_id,
            scopes=token_scopes,
            expires_at=int(now + self.REFRESH_TOKEN_LIFETIME),
        )

        return access_token, new_refresh_token

    async def revoke_token(self, token: str, token_type_hint: str | None = None) -> None:
        """Revoke a token."""
        self._tokens.pop(token, None)
        self._refresh_tokens.pop(token, None)


def create_htpasswd_auth() -> HtpasswdAuth:
    """Create htpasswd auth provider."""
    return HtpasswdAuth()
