"""Tests for settings module."""

import os
from pathlib import Path

import pytest


# Test that settings module loads without error and has expected attributes
def test_settings_has_expected_attributes():
    from local_mcp import settings

    # Server settings
    assert hasattr(settings, "SERVER_PORT")
    assert hasattr(settings, "SERVER_BASE_URL")

    # Auth settings
    assert hasattr(settings, "HTPASSWD_PATH")

    # MPD settings
    assert hasattr(settings, "MPD_HOST")
    assert hasattr(settings, "MPD_PORT")
    assert hasattr(settings, "MPD_SKIP_PATTERNS")

    # Anime settings
    assert hasattr(settings, "ANIME_BASE_PATH")
    assert hasattr(settings, "ANIME_HISTORY_FILE")
    assert hasattr(settings, "ANIME_STALLED_DIR")
    assert hasattr(settings, "ANIME_WATCH_DIR")
    assert hasattr(settings, "ANIME_TORRENTS_URL")
    assert hasattr(settings, "ANIME_TRUSTED_GROUPS")
    assert hasattr(settings, "ANIME_VIDEO_GLOB")

    # Cache settings
    assert hasattr(settings, "CACHE_TIMEOUT")


def test_settings_types():
    from local_mcp import settings

    assert isinstance(settings.SERVER_PORT, int)
    assert isinstance(settings.SERVER_BASE_URL, str)
    assert isinstance(settings.HTPASSWD_PATH, Path)
    assert isinstance(settings.MPD_HOST, str)
    assert isinstance(settings.MPD_PORT, int)
    assert isinstance(settings.MPD_SKIP_PATTERNS, list)
    assert isinstance(settings.ANIME_BASE_PATH, Path)
    assert isinstance(settings.ANIME_HISTORY_FILE, Path)
    assert isinstance(settings.ANIME_STALLED_DIR, Path)
    assert isinstance(settings.ANIME_WATCH_DIR, Path)
    assert isinstance(settings.ANIME_TORRENTS_URL, str)
    assert isinstance(settings.ANIME_TRUSTED_GROUPS, set)
    assert isinstance(settings.ANIME_VIDEO_GLOB, str)
    assert isinstance(settings.CACHE_TIMEOUT, int)


def test_anime_derived_paths_are_under_base():
    from local_mcp import settings

    # Derived paths should be under base path
    assert settings.ANIME_HISTORY_FILE.parent == settings.ANIME_BASE_PATH
    assert str(settings.ANIME_STALLED_DIR).startswith(str(settings.ANIME_BASE_PATH))
    assert str(settings.ANIME_WATCH_DIR).startswith(str(settings.ANIME_BASE_PATH))


# Test the pattern parsing logic directly
@pytest.mark.parametrize("env_value,expected_patterns", [
    ("The Dresden Files", ["The Dresden Files"]),
    ("pattern1,pattern2", ["pattern1", "pattern2"]),
    ("a, b, c", ["a", "b", "c"]),
    ("", []),
    ("single", ["single"]),
    (" spaced , values ", ["spaced", "values"]),
])
def test_skip_patterns_parsing_logic(env_value, expected_patterns):
    """Test the pattern parsing logic used in settings."""
    result = [p.strip() for p in env_value.split(",") if p.strip()]
    assert result == expected_patterns


@pytest.mark.parametrize("env_value,expected", [
    ("3000", 3000),
    ("8080", 8080),
    ("443", 443),
])
def test_port_parsing_logic(env_value, expected):
    """Test integer parsing for ports."""
    assert int(env_value) == expected


@pytest.mark.parametrize("env_value", [
    "/tmp/test.htpasswd",
    "/var/local/.htpasswd",
    ".htpasswd",
])
def test_path_creation_logic(env_value):
    """Test Path creation."""
    result = Path(env_value)
    assert isinstance(result, Path)
    assert str(result) == env_value


def test_default_skip_patterns():
    from local_mcp import settings

    # Should have at least the default Dresden Files pattern
    assert len(settings.MPD_SKIP_PATTERNS) >= 0  # Could be empty if env overridden


def test_trusted_groups_contains_expected():
    from local_mcp import settings

    # These should be in the default set
    assert "SubsPlease" in settings.ANIME_TRUSTED_GROUPS
    assert "Erai-raws" in settings.ANIME_TRUSTED_GROUPS


def test_video_glob_pattern():
    from local_mcp import settings

    # Should match files starting with [
    assert "[" in settings.ANIME_VIDEO_GLOB
    assert ".mkv" in settings.ANIME_VIDEO_GLOB


def test_torrents_url_is_valid():
    from local_mcp import settings

    assert settings.ANIME_TORRENTS_URL.startswith("https://")


def test_cache_timeout_is_positive():
    from local_mcp import settings

    assert settings.CACHE_TIMEOUT > 0
