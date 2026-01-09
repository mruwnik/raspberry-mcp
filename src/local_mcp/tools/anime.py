"""Anime download management tools."""

from datetime import datetime, timezone
from typing import Literal

from fastmcp import FastMCP

from local_mcp.lib import anime, torrent

mcp = FastMCP(name="anime")


@mcp.tool()
def anime_library(
    series: str | None = None,
    status: anime.Status | None = None,
    search: str | None = None,
    group: str | None = None,
    since: str | None = None,
    before: str | None = None,
    min_episode: float | None = None,
    max_episode: float | None = None,
) -> dict:
    """
    Get local anime library state.

    Returns all tracked series with their episodes, watch status, and progress.

    Args:
        series: Exact series title to filter to a single series
        status: Filter: "unwatched", "watched", or "stalled"
        search: Fuzzy search series titles (case-insensitive, matches substrings or all words)
        group: Filter by release group (case-insensitive substring match)
        since: Only series with activity after this ISO timestamp (e.g., "2024-01-15T00:00:00Z")
        before: Only series with activity before this ISO timestamp
        min_episode: Only series with at least this episode number
        max_episode: Only series with episodes up to this number

    Returns dict with:
        - series: list of series, each containing:
            - title, group, quality
            - episodes: list of {episode, path, status}
            - latest_episode: highest episode number on disk
            - latest_watched: highest watched episode number
    """
    return anime.get_library(
        series=series,
        status=status,
        search=search,
        group=group,
        since=since,
        before=before,
        min_episode=min_episode,
        max_episode=max_episode,
    )


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
def anime_add(
    torrent_src: str,
    series: str,
    episode: float,
    group: str | None = None,
    quality: str | None = None,
) -> dict:
    """
    Add a torrent file to the watch directory for download.

    Args:
        torrent_src: Path to a local .torrent file or URL to download
        series: Series name (should match library naming for tracking)
        episode: Episode number
        group: Release group (optional)
        quality: Quality string like "1080p" (optional)

    Returns:
        - status: "added" on success
        - torrent_path: where the torrent was saved
        - video_path: expected path of the downloaded video
        - series, episode, group, quality: the metadata provided
    """
    fallback = f"{series.replace(' ', '_')}_{int(episode)}.torrent"
    try:
        dest = torrent.download(torrent_src, fallback_name=fallback)
    except FileNotFoundError as e:
        return {"error": str(e)}

    video_path = torrent.video_path(dest)
    if not video_path:
        return {"error": f"Could not extract video filename from torrent: {dest}"}

    grp = group or "unknown"
    qual = quality or "unknown"

    anime.write_history_entry(
        anime.HistoryEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            status="unwatched",
            path=video_path,
            series=series,
            episode=episode,
            group=grp,
            quality=qual,
        )
    )

    return {
        "status": "added",
        "torrent_path": str(dest),
        "video_path": video_path,
        "series": series,
        "episode": episode,
        "group": group,
        "quality": quality,
    }
