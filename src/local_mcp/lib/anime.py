"""Anime download management - core implementation.

Note: Uses fcntl for file locking, which is Unix-only (Linux/macOS).
"""

import fcntl  # Unix-only
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict

from local_mcp.lib import torrent
from local_mcp.settings import (
    ANIME_BASE_PATH as BASE_PATH,
    ANIME_HISTORY_FILE as HISTORY_FILE,
    ANIME_STALLED_DIR as STALLED_DIR,
    ANIME_TRUSTED_GROUPS as TRUSTED_GROUPS,
    ANIME_VIDEO_GLOB as VIDEO_GLOB,
    ANIME_WATCH_DIR as WATCH_DIR,
)

logger = logging.getLogger(__name__)

# Regex for local filenames (requires .mkv extension)
ANIME_NAME_REGEX = re.compile(
    r"\[(?P<group>.*?)\]\s*(?P<title>.*?)[\s-]*(?P<episode>\d*?)\s*(END)?\s*(\[v\d+\])?(\[|\()(?P<quality>.*?)(\]|\)).*?\.mkv"
)


# --- Types ---

# Unified status - used for both current episode state and history events
Status = Literal["unwatched", "watched", "stalled"]


class Episode(TypedDict, total=False):
    """Anime episode with parsed info and status."""

    group: str
    title: str
    episode: float
    quality: str
    path: str
    status: Status


class HistoryEntry(TypedDict, total=False):
    """History event entry (JSONL format).

    Records status transitions. The 'status' field indicates the status
    the episode transitioned to at timestamp 'ts'.
    """

    ts: str
    status: Status
    path: str
    series: str
    episode: float
    group: str
    quality: str


class SeriesEpisode(TypedDict):
    """Episode info within a series listing."""

    episode: float
    path: str
    status: Status


class Series(TypedDict):
    """A series with its episodes and metadata."""

    title: str
    group: str
    quality: str
    episodes: list[SeriesEpisode]
    latest_episode: float
    latest_watched: float


# --- Path management ---


