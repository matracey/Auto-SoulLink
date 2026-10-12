-- Read-only cold battery reload evidence, never a trade driver or an identity oracle.
-- Python pins the ROM/sites, checks this raw census, and owns the measurement verdict.
-- Only occupied pointers are dereferenced (both copies); unused database garbage is ignored.
-- Verify*Checksum.fail is a SHARED EPILOGUE, not proof of failure: CPU Z is recorded.
-- One snapshot is taken inside the third consecutive clean D3 dispatch callback after
-- an affirmative native load selection. No frame advancement occurs inside any hook.
local L = dofile(assert(os.getenv("SLINK_ROOT")) .. "/tools/polished_live/pol_lib.lua")
local config = L.json.decode(L.slurp(assert(os.getenv("POL_PROBE_CONFIG"))))
local C = assert(config.contract)
local trace, ids, hook_counts = {}, {}, {}
local elapsed, stored, snapshot_count = 0, 0, 0
local guest_writes, cpu_changes, driver_errors, overflows = 0, 0, 0, 0
local wrong_bank_sample_limit = 16
local stable_frame_count, stable_frame = 0, nil
local integrity = {load_started=false, primary="unobserved", backup="unobserved",
                   selected="unobserved", corrupt=false}
local backup_started = false
local site_names = {"dispatch", "load_begin", "primary_fail", "backup_begin",
                    "backup_fail", "primary_loaded", "backup_loaded", "corrupt"}
for _, kind in ipairs(site_names) do
    hook_counts[kind] = {total=0, qualified=0, wrong_pc=0, wrong_banks={}, wrong_bank_samples=0}
