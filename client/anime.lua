-- anime.lua — mpv as the anime client.
-- Menu of unwatched series from the MCP server; back-to-back playback over
-- HTTPS; auto-mark watched at 80%; series rating prompt when the series'
-- last available episode is the finale (or on drop) — otherwise just an
-- OSD nudge to rate manually with `r` in the menu. Config:
-- ~/.config/anime-watch/config.json
--   { "mcp_url": "https://ahiru.pl/mcp",
--     "files_url": "https://media.ahiru.pl/files/Unsorted/",
--     "auth_helper": "/Users/dan/code/local-mcp/client/anime_auth.py" }
-- Tokens: ~/.config/anime-watch/auth.json (written by anime_auth.py / watch.py).
-- That file is shared with watch.py's StoredAuth and must keep EXACTLY the keys
-- {access_token, refresh_token, expires_at, server_url} — the OAuth client id
-- is the fixed "anime-watch-cli" (same as watch.py), never stored in the file.

local mp = require "mp"
local utils = require "mp.utils"

local CONFIG_DIR = os.getenv("HOME") .. "/.config/anime-watch"
local CONFIG_FILE = CONFIG_DIR .. "/config.json"
local AUTH_FILE = CONFIG_DIR .. "/auth.json"
local RETRY_FILE = CONFIG_DIR .. "/pending-calls.jsonl"
local WATCHED_THRESHOLD = 0.8
local CLIENT_ID = "anime-watch-cli"  -- must match watch.py / anime_auth.py
local MENU_VISIBLE_ROWS = 15  -- OSD menu viewport height, in series rows

local config = {}
local auth = {}
local state = {
  menu_open = false,
  series = {},          -- from anime_library
  selected = 1,
  queue = {},           -- remaining episode paths for current series
  current_series = nil,
  current_path = nil,
  marked = {},          -- path -> true (marked this session)
  rating_open = false,
  rating_base = nil,    -- pending whole-number rating awaiting optional ".5"
  rating_series = nil,
  rating_status = "finished",
  rating_on_close = nil, -- callback run once the rating dialog is dismissed
  last_end_reason = nil,
  current_series_is_final = false,  -- does the queued series' last episode carry the finale marker?
}

-- ---------- small utils ----------

