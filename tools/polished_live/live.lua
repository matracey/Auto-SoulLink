-- tools/polished_live/live.lua -- the Polished live stages, launched by `harness.py live`.
-- The REAL entry path runs the client: lua/slink.lua -> (GB route) lua/gen2/entry.lua detect_title ->
-- lua/gen2/run.lua -> Entry.build -> admit_polished/compose_polished. This driver only presses buttons and
-- registers READ-ONLY probes (frame/bank/SP/PC/stack reads). Client writes are recorded; acquisition quarantine is expected.
-- Stages (POL_STAGES, default 1234): 1 hello, 4 frame-wait stack fingerprint, 2 wild catch (+ run/fail-shake
-- negatives), 3 box arrival (full-party catch) + census + native save.
-- Capture-negative replay uses the same oracle as STAGE2; zero failed throws is not evidence.
-- capture_contract.json owns the check label shared with the release verifier.
local Oracle = {}
function Oracle.evaluate_failed_throws(throws)
    local count, hits, clean = 0, 0, true
    for _, t in ipairs(throws) do
        if not t.caught then
            count = count + 1
            hits = hits + t.cap_hits
            clean = clean and t.cap_hits == 0
        end
    end
    return {failed_throw_count = count, capture_hits = hits, ok = count > 0 and clean}
end
function Oracle.report_failed_throws(throws, lib)
    local contract = assert(lib.json.decode(lib.slurp(lib.ROOT .. "/tools/polished_live/capture_contract.json")))
    local name = assert(contract.failed_throw_check)
    local negative = Oracle.evaluate_failed_throws(throws)
    lib.check("STAGE2 " .. name, negative.ok,
              string.format("failed throws %d; capture hits during failed throws %d",
                            negative.failed_throw_count, negative.capture_hits))
    return negative
