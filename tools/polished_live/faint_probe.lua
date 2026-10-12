-- Natural-faint observation only. Coordinates/ROM bytes come from the Python contract.
-- No SLink client, memory writes, register changes, forced battles or synthetic setup.
-- Wrong-bank callbacks retain only the first 16/site; counters cover every callback.
-- Qualified and bank-matched wrong-PC diagnostics share a separate protected cap.
local L = dofile(assert(os.getenv("SLINK_ROOT")) .. "/tools/polished_live/pol_lib.lua")
local config = L.json.decode(L.slurp(assert(os.getenv("POL_PROBE_CONFIG"))))
local C = assert(config.contract)
local trace, counts, hook_counts, ids = {}, {}, {}, {}
local wrong_bank_sample_limit = 16
for kind in pairs(C.sites) do
    counts[kind] = 0
    hook_counts[kind] = {total=0, qualified=0, wrong_pc=0, wrong_banks={}, wrong_bank_samples=0}
end
local hits, stored, overflows, guest_writes, cpu_changes, driver_errors = 0, 0, 0, 0, 0, 0
local function append(kind, fields)
    local row = {kind=kind, ord=#trace + 1, frame=emu.framecount()}
    for key, value in pairs(fields or {}) do row[key] = value end
    trace[#trace + 1] = row
end

-- Guard probe-originated mutation attempts; the native game necessarily writes RAM.
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

local function byte(field, delta)
    local row = assert(C.ram[field], "missing contract RAM field")
    local value = memory.read_u8(row.offset + (delta or 0), row.domain)
    assert(type(value) == "number" and value >= 0 and value <= 255 and value % 1 == 0, "unreadable " .. field)
    return value
end
local function hp(field, delta)
    local hi, lo = byte(field, delta), byte(field, (delta or 0) + 1)
    return {hi, lo}, hi * 256 + lo
end
local function record(kind, site)
    local counter = hook_counts[kind]
    hits, counts[kind], counter.total = hits + 1, counts[kind] + 1, counter.total + 1
    local bank, pc, sp = byte("rombank"), emu.getregister("PC"), emu.getregister("SP")
    local matched = site.addr < 16384 or bank == site.bank
    local qualified = matched and pc == site.addr
    if matched then
        local field = qualified and "qualified" or "wrong_pc"
        counter[field] = counter[field] + 1
        if stored >= config.trace_cap then return end
        stored = stored + 1
    else
        local key = string.format("%d", bank)
        counter.wrong_banks[key] = (counter.wrong_banks[key] or 0) + 1
        if counter.wrong_bank_samples >= wrong_bank_sample_limit then return end
        counter.wrong_bank_samples = counter.wrong_bank_samples + 1
    end
    local slot, count = byte("slot"), byte("count")
    local valid = count >= 1 and count <= C.party_capacity and slot < count
    local battle_raw, battle_hp = hp("battle_hp")
    local party_raw, party_hp, party_status = false, false, false
    if valid then
        party_raw, party_hp = hp("party_hp", slot * C.party_stride)
        party_status = byte("party_status", slot * C.party_stride)
    end
    local bytes = {}
    for i = 0, #site.bytes / 2 - 1 do
        bytes[#bytes + 1] = memory.read_u8(site.rom_offset + i, "ROM")
    end
    append(kind, {hook_addr=site.addr, hook_bytes=L.hex(bytes), pc=pc, sp=sp, bank=bank,
        matched=matched, qualified=qualified,
        slot=slot, count=count, party_slot_valid=valid, battle_hp_bytes=battle_raw, battle_hp=battle_hp,
        party_hp_bytes=party_raw, party_hp=party_hp, party_status=party_status,
        battle_status=byte("battle_status"), order=byte("order"), substatus2=byte("substatus2"),
        turn=byte("turn"), mode=byte("mode")})
    -- Capacity reached is already censored: never silently pass even without a later hook.
    if matched and stored == config.trace_cap then
        overflows = overflows + 1
        append("overflow", {cap=config.trace_cap})
    end
end
local function api_text(fn)
    if type(fn) ~= "function" then return "UNAVAILABLE" end
    local ok, value = pcall(fn)
    return ok and tostring(value) or "UNAVAILABLE"
end
append("begin", {run_id=config.run_id, provenance=config.provenance,
    emulator_version=api_text(client.getversion), system=api_text(emu.getsystemid)})

local elapsed = 0
local ok, err = pcall(function()
    assert(C.schema == "polished-faint-probe-v1", "wrong probe contract")
    for kind, site in pairs(C.sites) do
        ids[#ids + 1] = event.on_bus_exec(function()
            -- Callback exceptions may be swallowed by the emulator. Latch explicitly.
            local recorded, why = pcall(record, kind, site)
            if not recorded then
                driver_errors = driver_errors + 1
                if driver_errors == 1 then append("driver_error", {error=tostring(why), site=kind}) end
            end
        end, site.addr, "pol_faint_" .. kind, "System Bus")
    end
    client.speedmode(300)
    for _, step in ipairs(config.steps) do
        local buttons = {}
        for _, button in ipairs(step.buttons) do buttons[button] = true end
        for _ = 1, step.frames do L.frame(buttons); elapsed = elapsed + 1 end
    end
    while elapsed < config.frames do L.frame(); elapsed = elapsed + 1 end
end)
if not ok then
    driver_errors = driver_errors + 1
    append("driver_error", {error=tostring(err)})
end
for _, id in ipairs(ids) do
    local removed, why = pcall(event.unregisterbyid, id)
    if not removed then
        driver_errors = driver_errors + 1
        append("driver_error", {error=tostring(why)})
    end
end
append("final", {completed=ok and elapsed == config.frames and driver_errors == 0, elapsed=elapsed,
    hook_hits=hits, counts=counts, hook_counts=hook_counts,
    wrong_bank_sample_limit=wrong_bank_sample_limit,
    guest_writes=guest_writes, cpu_changes=cpu_changes,
    overflows=overflows, driver_errors=driver_errors})
local final = trace[#trace]

-- Independent fallback: never call the failed primary encoder a second time.
local function quote(text)
    return '"' .. text:gsub('[%z\1-\31\\"]', function(char)
        if char == '"' then return '\\"' end
        if char == "\\" then return "\\\\" end
        return string.format("\\u%04x", string.byte(char))
    end) .. '"'
end
local function minimal_json(value)
    local kind = type(value)
    if kind == "string" then return quote(value) end
    if kind == "number" or kind == "boolean" then return tostring(value) end
    assert(kind == "table", "unsupported fallback value")
    local out = {}
    if #value > 0 or next(value) == nil then
        for _, item in ipairs(value) do out[#out + 1] = minimal_json(item) end
        return "[" .. table.concat(out, ",") .. "]"
    end
    for key, item in pairs(value) do
        out[#out + 1] = quote(tostring(key)) .. ":" .. minimal_json(item)
    end
    return "{" .. table.concat(out, ",") .. "}"
end
local encoded_ok, encoded = pcall(L.json.encode, trace)
if not encoded_ok or type(encoded) ~= "string" then
    driver_errors = driver_errors + 1
    final.completed, final.driver_errors = false, driver_errors
    local diagnostic = {kind="driver_error", ord=1, frame=final.frame,
        error="trace serialization failed: " .. tostring(encoded)}
    final.ord = 2
    encoded = minimal_json({diagnostic, final})
end
local written, write_error = pcall(function()
    local f = assert(io.open(L.RUN .. "/trace.json", "w"))
    local wrote, why = pcall(function() assert(f:write(encoded)) end)
    f:close()
    assert(wrote, why)
end)
if not written then
    driver_errors = driver_errors + 1
    final.completed, final.driver_errors = false, driver_errors
    print("driver_error: trace write failed: " .. tostring(write_error))
end
L.check("complete read-only faint recording", ok and elapsed == config.frames
        and guest_writes == 0 and cpu_changes == 0
        and overflows == 0 and driver_errors == 0, "protected hook rows " .. stored .. "/" .. hits)
L.finish("faint-probe recording")
