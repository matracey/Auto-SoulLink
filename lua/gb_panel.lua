-- lua/gb_panel.lua — the GB in-game panel, shared by the Gen 1 and Gen 2 binders (P4.1d).
--
-- The cartridge's companion patch owns the SCREEN: it whites out, draws a fallback, sets the
-- panel state to AWAIT and polls for STAGED. We paint 18 rows x 20 tiles straight into the
-- tile map while the screen is white (plus the CGB attribute map when the binder has one),
-- publish the page count, and hand it back. The same mailbox carries the SFX request byte.
--
-- Every game fact is an explicit binder input (docs/shared-gb-panel.md); the mailbox ABI
-- offsets below are patch/gb/slink_abi.inc's (tests/unit/test_gb_panel.py pins the equality).
-- Gen 3 does not bind this: it speaks EWRAM opcodes through lua/mailbox.lua.
--
-- All writes go through the injected writes.lua instance under its own "panel" window, so a
-- mailbox byte that happens to read 1 on an UNPATCHED cartridge cannot paint over the map:
-- present() asks the capability bits first, and only an OBSERVED transition into AWAIT arms.
local G = {}

G.BEACON = { 0x53, 0x4C, 0x4E, 0x4B }       -- 'SLNK', rewritten every VBlank
G.OFF_ABI, G.OFF_SFX, G.OFF_CAPS, G.OFF_STATE, G.OFF_PAGE, G.OFF_PAGES = 4, 7, 8, 9, 10, 11
G.CAP_SFX, G.CAP_PANEL, G.CAP_SFX_NOTIFY = 0x01, 0x02, 0x04
G.CLOSED, G.AWAIT, G.STAGED = 0, 1, 2
-- What the request byte may carry. The ROM resolves the code to a sound id, holds it through
-- fades and busy channels, and zeroes the byte when it plays. Anything else is consumed unplayed.
G.SFX_SUCCESS, G.SFX_FAILURE, G.SFX_BOO, G.SFX_NOTIFY = 1, 2, 3, 4
G.SFX_QUEUE_MAX = 4                         -- a burst beyond this drops the OLDEST: the newest news wins
G.COLS, G.ROWS = 20, 18
G.MAX_PAGES = 8                             -- 144 rows; the patch pages with A, not forever
G.BLANK = 0x7F                              -- the space both GB charsets pad menu rows with
-- An optional TEXT payload (spec.text): a cartridge that cannot take a graphics lease renders the
-- page itself, so the host stages glyph codes instead of tiles (Polished has no WaitBGMap2 --
-- docs/polished/PANEL.md E7). `stride` is one line plus the game's string terminator, so the ROM
-- reads SLINK_PANEL_LINES fixed-stride lines and never needs a line-break code from the host.

local BEACON = G.BEACON
local CAP_SFX, CAP_PANEL = G.CAP_SFX, G.CAP_PANEL
local AWAIT, STAGED = G.AWAIT, G.STAGED
local SFX_SUCCESS, SFX_FAILURE, SFX_BOO, SFX_NOTIFY = G.SFX_SUCCESS, G.SFX_FAILURE, G.SFX_BOO, G.SFX_NOTIFY
local SFX_QUEUE_MAX = G.SFX_QUEUE_MAX
local COLS, ROWS, MAX_PAGES, BLANK = G.COLS, G.ROWS, G.MAX_PAGES, G.BLANK
local TILES = COLS * ROWS                   -- 360, one whole page

