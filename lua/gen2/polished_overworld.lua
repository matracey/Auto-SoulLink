-- lua/gen2/polished_overworld.lua -- the Polished Crystal v3.2.3 OVERWORLD write path: the hold, the armed
-- writer and the box executor compose_polished wires in (lua/gen2/entry.lua C-COMPOSE).
--
-- WHY A PREDICATE HOLD AND NOT A PC HOLD. The vanilla Gen 2 client writes inside a bus-exec hold at the
-- checkpoint PC (OWPlayerInput, before `call CheckAPressOW`), because that is where the U2 PHYSICAL receipt
-- proved a write lands. Polished has no such PC: data/games/polished_crystal/write_checkpoint.json
-- titles.polished_crystal.primary is {"status": "UNRESOLVED", "execution_before": null} (OverworldLoop
-- 25:50d5 is the right neighbourhood, but no execution_before instruction was re-derived). Nothing here may
-- invent one. So the hold is a PREDICATE hold, evaluated in the same frame as the write, from the live
-- bytes the game itself uses (docs/polished/HELLO_GATE.md §1, docs/polished/LIVE_RESULTS.md gate_transitions):
--   wMapStatus == MAPSTATUS_HANDLE (2)  OverworldLoop runs, not the title/menu/map load
--   wScriptRunning == 0                 no script owns the game
--   wGameLogicPaused == 0               no native save is running
--   wBattleMode == 0                    not in a battle
--   wLinkMode == 0                      not on a link cable
--   sWritingBackup != 1                 no backup save in progress (CartRAM)
-- plus: the WRAM bank that owns the party is the mapped one (io.bank_valid), the permit's lifetime token is
-- the frame count (a write that crosses a frame refuses), and every write is verified by reading it back.
-- This is strictly weaker than a CPU hold: it proves the engine is between frames and idle, not that the CPU
-- sits at a proven instruction. Recorded as DEV_OVERLAY_PREDICATE_HOLD, never as a receipt.
--
-- DECLARED WRITES (and nothing else):
--   System Bus  the party block wPartyCount..wPartyMonNicknamesEnd (01:DCCE..01:DE7A, bank 1); for a faint,
--               that one record's Status (+32) and HP (+34..35) bytes.
--   CartRAM     the six newbox pokedb sections and the 20 GAMEPLAY box records (sNewBox1..20; the backup
--               copies are never writable) - NEWBOX §1.
--   WRAM        the two pokedb allocation-flag windows wPokeDB{1,2}UsedEntries - NEWBOX §2.
-- wMirrorHerbPendingBoosts (01:D284) is asserted disjoint from the party block and never appears in a range.
--
-- BOX OPS. deposit() is the vanilla box_mon contract (party -> a newbox box) built on Polished's own two
-- halves: the engine deposits by erasing the target box pointer, allocating a pokedb entry, writing it and
-- repointing the slot (UpdateStorageBoxMonFromTemp, engine/pc/bills_pc.asm:495-531), and removes a party mon
-- by shifting its slot to the end and decrementing wPartyCount (RemoveMonFromParty -> ShiftPartySlotToEnd ->
-- SwapPartyMons, bills_pc.asm:539-623). This module runs the box half first, so a reset between the halves
-- leaves a duplicate, never a loss. withdraw() and memorialize() reuse the same writer, permit and hold (see below).
local O = {}

-- The composed write kinds. "party_hp" is the name lua/gen2/client.lua asks for before it runs ANY deferred
-- command (run_deferred / at_checkpoint, the vanilla U2 kind name): it means "the overworld hold", nothing
-- more specific. The other three are what a writer authorizes, one per declared range set.
O.KINDS = {party_hp = true, party_faint = true, party_collection = true, box_deposit = true}
-- lua/gen2/client.lua run_deferred arms the faint write with this reason
O.FAINT_REASON = "overworld"
O.MAPSTATUS_HANDLE = 2            -- constants/ram_constants.asm:207-210 (HELLO_GATE.md §1)
O.QUALIFICATION = "DEV_OVERLAY_PREDICATE_HOLD"
-- sWritingBackup 00:ABE5 -> flat CartRAM (the same byte lua/gen2/polished_boxes.lua refuses on)
O.SAVING = {bank = 0, addr = 0xABE5}
-- THE HOLD SITE: `call DelayFrame` inside NextOverworldFrame (engine/overworld/events.asm:99 calls it, :114
-- defines it; the routine is 25:5185..25:51D7). It is the idle-overworld frame wait: the overworld loop reaches it
-- once per frame after MapEvents and HandleMapObjects, so a write here is between frames, out of any script that
-- has already run, and BEFORE the next frame's MapEvents (a script cannot start and finish between two of these).
-- `call z, DelayFrame` = cc a8 0d (DelayFrame 00:0DA8); that byte triple occurs ONCE in the routine - verified
-- over the built ROM by tests/unit/test_polished_write_path.py, which also re-derives both addresses from
-- data/polished/polished_slink.sym and re-reads the bytes through the executed (overlay) ROM.
O.HOLD = {bank = 0x25, pc = 0x51BF, bytes = {0xCC, 0xA8, 0x0D},
          routine = "NextOverworldFrame", routine_start = 0x5185, routine_end = 0x51D7,
          instruction = "call z, DelayFrame"}
-- The armed reason -> the kind the hold is asked for. arm() re-checks the predicate: a permit armed for a box
-- write must not be usable in a frame the hold refuses (round-2 review item 5: the box path writes through the
-- permit directly, so authorize() alone never saw it).
O.KIND_OF_REASON = {[O.FAINT_REASON] = "party_hp", party_collection = "party_collection", box_deposit = "box_deposit"}

local function integer(value, low, high)
    return type(value) == "number" and value == math.floor(value) and value >= low and value <= high
end

local function saving_byte(io)
    local at = O.SAVING
    local value = io.read_u8(at.bank * 0x2000 + at.addr - 0xA000, "CartRAM")
    return integer(value, 0, 255) and value or nil
end

-- BOX3_STAGE2 §2: byte proof is independent of executor returns and permit receipts.
-- Pure classifier input: frozen before/postimage snapshots, fresh after snapshot,
-- source_valid, context_valid, emission_started, guard_refusal, pre_ignored and stats.
-- Snapshots contain {bytes={domain={[address]=byte}}, party_count, complete=true}.
-- pre_ignored names ONLY unpublished scratch or the planned inactive withdrawal tail.
local function image_matches(want, have, ignored)
    if not want or not have or have.complete ~= true or not integer(have.party_count, 0, 6) then return false end
    if want.party_count ~= have.party_count then return false end
    for domain, bytes in pairs(want.bytes) do
        for at, value in pairs(bytes) do
            if not (ignored and ignored[domain] and ignored[domain][at]) then
                if not have.bytes[domain] or have.bytes[domain][at] ~= value then return false end
            end
        end
    end
    return true
end

function O.classify_box_write(e)
    local uncertain = {outcome = "UNCERTAIN", reason = e.reason or "box write byte proof unavailable"}
    if e.source_valid ~= true or e.context_valid ~= true then return uncertain end
    if image_matches(e.postimage, e.after) and (e.cmd ~= "party_mon" or e.after.party_count > 0) then
        return {outcome = "PROVED_COMPLETE", party_count = e.after.party_count, stats = e.stats}
    end
    if image_matches(e.before, e.after, e.pre_ignored) then
        return {outcome = "PROVED_NOT_COMPLETE", party_count = e.after.party_count, reason = e.reason}
    end
    if e.guard_refusal == true and not e.emission_started then
        return {outcome = "PROVED_NOT_COMPLETE", reason = e.reason}
    end
    return uncertain
end

-- ── the hold ────────────────────────────────────────────────────────────────────────────────────────────

