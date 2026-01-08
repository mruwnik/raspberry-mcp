"""Anime download management - core implementation."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from urllib.request import urlretrieve

import httpx
from bs4 import BeautifulSoup

from local_mcp.settings import (
    ANIME_BASE_PATH as BASE_PATH,
    ANIME_HISTORY_FILE as HISTORY_FILE,
    ANIME_STALLED_DIR as STALLED_DIR,
    ANIME_TORRENTS_URL as TORRENTS_BASE_URL,
    ANIME_TRUSTED_GROUPS as TRUSTED_GROUPS,
    ANIME_VIDEO_GLOB as VIDEO_GLOB,
    ANIME_WATCH_DIR as WATCH_DIR,
)

ANIME_NAME_REGEX = re.compile(
    r"\[(?P<group>.*?)\]\s*(?P<title>.*?)[\s-]*(?P<episode>\d*?)\s*(END)?\s*(\[v\d+\])?(\[|\()(?P<quality>.*?)(\]|\)).*?\.mkv"
)


# --- Types ---


class Episode(TypedDict):
    """Anime episode with parsed info and status."""

    group: str
    title: str
    episode: float
    quality: str
    path: str
    stalled: bool
    watched: bool


class HistoryEntry(TypedDict, total=False):
    """History event entry (JSONL format)."""

    ts: str
    action: str  # "watched" or "stalled"
    path: str
    series: str
    episode: float
    group: str
    quality: str


# --- Path management ---


def ensure_paths():
    """Create required directories if they don't exist."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.touch(exist_ok=True)
    STALLED_DIR.mkdir(parents=True, exist_ok=True)
    WATCH_DIR.mkdir(parents=True, exist_ok=True)


# --- Parsing ---


def parse_episode(
    filename: str, path: str = "", stalled: bool = False, watched: bool = False
) -> Episode | None:
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
        stalled=stalled,
        watched=watched,
    )


# --- History ---


def read_history() -> list[HistoryEntry]:
    """Read history entries from JSONL file."""
    print(f"Reading history from {HISTORY_FILE}")
    if not HISTORY_FILE.exists():
        print(f"History file does not exist: {HISTORY_FILE}")
        return []

    entries: list[HistoryEntry] = []
    print(f"Processing {len(entries)} items")
    for line in HISTORY_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # Legacy format: plain path
            entries.append(HistoryEntry(action="watched", path=line))
    return entries


def write_history_entry(entry: HistoryEntry) -> None:
    """Append a single entry to history file."""
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def watched_filenames() -> set[str]:
    """Get set of watched episode filenames."""
    return {
        Path(e["path"]).name for e in read_history() if e.get("action") == "watched"
    }


# --- Entry producers ---


def disk_entries() -> list[Episode]:
    """Get all episodes on disk as parsed entries."""
    main_files = set(BASE_PATH.glob(VIDEO_GLOB))
    stalled_files = set(STALLED_DIR.glob(VIDEO_GLOB))
    watched = watched_filenames()

    entries = []
    for path in sorted(main_files | stalled_files):
        if ep := parse_episode(
            path.name,
            path=str(path),
            stalled=STALLED_DIR in path.parents,
            watched=path.name in watched,
        ):
            entries.append(ep)
    return entries


# --- Library ---


def build_library(entries: list[Episode] | None = None) -> dict[str, dict]:
    """Build library state from entries, grouped by series."""
    if entries is None:
        entries = disk_entries()

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
                "watched": ep["watched"],
                "stalled": ep["stalled"],
            }
        )

    # Sort episodes and compute aggregates
    for series in series_map.values():
        series["episodes"].sort(key=lambda e: e["episode"])
        series["latest_episode"] = max(e["episode"] for e in series["episodes"])
        series["latest_watched"] = max(
            (e["episode"] for e in series["episodes"] if e["watched"]),
            default=0,
        )

    return series_map


def torrent_url(group: str, title: str, quality: str) -> str:
    """Build nyaa.si search URL."""
    if group in TRUSTED_GROUPS:
        query = f"/user/{group}?f=0&c=0_0&q={title}+%5B{quality}%5D"
    else:
        query = f"?f=0&c=0_0&q={group}+%5B{title}+%5B{quality}%5D"
    return TORRENTS_BASE_URL + query