--- spec (all required; a missing one asserts):
---   mailbox  number   base of the 'SLNK' mailbox
---   tilemap  number   wTileMap / wTilemap
---   attrmap  {base=number, fill=byte} | false   CGB attribute map painted with `fill`; false = DMG
---   charmap  callable(ch) -> tile id, for single ASCII characters
---   se_map   table    server play_sound id -> SFX code
---   deadline number   frames after an OBSERVED AWAIT transition within which we may paint
--- io needs read_u8/framecount; writes is a writes.lua instance; sanitize is hud.lua's.
function G.new(spec, io, writes, sanitize)
    assert(type(spec) == "table", "gb_panel spec required")
    local MAILBOX = assert(spec.mailbox, "gb_panel spec.mailbox required")
    local tilemap = assert(spec.tilemap, "gb_panel spec.tilemap required")
    assert(spec.attrmap ~= nil, "gb_panel spec.attrmap required ({base, fill} or false for DMG)")
    local attr = spec.attrmap or nil
    if attr then assert(attr.base and attr.fill, "gb_panel spec.attrmap needs base and fill") end
    local tile_for = assert(spec.charmap, "gb_panel spec.charmap required")
    local se_map = assert(spec.se_map, "gb_panel spec.se_map required")
    local DEADLINE = assert(spec.deadline, "gb_panel spec.deadline required")
    -- Presence, not type(...) == "function": under lupa the injected io/sanitize may be
    -- Python callables, which Lua sees as userdata.
    assert(type(io) == "table" and io.read_u8 and io.framecount,
           "injected io with read_u8/framecount required")
    assert(type(writes) == "table" and writes.arm and writes.disarm and writes.write_bytes,
           "writes.lua instance required")
    assert(sanitize, "injected sanitize required (hud.lua)")
    local PAGE_ROWS = spec.rows_per_page or ROWS
    if type(PAGE_ROWS) ~= "number" or PAGE_ROWS < 1 or PAGE_ROWS > ROWS or PAGE_ROWS % 1 ~= 0 then
        error("gb_panel spec.rows_per_page must be 1.." .. ROWS .. ", got " .. tostring(spec.rows_per_page))
    end
    -- Optional text payload. `base` is where the cartridge expects the staged page, `lines` how
    -- many fixed-stride lines it reads, `line_max` the glyphs per line and `terminator` the game's
    -- own string terminator. Nothing else about the handshake changes: same AWAIT/STAGED, same
    -- PAGES-then-STATE publication order. The host never needs a line-break code; the ROM's own
    -- script puts one between the <RAM> blocks.
    local text = spec.text
    if text ~= nil then
        for _, key in ipairs({"base", "stride", "lines", "line_max", "terminator"}) do
            if type(text[key]) ~= "number" or text[key] % 1 ~= 0 then
                error("gb_panel spec.text." .. key .. " must be an integer, got " .. tostring(text[key]))
            end
        end
        if text.lines < 1 or text.lines > PAGE_ROWS or text.line_max > COLS
           or text.stride ~= text.line_max + 1 then
            error("gb_panel spec.text geometry does not fit the page")
        end
    end

    local ABI, CAPS, STATE = MAILBOX + G.OFF_ABI, MAILBOX + G.OFF_CAPS, MAILBOX + G.OFF_STATE
    local PAGE, PAGES, SFX = MAILBOX + G.OFF_PAGE, MAILBOX + G.OFF_PAGES, MAILBOX + G.OFF_SFX
    local attr_page
    if attr and not text then
        attr_page = {}
        for i = 1, TILES do attr_page[i] = attr.fill end
    end

    -- The only bytes this window may ever touch. write_bytes is expected to check the FULL
    -- interval; the predicate takes an optional length so it answers for either convention.
    local function allow(addr, n)
        if type(addr) ~= "number" then return false end
        local last = addr + (n or 1) - 1
        if text then
            if addr >= text.base and last < text.base + text.lines * text.stride then return true end
        else
            if addr >= tilemap and last < tilemap + TILES then return true end
            if attr and addr >= attr.base and last < attr.base + TILES then return true end
        end
        return (addr == STATE or addr == PAGES or addr == SFX) and last == addr
    end

    -- `mailbox` is read by the harness (duo VBlank-counter probe) so it never hard-codes vanilla's.
    local self = { pages = nil, tiles = nil, last_state = nil, await_frame = nil, armed = false, mailbox = MAILBOX,
                   sfx_queue = {} }

    local function u8(addr)
        local v = io.read_u8(addr)
        if type(v) ~= "number" then return nil end
        return math.floor(v) % 256
    end

    --- Is `bit` set in a readable caps byte? ($FF reads as open bus / erased, never a grant.)
    function self:caps_has(bit)
        local caps = u8(CAPS)
        return caps ~= nil and caps ~= 0xFF and (caps & bit) ~= 0
    end

    local function beacon()
        for i = 1, 4 do
            if u8(MAILBOX + i - 1) ~= BEACON[i] then return false end
        end
        return true
    end

    --- Does THIS cartridge have the panel? Asked of the capability bits rather than inferred
    --- from the ABI number, because a build may ship one feature without the other.
    function self:present() return beacon() and self:caps_has(CAP_PANEL) end

    function self:abi() return u8(ABI) or 0 end

    --- The SFX code for a server `play_sound` id, or nil for ids this game has no sound for.
    function self:sfx_code_for(sound_id) return se_map[sound_id] end

    --- Does THIS cartridge play sound? Same beacon, the SFX bit of the same caps byte.
    function self:sfx_present() return beacon() and self:caps_has(CAP_SFX) end

    --- Write one code to the request byte, through the armed window like every other
    --- mailbox write, so the receipts log shows it. Only when the byte reads 0: the ROM
    --- clears it when it plays, and overwriting a request it is still HOLDING (fade, busy
    --- channel) would lose that one. Returns true when written.
    local function post_sfx(code)
        if u8(SFX) ~= 0 then return false end
        writes:arm("panel", allow)
        local ok, err = pcall(function() writes:write_bytes(SFX, { code }) end)
        writes:disarm()
        if not ok then error(err, 0) end
        return true
    end

    --- Ask the cartridge to play a notification. `code` is one of G.SFX_*; a server id is
    --- mapped through sfx_code_for by the caller. Posted now if the request byte is free,
    --- else queued and drained by service().
    function self:request_sfx(code)
        if code ~= SFX_SUCCESS and code ~= SFX_FAILURE and code ~= SFX_BOO and code ~= SFX_NOTIFY then return false end
        if not self:sfx_present() then return false end
        if #self.sfx_queue == 0 and post_sfx(code) then return true end
        if #self.sfx_queue >= SFX_QUEUE_MAX then table.remove(self.sfx_queue, 1) end
        self.sfx_queue[#self.sfx_queue + 1] = code
        return true
    end

    function self:awaiting() return u8(STATE) == AWAIT end

    --- Forget the held rows and any queued sounds (WRAM clear / rejected hello — the caller
    --- owns that policy).
    function self:clear() self.pages, self.tiles, self.text_rows, self.sfx_queue = nil, nil, nil, {} end

    --- Drop queued sounds only: the run switched native sounds off, so nothing accepted
    --- under the old setting may still post after it.
    function self:clear_sfx() self.sfx_queue = {} end

    --- Validate, sanitize and PRE-RENDER every page. Nothing is computed inside the armed
    --- window, and a rejected payload leaves the previous one untouched rather than half-held.
    --- ponytail: text -> sanitized rows stays here until a Gen 3 card asks for it.
    function self:hold(rows)
        if type(rows) ~= "table" then return nil, "panel rows must be a list" end
        local n = #rows
        if n > PAGE_ROWS * MAX_PAGES then
            return nil, "panel rows exceed " .. (PAGE_ROWS * MAX_PAGES) .. " (" .. MAX_PAGES .. " pages)"
        end
        local clean = {}
        for i = 1, n do
            if type(rows[i]) ~= "string" then return nil, "panel row " .. i .. " is not a string" end
            local s = sanitize(rows[i])
            if type(s) ~= "string" then return nil, "panel row " .. i .. " did not sanitize to a string" end
            clean[i] = s
        end
        if n == 0 then self:clear() return true end   -- nothing to show; never open on blanks

        local pages = math.ceil(n / PAGE_ROWS)
        local tiles, text_rows = {}, text and {} or nil
        for p = 1, pages do
            local page = {}
            for r = 0, PAGE_ROWS - 1 do
                local row_text = clean[(p - 1) * PAGE_ROWS + r + 1] or ""
                for c = 1, COLS do
                    local ch = row_text:sub(c, c)               -- pad AND truncate to 20
                    page[r * COLS + c] = ch == "" and BLANK or tile_for(ch)
                end
            end
            tiles[p] = page
            if text then
                local rows = {}
                for line = 0, text.lines - 1 do
                    local bytes = {}
                    for c = 1, text.line_max do bytes[c] = page[line * COLS + c] end
                    bytes[text.line_max + 1] = text.terminator
                    rows[line + 1] = bytes
                end
                text_rows[p] = rows
            end
        end
        self.pages, self.tiles, self.text_rows = pages, tiles, text_rows
        return true
    end

    --- Hand the page the cartridge asked for back and publish it. Requires an armed window.
    --- The tile and text payloads publish the same three facts (page count, then STAGED last) in
    --- the same order; only the bytes before them differ.
    function self:stage()
        assert(self.tiles, "panel has no held rows")
        local page = u8(PAGE) or 0
        if page >= self.pages then page = self.pages - 1 end   -- never index past the end
        if text then
            local rows = self.text_rows[page + 1]
            for line = 0, text.lines - 1 do
                writes:write_bytes(text.base + line * text.stride, rows[line + 1])
            end
        else
            writes:write_bytes(tilemap, self.tiles[page + 1])
            if attr_page then writes:write_bytes(attr.base, attr_page) end
        end
        writes:write_bytes(PAGES, { math.min(self.pages, 255) })
        writes:write_bytes(STATE, { STAGED })                  -- published last
        return page
    end

    local function tick()
        local frame = io.framecount()
        -- One queued sound per frame, only once the ROM has consumed the previous request.
        if #self.sfx_queue > 0 then
            if not self:sfx_present() then
                self.sfx_queue = {}                       -- the cartridge changed under us
            elseif post_sfx(self.sfx_queue[1]) then
                table.remove(self.sfx_queue, 1)
            end
        end
        if not self:present() then
            -- Unpatched or gone: the state byte is ordinary WRAM, so nothing it says is a transition.
            self.last_state, self.await_frame = nil, nil
            return
        end
        local state, prev = u8(STATE), self.last_state
        self.last_state = state
        if state ~= AWAIT then
            self.await_frame = nil
        elseif prev ~= nil and prev ~= AWAIT then
            -- CLOSED->AWAIT on open, STAGED->AWAIT on a page turn. Each gets its own fresh
            -- deadline; an AWAIT that merely PERSISTS keeps the old one, and an AWAIT that was
            -- already there at first sight (prev == nil) is of unknown age, so that open shows
            -- the fallback and we never paint it.
            self.await_frame = frame
        end
        if state ~= AWAIT or not self.tiles or not self.await_frame then return end
        if type(frame) ~= "number" or frame - self.await_frame > DEADLINE then return end

        self.armed = true
        writes:arm("panel", allow)
        local ok, err = pcall(function() return self:stage() end)
        writes:disarm()
        self.armed = false
        if not ok then error(err, 0) end
        -- WE wrote STAGED, so record it rather than waiting to observe it: a client that
        -- misses frames must still see the patch's next AWAIT as a page turn, not a persist.
        self.last_state = STAGED
    end

    --- Called every frame, before anything else arms a window: the player may be sitting in
    --- the START menu with the overworld checkpoint long behind them.
    function self:service()
        local ok, err = pcall(tick)
        if not ok then
            -- Only if tick died with the window still open; the normal path closed it itself
            -- and a second disarm would clobber whatever the caller arms next.
            if self.armed then
                pcall(function() writes:disarm() end)
                self.armed = false
            end
            return nil, tostring(err)
        end
        return true
    end

    self.allow = allow
    return self
end

return G
