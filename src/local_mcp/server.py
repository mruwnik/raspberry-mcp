"""Local MCP server entry point."""

import base64

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request

from local_mcp.auth import HtpasswdAuth
from local_mcp.settings import SERVER_PORT, TOKEN_DB_PATH
from local_mcp.token_db import TokenDB
from local_mcp.tools import anime_mcp, music_mcp


class InjectClientIdMiddleware(BaseHTTPMiddleware):
    """Extract client_id from Basic auth and inject into form body if missing."""

    async def dispatch(self, request: Request, call_next):
        if "/token" in request.url.path and request.method == "POST":
            auth = request.headers.get("authorization", "")
            body = (await request.body()).decode()
            if auth.startswith("Basic ") and "client_id=" not in body:
                client_id = base64.b64decode(auth[6:]).decode().split(":")[0]
                new_body = f"client_id={client_id}&{body}".encode()
                request._body = new_body
        return await call_next(request)


# Create the MCP server with auth always enabled
mcp = FastMCP(
    name="local-mcp",
    instructions="Local MCP server with music/MPD control and anime download tools.",
    auth=HtpasswdAuth(db=TokenDB(TOKEN_DB_PATH)),
)

# Mount tool subservers
mcp.mount(anime_mcp)
mcp.mount(music_mcp)

# Custom middleware for OAuth flow
custom_middleware = [
    Middleware(InjectClientIdMiddleware),
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    ),
]


def main() -> None:
    """Run the server with HTTP transport."""
    http_app = mcp.http_app(
        transport="streamable-http",
        path="/mcp",
        middleware=custom_middleware,
    )
    uvicorn.run(http_app, host="0.0.0.0", port=SERVER_PORT)


if __name__ == "__main__":
    main()
