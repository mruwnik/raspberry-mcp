#!/usr/bin/env python3
"""Watch unwatched anime episodes, marking as watched when >80% complete.

Controls:
  q       - quit entirely
  ENTER   - skip to next episode
  (end)   - natural end, continues to next

Episodes watched >80% are marked as watched via MCP.
"""

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import dotenv
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

dotenv.load_dotenv()


# Config
MCP_URL = os.environ.get("ANIME_MCP_URL", "https://ahiru.pl/mcp")
CONFIG_DIR = Path.home() / ".config" / "anime-watch"
TOKEN_FILE = CONFIG_DIR / "auth.json"
SOCKET_PATH = "/tmp/mpv-anime-socket"

# Auth credentials (required)
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")

# OAuth settings
CLIENT_ID = "anime-watch-cli"
REDIRECT_URI = "http://localhost:18372/callback"  # Dummy, not actually used

# mpv input config (with --no-input-default-bindings, we define all needed keys)
# Exit codes: 1=quit all, 0=next (ENTER or natural end)
MPV_INPUT_CONF = """\
q quit 1
ENTER quit 0
SPACE cycle pause
LEFT seek -5
RIGHT seek 5
UP seek 60
DOWN seek -60
PGUP seek 300
PGDWN seek -300
m cycle mute
f cycle fullscreen
ESC set fullscreen no
. frame-step
, frame-back-step
[ multiply speed 0.9
] multiply speed 1.1
BS set speed 1.0
"""


@dataclass
class StoredAuth:
    """Locally stored OAuth tokens."""

    access_token: str
    refresh_token: str
    expires_at: float
    server_url: str


def get_server_base_url() -> str:
    """Extract base URL from MCP URL."""
    parsed = urlparse(MCP_URL)
    return f"{parsed.scheme}://{parsed.netloc}"


def load_auth() -> StoredAuth | None:
    """Load stored auth tokens if valid."""
    if not TOKEN_FILE.exists():
        return None

    try:
        data = json.loads(TOKEN_FILE.read_text())
        auth = StoredAuth(**data)
        # Check if token is for current server
        if auth.server_url != get_server_base_url():
            return None
        return auth
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def save_auth(auth: StoredAuth) -> None:
    """Save auth tokens to config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(asdict(auth), indent=2))
    TOKEN_FILE.chmod(0o600)


def clear_auth() -> None:
    """Remove stored auth."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(32)
    challenge = hashlib.sha256(verifier.encode()).digest()
    challenge_b64 = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
    return verifier, challenge_b64


def _exchange_code_for_tokens(
    base_url: str, code: str, code_verifier: str
) -> StoredAuth:
    """Exchange authorization code for tokens."""
    token_url = f"{base_url}/token"
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
    }

    # Server uses client_secret_basic auth - send client_id with empty secret
    auth = (CLIENT_ID, "")

    with httpx.Client() as client:
        response = client.post(token_url, data=token_data, auth=auth)
        response.raise_for_status()
        tokens = response.json()

    auth = StoredAuth(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=time.time() + tokens.get("expires_in", 3600),
        server_url=base_url,
    )
    save_auth(auth)
    return auth


