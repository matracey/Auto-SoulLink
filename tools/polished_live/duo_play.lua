-- tools/polished_live/duo_play.lua -- ONE side of the two-instance Polished Soul Link PLAY scenarios, launched by
-- `duo.py --scenario NAME|all`. The REAL client runs through the real entry (lua/slink.lua -> gen2 ->
-- compose_polished); this driver presses buttons, reads, and executes the runner's STEPS one at a time.
--
-- Step protocol (file based; the runner is tools/polished_live/duo.py play_main):
--   the runner writes <POL_RUN>/step_<k>.json = {"op": ..., ...} for k = 1, 2, ... in order;
--   this driver polls for the next k every 30 frames while the client keeps ticking, runs it, and appends
--   PLAY_STEP {"k":k,"op":...,"ok":...,...} to POL_OUT. Ops:
--     catch                     NATIVE wild catch on the fixture's Route 29 grass (walk, Bag -> Ball, throw; never
--                               Run: a fled battle would send no_catch and dead-zone the area). Result: the client's
--                               own `capture` line (key, area_id) and the capture-site hit count.
--     snapshot                  full cartridge read-back: keyed 48-byte party records and 11-byte OT/nickname
--                               arrays (status/moves/PP/item/stats included), plus the physical box/slot/key census.
--     await_cmd {cmd,key,frames} wait until the SERVER's reply delivered `cmd` (for `key`), let the client run it at
--                               its overworld hold, then snapshot. The command is never executed by this driver.
--     synth_hp0 {keys|all}      SYNTH SETUP (disclosed): poke current HP to 0 in the named party records (WRAM),
--                               then drop the client's TCP session (connector.disconnect) so its native reconnect
--                               re-sends hello carrying that party. Every SYNTH byte is listed in the result and is
--                               written through the UNTAPPED writer, so the client's own write count stays clean.
--     synth_hp1 {keys|all,lead} disclosed HP=1 setup and exact party/name-array lead swap; no reconnect.
--     lose_native {key,all,frames} native wild battle inputs only; records faint/copyback/whiteout witnesses.
--     idle {frames}
--     stop                      finish (exit marker RESULT: ... duo-play-side-<role>)
-- Bounds: 1800-frame NPC stall while walking, 9000 frames per battle, DUO_HOLD_CAP total frames; no per-frame log.
local L = dofile(os.getenv("SLINK_ROOT") .. "/tools/polished_live/pol_lib.lua")
local fmt, J = string.format, L.json
local ROLE = os.getenv("DUO_ROLE") or "?"
local HOLD_CAP = tonumber(os.getenv("DUO_HOLD_CAP")) or 400000
local STALL = 1800
local WALK = {46, 51}            -- Route 29 grass run on the warp fixture's row (live.lua POL_WALK default)
local STRIDE, STATUS_OFF, HP_OFF, MAXHP_OFF = 48, 32, 34, 36
-- The normal harness exports a subset. Resolve additional labels from the same pinned overlay sym,
-- not guessed addresses. Native SwapMonAndMail has no parallel species-list array in Polished.
for bank, addr, name in L.slurp(L.ROOT .. "/data/polished/polished_slink.sym"):gmatch("(%x+):(%x+)%s+([^\r\n]+)") do
    if not L.SYM[name] then L.SYM[name] = {tonumber(bank, 16), tonumber(addr, 16)} end
end
local party_evidence
local reports, force_readbacks = {}, {}
local witnesses = {battle_faint = {}, faint_copyback = {}, whiteout = {}, heal_party = {}, hp_transitions = {}}
local hook_errors, native_ui, native_ui_frame = {}, nil, 0
local function ui(kind)
    return function(right_bank)
        if right_bank then native_ui, native_ui_frame = kind, emu.framecount() end
    end
end
client.speedmode(300)
L.log(fmt("[play %s] boot frame %d rom %s", ROLE, emu.framecount(), gameinfo.getromhash()))

-- UNTAPPED writer: disclosed synth_hp0/synth_hp1 setup only; every byte is reported separately.
local raw_write_u8 = memory.write_u8
local client_writes = {}
local G = dofile(L.ROOT .. "/tools/polished_live/recording_guard.lua")
memory = G.observe_memory(memory, function(k, ...)
    local a = {...}
    client_writes[#client_writes + 1] = fmt("%s(%s,%s,%s) frame %d", k, tostring(a[1]), tostring(a[2]),
                                            tostring(a[3]), emu.framecount())
end)

