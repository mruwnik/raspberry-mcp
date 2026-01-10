"""Tests for anime library."""

from pathlib import Path
from unittest.mock import patch

import pytest

from local_mcp.lib.anime import (
    ANIME_NAME_REGEX,
    Episode,
    HistoryEntry,
    parse_episode,
)


# ANIME_NAME_REGEX tests

@pytest.mark.parametrize("filename,expected_match", [
    # Standard SubsPlease format
    ("[SubsPlease] Frieren - Beyond Journey's End - 01 [1080p].mkv", True),
    ("[SubsPlease] Dandadan - 05 [1080p].mkv", True),
    # Erai-raws format
    ("[Erai-raws] Sousou no Frieren - 28 END [1080p][Multiple Subtitle].mkv", True),
    # With version tag
    ("[SubsPlease] Show Name - 10 [v2][1080p].mkv", True),
    # Different quality
    ("[SubsPlease] Show - 01 [720p].mkv", True),
    ("[SubsPlease] Show - 01 (1080p).mkv", True),
    # Non-matching files
    ("random_video.mkv", False),
    ("Show.S01E01.1080p.mkv", False),
    # Note: "[NoGroup] Show [1080p].mkv" matches with empty episode (episode=-1)
])
def test_anime_name_regex_matches(filename, expected_match):
    match = ANIME_NAME_REGEX.match(filename)
    assert (match is not None) == expected_match


@pytest.mark.parametrize("filename,expected_groups", [
    (
        "[SubsPlease] Frieren - Beyond Journey's End - 01 [1080p].mkv",
        {"group": "SubsPlease", "title": "Frieren - Beyond Journey's End", "episode": "01", "quality": "1080p"},
    ),
    (
        "[SubsPlease] Dandadan - 05 [1080p].mkv",
        {"group": "SubsPlease", "title": "Dandadan", "episode": "05", "quality": "1080p"},
    ),
    (
        "[Erai-raws] Show - 28 END [1080p][Multiple Subtitle].mkv",
        {"group": "Erai-raws", "title": "Show", "episode": "28", "quality": "1080p"},
    ),
    (
        "[SubsPlease] Show - 10 [v2][1080p].mkv",
        {"group": "SubsPlease", "title": "Show", "episode": "10", "quality": "1080p"},
    ),
])
def test_anime_name_regex_groups(filename, expected_groups):
    match = ANIME_NAME_REGEX.match(filename)
    assert match is not None
    groups = match.groupdict()
    for key, value in expected_groups.items():
        assert groups[key] == value


# parse_episode tests

@pytest.mark.parametrize("filename,expected", [
    (
        "[SubsPlease] Frieren - 01 [1080p].mkv",
        {"group": "SubsPlease", "title": "Frieren", "episode": 1.0, "quality": "1080p"},
    ),
    (
        "[SubsPlease] Dandadan - 12 [1080p].mkv",
        {"group": "SubsPlease", "title": "Dandadan", "episode": 12.0, "quality": "1080p"},
    ),
    (
        "[Erai-raws] Show Name - 05 END [720p].mkv",
        {"group": "Erai-raws", "title": "Show Name", "episode": 5.0, "quality": "720p"},
    ),
])
def test_parse_episode_valid(filename, expected):
    result = parse_episode(filename)
    assert result is not None
    assert result["group"] == expected["group"]
    assert result["title"] == expected["title"]
    assert result["episode"] == expected["episode"]
    assert result["quality"] == expected["quality"]


@pytest.mark.parametrize("filename", [
    "random.mkv",
    "Show.S01E01.mkv",
    "",
    "not_a_video.txt",
])
def test_parse_episode_invalid(filename):
    result = parse_episode(filename)
    assert result is None


def test_parse_episode_returns_float():
    result = parse_episode("[SubsPlease] Show - 01 [1080p].mkv")
    assert result is not None
    assert isinstance(result["episode"], float)


# build_library tests (with mocked filesystem)

@pytest.fixture
def mock_anime_settings(temp_dir, monkeypatch):
    """Mock anime settings to use temp directory."""
    import sys
    # Remove cached settings module
    if "local_mcp.settings" in sys.modules:
        del sys.modules["local_mcp.settings"]
    if "local_mcp.lib.anime" in sys.modules:
        del sys.modules["local_mcp.lib.anime"]

    monkeypatch.setenv("ANIME_BASE_PATH", str(temp_dir))

    # Create necessary subdirs
    (temp_dir / "stalled").mkdir()
    (temp_dir / ".watch" / "start").mkdir(parents=True)
    (temp_dir / ".anime_history").touch()

    return temp_dir


def test_build_library_empty(mock_anime_settings):
    from local_mcp.lib.anime import build_library
    library = build_library()
    assert library == {}


def test_build_library_with_files(mock_anime_settings):
    temp_dir = mock_anime_settings
    # Create test files
    (temp_dir / "[SubsPlease] Frieren - 01 [1080p].mkv").touch()
    (temp_dir / "[SubsPlease] Frieren - 02 [1080p].mkv").touch()

    from local_mcp.lib.anime import build_library
    library = build_library()

    assert "Frieren" in library
    assert library["Frieren"]["latest_episode"] == 2.0
    assert len(library["Frieren"]["episodes"]) == 2


