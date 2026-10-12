-- pol-phone: Stage 1 live check of the Pokegear Phone card's virtual SLink contact (DEV evidence).
-- Boots run 1's warp fixture (CONTINUE, native; the fixture sits on ROUTE_29, reached by the engine's own warp).
-- SYNTH writes, each logged: wPokegearFlags = $87 (Pokegear + map/radio/phone cards) and wPhoneList per scenario.
-- Everything else is scripted native input; probes only read.
local L = dofile(os.getenv("SLINK_ROOT") .. "/tools/polished_live/pol_lib.lua")
local fmt = string.format
local J = L.json
local CM = dofile(L.ROOT .. "/data/games/polished_crystal/charmap.lua").glyphs
local ARROW = 240   -- charmap glyph for the filled cursor arrow (charmap.lua glyphs[240])
client.speedmode(300)
L.log(fmt("[phone] boot frame %d rom %s", emu.framecount(), gameinfo.getromhash()))

-- Raw observations are buffered, never logged from native callbacks. Write hooks
-- exist only for seven phone-list/cursor bytes during the substantive stage.
-- Gambatte's callback_value is not the stored byte; frame images resolve a
-- unique store, while repeated stores retain null operands and an ambiguity.
local evidence = {scenarios = {}, observations = {}, inputs = {}, hooks = {},
                  native_writes = {}, write_frames = {}, synth_writes = {}, coverage = {}}
local stage, sequence, coverage, window, synth_active = "boot", 0, nil, nil, false
local function tagged(record)
    sequence = sequence + 1
    record.seq, record.frame, record.stage = sequence, emu.framecount(), stage
    return record
