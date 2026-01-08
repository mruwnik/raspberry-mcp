"""Anime download management tools."""

from typing import Literal

from fastmcp import FastMCP

from local_mcp.lib import anime

mcp = FastMCP(name="anime")


@mcp.tool()
def anime_library(
    series: str | None = None,
    status: anime.Status | None = None,
) -> dict:
    """
    Get local anime library state.

    Returns all tracked series with their episodes, watch status, and progress.

    Args:
        series: Optional series title to filter to a single series
        status: Optional filter: "unwatched", "watched", or "stalled"

    Returns dict with:
        - series: list of series, each containing:
            - title, group, quality
            - episodes: list of {episode, path, status}
            - latest_episode: highest episode number on disk
            - latest_watched: highest watched episode number
    """
    return anime.get_library(series, status)


@mcp.tool()
def anime_mark(path: str, status: Literal["watched", "stalled"]) -> dict:
    """
    Mark an episode as watched or stalled.

    Args:
        path: Path to the episode file
        status: "watched" to add to history, "stalled" to move to stalled dir

    Returns confirmation of the action taken.
    """
    return anime.mark_episode(path, status)


@mcp.tool()
async def anime_check(download: bool = False) -> dict:
    """
    Check trusted groups for new episodes of tracked series.

    Fetches recent releases from SubsPlease and Erai-raws, matches against
    library, and optionally downloads new episodes.

    Args:
        download: If True, download any new episodes found

    Returns:
        - available: list of {series, episode, torrent_url}
        - downloaded: list of downloaded episodes (if download=True)
    """
    return await anime.check_trusted_releases(download)
