-- pol-panel: LIVE check of the SLink PANEL behind the Phone card's virtual contact (Stage 2).
-- DEV evidence. Boots the same warp fixture as pol-phone (CONTINUE, native; the fixture sits on
-- ROUTE_29, reached by the engine's own warp -- POL_POSMODE=warp, no bare position pokes).
--
-- SYNTH writes, each logged and disclosed:
--   * wPokegearFlags = $87  (Pokegear + map/radio/phone cards obtained)  -- as in pol-phone
--   * wPhoneList     = 00 00 00 00 00 (no native contact, so the virtual SLink row is row 0)
-- Everything else is scripted native input. Probes only read.
--
-- WHO PUBLISHES THE PAGES: a scripted mailbox writer in THIS driver (L.on_frame), not a real
-- SLinkServer. It speaks the shared GB panel protocol verbatim -- watch PANEL_STATE (+9) for
-- AWAIT, write the page's charmap codes into wSlinkPanelText with SLINK_PANEL_STRIDE, publish
-- PANEL_PAGES (+11), then PANEL_STATE = STAGED (+9) last. So what this run proves is the ROM
-- half (SlinkPanel, the PrintText render, paging, the close) against a host that obeys the ABI.
local L = dofile(os.getenv("SLINK_ROOT") .. "/tools/polished_live/pol_lib.lua")
local fmt = string.format
local J = L.json
local CM = dofile(L.ROOT .. "/data/games/polished_crystal/charmap.lua").glyphs
local ENC = dofile(L.ROOT .. "/data/games/polished_crystal/charmap.lua").encoding
local ARROW = 240
L.log("[panel] step 1: libraries loaded")
client.speedmode(400)
L.log(fmt("[panel] boot frame %d rom %s", emu.framecount(), gameinfo.getromhash()))

local ev = {pages = J.array({}), inputs = J.array({}), panel_edges = J.array({}),
    native_hooks = J.array({}), hook_registrations = J.array({})}
local native_context
local function native_observer(name)
    return function(right_bank)
        if not right_bank or not native_context then return end
        local hit = native_context()
        hit.site, hit.bank, hit.addr = name, L.SYM[name][1], L.SYM[name][2]
        ev.native_hooks[#ev.native_hooks + 1] = hit
    end
end
local function register_native(name)
    local handle = L.hook(name, native_observer(name))
    assert(handle ~= nil and handle ~= false and handle ~= "", "native panel hook registration failed: " .. name)
    ev.hook_registrations[#ev.hook_registrations + 1] = {
        site = name, bank = L.SYM[name][1], addr = L.SYM[name][2], handle = tostring(handle),
        frame = emu.framecount(), domain = "System Bus"}
end
for _, n in ipairs({"OWPlayerInput", "StartMenu", "PokeGear", "PokegearPhoneContactSubmenu",
                    "SlinkPhone_CallGate", "MakePhoneCallFromPokegear", "SlinkPanel", "YesNoBox"}) do
    if n == "PokegearPhoneContactSubmenu" or n == "SlinkPhone_CallGate"
        or n == "MakePhoneCallFromPokegear" or n == "SlinkPanel" then register_native(n)
    else L.hook(n) end
end
L.log("[panel] step 2: boot logged")
local function write(path, s) local f = assert(io.open(path, "w")) f:write(s) f:close() end

-- ── the mailbox contract, read off the generated profile so nothing is hardcoded ──────────
local PROF = J.decode(L.slurp(L.ROOT .. "/data/games/polished_crystal/profile.json"))
L.log("[panel] step 3: hooks + write audit installed")
local PAN = PROF.titles.polished.overlay.panel
local OFF_STATE, OFF_PAGE, OFF_PAGES, OFF_CAPS = 9, 10, 11, 8
local AWAIT, STAGED = 1, 2
local MB_LO = L.woff("wSlinkMailbox", 0)
local MB_HI = L.woff("wSlinkMailboxEnd", 0) - 1
local TEXT = L.woff("wSlinkPanelText", 0)
L.log(fmt("[panel] step 4: geometry base=$%X span $%X..$%X lines=%d stride=%d term=$%02X",
          TEXT, MB_LO, MB_HI, PAN.lines, PAN.stride, PAN.terminator))

-- Preserve actual calls and synchronous byte images; the proxy sticks even when
-- BizHawk's memory table does not accept replacement function bindings.
local AUDIT = {n = 0, lo = 0x7fffffff, hi = -1, outside = {}, seen = {}, on = false}
local raw_memory = memory
local write_ops, touched_spans, batches = J.array({}), J.array({}), J.array({})
local write_phase = "synth"
local function image(addr, n, domain)
    local bytes = {}
    for i = 0, n - 1 do bytes[#bytes + 1] = raw_memory.read_u8(addr + i, domain) end
    return L.hex(bytes)
end
local function wram_image()
    local n = raw_memory.getmemorydomainsize("WRAM")
    assert(type(n) == "number" and n > 0, "WRAM image geometry unavailable")
    return {domain = "WRAM", addr = 0, n = n, hex = image(0, n, "WRAM")}
end
local G = dofile(L.ROOT .. "/tools/polished_live/recording_guard.lua")
local observed_memory = G.observe_memory(raw_memory, function(_, a, _, d)
    if AUDIT.on then
        AUDIT.n = AUDIT.n + 1
        if a < AUDIT.lo then AUDIT.lo = a end
        if a > AUDIT.hi then AUDIT.hi = a end
        if d ~= "WRAM" or a < MB_LO or a > MB_HI then AUDIT.outside[#AUDIT.outside + 1] = fmt("%X", a) end
        if #AUDIT.seen < 80 then AUDIT.seen[#AUDIT.seen + 1] = fmt("%X", a) end
    end
end)
local host_depth = 0
memory = setmetatable({}, {__index = function(_, api)
    local fn = observed_memory[api]
    if type(api) ~= "string" or not api:match("^write")
       or (type(fn) ~= "function" and type(fn) ~= "userdata") then return fn end
    return function(...)
        local addr, value, domain = ...
        domain = domain or raw_memory.getcurrentmemorydomain()
        local sequence = #write_ops + 1
        local op = {sequence = sequence, api = api, addr = addr, value = value,
            domain = domain, bank = domain == "WRAM" and math.floor(addr / 0x1000) or J.null,
            frame = emu.framecount(), n = api == "write_u8" and 1 or J.null, phase = write_phase}
        local span = {sequence = sequence, addr = addr, domain = domain, bank = op.bank, n = op.n}
        if api == "write_u8" then
            op.old = raw_memory.read_u8(addr, domain)
            span.pre_hex = fmt("%02x", op.old)
        else
            op.old, span.pre_hex, span.reason = J.null, J.null, "unsupported image API: " .. api
        end
        write_ops[sequence], touched_spans[sequence] = op, span
        host_depth = host_depth + 1
        local result = table.pack(pcall(fn, ...))
        host_depth = host_depth - 1
        if api == "write_u8" then
            op.new = raw_memory.read_u8(addr, domain)
            span.post_hex = fmt("%02x", op.new)
        else op.new, span.post_hex = J.null, J.null end
        op.status, op.error = result[1] and "returned" or "raised", result[1] and J.null or tostring(result[2])
        op.result = result[2] == nil and J.null or result[2]
        span.frame_after = emu.framecount()
        if not result[1] then error(result[2], 0) end
        return table.unpack(result, 2, result.n)
    end
end})
local function wb(addr, v) return memory.write_u8(addr, v, "WRAM") end
ev.write_audit = {schema = "polished-panel-write-audit-v1", write_ops = write_ops,
    touched_spans = touched_spans, batches = batches, domain = "WRAM",
    mailbox = {addr = MB_LO, last = MB_HI, n = MB_HI - MB_LO + 1}}

-- ── the pages this host publishes ────────────────────────────────────────────────────────
L.log("[panel] step 5: writer closed over")
local PAGES = {
    {"SOUL LINK", "PARTNER: RED"},
    {"PAIRS 2/3", "BADGES 4/8"},
}
local function encode(line)
    local out = {}
    for i = 1, PAN.line_max do
        local ch = line:sub(i, i)
        out[i] = (ch ~= "" and type(ENC[ch]) == "number") and ENC[ch] or 0x7F
    end
    out[PAN.line_max + 1] = PAN.terminator
    return out
end
local staged_at, staged_page, staged_count = nil, nil, 0
-- NOTE (round 5 bisect): installing L.on_frame BEFORE to_overworld stalls the boot --
-- 8002 frames, never reaching the overworld. With it installed afterwards the same
-- driver reaches the Phone card in ~1000 frames. pol_panel.lua:writer() is therefore
-- called AFTER the boot, never at load.
local stage_frame_before_input = nil
local presses = 0
local function press(btn, settle)
    presses = presses + 1
    local input = {button = btn, start_frame = emu.framecount(), press_count = presses, frames = J.array({})}
    ev.inputs[#ev.inputs + 1] = input
    for _ = 1, 2 do L.frame({[btn] = true}) input.frames[#input.frames + 1] = emu.framecount() end
    L.idle(settle or 24)
    input.end_frame = emu.framecount()
end
local function writer()
    local st = L.rw("wSlinkMailbox", OFF_STATE)
    if st ~= AWAIT then staged_at = nil return end
    if staged_at then return end                    -- one stage per AWAIT edge, never per frame
    local page = L.rw("wSlinkMailbox", OFF_PAGE) or 0
    if page >= #PAGES then page = #PAGES - 1 end
    local lines = PAGES[page + 1]
    local batch = {phase = "panel", page = page, start_frame = emu.framecount(),
        presses = presses, sequence_start = #write_ops + 1, before = wram_image()}
    batches[#batches + 1] = batch
    for i = 0, PAN.lines - 1 do
        local bytes = encode(lines[i + 1] or "")
        for c = 1, PAN.stride do wb(TEXT + i * PAN.stride + c - 1, bytes[c]) end
    end
    wb(L.woff("wSlinkMailbox", OFF_PAGES), #PAGES)
    wb(L.woff("wSlinkMailbox", OFF_STATE), STAGED)  -- published last, exactly like gb_panel
    staged_at, staged_page, staged_count = emu.framecount(), page + 1, staged_count + 1
    if staged_count == 1 then stage_frame_before_input = presses end
    batch.after, batch.end_frame, batch.sequence_end = wram_image(), emu.framecount(), #write_ops
    ev.pages[#ev.pages + 1] = {frame = staged_at, page = page, presses = presses,
        state = L.rw("wSlinkMailbox", OFF_STATE), pages = L.rw("wSlinkMailbox", OFF_PAGES),
        text_hex = L.hex(L.wbytes("wSlinkPanelText", 0, PAN.lines * PAN.stride))}
end
L.on_frame = nil

-- ── helpers (pol_phone/drv/phone.lua, same shapes) ──────────────────────────────────────
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
local function rows(a, b) local o = {} for y = a, b do o[#o + 1] = fmt("%02d|%s|", y, row(y)) end return o end
local function screen() return table.concat(rows(0, 17), "\n") end
-- The textbox's own border glyphs, so a row can be compared as TEXT rather than as a picture.
local BORDER = { ["│"] = 1, ["┃"] = 1, ["┌"] = 1, ["┐"] = 1, ["└"] = 1, ["┘"] = 1,
                 ["─"] = 1, ["━"] = 1 }
--- One tilemap row as trimmed text: border glyphs removed, edges trimmed.
local function clean_row(y)
    local t = row(y)
    for ch in pairs(BORDER) do t = t:gsub(ch, " ") end
    return (t:gsub("^%s+", ""):gsub("%s+$", ""))
end
--- The textbox rows, each on its own. A line the ROM wrapped across a row boundary is two
--- entries here, which is what made the old joined-substring matcher miss "PARTNER: RED".
local function box_rows() local o = {} for y = 12, 17 do o[#o + 1] = clean_row(y) end return o end
--- The box as one string with rows joined by a space: a phrase laid out on ONE row still matches
--- contiguously, and a phrase the ROM wrapped is still findable.
local function box() return table.concat(box_rows(), " ") end
--- Rows with a column ruler, so a dump says which column a glyph is in (round 8: "PAR" landed at
--- row 15 columns 17-19 and the dump had no way to show it).
local function ruler(a, b)
    local tens, ones = "     ", "     "
    for i = 1, 20 do
        tens = tens .. tostring(math.floor((i - 1) / 10) % 10)
        ones = ones .. tostring((i - 1) % 10)
    end
    return tens .. "   (tens)\n" .. ones .. "   (cols)\n" .. table.concat(rows(a, b), "\n")
end
local function plist() return L.hex(L.wbytes("wPhoneList", 0, 5)) end
local function state() return L.rw("wJumptableIndex") end
local function cur() return L.rw("wPokegearPhoneCursorPosition"), L.rw("wPokegearPhoneScrollPosition") end
local function shot(name) client.screenshot(fmt("%s/%s.png", L.RUN, name)) end

-- Native closing is a distinct actor. Bus callback payloads are not values:
-- bracket the native stores with frame images, never interpolate multiple stores.
local close_active, close_pending, close_previous
local close_handles, close_hooks = {}, {}
local function context()
    return {frame = emu.framecount(), pc = emu.getregister("PC"), rom_bank = L.rombank(),
        wram_bank = L.bus(0xFF70) & 7}
end
native_context = context
for _, name in ipairs({"SlinkPanel.page", "SlinkPanel.ready", "SlinkPanel.WaitForStage"}) do
    register_native(name)
end
local function phone_snapshot()
    local s = context()
    s.phone_state, s.card = state(), L.rw("wPokegearCard")
    s.cursor, s.scroll = cur()
    s.caller, s.phone_list = L.rw("wCurCaller"), plist()
    s.calls = L.hits.MakePhoneCallFromPokegear
    s.panel_state, s.page, s.pages = L.rw("wSlinkMailbox", OFF_STATE),
        L.rw("wSlinkMailbox", OFF_PAGE), L.rw("wSlinkMailbox", OFF_PAGES)
    s.mailbox_hex = L.hex(L.wbytes("wSlinkMailbox", 0, MB_HI - MB_LO + 1))
    s.tilemap_hex = L.hex(L.wbytes("wTilemap", 0, 360))
    s.attrmap_hex = L.hex(L.wbytes("wAttrmap", 0, 360))
    return s
end
local function close_frame()
    if not close_active then return end
    local after = phone_snapshot()
    local bracket = {before = close_previous, after = after}
    close_active.frames[#close_active.frames + 1] = bracket
    for _, w in ipairs(close_pending) do
        if #close_pending == 1 then
            w.old, w.new, w.reason = close_previous.panel_state, after.panel_state, J.null
        else w.old, w.new, w.reason = J.null, J.null, "multiple qualified stores in frame bracket" end
        w.bracket = #close_active.frames
        close_active.writes[#close_active.writes + 1] = w
    end
    close_pending, close_previous = {}, after
end
local function close_remove()
    local failures = {}
    local function remove(api, handle, fn)
        local ok, why = pcall(fn)
        local row = {api = api, handle = tostring(handle), status = ok and "returned" or "raised"}
        row.error = ok and J.null or tostring(why)
        ev.close.removals[#ev.close.removals + 1] = row
        if not ok then failures[#failures + 1] = tostring(why) end
    end
    for _, id in ipairs(close_handles) do
        remove("event.unregisterbyid", id, function() event.unregisterbyid(id) end)
    end
    for _, name in ipairs(close_hooks) do
        remove("L.unhook", name, function() L.unhook(name) end)
    end
    close_handles, close_hooks, close_active = {}, {}, nil
    return #failures == 0, table.concat(failures, "; ")
end
local function close_begin()
    local before = phone_snapshot()
    ev.close = {before = before, start_frame = before.frame, frames = J.array({}),
        writes = J.array({}), native_hooks = J.array({}), removals = J.array({}), input_index = #ev.inputs + 1}
    close_active, close_previous, close_pending = ev.close, before, {}
    local ok, why = pcall(function()
        local addr = L.SYM.wSlinkMailbox[2] + OFF_STATE
        local id = event.onmemorywrite(function(_, value)
            if not close_active or host_depth > 0 then return end
            local w = context()
            w.sequence, w.addr, w.domain, w.bank, w.n = #close_active.writes + #close_pending + 1,
                addr, "System Bus", 0, 1
            w.callback_value = type(value) == "number" and value or J.null
            w.old, w.new, w.reason = J.null, J.null, "pending frame bracket"
            close_pending[#close_pending + 1] = w
        end, addr, "pol_panel_close_state", "System Bus")
        assert(id ~= nil and id ~= false and id ~= "", "native close write hook registration failed")
        close_handles[#close_handles + 1] = id
        for _, name in ipairs({"SlinkPanel.close", "PokegearPhone_Joypad"}) do
            local handle = L.hook(name, function(right_bank)
                if not close_active or not right_bank then return end
                local hit = context()
                hit.site, hit.bank, hit.addr = name, L.SYM[name][1], L.SYM[name][2]
                close_active.native_hooks[#close_active.native_hooks + 1] = hit
            end)
            assert(handle ~= nil and handle ~= false and handle ~= "", "native close exec hook registration failed")
            close_hooks[#close_hooks + 1] = name
        end
    end)
    if not ok then close_remove() error(why, 0) end
end
local function panel_edge(origin)
    local state_value, page, pages = L.rw("wSlinkMailbox", OFF_STATE),
        L.rw("wSlinkMailbox", OFF_PAGE), L.rw("wSlinkMailbox", OFF_PAGES)
    local previous = ev.panel_edges[#ev.panel_edges]
    if previous and previous.state == state_value and previous.page == page and previous.pages == pages then return end
    local edge = context()
    edge.origin, edge.state, edge.page, edge.pages, edge.presses = origin, state_value, page, pages, presses
    edge.mailbox_hex = L.hex(L.wbytes("wSlinkMailbox", 0, MB_HI - MB_LO + 1))
    ev.panel_edges[#ev.panel_edges + 1] = edge
end

L.log("[panel] step 6: helpers defined, entering boot")
-- ── drive to the Phone card ──────────────────────────────────────────────────────────────
-- (the round-2 boot probe that pulsed A/B here MOVED THE MAIN MENU off CONTINUE and was
-- the real cause of the boot-gate abort; it must never press anything here)
L.log("[panel] step 7: calling to_overworld (writer NOT yet installed)")
if not L.to_overworld(24, 3, 60, 8000, "continue") then L.die("CONTINUE did not reach ROUTE_29") end
L.on_frame = function() close_frame() panel_edge("native-frame") writer() panel_edge("host-stage") end
L.log("[panel] step 8: overworld reached; mailbox writer installed")
local flags0 = L.rw("wPokegearFlags")
local synth_batch = {phase = "synth", start_frame = emu.framecount(), sequence_start = #write_ops + 1, before = wram_image()}
batches[#batches + 1] = synth_batch
wb(L.woff("wPokegearFlags", 0), 0x87)
local list0 = plist()
wb(L.woff("wPhoneList", 0), 0)
for i = 1, 4 do wb(L.woff("wPhoneList", i), 0) end
synth_batch.after, synth_batch.end_frame, synth_batch.sequence_end = wram_image(), emu.framecount(), #write_ops
-- the SYNTH setup above wrote outside the mailbox on purpose; from here on every host-side panel write is audited
AUDIT.n, AUDIT.lo, AUDIT.hi, AUDIT.outside, AUDIT.seen = 0, 0x7fffffff, -1, {}, {}
AUDIT.on = true
write_phase = "panel"
ev.write_audit.start_frame = emu.framecount()
L.log(fmt("[panel] SYNTH wPokegearFlags %02X -> 87, wPhoneList %s -> 0000000000 (SLink is row 0)",
          flags0, list0))

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
-- C1: the caps byte advertises the panel and nothing else
local caps = L.rw("wSlinkMailbox", OFF_CAPS)
ev.caps = caps
-- C1: the caps byte advertises the panel and nothing else.
-- The span is a property of the SYMBOLS' ADDRESSES, not of what is stored at them: L.rw reads the
-- memory CONTENTS of a symbol, so End - Start was a difference of two mailbox bytes.
local SPAN = L.SYM.wSlinkMailboxEnd[2] - L.SYM.wSlinkMailbox[2]
L.check("C1 PANEL cap advertised (integrated overlay: caps=07 = PANEL|SFX|SFX_NOTIFY)", caps == 0x07, fmt("%02X", caps))
L.check("C1 mailbox span is the overlay's own 69 bytes (symbol addresses, not contents)",
        SPAN == 69, fmt("$%X..$%X = %d", L.SYM.wSlinkMailbox[2], L.SYM.wSlinkMailboxEnd[2], SPAN))

-- the list has exactly the SLink row
for _ = 1, 40 do local c, s = cur() if c + s == 0 then break end press("Up", 10) end
local c0, s0 = cur()
ev.initial = phone_snapshot()
ev.initial.submenu_hits = L.hits.PokegearPhoneContactSubmenu
L.check("C0 the SLink row is row 0 and is labelled by the overlay",
        row(4):find("SLink:", 1, true) ~= nil, row(4))

-- ── A on the SLink row -> Call -> the PANEL ─────────────────────────────────────────────
local calls0, gate0, panel0 = L.hits.MakePhoneCallFromPokegear, L.hits.SlinkPhone_CallGate, L.hits.SlinkPanel
local caller0, plist_before = L.rw("wCurCaller"), plist()
press("A", 30)                                     -- contact submenu
L.check("C0 A opened the contact submenu", L.hits.PokegearPhoneContactSubmenu >= 1)
ev.submenu = phone_snapshot()
ev.submenu.hits = L.hits.PokegearPhoneContactSubmenu
local presses_before_call = presses
ev.presses_before_call = presses_before_call
press("A", 10)                                     -- Call
wait(function() return L.hits.SlinkPhone_CallGate > gate0 end, 300, "Call never reached SlinkPhone_CallGate")
wait(function() return L.hits.SlinkPanel > panel0 end, 300, "the gate never entered SlinkPanel")

-- The ROM prints the fallback, publishes AWAIT and the host answers, all before it waits on a
-- KEY (the stage-2 fix: the fallback no longer ends in `prompt`). No press to dismiss.
wait(function() return staged_count >= 1 end, 400, "the host never saw an AWAIT")
L.idle(4)
L.check("C2 AWAIT was published with NO EXTRA keypress (the fallback no longer prompts)",
        -- presses_before_call is captured BEFORE the Call press, so staging on presses_before_call+1
        -- means the Call A itself and nothing after it: the lease was served without a dead press.
        stage_frame_before_input == presses_before_call + 1,
        fmt("presses when AWAIT was staged: %s; before Call: %d (Call itself = +1, expected %d)",
            tostring(stage_frame_before_input), presses_before_call, presses_before_call + 1))
-- Do NOT judge the render on the first frame after staging: the ROM prints the page through the
-- text engine and the tilemap is pushed on its own schedule. Poll, and dump the screen at the
-- AWAIT and 120 frames later so a miss says WHICH screen we were looking at.
L.log("[panel] --- textbox AT AWAIT (column ruler) ---")
L.log(ruler(12, 17))
local seen_frame, seen = nil, nil
for _ = 1, 30 do
    local b = box()
    if b:find("SOUL LINK", 1, true) then seen_frame, seen = emu.framecount(), b break end
    L.idle(10)
end
L.log(seen_frame and fmt("[panel] box text appeared at frame %d", seen_frame)
      or fmt("[panel] box text NEVER appeared within 300 frames (last read at frame %d)", emu.framecount()))
L.idle(120)
L.log("[panel] --- textbox 120 frames later (column ruler) ---")
L.log(ruler(12, 17))
shot("panel_2_page1")
local p1 = box()
ev.page1 = p1
local br1 = box_rows()
ev.page1_rows, ev.page1_snapshot = J.array(br1), phone_snapshot()
ev.staging_press_count = stage_frame_before_input
local on_row = function(s) for _, r in ipairs(br1) do if r:find(s, 1, true) then return r end end end
L.check("C2 page 1 rendered: SOUL LINK on one row", on_row("SOUL LINK") ~= nil, table.concat(br1, " / "))
L.check("C2 page 1 rendered: PARTNER: RED on ONE row (row-aware)",
        on_row("PARTNER: RED") ~= nil, table.concat(br1, " / "))
L.check("C2 both lines present (joined form)", p1:find("SOUL LINK", 1, true) ~= nil
        and p1:find("PARTNER: RED", 1, true) ~= nil, p1)
L.check("C2 the ROM's fallback second line is gone", p1:find("NO CLIENT", 1, true) == nil, p1)
L.check("C2 PANEL_PAGE/PAGES published by the host",
        L.rw("wSlinkMailbox", OFF_PAGES) == 2 and L.rw("wSlinkMailbox", OFF_PAGE) == 0,
        fmt("page %d of %d", L.rw("wSlinkMailbox", OFF_PAGE), L.rw("wSlinkMailbox", OFF_PAGES)))

-- ── A advances to page 2 ─────────────────────────────────────────────────────────────────
press("A", 10)                                     -- advance: page 2's fallback + AWAIT, no keypress needed
wait(function() return staged_count >= 2 end, 400, "the host never saw the page-2 AWAIT")
L.idle(4)
local p2 = box()
shot("panel_3_page2")
ev.page2 = p2
local br2 = box_rows()
ev.page2_rows, ev.page2_snapshot = J.array(br2), phone_snapshot()
local on_row2 = function(s) for _, r in ipairs(br2) do if r:find(s, 1, true) then return r end end end
L.check("C3 A advanced to page 2: PAIRS 2/3 on one row", on_row2("PAIRS 2/3") ~= nil, table.concat(br2, " / "))
L.check("C3 A advanced to page 2: BADGES 4/8 on one row", on_row2("BADGES 4/8") ~= nil, table.concat(br2, " / "))
L.check("C3 PANEL_PAGE advanced to 1", L.rw("wSlinkMailbox", OFF_PAGE) == 1, L.rw("wSlinkMailbox", OFF_PAGE))

-- ── B closes back to the Phone list ──────────────────────────────────────────────────────
close_begin()
local close_ok, close_error = pcall(function()
press("B", 10)
wait(function() return state() == 0x0a end, 400, "B did not return to PHONEJOYPAD")
L.idle(40)
local back = screen()
shot("panel_4_back")
local c1, s1 = cur()
L.check("C4 back on the Phone list with the cursor/scroll intact", c1 == c0 and s1 == s0,
        fmt("%d/%d -> %d/%d", c0, s0, c1, s1))
L.check("C4 the SLink row is still row 0", row(4):find("SLink:", 1, true) ~= nil, row(4))
L.check("C4 no phone call placed (MakePhoneCallFromPokegear never entered)",
        L.hits.MakePhoneCallFromPokegear == calls0)
L.check("C4 wCurCaller untouched", L.rw("wCurCaller") == caller0, fmt("%02X", L.rw("wCurCaller")))
L.check("C4 wPhoneList untouched by the panel", plist() == plist_before, plist())
L.check("C4 the panel cleared PANEL_STATE on close", L.rw("wSlinkMailbox", OFF_STATE) == 0,
        L.rw("wSlinkMailbox", OFF_STATE))
ev.back = back
end)
local close_snapshot_ok, close_snapshot_error = pcall(function()
    close_frame()
    ev.close.after, ev.close.end_frame, ev.close.input = phone_snapshot(), emu.framecount(), ev.inputs[ev.close.input_index]
end)
local close_cleanup_ok, close_cleanup_error = close_remove()
if not close_ok or not close_snapshot_ok or not close_cleanup_ok then
    local failures = {}
    if not close_ok then failures[#failures + 1] = tostring(close_error) end
    if not close_snapshot_ok then failures[#failures + 1] = tostring(close_snapshot_error) end
    if not close_cleanup_ok then failures[#failures + 1] = tostring(close_cleanup_error) end
    ev.close.error = table.concat(failures, "; ")
    write(L.RUN .. "/panel.json", J.encode(ev))
    error(ev.close.error, 0)
end

-- ── write audit ──────────────────────────────────────────────────────────────────────────
-- The whole block is wrapped so a Lua error here cannot silently swallow the run: the harness
-- records result.txt but not BizHawk's console, so an uncaught error used to look exactly like
-- "the driver stopped". Round 11: this was reached only after the flow stalled at the last C4
-- line, with no RESULT: at all. L.finish stays OUTSIDE the pcall (it ends by raising, on purpose),
-- so a RESULT: line is always written.
local okC5, errC5 = pcall(function()
    L.log("[panel] C5 BLOCK ENTER")
    local hex = function(v) return fmt("%X", v % 0x10000) end   -- safe for 0 and the sentinels
    L.log(fmt("[panel] audit n=%d lo=%s hi=%s outside=%d", AUDIT.n, tostring(AUDIT.lo), tostring(AUDIT.hi),
              #AUDIT.outside))
    ev.audit = {n = AUDIT.n, lo = hex(AUDIT.lo), hi = hex(AUDIT.hi),
                mailbox = fmt("%X..%X", MB_LO, MB_HI), outside = AUDIT.outside,
                writes = AUDIT.seen}
    L.log("[panel] C5 BLOCK ev.audit built")
    L.check("C5 every PANEL write is inside the mailbox span", #AUDIT.outside == 0,
            fmt("%d panel writes, %s..%s vs mailbox %s; outside: %s", AUDIT.n,
                AUDIT.n > 0 and ev.audit.lo or "-", AUDIT.n > 0 and ev.audit.hi or "-",
                ev.audit.mailbox, #AUDIT.outside == 0 and "none" or table.concat(AUDIT.outside, ",")))
    -- The vacuous-pass guard: an audit that SAW nothing must not read as a clean run.
    L.check("C5 the audit actually SAW writes (not a vacuous pass)", AUDIT.n > 0, AUDIT.n)
    L.log("[panel] C5 CHECKS DONE, ev.hits/panel.json next")
    ev.hits = L.hits
    ev.write_audit.end_frame = emu.framecount()
    memory = raw_memory
    for _, name in ipairs({"SlinkPanel.page", "SlinkPanel.ready", "SlinkPanel.WaitForStage"}) do L.unhook(name) end
    L.on_frame = nil
    write(L.RUN .. "/panel.json", J.encode(ev))
    L.log("[panel] C5 write addresses: " .. table.concat(AUDIT.seen, " "))
    L.log("[panel] C5 BLOCK OK")
end)
if not okC5 then
    L.log("[panel] C5 BLOCK ERROR: " .. tostring(errC5))
    L.check("C5 audit block ran without a Lua error", false, tostring(errC5))
end
L.finish("pol-panel")