def do_credential_auth(username: str, password: str) -> StoredAuth:
    """Perform OAuth flow with credentials."""
    base_url = get_server_base_url()
    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    # Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{base_url}/authorize?{urlencode(auth_params)}"

    with httpx.Client(follow_redirects=False) as client:
        # Step 1: Hit authorize endpoint to get pending ID
        response = client.get(auth_url)
        if response.status_code != 302:
            raise RuntimeError(
                f"Expected redirect from /authorize, got {response.status_code}"
            )

        login_url = response.headers.get("location", "")
        if not login_url:
            raise RuntimeError("No redirect location from /authorize")

        # Extract pending ID from login URL
        parsed = urlparse(login_url)
        pending_params = parse_qs(parsed.query)
        pending_id = pending_params.get("pending", [None])[0]
        if not pending_id:
            raise RuntimeError("No pending ID in login redirect")

        # Step 2: POST credentials to login
        login_post_url = f"{base_url}/login"
        response = client.post(
            login_post_url,
            data={"username": username, "password": password, "pending": pending_id},
        )
        if response.status_code != 302:
            raise RuntimeError("Login failed - invalid credentials")

        # Step 3: Extract code from redirect
        callback_url = response.headers.get("location", "")
        parsed = urlparse(callback_url)
        callback_params = parse_qs(parsed.query)

        if "error" in callback_params:
            raise RuntimeError(f"OAuth error: {callback_params['error'][0]}")

        code = callback_params.get("code", [None])[0]
        returned_state = callback_params.get("state", [None])[0]

        if not code:
            raise RuntimeError("No authorization code in callback")
        if returned_state != state:
            raise RuntimeError("State mismatch")

    # Step 4: Exchange code for tokens
    auth = _exchange_code_for_tokens(base_url, code, code_verifier)
    print("Login successful!")
    return auth


def refresh_token(auth: StoredAuth) -> StoredAuth | None:
    """Attempt to refresh the access token."""
    base_url = get_server_base_url()
    token_url = f"{base_url}/token"

    try:
        with httpx.Client() as client:
            response = client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": auth.refresh_token,
                    "client_id": CLIENT_ID,
                },
                auth=(CLIENT_ID, ""),  # client_secret_basic with empty secret
            )
            response.raise_for_status()
            tokens = response.json()

        new_auth = StoredAuth(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=time.time() + tokens.get("expires_in", 3600),
            server_url=base_url,
        )
        save_auth(new_auth)
        return new_auth
    except Exception:
        return None


def get_valid_auth() -> StoredAuth:
    """Get valid auth, refreshing or re-authenticating as needed."""
    auth = load_auth()

    if auth:
        # Check if expired (with 5 min buffer)
        if auth.expires_at > time.time() + 300:
            return auth

        # Try refresh
        refreshed = refresh_token(auth)
        if refreshed:
            return refreshed

        print("Session expired, need to re-login")
        clear_auth()

    # Credentials required
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        raise RuntimeError(
            "AUTH_USERNAME and AUTH_PASSWORD environment variables required"
        )
    return do_credential_auth(AUTH_USERNAME, AUTH_PASSWORD)


# --- MCP Client ---


async def call_mcp_tool(session: ClientSession, name: str, args: dict) -> dict:
    """Call an MCP tool and return the result."""
    result = await session.call_tool(name, args)
    for content in result.content:
        if content.type == "text":
            return json.loads(content.text)
    return {}


async def get_unwatched_episodes(session: ClientSession) -> list[dict]:
    """Get all unwatched episodes sorted by series then episode."""
    library = await call_mcp_tool(session, "anime_library", {"status": "unwatched"})
    episodes = []
    for series in library.get("series", []):
        for ep in series.get("episodes", []):
            if ep.get("status") == "unwatched":
                episodes.append(
                    {
                        "series": series["title"],
                        "episode": ep["episode"],
                        "path": ep["path"],
                    }
                )
    return sorted(episodes, key=lambda e: (e["series"], e["episode"]))


# --- mpv Playback ---


_request_id = 0


def mpv_command(sock: socket.socket, *args) -> dict:
    """Send command to mpv via IPC socket."""
    global _request_id
    _request_id += 1
    cmd = {"command": list(args), "request_id": _request_id}
    sock.sendall(json.dumps(cmd).encode() + b"\n")

    # Read responses until we get one matching our request_id
    buffer = b""
    for _ in range(10):  # Max 10 messages to find our response
        while b"\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                return {}
            buffer += chunk

        line, buffer = buffer.split(b"\n", 1)
        try:
            response = json.loads(line.decode())
            if response.get("request_id") == _request_id:
                return response
            # Otherwise it's an event or different response, keep reading
        except json.JSONDecodeError:
            pass

    return {}


# Minimum duration for a valid anime episode (10 minutes)
MIN_EPISODE_DURATION = 600