-- read-only exec probes, registered BEFORE the client exactly as live.lua does
local sites = J.decode(L.slurp(L.ROOT .. "/data/games/polished_crystal/engine_signals.json"))
local cap = sites.titles.polished_crystal.sites.capture_party
local cap_hits = 0
L.hook_at("capture_site", cap.bank, cap.addr, function(right_bank) if right_bank then cap_hits = cap_hits + 1 end end)
for _, name in ipairs({"OWPlayerInput", "SetInitialOptions.joypad_loop", "LoadBattleMenu", "BattleMenu_Run",
                       "PokeBallEffect", "PokeBallEffect.caught", "PokeBallEffect.SendToPC", "BlinkCursor", "YesNoBox",
                       "StartBattle", "ExitBattle", "BattlePack"}) do
    if L.SYM[name] then L.hook(name) end
end
for name, kind in pairs({LoadBattleMenu = "root", SelectBattleMon = "party",
        ["BattleMenuPKMN_Loop.GetMenu"] = "switch_menu", ["MoveSelectionScreen.menu_loop"] = "move",
        YesNoBox = "yesno", BattleTurn = "turn", TryPlayerSwitch = "turn", BattleMenu_Run = "post_run",
        ["MoveSelectionScreen.use_move"] = "turn"}) do
    if L.ids[name] then L.unhook(name) end
    if L.SYM[name] then L.hook(name, ui(kind)) end
end
for _, name in ipairs({"BattleMenu_Fight", "SendInUserPkmn", "HealParty"}) do
    if L.SYM[name] then L.hook(name) end
end
local function rom_matches(offset, hex)
    return L.hex((function()
        local bytes = {}
        for i = 0, #hex / 2 - 1 do bytes[#bytes + 1] = memory.read_u8(offset + i, "ROM") end
        return bytes
    end)()):upper() == hex:upper()