end
local function append(kind, fields)
    local row = {kind=kind, ord=#trace + 1, frame=emu.framecount()}
    for key, value in pairs(fields or {}) do row[key] = value end
    trace[#trace + 1] = row
    return row
end
local function driver_error(why, site)
    driver_errors = driver_errors + 1
    stable_frame_count, stable_frame = 0, nil
    if driver_errors == 1 then
        append("driver_error", {error=tostring(why):sub(1, 1024), site=site})
    end
end

-- Guard probe-originated writes only; native gameplay must remain able to write RAM.
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

local function integer(value, low, high, label)
    assert(type(value) == "number" and value % 1 == 0 and value >= low and value <= high,
           "unreadable " .. label)
    return value
end
local function byte(domain, offset)
    return integer(memory.read_u8(offset, domain), 0, 255, domain .. " byte")
end
local function bytes(domain, offset, size)
    local result = {}
    for i = 0, size - 1 do result[i + 1] = byte(domain, offset + i) end
    return result
end
local function ram(field, size, delta)
    local where = assert(C.ram[field], "missing RAM contract: " .. field)
    return bytes(where.domain, where.offset + (delta or 0), size)
end
local function bus(offset) return byte("System Bus", offset) end
local function register(name)
    return integer(emu.getregister(name), 0, 65535, name)
end
local function anchor(site)
    assert(type(site.bytes) == "string" and #site.bytes > 0 and #site.bytes % 2 == 0,
           "missing ROM anchor")
    local actual = L.hex(bytes("ROM", site.rom_offset, #site.bytes / 2))
    assert(actual == site.bytes:lower(), "ROM hook anchor mismatch")
    return actual
end
local function checksum_flags()
    -- getregisters establishes that the register exists: unknown-name APIs may return 0.
    local registers = emu.getregisters()
    assert(type(registers) == "table", "CPU registers unavailable")
    local flags = registers.F or registers.f
    if flags ~= nil then return integer(flags, 0, 255, "CPU F") end
    local af = registers.AF or registers.af
    assert(af ~= nil, "CPU checksum flags unavailable")
    return integer(af, 0, 65535, "CPU AF") % 256
end
local function native_load(kind, row)
    if kind == "load_begin" then
        assert(not integrity.load_started, "contradictory second cold load")
        integrity.load_started = true
        stable_frame_count, stable_frame = 0, nil
        return
    end
    assert(integrity.load_started, "load branch before cold load entry")
    assert(not integrity.corrupt, "load branch after corrupt result")
    if kind == "primary_fail" or kind == "backup_fail" then
        local flags = checksum_flags()
        row.checksum_flags = flags
        row.checksum_zero = math.floor(flags / 128) % 2 == 1
        assert(integrity.selected == "unobserved", "checksum result after selected load")
        local copy = kind == "primary_fail" and "primary" or "backup"
        assert((copy == "backup") == backup_started, "checksum result in wrong load branch")
        assert(integrity[copy] == "unobserved", "duplicate checksum result")
        integrity[copy] = row.checksum_zero and "valid" or "invalid"
    elseif kind == "backup_begin" then
        assert(not backup_started and integrity.primary == "invalid"
               and integrity.selected == "unobserved", "contradictory backup branch")
        backup_started = true
    elseif kind == "primary_loaded" then
        assert(not backup_started and integrity.primary == "valid"
               and integrity.selected == "unobserved", "contradictory primary selection")
        integrity.selected = "main"
    elseif kind == "backup_loaded" then
        assert(backup_started and integrity.primary == "invalid" and integrity.backup == "valid"
               and integrity.selected == "unobserved", "contradictory backup selection")
        integrity.selected = "backup"
    elseif kind == "corrupt" then
        assert(backup_started and integrity.primary == "invalid" and integrity.backup == "invalid"
               and integrity.selected == "unobserved", "contradictory corrupt branch")
        integrity.corrupt = true
    end
end
local function checkpoint(sp)
    local guards = {}
    for field, where in pairs(C.guards) do guards[field] = byte(where.domain, where.offset) end
    local svbk = bus(C.svbk_addr)
    local stack = {}
    for i = 0, 27 do stack[i + 1] = bus((sp + i) % 65536) end
    local stack_match = true
    for _, pin in ipairs(C.pins) do
        if stack[pin.offset + 1] ~= pin.value then stack_match = false end
    end
    -- These are D3's pinned pre-lease predicates, not a generic "not in battle" gate.
    local engine_clean = svbk % 8 < 2
        and guards.script_mode == 0 and guards.battle_mode == 0 and guards.link_mode == 0
        and guards.paused == 0 and guards.in_menu == 0 and guards.vblank == 0
        and guards.map_status == 2 and guards.map_event_status == 0
        and math.floor(guards.step_flags / 32) % 2 == 0
    return {guards=guards, svbk=svbk, stack_hex=L.hex(stack)}, engine_clean, stack_match
end
local function storage_copy(offset)
    local metadata = bytes("CartRAM", offset, C.storage.boxes * C.storage.metadata_size)
    local records = L.json.array({})
    for box = 0, C.storage.boxes - 1 do
        local base = box * C.storage.metadata_size
        for slot = 0, C.storage.slots - 1 do
            local entry = metadata[base + slot + 1]
            if entry > 0 and entry <= 207 then
                local bank_bits = metadata[base + 20 + math.floor(slot / 8) + 1]
                local db_bank = math.floor(bank_bits / 2 ^ (slot % 8)) % 2 + 1
                local location
                for _, section in ipairs(C.storage.sections) do
                    if section.db_bank == db_bank and entry >= section.first and entry <= section.last then
                        assert(location == nil, "ambiguous database section")
                        location = section.offset + (entry - section.first) * 49
                    end
                end
                assert(location ~= nil, "occupied pointer outside database contract")
                records[#records + 1] = {box=box, slot=slot, db_bank=db_bank, entry=entry,
                    record_hex=L.hex(bytes("CartRAM", location, 49))}
            end
            -- Out-of-range pointers stay in raw metadata; Python classifies the census mismatch.
        end
    end
    return {metadata_hex=L.hex(metadata), records=records}
end
local function unhook_all()
    local remaining = {}
    for _, id in ipairs(ids) do
        local removed, error_text = pcall(event.unregisterbyid, id)
        if not removed then
            driver_error(error_text, "unhook")
            remaining[#remaining + 1] = id
        end
    end
    ids = remaining
end
local function snapshot(point)
    local count = ram("party_count", 1)[1]
    assert(count <= 6, "invalid live party count")
    local party = L.json.array({})
    for slot = 0, count - 1 do
        local record = ram("party_mon", 48, slot * 48)
        party[#party + 1] = {slot=slot, record_hex=L.hex(record),
            ot_hex=L.hex(ram("party_ot", 11, slot * 11)),
            nickname_hex=L.hex(ram("party_nick", 11, slot * 11)),
            species_id=record[1] + (math.floor(record[22] / 32) % 2) * 256,
            held_item=record[2], ot_id_hex=L.hex({record[7], record[8]}),
            personality_byte=record[21], form_byte=record[22], level=record[32],
            hp=record[35] * 256 + record[36]}
    end
    local version = L.hex(bytes("ROM", C.version.offset, C.version.length))
    assert(version == C.version.hex:lower(), "overlay version changed")
    local evidence = {player_id_hex=L.hex(ram("player_id", 2)),
        player_name_hex=L.hex(ram("player_name", 8)), party_count=count, party=party,
        storage={main=storage_copy(C.storage.main), backup=storage_copy(C.storage.backup),
                 used_flags={L.hex(ram("poke_used_1", 26)), L.hex(ram("poke_used_2", 26))}},
        saved_at_least_once=ram("saved", 1)[1],
        save_version_hex=L.hex(bytes("CartRAM", C.save.version_offset, 2)),
        save_phase=byte("CartRAM", C.save.phase_offset), overlay_version_hex=version,
        engine_integrity={load_started=integrity.load_started, primary=integrity.primary,
                          backup=integrity.backup, selected=integrity.selected, corrupt=integrity.corrupt},
        stable_frame_count=stable_frame_count, checkpoint=point}
    append("snapshot", evidence)
    snapshot_count = snapshot_count + 1
    -- Stop callbacks immediately, not after the rest of this native frame has executed.
    unhook_all()
end
local function record(kind, site)
    local counter = hook_counts[kind]
    counter.total = counter.total + 1 -- Count every callback, including errored callbacks.
    local bank, pc, sp = bus(C.rombank_addr), register("PC"), register("SP")
    local matched = site.addr < 16384 or bank == site.bank
    local qualified = matched and pc == site.addr
    local retain = true
    if not matched then
        local key = string.format("%d", bank)
        counter.wrong_banks[key] = (counter.wrong_banks[key] or 0) + 1
        if counter.wrong_bank_samples >= wrong_bank_sample_limit then retain = false
        else counter.wrong_bank_samples = counter.wrong_bank_samples + 1 end
    else
        local field = qualified and "qualified" or "wrong_pc"
        counter[field] = counter[field] + 1
        if stored >= config.trace_cap then
            retain = false
            if overflows == 0 then
                overflows = 1
                append("overflow", {cap=config.trace_cap})
            end
        else
            stored = stored + 1
        end
    end
    -- Even aggregate-only callbacks recheck their pinned ROM bytes. Wrong-bank execution
    -- is not qualification: this reads the contract's ROM domain, not the current ROMX view.
    local hook_bytes = anchor(site)
    if not retain then return end
    local row = append(kind, {hook_addr=site.addr, hook_bytes=hook_bytes, pc=pc, sp=sp, bank=bank,
                             matched=matched, qualified=qualified})
    if kind == "dispatch" then
        local point, engine_clean, stack_match = checkpoint(sp)
        row.guards, row.svbk, row.stack_hex = point.guards, point.svbk, point.stack_hex
        row.engine_clean, row.stack_match = engine_clean, stack_match
        if qualified and snapshot_count == 0 then
            local eligible = integrity.load_started and integrity.selected ~= "unobserved"
                and not integrity.corrupt and engine_clean and stack_match
                and guest_writes == 0 and cpu_changes == 0 and driver_errors == 0 and overflows == 0
            if not eligible then stable_frame_count, stable_frame = 0, nil
            else
                local frame = row.frame
                if stable_frame ~= frame then
                    stable_frame_count = stable_frame == frame - 1 and stable_frame_count + 1 or 1
                    stable_frame = frame
                end
                if stable_frame_count == C.stable_frames then snapshot(point) end
            end
        end
    elseif qualified then
        native_load(kind, row)
    end
end

-- This row MUST precede hook installation; boot provenance is supplied by the parent.
append("begin", {run_id=config.run_id, provenance=config.provenance, cold_boot=true})
local ok, why = pcall(function()
    assert(C.schema == "polished-reload-contract-v1", "wrong reload contract")
    assert(C.stable_frames == 3 and C.storage.boxes == 20 and C.storage.slots == 20
           and C.storage.metadata_size == 33, "wrong reload geometry")
    integer(config.frames, 1, 2147483647, "frame budget")
    integer(config.trace_cap, 1, 2147483647, "trace capacity")
    assert(C.version.length == 20 and L.hex(bytes("ROM", C.version.offset, 20)) == C.version.hex:lower(),
           "overlay version mismatch")
    -- Validate ALL anchors before installing any callback.
    for _, kind in ipairs(site_names) do anchor(assert(C.sites[kind], "missing hook site: " .. kind)) end
    for _, kind in ipairs(site_names) do
        local site = C.sites[kind]
        ids[#ids + 1] = assert(event.on_bus_exec(function()
            local captured, error_text = pcall(record, kind, site)
            if not captured then driver_error(error_text, kind) end
        end, site.addr, "pol_reload_" .. kind, "System Bus"), "hook installation failed")
    end
    client.speedmode(300)
    for _, step in ipairs(config.steps) do
        local buttons = {}
        for _, button in ipairs(step.buttons) do buttons[button] = true end
        for _ = 1, step.frames do
            if snapshot_count > 0 or elapsed >= config.frames then break end
            L.frame(buttons)
            elapsed = elapsed + 1
        end
        if snapshot_count > 0 or elapsed >= config.frames then break end
    end
    while snapshot_count == 0 and elapsed < config.frames do
        L.frame()
        elapsed = elapsed + 1
    end
    joypad.set({})
end)
if not ok then driver_error(why) end
unhook_all()
append("final", {completed=ok and (snapshot_count == 1 or elapsed == config.frames)
    and guest_writes == 0 and cpu_changes == 0 and driver_errors == 0 and overflows == 0,
    elapsed=elapsed, guest_writes=guest_writes, cpu_changes=cpu_changes,
    driver_errors=driver_errors, overflows=overflows, snapshot_count=snapshot_count,
    wrong_bank_sample_limit=wrong_bank_sample_limit, hook_counts=hook_counts})
local final = trace[#trace]

-- Independent tiny encoder: a broken primary encoder may throw OR return nil every time.
local function minimal_json(value)
    local kind = type(value)
    if kind == "string" then
        return '"' .. value:gsub('[%z\1-\31\127-\255\\"]', function(char)
            return string.format("\\u%04x", char:byte())
        end) .. '"'
    elseif kind == "boolean" or kind == "number" then return tostring(value)
    elseif kind == "table" then
        local parts = {}
        -- Fallback only contains diagnostic/final objects; numeric keys are not arrays.
        for key, item in pairs(value) do
            parts[#parts + 1] = minimal_json(tostring(key)) .. ":" .. minimal_json(item)
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return "null"
end
local encoded_ok, encoded, encode_error = pcall(L.json.encode, trace)
local serialized = encoded_ok and type(encoded) == "string"
if not serialized then
    driver_error("trace serialization failed")
    final.completed, final.driver_errors, final.ord = false, driver_errors, 2
    local failure = {kind="driver_error", ord=1, frame=final.frame,
        error=("trace serialization failed: " .. tostring(encoded_ok and encode_error or encoded)):sub(1, 1024)}
    encoded = "[" .. minimal_json(failure) .. "," .. minimal_json(final) .. "]"
end
local written, write_error = pcall(function()
    local file = assert(io.open(L.RUN .. "/trace.json", "w"))
    local wrote, error_text = pcall(function() assert(file:write(encoded)) end)
    local closed, close_error = pcall(function() assert(file:close()) end)
    assert(wrote, error_text)
    assert(closed, close_error)
end)
if not written then
    driver_error("trace write failed: " .. tostring(write_error))
    final.completed, final.driver_errors = false, driver_errors
    pcall(print, "driver_error: trace write failed: " .. tostring(write_error))
end
-- RESULT means complete recording, including a bounded NO_HIT; it is NEVER measurement PASS.
local checked = pcall(L.check, "complete read-only reload recording", final.completed and serialized and written,
                      "snapshots " .. snapshot_count .. ", protected rows " .. stored)
if not checked then L.failures = L.failures + 1 end
-- Teardown is attempted even when trace dumping or milestone logging failed.
pcall(L.finish, "reload-probe recording")
pcall(client.exit)
error("pol-live-finished", 0)
