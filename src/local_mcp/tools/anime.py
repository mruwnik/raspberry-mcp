"""Anime download management tools."""

from typing import Literal

from fastmcp import FastMCP

from local_mcp.lib import anime

mcp = FastMCP(name="anime")


@mcp.tool()
async def anime_library(series: str | None = None) -> dict:
    """
    Get local anime library state.

    Returns all tracked series with their episodes, watch status, and progress.

    Args:
        series: Optional series title to filter to a single series

    Returns dict with:
        - series: list of series, each containing:
            - title, group, quality
            - episodes: list of {episode, path, watched, stalled}
            - latest_episode: highest episode number on disk
            - latest_watched: highest watched episode number
    """
    return await anime.get_library(series)


@mcp.tool()
async def anime_mark(path: str, status: Literal["watched", "stalled"]) -> dict:
    """
    Mark an episode as watched or stalled.

    Args:
        path: Path to the episode file
        status: "watched" to add to history, "stalled" to move to stalled dir

    Returns confirmation of the action taken.
    """
    return await anime.mark_episode(path, status)


@mcp.tool()
async def anime_check(series: str | None = None, download: bool = False) -> dict:
    """
    Check nyaa.si for new episodes of tracked series.

    Args:
        series: Optional series title to check (default: check all)
        download: If True, download any new episodes found

    Returns:
        - available: list of {series, episode, torrent_url}
        - downloaded: list of downloaded episodes (if download=True)
    """
    return await anime.check_episodes(series, download)
