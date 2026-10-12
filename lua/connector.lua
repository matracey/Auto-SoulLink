--[[
  lua/connector.lua — Non-blocking LuaSocket TCP client for SLink.

  Implements Archipelago's lag-free technique: LuaSocket with settimeout(0)
  so send/receive never block BizHawk's emulation thread.

  Protocol: newline-delimited JSON.
    Lua → Python : {"event":"...", "player":"a", "seq":N, ...}\n
    Python → Lua : {"commands":[{"cmd":"..."}]}\n

  Requirements:
    lua/socket.lua                        (LuaSocket wrapper — included)
    lua/x64/socket-windows-5-4.dll        (Lua 5.4 / BizHawk 2.9)
    lua/x64/socket-windows-5-1.dll        (Lua 5.1 / older BizHawk)
    → Copy from: <Archipelago>/data/lua/x64/

  API:
    local C = require("connector")
    C.init(host, port)    -- call once at startup; attempts first connect
    C.send(json_str)      -- queue a message to send (non-blocking)
    C.receive()           -- return next complete response line, or nil
    C.pump()              -- call once per frame: flushes send queue, fills receive queue
    C.connected()         -- returns true if socket is open
    C.disconnect()        -- close the socket (called automatically on error)
--]]

package.loaded["socket"] = nil          -- always reload fresh
local socket = require("socket")

local M = {}

local function _safe_close(sock)
    if sock then pcall(function() sock:close() end) end
end

-- ── Private state ─────────────────────────────────────────────────────────────
local _host, _port
local _sock           = nil
local _connected      = false
local _send_queue     = {}      -- {string} lines waiting to be sent
local _line_queue     = {}      -- {string} complete received lines ready to read
-- PARTIAL I/O STATE. With settimeout(0) LuaSocket may move only part of a line and reports
-- how far it got in its THIRD return value. Both directions used to discard that: a partial
-- send left the whole line queued and re-sent the bytes the peer already had, and a partial
-- receive dropped the bytes LuaSocket had already taken off the socket. Neither ever fired,
-- because a small JSON line crosses loopback in one piece -- but "small" was an assumption,
-- and the ROM content payload is kilobytes.
local _send_offset    = 0       -- bytes of _send_queue[1] the peer has already taken
local _recv_buf       = ""      -- bytes of an incomplete inbound line, kept across frames
local _drop_until_newline = false  -- true while skipping the tail of an over-long line
local MAX_LINE        = 4 * 1024 * 1024   -- matches the server's own per-line cap
local _fail_logged = false   -- one connect-failure message per outage
local _reconnect_cd   = 0       -- frames remaining before next reconnect attempt
local _connect_wait   = 0       -- frames left for an in-progress OS connect

local RECONNECT_FRAMES = 30     -- ~0.5 s at 60 fps (initial retry interval)
local RECONNECT_MAX   = 1800   -- ~30 s cap (exponential backoff)
local CONNECT_TIMEOUT = 300    -- ~5 s for a WAN handshake before retrying
local _reconnect_step = RECONNECT_FRAMES  -- current backoff interval

-- ── Internal: connect attempt ─────────────────────────────────────────────────
-- Non-blocking connect: settimeout(0) so BizHawk never stalls.
-- On loopback this connects in < 1 ms; over LAN the OS returns EINPROGRESS
-- and pump() waits until a protocol-safe newline can actually be written.
local function _do_connect()
    _safe_close(_sock)
    _sock = nil
    _connected = false

    local s, err = socket.socket.tcp4()
    if not s then return false, err end

    s:settimeout(0)                     -- fully non-blocking (including connect)
    local ok, cerr = s:connect(_host, _port)

    if ok then
        -- Immediate success (common on loopback when server is running)
        _sock = s
        _connected = true
        _reconnect_step = RECONNECT_FRAMES  -- reset backoff on success
        return true
    elseif cerr == "timeout" or cerr == "Operation already in progress" then
        -- Connection in progress — store socket, we'll check on next pump()
        _sock = s
        _connected = false
        _connect_wait = CONNECT_TIMEOUT
        return false, "connecting"
    else
        _safe_close(s)
        return false, cerr
    end