def ensure_paths():
    """Create required directories if they don't exist."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.touch(exist_ok=True)
    STALLED_DIR.mkdir(parents=True, exist_ok=True)
    WATCH_DIR.mkdir(parents=True, exist_ok=True)


# --- Parsing ---


def parse_episode(filename: str, path: str = "") -> Episode | None:
    """Parse episode info from filename into an Episode."""
    match = ANIME_NAME_REGEX.match(filename)
    if not match:
        return None

    groups = match.groupdict()
    return Episode(
        group=groups["group"],
        title=groups["title"],
        episode=float(groups["episode"]) if groups["episode"] else -1,
        quality=groups["quality"],
        path=path,
    )


# --- History ---


def _build_history_entry(status: Status, path: Path) -> HistoryEntry:
    """Build a history entry with parsed metadata."""
    entry = HistoryEntry(
        ts=datetime.now(timezone.utc).isoformat(),
        status=status,
        path=str(path),
    )
    if parsed := parse_episode(path.name):
        entry["series"] = parsed["title"]
        entry["episode"] = parsed["episode"]
        entry["group"] = parsed["group"]
        entry["quality"] = parsed["quality"]
    return entry


def _load_history_file() -> list[HistoryEntry]:
    """Load history entries from JSONL file (internal use)."""
    if not HISTORY_FILE.exists():
        return []

    entries: list[HistoryEntry] = []
    for i, line in enumerate(HISTORY_FILE.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning(f"Malformed JSON at line {i} in history file")
    return entries


def _get_disk_files() -> set[Path]:
    """Get all video files on disk (main + stalled directories)."""
    main_files = set(BASE_PATH.glob(VIDEO_GLOB))
    stalled_files = set(STALLED_DIR.glob(VIDEO_GLOB))
    return main_files | stalled_files


HISTORY_LOCK_FILE = HISTORY_FILE.parent / ".anime_history.lock"


@contextmanager
def _history_lock():
    """Acquire exclusive lock on history file to prevent race conditions."""
    HISTORY_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_LOCK_FILE, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def sync_history(disk_files: set[Path]) -> list[HistoryEntry]:
    """
    Sync history with disk state.

    Combines JSONL entries with files on disk that aren't in history.
    Files on disk without history entries are added as "unwatched".
    Note: This function writes to the history file when new files are found.
    """
    ensure_paths()

    with _history_lock():
        entries = _load_history_file()

        # Get known paths from history
        known_paths = {Path(e["path"]).name for e in entries if "path" in e}

        # Check disk for files not in history, add them as unwatched
        for path in disk_files:
            if path.name not in known_paths:
                entry = _build_history_entry("unwatched", path)
                entries.append(entry)
                _write_history_entry_unlocked(entry)

    return entries


def _write_history_entry_unlocked(entry: HistoryEntry) -> None:
    """Append entry to history file (caller must hold lock)."""
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def write_history_entry(entry: HistoryEntry) -> None:
    """Append a single entry to history file (thread-safe)."""
    with _history_lock():
        _write_history_entry_unlocked(entry)


def _watched_filenames(history: list[HistoryEntry]) -> set[str]:
    """Get set of watched episode filenames from history."""
    return {Path(e["path"]).name for e in history if e.get("status") == "watched"}


def _watched_episodes_by_series(history: list[HistoryEntry]) -> dict[str, float]:
    """Get highest watched episode number for each series from history."""
    series_max: dict[str, float] = {}
    for entry in history:
        if (
            entry.get("status") == "watched"
            and "series" in entry
            and "episode" in entry
        ):
            series = entry["series"]
            episode = entry["episode"]
            if series not in series_max or episode > series_max[series]:
                series_max[series] = episode
    return series_max


# --- Library ---


def _episode_status(path: Path, watched_filenames: set[str]) -> Status:
    """Determine episode status: 'watched', 'stalled', or 'unwatched'."""
    if path.name in watched_filenames:
        return "watched"
    if STALLED_DIR in path.parents:
        return "stalled"
    return "unwatched"


def build_library() -> dict[str, Series]:
    """Build library state from disk entries, grouped by series."""
    disk_files = _get_disk_files()
    history = sync_history(disk_files)
    watched = _watched_filenames(history)
    history_watched = _watched_episodes_by_series(history)

    # Parse disk entries with status
    entries = []
    for path in sorted(disk_files):
        if ep := parse_episode(path.name, path=str(path)):
            ep["status"] = _episode_status(path, watched)
            entries.append(ep)

    series_map: dict[str, dict] = {}

    for ep in entries:
        title = ep["title"]
        if title not in series_map:
            series_map[title] = {
                "title": title,
                "group": ep["group"],
                "quality": ep["quality"],
                "episodes": [],
            }

        series_map[title]["episodes"].append(
            {
                "episode": ep["episode"],
                "path": ep["path"],
                "status": ep["status"],
            }
        )

    # Sort episodes and compute aggregates
    for series in series_map.values():
        series["episodes"].sort(key=lambda e: e["episode"])
        series["latest_episode"] = max(e["episode"] for e in series["episodes"])
        # Include history so deleted-but-watched episodes are counted
        series["latest_watched"] = max(
            max(
                (e["episode"] for e in series["episodes"] if e["status"] == "watched"),
                default=0,
            ),
            history_watched.get(series["title"], 0),
        )

    return series_map


async def check_trusted_releases(download: bool = False) -> dict:
    """Check trusted groups for new episodes of tracked series.

    Fetches recent releases from all trusted groups (3 pages each),
    merges with priority based on TRUSTED_GROUPS order, and optionally
    downloads new episodes for tracked series.

    This is more efficient than per-series searching - only a few requests
    regardless of library size.
    """
    library = build_library()
    tracked_series = {s["title"]: s for s in library.values()}

    # Fetch and merge releases from trusted groups (priority order)
    # series -> episode -> info (first group wins)
    all_releases: dict[str, dict[float, dict]] = {}

    for group in TRUSTED_GROUPS:
        print(f"Fetching releases from {group}...")
        try:
            releases = await torrent.fetch_group_releases(group)
            for ep in releases:
                series = ep["title"]
                episode = ep["episode"]

                # Only track if series is in library
                if series not in tracked_series:
                    continue

                # Priority: first group wins
                if series not in all_releases:
                    all_releases[series] = {}
                if episode not in all_releases[series]:
                    all_releases[series][episode] = ep
        except Exception as e:
            print(f"  Error fetching {group}: {e}")

    # Find new episodes
    available = []
    downloaded = []

    for series, episodes in sorted(all_releases.items()):
        lib_series = tracked_series[series]
        after_episode = max(lib_series["latest_episode"], lib_series["latest_watched"])

        for episode_num, ep in sorted(episodes.items()):
            if episode_num <= after_episode:
                continue

            entry = {
                "series": series,
                "episode": episode_num,
                "torrent": ep.get("torrent"),
                "group": ep["group"],
                "quality": ep["quality"],
            }
            available.append(entry)

            if download and ep.get("torrent"):
                dest = torrent.download(ep["torrent"])
                video_path = torrent.video_path(dest)
                if video_path:
                    write_history_entry(
                        HistoryEntry(
                            ts=datetime.now(timezone.utc).isoformat(),
                            status="unwatched",
                            path=video_path,
                            series=series,
                            episode=episode_num,
                            group=ep["group"],
                            quality=ep["quality"],
                        )
                    )
                downloaded.append({**entry, "downloaded_to": str(dest)})

    return {
        "available": available,
        "downloaded": downloaded if download else None,
        "checked_groups": len(TRUSTED_GROUPS),
        "matched_series": len(all_releases),
    }


def get_library(
    series: str | None = None,
    status: Status | None = None,
) -> dict:
    """Get local anime library state.

    Args:
        series: Filter to a single series by title
        status: Filter by status: "unwatched", "watched", "stalled"
    """
    library = build_library()

    if series:
        if series in library:
            return {"series": [library[series]]}
        return {"series": [], "error": f"Series '{series}' not found"}

    result = list(library.values())

    # Filter by status if requested
    if status == "unwatched":
        # Series with unwatched episodes
        result = [
            s for s in result if any(e["status"] == "unwatched" for e in s["episodes"])
        ]
    elif status == "watched":
        # Series where all episodes are watched
        result = [
            s for s in result if all(e["status"] == "watched" for e in s["episodes"])
        ]
    elif status == "stalled":
        # Series with stalled episodes
        result = [
            s for s in result if any(e["status"] == "stalled" for e in s["episodes"])
        ]

    return {"series": sorted(result, key=lambda s: s["title"])}


def mark_episode(path: str, status: Literal["watched", "stalled"]) -> dict:
    """Mark an episode as watched or stalled."""
    ensure_paths()
    episode_path = Path(path)

    if not episode_path.exists():
        # Try to resolve partial path in both directories
        for search_dir in [BASE_PATH, STALLED_DIR]:
            possible = search_dir / episode_path.name
            if possible.exists():
                episode_path = possible
                break
        else:
            return {"error": f"Episode not found: {path}"}

    if status == "watched":
        write_history_entry(_build_history_entry("watched", episode_path))
        return {"status": "watched", "path": str(episode_path)}

    if status == "stalled":
        # Already in stalled dir? Just record in history, don't move
        if STALLED_DIR in episode_path.parents or episode_path.parent == STALLED_DIR:
            write_history_entry(_build_history_entry("stalled", episode_path))
            return {"status": "stalled", "path": str(episode_path)}
        dest = STALLED_DIR / episode_path.name
        episode_path.rename(dest)
        write_history_entry(_build_history_entry("stalled", dest))
        return {"status": "stalled", "path": str(dest)}

    return {"error": f"Unknown status: {status}"}


async def check_and_download():
    """CLI entry point: check trusted groups and download new episodes."""
    result = await check_trusted_releases(download=True)
    print(
        f"Checked {result['checked_groups']} groups, {result['matched_series']} series matched"
    )
    if result["downloaded"]:
        for ep in result["downloaded"]:
            print(f"Downloaded: [{ep['group']}] {ep['series']} - {ep['episode']}")
    else:
        print("No new episodes found")
    return result


def main():
    """Sync CLI entry point for cron."""
    import asyncio

    asyncio.run(check_and_download())
