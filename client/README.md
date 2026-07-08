# anime.lua — mpv as the anime client

An mpv script that turns mpv into the anime watch flow client: it lists
unwatched series from the MCP server, plays episodes back-to-back over
HTTPS, auto-marks episodes watched at 80%, and prompts for a series
rating when a series runs out (or when you drop it).

## Install

```bash
ln -s "$PWD/client/anime.lua" ~/.config/mpv/scripts/anime.lua
```

Requirements:

- `curl` on PATH (used for all MCP/OAuth HTTP calls).
- `python3` on PATH and `client/anime_auth.py` reachable at the path you
  set as `auth_helper` in the config (used once for the browser login).

For mid-episode resume, add to `~/.config/mpv/mpv.conf`:

```
save-position-on-quit=yes
```

Quitting mid-episode then reopening the same episode resumes where you
left off (episodes are only auto-marked watched at >= 80%).

## Config

`~/.config/anime-watch/config.json`:

```json
{
  "mcp_url": "https://ahiru.pl/mcp",
  "files_url": "https://media.ahiru.pl/files/Unsorted/",
  "http_user": "dan",
  "http_pass": "...",
  "auth_helper": "/Users/dan/code/local-mcp/client/anime_auth.py"
}
```

| Key           | Required | Meaning                                                                 |
|---------------|----------|-------------------------------------------------------------------------|
| `mcp_url`     | yes      | The MCP endpoint (stateless `POST /mcp`).                               |
| `files_url`   | no       | Base URL episodes are fetched from (default `https://media.ahiru.pl/files/Unsorted/`). |
| `http_user`   | no       | Basic-auth user for the files host (needed outside the LAN).           |
| `http_pass`   | no       | Basic-auth password for the files host.                                 |
| `auth_helper` | yes*     | Absolute path to `client/anime_auth.py`. *Needed for the first login and after tokens expire. |

Without a config file the script logs a warning and the menu stays
disabled.

## First run / auth

Tokens live in `~/.config/anime-watch/auth.json`, shared with
`watch.py`. On first use (no token file) the script runs the auth
helper: a browser window opens, you log in, and the menu loads once the
token file appears. Expired access tokens are refreshed automatically
using the stored refresh token; if refresh fails, delete `auth.json`
and press `a` to log in again.

## Keybindings

Menu (`a` opens it; it also opens automatically when mpv starts idle):

| Key       | Action                              |
|-----------|-------------------------------------|
| `↑` / `↓` | Select series                       |
| `Enter`   | Play all unwatched episodes in order|
| `d`       | Drop the selected series (opens the rating prompt with status `dropped`) |
| `q` / `Esc` | Close the menu                    |

During playback:

| Key | Action                                             |
|-----|----------------------------------------------------|
| `n` | Mark current episode `manual` (watch later) + next |

Rating prompt (appears when the last queued episode finishes, or on drop):

| Key       | Action                                              |
|-----------|-----------------------------------------------------|
| `1`-`5`   | Pick a whole-number rating                          |
| `.`       | Add half a step to the picked rating and submit (e.g. `4` then `.` = 4.5) |
| `Enter`   | Submit the picked whole-number rating               |
| `d`       | Submit as `dropped` (with the picked rating, or none) |
| `Esc`     | Skip rating                                         |

## Offline behavior

If a `anime_mark` / `anime_rate` call fails (server unreachable), it is
appended to `~/.config/anime-watch/pending-calls.jsonl` and the OSD
shows "saved offline". The queue is flushed the next time the menu
opens successfully.
