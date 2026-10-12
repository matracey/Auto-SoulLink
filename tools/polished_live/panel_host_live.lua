-- panel_host_live: the Polished SLink PANEL published by the REAL HOST (RC proof, OPEN-PANEL-PAGES host half).
-- Launched by tools/polished_live/panel_host_live.py (real SLinkServer + a scripted partner `b` TCP identity).
--
-- WHO PUBLISHES THE PAGES: lua/slink.lua -> lua/gen2 client. The server's `link_panel` command is held by the
-- client (panel:hold), and the client's own service() answers the ROM's AWAIT by staging the page and then STAGED
-- (lua/gb_panel.lua stage). THIS DRIVER WRITES NOTHING IN THE MAILBOX: its only writes are the two SYNTH setup
-- writes below (wPokegearFlags, wPhoneList), each logged and asserted to lie outside the mailbox span. The rest is
-- scripted native input (walk, Bag -> Ball -> throw for the one real catch, START -> Pokegear -> Phone -> Call).
--
-- Why a catch: Soul Link pairs form when BOTH players caught in the same area. Player a's half is a REAL native wild
-- catch on Route 29 (live.lua's stage-2 battle logic); the partner's half is the scripted TCP partner's `capture`
-- (sent by the Python side before this run starts catching).
-- Replay checks cover every server-derived page, not merely the subset the ROM advertised.
local Oracle = {}
function Oracle.evaluate_pages(rows, pages, lines)
    local expected = math.ceil(#rows / lines)
    local ok = expected > 0 and #pages == expected
    for i, p in ipairs(pages) do
        ok = ok and p.page == i and p.pages == expected
    end
    return {expected_pages = expected, ok = ok}
end
if POL_ORACLE_ONLY then return Oracle end
local L = dofile(os.getenv("SLINK_ROOT") .. "/tools/polished_live/pol_lib.lua")
local fmt = string.format
local J = L.json
local CM = dofile(L.ROOT .. "/data/games/polished_crystal/charmap.lua").glyphs
local ENC = dofile(L.ROOT .. "/data/games/polished_crystal/charmap.lua").encoding
local function nums(s) local t = {} for v in s:gmatch("%d+") do t[#t + 1] = tonumber(v) end return t end
local WALK = nums(os.getenv("POL_WALK") or "46,51")
client.speedmode(400)
L.log(fmt("[panelhost] boot frame %d rom %s", emu.framecount(), gameinfo.getromhash()))

for _, n in ipairs({"OWPlayerInput", "LoadBattleMenu", "BattleMenu_Run", "PokeBallEffect", "PokeBallEffect.caught",
                    "BlinkCursor", "YesNoBox", "StartMenu", "StartBattle", "ExitBattle", "BattlePack",
                    "SetInitialOptions.joypad_loop", "PokeGear", "PokegearPhoneContactSubmenu",
                    "SlinkPhone_CallGate", "MakePhoneCallFromPokegear", "SlinkPanel"}) do
    if L.SYM[n] then L.hook(n) end
end

-- ── the real client, through the real entry (same sequence as live.lua) ─────────────────────────────────────
SLINK_HOST, SLINK_PORT, SLINK_PLAYER = os.getenv("SLINK_HOST"), tonumber(os.getenv("SLINK_PORT")), os.getenv("SLINK_PLAYER")
dofile(L.ROOT .. "/lua/slink.lua")
if not SLINK_GEN2_CLIENT then L.die("the Gen 2 route did not start a client (see slink_lua.log)") end
local P = SLINK_GEN2_PARTS
L.check("admitted as the Polished overlay (DEV_OVERLAY_SHA1)", P.pack == "polished_crystal" and P.title == "polished"
        and P.artifact_kind == "overlay" and P.qualification == "DEV_OVERLAY_SHA1")
local PROF = J.decode(L.slurp(L.ROOT .. "/data/games/polished_crystal/profile.json"))
local PAN = PROF.titles.polished.overlay.panel
local OFF_STATE, OFF_PAGE, OFF_PAGES, OFF_CAPS = 9, 10, 11, 8
local AWAIT, STAGED = 1, 2
local MB_LO = L.woff("wSlinkMailbox", 0)
local MB_HI = L.woff("wSlinkMailboxEnd", 0) - 1
local TEXT = L.woff("wSlinkPanelText", 0)

-- wire tap: every line the client sends / receives. link_panel commands are kept in full (rows decoded).
local C = package.loaded["connector"]
local ev = {sent_events = {}, panels = {}, synth = {}, pages = {}, driver_writes = {}}
ev.lines_per_page = PAN.lines
local orig_send, orig_recv = C.send, C.receive
C.send = function(line, ...)
    local ok, msg = pcall(J.decode, line)
    if ok and type(msg) == "table" and msg.event ~= "tick" then
        ev.sent_events[#ev.sent_events + 1] = {frame = emu.framecount(), event = msg.event, area_id = msg.area_id,
                                                key = msg.key, species_id = msg.species_id, panel = msg.panel,
                                                panel_abi = msg.panel_abi}
    end
    return orig_send(line, ...)
end
local last_rows = nil
C.receive = function(...)
    local line = orig_recv(...)
    if line ~= nil and line:find("link_panel", 1, true) then
        local ok, msg = pcall(J.decode, line)
        for _, c in ipairs(ok and type(msg) == "table" and msg.commands or {}) do
            if c.cmd == "link_panel" then
                ev.panels[#ev.panels + 1] = {frame = emu.framecount(), rows = c.rows}
                last_rows = c.rows
            end
        end
    end
    return line
end

-- ── helpers ───────────────────────────────────────────────────────────────────────────────────────────────
local function write_file(path, s) local f = assert(io.open(path, "w")) f:write(s) f:close() end
local function wait(cond, bound, what)
    local f0 = emu.framecount()
    while not cond() do
        if emu.framecount() - f0 > bound then L.die(what) return end
        L.frame()
    end
end
local function row(y)
    local s = {}
    for i, b in ipairs(L.wbytes("wTilemap", y * 20, 20)) do s[i] = CM[b] or "?" end
    return table.concat(s)
end
local BORDER = {"│", "┃", "┌", "┐", "└", "┘", "─", "━"}
local function clean_row(y)
    local t = row(y)
    for _, ch in ipairs(BORDER) do t = t:gsub(ch, " ") end
    return (t:gsub("^%s+", ""):gsub("%s+$", ""))
end
local function box_rows() local o = {} for y = 12, 17 do o[#o + 1] = clean_row(y) end return o end
local function screen() local o = {} for y = 0, 17 do o[#o + 1] = fmt("%02d|%s|", y, row(y)) end return table.concat(o, "\n") end
local function shot(name) client.screenshot(fmt("%s/%s.png", L.RUN, name)) end
local presses = 0
local function press(btn, settle) presses = presses + 1 for _ = 1, 2 do L.frame({[btn] = true}) end L.idle(settle or 24) end
local function state() return L.rw("wJumptableIndex") end
local function cur() return L.rw("wPokegearPhoneCursorPosition"), L.rw("wPokegearPhoneScrollPosition") end
local function plist() return L.hex(L.wbytes("wPhoneList", 0, 5)) end
local function enc_line(s)   -- what the ROM should show for a 16-glyph panel line: encodable chars kept, others blank, trimmed
    local out = {}
    for i = 1, math.min(#s, PAN.line_max) do
        local ch = s:sub(i, i)
        local b = ENC[ch]
        out[i] = (type(b) == "number" and b >= 0x7F and b <= 0xFF) and ch or " "
    end
    return (table.concat(out):gsub("%s+$", ""))
end

-- ── continue into the game, hello, baseline panel ────────────────────────────────────────────────────────
if not L.to_overworld(24, 3, 60, 8000, "continue") then L.die("CONTINUE did not reach ROUTE_29") end
wait(function() return SLINK_GEN2_CLIENT.hello_sent == true end, 1800, "client never sent hello")
L.idle(120)
wait(function() return last_rows ~= nil end, 900, "server never published a link_panel to the client")
ev.baseline_rows = last_rows
L.log("[panelhost] baseline link_panel rows: " .. table.concat(last_rows, " || "))

-- ── the one REAL native wild catch (live.lua stage-2 logic) ────────────────────────────────────────────────
local function walk_for_battle(label)
    local f, dir = emu.framecount(), "Left"
    while not L.after("StartBattle", f) do
        if emu.framecount() - f > 20000 then L.die(label .. ": no wild battle in 20000 frames") end
        local x = L.rw("wXCoord")
        if x <= WALK[1] then dir = "Right" elseif x >= WALK[2] then dir = "Left" end
        if L.recent("BlinkCursor", 2) then L.pulse("A") else L.frame({[dir] = true}) end
    end
    L.idle(2)
end
local BALL_POCKET = 2
local function battle_throw(label)
    local f_start, handled, last_act = emu.framecount(), -1, emu.framecount()
    local prepped, prep_pulses, caught = -1, 0, false
    while not L.after("OWPlayerInput", f_start) do
        local f = emu.framecount()
        if f - f_start > 9000 then L.die(label .. ": battle did not end") end
        local menu, btn = L.hit.LoadBattleMenu, nil
        if menu and menu > handled and f - menu >= 6 then
            if L.after("PokeBallEffect", menu) or L.after("BattleMenu_Run", menu) then
                handled = menu
            elseif L.after("BattlePack", menu) then
                btn = L.rw("wCurPocket") == BALL_POCKET and "A" or "Right"
            elseif prepped ~= menu then
                btn = "Start"
                if (emu.framecount() % 16) < 2 then prep_pulses = prep_pulses + 1 end
                if prep_pulses >= 2 then prepped = menu prep_pulses = 0 end
            else btn = "A" end
        elseif L.recent("YesNoBox", 40) then btn = "B"
        elseif L.recent("BlinkCursor", 2) then btn = "A"
        elseif f - math.max(last_act, L.hit.BlinkCursor or 0, L.hit.LoadBattleMenu or 0) > 90 then btn = "B" end
        if btn and (f % 16) < 2 then last_act = f end
        L.pulse(btn)
    end
    local c = L.hit["PokeBallEffect.caught"]
    caught = c ~= nil and c >= f_start
    L.to_overworld(nil, nil, 30, 3000, label .. "-after")
    return caught
end
local caught = false
for attempt = 1, 5 do
    walk_for_battle("catch" .. attempt)
    local before = L.rw("wPartyCount")
    caught = battle_throw("catch" .. attempt)
    L.log(fmt("[panelhost] catch attempt %d: caught %s party %d -> %d", attempt, tostring(caught), before, L.rw("wPartyCount")))
    if caught then break end
end
L.check("a wild mon was caught natively", caught)
L.idle(240)
local cap
for _, e in ipairs(ev.sent_events) do if e.event == "capture" then cap = e end end
L.check("client emitted `capture`", cap ~= nil, cap and fmt("area %s key %s species %s", tostring(cap.area_id), tostring(cap.key), tostring(cap.species_id)) or "none")
ev.capture = cap

-- the pair forms server-side when the partner's capture and ours are both in; the next link_panel carries it
local function pair_rows(rows)
    for _, r in ipairs(rows or {}) do
        local n, d = r:match("^PAIRS (%d+)/(%d+)$")   -- compact rows: Gen2PolishedAdapter.info_panel_width() == line_max
        if n and tonumber(d) >= 1 then return true end
    end
    return false
end
local f_wait = emu.framecount()
while not pair_rows(last_rows) do
    if emu.framecount() - f_wait > 1800 then break end
    L.frame()
end
L.check("the real server published a link_panel WITH a linked pair", pair_rows(last_rows), table.concat(last_rows or {}, " || "))
ev.pair_rows = last_rows
L.log("[panelhost] pair link_panel rows: " .. table.concat(last_rows or {}, " || "))

-- ── SYNTH setup (the only driver writes in this file), then drive to the Phone card ───────────────────────
local function synth_write(name, plus, v)
    local addr = L.woff(name, plus)
    assert(addr < MB_LO or addr > MB_HI, "SYNTH write inside the mailbox: " .. name)
    memory.write_u8(addr, v, "WRAM")
    ev.driver_writes[#ev.driver_writes + 1] = {name = name, plus = plus, off = fmt("%X", addr), value = v}
end
local flags0, list0 = L.rw("wPokegearFlags"), plist()
synth_write("wPokegearFlags", 0, 0x87)
for i = 0, 4 do synth_write("wPhoneList", i, 0) end
L.log(fmt("[panelhost] SYNTH wPokegearFlags %02X -> 87, wPhoneList %s -> 0000000000 (SLink is row 0)", flags0, list0))
ev.synth = {pokegear_flags_before = flags0, phone_list_before = list0}

local function open_phone()
    local s0 = emu.framecount()
    while not L.after("StartMenu", s0) do
        if emu.framecount() - s0 > 600 then L.die("START menu never opened") end
        L.pulse("Start")
    end
    L.idle(30)
    local r
    for i = 1, L.rw("wMenuItemsList") do if L.rw("wMenuItemsList", i) == 7 then r = i end end
    if not r then L.die("no POKEGEAR row in the START menu") end
    local s1 = emu.framecount()
    while not L.after("PokeGear", s1) do
        if emu.framecount() - s1 > 1200 then L.die("PokeGear never ran") end
        L.pulse(L.rw("wMenuCursorY") == r and "A" or "Down")
    end
    local c0 = emu.framecount()
    while not (L.rw("wPokegearCard") == 2 and state() == 0x0a) do
        if emu.framecount() - c0 > 1200 then L.die("the Phone card never reached its joypad state") end
        L.pulse(L.rw("wPokegearCard") < 2 and "Right" or nil)
    end
    L.idle(40)
end
open_phone()
for _ = 1, 40 do local c, s = cur() if c + s == 0 then break end press("Up", 10) end
local c0, s0 = cur()
L.check("C0 the SLink row is row 0 and labelled by the overlay", row(4):find("SLink:", 1, true) ~= nil, row(4))
L.check("C1 mailbox advertises the PANEL cap (caps byte has bit 1)", (L.rw("wSlinkMailbox", OFF_CAPS) & 2) ~= 0,
        fmt("%02X", L.rw("wSlinkMailbox", OFF_CAPS)))
L.check("client reports the panel present+fresh", SLINK_GEN2_PARTS.panel:present() == true)

-- ── open the panel (A -> submenu, A -> Call) and page through it; the CLIENT answers every AWAIT ─────────────
local calls0, gate0, panel0 = L.hits.MakePhoneCallFromPokegear, L.hits.SlinkPhone_CallGate, L.hits.SlinkPanel
local caller0, plist_before = L.rw("wCurCaller"), plist()
press("A", 30)
L.check("C0 A opened the contact submenu", L.hits.PokegearPhoneContactSubmenu >= 1)
press("A", 10)
wait(function() return L.hits.SlinkPhone_CallGate > gate0 end, 300, "Call never reached SlinkPhone_CallGate")
wait(function() return L.hits.SlinkPanel > panel0 end, 300, "the gate never entered SlinkPanel")

local expected_pages = math.ceil(#(ev.pair_rows or {}) / PAN.lines)
local page = 0
while true do
    -- the ROM publishes AWAIT, the CLIENT stages and publishes STAGED (the driver does nothing here)
    wait(function() return L.rw("wSlinkMailbox", OFF_STATE) == STAGED end, 400,
         fmt("page %d: the client never published STAGED", page + 1))
    L.idle(40)   -- the ROM prints the page through the text engine; give the tilemap time
    local pg, pgs = L.rw("wSlinkMailbox", OFF_PAGE), L.rw("wSlinkMailbox", OFF_PAGES)
    L.check("runtime page count equals the server-derived count", pgs == expected_pages, pgs)
    L.check("runtime page index is the next expected page", pg + 1 == #ev.pages + 1, pg + 1)
    local text_bytes = L.wbytes("wSlinkPanelText", 0, PAN.lines * PAN.stride)
    local staged = {}
    for l = 0, PAN.lines - 1 do
        local s = {}
        for c = 1, PAN.line_max do s[c] = CM[text_bytes[l * PAN.stride + c]] or "?" end
        staged[#staged + 1] = (table.concat(s):gsub("%s+$", ""))
    end
    shot(fmt("panel_host_page%d", pg + 1))
    local rec = {page = pg + 1, pages = pgs, frame = emu.framecount(), box_rows = box_rows(), staged_text = staged,
                 screen = screen()}
    ev.pages[#ev.pages + 1] = rec
    L.log(fmt("[panelhost] page %d/%d box rows: %s", pg + 1, pgs, table.concat(rec.box_rows, " | ")))
    if pg + 1 ~= #ev.pages or pgs ~= expected_pages or #ev.pages >= expected_pages then break end
    page = pg
    press("A", 10)   -- advance: the ROM re-publishes AWAIT for the next page; the client stages it
    wait(function() return L.rw("wSlinkMailbox", OFF_PAGE) == pg + 1 end, 300, "A did not advance PANEL_PAGE")
    -- STATE goes STAGED -> AWAIT (ROM) -> STAGED (client) within a frame or two; give both time, then the loop top
    -- waits for STAGED and the staged-text check proves the NEW page's bytes were published
    L.idle(60)
end
press("B", 10)
wait(function() return state() == 0x0a end, 400, "B did not return to PHONEJOYPAD")
L.idle(40)
shot("panel_host_back")
local c1, s1 = cur()
L.check("C4 back on the Phone list with the cursor/scroll intact", c1 == c0 and s1 == s0, fmt("%d/%d -> %d/%d", c0, s0, c1, s1))
L.check("C4 no phone call placed", L.hits.MakePhoneCallFromPokegear == calls0)
L.check("C4 wCurCaller untouched", L.rw("wCurCaller") == caller0)
L.check("C4 wPhoneList untouched by the panel", plist() == plist_before, plist())
L.check("C4 the panel cleared PANEL_STATE on close", L.rw("wSlinkMailbox", OFF_STATE) == 0, L.rw("wSlinkMailbox", OFF_STATE))

-- ── who wrote the mailbox? the CLIENT's panel permit receipts (and nothing from this driver) ────────────────
local receipts = {}
local plog = SLINK_GEN2_PARTS.panel_writes and SLINK_GEN2_PARTS.panel_writes.log or {}
local outside = 0
for _, r in ipairs(plog) do
    receipts[#receipts + 1] = {addr = fmt("%X", r.addr), n = r.n, why = r.why, frame = r.frame, status = r.status}
    if r.addr < 0xC000 + MB_LO or r.addr + r.n - 1 > 0xC000 + MB_HI then outside = outside + 1 end
end
ev.client_receipts, ev.mailbox = receipts, fmt("%X..%X", 0xC000 + MB_LO, 0xC000 + MB_HI)
L.check("C5 the CLIENT's panel permit logged writes (attribution is non-vacuous)", #plog > 0, #plog)
L.check("C5 every client panel write is inside the mailbox span", outside == 0, outside)
L.check("C5 driver wrote only the 6 SYNTH setup bytes, none in the mailbox", #ev.driver_writes == 6, #ev.driver_writes)
local coverage = Oracle.evaluate_pages(ev.pair_rows or {}, ev.pages, PAN.lines)
ev.expected_pages = coverage.expected_pages
L.check("every server-derived page rendered exactly once in order with a stable count", coverage.ok,
        fmt("expected %d; rendered %d", coverage.expected_pages, #ev.pages))
-- render fidelity against the rows the server SENT: page p shows rows 2p-1 and 2p of the pair link_panel
local pr = ev.pair_rows or {}
for i, r in ipairs(pr) do
    L.check(fmt("server row %d fits the ROM line whole and has no '|' (%q)", i, r), #r <= PAN.line_max and not r:find("|", 1, true), #r)
end
for _, p in ipairs(ev.pages) do
    for l = 1, PAN.lines do
        local want = enc_line(pr[(p.page - 1) * PAN.lines + l] or "")   -- right-trimmed; keeps a blanked leading '|'
        local want_row = want:gsub("^%s+", "")                          -- the tilemap row is read left-trimmed (clean_row)
        local got_staged = p.staged_text[l]
        local found = want == ""
        for _, r in ipairs(p.box_rows) do if r == want_row and want_row ~= "" then found = true end end
        L.check(fmt("page %d line %d: rendered text equals the server row (%q)", p.page, l, want_row), found,
                table.concat(p.box_rows, " | "))
        L.check(fmt("page %d line %d: mailbox text the client staged equals the server row", p.page, l), got_staged == want,
                tostring(got_staged))
    end
end
write_file(L.RUN .. "/panel_host.json", J.encode(ev))
L.finish("pol-panel-host")
