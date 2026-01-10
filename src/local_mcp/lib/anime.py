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
# "manual" means the episode should be watched manually, not in auto-queue
Status = Literal["unwatched", "watched", "stalled", "manual"]


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
    Files in stalled directory are added as "stalled", others as "unwatched".
    Note: This function writes to the history file when new files are found.
    """
    ensure_paths()

    with _history_lock():
        entries = _load_history_file()

        # Get known paths from history
        known_paths = {Path(e["path"]).name for e in entries if "path" in e}

        # Check disk for files not in history
        for path in disk_files:
            if path.name not in known_paths:
                # Files in stalled dir start as "stalled", others as "unwatched"
                initial_status: Status = (
                    "stalled" if STALLED_DIR in path.parents else "unwatched"
                )
                entry = _build_history_entry(initial_status, path)
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


def _latest_status_by_filename(history: list[HistoryEntry]) -> dict[str, Status]:
    """Get the latest status for each filename from history.

    Returns a dict mapping filename -> status, using the most recent entry
    for each file (latest entry wins).
    """
    result: dict[str, Status] = {}
    for entry in history:
        if "path" in entry and "status" in entry:
            filename = Path(entry["path"]).name
            # Later entries overwrite earlier ones (latest wins)
            result[filename] = entry["status"]
    return result


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


def _episode_status(path: Path, status_by_filename: dict[str, Status]) -> Status:
    """Determine episode status: 'watched', 'stalled', 'manual', or 'unwatched'.

    Priority: history status > stalled directory > unwatched default.
    Uses latest history entry for each file (latest wins).
    """
    # Check history first (latest entry wins)
    if path.name in status_by_filename:
        return status_by_filename[path.name]
    # Files in stalled directory without explicit history status
    if STALLED_DIR in path.parents:
        return "stalled"
    return "unwatched"


def build_library() -> dict[str, Series]:
    """Build library state from disk entries, grouped by series."""
    disk_files = _get_disk_files()
    history = sync_history(disk_files)
    status_by_filename = _latest_status_by_filename(history)
    history_watched = _watched_episodes_by_series(history)

    # Parse disk entries with status
    entries = []
    for path in sorted(disk_files):
        if ep := parse_episode(path.name, path=str(path)):
            ep["status"] = _episode_status(path, status_by_filename)
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


def _fuzzy_match(query: str, target: str) -> bool:
    """Basic fuzzy matching: case-insensitive substring or word matching.

    Matches if:
    - query is a substring of target (case-insensitive)
    - all words in query appear in target (case-insensitive)
    """
    query_lower = query.lower()
    target_lower = target.lower()

    # Direct substring match
    if query_lower in target_lower:
        return True

    # All query words appear in target
    query_words = query_lower.split()
    if all(word in target_lower for word in query_words):
        return True

    return False


def _parse_timestamp(ts_str: str | None) -> datetime | None:
    """Parse ISO timestamp string to datetime, returns None on failure."""
    if not ts_str:
        return None
    try:
        # Handle both with and without timezone
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _get_series_timestamps(history: list[HistoryEntry]) -> dict[str, datetime]:
    """Get the most recent timestamp for each series from history."""
    series_ts: dict[str, datetime] = {}
    for entry in history:
        series = entry.get("series")
        ts = _parse_timestamp(entry.get("ts"))
        if series and ts:
            if series not in series_ts or ts > series_ts[series]:
                series_ts[series] = ts
    return series_ts


def get_library(
    series: str | None = None,
    status: Status | None = None,
    search: str | None = None,
    group: str | None = None,
    since: str | None = None,
    before: str | None = None,
    min_episode: float | None = None,
    max_episode: float | None = None,
) -> dict:
    """Get local anime library state.

    Args:
        series: Filter to a single series by exact title
        status: Filter by status: "unwatched", "watched", "stalled", "manual"
        search: Fuzzy search series titles (case-insensitive substring/word match)
        group: Filter by release group (case-insensitive substring)
        since: Only series with activity after this ISO timestamp
        before: Only series with activity before this ISO timestamp
        min_episode: Only series with episodes >= this number
        max_episode: Only series with episodes <= this number
    """
    library = build_library()

    # Get history for timestamp filtering
    history = _load_history_file() if (since or before) else []
    series_timestamps = _get_series_timestamps(history) if history else {}

    # Parse timestamp filters
    since_dt = _parse_timestamp(since)
    before_dt = _parse_timestamp(before)

    if series:
        if series in library:
            return {"series": [library[series]]}
        return {"series": [], "error": f"Series '{series}' not found"}

    result = list(library.values())

    # Fuzzy search by title
    if search:
        result = [s for s in result if _fuzzy_match(search, s["title"])]

    # Filter by release group
    if group:
        group_lower = group.lower()
        result = [s for s in result if group_lower in s["group"].lower()]

    # Filter by timestamp - series with recent activity
    if since_dt:
        result = [
            s
            for s in result
            if s["title"] in series_timestamps and series_timestamps[s["title"]] >= since_dt
        ]
    if before_dt:
        result = [
            s
            for s in result
            if s["title"] in series_timestamps and series_timestamps[s["title"]] <= before_dt
        ]

    # Filter by episode range
    if min_episode is not None:
        result = [s for s in result if s["latest_episode"] >= min_episode]
    if max_episode is not None:
        result = [
            s for s in result if any(e["episode"] <= max_episode for e in s["episodes"])
        ]

    # Filter by status if requested
    if status == "watched":
        # Special case: series where ALL episodes are watched
        result = [
            s for s in result if all(e["status"] == "watched" for e in s["episodes"])
        ]
    elif status:
        # For unwatched/stalled/manual: series with at least one episode of that status
        result = [
            s for s in result if any(e["status"] == status for e in s["episodes"])
        ]

    return {"series": sorted(result, key=lambda s: s["title"])}


def mark_episode(path: str, status: Literal["watched", "stalled", "manual"]) -> dict:
    """Mark an episode as watched, stalled, or manual.

    - watched: Records in history, file stays in place
    - stalled: Moves file to stalled directory, records in history
    - manual: Records in history, file stays in place (excluded from auto-queue)
    """
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

    if status in ("watched", "manual"):
        # Just record in history, file stays in place
        write_history_entry(_build_history_entry(status, episode_path))
        return {"status": status, "path": str(episode_path)}

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
