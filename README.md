# local-mcp

A local MCP server with music/MPD control and anime download management tools.

## Features

### Music Control (MPD)

- Play, pause, stop, skip tracks
- Browse music library
- Play random tracks from any directory
- Configurable skip patterns for audiobooks/podcasts

### Anime Management

- Track local anime library state
- Check nyaa.si for new episodes
- Auto-download from trusted groups (SubsPlease, Erai-raws)
- Mark episodes as watched or stalled
- CLI command for cron-based automation

## Installation

```bash
uv sync
```

## Configuration

All settings are configured via environment variables (or `.env` file):

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCAL_MCP_PORT` | `3000` | Server port |
| `LOCAL_MCP_BASE_URL` | `http://localhost:3000` | Base URL for OAuth redirects |
| `LOCAL_MCP_HTPASSWD` | `.htpasswd` | Path to htpasswd file |

### MPD

| Variable | Default | Description |
|----------|---------|-------------|
| `MPD_HOST` | `localhost` | MPD server host |
| `MPD_PORT` | `6600` | MPD server port |
| `MPD_SKIP_PATTERNS` | `The Dresden Files` | Comma-separated regex patterns to skip |

### Anime

| Variable | Default | Description |
|----------|---------|-------------|
| `ANIME_BASE_PATH` | `/media/data/Unsorted` | Base directory for anime files |

### Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_TIMEOUT` | `604800` (7 days) | Cache timeout in seconds |

## Usage

### Running the Server

```bash
# Start server
uv run local-mcp

# With auto-reload for development
uv run local-mcp --reload
```

### CLI Commands

```bash
# Check for new episodes and download them (for cron)
uv run anime-check
```

## Authentication

The server uses htpasswd-based OAuth authentication. On first run, it creates a `.htpasswd` file with a randomly generated password and prints the credentials to stdout:

```
============================================================
Created new htpasswd file with default credentials:
  Username: admin
  Password: <random>
  File: .htpasswd
============================================================
```

Add additional users with:

```bash
htpasswd .htpasswd newuser
```

## MCP Tools

### Music

| Tool | Description |
|------|-------------|
| `mpd_player_command` | Execute MPD commands (play, pause, stop, next, etc.) |
| `mpd_browse_directory` | Browse music library |
| `mpd_play_tracks` | Add tracks to playlist and play |
| `mpd_play_random_tracks` | Play random tracks from a directory |
| `mpd_get_status` | Get current player status |

### Anime

| Tool | Description |
|------|-------------|
| `anime_library` | Get local library state |
| `anime_mark` | Mark episode as watched/stalled |
| `anime_check` | Check nyaa.si for new episodes |

## Development

### Running Tests

```bash
uv sync --group dev
uv run pytest tests/ -v
```
