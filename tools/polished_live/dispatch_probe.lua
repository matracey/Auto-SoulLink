-- D3: read-only dispatcher-entry measurement. Native played input only, no lease publication.
-- All hook addresses and nine pinned byte VALUES come from Python's sym-derived input.json.
-- Wrong-bank callbacks are aggregate-only after 16 samples/site; protected rows keep full detail.
local L = dofile(assert(os.getenv("SLINK_ROOT")) .. "/tools/polished_live/pol_lib.lua")
local config = L.json.decode(L.slurp(assert(os.getenv("POL_PROBE_CONFIG"))))
local C = config.contract
local trace, recorded, dispatch_hits, prompt_hits = {}, 0, 0, 0
local guest_writes, cpu_changes, driver_errors, overflows = 0, 0, 0, 0
local phase, phase_frames, elapsed = "setup", {}, 0
local hook_counts = {}
for _, kind in ipairs({"dispatch", "prompt"}) do
    hook_counts[kind] = {total = 0, qualified = 0, wrong_pc = 0, wrong_banks = {}, wrong_bank_samples = 0}
end
local function append(kind, extra)
    local e = {kind = kind, ord = #trace + 1, frame = emu.framecount(), phase = phase}
    if extra then for k, v in pairs(extra) do e[k] = v end end
    trace[#trace + 1] = e
end

-- Guard PROBE writes, not ordinary native engine RAM writes caused by played input.
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

local function record(kind, addr, bank_matched)
    local pc, bank = emu.getregister("PC"), L.bus(C.rombank_addr)
    local counts = hook_counts[kind]
    counts.total = counts.total + 1
    local qualified = bank_matched and bank == C.bank and pc == addr
    if bank ~= C.bank then
        local key = string.format("%d", bank)
        counts.wrong_banks[key] = (counts.wrong_banks[key] or 0) + 1
        if counts.wrong_bank_samples >= 16 then return end
        counts.wrong_bank_samples = counts.wrong_bank_samples + 1
        append(kind, {hook_addr = addr, pc = pc, bank = bank, matched = false, qualified = false})
        return
    end
    if qualified then
        counts.qualified = counts.qualified + 1
        if kind == "dispatch" then dispatch_hits = dispatch_hits + 1 else prompt_hits = prompt_hits + 1 end
    else
        counts.wrong_pc = counts.wrong_pc + 1
    end
    if recorded >= config.trace_cap then return end
    recorded = recorded + 1
    local sp, stack = emu.getregister("SP"), {}
    for i = 0, 27 do stack[i + 1] = L.bus((sp + i) % 0x10000) end
    local stack_match = true
    for _, pin in ipairs(C.pins) do
        if stack[pin.offset + 1] ~= pin.value then stack_match = false end
    end
    local e = {hook_addr = addr, pc = pc, sp = sp, bank = bank, matched = bank_matched,
               qualified = qualified, stack = L.hex(stack), stack_match = stack_match,
               svbk = L.bus(C.svbk_addr)}
    -- These are the dispatcher's actual System Bus loads, not flat WRAM bank-1 aliases.
    for field, symbol in pairs(C.fields) do e[field] = L.bus(L.SYM[symbol][2]) end
    e.engine_clean = e.svbk % 8 < 2 and e.script_mode == 0 and e.battle_mode == 0 and e.link_mode == 0
                     and e.paused == 0 and e.in_menu == 0 and e.vblank == 0
                     and e.map_status == C.map_status_handle
                     and math.floor(e.step_flags / C.step_continue_mask) % 2 == 0
                     and e.map_event_status == C.map_events_on
    -- Context eligibility ONLY. The unpublished lease still refuses actual prompt dispatch.
    e.context_accept = qualified and stack_match and e.engine_clean
    append(kind, e)
    if recorded == config.trace_cap then
        overflows = overflows + 1
        append("overflow", {cap = config.trace_cap})
    end
end
local function recorder(kind, addr)
    return function(bank_matched)
        local ok, err = pcall(record, kind, addr, bank_matched)
        if not ok then
            driver_errors = driver_errors + 1
            if driver_errors == 1 then append("driver_error", {error = tostring(err), site = kind}) end
        end
    end
end

local function advance(buttons)
    L.frame(buttons)
    phase_frames[phase] = (phase_frames[phase] or 0) + 1
end
local function play()
    L.hook_at("dispatch_probe_entry", C.bank, C.dispatch_addr, recorder("dispatch", C.dispatch_addr))
    L.hook_at("dispatch_probe_prompt", C.bank, C.prompt_addr, recorder("prompt", C.prompt_addr))
    client.speedmode(400)
    elapsed = 0
    for _, step in ipairs(config.steps) do
        phase = step.phase
        local buttons = {}
        for _, button in ipairs(step.buttons) do buttons[button] = true end
        for _ = 1, step.frames do advance(buttons) elapsed = elapsed + 1 end
    end
    phase = "setup" -- any spare observation budget is unclassified, never a mislabeled positive.
    while elapsed < config.frames do advance() elapsed = elapsed + 1 end
end
local ok, err = pcall(play)
if not ok then
    driver_errors = driver_errors + 1
    append("driver_error", {error = tostring(err)})
end
L.unhook("dispatch_probe_entry")
L.unhook("dispatch_probe_prompt")
append("final", {completed = ok and elapsed == config.frames, elapsed = elapsed, phase_frames = phase_frames,
                 guest_writes = guest_writes, cpu_changes = cpu_changes, driver_errors = driver_errors,
                 overflows = overflows, dispatch_hits = dispatch_hits, prompt_hits = prompt_hits,
                 wrong_bank_sample_limit = 16, hook_counts = hook_counts})
local final = trace[#trace]
local encoded_ok, encoded, encode_error = pcall(L.json.encode, trace)
local serialized = encoded_ok and type(encoded) == "string"
if not serialized then
    driver_errors = driver_errors + 1
    final.driver_errors, final.completed, final.ord = driver_errors, false, 2
    local failure = {kind = "driver_error", ord = 1, frame = final.frame,
                     error = ("trace serialization failed: " .. tostring(encoded_ok and encode_error or encoded)):sub(1, 1024)}
    -- Independent, tiny fallback: the primary encoder can throw or return nil on EVERY call.
    local function minimal_json(value)
        if type(value) == "string" then
            return '"' .. value:gsub('[%z\1-\31\127-\255\\"]', function(c)
                return string.format("\\u%04x", c:byte())
            end) .. '"'
        elseif type(value) == "table" then
            local parts = {}
            for key, item in pairs(value) do
                parts[#parts + 1] = minimal_json(tostring(key)) .. ":" .. minimal_json(item)
            end
            return "{" .. table.concat(parts, ",") .. "}"
        elseif type(value) == "boolean" or type(value) == "number" then
            return tostring(value)
        end
        return "null"
    end
    encoded = "[" .. minimal_json(failure) .. "," .. minimal_json(final) .. "]"
end
local f = assert(io.open(L.RUN .. "/trace.json", "w"))
f:write(encoded)
f:close()
L.check("played route recorded without probe mutations",
        ok and serialized and guest_writes == 0 and cpu_changes == 0 and driver_errors == 0 and overflows == 0,
        ok and ("dispatch hits " .. dispatch_hits .. ", prompt hits " .. prompt_hits) or tostring(err))
-- This RESULT means complete recording only; the Python oracle owns the measurement verdict.
L.finish("dispatch-probe recording")
