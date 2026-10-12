-- lua/hud.lua — Shared BizHawk HUD overlay module.
-- Provides message queue, center-screen prompts, and game-over banner.
-- Usage:
--   local HUD = dofile(SLINK_ROOT .. "lua/hud.lua")
--   HUD.init({screen_w=240, screen_h=160})  -- GBA
--   HUD.show("Hello!", 255, 255, 0, 180)
--   HUD.render()  -- call every frame

local fmt = string.format
local remove = table.remove

local H = {}

-- Glyph sanitization -----------------------------------------------------------
-- The HUD draws via gui.drawText (GDI+, .NET FontFamily; SLink passes "Courier
-- New"). The font has the glyphs; the problem is that BizHawk's Lua marshals
-- strings byte-by-byte and mangles multibyte UTF-8, so anything >= U+0080 (star,
-- gender signs, em dash, ellipsis, accented letters) reaches the overlay as a
-- tofu box / mojibake. Long-standing limitation -- see TASEmulators/BizHawk
-- issues #190 (kana/Unicode) and #3235 (shape chars not marshalled through Lua).
--
-- What BizHawk renders, by API (test notes):
--   * gui.drawText  - GDI+ real fonts. RELIABLE range: printable ASCII
--                     U+0020-U+007E. Single-byte Latin-1 (U+00A0-U+00FF) and
--                     other Courier New glyphs MAY render but are font/version
--                     dependent -- VERIFY in BizHawk before adding one to the
--                     keep-list below; do not assume from the font alone.
--   * gui.text      - fast, fixed style, no font control. ASCII only in practice.
--   * gui.pixelText - bitmap fonts "fceux"/"gens" only; ASCII only.
--
-- So we fold the symbols we actually use down to readable ASCII at this single
-- choke point, then strip any remaining high bytes so nothing un-drawable slips
-- through. Keys are raw UTF-8 byte sequences written with decimal escapes, which
-- parse on both Lua 5.1 (older BizHawk) and 5.4 (BizHawk 2.9+) -- unlike \u{}
-- (5.3+ only).
local GLYPH_MAP = {
    ["\226\152\133"] = "*",    -- U+2605 black star
    ["\226\152\134"] = "*",    -- U+2606 white star
    ["\226\156\168"] = "*",    -- U+2728 sparkles
    ["\226\156\166"] = "*",    -- U+2726 black four-pointed star
    ["\226\152\160"] = "X",    -- U+2620 skull and crossbones
    ["\226\154\160"] = "!",    -- U+26A0 warning sign
    ["\226\153\130"] = "M",    -- U+2642 male sign
    ["\226\153\128"] = "F",    -- U+2640 female sign
    ["\226\134\148"] = "<>",   -- U+2194 left-right arrow
    ["\226\134\146"] = ">",    -- U+2192 rightwards arrow
    ["\226\134\144"] = "<",    -- U+2190 leftwards arrow
    ["\226\134\147"] = "v",    -- U+2193 down arrow (deposited)
    ["\226\134\145"] = "^",    -- U+2191 up arrow (retrieved)
    ["\226\128\160"] = "+",    -- U+2020 dagger (memorialized)
    ["\226\128\148"] = "-",    -- U+2014 em dash
    ["\226\128\147"] = "-",    -- U+2013 en dash
    ["\226\128\166"] = "...",  -- U+2026 horizontal ellipsis
    ["\226\128\152"] = "'",    -- U+2018 left single quote
    ["\226\128\153"] = "'",    -- U+2019 right single quote
    ["\226\128\156"] = '"',    -- U+201C left double quote
    ["\226\128\157"] = '"',    -- U+201D right double quote
    ["\195\169"]     = "e",    -- U+00E9 e-acute (Pokemon, Flabebe)
    ["\195\168"]     = "e",    -- U+00E8 e-grave
    ["\195\161"]     = "a",    -- U+00E1 a-acute
    ["\195\173"]     = "i",    -- U+00ED i-acute
    ["\195\179"]     = "o",    -- U+00F3 o-acute
    ["\195\186"]     = "u",    -- U+00FA u-acute
    ["\195\177"]     = "n",    -- U+00F1 n-tilde
    ["\195\151"]     = "x",    -- U+00D7 multiplication sign (FR charmap 0xB9 in nicknames)
}

-- Fold known glyphs to ASCII, then drop any remaining bytes >= 0x80.
local function sanitize(s)
    -- never hand gui.drawText a non-string; nil passes through unchanged (the and/or form
    -- returned the literal string "nil" — classic falsy-branch footgun)
    if type(s) ~= "string" then
        if s == nil then return nil end
        return tostring(s)
    end
    if s == "" then return s end
    for utf8_seq, ascii in pairs(GLYPH_MAP) do
        s = s:gsub(utf8_seq, ascii)
    end
    s = s:gsub("[\128-\255]", "")
    return s
end

-- The same fold the in-game panel needs (lua/gen1/panel.lua): the cartridge tilemap has no
-- glyph for a byte >= 0x80 either, and two spellings of "fold to ASCII" would drift.
H.sanitize = sanitize

-- ── Configuration (set via init) ────────────────────────────────────────────
local cfg = {
    screen_w   = 240,   -- screen width
    screen_h   = 160,   -- screen height
    hud_x      = 3,     -- HUD bar left edge
    hud_y      = 146,   -- HUD bar top (near bottom of screen)
    hud_right  = 237,   -- HUD bar right edge
    prompt_y   = 44,    -- center prompt Y position
    prompt_h   = 14,    -- center prompt height
    gameover_y = 60,    -- game-over banner top
    font_size  = 10,    -- text font size
    -- Empirical Courier New Bold advance per font size, used by the word wrap in
    -- H.show / H.prompt to keep text from bleeding past the dark backdrop.
    -- Numbers are intentionally GENEROUS — wrapping a little early is invisible,
    -- wrapping late puts pixels outside the box. Real GDI+ Courier Bold at 10pt
    -- advances ~6px; at 8pt ~5px. Per-line budgets at these widths:
    --   GBC (font_size=8,  char_width=5): 156 / 5 = 31 chars
    --   GBA (font_size=10, char_width=6): 234 / 6 = 39 chars
    --   NDS (font_size=10, char_width=6): 250 / 6 = 41 chars
    char_width = 6,
    -- BizHawk bitmap font for gui.pixelText, or false for GDI+ Courier. A 144px screen
    -- defaults to "fceux": 8pt Courier drawn at 160x144 then scaled up is a smear, while
    -- fceux is 5x7 glyphs on a 6px advance (fceux.ttf in BizHawk.Client.Common.dll,
    -- 128 units/px), the size of the game's own font and one screen pixel per font pixel.
    pixel_font = nil,
}

-- Word wrap -------------------------------------------------------------------
-- Notifications used to be truncated to one line with "...", which threw away
-- exactly the tail that names the mon ("[x] Dead in par..."). They are wrapped
-- instead: up to MAX_*_LINES lines of max_chars, and the bar/banner grows to fit.
-- "..." only survives as a last resort when even the full line budget is short,
-- and that case is logged so a lane receipt names the message that overflowed.
local MAX_HUD_LINES    = 3   -- bottom bar: 3 lines still clear the Gen 1 144px screen
local MAX_PROMPT_LINES = 4

-- Split `text` into at most `max_lines` lines of `max_chars`, breaking on spaces;
-- a single token longer than max_chars is hard-broken. Returns a list of lines
-- (always at least one). Runs once per message, at H.show / H.prompt time.
local function wrap(text, max_chars, max_lines)
    if type(text) ~= "string" then return { text == nil and "" or tostring(text) } end
    if max_chars < 1 then max_chars = 1 end
    local lines, cur = {}, ""
    local function push() lines[#lines + 1] = cur; cur = "" end
    -- "\n" forces a break: each newline-separated part wraps on its own
    for tok in text:gsub("\n", " \1 "):gmatch("%S+") do
      if tok == "\1" then
        if cur ~= "" then push() end
      else
        local word = tok                    -- a for-loop variable is const in Lua 5.5
        while #word > max_chars do          -- hard-break an over-long token
            if cur ~= "" then push() end
            lines[#lines + 1] = word:sub(1, max_chars)
            word = word:sub(max_chars + 1)
        end
        if cur == "" then
            cur = word
        elseif #cur + 1 + #word <= max_chars then
            cur = cur .. " " .. word
        else
            push()
            cur = word
        end
      end
    end
    if cur ~= "" then push() end
    if #lines == 0 then lines[1] = "" end
    if #lines > max_lines then
        local last = lines[max_lines]
        for i = #lines, max_lines + 1, -1 do lines[i] = nil end
        lines[max_lines] = last:sub(1, math.max(0, max_chars - 3)) .. "..."
        if type(console) == "table" and console.log ~= nil then
            console.log(fmt("[SLink-HUD] message exceeds %dx%d, ellipsized: %s",
                            max_lines, max_chars, text))
        end
    end
    return lines
end

-- Wrap `text` to the bottom HUD bar at the current font.
local function wrap_hud(text)
    return wrap(text, math.floor((cfg.hud_right - cfg.hud_x) / cfg.char_width), MAX_HUD_LINES)
end

-- Same as wrap_hud but for the center prompt (full screen width minus 8px).
local function wrap_prompt(text)
    return wrap(text, math.floor((cfg.screen_w - 8) / cfg.char_width), MAX_PROMPT_LINES)
end

local function wrap_prompt_n(text, max_lines)
    return wrap(text, math.floor((cfg.screen_w - 8) / cfg.char_width), max_lines)
end

function H.init(opts)
    H.clear()
    if not opts then return end
    for k, v in pairs(opts) do cfg[k] = v end
    -- Derive defaults if not explicitly set. Small screens (GB/GBC, 144 px high)
    -- take the 8/5 font so the bottom bar fits; the bar's top is derived from the
    -- screen height so it never lands below the screen (the GBA default 146 sat
    -- two pixels under a 144-px screen: LANE-BOOT2 found the Gen 1 HUD invisible).
    -- GB (144) and GBA (160) both draw the fceux pixel font at the GB metrics (owner
    -- 2026-09-24: Gen 3 notices look like Gen 1/2's); NDS keeps GDI+ Courier.
    if opts.pixel_font == nil then
        cfg.pixel_font = (cfg.screen_h <= 160) and "fceux" or false
    end
    if not opts.font_size then
        cfg.font_size = (cfg.pixel_font or cfg.screen_h <= 144) and 8 or 10
    end
    if not opts.char_width then
        -- fceux advances 6px; 8pt Courier Bold ~5px
        cfg.char_width = (cfg.pixel_font or cfg.screen_h > 144) and 6 or 5
    end
    if not opts.hud_y then
        cfg.hud_y = cfg.screen_h - cfg.font_size - 4
    end
    if not opts.hud_right then
        cfg.hud_right = cfg.screen_w - 3
    end
    if not opts.prompt_y then
        cfg.prompt_y = math.floor(cfg.screen_h * 0.275)
    end
    if not opts.prompt_h then
        cfg.prompt_h = cfg.font_size + 4
    end
    if not opts.gameover_y then
        cfg.gameover_y = math.floor(cfg.screen_h * 0.375)
    end
end

-- ── Surface clearing ────────────────────────────────────────────────────────
-- BizHawk's lua draw surface is PERSISTENT: whatever gui.drawBox/gui.drawText
-- painted stays on screen until something overdraws it or the surface is
-- cleared (EmuHawk 2.11, _docs_luacats/gui.d.lua:21-25 "clears all lua drawn
-- graphics from the screen"). Painting a fully transparent box over the old
-- area erases nothing, so an expired banner used to sit there forever.
-- Instead we wipe the whole surface at the top of every render and repaint
-- only what is still live.
-- Two layers, two clears: gui.clearGraphics wipes the box layer (drawBox), but the
-- text gui.drawText paints lives in BizHawk's text layer, which only gui.cleartext
-- wipes -- measured 2026-09-18 (HUD-SHOT): at frame 1000 with zero draw calls the box
-- was gone and 'Weedle linked!' was still on screen. 6c9f72c had dropped cleartext to
-- spare the diagnostic harnesses' gui.text (lua/tests/test_*_force_faint.lua,
-- test_force_explosion.lua, sprite_gallery.lua); they redraw their text every frame
-- from their own onframeend handlers, so the worst case alongside this HUD is a
-- one-frame flicker, not lost output. Guarded so the module loads outside BizHawk.
local function clear_surface()
    if type(gui) ~= "table" then return end
    -- BizHawk exposes its API as NLua delegates: type(gui.clearGraphics) is "userdata",
    -- never "function". A type()=="function" guard here was false on every real frame,
    -- so no clear ever ran in production (HUD-SHOT 2026-09-18: unguarded call -> text
    -- gone; guarded -> "Weedle linked!" still on screen at frame 1000 with zero draws).
    -- Test for presence, call through pcall, and let the lupa stub model the same shape.
    if gui.clearGraphics ~= nil then pcall(gui.clearGraphics) end
    if gui.cleartext ~= nil then pcall(gui.cleartext) end
end

-- Every HUD string goes through here: the pixel font when configured, else Courier.
-- pixelText has no size, so `size` (the bigger banner text) only applies to Courier.
local function draw_text(x, y, s, color, size)
    if cfg.pixel_font then
        gui.pixelText(x, y, s, color, 0x00000000, cfg.pixel_font)
    else
        gui.drawText(x, y, s, color, nil, size or cfg.font_size, "Courier New", "Bold")
    end
end

-- Left x that centres `s` between `left` and `right`. Exact for the pixel font (fixed
-- advance); the Courier char_width is an estimate, so Courier lines sit within a pixel or two.
local function centre_x(s, left, right, size)
    local scale = (cfg.pixel_font or not size) and 1 or size / cfg.font_size   -- pixelText has no size
    local w = #s * cfg.char_width * scale
    return math.max(left, math.floor((left + right - w) / 2))
end

-- ── HUD message bar (bottom of screen, queued) ──────────────────────────────
-- Message lifecycle -----------------------------------------------------------
-- Only the HEAD of a queue ages, so K queued messages used to occupy the screen
-- for K x their duration, one after another. A hello that re-memorializes a full
-- party, or a whiteout cascade, then reads as "the HUD never clears". Three
-- bounds, applied to both queues:
--   * identical text already queued REFRESHES it instead of stacking a copy;
--   * a backlog shortens each head remaining dwell to BURST_FRAMES;
--   * the queue is capped, dropping the OLDEST (the newest event is the one the
--     player needs to read).
-- Worst case on screen is therefore (MAX_QUEUE-1) * BURST_FRAMES + duration.
local MAX_QUEUE    = 4    -- hard ceiling per queue
local BURST_FRAMES = 90   -- 1.5s per message while a backlog exists

local function enqueue(q, text, lines, color, frames)
    for i = 1, #q do
        if q[i].text == text then
            q[i].frames = math.max(q[i].frames, frames)
            return
        end
    end
    q[#q + 1] = { text = text, lines = lines, color = color, frames = frames }
    while #q > MAX_QUEUE do remove(q, 1) end
end

-- Age the head of `q` by one frame and pop it when spent.
local function age(q)
    local m = q[1]
    if #q > 1 and m.frames > BURST_FRAMES then m.frames = BURST_FRAMES end
    m.frames = m.frames - 1
    if m.frames <= 0 then remove(q, 1) end
end

local hud_queue = {}

function H.show(text, r, g, b, duration_frames)
    text = sanitize(text)
    enqueue(hud_queue, text, wrap_hud(text),
            fmt("#%02X%02X%02X", r or 255, g or 255, b or 255),
            duration_frames or 240)
end

local function render_hud()
    if #hud_queue == 0 then return end
    local msg = hud_queue[1]
    -- cfg.hud_y is the BOTTOM line: extra lines grow UPWARD so a 3-line bar
    -- still ends on the same pixel row of the 144px Gen 1 screen.
    local n, line_h = #msg.lines, cfg.font_size + 2
    gui.drawBox(cfg.hud_x - 2, cfg.hud_y - 2 - (n - 1) * line_h,
                cfg.hud_right, cfg.hud_y + cfg.font_size,
                0xFF000000, 0xBB000000)
    for i = 1, n do
        draw_text(centre_x(msg.lines[i], cfg.hud_x, cfg.hud_right), cfg.hud_y - 1 - (n - i) * line_h,
                  msg.lines[i], msg.color)
    end
    age(hud_queue)
end

-- ── Center-screen prompt (prominent banner, auto-dismiss) ───────────────────
local prompt_queue = {}

function H.prompt(text, r, g, b, duration_frames)
    text = sanitize(text)
    enqueue(prompt_queue, text, wrap_prompt(text),
            fmt("#%02X%02X%02X", r or 255, g or 255, b or 255),
            duration_frames or 300)
end

local function render_prompt()
    if #prompt_queue == 0 then return end
    local py = cfg.prompt_y
    local p = prompt_queue[1]
    -- The prompt floats mid-screen, so extra lines grow DOWNWARD from prompt_y.
    local n, line_h = #p.lines, cfg.font_size + 2
    gui.drawBox(1, py, cfg.screen_w - 1, py + cfg.prompt_h + (n - 1) * line_h,
                0xFF000000, 0xCC000000)
    for i = 1, n do
        draw_text(centre_x(p.lines[i], 4, cfg.screen_w - 4), py + 1 + (i - 1) * line_h, p.lines[i], p.color)
    end
    age(prompt_queue)
end

-- A banner sits at gameover_y, unless a live prompt has wrapped far enough to reach it
-- (two lines on a 144px screen end at y=61, the banner starts at 54): then it drops to
-- just below the prompt, so neither text runs under the other.
local function banner_y()
    local p = prompt_queue[1]
    if not p then return cfg.gameover_y end
    local bottom = cfg.prompt_y + cfg.prompt_h + (#p.lines - 1) * (cfg.font_size + 2)
    return math.max(cfg.gameover_y, bottom + 2)
end

-- ── Game-over persistent overlay ────────────────────────────────────────────
local game_over = false

function H.set_game_over()
    game_over = true
end

function H.is_game_over()
    return game_over
end

local function render_game_over()
    if not game_over then return end
    local gy = banner_y()
    gui.drawBox(0, gy, cfg.screen_w, gy + 24, 0xFFBB0000, 0xDD990000)
    draw_text(centre_x("GAME OVER!", 0, cfg.screen_w, cfg.font_size + 2), gy + 4, "GAME OVER!", "#FFFFFF",
              cfg.font_size + 2)
end

-- ── Rebuild (post-whiteout) persistent banner ───────────────────────────────
-- Shown while the server is auto-restoring alive PC mons after a whiteout.
-- Blue palette to differentiate from red game_over; game_over overdraws if both
-- happen to be set (render order below).
local rebuild_text = nil

function H.set_rebuilding(text)
    rebuild_text = sanitize(text or "REBUILDING TEAM")
end

function H.clear_rebuilding()
    rebuild_text = nil
end

function H.is_rebuilding()
    return rebuild_text ~= nil
end

local function render_rebuilding()
    if not rebuild_text or game_over then return end
    local ry = banner_y()
    -- "REBUILDING: A, B, C +N" runs to ~46 chars (276px): wrap it like the prompt, 2 lines max.
    local lines, line_h = wrap_prompt_n(rebuild_text, 2), cfg.font_size + 2
    gui.drawBox(0, ry, cfg.screen_w, ry + 4 + #lines * line_h, 0xFF0066AA, 0xDD003388)
    for i, line in ipairs(lines) do
        draw_text(centre_x(line, 0, cfg.screen_w), ry + 2 + (i - 1) * line_h, line, "#FFFFFF")
    end
end

-- ── Nuzlocke-start transient banner ─────────────────────────────────────────
-- Blue celebratory banner shown the moment the player first picks up Pokéballs.
-- Auto-dismisses; a later rebuild/game_over banner overdraws if both collide.
local nuzlocke_start_text   = nil
local nuzlocke_start_frames = 0

function H.nuzlocke_start(text, duration_frames)
    nuzlocke_start_text   = sanitize(text or "Nuzlocke Start!")
    nuzlocke_start_frames = duration_frames or 180
end

function H.is_nuzlocke_start()
    return nuzlocke_start_text ~= nil
end

local function render_nuzlocke_start()
    if not nuzlocke_start_text or game_over then return end
    local ny = banner_y()
    gui.drawBox(0, ny, cfg.screen_w, ny + 24, 0xFF0066AA, 0xDD003388)
    draw_text(centre_x(nuzlocke_start_text, 0, cfg.screen_w, cfg.font_size + 2), ny + 4, nuzlocke_start_text,
              "#FFFFFF", cfg.font_size + 2)
    nuzlocke_start_frames = nuzlocke_start_frames - 1
    if nuzlocke_start_frames <= 0 then nuzlocke_start_text = nil end
end

-- ── Master render (call once per frame, after all game logic) ───────────────
function H.render()
    -- Wipe first, then repaint only the live elements: anything whose duration
    -- ran out simply stops being drawn and is gone the same frame. This only
    -- clears drawBox/drawText (see clear_surface above) -- callers that draw
    -- via gui.text() directly (the lua/tests diagnostic harnesses) are not
    -- routed through here and are unaffected by this wipe.
    clear_surface()
    render_prompt()
    render_hud()
    render_nuzlocke_start()
    render_rebuilding()
    render_game_over()
end

-- ── Utility ─────────────────────────────────────────────────────────────────
function H.clear()
    hud_queue = {}
    prompt_queue = {}
    game_over = false
    rebuild_text = nil
    nuzlocke_start_text = nil
    nuzlocke_start_frames = 0
    clear_surface()   -- take effect now, not on the next frame's render
end

return H