--- The overworld hold. check(kind) re-reads the whole predicate set every time it is asked, so a caller
--- cannot cache an accept across frames. covers(kind) is what this graph is allowed to do at all.
--- site (optional): {bank, pc, bytes, routine, routine_start, routine_end}. When given, the hold additionally
--- requires that the CPU is executing THAT instruction in THAT bank, re-read at check time (i.e. at the moment the
--- hook fires, because the client's checkpoint hook calls check() synchronously inside the exec callback).
function O.checkpoint(profile, io, log, site)
    assert(type(profile) == "table" and profile.title == "polished" and type(profile.ram) == "table",
           "generated Polished profile required")
    assert(type(io) == "table" and type(io.read_u8) == "function" and type(io.bank_valid) == "function"
           and type(io.read_range) == "function", "explicit read_u8/read_range/bank_valid required")
    local ram, banks, hram = profile.ram, profile.ram_bank, profile.hram
    -- an HRAM register (hROMBank) is in the profile's hram table, always bank 0
    local function byte(name)
        local at, bank = ram[name], banks[name]
        local hram_at = (hram ~= nil) and hram[name] or nil
        if at == nil and hram_at ~= nil then at, bank = hram_at, 0 end
        -- WRAM ($C000-$DFFF) or HRAM ($FF80-$FFFF, bank 0): hROMBank is an HRAM register
        local wram = integer(at, 0xC000, 0xDFFF)
        local hram_window = integer(at, 0xFF80, 0xFFFF) and bank == 0
        if not (wram or hram_window) or not integer(bank, 0, 7) then return nil, "missing " .. name end
        if io.bank_valid(bank, at, 1) ~= true then return nil, name .. " bank not mapped" end
        return io.read_u8(at, "System Bus")
    end
    if site ~= nil then
        assert(integer(site.bank, 1, 0x7F) and integer(site.pc, 0x4000, 0x7FFF) and type(site.bytes) == "table",
               "a hold site needs a bank, a PC and its instruction bytes")
        assert(type(site.routine_start) == "number" and site.routine_start <= site.pc
               and site.pc + #site.bytes <= site.routine_end, "the hold site must lie inside its named routine")
        for i, byte in ipairs(site.bytes) do assert(integer(byte, 0, 255), "hold site byte out of range") end
    end
    local last
    local function refuse(why)
        if log and last ~= why then
            last = why
            log("[SLink-gen2] overworld write refused: " .. tostring(why))
        end
        return false, why
    end
    local self = {qualification = O.QUALIFICATION, facts = {}}
    function self:covers(kind) return O.KINDS[kind] == true end
    function self:check(kind)
        if not self:covers(kind) then return refuse("no composed Polished write kind " .. tostring(kind)) end
        for _, name in ipairs({"wMapStatus", "wScriptRunning", "wGameLogicPaused", "wBattleMode", "wLinkMode"}) do
            local value, why = byte(name)
            if value == nil then return refuse(why) end
            self.facts[name] = value
        end
        if self.facts.wMapStatus ~= O.MAPSTATUS_HANDLE then
            return refuse("overworld loop not running (wMapStatus $" .. string.format("%02X", self.facts.wMapStatus) .. ")")
        end
        if self.facts.wScriptRunning ~= 0 then return refuse("a script owns the game (wScriptRunning)") end
        if self.facts.wGameLogicPaused ~= 0 then return refuse("native save running (wGameLogicPaused)") end
        if self.facts.wBattleMode ~= 0 then
            return refuse("in battle (wBattleMode $" .. string.format("%02X", self.facts.wBattleMode) .. ")")
        end
        if self.facts.wLinkMode ~= 0 then return refuse("link cable active (wLinkMode)") end
        local saving = saving_byte(io)
        if saving == nil then return refuse("sWritingBackup unreadable") end
        if saving == 1 then return refuse("backup save in progress (sWritingBackup)") end
        -- the PC hold, when one is composed: the executing bank must be the site's bank and the executed bytes
        -- must still be the instruction (a patched/relocated ROM never holds the write)
        if site ~= nil then
            local rom_bank, bank_why = byte("hROMBank")
            if rom_bank == nil then return refuse(bank_why) end
            if rom_bank ~= site.bank then
                return refuse("not at the " .. site.routine .. " hold (hROMBank $" .. string.format("%02X", rom_bank) .. ")")
            end
            -- the ROM domain is addressed by its LINEAR offset, not the banked bus address
            local rom_at = site.bank * 0x4000 + (site.pc - 0x4000)
            for i, expected in ipairs(site.bytes) do
                local got = io.read_u8(rom_at + i - 1, "ROM")
                if got ~= expected then
                    return refuse("hold site bytes differ at " .. string.format("%02X:%04X", site.bank, site.pc)
                                  .. " (patched ROM)")
                end
            end
        end
        last = nil
        return true, kind
    end
    return self
end

-- ── the writer ─────────────────────────────────────────────────────────────────────────────────────────

--- coords: polished_boxes.lua's B.COORD_LABELS map ({bank, address}), exactly P.newbox_coords().
--- policy: the vanilla write_policy shape {authorize(operation, request), pointer_stable(token, addr, n),
---          provenance(domain, addr, n, reason, token), lifetime = {capture, valid}}.
function O.writes(profile, coords, io, Permit, policy, hold)
    assert(type(profile) == "table" and profile.title == "polished", "generated Polished profile required")
    assert(Permit and type(Permit.new) == "function" and type(Permit.sequence_length) == "function",
           "shared write permit factory required")
    assert(type(io) == "table" and type(io.write_u8) == "function" and type(io.read_u8) == "function"
           and type(io.bank_valid) == "function" and type(io.read_range) == "function",
           "explicit write_u8/read_u8/read_range/bank_valid required")
    assert(type(policy) == "table", "explicit ownership policy required")
    for _, name in ipairs({"authorize", "pointer_stable", "provenance"}) do
        assert(type(policy[name]) == "function", "explicit policy." .. name .. " required")
    end
    assert(type(policy.lifetime) == "table" and type(policy.lifetime.capture) == "function"
           and type(policy.lifetime.valid) == "function", "explicit lifetime capture/valid required")
    local c, s, ram, banks = profile.constants, profile.structs.party, profile.ram, profile.ram_bank
    assert(c.PARTY_LENGTH == 6 and c.NAME_LENGTH == 11 and c.MON_NAME_LENGTH == 11, "Polished party constants")
    assert(s.End == 48 and s.Status == 32 and s.HP == 34, "party_struct geometry disagrees (RAM.md §2.1)")

    -- the party block, and the derived sub-offsets (the same derivation lua/gen2/polished.lua makes)
    local block, base, bank = ram.wPartyCount, ram.wPartyMons, banks.wPartyMons
    local mons_at = base - block
    local ots_at = mons_at + c.PARTY_LENGTH * s.End
    local nicks_at = ots_at + c.PARTY_LENGTH * c.NAME_LENGTH
    local block_length = nicks_at + c.PARTY_LENGTH * c.MON_NAME_LENGTH
    assert(integer(block, 0xC000, 0xDFFF) and integer(base, 0xC000, 0xDFFF) and integer(bank, 1, 7)
           and mons_at > 0 and ram.wPartyMonOTs == block + ots_at and ram.wPartyMonNicknames == block + nicks_at
           and ram.wPartyMonNicknamesEnd == block + block_length and banks.wPartyCount == bank
           and block >= 0xD000 and block + block_length <= 0xE000,
           "party block geometry disagrees with the profile")
    -- wMirrorHerbPendingBoosts is the ENEMY party's gap; it must never be inside a declared party span
    local herb = ram.wMirrorHerbPendingBoosts
    if herb ~= nil then
        assert(not (herb < block + block_length and herb + 1 > block),
               "party block overlaps wMirrorHerbPendingBoosts")
    end

    -- the newbox spans, in the flat domains polished_boxes.lua writes (CartRAM bank*0x2000 + addr-0xA000,
    -- WRAM bank*0x1000 + addr-0xD000). Declared here so the permit can refuse anything outside them.
    local SECTIONS = {{"sBoxMons1A", 167}, {"sBoxMons1B", 28}, {"sBoxMons1C", 12},
                      {"sBoxMons2A", 167}, {"sBoxMons2B", 28}, {"sBoxMons2C", 12}}
    local spans = {CartRAM = {}, WRAM = {}}
    local function cart_span(label, length)
        local row = coords[label]
        assert(type(row) == "table" and integer(row[1], 0, 3) and integer(row[2], 0xA000, 0xBFFF)
               and row[2] + length <= 0xC000, "newbox coordinate missing or outside its SRAM bank: " .. label)
        spans.CartRAM[#spans.CartRAM + 1] = {at = row[1] * 0x2000 + row[2] - 0xA000, n = length}
    end
    local function wram_span(label, length)
        local row = coords[label]
        assert(type(row) == "table" and integer(row[1], 0, 7) and integer(row[2], 0xD000, 0xDFFF)
               and row[2] + length <= 0xE000, "newbox coordinate missing or outside its WRAM window: " .. label)
        spans.WRAM[#spans.WRAM + 1] = {at = row[1] * 0x1000 + row[2] - 0xD000, n = length}
    end
    for _, section in ipairs(SECTIONS) do cart_span(section[1], section[2] * 49) end
    for n = 1, 20 do cart_span("sNewBox" .. n, 33) end
    for d = 1, 2 do wram_span("wPokeDB" .. d .. "UsedEntries", 26) end

    local function inside(list, addr, n)
        for _, span in ipairs(list) do
            if addr >= span.at and addr + n <= span.at + span.n then return true end
        end
        return false
    end

    -- the declared System Bus ranges, per armed reason
    local ranges = {[O.FAINT_REASON] = {}, party_collection = {{addr = block, n = block_length, bank = bank}}}
    for slot = 0, c.PARTY_LENGTH - 1 do
        local at = base + slot * s.End
        table.insert(ranges[O.FAINT_REASON], {addr = at + s.Status, n = 1, bank = bank})
        table.insert(ranges[O.FAINT_REASON], {addr = at + s.HP, n = 2, bank = bank})
    end
    local function declared(reason, addr, n)
        for _, r in ipairs(ranges[reason] or {}) do
            if addr >= r.addr and addr + n <= r.addr + r.n then return r end
        end
    end
    local function permitted(reason, addr, n)
        if declared(reason, addr, n) then return true end
        return reason == "box_deposit" and (inside(spans.CartRAM, addr, n) or inside(spans.WRAM, addr, n))
    end

    -- Polished-local observer: called BEFORE underlying I/O, including swallowed/throwing writes.
    -- Legacy callers never install one; receipts and legacy return values remain unchanged.
    local box_observer
    local gate = Permit.new({
        write_u8 = function(addr, value, domain)
            if box_observer then box_observer(addr, value, domain) end
            return io.write_u8(addr, value, domain)
        end,
        domains = {
            ["System Bus"] = {
                bounds = function(addr, n, reason) return permitted(reason, addr, n) == true end,
                mapped = function(addr, n, reason)
                    local r = declared(reason, addr, n)
                    return r ~= nil and io.bank_valid(r.bank, addr, n) == true
                end,
                pointer_stable = function(addr, n, _reason, token)
                    return policy.pointer_stable(token, addr, n) == true
                end,
            },
            CartRAM = {
                bounds = function(addr, n, reason) return reason == "box_deposit" and inside(spans.CartRAM, addr, n) end,
                mapped = function(addr, n, reason) return reason == "box_deposit" and inside(spans.CartRAM, addr, n) end,
                pointer_stable = function(addr, n, _reason, token)
                    return policy.pointer_stable(token, addr, n) == true
                end,
            },
            WRAM = {
                bounds = function(addr, n, reason) return reason == "box_deposit" and inside(spans.WRAM, addr, n) end,
                mapped = function(addr, n, reason) return reason == "box_deposit" and inside(spans.WRAM, addr, n) end,
                pointer_stable = function(addr, n, _reason, token)
                    return policy.pointer_stable(token, addr, n) == true
                end,
            },
        },
        lifetime = {
            capture = policy.lifetime.capture,
            valid = function(token, reason) return policy.lifetime.valid(token, reason) == true end,
        },
        provenance = function(domain, addr, n, reason, token)
            local supplied = policy.provenance(domain, addr, n, reason, token)
            assert(type(supplied) == "table" and type(supplied.site) == "string" and supplied.site ~= "",
                   "write-site provenance required")
            local receipt = {}
            for key, value in pairs(supplied) do receipt[key] = value end
            receipt.domain, receipt.addr, receipt.n, receipt.why = domain, addr, n, reason
            receipt.title, receipt.artifact, receipt.rom_sha1 = profile.title, profile.artifact, profile.rom_sha1
            return receipt
        end,
    })

    local self = setmetatable({}, {__index = function(_, key)
        if key == "armed" or key == "log" then return gate[key] end
    end})
    -- arm re-proves the hold. The box path writes through the permit directly (polished_boxes.lua insert_mon),
    -- so without this the box bytes would never see the predicate set at all.
    function self:arm(reason, allow)
        assert(hold, "the writer needs the hold it re-checks on arm")
        local kind = O.KIND_OF_REASON[reason]
        assert(kind ~= nil, "no composed Polished write kind for the armed reason " .. tostring(reason))
        local ok, why = hold:check(kind)
        assert(ok == true, "write refused at arm(" .. tostring(reason) .. "): " .. tostring(why))
        return gate:arm(reason, allow)
    end
    function self:disarm() return gate:disarm() end
    function self:write_batch(spans_) return gate:write_batch(spans_) end
    function self:block() return block, block_length, mons_at, ots_at, nicks_at end
    function self:observe_box_write(observer)
        assert(box_observer == nil or observer == nil, "box observer already installed")
        box_observer = observer
    end
    function self:box_context(token)
        if token == nil then return policy.lifetime.capture() end
        return policy.lifetime.valid(token) == true and hold:check("party_collection") == true
    end

    local function wram(addr, n)
        assert(io.bank_valid(bank, addr, n) == true, "party WRAM bank unavailable")
        local out = io.read_range(addr, n, "System Bus")
        for i = 1, n do assert(integer(out[i], 0, 255), "unavailable or malformed System Bus read") end
        return out
    end
    local function be16(bytes, at) return bytes[at] * 256 + bytes[at + 1] end

    -- force_faint: the keyed record's HP to 0 and its Status byte to 0 (what the vanilla writer writes,
    -- lua/gen2/writes.lua faint_party_slot - a Gen 2 mon is fainted by HP 0; there is no FNT status bit).
    -- Refused unless the overworld hold authorized this operation, then both bytes are proven by read-back.
    function self:faint_party_slot(slot, snapshot)
        return gate:guard(function()
            assert(integer(slot, 0, c.PARTY_LENGTH - 1), "party slot out of range")
            assert(gate.armed == O.FAINT_REASON, "write refused: the faint gate is not armed")
            assert(snapshot == nil or (type(snapshot) == "table" and integer(snapshot.mode, 0, 255)),
                   "explicit battle snapshot required")
            assert(snapshot == nil or snapshot.mode == 0,
                   "an in-battle faint is not composed (no battle_hold receipt on Polished)")
            assert(snapshot == nil or snapshot.link_mode == nil or snapshot.link_mode == 0,
                   "linked or unknown battle context refused")
            assert(policy.authorize("party_faint", {slot = slot, snapshot = snapshot}) == true,
                   "write ownership refused")
            local at = base + slot * s.End
            local was = {status = wram(at + s.Status, 1)[1], hp = be16(wram(at + s.HP, 2), 1)}
            gate:write_batch({
                {domain = "System Bus", addr = at + s.Status, bytes = {0}},
                {domain = "System Bus", addr = at + s.HP, bytes = {0, 0}},
            })
            local now = {status = wram(at + s.Status, 1)[1], hp = be16(wram(at + s.HP, 2), 1)}
            assert(now.status == 0 and now.hp == 0,
                   "read-back refused: the party record still holds HP/status")
            return {slot = slot, hp_before = was.hp, status_before = was.status}
        end)
    end

    -- the whole party block, for a deposit's compaction (RemoveMonFromParty's shift and decrement)
    function self:write_party_block(bytes)
        return gate:guard(function()
            assert(Permit.sequence_length(bytes, "party block") == block_length, "party block length mismatch")
            assert(policy.authorize("party_collection", {length = block_length}) == true,
                   "write ownership refused")
            -- records, OT fields and nicknames first, wPartyCount LAST (the engine's order: RemoveMonFromParty
            -- decrements the count after the swaps), so an interrupted write never leaves a decremented count
            -- over records that have not shifted yet.
            local rest = {}
            for i = 2, block_length do rest[i - 1] = bytes[i] end
            gate:write_batch({{domain = "System Bus", addr = block + 1, bytes = rest},
                              {domain = "System Bus", addr = block, bytes = {bytes[1]}}})
            local after = wram(block, block_length)
            for i = 1, block_length do
                assert(after[i] == bytes[i], "read-back refused: the party block did not take the write")
            end
            return true
        end)
    end
    return self
end

-- ── the box executor (box_mon) ───────────────────────────────────────────────────────────────────────────

--- deps = {profile, reads, census, boxes (the polished_boxes.lua MODULE), reader (one B.new over the same
---         coordinates), writes, log}
function O.boxes(deps)
    assert(type(deps) == "table", "box executor deps required")
    local profile, reads, census = deps.profile, deps.reads, deps.census
    local Boxes, reader, writes = deps.boxes, deps.reader, deps.writes
    local io_, coords = deps.io, deps.coords
    assert(type(reads) == "table" and type(reads.read_party) == "function", "reads.read_party required")
    assert(type(census) == "table" and type(census.read_storage_box) == "function", "a box census reader required")
    assert(type(Boxes) == "table", "the polished_boxes.lua module required")
    assert(type(reader) == "table" and type(reader.insert_mon) == "function", "a polished_boxes.lua reader required")
    assert(type(writes) == "table" and type(writes.arm) == "function", "the overworld writer required")
    local mail = deps.mail or {}
    local log = deps.log
    -- party_mon (withdraw) dependencies. NOT asserted here: a graph composed without them still runs box_mon, and
    -- withdraw refuses by name (its own contract: nothing defaulted, a missing table is a refusal).
    local STATS, BASE, VARIANT, MOVE_PP = deps.stats, deps.base_stats, deps.variant_record, deps.move_pp
    local c, s = profile.constants, profile.structs.party
    local MEMORIAL, PER_BOX = Boxes.MEMORIAL_BOX, Boxes.MONS_PER_BOX
    local block, block_length, mons_at, ots_at, nicks_at = writes:block()
    local STRIDE = s.End + c.NAME_LENGTH + c.MON_NAME_LENGTH

    local function refuse(why) error(why, 0) end
    local function from_hex(h)
        local out = {}
        for i = 1, #h, 2 do out[#out + 1] = tonumber(h:sub(i, i + 1), 16) end
        return out
    end
    local function party_block()
        local r = reads.read_party()
        if not r then refuse("party unreadable") end
        local raw = from_hex(r.raw_hex)
        if #raw ~= block_length then refuse("party block length disagrees with the profile") end
        return r, raw
    end
    -- the 70-byte transfer blob, straight from the live block (never re-encoded)
    local function blob_of(raw, slot)
        local out = {}
        for i = 1, s.End do out[i] = raw[mons_at + slot * s.End + i] end
        for i = 1, c.NAME_LENGTH do out[s.End + i] = raw[ots_at + slot * c.NAME_LENGTH + i] end
        for i = 1, c.MON_NAME_LENGTH do
            out[s.End + c.NAME_LENGTH + i] = raw[nicks_at + slot * c.MON_NAME_LENGTH + i]
        end
        return out
    end
    -- RemoveMonFromParty's compaction: every byte from the removed slot's record to the end of the block
    -- shifts up one slot, and the count drops by one (raw is 1-based: byte offset 0 is wPartyCount, so the
    -- removed slot's first byte is raw[mons_at + slot * STRIDE + 1]). Bytes past the new count are not read
    -- by anything; the engine's ShiftPartySlotToEnd leaves the same tail it brought.
    local function removed(raw, slot)
        local out = {}
        for i = 1, #raw do out[i] = raw[i] end
        -- three parallel arrays, three strides: records (48), OT fields (11), nicknames (11). Each slot from the
        -- removed one takes its successor's bytes; the vacated tail keeps what the shift brought (the engine's
        -- ShiftPartySlotToEnd leaves the same tail). Bytes past the new count are read by nothing.
        local first = {mons_at + slot * s.End, ots_at + slot * c.NAME_LENGTH, nicks_at + slot * c.MON_NAME_LENGTH}
        local width = {s.End, c.NAME_LENGTH, c.MON_NAME_LENGTH}
        local limit = {first[1] + (c.PARTY_LENGTH - slot) * s.End, first[2] + (c.PARTY_LENGTH - slot) * c.NAME_LENGTH,
                       first[3] + (c.PARTY_LENGTH - slot) * c.MON_NAME_LENGTH}
        for array = 1, 3 do
            local from = first[array]
            -- raw is 1-based and `from` a zero-based offset: the removed slot's first byte is index from + 1.
            -- (Starting at `from` overwrote the LAST byte of the preceding slot: Codex P1, 2026-10-05.)
            for i = from + 1, limit[array] - width[array] do out[i] = raw[i + width[array]] end
        end
        out[1] = raw[1] - 1
        return out
    end
    -- the 20-box walk the client already does, as one complete census or nothing
    local function scan()
        local boxes = {}
        for index = 0, c.NUM_BOXES - 1 do
            local read, why = census.read_storage_box(index)
            if not read then return nil, why or ("box " .. (index + 1) .. " unreadable") end
            boxes[index + 1] = read.mons or {}
        end
        return boxes
    end
    local function find_key(list, key)
        local hit
        for _, mon in ipairs(list) do
            if mon.key == key then
                if hit then return nil, "ambiguous duplicate key" end
                hit = mon
            end
        end
        return hit
    end

    local self = {memorial_box = MEMORIAL, qualification = O.QUALIFICATION}

    --- box_mon. true, note on success; nil, why on refusal. Never raises.
    function self.deposit(key)
        local okr, result, note = pcall(function()
            local boxes, why = scan()
            if not boxes then refuse("box census incomplete: " .. tostring(why)) end
            local party, raw = party_block()
            local slot, mon = nil, nil
            for _, m in ipairs(party.mons) do
                if m.key == key and not m.is_egg then
                    if slot then refuse("ambiguous duplicate key") end
                    slot, mon = m.slot, m
                end
            end
            if not slot then refuse("key not in party") end
            if party.count <= 1 then refuse("last party mon") end
            -- SwapPartyMons swaps sPartyMon1Mail with the mon (DoMailSwap, engine/pc/bills_pc.asm:289-297:
            -- MAIL_STRUCT_LENGTH = $2f bytes per slot), and SLink never rewrites that SRAM block. So a removal
            -- refuses while the removed mon OR ANY LATER party slot holds Mail (ItemIsMail, home/header.asm:114:
            -- item >= FIRST_MAIL) - the vanilla rule (lua/gen2/boxes.lua no_mail_from). Mirroring the swap instead
            -- would mean declaring a 6 x $2f-byte SRAM range this writer never populates.
            for _, later in ipairs(party.mons) do
                if later.slot >= slot and mail[later.held_item] then
                    refuse("party mail in the party (slot " .. later.slot .. " holds item "
                           .. tostring(later.held_item) .. "; sPartyMail is never shifted, T-3)")
                end
            end
            local box, refusal = nil, "no free box slot"
            for index = 1, c.NUM_BOXES do
                if index ~= MEMORIAL then
                    local hit, ambiguous = find_key(boxes[index], key)
                    if ambiguous then refuse(ambiguous) end
                    if hit then refuse("key already boxed (box " .. index .. " slot " .. hit.slot .. ")") end
                    if not box and #boxes[index] < PER_BOX then box = index end
                end
            end
            if not box then refuse(refusal) end
            -- The box half first, in TWO steps with a verification between them. A reset reloads the last save, so
            -- neither half persists on its own; an exception or lifetime loss mid-write can leave a partial party
            -- until the next save/reload (the count is written last to keep that window small).
            --   step 1  stage_entry writes ONLY the sealed 49-byte entry, into an unallocated, unreferenced pokedb
            --           entry; it is read back (checksum + byte equality) before anything is published. A failure
            --           here publishes nothing: the bytes sit in a free entry the game cannot see.
            --   step 2  publish_entry sets the allocation flag, the Banks bit and the Entries pointer, which are
            --           read back below (and the neighbouring Banks bits must be unchanged) before the party is
            --           touched, so a bad entry is never published and the party original never removed for it.
            local blob = blob_of(raw, slot)
            local want = Boxes.entry_from_party(blob)
            writes:arm("box_deposit")
            local staged_ok, staged, swhy = pcall(function() return reader.stage_entry(writes, box, blob) end)
            writes:disarm()
            if not staged_ok then refuse("box write refused: " .. tostring(staged)) end
            if not staged then refuse("box " .. box .. " refused the deposit: " .. tostring(swhy)) end
            do
                local have, bad1 = reader.entry_bytes(staged.bank, staged.entry), nil
                if not Boxes.verify(have) then bad1 = "entry checksum fails" end
                for i = 1, #want do
                    if have[i] ~= want[i] then bad1 = bad1 or "entry bytes differ from the sealed entry" end
                end
                if bad1 then
                    refuse("deposit read-back refused: " .. bad1 .. " (nothing published: the entry is in an unallocated,"
                           .. " unreferenced pokedb slot; party untouched)")
                end
            end
            local banks_before = reader.banks_byte(box, staged.slot)
            writes:arm("box_deposit")
            local boxed, placed, bwhy = pcall(function() return reader.publish_entry(writes, staged) end)
            writes:disarm()
            local PUBLISHED = " (the entry may be partly published; party untouched)"
            if not boxed then refuse("box publish refused: " .. tostring(placed) .. PUBLISHED) end
            if not placed then refuse("box " .. box .. " refused the publish: " .. tostring(bwhy) .. PUBLISHED) end
            -- READ-BACK 0 (before the party half): the entry bytes (checksum + equality with what was sealed), the
            -- WRAM allocation flag, the Banks bit and the Entries pointer byte, re-read through the box reader's io.
            do
                local snap = reader.read_boxes("gameplay")
                local function at(list)
                    for _, w in ipairs(list or {}) do
                        if w.box == box and w.slot == placed.slot then return w end
                    end
                end
                local bad
                if not snap then bad = "the box census is unreadable"
                elseif at(snap.unflagged) then bad = "allocation flag not set"
                elseif at(snap.bad_eggs) then bad = "entry checksum fails"
                elseif at(snap.invalid) then bad = "invalid pointer or record"
                else
                    local got = at(snap.mons)
                    if not got then bad = "Entries pointer byte does not point at the entry"
                    elseif got.entry ~= placed.entry then bad = "Entries pointer byte"
                    elseif got.bank ~= placed.bank then bad = "Banks bit"
                    else
                        local have = from_hex(got.raw_hex)
                        if not Boxes.verify(have) then bad = "entry checksum fails" end
                        for i = 1, #want do
                            if have[i] ~= want[i] then bad = bad or "entry bytes differ from the sealed entry" end
                        end
                    end
                end
                local mask = 1 << ((placed.slot - 1) & 7)
                if not bad and ((banks_before ~ reader.banks_byte(box, placed.slot)) & ~mask & 0xFF) ~= 0 then
                    bad = "neighbouring Banks bits changed"
                end
                if bad then refuse("deposit read-back refused: " .. bad .. PUBLISHED) end
            end
            writes:arm("party_collection")
            local dropped, dwhy = pcall(function() return writes:write_party_block(removed(raw, slot)) end)
            writes:disarm()
            if not dropped then refuse("party compaction refused: " .. tostring(dwhy)) end
            -- READ-BACK 1: the party. The count dropped by one and the key is gone from it.
            local after = reads.read_party()
            if not after then refuse("read-back refused: the party became unreadable") end
            if after.count ~= party.count - 1 then
                refuse("read-back refused: party count " .. party.count .. " -> " .. tostring(after.count))
            end
            for _, m in ipairs(after.mons) do
                if m.key == key then refuse("read-back refused: the mon is still in the party") end
            end
            -- READ-BACK 2: the box. A fresh census re-reads the pointer byte, the WRAM allocation flag and the
            -- pokedb entry itself, and a bad checksum / unflagged pointer / corrupt pointer makes it
            -- incomplete (polished.lua P.census) - so this proves the entry the game will decode.
            local after_boxes, cwhy = scan()
            if not after_boxes then refuse("read-back refused: the census no longer completes (" .. tostring(cwhy) .. ")") end
            local found = find_key(after_boxes[box], key)
            if not found then refuse("read-back refused: box " .. box .. " does not read the mon back") end
            -- the census reports slots 0-based (client.lua's box_entry contract), insert_mon reports them 1-based
            if found.slot + 1 ~= placed.slot or found.bank ~= placed.bank or found.entry ~= placed.entry then
                refuse("read-back refused: box " .. box .. " slot " .. tostring(found.slot + 1)
                       .. " entry " .. tostring(found.bank) .. "/" .. tostring(found.entry)
                       .. " is not the written one")
            end
            return true, {box = box, slot = placed.slot, entry = placed.entry, bank = placed.bank,
                          durability = placed.durability, nickname = mon.nickname}
        end)
        if okr then return result, note end
        return nil, tostring(result)
    end

    -- ── party_mon (withdraw) — docs/polished/WITHDRAW.md §1-§2 ───────────────────────────────────────────────
    -- The engine (engine/pc/bills_pc.asm SetStorageBoxPointer .party :582-613, GetStorageMon :1342,
    -- DecodeTempMon :887, SetTempPartyMonData :960) appends to the party and clears only the box slot's pointer
    -- byte and its Banks bit. It never frees the pokedb entry: those bytes, the name-MSB checksum and the
    -- allocation flag stay, and NewStoragePointer (:392) reuses the entry later. This executor follows that
    -- exactly, but PARTY half first (count last), verified, THEN the box half: a failure or lifetime loss in
    -- between leaves the mon in BOTH places (a retry reconciles), never in neither. (The earlier box-first order
    -- left ZERO live copies on 72 of 73 injected failure points.)
    -- Known limit: reconciliation matches by identity key plus byte equality, so two byte-identical INDEPENDENT mons
    -- (one boxed, one in the party) collapse into one on a reconcile; the key cannot tell them apart.
    -- sNewBox<n> flat CartRAM + the 0x21 record stride, and the Banks byte at +0x14 (NEWBOX §1.1).
    -- Declared BEFORE require_withdraw, which assigns it: as a later `local`, the assignment compiled
    -- to a global and every withdraw raised inside record_flat (introduced in a0487e8bd).
    local BOX1
    local function require_withdraw()
        if type(io_) ~= "table" or type(io_.read_u8) ~= "function" then
            refuse("withdraw is not composed: no live io for the option bytes")
        end
        if type(coords) ~= "table" or type(coords.sNewBox1) ~= "table" then
            refuse("withdraw is not composed: no newbox coordinates for the box record")
        end
        BOX1 = coords.sNewBox1[1] * 0x2000 + coords.sNewBox1[2] - 0xA000
        if type(STATS) ~= "table" or type(STATS.party_from_savemon) ~= "function" then
            refuse("withdraw is not composed: polished_stats.lua is not loaded")
        end
        if type(BASE) ~= "table" or type(VARIANT) ~= "table" or type(MOVE_PP) ~= "table" then
            refuse("withdraw is not composed: base_stats / variant_record / move_pp are all required")
        end
    end
    -- NATURES_OPT bit 0, PERFECT_IVS_OPT bit 3 (constants/ram_constants.asm:101,104);
    -- EV_OPTMASK EQU %11 = $03 (constants/ram_constants.asm:125) - NOT %1011: bit 3 of
    -- wInitialOptions2 is the RTC option, so $0B would enable EVs on a save that has them off.
    local NATURES_OPT, PERFECT_IVS_OPT, EV_OPTMASK = 0, 3, 0x03
    -- wInitialOptions 00:CFF6 / wInitialOptions2 00:CFF7 (constants/ram_constants.asm:101,104,125). WRAM0, so bank 0.
    local OPT1, OPT2 = 0xCFF6, 0xCFF7
    local function live_options()
        for _, at in ipairs({OPT1, OPT2}) do
            if io_.bank_valid(0, at, 1) ~= true then return nil, "options: WRAM0 is not mapped" end
        end
        local a, b = io_.read_u8(OPT1, "System Bus"), io_.read_u8(OPT2, "System Bus")
        if not integer(a, 0, 255) or not integer(b, 0, 255) then
            return nil, "options: wInitialOptions unreadable"
        end
        return {apply_evs = (b & EV_OPTMASK) ~= 0, natures_on = ((a >> NATURES_OPT) & 1) == 1,
                perfect_ivs = ((a >> PERFECT_IVS_OPT) & 1) == 1}
    end
    -- sNewBox<n> flat CartRAM + the 0x21 record stride, and the Banks byte at +0x14 (NEWBOX §1.1)
    local function record_flat(box) return BOX1 + 0x21 * (box - 1) end
    local function hex_bytes(h, n, what)
        local out = {}
        if type(h) ~= "string" or #h ~= 2 * n then refuse(what .. " is not " .. n .. " bytes") end
        for i = 1, n do out[i] = tonumber(h:sub(2 * i - 1, 2 * i), 16) end
        return out
    end
    local function pad(out, n)
        for i = #out + 1, n do out[i] = 0 end
        if #out ~= n then refuse("name field is " .. #out .. " bytes, expected " .. n) end
        return out
    end

    --- party_mon. true, note on success; nil, why on refusal. Never raises.
    --- Order (docs/polished/WITHDRAW.md section 2): the PARTY half first (record, OT, nickname, wPartyCount LAST), a
    --- read-back of the appended slot, THEN the box half (Entries byte + Banks bit), then the final read-back. Any
    --- failure between the halves leaves the mon in BOTH places, never in neither; withdraw() on that both-places
    --- state removes the box copy idempotently (the party copy must equal the rebuilt record) instead of refusing.
    function self.withdraw(key)
        local okr, result, note = pcall(function()
            require_withdraw()
            local boxes, why = scan()
            if not boxes then refuse("box census incomplete: " .. tostring(why)) end
            local party = party_block()
            local found, box, hits = nil, nil, 0
            for index = 1, c.NUM_BOXES do
                for _, m in ipairs(boxes[index] or {}) do
                    if m.key == key then
                        hits = hits + 1
                        found, box = m, index
                    end
                end
            end
            if hits > 1 then refuse("ambiguous duplicate boxed key") end
            if not found then
                -- IDEMPOTENT: a duplicate party_mon (a queued repeat, a re-send) for a mon that already moved finds it in no
                -- box but in the party, which IS the withdraw's postcondition. That is a done (a re-ack), never a refusal:
                -- the server reads `key not boxed` as a failed retrieval and re-boxes the partner. No write, no cache: the
                -- party is read fresh, so a later deposit and a legitimate new withdraw are unaffected.
                local already, awhy = find_key(party.mons, key)
                if awhy then refuse(awhy .. " in the party") end
                if already and not already.is_egg then
                    -- a DEAD mon (HP 0) still physically in the party must not be re-announced as retrieved: the server's
                    -- sync_retrieve_done would add its key back to party_keys after the death handler removed it
                    -- (cx-082b6600). A refusal here is right for the server too: a dead pair is not ALIVE, so the failure
                    -- handler discards the (already absent) key and re-boxes nobody.
                    if (already.hp or 0) == 0 then refuse("dead in the party (hp 0)") end
                    return true, {already_in_party = true, slot = already.slot}
                end
                refuse("key not boxed")
            end
            -- Eggs are off the wire, but the executor does not rely on that: an egg is never withdrawn as a mon. And
            -- native Polished never lets a mail holder into a box (bills_pc.asm:135-139 PCSWAP_HOLDING_MAIL), so a
            -- boxed record holding Mail has no mail SRAM to come back with; refuse both before any write.
            if found.is_egg then refuse("egg in the box") end
            if mail[found.held_item] then refuse("mail in the box (item " .. tostring(found.held_item) .. ")") end
            -- a second party match is an ambiguity, not "absent": refuse by name before any write
            local pmon, pwhy = find_key(party.mons, key)
            if pwhy then refuse(pwhy .. " in the party") end
            -- the 49-byte savemon, checksum re-proved here (the census already refuses Bad Eggs; a change between
            -- the scan and now would be a torn image)
            local entry = hex_bytes(found.raw_hex, 49, "box entry")
            if not Boxes.verify(entry) then
                refuse("box entry checksum mismatch (a Bad Egg is never withdrawn)")
            end
            local opts, owhy = live_options()
            if not opts then refuse(owhy) end
            local record, view = STATS.party_from_savemon(entry, {
                apply_evs = opts.apply_evs, natures_on = opts.natures_on, perfect_ivs = opts.perfect_ivs,
                base_stats = BASE, variant_record = VARIANT, move_pp = MOVE_PP})
            if not record then refuse("stat rebuild refused: " .. tostring(view)) end
            -- nickname 11 B (already decoded back to the party-side encoding), OT 8 name bytes + 3 EXTRA
            -- (savemon bytes 30..32, which carry hyper training - bills_pc.asm:960 reads the mask from
            -- wTempMonOT + PLAYER_NAME_LENGTH)
            local nick = pad(hex_bytes(found.nickname_raw_hex, 11, "nickname"), 11)
            local ot = {}
            for i = 1, 8 do ot[i] = hex_bytes(found.ot_raw_hex, 8, "OT name")[i] end
            for i = 1, 3 do ot[8 + i] = entry[29 + i] end
            -- what the party slot must hold: record 48 + OT 11 + nickname 11, the blob_of layout
            local want = {}
            for i = 1, #record do want[#want + 1] = record[i] end
            for i = 1, #ot do want[#want + 1] = ot[i] end
            for i = 1, #nick do want[#want + 1] = nick[i] end
            if #want ~= STRIDE then refuse("rebuilt party slot is " .. #want .. " bytes, expected " .. STRIDE) end
            local function slot_is_want(raw, slot)
                local have = blob_of(raw, slot)
                for i = 1, STRIDE do
                    if have[i] ~= want[i] then return false end
                end
                return true
            end
            local rec = record_flat(box)
            local slot = found.slot                          -- 0-based, as the census reports it
            local entries_at = rec + slot
            local bits_at = rec + 0x14 + (slot >> 3)
            local append = pmon == nil
            local target, expected = nil, party.count
            if append then
                if party.count >= c.PARTY_LENGTH then refuse("party full (" .. party.count .. "/" .. c.PARTY_LENGTH .. ")") end
                target, expected = party.count, party.count + 1  -- append: the engine only raises the count past it
            else
                -- BOTH-PLACES state (an earlier withdraw died between its halves). Finish the box removal only if
                -- the party copy is byte-for-byte what this withdraw would have appended.
                target = pmon.slot
                local now, praw = party_block()
                if target >= now.count then refuse("party changed during withdraw") end
                if not slot_is_want(praw, target) then
                    refuse("withdraw reconcile refused: the party copy differs from the boxed entry (the box copy is kept)")
                end
            end
            -- ── PARTY HALF: record, OT, nickname, wPartyCount LAST, permit = exactly those spans ──
            local function append_to_party()
                -- the slot index was planned from an earlier read: prove the party did not move under it
                local live = party_block()
                if live.count ~= party.count then refuse("party changed during withdraw") end
                local at_rec = block + mons_at + target * s.End
                local at_ot = block + ots_at + target * c.NAME_LENGTH
                local at_nick = block + nicks_at + target * c.MON_NAME_LENGTH
                writes:arm("party_collection", function(domain, addr, n)
                    return domain == "System Bus" and ((addr == at_rec and n == s.End) or (addr == at_ot and n == c.NAME_LENGTH)
                           or (addr == at_nick and n == c.MON_NAME_LENGTH) or (addr == block and n == 1))
                end)
                local okp, perr = pcall(function()
                    writes:write_batch({
                        {domain = "System Bus", addr = at_rec, bytes = record},
                        {domain = "System Bus", addr = at_ot, bytes = ot},
                        {domain = "System Bus", addr = at_nick, bytes = nick},
                        {domain = "System Bus", addr = block, bytes = {party.count + 1}},
                    })
                end)
                writes:disarm()
                if not okp then refuse("party half refused: " .. tostring(perr) .. " (the box copy is untouched)") end
                -- READ-BACK 1: the appended slot is exactly what was built and the count rose by one
                local after, araw = party_block()
                if after.count ~= expected then
                    refuse("read-back refused: party count " .. party.count .. " -> " .. tostring(after.count)
                           .. " (the box copy is untouched)")
                end
                if not slot_is_want(araw, target) then
                    refuse("read-back refused: the appended party slot differs from what was built (the box copy is untouched)")
                end
                local landed = find_key(after.mons, key)
                if not landed then refuse("read-back refused: the withdrawn mon is not in the party (the box copy is untouched)") end
                if landed.hp ~= view.hp or landed.status ~= 0 then
                    refuse("read-back refused: hp " .. tostring(landed.hp) .. "/" .. tostring(view.hp)
                           .. " status " .. tostring(landed.status) .. " (the box copy is untouched)")
                end
            end
            -- ── BOX HALF: the slot pointer byte and its Banks bit, nothing else, permit = exactly those two ──
            -- Two steps, each verified. The Entries byte goes first and ALONE: for a BANK-2 mon the Banks bit is what
            -- selects the pokedb bank, so clearing it while a silently failed Entries write left a non-zero pointer
            -- would redirect that pointer into bank 1 (an unallocated entry: the census then refuses everything and
            -- a retry cannot reconcile). The Banks bit is only touched once the Entries byte is proved 0.
            local cleared
            local function remove_box_copy()
                writes:arm("box_deposit", function(domain, addr, n)
                    return domain == "CartRAM" and n == 1 and addr == entries_at
                end)
                local oke, eerr = pcall(function()
                    writes:write_batch({{domain = "CartRAM", addr = entries_at, bytes = {0}}})
                end)
                writes:disarm()
                if not oke then
                    refuse("box half refused: " .. tostring(eerr) .. " (the mon is in the party AND the box; a retry reconciles)")
                end
                if io_.read_u8(entries_at, "CartRAM") ~= 0 then
                    refuse("withdraw read-back refused: Entries byte not cleared (box untouched beyond that byte)")
                end
                local bits = io_.read_u8(bits_at, "CartRAM")
                if not integer(bits, 0, 255) then refuse("box Banks byte unreadable (the Entries byte is already clear)") end
                cleared = bits & ~(1 << (slot & 7)) & 0xFF
                writes:arm("box_deposit", function(domain, addr, n)
                    return domain == "CartRAM" and n == 1 and addr == bits_at
                end)
                local okb, werr = pcall(function()
                    writes:write_batch({{domain = "CartRAM", addr = bits_at, bytes = {cleared}}})
                end)
                writes:disarm()
                if not okb then
                    refuse("box half refused: " .. tostring(werr) .. " (the mon is in the party; the Entries byte is clear)")
                end
                if io_.read_u8(bits_at, "CartRAM") ~= cleared then
                    refuse("withdraw read-back refused: Banks byte not cleared (the mon is in the party; the Entries byte is clear)")
                end
            end
            if append then append_to_party() end
            remove_box_copy()
            -- ── FINAL READ-BACK: the box no longer reads the mon (slot empty, pokedb entry untouched), the party does ──
            local after, araw = party_block()
            if after.count ~= expected then
                refuse("read-back refused: party count " .. party.count .. " -> " .. tostring(after.count))
            end
            if not find_key(after.mons, key) or not slot_is_want(araw, target) then
                refuse("read-back refused: the withdrawn mon is not in the party")
            end
            local after_boxes, cwhy = scan()
            if not after_boxes then
                refuse("read-back refused: the census no longer completes (" .. tostring(cwhy) .. ")")
            end
            if find_key(after_boxes[box], key) then
                refuse("read-back refused: box " .. box .. " still reads the mon")
            end
            if io_.read_u8(entries_at, "CartRAM") ~= 0 then refuse("read-back refused: the Entries byte did not clear") end
            if io_.read_u8(bits_at, "CartRAM") ~= cleared then
                refuse("read-back refused: the Banks bit did not clear")
            end
            return true, {box = box, from_slot = slot + 1, slot = target, hp = view.hp, max_hp = view.max_hp,
                          level = view.level, durability = "VOLATILE_UNTIL_NATIVE_SAVE",
                          reconciled = (not append) or nil,
                          message = (not append) and "withdraw reconciled: removed the box copy" or nil}
        end)
        if okr then return result, note end
        return nil, tostring(result)
    end

    -- ── memorialize — docs/polished/MEMORIALIZE.md (design of record; owner ruling 2026-10-09: in RC 1) ──────────
    -- The memorial box is box 20 (Boxes.MEMORIAL_BOX; gen2_polished.py memorial_box_index() = 19), settled by NEWBOX
    -- §6.2 "Set M = 20". Ordinary deposit never uses it. Two origins, the SAME writer/permit/hold as deposit/withdraw:
    --   PARTY  the deposit's own halves into box 20: stage a FRESH pokedb entry (bank 1 then 2, unflagged and
    --          unreferenced by the gameplay AND backup records), verify it, publish flag/Banks/Entries into the FIRST
    --          EMPTY slot of box 20 (holes included), verify, then compact the party (count last), verify. The entry is
    --          entry_from_party: no status, no HP. Refused before any write: an egg, the last party mon (count-based,
    --          as vanilla boxes.lua; no healthy-survivor rule), Mail on the removed or a later slot, a key also in boxes
    --          1-19. An interrupted attempt whose memorial copy is PUBLISHED is finished (party compaction only) when that
    --          copy is byte-for-byte the entry this mon encodes to and carries its key - never on the key alone.
    --   BOX    a key in boxes 1-19: the SAME (bank, entry) is published in box 20 (Banks bit, then Entries byte) and
    --          READ BACK, and only then is the source's Entries byte cleared and read back. No allocation, no re-encode,
    --          no flag change. (Banks/move_to_memorial does both in one batch, so a silently dropped destination write
    --          could be followed by the source delete; this does not use it.) The source's Banks bit is left as junk on
    --          an empty slot (NEWBOX §1.1: an empty slot's bit is never read, and a deposit rewrites it before its
    --          Entries byte), which also avoids the bank-2 redirect hazard withdraw documents.
    -- A key already ONLY in box 20 is a verified no-op success (even with box 20 full). Client contract (client.lua
    -- run_box): success is a bare `true` (a truthy second value would be concatenated or queued as a settle); the only
    -- retry is `last party mon`; `memorial box full` and `no free pokedb entry: native save required` verbatim.
    local function box_io(what)
        if type(io_) ~= "table" or type(io_.read_u8) ~= "function" then
            refuse(what .. " is not composed: no live io for the box record")
        end
        if type(coords) ~= "table" or type(coords.sNewBox1) ~= "table" then
            refuse(what .. " is not composed: no newbox coordinates for the box record")
        end
        BOX1 = coords.sNewBox1[1] * 0x2000 + coords.sNewBox1[2] - 0xA000
    end
    local function cart_byte(at)
        local v = io_.read_u8(at, "CartRAM")
        if not integer(v, 0, 255) then refuse("box record byte unreadable") end
        return v
    end
    local function one_byte(at, value, why)
        writes:arm("box_deposit", function(domain, addr, n) return domain == "CartRAM" and n == 1 and addr == at end)
        local ok, err = pcall(function() writes:write_batch({{domain = "CartRAM", addr = at, bytes = {value}}}) end)
        writes:disarm()
        if not ok then refuse(why .. " refused: " .. tostring(err)) end
        if cart_byte(at) ~= value then refuse(why .. " read-back refused") end
    end
    local function memorial_from_party(key, party, raw, pmon, buried)
        if party.count <= 1 then refuse("last party mon") end
        for _, later in ipairs(party.mons) do
            if later.slot >= pmon.slot and mail[later.held_item] then
                refuse("party mail in the party (slot " .. later.slot .. " holds item "
                       .. tostring(later.held_item) .. "; sPartyMail is never shifted, T-3)")
            end
        end
        local blob = blob_of(raw, pmon.slot)
        local want = Boxes.entry_from_party(blob)
        local placed
        if buried then
            -- INTERRUPTED ATTEMPT: the published copy must be exactly this mon's encoding (the census already matched
            -- the key); otherwise it is another mon under the same key and both are kept.
            local have = from_hex(buried.raw_hex)
            for i = 1, #want do
                if have[i] ~= want[i] then
                    refuse("memorial reconcile refused: the box " .. MEMORIAL .. " copy differs from the party mon (both kept)")
                end
            end
            placed = {slot = buried.slot + 1, bank = buried.bank, entry = buried.entry}
        else
            writes:arm("box_deposit")
            local staged_ok, staged, swhy = pcall(function() return reader.stage_entry(writes, MEMORIAL, blob) end)
            writes:disarm()
            if not staged_ok then refuse("box write refused: " .. tostring(staged)) end
            if not staged then
                swhy = tostring(swhy)
                refuse(swhy == "box full" and "memorial box full" or swhy)
            end
            local have = reader.entry_bytes(staged.bank, staged.entry)
            local bad = not Boxes.verify(have) and "entry checksum fails" or nil
            for i = 1, #want do
                if have[i] ~= want[i] then bad = bad or "entry bytes differ from the sealed entry" end
            end
            if bad then
                refuse("memorial read-back refused: " .. bad .. " (nothing published: the entry is in an unallocated,"
                       .. " unreferenced pokedb slot; party untouched)")
            end
            local banks_before = reader.banks_byte(MEMORIAL, staged.slot)
            writes:arm("box_deposit")
            local pub_ok, pub, pwhy = pcall(function() return reader.publish_entry(writes, staged) end)
            writes:disarm()
            local PUBLISHED = " (the entry may be partly published; party untouched)"
            if not pub_ok then refuse("box publish refused: " .. tostring(pub) .. PUBLISHED) end
            if not pub then refuse("box " .. MEMORIAL .. " refused the publish: " .. tostring(pwhy) .. PUBLISHED) end
            local snap = reader.read_boxes("gameplay")
            local got
            for _, w in ipairs((snap and snap.mons) or {}) do
                if w.box == MEMORIAL and w.slot == pub.slot then got = w end
            end
            bad = nil
            if not snap or not snap.complete then bad = "the box census is incomplete"
            elseif not got then bad = "box " .. MEMORIAL .. " does not read the entry back (flag, Banks or Entries)"
            elseif got.entry ~= pub.entry or got.bank ~= pub.bank then bad = "Entries pointer / Banks bit"
            else
                local back = from_hex(got.raw_hex)
                for i = 1, #want do if back[i] ~= want[i] then bad = "entry bytes differ from the sealed entry" end end
            end
            local mask = 1 << ((pub.slot - 1) & 7)
            if not bad and ((banks_before ~ reader.banks_byte(MEMORIAL, pub.slot)) & ~mask & 0xFF) ~= 0 then
                bad = "neighbouring Banks bits changed"
            end
            if bad then refuse("memorial read-back refused: " .. bad .. PUBLISHED) end
            placed = pub
        end
        writes:arm("party_collection")
        local dropped, dwhy = pcall(function() return writes:write_party_block(removed(raw, pmon.slot)) end)
        writes:disarm()
        if not dropped then refuse("party compaction refused: " .. tostring(dwhy)) end
        local after = reads.read_party()
        if not after then refuse("read-back refused: the party became unreadable") end
        if after.count ~= party.count - 1 then
            refuse("read-back refused: party count " .. party.count .. " -> " .. tostring(after.count))
        end
        for _, m in ipairs(after.mons) do
            if m.key == key then refuse("read-back refused: the mon is still in the party") end
        end
        local after_boxes, cwhy = scan()
        if not after_boxes then refuse("read-back refused: the census no longer completes (" .. tostring(cwhy) .. ")") end
        local found = find_key(after_boxes[MEMORIAL], key)
        if not found or found.slot + 1 ~= placed.slot or found.bank ~= placed.bank or found.entry ~= placed.entry then
            refuse("read-back refused: box " .. MEMORIAL .. " does not read the buried mon at the written slot")
        end
        if log then
            log("[SLink-gen2] memorialize " .. tostring(key) .. ": party -> box " .. MEMORIAL .. " slot " .. placed.slot
                .. " entry " .. placed.bank .. "/" .. placed.entry .. (buried and " (reconciled)" or ""))
        end
        return true
    end
    local function memorial_from_box(key, source, buried, boxes)
        box_io("memorialize")
        if buried then
            -- INTERRUPTED box-origin attempt (destination published, source not yet cleared): both slots must point at
            -- the SAME pokedb entry; anything else is two mons under one key.
            if buried.bank ~= source.bank or buried.entry ~= source.entry then
                refuse("ambiguous: key in box " .. source.box .. " and box " .. MEMORIAL .. " at different entries")
            end
        elseif #(boxes[MEMORIAL] or {}) >= PER_BOX then
            refuse("memorial box full")
        end
        local mem = record_flat(MEMORIAL)
        local slot = buried and (buried.slot + 1)
        if not slot then
            for s_ = 1, PER_BOX do
                if cart_byte(mem + s_ - 1) == 0 then slot = s_; break end
            end
            if not slot then refuse("memorial box full") end
            -- DESTINATION: the Banks bit (only this slot's), then the Entries byte; each read back BEFORE the source moves
            local bits_at, mask = mem + 0x14 + ((slot - 1) >> 3), 1 << ((slot - 1) & 7)
            local before = cart_byte(bits_at)
            local want = source.bank == 2 and (before | mask) or (before & ~mask & 0xFF)
            if want ~= before then one_byte(bits_at, want, "memorial Banks bit") end
            one_byte(mem + slot - 1, source.entry, "memorial Entries byte")
            local snap = reader.read_boxes("gameplay")
            local got
            for _, w in ipairs((snap and snap.mons) or {}) do
                if w.box == MEMORIAL and w.slot == slot then got = w end
            end
            if not snap or not snap.complete or not got or got.bank ~= source.bank or got.entry ~= source.entry
               or got.raw_hex ~= source.raw_hex then
                refuse("memorial read-back refused: box " .. MEMORIAL .. " slot " .. slot
                       .. " does not read the source entry (source untouched)")
            end
        end
        -- SOURCE: its Entries byte only, read back
        one_byte(record_flat(source.box) + source.slot, 0, "memorial source Entries byte")
        local after_boxes, cwhy = scan()
        if not after_boxes then refuse("read-back refused: the census no longer completes (" .. tostring(cwhy) .. ")") end
        local found = find_key(after_boxes[MEMORIAL], key)
        if not found or found.slot + 1 ~= slot or found.entry ~= source.entry or found.bank ~= source.bank then
            refuse("read-back refused: box " .. MEMORIAL .. " does not read the buried mon at the written slot")
        end
        if find_key(after_boxes[source.box], key) then refuse("read-back refused: box " .. source.box .. " still reads the mon") end
        if log then
            log("[SLink-gen2] memorialize " .. tostring(key) .. ": box " .. source.box .. " slot " .. (source.slot + 1)
                .. " -> box " .. MEMORIAL .. " slot " .. slot .. " entry " .. source.bank .. "/" .. source.entry
                .. (buried and " (reconciled)" or ""))
        end
        return true
    end

    --- memorialize. Bare true on success (or a verified already-buried no-op); nil, why on refusal. Never raises.
    function self.memorialize(key)
        local okr, result = pcall(function()
            local boxes, why = scan()
            if not boxes then refuse("box census incomplete: " .. tostring(why)) end
            local party, raw = party_block()
            local pmon, pwhy = find_key(party.mons, key)
            if pwhy then refuse(pwhy .. " in the party") end
            local buried, bwhy = find_key(boxes[MEMORIAL] or {}, key)
            if bwhy then refuse(bwhy .. " in box " .. MEMORIAL) end
            local source, sbox
            for index = 1, c.NUM_BOXES do
                if index ~= MEMORIAL then
                    for _, m in ipairs(boxes[index] or {}) do
                        if m.key == key then
                            if source then refuse("ambiguous duplicate boxed key") end
                            source, sbox = m, index
                        end
                    end
                end
            end
            if pmon then
                if pmon.is_egg then refuse("egg in the party") end
                if source then refuse("ambiguous: key in the party and box " .. sbox) end
                return memorial_from_party(key, party, raw, pmon, buried)
            end
            if source then
                source.box = sbox
                return memorial_from_box(key, source, buried, boxes)
            end
            if buried then return true end                -- already buried, verified by this census
            refuse("key not in party or boxes")
        end)
        if okr then return result end
        return nil, tostring(result)
    end
    -- A settle only exists after a native save witness; Polished composes no save site, so there is never one.
    function self.settle() return true end
    function self.settle_memorial() return true end
    -- Read-only BOX3 planner. It uses the SAME compaction, sealing and stat codec as
    -- the legacy executor; no permit is armed and allocation is chosen before staging.
    -- All pokedb bytes and both record copies are frozen, including unused Banks bits.
    local function clone(value)
        if type(value) ~= "table" then return value end
        local out = {}
        for k, v in pairs(value) do out[k] = clone(v) end
        return out
    end
    local function flat(label, domain)
        local row = assert(coords[label], "missing box coordinate " .. label)
        if domain == "CartRAM" then return row[1] * 0x2000 + row[2] - 0xA000 end
        return row[1] * 0x1000 + row[2] - 0xD000
    end
    local function entry_address(bank, entry)
        for _, section in ipairs({{"A", 1, 167}, {"B", 168, 195}, {"C", 196, 207}}) do
            if entry >= section[2] and entry <= section[3] then
                return flat("sBoxMons" .. bank .. section[1], "CartRAM") + (entry - section[2]) * 49
            end
        end
        refuse("entry outside pokedb")
    end
    local function snapshot()
        local party, raw = party_block()
        local snap = {bytes = {["System Bus"] = {}, CartRAM = {}, WRAM = {}}, party_count = party.count}
        for i, v in ipairs(raw) do snap.bytes["System Bus"][block + i - 1] = v end
        local function span(domain, at, n)
            for a = at, at + n - 1 do
                local v = io_.read_u8(a, domain)
                if not integer(v, 0, 255) then refuse("box proof read unavailable") end
                snap.bytes[domain][a] = v
            end
        end
        for box = 1, MEMORIAL do
            span("CartRAM", flat("sNewBox" .. box, "CartRAM"), 33)
            span("CartRAM", flat("sBackupNewBox" .. box, "CartRAM"), 33)
        end
        for bank = 1, 2 do
            span("WRAM", flat("wPokeDB" .. bank .. "UsedEntries", "WRAM"), 26)
            for entry = 1, 207 do span("CartRAM", entry_address(bank, entry), 49) end
        end
        local census_ = reader.read_boxes("gameplay")
        if not census_ or not census_.complete or #census_.bad_eggs > 0 or #census_.unflagged > 0 then
            refuse("box proof census incomplete")
        end
        -- The decoded census must agree with frozen raw pointers/entries, not
        -- merely be complete on a second, potentially contradictory read.
        local pointers = 0
        for box = 1, MEMORIAL do
            local at = flat("sNewBox" .. box, "CartRAM")
            for slot = 0, PER_BOX - 1 do
                if snap.bytes.CartRAM[at + slot] ~= 0 then pointers = pointers + 1 end
            end
        end
        if pointers ~= #census_.mons then refuse("box proof census/raw pointers disagree") end
        for _, mon in ipairs(census_.mons) do
            local at, slot = flat("sNewBox" .. mon.box, "CartRAM"), mon.slot - 1
            local bank = 1 + ((snap.bytes.CartRAM[at + 20 + (slot >> 3)] >> (slot & 7)) & 1)
            if snap.bytes.CartRAM[at + slot] ~= mon.entry or bank ~= mon.bank then
                refuse("box proof census/raw coordinates disagree")
            end
            local raw_entry, bytes = entry_address(mon.bank, mon.entry), from_hex(mon.raw_hex)
            for i = 1, 49 do
                if snap.bytes.CartRAM[raw_entry + i - 1] ~= bytes[i] then refuse("box proof census/raw entry differs") end
            end
        end
        snap.complete = true
        return snap, party, raw, census_
    end
    local function identity()
        local player = reads.read_player()
        if not player then refuse("box proof player identity unreadable") end
        local options, why = live_options()
        if not options then refuse(why) end
        return {player = player.raw_hex, rom = profile.rom_sha1, artifact = profile.artifact, options = options,
                option_bytes = {io_.read_u8(OPT1, "System Bus"), io_.read_u8(OPT2, "System Bus")}}
    end
    local function same(a, b)
        if type(a) ~= type(b) then return false end
        if type(a) ~= "table" then return a == b end
        for k, v in pairs(a) do if not same(v, b[k]) then return false end end
        for k in pairs(b) do if a[k] == nil then return false end end
        return true
    end
    local function stats_of(mon)
        return {hp = mon.hp, maxHP = mon.max_hp, level = mon.level, status = mon.status,
                attack = mon.attack, defense = mon.defense, speed = mon.speed,
                spAttack = mon.special_attack, spDefense = mon.special_defense}
    end
    local function context_valid(p, context)
        return type(context) == "table" and type(context.valid) == "function" and context.valid() == true
               and p.write_id == context.write_id and p.session == context.session
               and p.player == context.player and p.wire_key == (context.wire_key or p.key)
               and p.generation == context.generation and writes:box_context(p.read_token or p.token)
               and same(p.identity, identity())
    end
    local function put(image, domain, at, values)
        for i, v in ipairs(values) do image.bytes[domain][at + i - 1] = v end
    end
    local function ignore(p, domain, at, n)
        p.pre_ignored[domain] = p.pre_ignored[domain] or {}
        for a = at, at + n - 1 do p.pre_ignored[domain][a] = true end
    end
    function self.plan_box_write(cmd, key, context)
        local ok, p = pcall(function()
            assert(cmd == "box_mon" or cmd == "party_mon" or cmd == "memorialize", "unknown box command")
            assert(type(context) == "table" and type(context.write_id) == "string" and context.write_id ~= ""
                   and type(context.session) == "string" and context.session ~= "" and context.generation ~= nil
                   and type(context.valid) == "function", "explicit box operation context required")
            local plan = {cmd = cmd, key = key, wire_key = context.wire_key or key, player = context.player,
                          write_id = context.write_id, session = context.session, generation = context.generation,
                          token = writes:box_context(), identity = identity(), permit_log_start = #writes.log,
                          pre_ignored = {}, emission_started = false, source_valid = false}
            assert(context_valid(plan, context), "box operation context unavailable")
            local before, party, raw, census_ = snapshot()
            plan.before = before
            local pmon, pwhy = find_key(party.mons, key)
            local found, hits = nil, 0
            for _, mon in ipairs(census_.mons) do
                if mon.key == key then found, hits = mon, hits + 1 end
            end
            if pwhy or hits > 1 or (not pmon and not found) then
                plan.reason = "missing or ambiguous expected source"
                return plan
            end
            local function guard(why)
                plan.guard_refusal, plan.reason = true, why
                return plan
            end
            if cmd == "memorialize" then
                -- memorialize runs on the LEGACY client contract only (self.memorialize, bare true / nil, why); its
                -- three-outcome postimage is not planned here, so this explicit-only seam proves a no-write negative.
                plan.source_valid = not (pmon and found)
                return guard("memorialize has no three-outcome plan (legacy client contract only)")
            end
            if cmd == "box_mon" then
                if not pmon or found then plan.reason = "deposit source inconsistent"; return plan end
                plan.source_valid = true
                if pmon.is_egg then return guard("egg in the party") end
                if party.count <= 1 then return guard("last party mon") end
                for _, mon in ipairs(party.mons) do
                    if mon.slot >= pmon.slot and mail[mon.held_item] then return guard("party mail in the party") end
                end
                local box, slot
                for b = 1, MEMORIAL - 1 do
                    for s_ = 1, PER_BOX do
                        if before.bytes.CartRAM[flat("sNewBox" .. b, "CartRAM") + s_ - 1] == 0 then
                            box, slot = b, s_
                            break
                        end
                    end
                    if box then break end
                end
                if not box then return guard("no free box slot") end
                local referenced = {{}, {}}
                for _, copy in ipairs({"sNewBox", "sBackupNewBox"}) do
                    for b = 1, MEMORIAL do
                        local at = flat(copy .. b, "CartRAM")
                        for s_ = 0, PER_BOX - 1 do
                            local entry = before.bytes.CartRAM[at + s_]
                            local bank = 1 + ((before.bytes.CartRAM[at + 20 + (s_ >> 3)] >> (s_ & 7)) & 1)
                            if entry ~= 0 then referenced[bank][entry] = true end
                        end
                    end
                end
                local bank, entry, flag_at
                for d = 1, 2 do
                    for e = 1, 207 do
                        local at = flat("wPokeDB" .. d .. "UsedEntries", "WRAM") + ((e - 1) >> 3)
                        if not referenced[d][e] and (before.bytes.WRAM[at] & (1 << ((e - 1) & 7))) == 0 then
                            bank, entry, flag_at = d, e, at
                            break
                        end
                    end
                    if bank then break end
                end
                if not bank then return guard("no free pokedb entry: native save required") end
                local at = flat("sNewBox" .. box, "CartRAM")
                local bits_at, mask = at + 20 + ((slot - 1) >> 3), 1 << ((slot - 1) & 7)
                local sealed = Boxes.entry_from_party(blob_of(raw, pmon.slot))
                plan.source = {slot = pmon.slot, record_at = block + mons_at + pmon.slot * s.End,
                               ot_at = block + ots_at + pmon.slot * c.NAME_LENGTH,
                               nickname_at = block + nicks_at + pmon.slot * c.MON_NAME_LENGTH}
                plan.destination = {box = box, slot = slot, bank = bank, entry = entry, entries_at = at + slot - 1,
                                    bits_at = bits_at, flag_at = flag_at, entry_at = entry_address(bank, entry)}
                plan.sealed_entry = sealed
                plan.postimage = clone(before)
                plan.postimage.party_count = party.count - 1
                put(plan.postimage, "System Bus", block, removed(raw, pmon.slot))
                put(plan.postimage, "CartRAM", entry_address(bank, entry), sealed)
                put(plan.postimage, "WRAM", flag_at, {before.bytes.WRAM[flag_at] | (1 << ((entry - 1) & 7))})
                put(plan.postimage, "CartRAM", bits_at,
                    {bank == 2 and (before.bytes.CartRAM[bits_at] | mask) or (before.bytes.CartRAM[bits_at] & ~mask & 255)})
                put(plan.postimage, "CartRAM", at + slot - 1, {entry})
                ignore(plan, "CartRAM", entry_address(bank, entry), 49)
                plan.stats = stats_of(pmon)
                return plan
            end
            if not found then
                if pmon and not pmon.is_egg and (pmon.hp or 0) == 0 then
                    plan.source_valid = true
                    return guard("dead in the party (hp 0)")
                end
                plan.reason = "new withdrawal has no boxed source; retained operation evidence required"
                return plan
            end
            if pmon then plan.reason = "both places without retained operation evidence"; return plan end
            plan.source_valid = true
            if found.is_egg then return guard("egg in the box") end
            if mail[found.held_item] then return guard("mail in the box") end
            if party.count >= c.PARTY_LENGTH then return guard("party full") end
            local composed, composition_why = pcall(require_withdraw)
            if not composed then return guard(tostring(composition_why)) end
            local entry = hex_bytes(found.raw_hex, 49, "box entry")
            local opts = plan.identity.options
            local built, record, view = pcall(STATS.party_from_savemon, entry, {
                apply_evs = opts.apply_evs, natures_on = opts.natures_on, perfect_ivs = opts.perfect_ivs,
                base_stats = BASE, variant_record = VARIANT, move_pp = MOVE_PP})
            if not built then return guard("stat rebuild refused: " .. tostring(record)) end
            if not record then return guard("stat rebuild refused: " .. tostring(view)) end
            local ot = hex_bytes(found.ot_raw_hex, 8, "OT name")
            for i = 1, 3 do ot[8 + i] = entry[29 + i] end
            local nick = hex_bytes(found.nickname_raw_hex, 11, "nickname")
            local at, target = record_flat(found.box), party.count
            local entries_at, bits_at = at + found.slot - 1, at + 20 + ((found.slot - 1) >> 3)
            plan.source = {box = found.box, slot = found.slot, bank = found.bank, entry = found.entry,
                           entries_at = entries_at, bits_at = bits_at, entry_at = entry_address(found.bank, found.entry)}
            plan.sealed_entry = entry
            plan.postimage = clone(before)
            plan.postimage.party_count = party.count + 1
            for _, field in ipairs({{mons_at, s.End, record}, {ots_at, c.NAME_LENGTH, ot},
                                   {nicks_at, c.MON_NAME_LENGTH, nick}}) do
                local dest = block + field[1] + target * field[2]
                put(plan.postimage, "System Bus", dest, field[3])
                ignore(plan, "System Bus", dest, field[2])
            end
            put(plan.postimage, "System Bus", block, {party.count + 1})
            put(plan.postimage, "CartRAM", entries_at, {0})
            put(plan.postimage, "CartRAM", bits_at, {before.bytes.CartRAM[bits_at] & ~(1 << ((found.slot - 1) & 7)) & 255})
            plan.stats = stats_of(view)
            return plan
        end)
        if ok then return p end
        return nil, tostring(p)
    end

    -- Read-only reclassification, optionally finishing ONLY retained withdrawal pointer/Banks
    -- cleanup. No new append, allocation, deposit repair or memorial repair is authorized.
    function self.classify_box_write(p, context, cleanup)
        -- A later proof may use a fresh hold, never a new session/save/checkpoint
        -- generation. The caller's valid() must cover reset and ROM continuity.
        p.read_token = writes:box_context()
        local valid = pcall(function() assert(context_valid(p, context), "box context changed") end)
        p.context_valid = valid
        if not valid then p.reason = "box operation identity/options/context changed" end
        local ok, after = pcall(snapshot)
        p.after = ok and after or nil
        valid = valid and pcall(function() assert(context_valid(p, context), "box context changed during readback") end)
        p.context_valid = valid
        if cleanup and valid and p.cmd == "party_mon" and p.source and p.postimage and p.after then
            local ignored = {CartRAM = {[p.source.entries_at] = true, [p.source.bits_at] = true}}
            if image_matches(p.postimage, p.after, ignored) then
                local src, pre, got = p.source, p.before.bytes.CartRAM, p.after.bytes.CartRAM
                if (got[src.entries_at] == pre[src.entries_at] or got[src.entries_at] == 0)
                   and (got[src.bits_at] == pre[src.bits_at] or got[src.bits_at] == p.postimage.bytes.CartRAM[src.bits_at]) then
                    for _, at in ipairs({src.entries_at, src.bits_at}) do
                        local want = p.postimage.bytes.CartRAM[at]
                        if got[at] ~= want then
                            local wrote = pcall(function()
                                assert(context_valid(p, context), "box cleanup context changed")
                                writes:arm("box_deposit", function(d, a, n) return d == "CartRAM" and a == at and n == 1 end)
                                writes:write_batch({{domain = "CartRAM", addr = at, bytes = {want}}})
                            end)
                            writes:disarm()
                            -- Entries must be freshly proved clear BEFORE touching Banks.
                            local read_ok, byte = pcall(io_.read_u8, at, "CartRAM")
                            if not wrote or not read_ok or byte ~= want then break end
                        end
                    end
                    local read_ok, fresh = pcall(snapshot)
                    p.after = read_ok and fresh or nil
                    p.context_valid = pcall(function() assert(context_valid(p, context)) end)
                end
            end
        end
        return O.classify_box_write(p)
    end

    -- Explicit-only adapter seam; entry/client do not negotiate or call it in this card.
    -- Returns structured result AND retained operation evidence for same-ID reclassification.
    function self.attempt_box_write(cmd, key, context)
        local p, why = self.plan_box_write(cmd, key, context)
        if not p then return {outcome = "UNCERTAIN", reason = why} end
        if p.source_valid and p.postimage and not p.guard_refusal then
            p.attempted = true
            writes:observe_box_write(function()
                p.emission_started = true
            end)
            local ran, failure = pcall(function()
                local fn = cmd == "box_mon" and self.deposit or self.withdraw
                local done, note = fn(key)
                if not done then p.reason = tostring(note) end
            end)
            writes:observe_box_write(nil)
            writes:disarm()
            if not ran then p.reason = tostring(failure) end
        end
        p.permit_log_end = #writes.log
        return self.classify_box_write(p, context), p
    end
    return self
end

-- ── the BATTLE hold (sibling of O.checkpoint; lua/gen2/polished_explode.lua composes it) ─────────────────────
--
-- The overworld predicate above refuses a battle by construction (wBattleMode == 0, hROMBank == the overworld
-- bank), so the battle writers get their own hold with the SAME shape and refusal conventions and a battle fact
-- set. O.checkpoint's body is untouched: this is a sibling, not a mode.
--   wBattleMode in {WILD, TRAINER}   a battle is running (not 0, nothing outside the source enumeration)
--   wLinkMode == 0                   never write into a link battle (the other Game Boy would desync)
--   wGameLogicPaused == 0            no native save is running
--   sWritingBackup != 1              no backup save in progress
-- and, per kind, the instruction bytes of the named sites re-read from the EXECUTED ROM at check time (so a
-- patched or relocated ROM never takes a write), plus, for a PC-hold kind, hROMBank == the site's bank.
--
-- THE SITES (bank 0F, ROM = the overlay; each byte sequence occurs ONCE in bank 0F and once in its routine -
-- verified by tests/unit/test_polished_explode_path.py over the executed overlay ROM, and every address
-- re-derived there from data/polished/polished_slink.sym):
--   explode       0f:416A  CD 35 42  `call DetermineMoveOrder` inside BattleTurn (engine/battle/core.asm:190): the
--                                    PC hold the client hooks (write_checkpoint.json battle_hold.execution_before)
--   rival_gate    0f:47DD  21 8B D2  `ld hl, wOTPartyMons` inside SendInUserPkmn's enemy branch: the PC where
--                                    wOTPartyMons[wCurPartyMon] has not been copied yet (battle_hold.rival_swap_gate)
--   trainer_ready 0f:7271  D7 00 40 07  inside InitEnemy's trainer branch. A UNIQUE-BYTE ANCHOR ONLY: the
--                                    2-byte D7 00 occurs 4x in bank 0F, so the 4-byte sequence plus the address is
--                                    the pin. No mnemonic is claimed for it.
-- rival_gate and trainer_ready are anchors for the frame-end rival poll (lua/gen2/client.lua rival_tick is a
-- frame_end POLL, not a PC hold): a frame-kind check re-reads their bytes but does not require the CPU to be
-- there. Only `explode` is a PC hold.
O.BATTLE_MODES = {[1] = true, [2] = true}            -- WILD_BATTLE, TRAINER_BATTLE (profile.constants)
O.BATTLE_QUALIFICATION = "DEV_OVERLAY_PREDICATE_HOLD"
O.BATTLE_SITES = {
    explode = {bank = 0x0F, pc = 0x416A, bytes = {0xCD, 0x35, 0x42}, routine = "BattleTurn",
               routine_start = 0x4109, routine_end = 0x41A7, instruction = "call DetermineMoveOrder"},
    rival_gate = {bank = 0x0F, pc = 0x47DD, bytes = {0x21, 0x8B, 0xD2}, routine = "SendInUserPkmn",
                  routine_start = 0x4748, routine_end = 0x493C, instruction = "ld hl, wOTPartyMons"},
    trainer_ready = {bank = 0x0F, pc = 0x7271, bytes = {0xD7, 0x00, 0x40, 0x07}, routine = "InitEnemy",
                     routine_start = 0x7260, routine_end = 0x72E0},
}
-- kind -> the sites whose bytes are re-read, and `at` = the site the CPU must be executing (hROMBank). The first
-- two are what lua/gen2/client.lua asks for; the last two are the writer operations lua/gen2/polished_explode.lua
-- maps battle_explode and enemy_party to.
O.BATTLE_KINDS = {
    battle_faint = {at = "explode", sites = {"explode"}},
    battle_bench = {sites = {"rival_gate", "trainer_ready"}},
    explode = {at = "explode", sites = {"explode"}},
    rival = {sites = {"rival_gate", "trainer_ready"}},
}

--- sites: O.BATTLE_SITES-shaped ({bank, pc, bytes, routine, routine_start, routine_end}). Same check(kind)
--- -> ok, why contract as O.checkpoint, qualification DEV_OVERLAY_PREDICATE_HOLD.
function O.battle_checkpoint(profile, io, log, sites)
    assert(type(profile) == "table" and profile.title == "polished" and type(profile.ram) == "table",
           "generated Polished profile required")
    assert(type(io) == "table" and type(io.read_u8) == "function" and type(io.bank_valid) == "function",
           "explicit read_u8/bank_valid required")
    assert(type(sites) == "table", "battle hold sites required")
    for _, kind in pairs(O.BATTLE_KINDS) do
        for _, id in ipairs(kind.sites) do
            local site = sites[id]
            assert(type(site) == "table" and integer(site.bank, 1, 0x7F) and integer(site.pc, 0x4000, 0x7FFF)
                   and type(site.bytes) == "table", "battle hold site missing or malformed: " .. id)
            assert(site.routine_start <= site.pc and site.pc + #site.bytes <= site.routine_end,
                   "the battle hold site must lie inside its named routine: " .. id)
            for _, value in ipairs(site.bytes) do assert(integer(value, 0, 255), "hold site byte out of range") end
        end
    end
    local ram, banks, hram = profile.ram, profile.ram_bank, profile.hram
    -- a copy of O.checkpoint's reader (kept separate so the overworld body stays untouched)
    local function byte(name)
        local at, bank = ram[name], banks[name]
        local hram_at = (hram ~= nil) and hram[name] or nil
        if at == nil and hram_at ~= nil then at, bank = hram_at, 0 end
        local wram = integer(at, 0xC000, 0xDFFF)
        local hram_window = integer(at, 0xFF80, 0xFFFF) and bank == 0
        if not (wram or hram_window) or not integer(bank, 0, 7) then return nil, "missing " .. name end
        if io.bank_valid(bank, at, 1) ~= true then return nil, name .. " bank not mapped" end
        return io.read_u8(at, "System Bus")
    end
    local last
    local function refuse(why)
        if log and last ~= why then
            last = why
            log("[SLink-gen2] battle write refused: " .. tostring(why))
        end
        return false, why
    end
    local self = {qualification = O.BATTLE_QUALIFICATION, facts = {}}
    function self:covers(kind) return O.BATTLE_KINDS[kind] ~= nil end
    function self:check(kind)
        local want = O.BATTLE_KINDS[kind]
        if want == nil then return refuse("no composed Polished battle write kind " .. tostring(kind)) end
        local observed = self.facts
        for _, name in ipairs({"wBattleMode", "wLinkMode", "wGameLogicPaused"}) do
            local value, why = byte(name)
            if value == nil then return refuse(why) end
            observed[name] = value
        end
        if not O.BATTLE_MODES[observed.wBattleMode] then
            return refuse("not in a battle (wBattleMode $" .. string.format("%02X", observed.wBattleMode) .. ")")
        end
        if observed.wLinkMode ~= 0 then return refuse("link cable active (wLinkMode)") end
        if observed.wGameLogicPaused ~= 0 then return refuse("native save running (wGameLogicPaused)") end
        local saving = saving_byte(io)
        if saving == nil then return refuse("sWritingBackup unreadable") end
        if saving == 1 then return refuse("backup save in progress (sWritingBackup)") end
        if want.at ~= nil then
            local hold_bank, bank_why = byte("hROMBank")
            if hold_bank == nil then return refuse(bank_why) end
            if hold_bank ~= sites[want.at].bank then
                return refuse("not at the " .. sites[want.at].routine .. " hold (hROMBank $"
                              .. string.format("%02X", hold_bank) .. ")")
            end
        end
        for _, id in ipairs(want.sites) do
            local site = sites[id]
            -- the ROM domain is addressed by its LINEAR offset, not the banked bus address
            local rom_at = site.bank * 0x4000 + (site.pc - 0x4000)
            for i, expected in ipairs(site.bytes) do
                local seen = io.read_u8(rom_at + i - 1, "ROM")
                if seen ~= expected then
                    return refuse("hold site bytes differ at " .. string.format("%02X:%04X", site.bank, site.pc)
                                  .. " (patched ROM)")
                end
            end
        end
        last = nil
        return true, kind
    end
    return self
end

--- The box executor as lua/gen2/client.lua run_box expects it. The client reads a party_mon success as
--- `true, <settle note STRING>` and refuses with `party full` EXACTLY to take its deferred-retry path, but the
--- executor reports `true, <metadata table>` and `party full (n/m)`: the table was concatenated into a log line
--- BEFORE sync_retrieve_done (an error, so no ack and a stray unarmed settle) and a full party failed at once.
--- Polished keeps no durable backing copy to settle (a reset reverts the party and the box halves together), so a
--- success is a bare `true`. Everything else passes through unchanged (the executor's own return shape is pinned
--- by its tests).
function O.client_boxes(raw, log)
    assert(type(raw) == "table" and type(raw.withdraw) == "function", "client_boxes needs the box executor")
    local wrapped = setmetatable({}, {__index = raw})
    function wrapped.withdraw(key, opts)
        local done, why = raw.withdraw(key, opts)
        if done then
            if log and type(why) == "table" then
                log("[SLink-gen2] party_mon " .. tostring(key) .. " withdrawn: " .. tostring(why.message or why.durability))
            end
            return true
        end
        why = tostring(why)
        if why:sub(1, 10) == "party full" then
            if log then log("[SLink-gen2] party_mon " .. tostring(key) .. ": " .. why) end
            why = "party full"
        end
        return nil, why
    end
    return wrapped
end

return O
