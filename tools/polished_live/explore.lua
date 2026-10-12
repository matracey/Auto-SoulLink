-- tools/polished_live/explore.lua -- run-2 read-only explorations, launched by `harness.py explore` (no SLink client).
-- POL_EXPLORE=B: Pokemon Center 2F trade receptionist, DelayFrame-bridge stack samples per script phase.
-- POL_EXPLORE=C: the real Pokegear from the START menu, icon strip / cursor / tile usage per native card.
-- Boots the run-1 warp fixture (CONTINUE, native). SYNTH writes, each logged and disclosed in LIVE_RESULTS.md:
--   B: EVENT_GAVE_MYSTERY_EGG_TO_ELM (wEventFlags bit 33, the receptionist's checkevent) + the engine warp to
--      POKECENTER_2F (group 20 #1) step (5,3), the square Script_LeftCableTradeCenter walks the player to.
--   C: wPokegearFlags = $87 (POKEGEAR_OBTAINED_F + MAP/RADIO/PHONE card bits; EXPN bit 3 left clear).
-- Everything else is native input. Probes only read; logs go to files at milestones, never per frame.
local L = dofile(os.getenv("SLINK_ROOT") .. "/tools/polished_live/pol_lib.lua")
local fmt = string.format
local J = L.json
local WHICH = os.getenv("POL_EXPLORE") or "B"
client.speedmode(300)
L.log(fmt("[explore] %s boot frame %d rom %s", WHICH, emu.framecount(), gameinfo.getromhash()))

for _, name in ipairs({"OWPlayerInput", "StartMenu", "BlinkCursor", "YesNoBox", "TitleScreenMain", "MainMenu",
                       "SetInitialOptions.joypad_loop", "PokeGear", "InitPokegearTilemap", "FixPlayerEVsAndStats",
                       "CheckPartyForMail", "Special_WaitForLinkedFriend", "Special_WaitForLinkedFriend.done",
                       "Special_TryQuickSave", "SlinkTradeWaitGate", "SlinkTradeTimeoutGate", "SlinkTradeProposerService", "SlinkTradeExit"}) do
    L.hook(name)
end
local function write(path, s) local f = assert(io.open(path, "w")) f:write(s) f:close() end

if not L.to_overworld(24, 3, 60, 8000, "continue") then L.die("CONTINUE did not reach ROUTE_29") end

-- the engine's own warp (Script_warp bytes), never a bare position poke
local function warp(group, number, x, y)
    L.ww("wMapGroup", 0, group) L.ww("wMapNumber", 0, number) L.ww("wXCoord", 0, x) L.ww("wYCoord", 0, y)
    L.ww("wDefaultSpawnpoint", 0, 0xFF)
    memory.write_u8(L.SYM.hMapEntryMethod[2], 0xF1, "System Bus")  -- MAPSETUP_WARP
    L.ww("wMapStatus", 0, 1)
end

if WHICH == "B" then
    local ev = L.rw("wEventFlags", 4)
    L.ww("wEventFlags", 4, ev | 0x02)                                -- event 33 = byte 4 bit 1
    L.log(fmt("[explore] SYNTH wEventFlags+4 %02X -> %02X (EVENT_GAVE_MYSTERY_EGG_TO_ELM)", ev, L.rw("wEventFlags", 4)))
    warp(20, 1, 5, 3)
    L.log("[explore] SYNTH engine warp -> POKECENTER_2F (20:1) step (5,3)")
    if not L.to_overworld(20, 1, 60, 3000, "warp-pc2f") then L.die("MAPSETUP_WARP to POKECENTER_2F did not land") end
    L.log(fmt("[explore] header: tileset %d width %d height %d (map_constants POKECENTER_2F = 8x4); at (%d,%d)",
              L.rw("wMapTileset"), L.rw("wMapWidth"), L.rw("wMapHeight"), L.rw("wXCoord"), L.rw("wYCoord")))
    L.check("POKECENTER_2F header loaded (8x4)", L.rw("wMapWidth") == 8 and L.rw("wMapHeight") == 4)
    client.screenshot(L.RUN .. "/pc2f.png")

    -- bridge samples, tagged by phase; at most 300 per phase
    local phase = "overworld"
    local stacks, order, counts = {}, {}, {}
    L.hook("SlinkDelayFrameBridge", function(right_bank)
        if not right_bank then return end
        local n = counts[phase] or 0
        if n >= 300 then return end
        counts[phase] = n + 1
        local sp = emu.getregister("SP")
        local bytes = {}
        for i = 0, 31 do bytes[#bytes + 1] = L.bus((sp + i) & 0xFFFF) end
        local key = fmt("%s|%04X|%s|b%02X|s%d", phase, sp, L.hex(bytes), L.rombank(), L.bus(0xFF70) % 8)
        if not stacks[key] then stacks[key] = 0 order[#order + 1] = key end
        stacks[key] = stacks[key] + 1
    end)
    -- known-positive control: the idle overworld here must give the run-1 Route 29 chain
    L.idle(320)
    -- face the receptionist and talk
    for _ = 1, 6 do L.frame({Up = true}) end
    L.idle(10)
    local t0 = emu.framecount()
    phase = "talk"
    while not L.after("FixPlayerEVsAndStats", t0) do
        if emu.framecount() - t0 > 600 then L.die("the receptionist script never reached FixPlayerEVsAndStats") end
        L.pulse("A")
    end
    L.log(fmt("[explore] receptionist script past checkevent (FixPlayerEVsAndStats) at frame %d", L.hit.FixPlayerEVsAndStats))
    local f1 = emu.framecount()
    local seen_yesno = false
    while true do
        local f = emu.framecount()
        if f - f1 > 6000 then L.die("receptionist script did not finish") end
        local w, d = L.hit.SlinkTradeProposerService, L.hit.SlinkTradeExit
        local in_wait = w ~= nil and w > f1 and (d == nil or d < w)
        if in_wait then phase = "overlay_host_wait"
        elseif L.recent("YesNoBox", 2) or (L.hit.YesNoBox and not L.after("CheckPartyForMail", f1) and L.after("YesNoBox", f1)) then
            phase = "yesno" seen_yesno = true
        elseif L.rw("wScriptRunning") ~= 0 then
            phase = L.after("SlinkTradeExit", f1) and "after_wait" or "script"
        else phase = "overworld_after" end
        if phase == "overworld_after" and L.ow_idle() and L.after("SlinkTradeExit", f1) then break end
        if phase == "overlay_host_wait" then L.frame() else L.pulse("A") end   -- A = YES on the prompt, A closes text
        if phase == "overlay_host_wait" and not L.wait_shot then L.wait_shot = true client.screenshot(L.RUN .. "/please_wait.png") end
    end
    L.idle(60)
    L.unhook("SlinkDelayFrameBridge")
    L.log(fmt("[explore] milestones: FixPlayerEVsAndStats %s CheckPartyForMail %s WaitForLinkedFriend %s .done %s TryQuickSave %s YesNoBox %s",
              tostring(L.hit.FixPlayerEVsAndStats), tostring(L.hit.CheckPartyForMail), tostring(L.hit.Special_WaitForLinkedFriend),
              tostring(L.hit["Special_WaitForLinkedFriend.done"]), tostring(L.hit.Special_TryQuickSave), tostring(L.hit.YesNoBox)))
    L.log(fmt("[explore] overlay milestones: wait_gate %s service %s exit %s", tostring(L.hit.SlinkTradeWaitGate), tostring(L.hit.SlinkTradeProposerService), tostring(L.hit.SlinkTradeExit)))
    L.check("the trade prompt was answered YES (CheckPartyForMail ran)", L.hits.CheckPartyForMail > 0)
    L.check("overlay trade service entered and returned without a host", L.hits.SlinkTradeProposerService > 0 and L.hits.SlinkTradeExit > 0)
    L.check("trade gate skipped the native cable wait", L.hits.SlinkTradeWaitGate > 0 and L.hits.Special_WaitForLinkedFriend == 0)
    local out = {}
    for _, key in ipairs(order) do
        local ph, sp, hex, bank, svbk = key:match("^(.-)|(%x+)|(%x+)|b(%x+)|s(%d)$")
        out[#out + 1] = {phase = ph, sp = sp, bytes = hex, rombank = bank, svbk = tonumber(svbk), count = stacks[key]}
        L.log(fmt("[explore] STAGEB stack %s x%d", key, stacks[key]))
    end
    for ph, n in pairs(counts) do L.log(fmt("[explore] STAGEB phase %s: %d samples", ph, n)) end
    write(L.RUN .. "/stacks.json", J.encode(out))
    L.finish("explore-B")
end

-- ── C: Pokegear ────────────────────────────────────────────────────────────────────────────────
local flags0 = L.rw("wPokegearFlags")
L.ww("wPokegearFlags", 0, 0x87)
L.log(fmt("[explore] SYNTH wPokegearFlags %02X -> %02X", flags0, L.rw("wPokegearFlags")))
L.idle(4)
local s0 = emu.framecount()
while not L.after("StartMenu", s0) do
    if emu.framecount() - s0 > 600 then L.die("START menu never opened") end
    L.pulse("Start")
end
L.idle(30)
local row, items = nil, {}
for i = 1, L.rw("wMenuItemsList") do
    items[#items + 1] = L.rw("wMenuItemsList", i)
    if items[#items] == 7 then row = i end                           -- STARTMENUITEM_POKEGEAR
end
L.log(fmt("[explore] start menu items %s, POKEGEAR row %s", table.concat(items, ","), tostring(row)))
if not row then L.die("no POKEGEAR row in the START menu") end

-- Gambatte's write callback payload is not the stored byte. Only frame images
-- around one bank-qualified native store can supply old/new, including no-op stores.
local cards, union = {}, {}
-- Bounded for the existing entry/card timeouts plus dwell; images stay360 bytes
-- per WRAM map and16 narrowly watched VRAM cells, never a full VRAM dump.
local redraw_json_limits = {bytes = 64 * 1024 * 1024, items = 4000000}
local native_frame = L.frame
local active_redraw, frame_bracket, redraw_card
local redraw_ids = {}
local watches = {}
for _, name in ipairs({"wTilemap", "wAttrmap"}) do
    local symbol = assert(L.SYM[name], "unknown symbol " .. name)
    for _, offset in ipairs({8, 9, 28, 29}) do
        local addr = symbol[2] + offset
        watches[#watches + 1] = {addr = addr, target = name == "wTilemap" and "tilemap" or "attrmap",
            offset = offset, row = offset // 20, col = offset % 20,
            expected_bank = addr < 0xD000 and 0 or (symbol[1] == 0 and 1 or symbol[1])}
    end
end
for _, map in ipairs({0x9800, 0x9C00}) do
    for row = 0, 1 do
        for _, col in ipairs({8, 9}) do
            watches[#watches + 1] = {addr = map + row * 32 + col, target = "bgmap",
                map = map, row = row, col = col, offset = row * 32 + col}
        end
    end
end
local function redraw_context()
    local svbk, vbk = L.bus(0xFF70), L.bus(0xFF4F)
    local wram_bank = svbk % 8
    return {frame = emu.framecount(), pc = emu.getregister("PC"), rom_bank = L.rombank(),
        wram_bank = wram_bank == 0 and 1 or wram_bank, vram_bank = vbk % 2, svbk = svbk, vbk = vbk}
end
local function redraw_image()
    local cells = J.array({})
    for bank = 0, 1 do
        for _, watch in ipairs(watches) do
            if watch.target == "bgmap" then
                local image_addr = bank * 0x2000 + watch.addr - 0x8000
                cells[#cells + 1] = {addr = watch.addr, bank = bank, map = watch.map,
                    row = watch.row, col = watch.col, domain = "VRAM", image_addr = image_addr,
                    value = memory.read_u8(image_addr, "VRAM")}
            end
        end
    end
    return {tilemap = L.hex(L.wbytes("wTilemap", 0, 360)),
        attrmap = L.hex(L.wbytes("wAttrmap", 0, 360)), vram_cells = cells}
end
local function image_byte(image, op)
    if op.target ~= "bgmap" then
        local first = op.offset * 2 + 1
        return tonumber(image[op.target]:sub(first, first + 1), 16)
    end
    for _, cell in ipairs(image.vram_cells) do
        if cell.addr == op.addr and cell.bank == op.vram_bank then return cell.value end
    end
end
local function bind_frame_values(redraw, bracket)
    local counts = {}
    for i = bracket.first_write, #redraw.writes do
        local op = redraw.writes[i]
        if op.qualified then
            local bank = op.target == "bgmap" and op.vram_bank or op.expected_bank
            local key = op.addr .. ":" .. bank
            counts[key] = (counts[key] or 0) + 1
        end
    end
    bracket.last_write = #redraw.writes
    for i = bracket.first_write, bracket.last_write do
        local op = redraw.writes[i]
        if op.qualified then
            local bank = op.target == "bgmap" and op.vram_bank or op.expected_bank
            if redraw.callback_error then
                op.reason = "callback_error_in_stage"
            elseif counts[op.addr .. ":" .. bank] ~= 1 then
                op.reason = "multiple_qualified_stores_in_frame"
            else
                local old, new = image_byte(bracket.before, op), image_byte(bracket.after, op)
                if old == nil or new == nil then
                    op.reason = "missing_bank_qualified_image_byte"
                else
                    op.old, op.new = old, new
                    op.reason = "single_qualified_store_bound_to_frame_images"
                end
            end
        end
    end
end
L.frame = function(buttons)
    if not active_redraw then return native_frame(buttons) end
    local redraw = active_redraw
    local bracket = {index = #redraw.frames + 1, pre_context = redraw_context(), before = redraw_image(),
        post_context = J.null, after = J.null, first_write = #redraw.writes + 1}
    redraw.frames[#redraw.frames + 1] = bracket
    frame_bracket = bracket
    local ok, why = pcall(native_frame, buttons)
    frame_bracket = nil
    local images_ok, images_why = pcall(function()
        bracket.post_context, bracket.after = redraw_context(), redraw_image()
        bind_frame_values(redraw, bracket)
    end)
    if not images_ok then
        redraw.errors[#redraw.errors + 1] = {kind = "frame_images", error = tostring(images_why)}
        error(images_why, 0)
    end
    if not ok then error(why, 0) end
    if redraw.callback_error then error("native redraw callback failed", 0) end
end
local function end_redraw()
    local redraw = active_redraw
    local ok, why = pcall(function()
        redraw.exit = redraw_context()
        redraw.phase_end = redraw.exit.frame
        redraw.after = redraw_image()
    end)
    if not ok then redraw.errors[#redraw.errors + 1] = {kind = "stage_images", error = tostring(why)} end
    active_redraw, frame_bracket = nil, nil
    local cleanup_error
    for _, registration in ipairs(redraw_ids) do
        local removed, result = pcall(event.unregisterbyid, registration.handle)
        registration.removed = removed and result ~= false
        if not registration.removed then
            registration.cleanup_error = removed and "unregisterbyid returned false" or tostring(result)
            redraw.errors[#redraw.errors + 1] = {kind = "unregister", addr = registration.addr,
                handle = registration.handle, error = registration.cleanup_error}
            cleanup_error = registration.cleanup_error
        end
    end
    redraw_ids = {}
    if not ok then error(why, 0) end
    if cleanup_error then error(cleanup_error, 0) end
    return redraw
end
local function begin_redraw(card)
    redraw_card = card
    local redraw = {phase = "card_" .. card .. "_transition_dwell", phase_start = emu.framecount(),
        phase_end = J.null, entry = J.null, exit = J.null, before = J.null, after = J.null,
        frames = J.array({}), writes = J.array({}), registrations = J.array({}), errors = J.array({})}
    active_redraw = redraw
    redraw.entry, redraw.before = redraw_context(), redraw_image()
    for _, watch in ipairs(watches) do
        local registration = {addr = watch.addr, domain = "System Bus", target = watch.target, handle = J.null}
        redraw.registrations[#redraw.registrations + 1] = registration
        local ok, handle = pcall(event.onmemorywrite, function(addr, callback_value, callback_flags)
            if active_redraw ~= redraw then return end
            local observed, why = pcall(function()
                local context = redraw_context()
                local qualified, reason = addr == watch.addr, "unbracketed_store"
                if not qualified then
                    reason = "callback_address_mismatch"
                elseif watch.target ~= "bgmap" and watch.expected_bank ~= 0
                        and context.wram_bank ~= watch.expected_bank then
                    qualified, reason = false, "wrong_wram_bank"
                end
                local op = {sequence = #redraw.writes + 1, frame = context.frame, pc = context.pc,
                    rom_bank = context.rom_bank, wram_bank = context.wram_bank, vram_bank = context.vram_bank,
                    svbk = context.svbk, vbk = context.vbk, addr = addr, domain = "System Bus",
                    callback_value = callback_value == nil and J.null or callback_value,
                    callback_flags = callback_flags == nil and J.null or callback_flags,
                    target = watch.target, offset = watch.offset, row = watch.row, col = watch.col,
                    map = watch.map, expected_bank = watch.expected_bank, qualified = qualified,
                    bracket = frame_bracket and frame_bracket.index or J.null,
                    old = J.null, new = J.null, reason = reason}
                redraw.writes[#redraw.writes + 1] = op
            end)
            if not observed then
                redraw.callback_error = true
                redraw.errors[#redraw.errors + 1] = {kind = "callback", addr = watch.addr, error = tostring(why)}
            end
        end, watch.addr, "pol_explore_redraw_" .. card .. "_" .. fmt("%04X", watch.addr), "System Bus")
        if not ok or type(handle) ~= "string" or handle == "" then
            registration.error = ok and "onmemorywrite returned no handle" or tostring(handle)
            redraw.errors[#redraw.errors + 1] = {kind = "register", addr = watch.addr, error = registration.error}
            error(registration.error, 0)
        end
        registration.handle = handle
        redraw_ids[#redraw_ids + 1] = registration
    end
    return redraw
end
local pending_redraw
local function vram_tiles(first, last, bank)
    local t = {}
    for id = first, last do
        local b = {}
        for i = 0, 15 do b[#b + 1] = memory.read_u8(bank * 0x2000 + 0x1000 + id * 16 + i, "VRAM") end
        t[fmt("%02X", id)] = L.hex(b)
    end
    return t
end
local function visit_cards()
pending_redraw = begin_redraw(0) -- includes the first input that enters PokeGear
local s1 = emu.framecount()
while not L.after("PokeGear", s1) do
    if emu.framecount() - s1 > 1200 then L.die("PokeGear never ran") end
    L.pulse(L.rw("wMenuCursorY") == row and "A" or "Down")
end
L.log(fmt("[explore] PokeGear entered at frame %d", L.hit.PokeGear))

local JOYPAD_STATE = {[0] = {[1] = true}, [1] = {[4] = true, [6] = true, [8] = true}, [2] = {[0x0a] = true}, [3] = {[0x0e] = true}}
local function grab_tilemap()
    local tm = L.wbytes("wTilemap", 0, 360)
    for _, v in ipairs(tm) do union[v] = true end
    return tm
end
for card = 0, 3 do
    if card > 0 then pending_redraw = begin_redraw(card) end
    local c0 = emu.framecount()
    while not (L.rw("wPokegearCard") == card and JOYPAD_STATE[card][L.rw("wJumptableIndex")]) do
        if emu.framecount() - c0 > 1200 then L.die("card " .. card .. " never reached its joypad state") end
        L.pulse(card > 0 and "Right" or nil)
    end
    for i = 1, 90 do L.frame() if i % 30 == 0 then grab_tilemap() end end   -- dwell; union catches redraws
    end_redraw()
    local tm = grab_tilemap()
    local rows01, attr01 = {}, L.wbytes("wAttrmap", 0, 40)
    for i = 1, 40 do rows01[i] = tm[i] end
    local anim = L.wbytes("wSpriteAnim1", 0, 16)
    local oam = L.wbytes("wShadowOAM", 0, 160)
    client.screenshot(fmt("%s/pokegear_card%d.png", L.RUN, card))
    cards[#cards + 1] = {card = card, jumptable = L.rw("wJumptableIndex"), rows01 = L.hex(rows01), attr01 = L.hex(attr01),
                         tilemap = L.hex(tm), spriteanim1 = L.hex(anim), shadow_oam = L.hex(oam),
                         cursor_x = (anim[5] + anim[7]) & 0xFF, frame = emu.framecount()}
    cards[#cards].redraw = pending_redraw
    pending_redraw = nil
    L.log(fmt("[explore] STAGEC card %d (state %02X) rows0-1 %s | attr %s | anim1 %s cursor x %d", card,
              L.rw("wJumptableIndex"), L.hex(rows01), L.hex(attr01), L.hex(anim), cards[#cards].cursor_x))
end
end
local visited, visit_error = pcall(visit_cards)
L.frame = native_frame
if not visited then
    local partial = active_redraw or pending_redraw
    if partial then
        partial.errors[#partial.errors + 1] = {kind = "driver", error = tostring(visit_error)}
        if active_redraw then pcall(end_redraw) end
        cards[#cards + 1] = {card = redraw_card, redraw = partial, frame = emu.framecount()}
    end
    write(L.RUN .. "/pokegear.json", assert(J.encode({flags_before = flags0, cards = cards,
        driver_error = tostring(visit_error)}, redraw_json_limits)))
    error(visit_error, 0)
end
local ids = {}
for v in pairs(union) do ids[#ids + 1] = v end
table.sort(ids)
local idhex = {}
for i, v in ipairs(ids) do idhex[i] = fmt("%02X", v) end
L.log("[explore] STAGEC tile ids in any card tilemap: " .. table.concat(idhex, " "))
write(L.RUN .. "/pokegear.json", assert(J.encode({flags_before = flags0, cards = cards, union = idhex,
    vram0 = vram_tiles(0x50, 0x7F, 0), vram1 = vram_tiles(0x50, 0x7F, 1)}, redraw_json_limits)))
L.check("all four native cards visited", #cards == 4)
L.finish("explore-C")
