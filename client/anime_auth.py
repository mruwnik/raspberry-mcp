#!/usr/bin/env python3
"""One-shot OAuth login for the anime watch flow. Stdlib only.

Opens the browser to the server's login page, catches the redirect on
localhost, exchanges the code (PKCE S256), and writes the token file
shared with watch.py (~/.config/anime-watch/auth.json).

Notes on compatibility with src/local_mcp/watch.py (which shares the file):
- The token file must contain EXACTLY the StoredAuth fields
  {access_token, refresh_token, expires_at, server_url} - watch.py loads it
  via StoredAuth(**data), so any extra key breaks it.
- We reuse watch.py's fixed CLIENT_ID instead of dynamic registration:
  the server (src/local_mcp/auth.py) auto-creates a permissive client for
  unknown ids, while /register would generate a client_secret we could not
  store anywhere without breaking the shared token file.
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "anime-watch"
TOKEN_FILE = CONFIG_DIR / "auth.json"
CALLBACK_PORT = 8976
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
CLIENT_ID = "anime-watch-cli"  # Must match watch.py so both clients share auth
LOGIN_TIMEOUT = 300  # seconds to wait for the browser round-trip

_result_holder: dict = {}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path).path
        if parsed_path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _result_holder["code"] = (params.get("code") or [None])[0]
        _result_holder["state"] = (params.get("state") or [None])[0]
        _result_holder["error"] = (params.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Logged in - you can close this tab.</h2>")

    def log_message(self, *args):
        pass


def default_base_url() -> str:
    """Base URL from ANIME_MCP_URL (same env var watch.py uses), else ahiru.pl."""
    mcp_url = os.environ.get("ANIME_MCP_URL")
    if not mcp_url:
        return "https://ahiru.pl"
    parsed = urllib.parse.urlparse(mcp_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_base_url(url: str) -> str:
    """Reduce to scheme://netloc - watch.py compares server_url exactly."""
    parsed = urllib.parse.urlparse(url.rstrip("/"))
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid base URL: {url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(32)
    challenge = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()


def exchange_code(base: str, code: str, code_verifier: str) -> dict:
    """Exchange the authorization code for tokens at {base}/token."""
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
    }
    # client_secret_basic with empty secret, exactly like watch.py
    basic = base64.b64encode(f"{CLIENT_ID}:".encode()).decode()
    req = urllib.request.Request(
        f"{base}/token",
        data=urllib.parse.urlencode(fields).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def save_auth(tokens: dict, base: str) -> None:
    """Write the token file with EXACTLY watch.py's StoredAuth field names."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(
        json.dumps(
            {
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "expires_at": time.time() + tokens.get("expires_in", 3600),
                "server_url": base,
            },
            indent=2,
        )
    )
    TOKEN_FILE.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url",
        default=default_base_url(),
        help="OAuth server base URL (default: from ANIME_MCP_URL, else https://ahiru.pl)",
    )
    args = parser.parse_args()
    base = normalize_base_url(args.base_url)

    server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    auth_url = f"{base}/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    print(f"Opening browser: {auth_url}")
    webbrowser.open(auth_url)

    deadline = time.time() + LOGIN_TIMEOUT
    while "code" not in _result_holder and time.time() < deadline:
        time.sleep(0.2)
    server.shutdown()

    if _result_holder.get("error"):
        print(f"OAuth error: {_result_holder['error']}", file=sys.stderr)
        return 1
    if not _result_holder.get("code"):
        print("Timed out waiting for browser login", file=sys.stderr)
        return 1
    if _result_holder.get("state") != state:
        print("State mismatch - aborting", file=sys.stderr)
        return 1

    tokens = exchange_code(base, _result_holder["code"], code_verifier)
    save_auth(tokens, base)
    print(f"Wrote {TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
