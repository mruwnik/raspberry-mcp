"""Shared fixtures for local-mcp tests."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def mock_env(monkeypatch):
    """Factory fixture for setting environment variables."""
    def _set_env(**kwargs):
        for key, value in kwargs.items():
            monkeypatch.setenv(key, value)
    return _set_env


@pytest.fixture
def anime_files(temp_dir):
    """Create test anime files in temp directory."""
    files = [
        "[SubsPlease] Frieren - 01 [1080p].mkv",
        "[SubsPlease] Frieren - 02 [1080p].mkv",
        "[Erai-raws] Dandadan - 05 [1080p].mkv",
    ]
    for f in files:
        (temp_dir / f).touch()
    return temp_dir, files


@pytest.fixture
def htpasswd_file(temp_dir):
    """Create a test htpasswd file."""
    htpasswd_path = temp_dir / ".htpasswd"
    # bcrypt hash of "testpass"
    htpasswd_path.write_text("testuser:$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKyNiLXCJzFgIWC\n")
    return htpasswd_path


@pytest.fixture
def mock_mpd_connection():
    """Mock MPD connection for testing."""
    reader = AsyncMock()
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return reader, writer
