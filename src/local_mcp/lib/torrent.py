"""Torrent file handling and nyaa.si fetching."""

import logging
import shutil
import re
from pathlib import Path
from typing import TypedDict
from urllib.request import urlretrieve

import httpx
from bs4 import BeautifulSoup

from local_mcp.settings import (
    ANIME_BASE_PATH as BASE_PATH,
    ANIME_WATCH_DIR as WATCH_DIR,
)

logger = logging.getLogger(__name__)

NYAA_BASE_URL = "https://nyaa.si"

# Regex for torrent titles (no .mkv, handles language tags like (JA))
TORRENT_NAME_REGEX = re.compile(
    r"\[(?P<group>.*?)\]\s*(?P<title>.*?)\s*-\s*(?P<episode>\d+)\s*"
    r"(\((?:JA|EN|Multi)\))?\s*(END)?\s*(\[v\d+\])?\s*"
    r"[\[\(](?P<quality>\d+p[^\]]*?)[\]\)]"
)


class TorrentInfo(TypedDict):
    """Parsed torrent info from nyaa.si."""

    group: str
    title: str
    episode: float
    quality: str
    torrent: str  # may be empty string if not found
    magnet: str  # may be empty string if not found


# --- Bencode parsing ---


def _bdecode(data: bytes, idx: int = 0) -> tuple:
    """Decode bencode data, return (value, next_index)."""
    c = chr(data[idx])
    if c == "i":  # integer: i<num>e
        end = data.index(b"e", idx)
        return int(data[idx + 1 : end]), end + 1
    elif c == "l":  # list: l<items>e
        items, idx = [], idx + 1
        while chr(data[idx]) != "e":
            val, idx = _bdecode(data, idx)
            items.append(val)
        return items, idx + 1
    elif c == "d":  # dict: d<key><val>...e
        d, idx = {}, idx + 1
        while chr(data[idx]) != "e":
            key, idx = _bdecode(data, idx)
            val, idx = _bdecode(data, idx)
            d[key.decode() if isinstance(key, bytes) else key] = val
        return d, idx + 1
    elif c.isdigit():  # string: <len>:<bytes>
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        start = colon + 1
        return data[start : start + length], start + length
    raise ValueError(f"Invalid bencode at {idx}: {c!r}")


VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".webm")


def video_filename(torrent_path: Path) -> str | None:
    """Extract video filename from a torrent file."""
    try:
        data = torrent_path.read_bytes()
        info = _bdecode(data)[0].get("info", {})

        # Single file torrent
        if "name" in info and "files" not in info:
            name = info["name"]
            return name.decode() if isinstance(name, bytes) else name

        # Multi-file torrent - find the largest video file
        if "files" in info:
            best_file = None
            best_size = 0
            for f in info["files"]:
                path_parts = f.get("path", [])
                size = f.get("length", 0)
                if path_parts:
                    filename = path_parts[-1]
                    filename = (
                        filename.decode() if isinstance(filename, bytes) else filename
                    )
                    if filename.endswith(VIDEO_EXTENSIONS) and size > best_size:
                        best_file = filename
                        best_size = size
            return best_file
    except Exception as e:
        logger.warning(f"Failed to parse torrent {torrent_path}: {e}")
    return None


def video_path(torrent_path: Path) -> str | None:
    """Get full expected video path from a torrent file."""
    if filename := video_filename(torrent_path):
        return str(BASE_PATH / filename)
    return None


# --- Torrent downloading ---


def ensure_watch_dir():
    """Create watch directory if it doesn't exist."""
    WATCH_DIR.mkdir(parents=True, exist_ok=True)


def download(torrent: str, fallback_name: str | None = None) -> Path:
    """Copy or download a torrent file to the watch directory.

    Args:
        torrent: Local path or URL to a .torrent file
        fallback_name: Filename to use if URL doesn't end in .torrent

    Returns:
        Path to the torrent in the watch directory

    Raises:
        FileNotFoundError: If local file doesn't exist
    """
    ensure_watch_dir()

    if torrent.startswith(("http://", "https://")):
        filename = torrent.split("/")[-1]
        if not filename.endswith(".torrent") and fallback_name:
            filename = fallback_name
        dest = WATCH_DIR / filename
        urlretrieve(torrent, dest)
    else:
        src = Path(torrent)
        if not src.exists():
            raise FileNotFoundError(f"Torrent not found: {torrent}")
        dest = WATCH_DIR / src.name
        if src != dest:
            shutil.copy2(src, dest)

    return dest


# --- Nyaa.si fetching ---


def _parse_row(row) -> TorrentInfo | None:
    """Parse a single nyaa.si result row."""
    cells = row.find_all("td")
    if len(cells) < 8:
        return None

    cat, name, dl, size, date, seeds, leeches, status = cells
    title_link = name.find_all("a")[-1] if name.find_all("a") else None
    if not title_link:
        return None

    match = TORRENT_NAME_REGEX.match(title_link.get("title", ""))
    if not match:
        return None

    groups = match.groupdict()

    torrent_url = ""
    magnet_url = ""
    torrent_link = dl.find("i", attrs={"class": "fa-download"})
    magnet_link = dl.find("i", attrs={"class": "fa-magnet"})
    if torrent_link and torrent_link.parent:
        torrent_url = NYAA_BASE_URL + torrent_link.parent.get("href", "")
    if magnet_link and magnet_link.parent:
        magnet_url = magnet_link.parent.get("href", "")

    return TorrentInfo(
        group=groups["group"],
        title=groups["title"].strip(),
        episode=float(groups["episode"]) if groups["episode"] else -1,
        quality=groups["quality"],
        torrent=torrent_url,
        magnet=magnet_url,
    )


async def fetch_group_releases(group: str, pages: int = 3) -> list[TorrentInfo]:
    """Fetch recent releases from a trusted group via nyaa.si search.

    Args:
        group: Group name to search for
        pages: Number of pages to fetch (each ~75 results)

    Returns:
        List of parsed torrent info dicts
    """
    results = []

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; anime-checker/1.0)",
        "Accept-Encoding": "gzip, deflate",
    }

    async with httpx.AsyncClient(headers=headers) as client:
        for page in range(1, pages + 1):
            # f=2 = trusted only, c=1_2 = Anime English-translated
            url = f"{NYAA_BASE_URL}/?f=2&c=1_2&q={group}"
            if page > 1:
                url += f"&p={page}"

            response = await client.get(url, timeout=30.0)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            for row in soup.find_all("tr", attrs={"class": "success"}):
                if info := _parse_row(row):
                    results.append(info)

    return results
