-- lua/gen2/polished_rival.lua -- the Polished Crystal v3.2.3 Rival Team Swap (W-4) SOURCE-level writer, composed by
-- compose_polished (lua/gen2/entry.lua) and REFUSING by default. Spec: docs/polished/EXPLODE_RIVAL.md §3, §9.2, §10.
--
-- WHAT THE CLIENT HANDS IT. The server's replace_rival_team carries the PARTNER'S LIVE PARTY as 70-byte blobs
-- (48 party_struct + 11 OT + 11 nickname, client.lua replace_rival_team) -- complete records, HP and stats included -- so
-- nothing is rebuilt here (lua/gen2/polished_stats.lua is the BOX-withdraw twin and is not needed). Polished has no enemy
-- species list: the 9-bit species is each record's own Species byte (+0) plus the EXTSPECIES bit of its Form byte.
--
-- THE WRITE (all in WRAMX bank 1, nothing else is ever writable):
--   1. wOTPartyMons          count x 48 B   (records)
--   2. wOTPartyMonOTs        count x 11 B
--   3. wOTPartyMonNicknames  count x 11 B   -> every byte READ BACK; a mismatch restores the saved originals
--   4. wOTPartyCount         1 B, LAST      -> read back; a mismatch restores everything
-- wOTPartyCount + 1 (wMirrorHerbPendingBoosts, 01:D284) is never written: the declared bounds exclude it and its
-- absence is asserted at construction. The permit is armed with an `allow` predicate NARROWED to exactly the ranges the
-- count needs (R.ranges), on top of the whole-block bounds.
--
-- THE GATE. Every refusal below happens BEFORE the first byte is written (a named reason, nothing written):
--   * no rival trainer classes configured. OWNER RULING 2026-10-08: when deps.rival_classes is nil the set is
--     R.DEFAULT_RIVAL_CLASSES = RIVAL0/RIVAL1/RIVAL2 ($1B-$1D); LYRA1/LYRA2 ($1E/$1F) are EXCLUDED. An explicit
--     deps.rival_classes / :set_classes() list (even an empty one) overrides the default.
--   * the battle hold (polished_overworld.lua O.battle_checkpoint kind "rival": a battle, no link, no native save, no
--     backup save, the 0f:47DD site bytes re-read from the executed ROM), wBattleMode == TRAINER_BATTLE
--   * the CPU at 0f:47DD: hROMBank == 0x0F AND the PC register == 0x47DD (io.register("PC")). The native-window
--     interface below is registered only for a composition with configured classes. The callback predicate and
--     plan each check the live engine state; model composition is not a live consumed-write receipt.
--   * the class of the trainer being fought is in the rival set; hBattleTurn is enemy (1), and required ctx.trainer_id names it (stale battle)
--   * ctx.cur_ot_mon is what wCurOTMon AND wCurPartyMon read (committed at 0f:47cc), inside the NEW party, and that mon
--     has HP (the engine copies it next, §10); count 1..6; every record complete, a known species, not an egg, level
--     1..MAX_LEVEL
-- The write is only valid before 0f:480d (§10.3). The client parks a command for the native window; frame end
-- announces the visit and reports its result. Live swap consumption and callback timing remain separate evidence.
local R = {}

--- The default rival set (owner ruling 2026-10-08): wOtherTrainerClass of RIVAL0, RIVAL1, RIVAL2. The profile carries no
--- trainer-class constants (profile.json titles.polished.constants has none), so these are pinned here and held equal, by
--- NAME, to data/games/polished_crystal/trainers.json rival_classes (RIVAL0 27, RIVAL1 28, RIVAL2 29 = $1B/$1C/$1D;
--- constants/trainer_constants.asm:95-127) by tests/unit/test_polished_rival_path.py. LYRA1 $1E / LYRA2 $1F are excluded.
R.DEFAULT_RIVAL_CLASSES = {0x1B, 0x1C, 0x1D}

local function integer(value, low, high)
    return type(value) == "number" and value % 1 == 0 and value >= low and value <= high
end

--- The constants client.lua reads (c.PARTYMON_STRUCT_LENGTH / c.MON_HP / c.MON_SPECIES), derived from the generated
--- profile's own party_struct geometry; the profile's constants table does not carry them.
function R.constants(profile)
    local s = assert(profile.structs and profile.structs.party, "profile party_struct required")
    assert(s.End == 48 and s.HP == 34 and s.Species == 0, "party_struct geometry disagrees (RAM.md §2.1)")
    return {PARTYMON_STRUCT_LENGTH = s.End, MON_HP = s.HP, MON_SPECIES = s.Species}
end