end

-- Check if a pending non-blocking connect has completed.
local function _check_pending_connect()
    if not _sock or _connected then return end
    -- Neither a zero-length send nor getpeername() proves completion on Winsock:
    -- both can succeed before the socket is writable. A single newline is safe
    -- protocol traffic (the server ignores empty lines) and can only leave once
    -- the OS has completed the handshake.
    local bytes, err, last = _sock:send("\n")
    if (type(bytes) == "number" and bytes >= 1)
       or (type(last) == "number" and last >= 1) then
        _connected = true
        _connect_wait = 0
        _reconnect_step = RECONNECT_FRAMES
        console.log(string.format("[SLink] TCP connected to %s:%d", _host, _port))
        return
    end

    if err and err ~= "timeout" and err ~= "Socket is not connected"
       and err ~= "Operation already in progress" then
        _safe_close(_sock)
        _sock = nil
        _connect_wait = 0
        return
    end
    _connect_wait = _connect_wait - 1
    if _connect_wait <= 0 then
        _safe_close(_sock)
        _sock = nil
    end
end

-- ── Public API ────────────────────────────────────────────────────────────────

--- Call once at script startup. Attempts an initial connection (non-blocking).
function M.init(host, port)
    _host, _port = host, port
    local ok, err = _do_connect()
    if ok then
        console.log(string.format("[SLink] TCP connected to %s:%d", host, port))
    elseif err == "connecting" then
        console.log(string.format(
            "[SLink] TCP connecting to %s:%d (non-blocking)…", host, port))
    else
        console.log(string.format(
            "[SLink] TCP connect failed (%s:%d): %s — will retry every ~2 s",
            host, port, tostring(err)))
    end
end

--- Returns true while the socket is open and healthy.
function M.connected()
    return _connected
end

--- Queue a JSON string to be sent on the next pump().
--- The caller must NOT append \n — pump() does that.
function M.send(json_str)
    table.insert(_send_queue, json_str)
end

--- Return the next complete received line (without \n), or nil if none ready.
function M.receive()
    if #_line_queue == 0 then return nil end
    return table.remove(_line_queue, 1)
end

