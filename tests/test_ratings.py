"""Tests for series-level ratings."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def ratings_env(temp_dir, monkeypatch):
    """Point the ratings module at a temp file."""
    from local_mcp.lib import ratings
    monkeypatch.setattr(ratings, "RATINGS_FILE", temp_dir / ".anime_ratings")
    monkeypatch.setattr(ratings, "RATINGS_LOCK_FILE", temp_dir / ".anime_ratings.lock")
    return temp_dir


def test_write_and_read_rating(ratings_env):
    from local_mcp.lib import ratings
    entry = ratings.write_rating("Grand Blue S3", 5.0, "finished")
    assert entry["series"] == "Grand Blue S3"
    latest = ratings.latest_ratings()
    assert latest["Grand Blue S3"]["rating"] == 5.0
    assert latest["Grand Blue S3"]["status"] == "finished"
    assert latest["Grand Blue S3"]["origin"] == "local"
    assert latest["Grand Blue S3"]["synced_to_ap"] is False


def test_latest_entry_wins(ratings_env):
    from local_mcp.lib import ratings
    ratings.write_rating("Mushoku Tensei", 3.0, "watching")
    ratings.write_rating("Mushoku Tensei", None, "dropped")
    latest = ratings.latest_ratings()
    assert latest["Mushoku Tensei"]["status"] == "dropped"
    assert latest["Mushoku Tensei"]["rating"] is None


def test_drop_without_rating_is_legal(ratings_env):
    from local_mcp.lib import ratings
    entry = ratings.write_rating("Hana-Kimi", None, "dropped")
    assert entry["rating"] is None


@pytest.mark.parametrize("bad", [0.0, 0.3, 5.5, 4.25, -1])
def test_invalid_ratings_rejected(ratings_env, bad):
    from local_mcp.lib import ratings
    with pytest.raises(ValueError):
        ratings.write_rating("X", bad, "finished")


@pytest.mark.parametrize("ap_status,expected_status", [
    ("Watched", "finished"),
    ("Dropped", "dropped"),
    ("Won't Watch", "dropped"),
    ("Stalled", "dropped"),
    ("Watching", "watching"),
])
def test_migrate_status_mapping(ratings_env, ap_status, expected_status):
    from local_mcp.lib import ratings
    src = ratings_env / "ap.json"
    src.write_text(json.dumps([
        {"title": "Some Show", "year": 2020, "status": ap_status,
         "avg_rating": 4.0, "rating": 3.5, "episodes": 12, "times_watched": 1},
    ]))
    result = ratings.migrate_anime_planet(src)
    assert result["added"] == 1
    latest = ratings.latest_ratings()
    assert latest["Some Show"]["status"] == expected_status
    assert latest["Some Show"]["rating"] == 3.5
    assert latest["Some Show"]["origin"] == "anime-planet"
    assert latest["Some Show"]["synced_to_ap"] is True
    assert latest["Some Show"]["ap_status"] == ap_status


def test_migrate_skips_want_to_watch_and_unrated_unwatched(ratings_env):
    from local_mcp.lib import ratings
    src = ratings_env / "ap.json"
    src.write_text(json.dumps([
        {"title": "Planned", "year": 2025, "status": "Want to Watch", "rating": None},
        {"title": "Rated Drop", "year": 2019, "status": "Dropped", "rating": None},
    ]))
    result = ratings.migrate_anime_planet(src)
    # Want to Watch skipped; unrated Dropped still carries drop signal -> kept
    assert result == {"added": 1, "skipped": 1}


def test_migrate_does_not_duplicate_existing_local(ratings_env):
    from local_mcp.lib import ratings
    ratings.write_rating("Some Show", 4.5, "finished")  # local entry exists
    src = ratings_env / "ap.json"
    src.write_text(json.dumps([
        {"title": "Some Show", "year": 2020, "status": "Watched", "rating": 3.5},
    ]))
    result = ratings.migrate_anime_planet(src)
    assert result["skipped"] == 1
    assert ratings.latest_ratings()["Some Show"]["rating"] == 4.5  # local wins


def test_anime_rate_tool_writes_entry(ratings_env):
    from local_mcp.tools.anime import anime_rate
    result = anime_rate.fn(series="Grand Blue S3", rating=5.0, status="finished")
    assert result.get("series") == "Grand Blue S3"
    from local_mcp.lib import ratings
    assert ratings.latest_ratings()["Grand Blue S3"]["rating"] == 5.0


def test_anime_rate_tool_rejects_bad_rating(ratings_env):
    from local_mcp.tools.anime import anime_rate
    result = anime_rate.fn(series="X", rating=4.7, status="finished")
    assert "error" in result


def test_anime_rate_tool_rejects_bad_status(ratings_env):
    from local_mcp.tools.anime import anime_rate
    result = anime_rate.fn(series="X", rating=4.0, status="paused")
    assert "error" in result


def test_library_enriched_with_ratings(ratings_env, monkeypatch):
    from local_mcp.lib import anime, ratings
    monkeypatch.setattr(anime, "BASE_PATH", ratings_env)
    monkeypatch.setattr(anime, "STALLED_DIR", ratings_env / "stalled")
    monkeypatch.setattr(anime, "HISTORY_FILE", ratings_env / ".anime_history")
    monkeypatch.setattr(anime, "HISTORY_LOCK_FILE", ratings_env / ".anime_history.lock")
    monkeypatch.setattr(anime, "WATCH_DIR", ratings_env / ".watch/start")
    (ratings_env / "[SubsPlease] Grand Blue S3 - 01 (1080p) [ABC].mkv").touch()
    ratings.write_rating("Grand Blue S3", 5.0, "watching")
    lib = anime.get_library()
    series = {s["title"]: s for s in lib["series"]}
    assert series["Grand Blue S3"]["rating"]["rating"] == 5.0