def test_build_library_multiple_series(mock_anime_settings):
    temp_dir = mock_anime_settings
    (temp_dir / "[SubsPlease] Frieren - 01 [1080p].mkv").touch()
    (temp_dir / "[SubsPlease] Dandadan - 05 [1080p].mkv").touch()

    from local_mcp.lib.anime import build_library
    library = build_library()

    assert "Frieren" in library
    assert "Dandadan" in library


def test_build_library_stalled_episodes(mock_anime_settings):
    temp_dir = mock_anime_settings
    stalled = temp_dir / "stalled"
    (stalled / "[SubsPlease] Dropped Show - 01 [1080p].mkv").touch()

    from local_mcp.lib.anime import build_library
    library = build_library()

    assert "Dropped Show" in library
    assert library["Dropped Show"]["episodes"][0]["status"] == "stalled"


def test_build_library_watched_episodes_jsonl_format(mock_anime_settings):
    """Test that JSONL format works."""
    import json
    temp_dir = mock_anime_settings
    episode = temp_dir / "[SubsPlease] Watched Show - 01 [1080p].mkv"
    episode.touch()

    # Mark as watched in history (JSONL format)
    history = temp_dir / ".anime_history"
    entry = {"status": "watched", "path": str(episode), "series": "Watched Show", "episode": 1.0}
    history.write_text(json.dumps(entry) + "\n")

    from local_mcp.lib.anime import build_library
    library = build_library()

    assert "Watched Show" in library
    assert library["Watched Show"]["episodes"][0]["status"] == "watched"
    assert library["Watched Show"]["latest_watched"] == 1.0


def test_write_history_entry_creates_jsonl(mock_anime_settings):
    """Test that write_history_entry creates valid JSONL."""
    import json
    temp_dir = mock_anime_settings
    history = temp_dir / ".anime_history"

    from local_mcp.lib.anime import write_history_entry, HistoryEntry
    write_history_entry(HistoryEntry(status="watched", path="/test/path.mkv", series="Test"))

    content = history.read_text().strip()
    entry = json.loads(content)
    assert entry["status"] == "watched"
    assert entry["series"] == "Test"


def test_build_library_manual_episodes(mock_anime_settings):
    """Test that manual episodes are detected from history."""
    import json
    temp_dir = mock_anime_settings
    episode = temp_dir / "[SubsPlease] Manual Show - 01 [1080p].mkv"
    episode.touch()

    # Mark as manual in history (JSONL format)
    history = temp_dir / ".anime_history"
    entry = {"status": "manual", "path": str(episode), "series": "Manual Show", "episode": 1.0}
    history.write_text(json.dumps(entry) + "\n")

    from local_mcp.lib.anime import build_library
    library = build_library()

    assert "Manual Show" in library
    assert library["Manual Show"]["episodes"][0]["status"] == "manual"


def test_mark_episode_manual(mock_anime_settings):
    """Test that mark_episode can mark as manual."""
    temp_dir = mock_anime_settings
    episode = temp_dir / "[SubsPlease] Test Show - 01 [1080p].mkv"
    episode.touch()

    from local_mcp.lib.anime import mark_episode
    result = mark_episode(str(episode), "manual")

    assert result["status"] == "manual"
    assert result["path"] == str(episode)
    # File should still exist (not moved like stalled)
    assert episode.exists()


def test_mark_episode_manual_recorded_in_history(mock_anime_settings):
    """Test that marking as manual records entry in history."""
    import json
    temp_dir = mock_anime_settings
    episode = temp_dir / "[SubsPlease] Test Show - 01 [1080p].mkv"
    episode.touch()

    from local_mcp.lib.anime import mark_episode
    mark_episode(str(episode), "manual")

    history = temp_dir / ".anime_history"
    lines = history.read_text().strip().split("\n")
    # Find the manual entry (skip the auto-added unwatched entry)
    entries = [json.loads(line) for line in lines]
    manual_entries = [e for e in entries if e.get("status") == "manual"]
    assert len(manual_entries) == 1
    assert manual_entries[0]["status"] == "manual"


def test_get_library_filter_manual(mock_anime_settings):
    """Test filtering library by manual status."""
    import json
    temp_dir = mock_anime_settings

    # Create manual and unwatched episodes
    manual_ep = temp_dir / "[SubsPlease] Manual Show - 01 [1080p].mkv"
    manual_ep.touch()
    unwatched_ep = temp_dir / "[SubsPlease] Other Show - 01 [1080p].mkv"
    unwatched_ep.touch()

    # Mark one as manual
    history = temp_dir / ".anime_history"
    entry = {"status": "manual", "path": str(manual_ep), "series": "Manual Show", "episode": 1.0}
    history.write_text(json.dumps(entry) + "\n")

    from local_mcp.lib.anime import get_library
    result = get_library(status="manual")

    assert len(result["series"]) == 1
    assert result["series"][0]["title"] == "Manual Show"