--- deps: profile, io (read_u8/bank_valid/framecount/register), Permit, facade (the composed explode facade),
--- checkpoint (its O.battle_checkpoint), hold (its battle_hold: rival_swap_gate), coords (its sym: {bank, addr}),
--- species_known(effective_id), rival_classes (optional array of wOtherTrainerClass; nil = R.DEFAULT_RIVAL_CLASSES), log.
--- Returns {writes = the facade with rival_swap routed here, ranges, set_classes, constants}.
function R.new(deps)
    assert(type(deps) == "table", "Polished rival options required")
    local profile, io, Permit, facade = assert(deps.profile), assert(deps.io), assert(deps.Permit), assert(deps.facade)
    local checkpoint, coords, log = assert(deps.checkpoint), assert(deps.coords), deps.log
    local site = assert(assert(deps.hold).rival_swap_gate, "battle_hold.rival_swap_gate required")
    assert(type(deps.species_known) == "function", "species_known required: nothing is defaulted")
    assert(type(io.read_u8) == "function" and type(io.bank_valid) == "function"
           and type(io.framecount) == "function" and type(io.register) == "function", "explicit io required")
    assert(integer(site.bank, 1, 0x7F) and integer(site.pc, 0x4000, 0x7FFF), "gate PC coordinates required")
    local c, s = assert(profile.constants), assert(profile.structs).party
    local derived = R.constants(profile)
    for key, value in pairs(derived) do
        assert(c[key] == nil or c[key] == value, key .. " disagrees with the party_struct geometry")
        c[key] = value
    end
    assert(c.PARTY_LENGTH == 6 and c.NAME_LENGTH == 11 and c.MON_NAME_LENGTH == 11 and integer(c.TRAINER_BATTLE, 1, 255)
           and integer(c.EXTSPECIES_MASK, 1, 255) and integer(c.IS_EGG_MASK, 1, 255) and integer(c.MAX_LEVEL, 1, 255)
           and integer(s.Form, 0, 47) and integer(s.Level, 0, 47), "unsupported or missing Polished constants")
    local P, NM, NAME, NICK = c.PARTY_LENGTH, s.End, c.NAME_LENGTH, c.MON_NAME_LENGTH

    local at, bank_of = {}, {}
    for _, label in ipairs({"wOTPartyCount", "wMirrorHerbPendingBoosts", "wOTPartyMons", "wOTPartyMonOTs",
                            "wOTPartyMonNicknames", "wOTPartyDataEnd", "wCurOTMon", "wCurPartyMon"}) do
        local row = coords[label]
        assert(type(row) == "table" and integer(row[1], 0, 7) and integer(row[2], 0xC000, 0xDFFF), "coords missing: " .. label)
        at[label], bank_of[label] = row[2], row[1]
    end
    local herb, herb_end = at.wMirrorHerbPendingBoosts, at.wOTPartyMons
    assert(herb == at.wOTPartyCount + 1 and herb_end > herb, "Mirror Herb geometry disagrees (RAM.md §2.4)")
    assert(at.wOTPartyMonOTs == at.wOTPartyMons + P * NM and at.wOTPartyMonNicknames == at.wOTPartyMonOTs + P * NAME
           and at.wOTPartyDataEnd == at.wOTPartyMonNicknames + P * NICK, "enemy party block geometry disagrees")
    for label in pairs(at) do assert(bank_of[label] == (label == "wCurOTMon" and 0 or 1), "unexpected bank: " .. label) end

    -- count x each array, then the count byte last: the narrowed set a given party needs
    local function ranges(count)
        return {{at.wOTPartyMons, count * NM}, {at.wOTPartyMonOTs, count * NAME},
                {at.wOTPartyMonNicknames, count * NICK}, {at.wOTPartyCount, 1}}
    end
    -- the static bounds: the count byte and the whole party block, never the Herb gap between them
    local block = {{at.wOTPartyCount, 1}, {at.wOTPartyMons, at.wOTPartyDataEnd - at.wOTPartyMons}}
    local function inside(list, addr, n)
        for _, r in ipairs(list) do
            if addr >= r[1] and addr + n <= r[1] + r[2] then return true end
        end
        return false
    end
    local function touches_herb(addr, n) return addr < herb_end and addr + n > herb end

    local narrow, caller_allow = nil, nil
    local gate = Permit.new({
        write_u8 = function(addr, value, domain) return io.write_u8(addr, value, domain) end,
        domains = {["System Bus"] = {
            bounds = function(addr, n) return not touches_herb(addr, n) and inside(block, addr, n) end,
            mapped = function(addr, n) return io.bank_valid(1, addr, n) == true end,
            pointer_stable = function() return true end, -- the enemy party block is fixed WRAM
        }},
        lifetime = {capture = function() return io.framecount() end, valid = function(token) return token == io.framecount() end},
        provenance = function() return {site = "lua/gen2/polished_rival.lua", evidence = "DEV_OVERLAY_PREDICATE_HOLD"} end,
    })

    local self = {ranges = ranges, constants = derived}
    local classes = {}
    function self:set_classes(list)
        local set = {}
        for _, class in ipairs(list or {}) do
            assert(integer(class, 1, 255), "rival trainer class out of range")
            set[class] = true
        end
        classes = set
    end
    self:set_classes(deps.rival_classes == nil and R.DEFAULT_RIVAL_CLASSES or deps.rival_classes)

    local function wram(bank, addr)
        assert(io.bank_valid(bank, addr, 1) == true, "read refused: bank " .. bank .. " not mapped")
        return io.read_u8(addr, "System Bus")
    end
    local function ram(label) return wram(profile.ram_bank[label], profile.ram[label]) end

    -- every refusal; returns the plan {count, spans, saved} and writes nothing
    local function plan(mons, ctx)
        assert(gate.armed == "rival_swap", "write refused: rival_swap gate not armed")
        assert(next(classes) ~= nil, "refused: no rival trainer classes configured (owner decision, EXPLODE_RIVAL.md 3.4)")
        assert(type(ctx) == "table" and ctx.link_mode == 0, "linked or unknown battle context refused")
        local ok, why = checkpoint:check("rival")
        assert(ok == true, "write refused: " .. tostring(why))
        assert(ram("wBattleMode") == c.TRAINER_BATTLE, "not a trainer battle")
        local bank, pc = io.read_u8(profile.hram.hROMBank, "System Bus"), io.register("PC")
        assert(bank == site.bank and pc == site.pc, string.format(
            "not at the SendInUserPkmn rival gate %02X:%04X (hROMBank $%02X, PC %s)", site.bank, site.pc, bank,
            type(pc) == "number" and string.format("$%04X", pc) or tostring(pc)))
        assert(io.read_u8(profile.hram.hBattleTurn, "System Bus") == 1, "not an enemy send-in")
        local class, id = ram("wOtherTrainerClass"), ram("wOtherTrainerID")
        assert(classes[class] == true, string.format("trainer class $%02X is not a configured rival class", class))
        assert(ctx.trainer_id ~= nil, "bound trainer identity required")
        assert(ctx.trainer_id == class * 256 + id, "stale battle: the trainer being fought differs")
        local count = Permit.sequence_length(mons, "enemy party")
        assert(count >= 1 and count <= P, "enemy party count must be 1.." .. P)
        local cur = ctx.cur_ot_mon
        assert(integer(cur, 0, count - 1), "the enemy mon being sent out is outside the new party")
        assert(wram(bank_of.wCurOTMon, at.wCurOTMon) == cur and wram(1, at.wCurPartyMon) == cur, "stale snapshot")
        local records, names, nicks = {}, {}, {}
        for i, mon in ipairs(mons) do
            local what = "enemy mon " .. i
            assert(type(mon) == "table", what .. ": table required")
            for field, n in pairs({record = NM, ot = NAME, nick = NICK}) do
                assert(type(mon[field]) == "table" and Permit.sequence_length(mon[field], what .. " " .. field) == n,
                       what .. ": " .. field .. " must be " .. n .. " bytes")
                for j = 1, n do assert(integer(mon[field][j], 0, 255), what .. ": " .. field .. " byte out of range") end
            end
            local r = mon.record
            local form = r[s.Form + 1]
            local species = r[s.Species + 1] + ((form & c.EXTSPECIES_MASK) ~= 0 and 0x100 or 0)
            -- species_known takes a RAW species id (1..$123 minus the holes), never a variant BaseData record index
            assert(species >= 1 and deps.species_known(species) == true, what .. ": unknown species " .. species)
            assert((form & c.IS_EGG_MASK) == 0, what .. ": an egg cannot battle")
            assert(r[s.Level + 1] >= 1 and r[s.Level + 1] <= c.MAX_LEVEL, what .. ": level out of range")
            assert(i ~= cur + 1 or r[s.HP + 1] * 256 + r[s.HP + 2] > 0, what .. " is sent out next and has no HP")
            for j = 1, NM do records[#records + 1] = r[j] end
            for j = 1, NAME do names[#names + 1] = mon.ot[j] end
            for j = 1, NICK do nicks[#nicks + 1] = mon.nick[j] end
        end
        local list = ranges(count)
        local saved = {}
        for i, r in ipairs(list) do
            saved[i] = {}
            for j = 0, r[2] - 1 do saved[i][j + 1] = wram(1, r[1] + j) end
        end
        return {count = count, narrow = list, saved = saved,
                arrays = {{domain = "System Bus", addr = list[1][1], bytes = records},
                          {domain = "System Bus", addr = list[2][1], bytes = names},
                          {domain = "System Bus", addr = list[3][1], bytes = nicks}},
                last = {{domain = "System Bus", addr = list[4][1], bytes = {count}}}}
    end

    local function verify(spans)
        for _, span in ipairs(spans) do
            for i, want in ipairs(span.bytes) do
                local got = wram(1, span.addr + i - 1)
                assert(got == want, string.format("read-back mismatch at $%04X: wrote $%02X, read $%02X", span.addr + i - 1, want, got))
            end
        end
    end
    local function allow(domain, addr, n)
        return narrow ~= nil and inside(narrow, addr, n) and (caller_allow == nil or caller_allow(domain, addr, n))
    end
    -- a failed write disarms the permit (Permit:guard), so the restore arms its own, over the same narrowed ranges.
    -- Returns true only when every saved byte READS BACK: a restore write can fail like any other, and a torn enemy
    -- party must be reported as torn, never as the original read-back error alone.
    local function restore(p)
        local wrote, werr = pcall(function()
            local spans = {}
            for i, r in ipairs(p.narrow) do
                for j = 1, r[2] do
                    -- only a range that actually changed is rewritten: a refusal before the first byte rewrites nothing
                    if wram(1, r[1] + j - 1) ~= p.saved[i][j] then
                        spans[#spans + 1] = {domain = "System Bus", addr = r[1], bytes = p.saved[i]}
                        break
                    end
                end
            end
            if #spans == 0 then return end
            narrow = p.narrow
            gate:arm("rival_swap", allow)
            gate:write_batch(spans)
        end)
        local torn
        local read, rerr = pcall(function()
            for i, r in ipairs(p.narrow) do
                for j = 1, r[2] do
                    if wram(1, r[1] + j - 1) ~= p.saved[i][j] then torn = r[1] + j - 1 return end
                end
            end
        end)
        if wrote and read and torn == nil then return true end
        return false, torn and string.format("$%04X still differs from the saved original", torn)
                          or tostring(werr or rerr)
    end
    local function fail(p, why)
        local restored, rwhy = restore(p)
        if restored then error(why, 0) end
        error("TORN ENEMY PARTY: " .. tostring(why) .. "; rollback FAILED: " .. tostring(rwhy), 0)
    end

    local proxy = setmetatable({}, {__index = function(_, key)
        if key == "armed" then return gate.armed or facade.armed end
        local value = facade[key]
        if type(value) == "function" then return function(_, ...) return value(facade, ...) end end
        return value
    end})
    -- Polished-only synchronous window interface. The client owns visit/command lifetime;
    -- this module owns ROM-specific predicate bytes. plan() independently rechecks every guard.
    function proxy:rival_gate_enabled() return next(classes) ~= nil end
    function proxy:rival_gate_context(bound_trainer)
        assert(io.read_u8(profile.hram.hROMBank, "System Bus") == site.bank and io.register("PC") == site.pc,
               "rival callback: wrong bank/PC")
        assert(ram("wBattleMode") == c.TRAINER_BATTLE, "rival callback: not trainer")
        assert(io.read_u8(profile.hram.hBattleTurn, "System Bus") == 1, "rival callback: not enemy")
        local class, id = ram("wOtherTrainerClass"), ram("wOtherTrainerID")
        assert(classes[class] == true, "rival callback: class not configured")
        assert(bound_trainer ~= nil and bound_trainer == class * 256 + id, "rival callback: stale trainer")
        local link, cur = ram("wLinkMode"), wram(bank_of.wCurOTMon, at.wCurOTMon)
        assert(link == 0, "rival callback: link active")
        assert(integer(cur, 0, P - 1) and wram(bank_of.wCurPartyMon, at.wCurPartyMon) == cur,
               "rival callback: inconsistent selected index")
        return {mode=c.TRAINER_BATTLE, link_mode=link, cur_ot_mon=cur}
    end
    function proxy:arm(reason, extra)
        if reason ~= "rival_swap" then return facade:arm(reason, extra) end
        assert(extra == nil or type(extra) == "function", "allow must be a predicate")
        narrow, caller_allow = nil, extra
        return gate:arm(reason, allow)
    end
    function proxy:disarm()
        narrow, caller_allow = nil, nil
        gate:disarm()
        return facade:disarm()
    end
    -- mons = list of {record = 48, ot = 11, nick = 11}; ctx = {link_mode, cur_ot_mon, trainer_id (required)}
    function proxy:write_enemy_party(mons, ctx)
        return gate:guard(function()
            local p = plan(mons, ctx)
            narrow = p.narrow
            local ok, why = pcall(function() gate:write_batch(p.arrays); verify(p.arrays) end)
            if not ok then fail(p, why) end
            ok, why = pcall(function() gate:write_batch(p.last); verify(p.last) end)
            if not ok then fail(p, why) end
            -- The client reports/logs this verified result at frame end, outside the exec callback.
        end)
    end
    self.writes = proxy
    return self
end

return R