end
local function begin_stage(id)
    stage = id
    coverage = {id = id, frame_start = emu.framecount(), observations = {}}
    evidence.coverage[#evidence.coverage + 1] = coverage
end
local function end_stage()
    coverage.frame_end = emu.framecount()
    coverage, stage = nil, "between_stages"
end
local function observe(point)
    local r = tagged({
        point = point, pc = emu.getregister("PC"), sp = emu.getregister("SP"),
        rom_bank = L.rombank(), svbk = L.bus(0xFF70) & 7,
        state = L.rw("wJumptableIndex"), card = L.rw("wPokegearCard"),
        cursor = L.rw("wPokegearPhoneCursorPosition"), scroll = L.rw("wPokegearPhoneScrollPosition"),
        submenu_cursor = L.rw("wPokegearPhoneSubmenuCursor"),
        selected_person = L.rw("wPokegearPhoneSelectedPerson"), caller = L.rw("wCurCaller"),
        selected_person_address = L.woff("wPokegearPhoneSelectedPerson"), selected_person_domain = "WRAM",
        num_set_bits = L.rw("wNumSetBits"), phone_list = L.hex(L.wbytes("wPhoneList", 0, 5)),
        tilemap_hex = L.hex(L.wbytes("wTilemap", 0, 360)),
        mailbox_hex = L.hex(L.wbytes("wSlinkMailbox", 0, L.SYM.wSlinkMailboxEnd[2] - L.SYM.wSlinkMailbox[2])),
        map_group = L.rw("wMapGroup"), map_number = L.rw("wMapNumber"),
        script_running = L.rw("wScriptRunning"), hits = J.decode(J.encode(L.hits)),
    })
    evidence.observations[#evidence.observations + 1] = r
    if coverage then coverage.observations[#coverage.observations + 1] = r end
    return r
end
local function span_images()
    return {phone_list_hex = L.hex(L.wbytes("wPhoneList", 0, 5)),
            cursor_hex = L.hex(L.wbytes("wPokegearPhoneCursorPosition", 0, 2))}
end
local function begin_window(id)
    begin_stage(id)
    window = {handles = {}}
    for _, span in ipairs({{"wPhoneList", 5}, {"wPokegearPhoneCursorPosition", 2}}) do
        local name, size = span[1], span[2]
        for offset = 0, size - 1 do
            local index, address = offset, L.SYM[name][2] + offset
            local handle = event.on_bus_write(function(callback_address, callback_value)
                if synth_active or not window then return end
                local bank = L.bus(0xFF70) & 7
                if address >= 0xD000 and (bank == 0 and 1 or bank) ~= L.SYM[name][1] then return end
                evidence.native_writes[#evidence.native_writes + 1] = tagged({
                    op = "native_bus_write", addr = address, domain = "System Bus", span = name, offset = index,
                    callback_address = callback_address, callback_value = callback_value == nil and J.null or callback_value,
                    observed = L.rw(name, index), pc = emu.getregister("PC"), sp = emu.getregister("SP"),
                    rom_bank = L.rombank(), svbk = bank,
                })
            end, address, "pol_phone_" .. id .. "_" .. address, "System Bus")
            assert(handle ~= nil and handle ~= "" and handle ~= false, "phone write hook registration failed")
            window.handles[#window.handles + 1] = handle
        end
    end
end
local function end_window()
    for _, handle in ipairs(window.handles) do event.unregisterbyid(handle) end
    window = nil
    end_stage()
end
local original_frame = L.frame
L.frame = function(buttons)
    evidence.inputs[#evidence.inputs + 1] = tagged({buttons = buttons or {}})
    local before, first, start
    if window then before, first, start = span_images(), #evidence.native_writes, emu.framecount() end
    original_frame(buttons)
    if window then
        local after, operations, counts = span_images(), {}, {}
        for i = first + 1, #evidence.native_writes do
            local op = evidence.native_writes[i]
            operations[#operations + 1] = op
            counts[op.addr] = (counts[op.addr] or 0) + 1
        end
        for _, op in ipairs(operations) do
            if counts[op.addr] == 1 then
                local key = op.span == "wPhoneList" and "phone_list_hex" or "cursor_hex"
                local at = op.offset * 2 + 1
                op.old, op.new = tonumber(before[key]:sub(at, at + 1), 16), tonumber(after[key]:sub(at, at + 1), 16)
                op.reason = J.null
            else
                op.old, op.new, op.reason = J.null, J.null, "multiple_stores_in_frame"
            end
        end
        evidence.write_frames[#evidence.write_frames + 1] = tagged({
            frame_start = start, before = before, after = after, operations = operations,
        })
    end
end
local original_ww = L.ww
L.ww = function(name, plus, value)
    local old = L.rw(name, plus)
    synth_active = true
    original_ww(name, plus, value)
    synth_active = false
    evidence.synth_writes[#evidence.synth_writes + 1] = tagged({
        symbol = name, addr = L.woff(name, plus), domain = "WRAM", old = old,
        value = value, new = L.rw(name, plus),
    })
end
for _, n in ipairs({"OWPlayerInput", "StartMenu", "SetInitialOptions.joypad_loop", "PokeGear",
                    "PokegearPhoneContactSubmenu", "PokegearPhoneContactSubmenu.Delete", "MakePhoneCallFromPokegear",
                    "SlinkPhone_CallGate", "SlinkPhone_CallerName", "SlinkPhone_CanDelete", "SlinkPhone_CountSetBits",
                    "YesNoBox", "SlinkPanel"}) do
    local name = n
    L.hook(name, function(qualified)
        if not qualified then return end
        local r = tagged({
            name = name, bank = L.SYM[name][1], pc = emu.getregister("PC"), sp = emu.getregister("SP"),
            rom_bank = L.rombank(), svbk = L.bus(0xFF70) & 7,
            caller = L.rw("wCurCaller"), selected_person = L.rw("wPokegearPhoneSelectedPerson"),
        })
        if name == "SlinkPhone_CallGate" or name == "SlinkPanel" then
            r.mailbox_hex = L.hex(L.wbytes("wSlinkMailbox", 0, L.SYM.wSlinkMailboxEnd[2] - L.SYM.wSlinkMailbox[2]))
        end
        evidence.hooks[#evidence.hooks + 1] = r
    end)
end
local function write(path, s) local f = assert(io.open(path, "w")) f:write(s) f:close() end

local function press(btn, settle) for _ = 1, 2 do L.frame({[btn] = true}) end L.idle(settle or 24) end
local function wait(cond, bound, what)
    local f0 = emu.framecount()
    while not cond() do
        if emu.framecount() - f0 > bound then L.die(what) end
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
local function count(text, pat) local n = 0 for _ in text:gmatch(pat) do n = n + 1 end return n end
local function plist() return L.hex(L.wbytes("wPhoneList", 0, 5)) end
local function state() return L.rw("wJumptableIndex") end
local function cur() return L.rw("wPokegearPhoneCursorPosition"), L.rw("wPokegearPhoneScrollPosition") end
local function shot(name) client.screenshot(fmt("%s/%s.png", L.RUN, name)) end

if not L.to_overworld(24, 3, 60, 8000, "continue") then L.die("CONTINUE did not reach ROUTE_29") end
local flags0 = L.rw("wPokegearFlags")
L.ww("wPokegearFlags", 0, 0x87)
local list0 = plist()
L.log(fmt("[phone] SYNTH wPokegearFlags %02X -> %02X; native wPhoneList at boot %s", flags0, L.rw("wPokegearFlags"), list0))

local function open_phone()
    local s0 = emu.framecount()
    while not L.after("StartMenu", s0) do
        if emu.framecount() - s0 > 600 then L.die("START menu never opened") end
        L.pulse("Start")
    end
    L.idle(30)
    local r
    for i = 1, L.rw("wMenuItemsList") do if L.rw("wMenuItemsList", i) == 7 then r = i end end  -- STARTMENUITEM_POKEGEAR
    if not r then L.die("no POKEGEAR row in the START menu") end
    local s1 = emu.framecount()
    while not L.after("PokeGear", s1) do
        if emu.framecount() - s1 > 1200 then L.die("PokeGear never ran") end
        L.pulse(L.rw("wMenuCursorY") == r and "A" or "Down")
    end
    local c0 = emu.framecount()
    while not (L.rw("wPokegearCard") == 2 and state() == 0x0a) do               -- POKEGEARCARD_PHONE, PHONEJOYPAD
        if emu.framecount() - c0 > 1200 then L.die("the Phone card never reached its joypad state") end
        L.pulse(L.rw("wPokegearCard") < 2 and "Right" or nil)
    end
    L.idle(40)
end
local function close_pokegear()
    local f0, since = emu.framecount(), nil
    while true do
        if emu.framecount() - f0 > 1500 then L.die("could not leave the Pokegear") end
        if L.ow_idle() then
            since = since or emu.framecount()
            if emu.framecount() - since > 40 then return end
            L.frame()
        else since = nil L.pulse("B") end
    end
end
local function cursor_to(index)                   -- index = scroll + cursor, 0-based, pressing Up/Down one at a time
    for _ = 1, 40 do
        local c, s = cur()
        if c + s == index then return end
        press(c + s < index and "Down" or "Up", 12)
    end
    L.die("cursor never reached index " .. index)
end
local function list_text() return table.concat(rows(3, 11), "\n") end

-- one SLink entry: A on the row -> Call/Cancel (no Delete) -> A on Call -> entry text, no call -> B -> list, cursor intact
local function open_slink(tag, index)
    begin_window(tag .. ".entry")
    cursor_to(index)
    local c, s = cur()
    local calls0, gate0, sub0 = L.hits.MakePhoneCallFromPokegear, L.hits.SlinkPhone_CallGate, L.hits.PokegearPhoneContactSubmenu
    local caller0, list_before = L.rw("wCurCaller"), plist()
    local readings = {before = observe("before")}
    press("A", 30)
    L.check(tag .. ": A opened the contact submenu", L.hits.PokegearPhoneContactSubmenu == sub0 + 1)
    local menu = table.concat(rows(6, 11), "\n")
    shot(tag .. "_submenu")
    L.check(tag .. ": submenu offers Call and Cancel", menu:find("Call", 1, true) and menu:find("Cancel", 1, true), menu)
    L.check(tag .. ": submenu offers no Delete (Mom/Elm style)", not menu:find("Delete", 1, true), menu)
    L.check(tag .. ": wPokegearPhoneSubmenuCursor on Call", L.rw("wPokegearPhoneSubmenuCursor") == 0)
    readings.submenu = observe("submenu")
    press("A", 10)
    wait(function() return L.hits.SlinkPhone_CallGate > gate0 end, 300, tag .. ": Call never reached SlinkPhone_CallGate")
    L.idle(120) -- native no-host panel closes its lease after 90 frames
    local box = table.concat(rows(12, 17), "\n")
    shot(tag .. "_entry")
    local sel = L.rw("wPokegearPhoneSelectedPerson")
    readings.call = observe("call")
    L.check(tag .. ": the SLink entry text box shows", box:find("SOUL LINK", 1, true) and box:find("NO CLIENT", 1, true) and L.hits.SlinkPanel > 0, box)
    L.check(tag .. ": no phone call placed (MakePhoneCallFromPokegear not entered)", L.hits.MakePhoneCallFromPokegear == calls0)
    L.check(tag .. ": wCurCaller untouched", L.rw("wCurCaller") == caller0, fmt("%02X", L.rw("wCurCaller")))
    L.check(tag .. ": selected person is the virtual id 38", sel == 38, sel)
    press("B", 10)
    wait(function() return state() == 0x0a end, 300, tag .. ": B did not return to PHONEJOYPAD")
    L.idle(40)
    local back = screen()
    shot(tag .. "_back")
    local c2, s2 = cur()
    readings.after_b = observe("after_b")
    L.check(tag .. ": back on the list, cursor/scroll intact", c2 == c and s2 == s, fmt("%d/%d -> %d/%d", c, s, c2, s2))
    L.check(tag .. ": back text is the native prompt", back:find("Whom do you want", 1, true) ~= nil)
    L.check(tag .. ": wPhoneList untouched by the SLink entry", plist() == list_before, plist())
    L.check(tag .. ": the cursor arrow sits on the SLink row", L.rw("wTilemap", (4 + 2 * c2) * 20 + 1) == ARROW
            and row(4 + 2 * c2):find("SLink:", 1, true) ~= nil, row(4 + 2 * c2))
    end_window()
    return {menu = menu, entry_box = box, back = back, cursor = {c2, s2}, readings = readings}
end

local function scenario(tag, bytes, expect_rows, on_screen)
    begin_stage(tag .. ".list")
    local before = plist()
    for i = 1, 5 do L.ww("wPhoneList", i - 1, bytes[i]) end
    L.log(fmt("[phone] SYNTH wPhoneList %s -> %s (%s)", before, plist(), tag))
    open_phone()
    local text = list_text()
    shot(tag .. "_list")
    L.log(fmt("[phone] %s list (wNumSetBits %d):\n%s", tag, L.rw("wNumSetBits"), text))
    for y, want_text in pairs(expect_rows) do
        L.check(fmt("%s: row %d reads %q", tag, y, want_text), row(y):find(want_text, 1, true) ~= nil, row(y))
    end
    on_screen = on_screen or 1   -- s4: the 5th row is below the fold until the native scroll
    L.check(fmt("%s: %d SLink row(s) on the first screen", tag, on_screen), count(text, "SLink:") == on_screen)
    observe("list")
    end_stage()
    return {tag = tag, phone_list = plist(), num_set_bits = L.rw("wNumSetBits"), list = text}
end

-- S0: no native contact -> SLink is the only row
local s0 = scenario("s0", {0, 0, 0, 0, 0}, {[4] = "SLink:", [5] = "   Soul Link"})
s0.entry = open_slink("s0", 0)
close_pokegear()
evidence.scenarios[#evidence.scenarios + 1] = s0

-- S1: Mom only
local s1 = scenario("s1", {0x01, 0, 0, 0, 0}, {[4] = "Mom:", [6] = "SLink:", [7] = "   Soul Link"})
s1.entry = open_slink("s1", 1)
close_pokegear()
evidence.scenarios[#evidence.scenarios + 1] = s1

-- S4: Mom, Prof.Elm, Joey, Wade -> SLink is the 5th row, reached by the native scroll
local s4 = scenario("s4", {0x09, 0xC0, 0, 0, 0}, {[4] = "Mom:", [6] = "Prof.Elm:", [8] = "Joey:", [10] = "Wade:"}, 0)
begin_window("s4.scroll")
s4.scroll_readings = {before = observe("before")}
cursor_to(4)
local c, s = cur()
local scrolled = list_text()
shot("s4_scrolled")
L.check("s4: Down past the 4th row scrolls (cursor 3, scroll 1)", c == 3 and s == 1, fmt("%d/%d", c, s))
L.check("s4: exactly one SLink row after the scroll", count(scrolled, "SLink:") == 1)
L.check("s4: scrolled list ends with the SLink row", row(10):find("SLink:", 1, true) ~= nil and row(4):find("Prof.Elm:", 1, true) ~= nil, scrolled)
s4.scrolled = scrolled
s4.scroll_readings.after = observe("after")
end_window()
s4.entry = open_slink("s4", 4)
close_pokegear()
evidence.scenarios[#evidence.scenarios + 1] = s4

-- S3: Mom, Prof.Elm, Joey -> SLink entry, then DELETE Joey natively, then a native call to Mom (control)
local s3 = scenario("s3", {0x09, 0x40, 0, 0, 0}, {[4] = "Mom:", [6] = "Prof.Elm:", [8] = "Joey:", [10] = "SLink:"})
s3.entry = open_slink("s3", 3)
begin_window("s3.delete")
cursor_to(2)
local sub0, del0, yn0 = L.hits.PokegearPhoneContactSubmenu, L.hits["PokegearPhoneContactSubmenu.Delete"], L.hits.YesNoBox
s3.deletion = {readings = {before = observe("before")}}
press("A", 30)
local menu = table.concat(rows(6, 11), "\n")
shot("s3_joey_submenu")
L.check("s3: Joey's submenu offers Delete", L.hits.PokegearPhoneContactSubmenu == sub0 + 1 and menu:find("Delete", 1, true) ~= nil, menu)
s3.deletion.readings.submenu = observe("submenu")
press("Down", 12)
press("A", 10)
wait(function() return L.hits.YesNoBox > yn0 end, 300, "s3: Delete never asked YES/NO")
L.idle(30)
shot("s3_delete_prompt")
s3.deletion.readings.confirm = observe("confirm")
press("A", 10)                                                      -- YES (the native default row)
wait(function() return plist() ~= "0940000000" end, 600, "s3: YES never deleted Joey")
L.idle(40)
local after = list_text()
shot("s3_after_delete")
L.log("[phone] s3 after deleting Joey:\n" .. after)
L.check("s3: Joey's native bit cleared (wPhoneList 0900000000)", plist() == "0900000000", plist())
L.check("s3: list after delete = Mom, Prof.Elm, SLink", row(4):find("Mom:", 1, true) and row(6):find("Prof.Elm:", 1, true)
        and row(8):find("SLink:", 1, true) and not row(10):find("%a"), after)
L.check("s3: the SLink row neither vanished nor duplicated", count(after, "SLink:") == 1)
L.check("s3: wNumSetBits = 3 (2 native + SLink)", L.rw("wNumSetBits") == 3, L.rw("wNumSetBits"))
s3.after_delete = after
s3.deletion.readings.after = observe("after")
end_window()
-- control: the native call path still rings Mom
begin_window("s3.native_call")
cursor_to(0)
local calls0 = L.hits.MakePhoneCallFromPokegear
s3.native_call = {readings = {before = observe("before")}}
press("A", 30)
s3.native_call.readings.submenu = observe("submenu")
press("A", 10)                                                      -- Call
wait(function() return L.hits.MakePhoneCallFromPokegear > calls0 end, 600, "s3: Mom's call never reached MakePhoneCallFromPokegear")
L.idle(60)
shot("s3_mom_call")
s3.native_call.readings.call = observe("call")
L.check("s3: a native contact still places a real call", L.rw("wCurCaller") == 1, L.rw("wCurCaller"))
local f0 = emu.framecount()
while state() ~= 0x0a do
    if emu.framecount() - f0 > 3000 then L.die("Mom's call never hung up") end
    L.pulse("A")
end
L.idle(60)
if L.hits.PokegearPhoneContactSubmenu > sub0 + 2 then press("B", 30) end   -- a trailing A opened a submenu: cancel it
L.check("s3: back on the Phone card after the call", state() == 0x0a)
s3.native_call.readings.after = observe("after")
end_window()
close_pokegear()
evidence.scenarios[#evidence.scenarios + 1] = s3

begin_stage("terminal")
local terminal = observe("terminal")
terminal.wram_size = memory.getmemorydomainsize("WRAM")
local terminal_bytes = {}
for address = 0, terminal.wram_size - 1 do terminal_bytes[#terminal_bytes + 1] = memory.read_u8(address, "WRAM") end
terminal.wram_hex = L.hex(terminal_bytes)
end_stage()
evidence.pokegear_flags_before = flags0
evidence.phone_list_at_boot = list0
evidence.hits = L.hits
write(L.RUN .. "/phone.json", J.encode(evidence))
L.finish("pol-phone")
