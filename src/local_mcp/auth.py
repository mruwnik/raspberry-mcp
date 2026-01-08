"""htpasswd-based OAuth authentication for local-mcp."""

import html
import secrets
import time
from pathlib import Path
from string import Template
from urllib.parse import urlencode

from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from passlib.apache import HtpasswdFile
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from local_mcp.settings import HTPASSWD_PATH, SERVER_BASE_URL
from local_mcp.token_db import (
    PendingAuth,
    PermissiveClient,
    StoredAuthCode,
    StoredToken,
    TokenDB,
)

LOGIN_TEMPLATE = Template(
    (Path(__file__).parent / "templates" / "login.html").read_text()
)


class HtpasswdAuth(OAuthProvider):
    """OAuth provider using htpasswd file for authentication.

    Set htpasswd file path via LOCAL_MCP_HTPASSWD env var (default: .htpasswd).
    Create with: htpasswd -c .htpasswd username
    """

    ACCESS_TOKEN_LIFETIME = 30 * 24 * 3600  # 30 days
    REFRESH_TOKEN_LIFETIME = 30 * 24 * 3600  # 30 days
    AUTH_CODE_LIFETIME = 600  # 10 minutes
    PENDING_AUTH_LIFETIME = 600  # 10 minutes

    def __init__(self, db: TokenDB):
        super().__init__(
            base_url=SERVER_BASE_URL,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self._db = db

    # --- Helpers ---

    def _generate_token(self, prefix: str = "") -> str:
        return f"{prefix}{secrets.token_urlsafe(32)}"

    def _verify_credentials(self, username: str, password: str) -> bool:
        if not HTPASSWD_PATH.exists():
            return False
        htpasswd = HtpasswdFile(str(HTPASSWD_PATH))
        return htpasswd.check_password(username, password) is True

    def _create_tokens(
        self, user: str, client_id: str, scopes: list[str]
    ) -> tuple[str, str]:
        """Create and store access + refresh tokens. Returns (access_token, refresh_token)."""
        now = time.time()
        access_token = self._generate_token("at_")
        refresh_token = self._generate_token("rt_")

        self._db.set_token(
            access_token,
            StoredToken(
                token=access_token,
                user=user,
                scopes=scopes,
                expires_at=now + self.ACCESS_TOKEN_LIFETIME,
                client_id=client_id,
            ),
        )
        self._db.set_refresh_token(
            refresh_token,
            StoredToken(
                token=refresh_token,
                user=user,
                scopes=scopes,
                expires_at=now + self.REFRESH_TOKEN_LIFETIME,
                client_id=client_id,
            ),
        )
        return access_token, refresh_token

    def _make_oauth_token(
        self, access_token: str, refresh_token: str, scopes: list[str]
    ) -> OAuthToken:
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.ACCESS_TOKEN_LIFETIME,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh_token,
        )

    # --- Login Routes ---

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        routes.append(
            Route("/login", endpoint=self._handle_login, methods=["GET", "POST"])
        )
        return routes

    async def _handle_login(self, request: Request):
        pending_id = request.query_params.get("pending", "")

        if request.method == "GET":
            return self._login_page(pending_id)

        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        pending_id = str(form.get("pending", pending_id))

        if not self._verify_credentials(username, password):
            return self._login_page(pending_id, "Invalid username or password")

        pending = self._db.pop_pending_auth(pending_id) if pending_id else None
        if not pending:
            return HTMLResponse("Session expired. Please try again.", status_code=400)

        code = self._generate_token("code_")
        self._db.set_auth_code(
            code,
            StoredAuthCode(
                code=code,
                client_id=pending.client_id,
                redirect_uri=pending.redirect_uri,
                scopes=pending.scopes,
                code_challenge=pending.code_challenge,
                expires_at=time.time() + self.AUTH_CODE_LIFETIME,
                user=username,
                redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
            ),
        )

        params = {"code": code}
        if pending.state:
            params["state"] = pending.state
        sep = "&" if "?" in pending.redirect_uri else "?"
        return RedirectResponse(
            f"{pending.redirect_uri}{sep}{urlencode(params)}", status_code=302
        )

    def _login_page(self, pending_id: str, error: str | None = None) -> HTMLResponse:
        error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
        pending_field = (
            f'<input type="hidden" name="pending" value="{html.escape(pending_id)}">'
            if pending_id
            else ""
        )
        return HTMLResponse(
            LOGIN_TEMPLATE.substitute(
                error_html=error_html, pending_field=pending_field
            )
        )

    # --- OAuth Provider Interface ---

    async def get_client(self, client_id: str) -> PermissiveClient | None:
        client = self._db.get_client(client_id)
        if not client:
            client = PermissiveClient(
                client_id=client_id,
                client_secret=None,
                redirect_uris=[AnyUrl("http://localhost/placeholder")],
                token_endpoint_auth_method="client_secret_basic",
            )
            self._db.set_client(client_id, client)
        return client

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> OAuthClientInformationFull:
        client_id = client_info.client_id or self._generate_token("client_")
        client_secret = client_info.client_secret or self._generate_token("secret_")
        auth_method = "client_secret_basic"

        client_info.client_id = client_id
        client_info.client_secret = client_secret
        client_info.token_endpoint_auth_method = auth_method

        self._db.set_client(
            client_id,
            PermissiveClient(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uris=client_info.redirect_uris
                or [AnyUrl("http://localhost/placeholder")],
                client_name=client_info.client_name,
                token_endpoint_auth_method=auth_method,
            ),
        )
        return client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        pending_id = self._generate_token("pending_")
        self._db.set_pending_auth(
            pending_id,
            PendingAuth(
                client_id=client.client_id or "",
                redirect_uri=str(params.redirect_uri),
                scopes=params.scopes or [],
                state=params.state,
                code_challenge=params.code_challenge or "",
                expires_at=time.time() + self.PENDING_AUTH_LIFETIME,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            ),
        )
        return f"{str(self.base_url).rstrip('/')}/login?pending={pending_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, code: str
    ) -> AuthorizationCode | None:
        self._db.cleanup_expired()
        stored = self._db.get_auth_code(code)
        if not stored:
            return None
        return AuthorizationCode(
            code=stored.code,
            client_id=stored.client_id,
            redirect_uri=AnyUrl(stored.redirect_uri),
            scopes=stored.scopes,
            code_challenge=stored.code_challenge or "",
            expires_at=stored.expires_at,
            redirect_uri_provided_explicitly=stored.redirect_uri_provided_explicitly,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, auth_code: AuthorizationCode
    ) -> OAuthToken:
        stored = self._db.pop_auth_code(auth_code.code)
        if not stored:
            raise ValueError("Invalid authorization code")
        access, refresh = self._create_tokens(
            stored.user, auth_code.client_id, auth_code.scopes
        )
        return self._make_oauth_token(access, refresh, auth_code.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        self._db.cleanup_expired()
        stored = self._db.get_token(token)
        if not stored:
            return None
        return AccessToken(
            token=stored.token,
            client_id=stored.client_id,
            scopes=stored.scopes,
            expires_at=int(stored.expires_at),
            claims={"user": stored.user, "sub": stored.user},
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, token: str
    ) -> RefreshToken | None:
        self._db.cleanup_expired()
        stored = self._db.get_refresh_token(token)
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
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        stored = self._db.pop_refresh_token(refresh_token.token)
        if not stored:
            raise ValueError("Invalid refresh token")
        token_scopes = scopes or refresh_token.scopes
        access, refresh = self._create_tokens(
            stored.user, refresh_token.client_id, token_scopes
        )
        return self._make_oauth_token(access, refresh, token_scopes)

    async def revoke_token(
        self, token: str, token_type_hint: str | None = None
    ) -> None:
        self._db.delete_token(token)
        self._db.delete_refresh_token(token)
