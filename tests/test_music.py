"""Tests for music/MPD library."""

import pytest

from local_mcp.lib.music import (
    _should_skip,
    parse_list_response,
    parse_response,
)


# parse_response tests

@pytest.mark.parametrize("lines,expected", [
    ([], {}),
    (["volume: 100"], {"volume": "100"}),
    (["state: play", "song: 5"], {"state": "play", "song": "5"}),
    (["Artist: Pink Floyd", "Title: Comfortably Numb"], {"Artist": "Pink Floyd", "Title": "Comfortably Numb"}),
    (["file: Music/Artist/Album/01 Track.mp3"], {"file": "Music/Artist/Album/01 Track.mp3"}),
    # Lines without ": " should be ignored
    (["OK", "some garbage"], {}),
    (["key: value", "nocolon"], {"key": "value"}),
])
def test_parse_response(lines, expected):
    assert parse_response(lines) == expected


@pytest.mark.parametrize("lines,expected", [
    # Empty response
    ([], []),
    # Single file
    (
        ["file: Artist/Album/track.mp3", "Title: Track 1", "Time: 180"],
        [{"file": "Artist/Album/track.mp3", "Title": "Track 1", "Time": "180"}],
    ),
    # Single directory
    (
        ["directory: Artist/Album"],
        [{"directory": "Artist/Album"}],
    ),
    # Multiple files
    (
        [
            "file: track1.mp3",
            "Title: Track 1",
            "file: track2.mp3",
            "Title: Track 2",
        ],
        [
            {"file": "track1.mp3", "Title": "Track 1"},
            {"file": "track2.mp3", "Title": "Track 2"},
        ],
    ),
    # Mixed files and directories
    (
        [
            "directory: Artist1",
            "directory: Artist2",
            "file: loose.mp3",
            "Title: Loose Track",
        ],
        [
            {"directory": "Artist1"},
            {"directory": "Artist2"},
            {"file": "loose.mp3", "Title": "Loose Track"},
        ],
    ),
])
def test_parse_list_response(lines, expected):
    assert parse_list_response(lines) == expected


# _should_skip tests

@pytest.mark.parametrize("path,patterns,expected", [
    # No patterns - never skip
    ("Music/Artist/Album/track.mp3", [], False),
    # Simple string match
    ("Music/The Dresden Files/book.mp3", ["Dresden Files"], True),
    ("Music/Artist/Album/track.mp3", ["Dresden Files"], False),
    # Regex patterns
    ("Audiobooks/Book 1/chapter.mp3", [r"^Audiobooks"], True),
    ("Music/Audiobooks tribute/song.mp3", [r"^Audiobooks"], False),  # Doesn't start with
    # Multiple patterns - any match
    ("Music/Dresden/track.mp3", ["Audiobook", "Dresden"], True),
    ("Music/Artist/track.mp3", ["Audiobook", "Dresden"], False),
    # Case sensitivity
    ("Music/AUDIOBOOKS/track.mp3", ["audiobooks"], False),  # Default is case-sensitive
    ("Music/AUDIOBOOKS/track.mp3", ["(?i)audiobooks"], True),  # Case-insensitive regex
])
def test_should_skip(path, patterns, expected):
    assert _should_skip(path, patterns) == expected


@pytest.mark.parametrize("path,patterns", [
    ("Music/The Dresden Files/chapter1.mp3", ["The Dresden Files"]),
    ("Audiobooks/Discworld/Guards Guards/01.mp3", ["Audiobooks"]),
    ("Podcasts/Tech/episode.mp3", ["Podcasts", "Interviews"]),
])
def test_should_skip_matches(path, patterns):
    assert _should_skip(path, patterns) is True


@pytest.mark.parametrize("path,patterns", [
    ("Music/Pink Floyd/The Wall/track.mp3", ["Dresden", "Audiobook"]),
    ("Jazz/Miles Davis/album/track.mp3", [r"^Classical"]),
])
def test_should_skip_no_match(path, patterns):
    assert _should_skip(path, patterns) is False
