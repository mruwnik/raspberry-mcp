"""Centralized settings for local-mcp."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# Server
# =============================================================================
SERVER_PORT = int(os.getenv("LOCAL_MCP_PORT", "3001"))
SERVER_BASE_URL = os.getenv("LOCAL_MCP_BASE_URL", "http://localhost:3001")


# =============================================================================
# Auth
# =============================================================================
HTPASSWD_PATH = Path(os.getenv("LOCAL_MCP_HTPASSWD", ".htpasswd"))
TOKEN_DB_PATH = Path(os.getenv("LOCAL_MCP_TOKEN_DB", ".token_db.json"))


# =============================================================================
# MPD / Music
# =============================================================================
MPD_HOST = os.getenv("MPD_HOST", "localhost")
MPD_PORT = int(os.getenv("MPD_PORT", "6600"))

# Comma-separated list of regex patterns to skip (audiobooks, etc.)
_skip_env = os.getenv("MPD_SKIP_PATTERNS", "The Dresden Files")
MPD_SKIP_PATTERNS: list[str] = [p.strip() for p in _skip_env.split(",") if p.strip()]


# =============================================================================
# Anime
# =============================================================================
ANIME_BASE_PATH = Path(os.getenv("ANIME_BASE_PATH", "/media/data/Unsorted"))
ANIME_HISTORY_FILE = ANIME_BASE_PATH / ".anime_history"
ANIME_STALLED_DIR = ANIME_BASE_PATH / "stalled"
ANIME_WATCH_DIR = ANIME_BASE_PATH / ".watch/start"

ANIME_TRUSTED_GROUPS = ["SubsPlease", "Erai-raws"]
ANIME_VIDEO_GLOB = "[[]*.mkv"  # Match files starting with [ like "[SubsPlease] ..."


# =============================================================================
# Cache
# =============================================================================
CACHE_TIMEOUT = int(os.getenv("CACHE_TIMEOUT", 60 * 60 * 24 * 7))  # 7 days