end
if POL_ORACLE_ONLY then return Oracle end
local L = dofile(os.getenv("SLINK_ROOT") .. "/tools/polished_live/pol_lib.lua")
local fmt = string.format
local J = L.json
-- POL_PREPARE_FULL=1: native catch + SAVE only, no client; creates a private full-party fixture.
local PREPARE = os.getenv("POL_PREPARE_FULL") == "1"
local STAGES = os.getenv("POL_STAGES") or "1234"
local function want(n) return STAGES:find(tostring(n), 1, true) ~= nil end
-- randomized-cartridge runs (R2/R3) reuse every stage on another map/kind: POL_MAP "group,number", POL_HEADER
-- "tileset,width,height", POL_WALK "lo,hi" (x bounds of the grass run), POL_EXPECT_KIND overlay|rand_overlay.
-- Stage 5 = POL_ENCOUNTERS wild battles fled (species/form/level read from wEnemyMon*), stage 6 = hunt POL_TARGET
-- "species,form" (flee anything else) and catch it into the party.
local function nums(s) local t = {} for v in s:gmatch("%d+") do t[#t + 1] = tonumber(v) end return t end
local MAP = nums(os.getenv("POL_MAP") or "24,3")
local HEADER = nums(os.getenv("POL_HEADER") or "1,30,9")
local WALK = nums(os.getenv("POL_WALK") or "46,51")
local EXPECT_KIND = os.getenv("POL_EXPECT_KIND") or "overlay"
client.speedmode(300)
L.log(fmt("[live] boot frame %d rom %s stages %s", emu.framecount(), gameinfo.getromhash(), STAGES))

-- ── probes (registered BEFORE the client so both see every hit) ─────────────────────────────────
local sites = J.decode(L.slurp(L.ROOT .. "/data/games/polished_crystal/engine_signals.json"))
local cap = sites.titles.polished_crystal.sites.capture_party
L.log(fmt("[live] capture_party site from the pack: %02X:%04X flat 0x%X expected %s", cap.bank, cap.addr,
          cap.rom_offset, cap.expected_hex))
local cap_hits, cap_other_bank = {}, 0
L.hook_at("capture_site", cap.bank, cap.addr, function(right_bank)
    if not right_bank then cap_other_bank = cap_other_bank + 1 return end
    cap_hits[#cap_hits + 1] = {frame = emu.framecount(), bank = L.rombank(), pc = emu.getregister("PC"),
        sp = emu.getregister("SP"), party_count = L.rw("wPartyCount"),
        last_species = L.rw("wPartyMons", (math.max(L.rw("wPartyCount"), 1) - 1) * 48),
        last_struct = L.hex(L.wbytes("wPartyMons", (math.max(L.rw("wPartyCount"), 1) - 1) * 48, 48))}
end)
for _, name in ipairs({"OWPlayerInput", "LoadBattleMenu", "BattleMenu_Run", "PokeBallEffect", "PokeBallEffect.caught",
                       "PokeBallEffect.SendToPC", "BlinkCursor", "YesNoBox", "StartMenu", "SaveMenu", "SaveGameData",
                       "StartBattle", "ExitBattle", "BattlePack", "TitleScreenMain", "MainMenu",
                       "SetInitialOptions.joypad_loop"}) do
    if L.SYM[name] then L.hook(name) end
end

-- party-count watcher (end-of-frame value) for the frame-alignment observation
local pc_changes, last_pc = {}, nil
-- Stage A (hello gate): end-of-frame overworld-gate bytes, written to gate_transitions.log ONLY when they change
-- (never per frame); `first` keeps the first frame of each milestone
local gate_log, gate_last, gate_lines, first = L.RUN .. "/gate_transitions.log", nil, 0, {}
local function gate_bytes()
    return L.rw("wMapStatus"), L.rw("wScriptRunning"), L.rw("wGameLogicPaused"), L.rw("wLinkMode"), L.rw("wBattleMode")
end
L.on_frame = function()
    local f = emu.framecount()
    local n = L.rw("wPartyCount")
    if last_pc ~= nil and n ~= last_pc then pc_changes[#pc_changes + 1] = {frame = f, from = last_pc, to = n} end
    last_pc = n
    local ms, sr, gp, lm, bm = gate_bytes()
    local running = ms == 2 and sr == 0 and gp == 0 and lm == 0
    local hello = SLINK_GEN2_CLIENT ~= nil and SLINK_GEN2_CLIENT.hello_sent == true
    local s = fmt("wMapStatus=%d wScriptRunning=%d wGameLogicPaused=%d wLinkMode=%d wBattleMode=%d running=%s hello_sent=%s",
                  ms, sr, gp, lm, bm, tostring(running), tostring(hello))
    if s ~= gate_last and gate_lines < 600 then
        local fh = io.open(gate_log, "a")
        if fh then fh:write(fmt("frame %d %s OWPlayerInput_hits=%d" .. string.char(10), f, s, L.hits.OWPlayerInput or 0)) fh:close() end
        gate_lines = gate_lines + 1
    end
    gate_last = s
    if not first.owpi and (L.hits.OWPlayerInput or 0) > 0 then first.owpi = L.hit.OWPlayerInput end
    if not first.running and running then first.running = f end
    if not first.hello and hello then first.hello = f end
end

-- ── the client, through the real entry ──────────────────────────────────────────────────────────
SLINK_HOST, SLINK_PORT, SLINK_PLAYER = os.getenv("SLINK_HOST"), tonumber(os.getenv("SLINK_PORT")), os.getenv("SLINK_PLAYER")
-- write tap: every Lua-originated memory write (the client's io.write_u8 is memory.write_u8) is counted and logged;
-- this driver itself writes nothing in the live run, so any count here is the client's
local lua_writes = {}
local G = dofile(L.ROOT .. "/tools/polished_live/recording_guard.lua")
memory = G.observe_memory(memory, function(k, ...)
    local a = {...}
    lua_writes[#lua_writes + 1] = fmt("%s(%s) frame %d", k, table.concat({tostring(a[1]), tostring(a[2]), tostring(a[3])}, ","), emu.framecount())
end)
local P, sent = {}, {hello = {}, capture = {}, other = {}}
local last_gen, ticks, gen_changes, last_battle_end = nil, 0, {}, nil
local function append(path, s) local f = io.open(path, "a") if f then f:write(s .. "\n") f:close() end end
if not PREPARE then
local t_build = os.clock()
dofile(L.ROOT .. "/lua/slink.lua")
L.log(fmt("[live] lua/slink.lua returned after %.1fs cpu; client %s", os.clock() - t_build, tostring(SLINK_GEN2_CLIENT ~= nil)))
if not SLINK_GEN2_CLIENT then L.die("the Gen 2 route did not start a client (see slink_lua.log)") end
P = SLINK_GEN2_PARTS
L.log(fmt("[live] parts: pack %s title %s kind %s qualification %s production_admitted %s rom %s",
          tostring(P.pack), tostring(P.title), tostring(P.artifact_kind), tostring(P.qualification),
          tostring(P.production_admitted), tostring(P.runtime_rom_sha1)))
L.check("admitted as the Polished " .. EXPECT_KIND .. " (DEV_OVERLAY_SHA1)", P.pack == "polished_crystal" and P.title == "polished"
        and P.artifact_kind == EXPECT_KIND and P.qualification == "DEV_OVERLAY_SHA1")
L.log(fmt("[live] admission: admitted_by %s overlay_sha1 %s", tostring(P.admitted_by), tostring(P.overlay_sha1)))

-- wire tap: the connector module is looked up per call (run.lua), so wrapping M.send/M.receive sees every line
local C = package.loaded["connector"]
local sent_path, recv_path = L.RUN .. "/sent.jsonl", L.RUN .. "/recv.jsonl"
local orig_send, orig_recv = C.send, C.receive
C.send = function(line, ...)
    local ok, msg = pcall(J.decode, line)
    local ev = ok and type(msg) == "table" and msg.event or "?"
    if ev == "tick" then
        ticks = ticks + 1
        local gen = msg.pc_boxes_generation
        if msg.pc_boxes ~= nil and gen ~= last_gen then
            append(sent_path, fmt('{"frame":%d,"line":%s}', emu.framecount(), line))
            last_gen = gen
            gen_changes[#gen_changes + 1] = {frame = emu.framecount(), gen = gen, n = #msg.pc_boxes}
        end
    else
        append(sent_path, fmt('{"frame":%d,"line":%s}', emu.framecount(), line))
        local bucket = sent[ev] or sent.other
        bucket[#bucket + 1] = {frame = emu.framecount(), msg = msg}
    end
    return orig_send(line, ...)
end
C.receive = function(...)
    local line = orig_recv(...)
    if line ~= nil then append(recv_path, fmt('{"frame":%d,"len":%d,"head":%q}', emu.framecount(), #line, line:sub(1, 600))) end
    return line
end

end -- client construction is deliberately absent in native fixture preparation

-- ── continue into the game ──────────────────────────────────────────────────────────────────────
if not L.to_overworld(MAP[1], MAP[2], 60, 8000, "continue") then L.die(fmt("CONTINUE did not reach map %d:%d", MAP[1], MAP[2])) end
local first_ow = emu.framecount() - 60   -- to_overworld returns after 60 quiet idle frames
L.log(fmt("[live] loaded header: tileset %d width %d height %d (want %d, %dx%d)", L.rw("wMapTileset"),
          L.rw("wMapWidth"), L.rw("wMapHeight"), HEADER[1], HEADER[2], HEADER[3]))
L.check("map header is the target map", L.rw("wMapTileset") == HEADER[1] and L.rw("wMapWidth") == HEADER[2]
        and L.rw("wMapHeight") == HEADER[3])
client.screenshot(L.RUN .. "/continue.png")
local x0, y0 = L.rw("wXCoord"), L.rw("wYCoord")
L.log(fmt("[live] on map %d:%d at (%d,%d) party %d", MAP[1], MAP[2], x0, y0, L.rw("wPartyCount")))

-- ── STAGE 1: hello ──────────────────────────────────────────────────────────────────────────────
if not PREPARE then
local f0 = emu.framecount()
while not SLINK_GEN2_CLIENT.hello_sent do
    if emu.framecount() - f0 > 1800 then break end
    L.frame()
end
local hello = sent.hello[1]
L.check("STAGE1 client sent its hello", hello ~= nil, hello and ("frame " .. hello.frame) or "none in 1800 frames")
if hello then
    local m = hello.msg
    local species = {}
    for _, e in ipairs(m.party or {}) do species[#species + 1] = tostring(e.species_id or e.species) .. ":" .. tostring(e.key) end
    L.log(fmt("[live] STAGE1 hello: rom_type %s foundation %s kind %s sha1 %s abi %s panel %s party %d [%s] "
              .. "pc_boxes %d gen %s area %s/%s ot %s name %s", tostring(m.rom_type), tostring(m.foundation),
              tostring(m.artifact_kind), tostring(m.rom_sha1), tostring(m.companion_abi), tostring(m.panel),
              #(m.party or {}), table.concat(species, " "), #(m.pc_boxes or {}), tostring(m.pc_boxes_generation),
              tostring(m.area_id), tostring(m.loc_name), tostring(m.ot_id), tostring(m.trainer_name)))
    L.check("STAGE1 hello identity polished_crystal/gen2_polished/" .. EXPECT_KIND,
            m.rom_type == "polished_crystal" and m.foundation == "gen2_polished" and m.artifact_kind == EXPECT_KIND)
    L.check("STAGE1 hello rom_sha1 is the client rehash", m.rom_sha1 == P.runtime_rom_sha1, tostring(m.rom_sha1))
    L.check("STAGE1 hello party matches the fixture", #(m.party or {}) == tonumber(os.getenv("POL_PARTY_COUNT") or "5"), #(m.party or {}))
    L.log(fmt("[live] STAGE1 hello context: hello frame %d, TitleScreenMain last %s, MainMenu last %s, first-idle %s",
              hello.frame, tostring(L.hit.TitleScreenMain), tostring(L.hit.MainMenu), tostring(first_ow)))
    L.log(fmt("[live] STAGEA first OWPlayerInput frame %s, first gate-running end-of-frame %s, hello_sent first seen %s, hello wire frame %d",
              tostring(first.owpi), tostring(first.running), tostring(first.hello), hello.frame))
    L.check("STAGEA hello leaves after the first idle overworld frame (OWPlayerInput)",
            first.owpi ~= nil and hello.frame >= first.owpi, fmt("hello %d vs OWPlayerInput %s", hello.frame, tostring(first.owpi)))
    L.check("STAGEA hello leaves after the gate first reads running", first.running ~= nil and hello.frame >= first.running,
            fmt("hello %d vs running %s", hello.frame, tostring(first.running)))
    L.check("STAGEA hello is not from the main menu", L.hit.MainMenu == nil or hello.frame > L.hit.MainMenu + 30,
            fmt("hello %d vs MainMenu last %s", hello.frame, tostring(L.hit.MainMenu)))
end
L.idle(120) -- let the server reply land
end

-- ── STAGE 4: frame-wait stack fingerprint (read-only) ──────────────────────────────────────────
if want(4) then
    local sigs, order, samples = {}, {}, 0
    L.hook("SlinkDelayFrameBridge", function()
        if not L.recent("OWPlayerInput", 1) or samples >= 4000 then return end
        local sp = emu.getregister("SP")
        local bytes = {}
        for i = 0, 31 do bytes[#bytes + 1] = L.bus((sp + i) & 0xFFFF) end
        local key = fmt("%04X|%s|b%02X|s%d", sp, L.hex(bytes), L.rombank(), L.bus(0xFF70) % 8)
        if not sigs[key] then sigs[key] = 0 order[#order + 1] = key end
        sigs[key] = sigs[key] + 1
        samples = samples + 1
    end)
    L.idle(300)
    L.unhook("SlinkDelayFrameBridge")
    L.log(fmt("[live] STAGE4 idle-overworld DelayFrame bridge entries: %d samples, %d distinct stacks", samples, #order))
    for _, key in ipairs(order) do L.log(fmt("[live] STAGE4 stack %s x%d", key, sigs[key])) end
    L.check("STAGE4 bridge observed from the idle overworld", samples > 0)
end

-- ── battles ─────────────────────────────────────────────────────────────────────────────────────
local function walk_for_battle(label)
    local f, dir = emu.framecount(), "Left"
    while not L.after("StartBattle", f) do
        if emu.framecount() - f > 20000 then L.die(label .. ": no wild battle in 20000 frames") end
        local x = L.rw("wXCoord")
        if x <= WALK[1] then dir = "Right" elseif x >= WALK[2] then dir = "Left" end
        if L.recent("BlinkCursor", 2) then L.pulse("A") else L.frame({[dir] = true}) end
    end
    L.idle(2)
    if label == "STAGE2-run" then client.screenshot(L.RUN .. "/battle1.png") end
    -- wEnemyMon* is not loaded yet here (it still holds the previous battle's mon); battle() logs the real foe
    L.log(fmt("[live] %s: wild battle at frame %d (walk %d frames) type %d", label,
              emu.framecount(), emu.framecount() - f, L.rw("wBattleType")))
end

-- the wild mon as the battle holds it: wEnemyMonForm = form (bits 0-4) | extspecies (bit 5) | gender/egg (6-7)
local function enemy()
    local fb = L.rw("wEnemyMonForm")
    local g, n = L.map()
    return {species = L.rw("wEnemyMonSpecies") | ((fb & 0x20) << 3), form = fb & 0x1F, form_byte = fb,
            level = L.rw("wEnemyMonLevel"), frame = emu.framecount(), map = {g, n}}
end

-- action "run" | "throw" | function(foe) -> "run"/"throw" (decided at the first battle menu, when wEnemyMon* is
-- loaded); returns the throw outcomes, the capture-site hits and the wild mon
local BALL_POCKET = 2  -- wCurPocket is 0-based (pack.asm: cp TM_HM - 1); BALL = 3
local function battle(action, label)
    local foe = nil
    -- handled starts at this battle's first frame: L.hit.LoadBattleMenu is never cleared, so a menu hit from the
    -- PREVIOUS battle must not count (it made enemy() read the last battle's wEnemyMon*)
    local f_start = emu.framecount()
    local handled, last_act = f_start, f_start
    local throws, prepped, prep_pulses = {}, -1, 0
    local caps0 = #cap_hits
    -- the battle is over once the overworld takes input again (wBattleMode is not a reliable end marker)
    while not L.after("OWPlayerInput", f_start) do
        local f = emu.framecount()
        if f - f_start > tonumber(os.getenv("POL_BATTLE_FRAMES") or "9000") then
            client.screenshot(L.RUN .. "/stuck_" .. label .. ".png")
            L.log(fmt("[live] %s STUCK: battle mode %d, last LoadBattleMenu %s Run %s Ball %s Pack %s Blink %s YesNo %s ExitBattle %s, menu cursor %d pocket %d",
                      label, L.rw("wBattleMode"), tostring(L.hit.LoadBattleMenu), tostring(L.hit.BattleMenu_Run),
                      tostring(L.hit.PokeBallEffect), tostring(L.hit.BattlePack), tostring(L.hit.BlinkCursor),
                      tostring(L.hit.YesNoBox), tostring(L.hit.ExitBattle), L.rw("wMenuCursorY"), L.rw("wCurPocket")))
            L.die(label .. ": battle did not end")
        end
        local menu, btn = L.hit.LoadBattleMenu, nil
        if menu and menu > handled and f - menu >= 6 and not foe then
            foe = enemy()
            if type(action) == "function" then action = action(foe) end
            L.log(fmt("[live] %s wild mon: species %d form %d (byte %02X) level %d -> %s", label, foe.species, foe.form,
                      foe.form_byte, foe.level, action))
        end
        if menu and menu > handled and f - menu >= 6 then
            if L.after("PokeBallEffect", menu) or L.after("BattleMenu_Run", menu) then
                handled = menu
            elseif L.after("BattlePack", menu) then                         -- bag open: go to the ball pocket, use
                btn = L.rw("wCurPocket") == BALL_POCKET and "A" or "Right"
            elseif prepped ~= menu then                                     -- menu.asm: B puts the cursor on Run,
                btn = action == "run" and "B" or "Start"                   -- START on Bag (+QUICK_PACK); A confirms
                if (emu.framecount() % 16) < 2 then prep_pulses = prep_pulses + 1 end
                if prep_pulses >= 2 then prepped = menu prep_pulses = 0 end
            else btn = "A" end
        elseif L.recent("YesNoBox", 40) then btn = "B"                      -- nickname? NO
        elseif L.recent("BlinkCursor", 2) then btn = "A"
        elseif f - math.max(last_act, L.hit.BlinkCursor or 0, L.hit.LoadBattleMenu or 0) > 90 then btn = "B" end
        if btn and (f % 16) < 2 then last_act = f end                     -- only an emitted press counts
        local before = L.hits.PokeBallEffect
        L.pulse(btn)
        if L.hits.PokeBallEffect > before then
            throws[#throws + 1] = {frame = L.hit.PokeBallEffect, caps_before = #cap_hits}
        end
    end
    local f_end = emu.framecount()
    last_battle_end = f_end
    -- outcome per throw: caught iff PokeBallEffect.caught ran after that throw and before the next
    for i, t in ipairs(throws) do
        local nxt = throws[i + 1] and throws[i + 1].frame or f_end + 1
        local c = L.hit["PokeBallEffect.caught"]
        t.caught = c ~= nil and c >= t.frame and c < nxt
        t.to_pc = L.hit["PokeBallEffect.SendToPC"] ~= nil and L.hit["PokeBallEffect.SendToPC"] >= t.frame
                  and L.hit["PokeBallEffect.SendToPC"] < nxt
        t.cap_hits = (throws[i + 1] and throws[i + 1].caps_before or #cap_hits) - t.caps_before
        L.log(fmt("[live] %s throw %d at frame %d: caught %s to_pc %s capture-site bank-3 hits %d", label, i, t.frame,
                  tostring(t.caught), tostring(t.to_pc), t.cap_hits))
    end
    L.log(fmt("[live] %s: battle ended frame %d (%d frames), throws %d, capture-site bank-3 hits %d, other-bank hits so far %d, party %d",
              label, f_end, f_end - f_start, #throws, #cap_hits - caps0, cap_other_bank, L.rw("wPartyCount")))
    L.to_overworld(nil, nil, 30, 3000, label .. "-after")
    return throws, #cap_hits - caps0, foe
end

local function native_save()
    -- Native menu SAVE; no RAM edits. Client, when present, keeps scanning.
    local s0 = emu.framecount()
    while not L.after("StartMenu", s0) do
        if emu.framecount() - s0 > 600 then L.log("[live] STAGE3 START menu never opened") break end
        L.pulse("Start")
    end
    L.idle(30)
    local row
    for i = 1, L.rw("wMenuItemsList") do if L.rw("wMenuItemsList", i) == 4 then row = i end end
    local s1 = emu.framecount()
    local paused_frames = 0
    while row and not L.after("SaveGameData", s1) and emu.framecount() - s1 < 2400 do
        if L.after("SaveMenu", s1) then L.pulse("A") else L.pulse(L.rw("wMenuCursorY") == row and "A" or "Down") end
    end
    for _ = 1, 600 do
        if L.rw("wGameLogicPaused") ~= 0 then paused_frames = paused_frames + 1 end
        if L.recent("BlinkCursor", 2) then L.pulse("A") else L.frame() end
    end
    L.log(fmt("[live] STAGE3 native save: SaveGameData %s, wGameLogicPaused nonzero on %d sampled end-of-frames",
              tostring(L.hit.SaveGameData), paused_frames))
    L.to_overworld(nil, nil, 60, 3000, "STAGE3-after-save")
    L.idle(600)
    L.check("native SAVE executed", L.after("SaveGameData", s1))
end

if want(2) then
    -- negative 1: escape
    walk_for_battle("STAGE2-run")
    local _, hits = battle("run", "STAGE2-run")
    L.check("STAGE2 escape does not fire the capture site", hits == 0, hits)
    L.check("STAGE2 escape took the Run path", L.hits.BattleMenu_Run > 0, L.hits.BattleMenu_Run)

    -- catch into the party (5 -> 6), failed shakes on the way are the second negative
    local caught = false
    local observed_throws = {}
    for attempt = 1, 4 do
        walk_for_battle("STAGE2-catch" .. attempt)
        local party_before = L.rw("wPartyCount")
        local captures_before = #sent.capture
        local pcc_before = #pc_changes
        local throws, hits = battle("throw", "STAGE2-catch" .. attempt)
        for _, t in ipairs(throws) do observed_throws[#observed_throws + 1] = t end
        local last = throws[#throws]
        if last and last.caught then
            caught = true
            local hit = cap_hits[#cap_hits]
            L.check("STAGE2 the party catch fires the capture site exactly once", hits == 1, hits)
            if hit then
                L.log(fmt("[live] STAGE2 capture-site hit: frame %d bank %02X PC %04X SP %04X party_count@hit %d last-slot species@hit %d",
                          hit.frame, hit.bank, hit.pc, hit.sp, hit.party_count, hit.last_species))
                L.check("STAGE2 hROMBank == 3 at the hit", hit.bank == 3, hit.bank)
            end
            for i = pcc_before + 1, #pc_changes do
                local c = pc_changes[i]
                L.log(fmt("[live] STAGE2 wPartyCount %d -> %d observed at end of frame %d (hit frame %s, delta %s)",
                          c.from, c.to, c.frame, hit and hit.frame or "?", hit and (c.frame - hit.frame) or "?"))
            end
            L.idle(240)
            local cap_ev = sent.capture[captures_before + 1]
            if not PREPARE then
            L.check("STAGE2 client emitted `capture`", cap_ev ~= nil,
                    cap_ev and fmt("frame %d (hit %+d frames)", cap_ev.frame, cap_ev.frame - (hit and hit.frame or 0)) or "none")
            if cap_ev then L.log("[live] STAGE2 capture line: " .. J.encode(cap_ev.msg)) end
            end
            L.check("STAGE2 native capture grew party 5 -> 6 before quarantine", party_before == 5 and hit ~= nil and hit.party_count == 6,
                    party_before .. " -> " .. L.rw("wPartyCount"))
            break
        end
    end
    L.check("STAGE2 a wild mon was caught into the party", caught)
    local negative = Oracle.report_failed_throws(observed_throws, L)
    local nf = assert(io.open(L.RUN .. "/capture_negative.json", "w"))
    nf:write(J.encode(negative)) nf:close()
end

if PREPARE then
    L.check("preparation has six party mons", L.rw("wPartyCount") == 6)
    native_save()
    L.check("native preparation has no client or Lua memory writes", SLINK_GEN2_CLIENT == nil and #lua_writes == 0)
    L.finish("native-full-party-preparation")
end

if want(3) then
    -- box arrival: the party is full, so the native catch takes PokeBallEffect.SendToPC (capture site must NOT fire)
    local gen_before = last_gen
    local mark = #gen_changes   -- census generations before the box catch (the battle-end one is after this)
    local caught = false
    for attempt = 1, 4 do
        if L.rw("wPartyCount") < 6 then L.log("[live] STAGE3 party not full; box route unavailable") break end
        walk_for_battle("STAGE3-catch" .. attempt)
        local throws, hits = battle("throw", "STAGE3-catch" .. attempt)
        local last = throws[#throws]
        if last and last.caught then
            caught = true
            L.check("STAGE3 the full-party catch went to the PC", last.to_pc == true)
            L.check("STAGE3 the box catch does NOT fire the capture site", hits == 0, hits)
            break
        end
    end
    L.check("STAGE3 a wild mon reached a box", caught)
    -- wait for a tick whose census carries the box mon (no reboot: same session as the catch)
    local f, seen = emu.framecount(), nil
    for i = mark + 1, #gen_changes do if not seen and gen_changes[i].n > 0 then seen = gen_changes[i] end end
    while not seen and emu.framecount() - f < 2400 do
        local g = gen_changes[#gen_changes]
        if #gen_changes > mark and g.n > 0 then seen = g end
        L.frame()
    end
    for _, g in ipairs(gen_changes) do
        L.log(fmt("[live] STAGE3 census generation %s at frame %d: pc_boxes %d", tostring(g.gen), g.frame, g.n))
    end
    L.log(fmt("[live] STAGE3 box-catch battle ended (overworld input) at frame %s", tostring(last_battle_end)))
    L.check("STAGE3 a tick carried the box mon in pc_boxes without a reboot", seen ~= nil,
            seen and fmt("generation %s frame %d (%+d frames after the battle end)", tostring(seen.gen), seen.frame,
                         seen.frame - (last_battle_end or 0)) or fmt("before %s after %s (ticks %d)", tostring(gen_before), tostring(last_gen), ticks))
    -- the battle-end trigger, not the 1800-frame periodic: the first box-carrying census lands within two ticks
    L.check("STAGE3 the box census followed the battle end (within 60 frames, periodic is 1800)",
            seen ~= nil and last_battle_end ~= nil and seen.frame - last_battle_end <= 60 and seen.frame >= last_battle_end - 30,
            seen and (seen.frame - (last_battle_end or 0)) or "none")
    native_save()
end

-- ── STAGE 5: wild encounters on a randomized cartridge (fled; the facts are wEnemyMon* bytes) ──────
local enc_path = L.RUN .. "/encounters.jsonl"
if want(5) then
    local n = tonumber(os.getenv("POL_ENCOUNTERS") or "5")
    for i = 1, n do
        walk_for_battle("STAGE5-" .. i)
        local _, hits, foe = battle("run", "STAGE5-" .. i)
        if foe then append(enc_path, J.encode({stage = 5, i = i, foe = foe})) end
        L.check("STAGE5 a fled encounter does not fire the capture site", hits == 0, hits)
    end
end

-- ── STAGE 6: hunt one (species, form) and catch it into the party ───────────────────────────────
if want(6) then
    local tgt = nums(os.getenv("POL_TARGET") or "53,2")
    local caught, seen = false, 0
    for i = 1, tonumber(os.getenv("POL_HUNT") or "30") do
        if caught then break end
        walk_for_battle("STAGE6-" .. i)
        local party_before, captures_before = L.rw("wPartyCount"), #sent.capture
        local throws, hits, foe = battle(function(f)
            return (f.species == tgt[1] and f.form == tgt[2]) and "throw" or "run" end, "STAGE6-" .. i)
        if foe then append(enc_path, J.encode({stage = 6, i = i, foe = foe, throws = #throws})) end
        if foe and foe.species == tgt[1] and foe.form == tgt[2] then
            seen = seen + 1
            local last = throws[#throws]
            if last and last.caught then
                caught = true
                L.check("STAGE6 the variant catch fires the capture site exactly once", hits == 1, hits)
                local hit = cap_hits[#cap_hits]
                if hit then
                    L.log(fmt("[live] STAGE6 capture-site hit: frame %d bank %02X PC %04X SP %04X party_count@hit %d",
                              hit.frame, hit.bank, hit.pc, hit.sp, hit.party_count))
                end
                L.idle(240)
                local cap_ev = sent.capture[captures_before + 1]
                L.check("STAGE6 client emitted capture", cap_ev ~= nil)
                if cap_ev then L.log("[live] STAGE6 capture line: " .. J.encode(cap_ev.msg)) end
                L.check("STAGE6 native party grew by one before quarantine", hit ~= nil and hit.party_count == party_before + 1,
                        party_before .. " -> " .. L.rw("wPartyCount"))
                local s = party_before
                L.log(fmt("[live] STAGE6 capture-time party slot %d: struct bytes %s", s + 1, hit and hit.last_struct or "missing"))
            else
                L.check("STAGE6 failed throws never fire the capture site", hits == 0, hits)
            end
        end
    end
    L.check(fmt("STAGE6 caught the target %d form %d (%d seen)", tgt[1], tgt[2], seen), caught)
end

-- ── wrap-up ─────────────────────────────────────────────────────────────────────────────────────
local wf = assert(io.open(L.RUN .. "/client_writes.json", "w"))
wf:write(J.encode(lua_writes)) wf:close()
if not want(2) and not want(3) and not want(6) then
    L.check("no Lua-originated memory write in observation-only stages", #lua_writes == 0, #lua_writes)
else
    -- Capture quarantine is now enabled. This acquisition probe records its writes;
    -- exact writer byte qualification belongs to writes.lua, not a zero-write claim.
    L.log(fmt("[live] acquisition-stage client writes recorded: %d (not a write-path qualification)", #lua_writes))
end
local pw = P.panel_writes and P.panel_writes.log or {}
L.check("the client's panel write path recorded no write attempt", #pw == 0, #pw)
local st = SLINK_GEN2_CLIENT.signals and SLINK_GEN2_CLIENT.signals.status and SLINK_GEN2_CLIENT.signals:status()
if st then L.log("[live] signals status: " .. J.encode(st)) end
L.log(fmt("[live] totals: capture-site bank-3 hits %d, other-bank hits %d, throws %d, caught %d, to_pc %d, ticks %d",
          #cap_hits, cap_other_bank, L.hits.PokeBallEffect, L.hits["PokeBallEffect.caught"],
          L.hits["PokeBallEffect.SendToPC"], ticks))
L.finish("polished-live")