def get_playback_progress(sock: socket.socket) -> float:
    """Get current playback progress as fraction (0.0 to 1.0)."""
    try:
        duration = mpv_command(sock, "get_property", "duration")
        position = mpv_command(sock, "get_property", "playback-time")
        dur = duration.get("data", 0)
        pos = position.get("data", 0)
        # Validate: duration must be realistic for anime (>10 min), position valid
        if dur and dur > MIN_EPISODE_DURATION and pos and pos >= 0:
            progress = pos / dur
            # Sanity check: must be between 0 and 1
            if 0 <= progress <= 1:
                return progress
    except Exception:
        pass
    return 0.0


def get_sftp_host() -> str:
    """Extract host from MCP URL for SFTP."""
    parsed = urlparse(MCP_URL)
    return parsed.hostname or "raspberry"


def play_episode(path: str, input_conf_path: str) -> tuple[bool, float]:
    """
    Play episode with mpv over SFTP.

    Returns: (should_continue, progress)
    - should_continue: False if user pressed q (quit all)
    - progress: fraction of episode watched (0.0 to 1.0)
    """
    global _request_id
    _request_id = 0  # Reset for clean state each episode

    sftp_host = get_sftp_host()
    sftp_url = f"sftp://{sftp_host}{path}"

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    proc = subprocess.Popen(
        [
            "/opt/homebrew/bin/mpv",
            f"--input-ipc-server={SOCKET_PATH}",
            "--no-input-default-bindings",
            f"--input-conf={input_conf_path}",
            "--force-window=yes",
            sftp_url,
        ]
    )

    # Wait for socket
    for _ in range(50):
        if os.path.exists(SOCKET_PATH):
            break
        time.sleep(0.1)
    else:
        proc.wait()
        return True, 0.0

    progress = 0.0
    sock = None

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        sock.settimeout(0.5)  # 500ms timeout for IPC commands

        while proc.poll() is None:
            new_progress = get_playback_progress(sock)
            if new_progress > progress:
                progress = new_progress
            time.sleep(0.1)  # Poll frequently to catch quick exits

        # Final progress check before socket closes
        try:
            final = get_playback_progress(sock)
            if final > progress:
                progress = final
        except Exception:
            pass

    except Exception as e:
        print(f"  IPC error: {e}", file=sys.stderr)
    finally:
        if sock:
            sock.close()

    exit_code = proc.wait()
    # 1=quit all, 0=next episode
    should_continue = exit_code != 1

    return should_continue, progress


# --- Main ---


async def run_session():
    """Main session loop - connect to MCP and run watch flow."""
    auth = get_valid_auth()

    # Create authenticated client
    headers = {"Authorization": f"Bearer {auth.access_token}"}

    async with streamablehttp_client(MCP_URL, headers=headers) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            episodes = await get_unwatched_episodes(session)

            if not episodes:
                print("No unwatched episodes found")
                return

            print(f"Found {len(episodes)} unwatched episodes")
            print("Controls: q=quit, ENTER=next, video end=next")
            print()

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".conf", delete=False
            ) as f:
                f.write(MPV_INPUT_CONF)
                input_conf_path = f.name

            try:
                for i, ep in enumerate(episodes):
                    print(
                        f"[{i + 1}/{len(episodes)}] {ep['series']} - Episode {ep['episode']}"
                    )

                    should_continue, progress = play_episode(
                        ep["path"], input_conf_path
                    )

                    if progress >= 0.8:
                        print(f"  Watched {progress:.0%}, marking as watched")
                        await call_mcp_tool(
                            session,
                            "anime_mark",
                            {"path": ep["path"], "status": "watched"},
                        )
                    else:
                        print(f"  Watched {progress:.0%}, not marking")

                    if not should_continue:
                        print("\nQuitting...")
                        break

                    print()

            finally:
                os.unlink(input_conf_path)

            print("Done!")


def main():
    """Entry point for anime-watch command."""
    import asyncio

    try:
        asyncio.run(run_session())
    except KeyboardInterrupt:
        print("\nInterrupted")
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
