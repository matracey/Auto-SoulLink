-- Read-only Polished exec-callback PC probe; wrong banks retain at most 16 rows per site.
-- Setup is declared by the config (played, or synth = a disclosed tool-built SaveRAM, O-33); either way the supplied
-- route is native boot/CONTINUE/battle input. No client, synthetic battle, warp, guest-memory write, or CPU/register
-- mutation. Battle-mode transitions are logged as separate bounded "mode" rows (read-only evidence of a wild battle).
local L = dofile(assert(os.getenv("SLINK_ROOT")) .. "/tools/polished_live/pol_lib.lua")
local config
local trace, hits, protected_hits, elapsed = {}, 0, 0, 0
local guest_writes, cpu_changes, driver_errors = 0, 0, 0
local wrong_bank_sample_limit = 16
local hook_counts = {}
local mode_row_limit = 64
local mode_prev, mode_rows, mode_changes, mode_rows_dropped = 0, 0, 0, 0
for _, site in ipairs({"gate", "next", "last"}) do
    hook_counts[site] = {total = 0, qualified = 0, wrong_pc = 0,
                         wrong_banks = {}, wrong_bank_samples = 0}
end
local function append(kind, extra)
    local e = {kind = kind, ord = #trace + 1, frame = emu.framecount()}
    if extra then for k, v in pairs(extra) do e[k] = v end end
    trace[#trace + 1] = e
end

-- Guard Lua mutation APIs, not native engine writes (normal play necessarily writes RAM).
-- A denied attempt is evidence of a probe violation even if the hook recorder overflowed.
local G = dofile(L.ROOT .. "/tools/polished_live/recording_guard.lua")
memory, emu = G.readonly(memory, emu, function(kind, api)
    if kind == "guest_write" then
        guest_writes = guest_writes + 1
        if guest_writes == 1 then append(kind, {api=api}) end
    else
        cpu_changes = cpu_changes + 1
        if cpu_changes == 1 then append(kind, {api=api}) end
    end
end)

local function rom_hex(addr)
    local bytes = {}
    local offset = 0x0F * 0x4000 + addr - 0x4000
    for i = 0, 5 do bytes[#bytes + 1] = memory.read_u8(offset + i, "ROM") end
    return L.hex(bytes)
end
local function recorder(kind, addr)
    return function()
        local counts = hook_counts[kind]
        hits, counts.total = hits + 1, counts.total + 1
        local bank, pc = L.bus(0xFF87), emu.getregister("PC")
        local matched = addr < 0x4000 or bank == 0x0F
        local qualified = matched and pc == addr
        if not matched then
            local key = string.format("%d", bank)
            counts.wrong_banks[key] = (counts.wrong_banks[key] or 0) + 1
            if counts.wrong_bank_samples >= wrong_bank_sample_limit then return end
            counts.wrong_bank_samples = counts.wrong_bank_samples + 1
        else
            if qualified then counts.qualified = counts.qualified + 1
            else counts.wrong_pc = counts.wrong_pc + 1 end
            if protected_hits >= config.trace_cap then return end
            protected_hits = protected_hits + 1
        end
        local side, mode = L.bus(L.SYM.hBattleTurn[2]), L.rw("wBattleMode")
        local class, id = L.rw("wOtherTrainerClass"), L.rw("wOtherTrainerID")
        local rival_classes = {[0x1B]=true, [0x1C]=true, [0x1D]=true, [0x1E]=true, [0x1F]=true}
        local operation = qualified and side == 1 and mode == 2
            and rival_classes[class] == true and config.expected ~= nil
            and id == config.expected.trainer_id
        append(kind, {
            hook_addr = addr, pc = pc, sp = emu.getregister("SP"),
            bank = bank, matched = matched, raw_qualified = qualified, qualified = operation,
            mode = mode, battle_turn = side,
            hl = emu.getregister("HL"), flags = emu.getregister("F"),
            trainer_class = class,
            trainer_id = id, cur_ot_mon = L.rw("wCurOTMon"),
            cur_party_mon = L.rw("wCurPartyMon"), ot_party_count = L.rw("wOTPartyCount"),
            -- Always read the pinned bank via ROM, never accidentally read another bank's System Bus bytes.
            site_bytes = rom_hex(0x47DD), hook_bytes = rom_hex(addr),
        })
        -- Fail closed as soon as capacity is reached, even if there is no later hook.
        if matched and protected_hits == config.trace_cap then
            append("overflow", {cap = config.trace_cap})
        end
    end
end

-- Log wBattleMode transitions (nothing -> battle, battle -> nothing, wild <-> trainer). Own cap: never consumes the
-- protected hook capacity, and an unbounded flicker is counted rather than stored.
local function observe_mode()
    local mode = L.rw("wBattleMode")
    if mode == mode_prev then return end
    mode_prev = mode
    mode_changes = mode_changes + 1
    if mode_rows >= mode_row_limit then
        mode_rows_dropped = mode_rows_dropped + 1
        return
    end
    mode_rows = mode_rows + 1
    append("mode", {
        mode = mode, trainer_class = L.rw("wOtherTrainerClass"), trainer_id = L.rw("wOtherTrainerID"),
        ot_party_count = L.rw("wOTPartyCount"),
    })
end

-- Source-derived route: RIVAL_STAGING section 3; each target is verified from WRAM,
-- never inferred from elapsed input or screenshots. Record all native inputs for replay.
local function calibrate()
    local steps, milestones = {}, {}
    local function save()
        for name, value in pairs({["route.json"] = {steps = steps}, ["calibration.json"] = milestones}) do
            local f = assert(io.open(L.RUN .. "/" .. name, "w"))
            f:write(L.json.encode(value)); f:close()
        end
    end
    local function mark(label)
        milestones[#milestones + 1] = {label = label, frame = emu.framecount(), elapsed = elapsed,
            group = L.rw("wMapGroup"), map = L.rw("wMapNumber"), x = L.rw("wXCoord"),
            y = L.rw("wYCoord"), mode = L.rw("wBattleMode")}
        save()
    end
    local function frame(button)
        assert(elapsed < config.frames, "calibration total frame bound")
        local prev = steps[#steps]
        if prev and prev.buttons[1] == button then prev.frames = prev.frames + 1
        else steps[#steps + 1] = {frames = 1, buttons = button and {button} or {}} end
        L.frame(button and {[button] = true} or {})
        elapsed = elapsed + 1
        observe_mode()
    end
    local function idle(n) for _ = 1, n do frame() end end
    local function pulse() frame(elapsed % 16 < 2 and "A" or nil) end
    local function bounded(predicate, action, label)
        local start = elapsed
        while not predicate() do
            if elapsed - start >= 1800 then mark("STALL " .. label); error("1800-frame stall: " .. label) end
            action()
        end
        mark(label)
    end
    L.hook("OWPlayerInput")
    bounded(function() return L.ow_idle() and L.rw("wMapGroup") == 24 and L.rw("wMapNumber") == 3 end,
        pulse, "boot Route29")
    idle(30)
    assert(L.rw("wXCoord") == 48 and L.rw("wYCoord") == 12, "unexpected fixture coordinates")
    if config.expect == "wild" then
        bounded(function() return L.rw("wBattleMode") == 1 end,
            function() frame(elapsed % 160 < 80 and "Left" or "Right") end, "wild control entered")
        idle(600)
        mark("wild control complete")
        return
    end
    local function wild()
        mark("wild entered")
        if config.expect == "wild" then return true end
        bounded(function() return L.rw("wBattleMode") == 0 and L.ow_idle() end, pulse, "wild ended")
        idle(30)
        return false
    end
    local route = {{"Left",4},{"Down",2},{"Left",6},{"Down",2},{"Left",7},{"Up",6},
        {"Right",5},{"Up",3},{"Left",13},{"Up",1},{"Left",2},{"Up",2},{"Left",5},
        {"Down",2},{"Left",5},{"Down",4},{"Left",7},{"Up",3},{"Left",11}}
    local dirs = {Left={-1,0},Right={1,0},Up={0,-1},Down={0,1}}
    for seg, row in ipairs(route) do
        for tile = 1, row[2] do
            if L.rw("wBattleMode") == 1 and wild() then mark("wild control complete"); return end
            local x,y = L.rw("wXCoord"), L.rw("wYCoord")
            local targetx,targety = x + dirs[row[1]][1],y + dirs[row[1]][2]
            local group,map = L.rw("wMapGroup"),L.rw("wMapNumber")
            if targetx == -1 then group,map,targetx = 26,4,39 end
            bounded(function()
                return L.rw("wMapGroup") == group and L.rw("wMapNumber") == map
                    and L.rw("wXCoord") == targetx and L.rw("wYCoord") == targety
            end, function()
                if L.rw("wBattleMode") == 1 then wild() else frame(row[1]) end
            end, "segment " .. seg .. " tile " .. tile)
            idle(24)
        end
    end
    assert(config.expect ~= "wild", "wild control route produced no encounter")
    local before = hook_counts.last.qualified
    bounded(function() return hook_counts.last.qualified > before and L.rw("wBattleMode") == 2
        and L.rw("wOtherTrainerClass") == 0x1B and L.rw("wOtherTrainerID") == 3 end,
        pulse, "rival last witness")
    idle(60)
    mark("calibration complete")
end

local function play()
    config = L.json.decode(L.slurp(assert(os.getenv("POL_PROBE_CONFIG"))))
    L.hook_at("rival_probe_gate", 0x0F, 0x47DD, recorder("gate", 0x47DD))
    L.hook_at("rival_probe_next", 0x0F, 0x47E0, recorder("next", 0x47E0))
    L.hook_at("rival_probe_last", 0x0F, 0x480D, recorder("last", 0x480D))
    client.speedmode(300)
    if config.calibrate then calibrate(); return end
    for _, step in ipairs(config.steps) do
        local buttons = {}
        for _, button in ipairs(step.buttons) do buttons[button] = true end
        for _ = 1, step.frames do
            L.frame(buttons)
            elapsed = elapsed + 1
            observe_mode()
        end
    end
    while elapsed < config.frames do
        L.frame()
        elapsed = elapsed + 1
        observe_mode()
    end
end

local ok, err = pcall(play)
if not ok then
    driver_errors = driver_errors + 1
    append("driver_error", {error = tostring(err)})
end
local final = {qualification_schema = "rival-operation/1", completed = ok, elapsed = elapsed, guest_writes = guest_writes,
               cpu_changes = cpu_changes, hook_hits = hits, driver_errors = driver_errors,
               wrong_bank_sample_limit = wrong_bank_sample_limit, hook_counts = hook_counts,
               mode_row_limit = mode_row_limit, mode_changes = mode_changes, mode_rows_dropped = mode_rows_dropped,
               setup = config and config.setup or nil,
               disclosure_sha256 = config and config.disclosure_sha256 or nil}
append("final", final)
final = trace[#trace]

-- Independent, small JSON encoder for the minimal failure record. Never retry the primary.
local function fallback_json(value)
    local t = type(value)
    if t == "string" then
        return '"' .. value:gsub('[%z\1-\31\\"]', function(c)
            return string.format("\\u%04x", string.byte(c))
        end) .. '"'
    elseif t == "number" or t == "boolean" then return tostring(value)
    elseif t == "table" then
        local parts = {}
        if #value > 0 then
            for _, v in ipairs(value) do parts[#parts + 1] = fallback_json(v) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        for k, v in pairs(value) do
            parts[#parts + 1] = fallback_json(tostring(k)) .. ":" .. fallback_json(v)
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return "null"
end
local encoded_ok, encoded = pcall(L.json.encode, trace)
if not encoded_ok or type(encoded) ~= "string" then
    driver_errors = driver_errors + 1
    final.completed, final.driver_errors = false, driver_errors
    local message = encoded_ok and "JSON encoder returned " .. type(encoded) or tostring(encoded)
    local failure = {kind = "driver_error", error = message}
    encoded = fallback_json({failure, final})
    ok, err = false, message
end
-- Bounded trace, one dump at the end; failures still reach finish.
local dump_ok, dump_err = pcall(function()
    local f = assert(io.open(L.RUN .. "/trace.json", "w"))
    local write_ok, write_err = pcall(function() assert(f:write(encoded)) end)
    f:close()
    assert(write_ok, write_err)
end)
L.check(((config and config.setup) or "played") .. " route completed without probe mutations",
        ok and dump_ok and guest_writes == 0 and cpu_changes == 0,
        not dump_ok and tostring(dump_err) or (ok and ("recorded " .. hits .. " hooks") or tostring(err)))
-- Driver PASS means complete recording only. Python alone decides PASS / NO_GATE_HIT / PC findings.
L.finish("rival-gate-probe recording")
