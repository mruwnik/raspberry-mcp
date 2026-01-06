"""Tests for anime library."""

from pathlib import Path
from unittest.mock import patch

import pytest

from local_mcp.lib.anime import (
    ANIME_NAME_REGEX,
    Episode,
    HistoryEntry,
    parse_episode,
    torrent_url,
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


# torrent_url tests

@pytest.mark.parametrize("group,title,quality,expected_contains", [
    # Trusted groups use /user/ path
    ("SubsPlease", "Frieren", "1080p", "/user/SubsPlease"),
    ("Erai-raws", "Dandadan", "1080p", "/user/Erai-raws"),
    # Non-trusted groups use search query
    ("RandomGroup", "Show", "1080p", "RandomGroup"),
])
def test_torrent_url_structure(group, title, quality, expected_contains):
    url = torrent_url(group, title, quality)
    assert expected_contains in url


@pytest.mark.parametrize("group,title,quality", [
    ("SubsPlease", "Frieren", "1080p"),
    ("Erai-raws", "Show Name", "720p"),
    ("RandomFansub", "Anime", "1080p"),
])
def test_torrent_url_contains_title_and_quality(group, title, quality):
    url = torrent_url(group, title, quality)
    assert title in url
    assert quality in url


def test_torrent_url_starts_with_base():
    url = torrent_url("SubsPlease", "Test", "1080p")
    assert url.startswith("https://nyaa.si")


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
    assert library["Dropped Show"]["episodes"][0]["stalled"] is True


def test_build_library_watched_episodes_legacy_format(mock_anime_settings):
    """Test that legacy plain-path format still works."""
    temp_dir = mock_anime_settings
    episode = temp_dir / "[SubsPlease] Watched Show - 01 [1080p].mkv"
    episode.touch()

    # Mark as watched in history (legacy plain path format)
    history = temp_dir / ".anime_history"
    history.write_text(f"{episode}\n")

    from local_mcp.lib.anime import build_library
    library = build_library()

    assert "Watched Show" in library
    assert library["Watched Show"]["episodes"][0]["watched"] is True
    assert library["Watched Show"]["latest_watched"] == 1.0


def test_build_library_watched_episodes_jsonl_format(mock_anime_settings):
    """Test that JSONL format works."""
    import json
    temp_dir = mock_anime_settings
    episode = temp_dir / "[SubsPlease] Watched Show - 01 [1080p].mkv"
    episode.touch()

    # Mark as watched in history (JSONL format)
    history = temp_dir / ".anime_history"
    entry = {"action": "watched", "path": str(episode), "series": "Watched Show", "episode": 1.0}
    history.write_text(json.dumps(entry) + "\n")

    from local_mcp.lib.anime import build_library
    library = build_library()

    assert "Watched Show" in library
    assert library["Watched Show"]["episodes"][0]["watched"] is True
    assert library["Watched Show"]["latest_watched"] == 1.0


def test_read_history_mixed_formats(mock_anime_settings):
    """Test reading history with mixed legacy and JSONL entries."""
    import json
    temp_dir = mock_anime_settings
    history = temp_dir / ".anime_history"

    # Mix of legacy and JSONL
    lines = [
        "/old/path/[SubsPlease] Old - 01 [1080p].mkv",
        json.dumps({"action": "watched", "path": "/new/path/file.mkv", "series": "New"}),
    ]
    history.write_text("\n".join(lines) + "\n")

    from local_mcp.lib.anime import read_history
    entries = read_history()

    assert len(entries) == 2
    assert entries[0]["path"] == "/old/path/[SubsPlease] Old - 01 [1080p].mkv"
    assert entries[0]["action"] == "watched"
    assert entries[1]["series"] == "New"


def test_write_history_entry_creates_jsonl(mock_anime_settings):
    """Test that write_history_entry creates valid JSONL."""
    import json
    temp_dir = mock_anime_settings
    history = temp_dir / ".anime_history"

    from local_mcp.lib.anime import write_history_entry
    write_history_entry({"action": "watched", "path": "/test/path.mkv", "series": "Test"})

    content = history.read_text().strip()
    entry = json.loads(content)
    assert entry["action"] == "watched"
    assert entry["series"] == "Test"


# Type tests


def test_parse_episode_returns_episode():
    """parse_episode returns Episode with all fields."""
    result = parse_episode("[SubsPlease] Show - 01 [1080p].mkv")
    assert result is not None
    assert set(result.keys()) == {"group", "title", "episode", "quality", "path", "stalled", "watched"}
    assert result["path"] == ""
    assert result["stalled"] is False
    assert result["watched"] is False


def test_parse_episode_with_status():
    """parse_episode accepts path and status parameters."""
    result = parse_episode("[SubsPlease] Show - 01 [1080p].mkv", path="/some/path.mkv", stalled=True, watched=True)
    assert result is not None
    assert result["path"] == "/some/path.mkv"
    assert result["stalled"] is True
    assert result["watched"] is True


# disk_entries tests


def test_disk_entries_empty(mock_anime_settings):
    """disk_entries returns empty list for empty directory."""
    from local_mcp.lib.anime import disk_entries
    entries = disk_entries()
    assert entries == []


def test_disk_entries_returns_episodes(mock_anime_settings):
    """disk_entries returns list of Episode."""
    temp_dir = mock_anime_settings
    (temp_dir / "[SubsPlease] Frieren - 01 [1080p].mkv").touch()

    from local_mcp.lib.anime import disk_entries
    entries = disk_entries()

    assert len(entries) == 1
    ep = entries[0]
    assert set(ep.keys()) == {"group", "title", "episode", "quality", "path", "stalled", "watched"}
    assert ep["group"] == "SubsPlease"
    assert ep["title"] == "Frieren"
    assert ep["episode"] == 1.0
    assert ep["quality"] == "1080p"
    assert ep["stalled"] is False
    assert ep["watched"] is False


def test_disk_entries_includes_stalled(mock_anime_settings):
    """disk_entries includes episodes from stalled directory."""
    temp_dir = mock_anime_settings
    stalled = temp_dir / "stalled"
    (stalled / "[SubsPlease] Dropped - 01 [1080p].mkv").touch()

    from local_mcp.lib.anime import disk_entries
    entries = disk_entries()

    assert len(entries) == 1
    assert entries[0]["stalled"] is True


def test_disk_entries_marks_watched(mock_anime_settings):
    """disk_entries marks watched episodes correctly."""
    import json
    temp_dir = mock_anime_settings
    episode = temp_dir / "[SubsPlease] Watched - 01 [1080p].mkv"
    episode.touch()

    history = temp_dir / ".anime_history"
    history.write_text(json.dumps({"action": "watched", "path": str(episode)}) + "\n")

    from local_mcp.lib.anime import disk_entries
    entries = disk_entries()

    assert len(entries) == 1
    assert entries[0]["watched"] is True


def test_build_library_accepts_entries(mock_anime_settings):
    """build_library can consume pre-built entries."""
    from local_mcp.lib.anime import build_library

    entries: list[Episode] = [
        Episode(
            group="SubsPlease",
            title="Test Show",
            episode=1.0,
            quality="1080p",
            path="/fake/path.mkv",
            stalled=False,
            watched=True,
        ),
        Episode(
            group="SubsPlease",
            title="Test Show",
            episode=2.0,
            quality="1080p",
            path="/fake/path2.mkv",
            stalled=False,
            watched=False,
        ),
    ]

    library = build_library(entries)

    assert "Test Show" in library
    assert library["Test Show"]["latest_episode"] == 2.0
    assert library["Test Show"]["latest_watched"] == 1.0
    assert len(library["Test Show"]["episodes"]) == 2