def parse_torrent_row(row) -> dict | None:
    """Parse a single nyaa.si result row."""
    cells = row.find_all("td")
    if len(cells) < 8:
        return None

    cat, name, download, size, date, seeds, leeches, status = cells
    title_link = name.find_all("a")[-1] if name.find_all("a") else None
    if not title_link:
        return None

    info = parse_episode(title_link.get("title", ""))
    if not info:
        return None

    torrent_link = download.find("i", attrs={"class": "fa-download"})
    magnet_link = download.find("i", attrs={"class": "fa-magnet"})

    if torrent_link and torrent_link.parent:
        info["torrent"] = TORRENTS_BASE_URL + torrent_link.parent.get("href", "")
    if magnet_link and magnet_link.parent:
        info["magnet"] = magnet_link.parent.get("href", "")

    return info


async def check_nyaa(group: str, title: str, after_episode: float) -> list[dict]:
    """Check nyaa.si for episodes newer than after_episode."""
    url = torrent_url(group, title, "1080p")  # Always search for 1080p

    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for row in soup.find_all("tr", attrs={"class": "success"}):
        info = parse_torrent_row(row)
        if info and info["title"] == title and info["episode"] > after_episode:
            results.append(info)

    # Dedupe by episode number
    seen_eps: dict[float, dict] = {}
    for ep in results:
        seen_eps[ep["episode"]] = ep

    return sorted(seen_eps.values(), key=lambda e: e["episode"])


def download_torrent(episode: dict) -> str:
    """Download torrent file to watch directory."""
    ensure_paths()
    torrent_url = episode["torrent"]
    filename = torrent_url.split("/")[-1]
    dest = WATCH_DIR / filename
    urlretrieve(torrent_url, dest)
    return str(dest)


async def get_library(series: str | None = None) -> dict:
    """Get local anime library state."""
    ensure_paths()
    library = build_library()

    if series:
        if series in library:
            return {"series": [library[series]]}
        return {"series": [], "error": f"Series '{series}' not found"}

    return {"series": sorted(library.values(), key=lambda s: s["title"])}


def _build_history_entry(action: str, path: Path) -> HistoryEntry:
    """Build a history entry with parsed metadata."""
    entry = HistoryEntry(
        ts=datetime.now(timezone.utc).isoformat(),
        action=action,
        path=str(path),
    )
    if parsed := parse_episode(path.name):
        entry["series"] = parsed["title"]
        entry["episode"] = parsed["episode"]
        entry["group"] = parsed["group"]
        entry["quality"] = parsed["quality"]
    return entry


async def mark_episode(path: str, status: str) -> dict:
    """Mark an episode as watched or stalled."""
    ensure_paths()
    episode_path = Path(path)

    if not episode_path.exists():
        possible = BASE_PATH / episode_path.name
        if possible.exists():
            episode_path = possible
        else:
            return {"error": f"Episode not found: {path}"}

    if status == "watched":
        write_history_entry(_build_history_entry("watched", episode_path))
        return {"status": "marked_watched", "path": str(episode_path)}

    if status == "stalled":
        dest = STALLED_DIR / episode_path.name
        episode_path.rename(dest)
        write_history_entry(_build_history_entry("stalled", dest))
        return {"status": "marked_stalled", "path": str(dest)}

    return {"error": f"Unknown status: {status}"}


async def check_episodes(series: str | None = None, download: bool = False) -> dict:
    """Check nyaa.si for new episodes."""
    ensure_paths()
    library = build_library()

    if series:
        if series not in library:
            return {"error": f"Series '{series}' not found", "available": []}
        to_check = [library[series]]
    else:
        to_check = list(library.values())

    available = []
    downloaded = []

    for s in to_check:
        try:
            new_eps = await check_nyaa(
                s["group"],
                s["title"],
                s["latest_episode"],
            )
            for ep in new_eps:
                entry = {
                    "series": s["title"],
                    "episode": ep["episode"],
                    "torrent": ep.get("torrent"),
                    "group": ep["group"],
                    "quality": ep["quality"],
                }
                available.append(entry)

                if download and ep.get("torrent"):
                    dest = download_torrent(ep)
                    downloaded.append({**entry, "downloaded_to": dest})

        except Exception as e:
            available.append(
                {
                    "series": s["title"],
                    "error": str(e),
                }
            )

    return {
        "available": available,
        "downloaded": downloaded if download else None,
        "checked_series": len(to_check),
    }


async def check_and_download():
    """CLI entry point: check all series and download new episodes."""
    result = await check_episodes(download=True)
    print(f"Checked {result['checked_series']} series")
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
