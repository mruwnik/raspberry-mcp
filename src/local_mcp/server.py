"""Local MCP server entry point."""

import sys
from pathlib import Path

from fastmcp import FastMCP

from local_mcp.auth import create_htpasswd_auth
from local_mcp.settings import SERVER_PORT
from local_mcp.tools import anime_mcp, music_mcp


# Create the MCP server with auth always enabled
mcp = FastMCP(
    name="local-mcp",
    instructions="Local MCP server with music/MPD control and anime download tools.",
    auth=create_htpasswd_auth(),
)

# Mount tool subservers
mcp.mount(anime_mcp)
mcp.mount(music_mcp)


def main() -> None:
    """Run the server with HTTP transport."""

    uvicorn_config = {}
    if "--reload" in sys.argv:
        uvicorn_config["reload"] = True
        uvicorn_config["reload_dirs"] = [str(Path(__file__).parent.parent)]

    mcp.run(transport="streamable-http", port=SERVER_PORT, uvicorn_config=uvicorn_config)


if __name__ == "__main__":
    main()
