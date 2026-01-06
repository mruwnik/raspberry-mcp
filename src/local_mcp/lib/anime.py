"""Anime download management - core implementation."""

import re
from pathlib import Path
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


def ensure_paths():
    """Create required directories if they don't exist."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.touch(exist_ok=True)
    STALLED_DIR.mkdir(parents=True, exist_ok=True)
    WATCH_DIR.mkdir(parents=True, exist_ok=True)


def parse_episode(filename: str) -> dict | None:
    """Parse episode info from filename."""
    match = ANIME_NAME_REGEX.match(filename)
    if not match:
        return None

    info = match.groupdict()
    info["episode"] = float(info["episode"]) if info["episode"] else -1
    return info


def seen_paths() -> set[Path]:
    """Get set of watched episode paths."""
    if not HISTORY_FILE.exists():
        return set()
    return {Path(line) for line in HISTORY_FILE.read_text().splitlines() if line}


def disk_episodes() -> list[Path]:
    """Get all episode files on disk (main dir + stalled)."""
    main = set(BASE_PATH.glob(VIDEO_GLOB))
    stalled = set(STALLED_DIR.glob(VIDEO_GLOB))
    return sorted(main | stalled)


def build_library() -> dict[str, dict]:
    """Build library state from disk + history."""
    seen = seen_paths()
    episodes = disk_episodes()

    # Group by series
    series_map: dict[str, dict] = {}

    for path in episodes:
        info = parse_episode(path.name)
        if not info:
            continue

        title = info["title"]
        if title not in series_map:
            series_map[title] = {
                "title": title,
                "group": info["group"],
                "quality": info["quality"],
                "episodes": [],
            }

        is_stalled = STALLED_DIR in path.parents
        series_map[title]["episodes"].append({
            "episode": info["episode"],
            "path": str(path),
            "watched": path in seen or any(p.name == path.name for p in seen),
            "stalled": is_stalled,
        })

    # Sort episodes within each series
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


async def mark_episode(path: str, status: str) -> dict:
    """Mark an episode as watched or stalled."""
    ensure_paths()
    episode_path = Path(path)

    if not episode_path.exists():
        # Check if it's just a filename
        possible = BASE_PATH / episode_path.name
        if possible.exists():
            episode_path = possible
        else:
            return {"error": f"Episode not found: {path}"}

    if status == "watched":
        # Add to history file
        with open(HISTORY_FILE, "a") as f:
            f.write(f"{episode_path}\n")
        return {"status": "marked_watched", "path": str(episode_path)}

    elif status == "stalled":
        # Move to stalled directory
        dest = STALLED_DIR / episode_path.name
        episode_path.rename(dest)
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
            available.append({
                "series": s["title"],
                "error": str(e),
            })

    return {
        "available": available,
        "downloaded": downloaded if download else None,
        "checked_series": len(to_check),
    }


async def check_and_download():
    """CLI entry point: check all series and download new episodes."""
    result = await check_episodes(download=True)
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
