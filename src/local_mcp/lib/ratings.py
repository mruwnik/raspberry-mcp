"""Series-level ratings — JSONL sibling of .anime_history.

Note: Uses fcntl for file locking, which is Unix-only (Linux/macOS).
"""

import fcntl  # Unix-only
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict

from local_mcp.settings import ANIME_RATINGS_FILE

logger = logging.getLogger(__name__)

RATINGS_FILE = ANIME_RATINGS_FILE
RATINGS_LOCK_FILE = ANIME_RATINGS_FILE.parent / ".anime_ratings.lock"

RatingStatus = Literal["finished", "dropped", "watching"]

# anime-planet export status -> our status (Want to Watch is skipped entirely)
AP_STATUS_MAP: dict[str, RatingStatus] = {
    "Watched": "finished",
    "Watching": "watching",
    "Dropped": "dropped",
    "Stalled": "dropped",
    "Won't Watch": "dropped",
}


class RatingEntry(TypedDict, total=False):
    ts: str
    series: str
    rating: float | None
    status: RatingStatus
    origin: str
    synced_to_ap: bool
    ap_status: str


@contextmanager
def _ratings_lock():
    RATINGS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RATINGS_LOCK_FILE, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _validate_rating(rating: float | None) -> None:
    if rating is None:
        return
    if not (0.5 <= rating <= 5) or (rating * 2) != int(rating * 2):
        raise ValueError(f"Rating must be 0.5-5 in 0.5 steps, got {rating}")


def _load_entries() -> list[RatingEntry]:
    if not RATINGS_FILE.exists():
        return []
    entries: list[RatingEntry] = []
    for i, line in enumerate(RATINGS_FILE.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning(f"Malformed JSON at line {i} in ratings file")
    return entries


def _append_unlocked(entry: RatingEntry) -> None:
    RATINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RATINGS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def write_rating(
    series: str,
    rating: float | None,
    status: RatingStatus,
    origin: str = "local",
    synced_to_ap: bool = False,
    ap_status: str | None = None,
) -> RatingEntry:
    """Append a rating entry (thread-safe). Latest entry per series wins."""
    _validate_rating(rating)
    entry = RatingEntry(
        ts=datetime.now(timezone.utc).isoformat(),
        series=series,
        rating=rating,
        status=status,
        origin=origin,
        synced_to_ap=synced_to_ap,
    )
    if ap_status is not None:
        entry["ap_status"] = ap_status
    with _ratings_lock():
        _append_unlocked(entry)
    return entry


def latest_ratings() -> dict[str, RatingEntry]:
    """Latest rating entry per series (file order = chronological)."""
    result: dict[str, RatingEntry] = {}
    for entry in _load_entries():
        if "series" in entry:
            result[entry["series"]] = entry
    return result


def migrate_anime_planet(path: Path) -> dict:
    """One-off merge of an anime-planet export (list of dicts with
    title/status/rating). Skips 'Want to Watch' and any series that already
    has a rating entry (existing entries win)."""
    data = json.loads(Path(path).read_text())
    existing = latest_ratings()
    added = skipped = 0
    with _ratings_lock():
        for item in data:
            title = item.get("title")
            ap_status = item.get("status")
            if not title or ap_status not in AP_STATUS_MAP:
                skipped += 1
                continue
            if title in existing:
                skipped += 1
                continue
            rating = item.get("rating")
            _validate_rating(rating)
            _append_unlocked(
                RatingEntry(
                    ts=datetime.now(timezone.utc).isoformat(),
                    series=title,
                    rating=rating,
                    status=AP_STATUS_MAP[ap_status],
                    origin="anime-planet",
                    synced_to_ap=True,
                    ap_status=ap_status,
                )
            )
            added += 1
    return {"added": added, "skipped": skipped}