--- Call once per frame. Drives:
---   1. Reconnect logic when disconnected.
---   2. Send all queued lines (non-blocking; stops on timeout/error).
---   3. Receive all complete lines currently buffered (non-blocking).
function M.pump()
    -- ── Check pending non-blocking connect ────────────────────────────────────
    if _sock and not _connected then
        _check_pending_connect()
        if not _connected then return end  -- still connecting or failed
    end

    -- ── Reconnect (with exponential backoff) ──────────────────────────────────
    if not _connected then
        _reconnect_cd = _reconnect_cd - 1
        if _reconnect_cd <= 0 then
            _reconnect_cd = _reconnect_step
            -- Exponential backoff: double interval each failure, cap at RECONNECT_MAX
            _reconnect_step = math.min(_reconnect_step * 2, RECONNECT_MAX)
            local ok, err = _do_connect()
            if ok then
                console.log(string.format("[SLink] Reconnected to %s:%d", _host, _port))
                _fail_logged = false
            elseif err and err ~= "connecting" then
                -- Say WHY, once per outage. Silence here is the worst possible first-run
                -- experience: the emulator looks fine, the script looks fine, and nothing
                -- happens. Logged once rather than every backoff tick so a long outage does
                -- not bury the console.
                if not _fail_logged then
                    _fail_logged = true
                    console.log(string.format(
                        "[SLink] Cannot reach the server at %s:%d (%s). Is it running? " ..
                        "Start it with:  python -m server.server", _host, _port, tostring(err)))
                end
            end
            -- If err == "connecting", _check_pending_connect will handle it next frame
        end
        return
    end

    -- ── Send ──────────────────────────────────────────────────────────────────
    -- Sends lines from the queue one at a time.
    -- settimeout(0) means send() returns immediately if the OS buffer is full
    -- having written only PART of the line. That is handled rather than assumed
    -- away: the comment here used to claim "a single JSON line is never > 64 KB",
    -- which stopped being true the moment a ROM content payload was sent.
    while #_send_queue > 0 do
        local line = _send_queue[1] .. "\n"
        -- Resume at the first byte the peer has NOT taken. send(data, i) is 1-based and
        -- both success and timeout report the index of the last byte written, so the
        -- offset survives however many frames the OS buffer stays full.
        local bytes, err, lastindex = _sock:send(line, _send_offset + 1)
        if bytes then
            table.remove(_send_queue, 1)    -- sent successfully
            _send_offset = 0
        elseif err == "timeout" then
            _send_offset = lastindex or _send_offset
            break                           -- OS buffer full; resume next frame
        else
            -- "closed" or other error
            console.log("[SLink] TCP send error: " .. tostring(err) .. " — disconnecting")
            M.disconnect()
            return
        end
    end

    -- ── Receive ───────────────────────────────────────────────────────────────
    -- receive("*l") reads one complete line (up to \n, not including it).
    -- With settimeout(0) it returns nil,"timeout" immediately if no full line.
    while true do
        local line, err, partial = _sock:receive("*l")
        if line then
            if _drop_until_newline then
                -- This `line` is the TAIL of the oversized line we abandoned, so its
                -- terminator ends the garbage rather than starting anything. Without this
                -- the cap bounded memory and then handed the tail to the parser as a
                -- perfectly ordinary line -- megabytes of it -- which only looked harmless
                -- because parse_command_list is a pattern scraper that returns {} instead
                -- of raising. Resync properly: skip to the next newline and start clean.
                _drop_until_newline = false
            else
                -- Whatever earlier frames collected belongs in front of this tail.
                table.insert(_line_queue, _recv_buf .. line)
            end
            _recv_buf = ""
        elseif err == "timeout" then
            -- KEEP THE PARTIAL. LuaSocket has already consumed these bytes from the
            -- socket; dropping them does not re-read them later, it loses them, and the
            -- next complete line then arrives with its head missing.
            if partial and #partial > 0 then
                if _drop_until_newline then
                    -- Still inside the abandoned line; keep throwing bytes away.
                    partial = nil
                elseif #_recv_buf + #partial > MAX_LINE then
                    console.log("[SLink] inbound line exceeded " .. MAX_LINE
                                .. " bytes — discarding it and resyncing at the next "
                                .. "newline")
                    _recv_buf = ""
                    _drop_until_newline = true
                elseif partial then
                    _recv_buf = _recv_buf .. partial
                end
            end
            break                           -- no more complete lines right now
        else
            -- "closed" or other error
            console.log("[SLink] TCP receive error: " .. tostring(err) .. " — disconnecting")
            M.disconnect()
            break
        end
    end
end

--- Close the socket and schedule a reconnect attempt.
function M.disconnect()
    _safe_close(_sock)
    _sock = nil
    _connected = false
    -- A new socket starts a new byte stream: resuming a half-written line or prepending a
    -- half-read one would corrupt the first message of the next session.
    _send_offset = 0
    _recv_buf = ""
    _line_queue = {}
    _drop_until_newline = false
    _connect_wait = 0
    -- An event queued the frame the socket died must not outlive the connection: every
    -- client re-sends hello first thing on (re)connect (e.g. lua/gen1/client.lua frame_end,
    -- archive/gen3-old-client:lua/clients/gen3_frlge_client.lua), and server.py refuses any event that arrives
    -- before hello on a connection (dropped with a `noop` + WARNING) -- so a queued event
    -- that survived here would just get thrown away one hop later, out of order and with
    -- log noise. A fresh connection starts clean instead.
    _send_queue = {}
    _reconnect_cd = RECONNECT_FRAMES      -- first retry after ~0.5 s
    _reconnect_step = RECONNECT_FRAMES    -- reset backoff
end

return M