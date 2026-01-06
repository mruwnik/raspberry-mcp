"""Music/MPD control - core implementation using direct MPD protocol."""

import asyncio
import random
import re
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from local_mcp.settings import (
    CACHE_TIMEOUT,
    MPD_HOST,
    MPD_PORT,
    MPD_SKIP_PATTERNS,
)


class MPDError(Exception):
    """MPD protocol error."""
    pass


@asynccontextmanager
async def mpd_connection() -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    """Context manager for MPD connection."""
    reader, writer = await asyncio.open_connection(MPD_HOST, MPD_PORT)
    try:
        # Read greeting (OK MPD x.x.x)
        greeting = await reader.readline()
        if not greeting.startswith(b"OK MPD"):
            raise MPDError(f"Unexpected greeting: {greeting.decode()}")
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def mpd_command(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cmd: str) -> list[str]:
    """Send command and read response lines until OK or ACK."""
    writer.write(f"{cmd}\n".encode())
    await writer.drain()

    lines = []
    while True:
        line = await reader.readline()
        if not line:
            raise MPDError("Connection closed")
        line = line.decode().rstrip("\n")
        if line == "OK":
            break
        if line.startswith("ACK"):
            raise MPDError(line)
        lines.append(line)
    return lines


def parse_response(lines: list[str]) -> dict:
    """Parse key: value lines into dict."""
    result = {}
    for line in lines:
        if ": " in line:
            key, value = line.split(": ", 1)
            result[key] = value
    return result


def parse_list_response(lines: list[str]) -> list[dict]:
    """Parse multi-item response (like lsinfo) into list of dicts."""
    items = []
    current: dict = {}
    for line in lines:
        if ": " in line:
            key, value = line.split(": ", 1)
            # New item starts with file/directory/playlist
            if key in ("file", "directory", "playlist") and current:
                items.append(current)
                current = {}
            current[key] = value
    if current:
        items.append(current)
    return items


async def player_command(commands: list[list[str]]) -> dict:
    """Execute MPD player commands."""
    async with mpd_connection() as (reader, writer):
        result = {}
        for cmd_parts in commands:
            cmd = cmd_parts[0]
            args = cmd_parts[1:] if len(cmd_parts) > 1 else []

            # Build command string
            if args:
                # Quote args with spaces
                quoted_args = [f'"{a}"' if " " in a else a for a in args]
                cmd_str = f"{cmd} {' '.join(quoted_args)}"
            else:
                cmd_str = cmd

            lines = await mpd_command(reader, writer, cmd_str)
            if lines:
                result.update(parse_response(lines))

        # Always return current status
        status_lines = await mpd_command(reader, writer, "status")
        result.update(parse_response(status_lines))

        # Get current song info if playing
        if result.get("state") in ("play", "pause"):
            song_lines = await mpd_command(reader, writer, "currentsong")
            result["current_song"] = parse_response(song_lines)

        return result


async def browse_directory(paths: list[str]) -> dict:
    """Browse MPD music directory using lsinfo."""
    async with mpd_connection() as (reader, writer):
        result = {}
        for path in paths or [""]:
            cmd = f'lsinfo "{path}"' if path else "lsinfo"
            lines = await mpd_command(reader, writer, cmd)
            items = parse_list_response(lines)

            files = []
            directories = []
            for item in items:
                if "file" in item:
                    files.append({
                        "file": item["file"],
                        "title": item.get("Title", item["file"].split("/")[-1]),
                        "duration": item.get("Time", ""),
                    })
                elif "directory" in item:
                    directories.append({
                        "folder": item["directory"],
                        "title": item["directory"].split("/")[-1],
                    })

            result[path] = {"files": files, "directories": directories}
        return result


async def play_tracks(
    tracks: list[str], clear_first: bool = True, start_playing: bool = True
) -> dict:
    """Add tracks to playlist and optionally start playback."""
    commands = []
    if clear_first:
        commands.append(["clear"])
    for track in tracks:
        commands.append(["add", track])
    if start_playing:
        commands.append(["play"])
    return await player_command(commands)


# Cache for recursive file listing
_cache: dict[tuple, tuple[list[dict], float]] = {}


def _should_skip(path: str, patterns: list[str]) -> bool:
    """Check if path matches any skip pattern."""
    return any(re.search(p, path) for p in patterns)


async def _get_all_files_recursive(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    path: str,
    skip_patterns: list[str],
) -> list[dict]:
    """Recursively get all files under a path."""
    if _should_skip(path, skip_patterns):
        return []

    cmd = f'lsinfo "{path}"' if path else "lsinfo"
    lines = await mpd_command(reader, writer, cmd)
    items = parse_list_response(lines)

    files = []
    for item in items:
        if "file" in item:
            if not _should_skip(item["file"], skip_patterns):
                files.append({
                    "file": item["file"],
                    "title": item.get("Title", item["file"].split("/")[-1]),
                    "duration": item.get("Time", ""),
                })
        elif "directory" in item:
            subfiles = await _get_all_files_recursive(reader, writer, item["directory"], skip_patterns)
            files.extend(subfiles)

    return files


async def get_all_files(path: str = "", skip: list[str] | None = None) -> list[dict]:
    """Get all files under a path (cached)."""
    # Combine caller's patterns with defaults
    patterns = (skip or []) + MPD_SKIP_PATTERNS

    key = (path, tuple(patterns))
    if key in _cache:
        cached, ts = _cache[key]
        if time.time() - ts < CACHE_TIMEOUT:
            return cached

    async with mpd_connection() as (reader, writer):
        files = await _get_all_files_recursive(reader, writer, path, patterns)

    _cache[key] = (files, time.time())
    return files


async def play_random_tracks(
    path: str = "",
    count: int = 10,
    clear_first: bool = True,
    start_playing: bool = True,
    skip: list[str] | None = None,
) -> dict:
    """Play random tracks from a directory."""
    all_files = await get_all_files(path, skip)
    if not all_files:
        return {"error": "No files found", "path": path}

    selected = random.sample(all_files, min(count, len(all_files)))
    tracks = sorted([f["file"] for f in selected])
    return await play_tracks(tracks, clear_first, start_playing)


async def get_status() -> dict:
    """Get current MPD player status."""
    return await player_command([["status"]])