end
local function observe_site(kind, site)
    L.hook_at("duo_" .. kind, site.bank, site.addr, function(right_bank)
        if not right_bank or emu.getregister("PC") ~= site.addr or not party_evidence then return end
        local ok, why = pcall(function()
            if not rom_matches(site.rom_offset, site.expected_hex) then error("site bytes changed") end
            if kind == "whiteout" then
                for _, guard in ipairs(site.guards.memory_equals) do
                    local v = L.bus(guard.addr)
                    if guard.width == 2 then v = v + 256 * L.bus(guard.addr + 1) end
                    if v ~= guard.value then return end
                end
                local ctx = site.guards.script_context
                if not rom_matches(ctx.bank * 0x4000 + ctx.addr - 0x4000, ctx.expected_hex) then
                    error("whiteout script bytes changed")
                end
            end
            local row = {frame = emu.framecount(), site = site.symbol, pc = site.addr, bank = L.rombank(),
                         qualified = true, expected_hex = site.expected_hex, party = party_evidence(),
                         slot = L.rw("wCurBattleMon"),
                         battle_hp = L.rw("wBattleMonHP") * 256 + L.rw("wBattleMonHP", 1)}
            -- ResolveFaints runs on healthy turns too: retain only actual battle HP0 witnesses.
            if kind == "whiteout" or row.battle_hp == 0 then
                witnesses[kind][#witnesses[kind] + 1] = row
            end
        end)
        if not ok then hook_errors[#hook_errors + 1] = {frame = emu.framecount(), site = kind, why = tostring(why)} end
    end)
end
observe_site("battle_faint", sites.titles.polished_crystal.sites.battle_faint)
observe_site("faint_copyback", sites.titles.polished_crystal.sites.battle_faint_copyback_return)
observe_site("whiteout", sites.titles.polished_crystal.sites.whiteout_before_heal)
if L.SYM.HealParty then
    L.unhook("HealParty")
    L.hook("HealParty", function(right_bank)
        if right_bank and party_evidence then
            witnesses.heal_party[#witnesses.heal_party + 1] = {frame = emu.framecount(), site = "HealParty", party = party_evidence()}
        end
    end)
end

SLINK_HOST, SLINK_PORT, SLINK_PLAYER = os.getenv("SLINK_HOST"), tonumber(os.getenv("SLINK_PORT")), os.getenv("SLINK_PLAYER")
dofile(L.ROOT .. "/lua/slink.lua")
if not SLINK_GEN2_CLIENT then L.die("the Gen 2 route did not start a client") end
local P = SLINK_GEN2_PARTS
L.check("admitted as the Polished overlay (DEV_OVERLAY_SHA1)", P.pack == "polished_crystal" and P.title == "polished"
        and P.artifact_kind == "overlay" and P.qualification == "DEV_OVERLAY_SHA1")
local okpm, PM = pcall(dofile, L.ROOT .. "/lua/gen2/polished.lua")
if not okpm then PM = nil end

-- Read-only wire tap: real sends, never altered; physical party and transmitted party are distinct evidence.
local C = package.loaded["connector"]
local hellos, captures, received = {}, {}, {}
local orig_send, orig_recv = C.send, C.receive
C.send = function(line, ...)
    local ok, msg = pcall(J.decode, line)
    if ok and type(msg) == "table" then
        if msg.event == "hello" then
            local party = {}
            for _, e in ipairs(msg.party or {}) do party[#party + 1] = {key = e.key, hp = e.hp} end
            hellos[#hellos + 1] = {frame = emu.framecount(), party = party}
        elseif msg.event == "capture" then
            captures[#captures + 1] = {frame = emu.framecount(), key = msg.key, area_id = msg.area_id,
                                       species_id = msg.species_id, level = msg.level, in_box = msg.in_box}
        elseif msg.event == "faint" or msg.event == "whiteout" then
            reports[#reports + 1] = {event = msg.event, key = msg.key, frame = emu.framecount(), seq = msg.seq,
                party = party_evidence and party_evidence() or {}, wire_party = msg.party,
                wire_has_party = type(msg.party) == "table"}
        end
    end
    return orig_send(line, ...)
end
C.receive = function(...)
    local line = orig_recv(...)
    if line ~= nil then
        local ok, msg = pcall(J.decode, line)
        if ok and type(msg) == "table" and type(msg.commands) == "table" then
            for _, c in ipairs(msg.commands) do
                if type(c) == "table" and c.cmd ~= "noop" then
                    received[#received + 1] = {frame = emu.framecount(), cmd = tostring(c.cmd), key = c.key,
                                              seq = c.seq, received_index = #received + 1}
                end
            end
        end
    end
    return line
end

-- ── cartridge read-back ──────────────────────────────────────────────────────────────────────────
-- party records read (and SYNTH-poked) in the WRAM domain at the symbol's own bank (L.woff), never through
-- whatever WRAMX bank SVBK happens to map on the System Bus
local function pbyte(slot, off) return L.rw("wPartyMons", slot * STRIDE + off) end
local function be16(slot, off) return pbyte(slot, off) * 256 + pbyte(slot, off + 1) end
party_evidence = function()
    local party = P.reads:read_party()
    local rows = {}
    for i, m in ipairs(party and party.mons or {}) do
        local slot = i - 1
        rows[#rows + 1] = {slot = slot, key = m.key, species = pbyte(slot, 0), hp = be16(slot, HP_OFF),
            max_hp = be16(slot, MAXHP_OFF), level = pbyte(slot, 31), egg = math.floor(pbyte(slot, 21) / 64) % 2 == 1}
    end
    return rows
end
local last_hp = {}
L.on_frame = function()
    local rows = party_evidence()
    for _, m in ipairs(rows) do
        if last_hp[m.key] ~= nil and last_hp[m.key] ~= m.hp and L.rw("wBattleMode") == 1 then
            witnesses.hp_transitions[#witnesses.hp_transitions + 1] = {
                frame = emu.framecount(), key = m.key, slot = m.slot, old = last_hp[m.key], new = m.hp}
        end
        last_hp[m.key] = m.hp
        if m.hp == 0 then
            for i, r in ipairs(received) do
                if r.cmd == "force_faint" and r.key == m.key and not force_readbacks[i] then
                    force_readbacks[i] = {frame = emu.framecount(), key = m.key, hp = m.hp,
                        slot = m.slot, party = rows, command_frame = r.frame, seq = r.seq, received_index = i}
                end
            end
        end
    end
end
local function snapshot()
    local party, why = P.reads:read_party()
    local mons = {}
    for i, m in ipairs(party and party.mons or {}) do
        local slot = i - 1
        mons[#mons + 1] = {slot = slot, key = m.key, species = pbyte(slot, 0), hp = be16(slot, HP_OFF),
                           max_hp = be16(slot, MAXHP_OFF), status = pbyte(slot, STATUS_OFF),
                           level = pbyte(slot, 31), egg = math.floor(pbyte(slot, 21) / 64) % 2 == 1,
                           record_hex = L.hex(L.wbytes("wPartyMons",slot*STRIDE,STRIDE)),
                           ot_hex = L.hex(L.wbytes("wPartyMonOTs",slot*11,11)),
                           nickname_hex = L.hex(L.wbytes("wPartyMonNicknames",slot*11,11))}
    end
    local box, box_why = {}, nil
    local okb, err = pcall(function()
        for b = 0, 19 do
            local list, w = P.reads.read_storage_box(b)
            if not list then box_why = tostring(w) return end
            for _, m in ipairs(list.mons) do
                local k = m.key
                if k == nil and PM then local okk, kk = pcall(PM.mon_key, m) k = okk and kk or nil end
                box[#box + 1] = {box = b, slot = m.slot, key = k}
            end
        end
    end)
    if not okb then box_why = tostring(err) end
    local diagnostic
    if L.SYM and L.SYM.wSlinkMailbox then
        local fs=P.client and P.client.faint_settle
        local owed={}
        if fs then for _,o in ipairs(fs.owed) do owed[#owed+1]={key=o.key,state=o.state,kind=o.kind} end end
        diagnostic={pc=emu.getregister("PC"),sp=emu.getregister("SP"),rom_bank=L.rombank(),svbk=L.bus(0xff70),
            battle_mode=L.rw("wBattleMode"),battle_turn=L.bus(L.SYM.hBattleTurn[2]),
            game_paused=L.rw("wGameLogicPaused"),script_running=L.rw("wScriptRunning"),
            menu_x=L.rw("wMenuCursorX"),menu_y=L.rw("wMenuCursorY"),
            menu_flags=L.rw("wBattleMenuFlags"),menu_buffer=L.rw("wBattleMenuCursorBuffer"),
            mailbox_hex=L.hex(L.wbytes("wSlinkMailbox",0,64)),
            fs_owed=owed,client_writes=#client_writes}
    end
    return {frame = emu.framecount(), diagnostic=diagnostic, party_count = L.rw("wPartyCount"), party = mons,
            party_why = party == nil and tostring(why) or nil, box = box, box_why = box_why}
end
local function slot_of(snap, key)
    for _, m in ipairs(snap.party) do if m.key == key then return m.slot end end
    return nil
end

-- ── native catch (ported from live.lua STAGE 2; Bag -> Ball, never Run) ─────────────────────────────
local BALL_POCKET = 2
local function walk_for_battle()
    local f, dir, lastx, moved = emu.framecount(), "Left", L.rw("wXCoord"), emu.framecount()
    while not L.after("StartBattle", f) do
        if emu.framecount() - f > 20000 then return false, "no wild battle in 20000 frames" end
        local x = L.rw("wXCoord")
        if x ~= lastx then lastx, moved = x, emu.framecount() end
        if emu.framecount() - moved > STALL then return false, fmt("NPC stall: x unchanged %d frames", STALL) end
        if x <= WALK[1] then dir = "Right" elseif x >= WALK[2] then dir = "Left" end
        if L.recent("BlinkCursor", 2) then L.pulse("A") else L.frame({[dir] = true}) end
    end
    L.idle(2)
    return true
end
local function battle_throw()
    local f_start, handled, last_act, prepped, prep_pulses = emu.framecount(), -1, emu.framecount(), -1, 0
    local throws, foe = 0, nil
    while not L.after("OWPlayerInput", f_start) do
        local f = emu.framecount()
        if f - f_start > 9000 then return nil, "battle did not end in 9000 frames" end
        local menu, btn = L.hit.LoadBattleMenu, nil
        if menu and menu > handled and f - menu >= 6 and not foe then
            foe = {species = L.rw("wEnemyMonSpecies"), level = L.rw("wEnemyMonLevel")}
        end
        if menu and menu > handled and f - menu >= 6 then
            if L.after("PokeBallEffect", menu) or L.after("BattleMenu_Run", menu) then handled = menu
            elseif L.after("BattlePack", menu) then btn = L.rw("wCurPocket") == BALL_POCKET and "A" or "Right"
            elseif prepped ~= menu then
                btn = "Start"                                               -- START on Bag (menu.asm QUICK_PACK)
                if (emu.framecount() % 16) < 2 then prep_pulses = prep_pulses + 1 end
                if prep_pulses >= 2 then prepped, prep_pulses = menu, 0 end
            else btn = "A" end
        elseif L.recent("YesNoBox", 40) then btn = "B"                      -- nickname? NO
        elseif L.recent("BlinkCursor", 2) then btn = "A"
        elseif f - math.max(last_act, L.hit.BlinkCursor or 0, L.hit.LoadBattleMenu or 0) > 90 then btn = "B" end
        if btn and (f % 16) < 2 then last_act = f end
        local before = L.hits.PokeBallEffect
        L.pulse(btn)
        if L.hits.PokeBallEffect > before then throws = throws + 1 end
    end
    local caught = L.hit["PokeBallEffect.caught"] ~= nil and L.hit["PokeBallEffect.caught"] >= f_start
    L.to_overworld(nil, nil, 30, 3000, "play-after-battle")
    return {caught = caught, throws = throws, foe = foe, frames = emu.framecount() - f_start,
            run_used = L.after("BattleMenu_Run", f_start)}
end
local function op_catch(step)
    local attempts, cap0, c0, battles = tonumber(step.attempts) or 4, cap_hits, #captures, {}
    for i = 1, attempts do
        local ok, why = walk_for_battle()
        if not ok then return {ok = false, why = why, battles = battles} end
        local b, bwhy = battle_throw()
        if not b then return {ok = false, why = bwhy, battles = battles} end
        battles[#battles + 1] = b
        if b.caught then break end
    end
    local f = emu.framecount()
    while #captures == c0 and emu.framecount() - f < 600 do L.frame() end
    local cev = captures[c0 + 1]
    return {ok = cev ~= nil, why = cev == nil and "the client sent no capture line" or nil, capture = cev,
            capture_site_hits = cap_hits - cap0, battles = battles, snapshot = snapshot()}
end

local function op_await(step)
    local f0, found = emu.framecount(), nil
    local frames = tonumber(step.frames) or 3600
    while emu.framecount() - f0 < frames do
        for _, r in ipairs(received) do
            if r.cmd == step.cmd and (step.key == nil or r.key == step.key) then found = r break end
        end
        if found then break end
        L.frame()
    end
    if found then L.idle(tonumber(step.settle) or 300) end           -- the client runs it at its overworld hold
    local readback
    for i, r in ipairs(received) do
        if r == found then readback = force_readbacks[i] break end
    end
    return {ok = found ~= nil, why = found == nil and fmt("%s %s not received in %d frames", tostring(step.cmd),
            tostring(step.key), frames) or nil, received = found, snapshot = snapshot(), force_faint_readback = readback}
end

local function op_synth_hp0(step)
    local before = snapshot()
    local want, writes = {}, {}
    for _, k in ipairs(step.keys or {}) do want[k] = true end
    for _, m in ipairs(before.party) do
        if step.all == true or want[m.key] then
            for _, off in ipairs({HP_OFF, HP_OFF + 1}) do
                local addr = L.woff("wPartyMons", m.slot * STRIDE + off)
                writes[#writes + 1] = {wram = addr, old = memory.read_u8(addr, "WRAM"), new = 0, slot = m.slot, key = m.key}
                raw_write_u8(addr, 0, "WRAM")
            end
        end
    end
    local h0 = #hellos
    C.disconnect()                       -- SYNTH: drop the session; the client's own reconnect re-sends hello
    local f = emu.framecount()
    while #hellos == h0 and emu.framecount() - f < STALL do L.frame() end
    L.idle(tonumber(step.settle) or 300)
    local h = hellos[h0 + 1]
    return {ok = #writes > 0 and h ~= nil, why = (#writes == 0 and "no party record matched") or (h == nil and
            "no hello re-sent after the reconnect") or nil, synth_writes = writes, before = before,
            rehello = h, snapshot = snapshot()}
end

local staged
local function op_synth_hp1(step)
    local before, writes, lead_writes, lead_after = snapshot(), {}, {}, nil
    local function result(ok, why)
        return {ok = ok, why = why, before = before, synth_writes = writes,
                lead_writes = lead_writes, lead_after = lead_after, snapshot = snapshot()}
    end
    if L.rw("wBattleMode") ~= 0 or not L.ow_idle() then return result(false, "setup-not-overworld-idle") end
    local want, matched = {}, {}
    for _, key in ipairs(step.keys or {}) do want[key] = true end
    local lead = slot_of(before, step.lead)
    if lead == nil then return result(false, "setup-lead-key-missing") end
    for _, m in ipairs(before.party) do
        if m.egg or m.hp <= 0 then return result(false, "setup-party-egg-or-already-fainted") end
        if step.all == true or want[m.key] then matched[m.key] = true end
    end
    for key in pairs(want) do if not matched[key] then return result(false, "setup-target-key-missing") end end
    if not matched[step.lead] then return result(false, "setup-lead-not-hp1-target") end
    if lead ~= 0 then
        -- Native SwapMonAndMail swaps these three WRAM arrays (48/11/11 bytes).
        -- No species-list array exists in Polished. Refuse held items rather than overlook parallel mail.
        if pbyte(0, 1) ~= 0 or pbyte(lead, 1) ~= 0 then
            return result(false, "setup-lead-reorder-held-item-not-supported")
        end
        for _, spec in ipairs({{"wPartyMons", STRIDE}, {"wPartyMonOTs", 11}, {"wPartyMonNicknames", 11}}) do
            local name, size = spec[1], spec[2]
            local a, b = L.wbytes(name, 0, size), L.wbytes(name, lead * size, size)
            for _, swap in ipairs({{0, a, b, before.party[lead + 1].key}, {lead, b, a, before.party[1].key}}) do
                for i = 0, size - 1 do
                    local addr = L.woff(name, swap[1] * size + i)
                    lead_writes[#lead_writes + 1] = {array = name, wram = addr, slot = swap[1],
                        key = swap[4], old = swap[2][i + 1], new = swap[3][i + 1]}
                    raw_write_u8(addr, swap[3][i + 1], "WRAM")
                end
            end
        end
    end
    lead_after = snapshot()
    for _, m in ipairs(party_evidence()) do
        if matched[m.key] then
            for i, value in ipairs({0, 1}) do
                local addr = L.woff("wPartyMons", m.slot * STRIDE + HP_OFF + i - 1)
                writes[#writes + 1] = {key = m.key, slot = m.slot, wram = addr,
                                      old = memory.read_u8(addr, "WRAM"), new = value}
                raw_write_u8(addr, value, "WRAM")
            end
        end
    end
    staged = {key = step.lead, all = step.all == true, frame = emu.framecount()}
    return result(true)
end

local function op_lose_native(step)
    local start, report0 = emu.framecount(), #reports
    local mark, inputs, initial, foe = {}, {}, party_evidence(), nil
    for kind, rows in pairs(witnesses) do mark[kind] = #rows end
    local run0, fight0 = L.hits.BattleMenu_Run or 0, L.hits.BattleMenu_Fight or 0
    local function new_reports()
        local rows = {}
        for i = report0 + 1, #reports do rows[#rows + 1] = reports[i] end
        return rows
    end
    local function has_report(event_name, key)
        for i = report0 + 1, #reports do
            if reports[i].event == event_name and (key == nil or reports[i].key == key) then return true end
        end
        return false
    end
    local function finish(ok, why)
        local hits = {}
        for kind, rows in pairs(witnesses) do
            hits[kind] = {}
            for i = mark[kind] + 1, #rows do hits[kind][#hits[kind] + 1] = rows[i] end
        end
        return {ok = ok, why = why, snapshot = snapshot(), reports = new_reports(), site_hits = hits,
            hook_errors = hook_errors, run_used = (L.hits.BattleMenu_Run or 0) > run0,
            fight_inputs = (L.hits.BattleMenu_Fight or 0) - fight0, inputs = inputs,
            initial_party = initial, foe = foe, frames = emu.framecount() - start}
    end
    if not staged or staged.key ~= step.key or staged.all ~= (step.all == true) then
        return finish(false, "hp1-setup-missing-or-mismatched")
    end
    if #initial == 0 then return finish(false, "setup-party-unreadable-or-empty") end
    for i, m in ipairs(initial) do
        m.moves, m.pp, m.item, m.status = {}, {}, pbyte(m.slot, 1), pbyte(m.slot, STATUS_OFF)
        for off = 0, 3 do
            m.moves[#m.moves + 1] = pbyte(m.slot, 2 + off)
            m.pp[#m.pp + 1] = pbyte(m.slot, 22 + off)
        end
        if (step.all or m.key == step.key) and m.hp ~= 1 then return finish(false, "hp1-setup-no-longer-present") end
        if i == 1 and m.key ~= step.key then return finish(false, "linked-key-not-lead") end
    end
    staged = nil
    native_ui = nil
    local walk_ok, walk_why = walk_for_battle()
    if not walk_ok then return finish(false, "encounter: " .. tostring(walk_why)) end
    local bound, changed, signature = math.min(tonumber(step.frames) or 72000, 72000), emu.framecount(), nil
    local target, encounters = nil, 1
    local function choose_input(button, action)
        if button and emu.framecount() % 16 < 2 then
            inputs[#inputs + 1] = {frame = emu.framecount(), button = button, action = action,
                ui = native_ui, active_slot = L.rw("wCurBattleMon"), target_slot = target}
        end
        L.pulse(button)
    end
    while emu.framecount() - start < bound do
        if #hook_errors > 0 then return finish(false, "read-only-hook-error") end
        -- Freeze at the real send, before whiteout text/pause/heal. Never advance buttons afterwards.
        if step.all and has_report("whiteout") then return finish(true) end
        local rows, alive, strong, active = party_evidence(), {}, {}, L.rw("wCurBattleMon")
        local lead_hp
        for _, m in ipairs(rows) do
            if m.key == step.key then lead_hp = m.hp end
            if not m.egg and m.hp > 0 then
                alive[#alive + 1] = m
                if m.key ~= step.key then strong[#strong + 1] = m end
            end
        end
        if foe and L.rw("wBattleMode") == 0 and L.ow_idle() then
            if not step.all and has_report("faint", step.key) then return finish(true) end
            -- No further setup writes: surviving HP1 mons fight a fresh native encounter.
            encounters = encounters + 1
            if encounters > 80 then return finish(false,"native-encounter-bound-80") end
            foe, native_ui, target = nil, nil, nil
            local again, why = walk_for_battle()
            if not again then return finish(false,"next-encounter: "..tostring(why)) end
            changed, signature = emu.framecount(), nil
        end
        if L.rw("wBattleMode") ~= 0 then
            if L.rw("wBattleMode") ~= 1 then return finish(false, "wrong-battle-type-not-wild") end
            if L.rw("wBattleType") ~= 0 then return finish(false, "wrong-battle-type-not-normal") end
        end
        if not foe and native_ui == "root" then
            foe = {species = L.rw("wEnemyMonSpecies"), level = L.rw("wEnemyMonLevel"),
                hp = L.rw("wEnemyMonHP") * 256 + L.rw("wEnemyMonHP", 1),
                moves = L.wbytes("wEnemyMonMoves", 0, 4), pp = L.wbytes("wEnemyMonPP", 0, 4)}
        end
        local sig = tostring(native_ui) .. ":" .. tostring(active) .. ":" .. tostring(L.hits.BattleTurn or 0)
        for _, m in ipairs(rows) do sig = sig .. ":" .. m.key .. "=" .. m.hp end
        if sig ~= signature then signature, changed = sig, emu.framecount() end
        if emu.framecount() - changed > 3600 then return finish(false, "native-battle-ui-or-hp-stall-3600") end
        if step.all and lead_hp == 0 and #strong > 0 then return finish(false, "weak-linked-mon-fainted-before-strong-party") end
        local btn, action
        if native_ui == "root" and emu.framecount() - native_ui_frame >= 8 then
            if not step.all and has_report("faint", step.key) then
                -- Native 2x2 menu RUN (bottom-right), only after the actual own faint wire.
                local y,x=L.rw("wMenuCursorY"),L.rw("wMenuCursorX")
                btn=y~=2 and "Down" or (x~=2 and "Right" or "A")
                action="select-run-after-linked-faint"
            elseif step.all and #strong > 0 then
                target = nil
                for _, m in ipairs(strong) do if m.slot ~= active then target = m.slot break end end
                if target == nil then
                    local y,x=L.rw("wMenuCursorY"),L.rw("wMenuCursorX")
                    btn=y~=1 and "Up" or (x~=1 and "Left" or "A")
                    action="fight-surviving-strong-existing-move"
                else
                    btn, action = "Select", "native-switch-turn"
                end
            else
                local y, x = L.rw("wMenuCursorY"), L.rw("wMenuCursorX")
                btn = y ~= 1 and "Up" or (x ~= 1 and "Left" or "A")
                action = "fight-linked-mon"
            end
        elseif native_ui == "party" and emu.framecount() - native_ui_frame >= 8 then
            if target == nil or be16(target, HP_OFF) == 0 or target == active then
                target = nil
                if not step.all then
                    for _, m in ipairs(alive) do if m.slot ~= active then target = m.slot break end end
                elseif #strong >= 2 then
                    target = strong[1].slot
                else
                    for _, m in ipairs(alive) do if m.key == step.key then target = m.slot break end end
                end
            end
            if target == nil then
                btn, action = "A", "advance-all-fainted-text"
            else
                local cursor = L.rw("wMenuCursorY")
                btn = cursor < target + 1 and "Down" or (cursor > target + 1 and "Up" or "A")
                action = "choose-native-party-slot"
            end
        elseif native_ui == "switch_menu" and emu.framecount() - native_ui_frame >= 8 then
            btn, action = L.rw("wMenuCursorY") ~= 1 and "Up" or "A", "confirm-switch"
        elseif native_ui == "move" then
            btn, action = "A", "choose-existing-move"
        elseif native_ui == "yesno" then
            btn, action = "A", "use-next-mon-yes"
        elseif native_ui == "post_run" then
            btn, action = "A", "advance-native-run-result-text"
        elseif L.recent("BlinkCursor", 2) then
            btn, action = "A", "advance-native-text"
        elseif (native_ui == nil or native_ui == "turn") and emu.framecount() - native_ui_frame > 90 then
            -- The intro can wait for a button before either the menu or BlinkCursor hook runs.
            btn, action = "A", "advance-unhooked-native-text"
        end
        choose_input(btn, action)
    end
    return finish(false, fmt("native-loss-frame-bound-%d",bound))
end

-- ── boot + step loop ─────────────────────────────────────────────────────────────────────────────
if not L.to_overworld(24, 3, 60, 8000, "continue") then L.die("CONTINUE did not reach map 24:3") end
local f0 = emu.framecount()
while not SLINK_GEN2_CLIENT.hello_sent and emu.framecount() - f0 < STALL do L.frame() end
L.check("client sent its hello", #hellos > 0)
L.idle(300)
L.log("PLAY_READY " .. J.encode({role = ROLE, frame = emu.framecount(), hellos = #hellos}))

local k, held, stopped = 0, 0, false
-- Diagnostics (pol-duo3): a Lua error in this loop would otherwise only reach the BizHawk console and silence the
-- client. Record it, then keep the emulator + client running so a script death is distinguishable from a hang.
local function beat()
    local fh = io.open(fmt("%s/beat_%s.txt", L.RUN, ROLE), "wb")
    if fh then fh:write(fmt("frame %d held %d k %d stopped %s", emu.framecount(), held, k, tostring(stopped))) fh:close() end
end
local function step_loop()
    while held < HOLD_CAP do
        if held % 30 == 0 then
            beat()
            local path = fmt("%s/step_%d.json", L.RUN, k + 1)
            local fh = io.open(path, "rb")
            if fh then
                local text = fh:read("*a")
                fh:close()
                local okd, step = pcall(J.decode, text)
                k = k + 1
                local res
                if not okd or type(step) ~= "table" then res = {ok = false, why = "unreadable step file"}
                elseif step.op == "stop" then stopped = true res = {ok = true}
                elseif step.op == "catch" then res = op_catch(step)
                elseif step.op == "snapshot" then res = {ok = true, snapshot = snapshot()}
                elseif step.op == "await_cmd" then res = op_await(step)
                elseif step.op == "synth_hp0" then res = op_synth_hp0(step)
                elseif step.op == "synth_hp1" then res = op_synth_hp1(step)
                elseif step.op == "lose_native" then res = op_lose_native(step)
                elseif step.op == "idle" then L.idle(tonumber(step.frames) or 60) res = {ok = true}
                else res = {ok = false, why = "unknown op " .. tostring(step.op)} end
                res.k, res.op, res.label, res.frame = k, okd and step.op or "?", okd and step.label or nil, emu.framecount()
                res.client_writes = #client_writes
                local oke, line = pcall(J.encode, res)
                L.log("PLAY_STEP " .. (oke and line or J.encode({k = k, op = res.op, ok = false, why = "encode: " .. tostring(line)})))
                if stopped then break end
            end
        end
        L.frame()
        held = held + 1
    end
end
local looped, lerr = xpcall(step_loop, debug.traceback)
if not looped then
    if tostring(lerr):find("pol-live-finished", 1, true) then error(lerr, 0) end
    L.log(fmt("PLAY_LUA_ERROR frame %d held %d k %d: %s", emu.framecount(), held, k, tostring(lerr)))
    while true do L.frame() end
end
L.check("stop step seen (not the hold cap)", stopped, held)
L.log("PLAY_FINAL " .. J.encode({role = ROLE, frame = emu.framecount(), client_writes = #client_writes,
      client_write_log = table.concat(client_writes, "; "):sub(1, 1500), received = #received, hellos = #hellos,
      captures = #captures}))
L.finish("duo-play-side-" .. ROLE)
