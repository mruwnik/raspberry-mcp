"""Music/MPD control tools."""

from fastmcp import FastMCP

from local_mcp.lib import music

mcp = FastMCP(name="music")


@mcp.tool()
async def mpd_player_command(commands: list[list[str]]) -> dict:
    """
    Execute MPD player commands. Commands should be a list of command arrays.

    Examples:
    - Play: [["play"]]
    - Pause: [["pause"]]
    - Stop: [["stop"]]
    - Next track: [["next"]]
    - Previous track: [["previous"]]
    - Clear playlist: [["clear"]]
    - Add track: [["add", "Artist/Album/track.mp3"]]
    - Set volume: [["volume", "75"]]
    - Set random: [["random", "1"]] (0=off, 1=on)
    - Set repeat: [["repeat", "1"]] (0=off, 1=on)
    - Multiple: [["clear"], ["add", "path.mp3"], ["play"]]

    Returns player status including current track info.
    """
    return await music.player_command(commands)


@mcp.tool()
async def mpd_browse_directory(paths: list[str] = []) -> dict:
    """
    Browse MPD music directory. Returns files and subdirectories.

    Args:
        paths: List of directory paths to browse (empty list for root)

    Returns a dict of {<path>: <Directory>}, where `Directory` is a dict with `files` and `directories` keys.
    """
    return await music.browse_directory(paths)


@mcp.tool()
async def mpd_play_tracks(
    tracks: list[str], clear_first: bool = True, start_playing: bool = True
) -> dict:
    """
    Add tracks to playlist and optionally start playback.

    Args:
        tracks: List of track paths (e.g., ["Artist/Album/01.mp3", "Artist/Album/02.mp3"])
        clear_first: Whether to clear playlist first (default: True)
        start_playing: Whether to start playing after adding (default: True)

    Returns player status.
    """
    return await music.play_tracks(tracks, clear_first, start_playing)


@mcp.tool()
async def mpd_play_random_tracks(
    path: str = "",
    count: int = 10,
    clear_first: bool = True,
    start_playing: bool = True,
    skip: list[str] | None = None,
) -> dict:
    """
    Play random tracks from a directory.

    This works recursively, by first getting all files from all subdirectories, then choosing `count` random files from the list.

    Args:
        path: Path to the directory to play tracks from
        count: Number of tracks to play
        clear_first: Whether to clear playlist first (default: True)
        start_playing: Whether to start playing after adding (default: True)
        skip: List of regex patterns to skip files matching any pattern

    Returns player status.
    """
    return await music.play_random_tracks(path, count, clear_first, start_playing, skip)


@mcp.tool()
async def mpd_get_status() -> dict:
    """
    Get current MPD player status.

    Returns info about current track, playback state, volume, etc.
    """
    return await music.get_status()