local function read_json(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local content = f:read("*a"); f:close()
  return utils.parse_json(content)
end

local function write_line(path, line)
  local f = io.open(path, "a")
  if f then f:write(line .. "\n"); f:close() end
end

local function osd(text, duration)
  mp.osd_message(text, duration or 3)
end

local function urlencode(s)
  return (s:gsub("[^%w%-%.%_%~%/]", function(c)
    return string.format("%%%02X", string.byte(c))
  end))
end

-- ---------- curl / MCP ----------

-- secrets go in `stdin` as a curl config (read via -K -) so they never appear in ps
local function curl(args, stdin)
  local res = mp.command_native({
    name = "subprocess", capture_stdout = true, capture_stderr = true,
    playback_only = false, args = args, stdin_data = stdin,
  })
  if res.status ~= 0 then return nil, res.stderr end
  return res.stdout
end

local function parse_sse(body)
  for line in body:gmatch("[^\r\n]+") do
    local data = line:match("^data: (.+)")
    if data then return utils.parse_json(data) end
  end
  -- plain JSON fallback
  return utils.parse_json(body)
end

local function refresh_tokens()
  if not auth.refresh_token then return false end
  local base = auth.server_url or "https://ahiru.pl"
  local body, err = curl({
    "curl", "-s", "-m", "15", "-X", "POST", base .. "/token", "-K", "-",
    "-u", CLIENT_ID .. ":",
    "--data-urlencode", "grant_type=refresh_token",
    "--data-urlencode", "client_id=" .. CLIENT_ID,
  }, string.format('data-urlencode = "refresh_token=%s"\n', auth.refresh_token))
  if not body then return false end
  local tokens = utils.parse_json(body)
  if not (tokens and tokens.access_token) then return false end
  auth.access_token = tokens.access_token
  auth.refresh_token = tokens.refresh_token or auth.refresh_token
  auth.expires_at = os.time() + (tokens.expires_in or 30 * 24 * 3600)
  auth.server_url = base
  local f = io.open(AUTH_FILE, "w")
  if f then
    -- keep watch.py's exact StoredAuth shape
    f:write(utils.format_json({
      access_token = auth.access_token,
      refresh_token = auth.refresh_token,
      expires_at = auth.expires_at,
      server_url = auth.server_url,
    }))
    f:close()
  end
  return true
end

local function mcp_call(tool, args_tbl, _retried)
  local payload = utils.format_json({
    jsonrpc = "2.0", id = 1, method = "tools/call",
    params = { name = tool, arguments = args_tbl or {} },
  })
  local body, err = curl({
    "curl", "-s", "-m", "20", "-X", "POST", config.mcp_url, "-K", "-",
    "-H", "Content-Type: application/json",
    "-H", "Accept: application/json, text/event-stream",
    "-d", payload,
  }, string.format('header = "Authorization: Bearer %s"\n', auth.access_token or ""))
  if not body then return nil, err end
  local resp = parse_sse(body)
  if resp and resp.error and not _retried then
    if refresh_tokens() then return mcp_call(tool, args_tbl, true) end
  end
  if not resp then return nil, "unparseable response" end
  if resp.error then return nil, utils.format_json(resp.error) end
  -- FastMCP puts tool output in result.structuredContent
  local result = resp.result or {}
  return result.structuredContent or result, nil
end

-- fire-and-forget with offline retry queue
local function mcp_call_queued(tool, args_tbl)
  local result, err = mcp_call(tool, args_tbl)
  if not result then
    write_line(RETRY_FILE, utils.format_json({ tool = tool, args = args_tbl }))
    osd("saved offline: " .. tool, 2)
  end
  return result
end

local function flush_retry_queue()
  local f = io.open(RETRY_FILE, "r")
  if not f then return end
  local lines = {}
  for line in f:lines() do table.insert(lines, line) end
  f:close()
  os.remove(RETRY_FILE)
  for _, line in ipairs(lines) do
    local item = utils.parse_json(line)
    if item then mcp_call_queued(item.tool, item.args) end
  end
end

-- ---------- auth bootstrap ----------

local function ensure_auth(and_then)
  auth = read_json(AUTH_FILE) or {}
  if auth.access_token then return and_then() end
  if not config.auth_helper then
    return osd("No auth. Set auth_helper in " .. CONFIG_FILE, 10)
  end
  osd("Log in via the browser window...", 30)
  mp.command_native_async({
    name = "subprocess", playback_only = false,
    args = { "python3", config.auth_helper },
  }, function()
    auth = read_json(AUTH_FILE) or {}
    if auth.access_token then osd("Logged in.", 2); and_then()
    else osd("Login failed — run " .. config.auth_helper .. " in a terminal to see why, then retry with key a", 10) end
  end)
end

-- ---------- library / menu ----------

local function unwatched(series)
  local eps = {}
  for _, ep in ipairs(series.episodes or {}) do
    if ep.status == "unwatched" then table.insert(eps, ep) end
  end
  table.sort(eps, function(a, b) return a.episode < b.episode end)
  return eps
end

local function load_library(and_then)
  osd("Loading library...", 60)
  local result, err = mcp_call("anime_library", { status = "unwatched" })
  if not result then return osd("Library failed: " .. tostring(err), 8) end
  state.series = {}
  for _, s in ipairs(result.series or {}) do
    if #unwatched(s) > 0 then table.insert(state.series, s) end
  end
  -- Newest activity first; series with no history timestamp sort last, alphabetically.
  table.sort(state.series, function(a, b)
    if a.last_activity and b.last_activity then
      return a.last_activity > b.last_activity
    elseif a.last_activity or b.last_activity then
      return a.last_activity ~= nil
    end
    return a.title < b.title
  end)
  and_then()
end

local menu_overlay = mp.create_osd_overlay("ass-events")

local function render_menu()
  local lines = { "{\\b1}Anime — unwatched{\\b0}", "" }
  local total = #state.series
  if total == 0 then
    table.insert(lines, "nothing unwatched — nice.")
  else
    -- Window ~MENU_VISIBLE_ROWS rows around the selection so it's always visible.
    local first = 1
    local last = total
    if total > MENU_VISIBLE_ROWS then
      first = state.selected - math.floor(MENU_VISIBLE_ROWS / 2)
      first = math.max(1, math.min(first, total - MENU_VISIBLE_ROWS + 1))
      last = first + MENU_VISIBLE_ROWS - 1
    end
    if first > 1 then
      table.insert(lines, string.format("{\\alpha&H80&}↑ %d more{\\alpha&H00&}", first - 1))
    end
    for i = first, last do
      local s = state.series[i]
      local marker = (i == state.selected) and "▶ " or "   "
      local eps = unwatched(s)
      table.insert(lines, string.format("%s%s   {\\alpha&H80&}%d new{\\alpha&H00&}",
        marker, s.title, #eps))
    end
    if last < total then
      table.insert(lines, string.format("{\\alpha&H80&}↓ %d more{\\alpha&H00&}", total - last))
    end
  end
  table.insert(lines, "")
  table.insert(lines, "{\\alpha&H80&}[↑↓] select  [⏎] play  [d] drop  [r] rate  [q/esc] close{\\alpha&H00&}")
  menu_overlay.data = table.concat(lines, "\\N")
  menu_overlay:update()
end

local function close_menu()
  state.menu_open = false
  menu_overlay:remove()
  for _, key in ipairs({ "UP", "DOWN", "ENTER", "d", "r", "q", "ESC" }) do
    mp.remove_key_binding("animenu_" .. key)
  end
end

local function play_selected()
  local s = state.series[state.selected]
  if not s then return end
  close_menu()
  state.current_series = s.title
  state.queue = {}
  local eps = unwatched(s)
  state.current_series_is_final = #eps > 0 and eps[#eps].is_final or false
  for _, ep in ipairs(eps) do table.insert(state.queue, ep.path) end
  mp.commandv("playlist-clear")
  local first = true
  for _, path in ipairs(state.queue) do
    local filename = path:match("([^/]+)$")
    mp.command_native({
      name = "loadfile",
      url = config.files_url .. urlencode(filename),
      flags = first and "replace" or "append-play",
      -- files host needs the MCP token off-LAN; per-file so other URLs played in mpv never get it
      options = { ["http-header-fields"] = "Authorization: Bearer " .. (auth.access_token or "") },
    })
    first = false
  end
end

local rating_overlay = mp.create_osd_overlay("ass-events")

local function close_rating()
  state.rating_open = false
  state.rating_base = nil
  rating_overlay:remove()
  for _, key in ipairs({ "1", "2", "3", "4", "5", ".", "d", "ENTER", "ESC" }) do
    mp.remove_key_binding("anirate_" .. key)
  end
  local on_close = state.rating_on_close
  state.rating_on_close = nil
  if on_close then on_close() end
end

local function submit_rating(rating)
  local series = state.rating_series
  local status = state.rating_status
  close_rating()
  mcp_call_queued("anime_rate", { series = series, rating = rating, status = status })
  osd(string.format("%s: %s (%s)", series, rating and tostring(rating) or "no rating", status), 3)
end

local function render_rating()
  rating_overlay.data = string.format(
    "{\\b1}Rate: %s{\\b0}\\N%s\\N\\N{\\alpha&H80&}[1-5] rate (then . for +0.5, ⏎ to confirm)  [d] dropped  [esc] skip{\\alpha&H00&}",
    state.rating_series,
    state.rating_base and ("→ " .. state.rating_base .. "  (press . for +0.5 or ⏎)") or "")
  rating_overlay:update()
end

local function open_rating(series, status, on_close)
  state.rating_open = true
  state.rating_series = series
  state.rating_status = status or "finished"
  state.rating_base = nil
  state.rating_on_close = on_close
  for n = 1, 5 do
    local key = tostring(n)
    mp.add_forced_key_binding(key, "anirate_" .. key, function()
      state.rating_base = n
      render_rating()
    end)
  end
  mp.add_forced_key_binding(".", "anirate_.", function()
    if state.rating_base and state.rating_base < 5 then
      submit_rating(state.rating_base + 0.5)
    end
  end)
  mp.add_forced_key_binding("ENTER", "anirate_ENTER", function()
    if state.rating_base then submit_rating(state.rating_base + 0.0) end
  end)
  mp.add_forced_key_binding("d", "anirate_d", function()
    state.rating_status = "dropped"
    if state.rating_base then submit_rating(state.rating_base + 0.0)
    else submit_rating(nil) end
  end)
  mp.add_forced_key_binding("ESC", "anirate_ESC", close_rating)
  render_rating()
end

-- forward declaration so reopen_menu_if_idle (below) can reference open_menu
local open_menu

local function reopen_menu_if_idle()
  -- After a finished series or a dismissed rating dialog, mpv sits idle with a
  -- blank window. Bring the series menu back rather than leaving it blank.
  -- Guarded on idle so rating from the menu *during playback* returns to the
  -- video, not the menu.
  if not mp.get_property_bool("idle-active") then return end
  if not state.menu_open and not state.rating_open then open_menu() end
end

function open_menu()
  if state.menu_open then return end
  if not config.mcp_url then
    return osd("anime.lua: no config at " .. CONFIG_FILE, 8)
  end
  ensure_auth(function()
    load_library(function()
      state.menu_open = true
      state.selected = 1
      mp.add_forced_key_binding("UP", "animenu_UP", function()
        state.selected = math.max(1, state.selected - 1); render_menu()
      end, { repeatable = true })
      mp.add_forced_key_binding("DOWN", "animenu_DOWN", function()
        state.selected = math.min(#state.series, state.selected + 1); render_menu()
      end, { repeatable = true })
      mp.add_forced_key_binding("ENTER", "animenu_ENTER", play_selected)
      mp.add_forced_key_binding("d", "animenu_d", function()
        local s = state.series[state.selected]
        if s then close_menu(); open_rating(s.title, "dropped", reopen_menu_if_idle) end
      end)
      mp.add_forced_key_binding("r", "animenu_r", function()
        local s = state.series[state.selected]
        if s then close_menu(); open_rating(s.title, "finished", reopen_menu_if_idle) end
      end)
      mp.add_forced_key_binding("q", "animenu_q", close_menu)
      mp.add_forced_key_binding("ESC", "animenu_ESC", close_menu)
      render_menu()
      flush_retry_queue()
    end)
  end)
end

-- ---------- playback tracking ----------

local function current_queue_path()
  -- match playing URL back to the queued path by filename
  local playing = mp.get_property("path") or ""
  local playing_name = playing:match("([^/]+)$") or playing
  playing_name = playing_name:gsub("%%(%x%x)", function(h)
    return string.char(tonumber(h, 16))
  end)
  for _, path in ipairs(state.queue) do
    if path:match("([^/]+)$") == playing_name then return path end
  end
  return nil
end

mp.observe_property("percent-pos", "number", function(_, pct)
  if not pct then return end
  local path = current_queue_path()
  if not path or state.marked[path] then return end
  if pct >= WATCHED_THRESHOLD * 100 then
    state.marked[path] = true
    mcp_call_queued("anime_mark", { path = path, status = "watched" })
    osd("✓ watched", 1)
  end
end)

mp.register_event("end-file", function(ev)
  state.last_end_reason = ev.reason
end)

mp.add_key_binding("n", "anime-mark-manual", function()
  local path = current_queue_path()
  if path then
    state.marked[path] = true
    mcp_call_queued("anime_mark", { path = path, status = "manual" })
  end
  mp.commandv("playlist-next", "force")
end)

-- ---------- entry ----------

mp.add_key_binding("a", "anime-menu", open_menu)

mp.observe_property("idle-active", "bool", function(_, idle)
  if not idle then return end
  -- queued series ran out naturally
  if state.current_series and state.last_end_reason == "eof" then
    local series = state.current_series
    local is_final = state.current_series_is_final
    state.current_series = nil
    if is_final then
      -- Rate the finale, then fall back to the menu once the dialog closes.
      return open_rating(series, "finished", reopen_menu_if_idle)
    end
    -- Only caught up on episodes released so far — not actually done with
    -- the series, so don't nag for a rating every time a new episode airs.
    open_menu()
    osd("caught up on " .. series .. " — press r in the menu to rate it", 4)
    return
  end
  if not state.menu_open and not state.rating_open then open_menu() end
end)

config = read_json(CONFIG_FILE) or {}
if not config.mcp_url then
  mp.msg.warn("anime.lua: no config at " .. CONFIG_FILE .. " — menu disabled")
else
  config.files_url = config.files_url or "https://media.ahiru.pl/files/Unsorted/"
end
