-- lua/gen2/polished_trade.lua -- the Polished Crystal half of SLink's in-game trade (card POL-TRADE).
--
-- WHY THIS FILE IS SEPARATE FROM compose_polished
--   lua/gen2/entry.lua `compose_polished` owns the writes/safety composition another card is
--   building. This module never edits it. The coordinator hooks it in at ONE seam:
--
--       -- in lua/gen2/entry.lua, inside compose_polished, next to the `client` construction:
--       local PT = load("lua/gen2/polished_trade.lua")
--       local trade, why = PT.compose({io = io_, profile = profile,
--                                     validation = pinned_validation_facts, dev = explicit_dev_flag})
--       -- C4 must adapt the dispositions below; this is NOT a vanilla binder drop-in.
--
--   compose() is fail-closed: if the profile does not yet carry the Polished overlay trade block, it
--   returns nil, why -- the client keeps its hello and ticks and logs the reason once. Nothing here
--   can half-arm.
--
-- WHAT IS ACTUALLY IMPLEMENTED
--   The standalone 53-byte `trademon` codec is retained; the binder below stages
--   the 70-byte party blob, never this animation struct. Vanilla Crystal's trademon is 50 bytes;
--   Polished's is 53 (wPlayerTrademon 00:c51c .. wPlayerTrademonEnd 00:c551, and wOTTrademon
--   00:c551 .. wOTTrademonEnd 00:c586 -- three more, and the extra bytes are the third DV byte plus
--   the two attribute bytes that carry shiny/ability/nature and ext-species/form/gender/is-egg).
--   See docs/polished/TRADE.md §8.2 and server/adapters/polished_codec.py, which is the byte-for-byte
--   twin of this module and whose tests/unit/test_polished_trade_codec.py pins both against the sym.
--
--   C3/H1 provide DEV-ONLY proposer and responder phases over the shared GB lease.
--   Commit, the client pump and production admission remain separate cards.
local module_dir = debug.getinfo(1, "S").source:match("^@(.+[/\\])[^/\\]+$")
local Lease = dofile(assert(module_dir, "polished_trade.lua must be loaded by path") .. "../gb_trade_lease.lua")
local Permit = dofile(module_dir .. "../write_permit.lua")
local PT = {}

-- ── the 53-byte struct ─────────────────────────────────────────────────────────
-- Offsets are macros/ram.asm:252-276 `MACRO trademon`, cross-checked against the built sym; every
-- one is asserted against that sym by the Python test named above.
PT.TRADEMON_SIZE = 53
PT.NAME_SIZE = 11          -- NAME_LENGTH / MON_NAME_LENGTH: the field stride, not the glyph count
PT.OFF = {
    Species = 0, SpeciesName = 1, Nickname = 12, SenderName = 23, OTName = 34,
    HPAtkDV = 45, DefSpeDV = 46, SatSdfDV = 47, Personality = 48, Form = 49,
    ID = 50, CaughtData = 52,
}
-- pokemon_data_constants.asm:235-245, as the codec already reads them.
PT.GENDER_MASK, PT.IS_EGG_MASK, PT.EXTSPECIES_MASK, PT.FORM_MASK = 0x80, 0x40, 0x20, 0x1F
PT.SHINY_MASK, PT.ABILITY_MASK, PT.NATURE_MASK = 0x80, 0x60, 0x1F

PT.BLOB_SIZE = 70          -- 48 party_struct + 11 OT + 11 nickname, the wire party blob
local OFF, NAME_SIZE, BLOB_SIZE = PT.OFF, PT.NAME_SIZE, PT.BLOB_SIZE
local function valid_bytes(bytes, n, label)
    if type(bytes) ~= "table" or #bytes ~= n then
        return nil, string.format("%s: expected %d bytes, got %s", label, n, tostring(bytes and #bytes))
    end
    for i = 1, n do
        local v = bytes[i]
        if type(v) ~= "number" or v < 0 or v > 255 or v ~= math.floor(v) then
            return nil, string.format("%s: byte %d is %s, not 0..255", label, i, tostring(v))
        end
    end
    return true
end

--- The 9-bit species: the low byte plus bit 5 of the form byte
--- (ConvertFormToExtendedSpecies, home/pokemon.asm:408). A species above 0xFF must never be
--- truncated -- that is precisely the check vanilla's one-byte species compare could not make.

--- Bitwise AND that does not assume the mask is 2^k-1. Every %/floor shortcut here was wrong for
--- at least one of the four masks: GENDER_MASK $80 is not 2^k-1, and FORM_MASK $1F is not a divisor
--- of $FF, so `form_byte % 31` reads 7 for a form of 3.
local function band(v, m)
    local out, bit = 0, 1
    while bit <= 128 do
        if (v % (bit * 2)) >= bit and (m % (bit * 2)) >= bit then out = out + bit end
        bit = bit * 2
    end
    return out
end
PT.band = band

local function species_of(low, form_byte)
    return low + band(form_byte, PT.EXTSPECIES_MASK) * 8
end
PT.species_of = species_of

--- 70-byte wire party blob -> 53-byte staged trademon.
--- species_name_raw / sender_name_raw are the two names the blob does not carry: the species name is
--- engine-owned, the sender name is the partner's trainer name. Both default to the engine's own
--- blank (all terminators), never to a guess.
function PT.encode(blob, species_name_raw, sender_name_raw)
    local ok, why = valid_bytes(blob, BLOB_SIZE, "party blob")
    if not ok then return nil, why end
    local out = {}
    for i = 1, PT.TRADEMON_SIZE do out[i] = 0 end

    out[OFF.Species + 1] = blob[1]                                   -- party_struct +0
    local form_byte = blob[22]                                      -- party_struct +21
    local personality = blob[21]                                    -- party_struct +20
    out[OFF.Personality + 1] = personality
    out[OFF.Form + 1] = form_byte
    out[OFF.HPAtkDV + 1], out[OFF.DefSpeDV + 1], out[OFF.SatSdfDV + 1] = blob[18], blob[19], blob[20]
    out[OFF.ID + 1], out[OFF.ID + 2] = blob[7], blob[8]              -- big-endian OT id
    out[OFF.CaughtData + 1] = blob[29]                              -- party_struct +28

    local function put(offset, raw)
        if raw == nil then return end
        local good, problem = valid_bytes(raw, NAME_SIZE, "name field")
        if not good then error(problem, 0) end
        for i = 1, NAME_SIZE do out[offset + i] = raw[i] end
    end
    put(OFF.SpeciesName, species_name_raw)
    put(OFF.SenderName, sender_name_raw)
    -- OT and nickname come out of the blob itself, at their own strides.
    for i = 1, NAME_SIZE do
        out[OFF.OTName + i] = blob[BLOB_SIZE - 2 * NAME_SIZE + i] or 0
        out[OFF.Nickname + i] = blob[BLOB_SIZE - NAME_SIZE + i] or 0
    end
    return out
end

--- 53-byte staged trademon -> the fields a consumer needs. Deliberately NOT the same dict shape as
--- a decoded party mon: a trademon has no level, item, moves, exp, EVs or stats, and pretending
--- otherwise is how a received mon would silently arrive at level 0.
function PT.decode(raw)
    local ok, why = valid_bytes(raw, PT.TRADEMON_SIZE, "trademon")
    if not ok then return nil, why end
    local form_byte, personality = raw[OFF.Form + 1], raw[OFF.Personality + 1]
    local function field(offset)
        local t = {}
        for i = 1, NAME_SIZE do t[i] = raw[offset + i] end
        return t
    end
    return {
        size = PT.TRADEMON_SIZE,
        species_id = species_of(raw[OFF.Species + 1], form_byte),
        form = band(form_byte, PT.FORM_MASK),
        gender = band(form_byte, PT.GENDER_MASK) > 0 and "female" or "male",
        is_egg = band(form_byte, PT.IS_EGG_MASK) > 0,
        shiny = band(personality, PT.SHINY_MASK) > 0,
        ability_slot = band(personality, PT.ABILITY_MASK) / 32,
        nature = band(personality, PT.NATURE_MASK),
        ot_id = raw[OFF.ID + 1] * 256 + raw[OFF.ID + 2],
        caught_data = raw[OFF.CaughtData + 1],
        dvs = {hp_atk = raw[OFF.HPAtkDV + 1], def_spe = raw[OFF.DefSpeDV + 1], sat_sdf = raw[OFF.SatSdfDV + 1]},
        species_name = field(OFF.SpeciesName), sender_name = field(OFF.SenderName),
        ot_name = field(OFF.OTName), nickname = field(OFF.Nickname),
    }
end

-- ── the composition seam ───────────────────────────────────────────────────────

-- spec = {profile=selected title, io, dev=true,
--         read_rom=function(bank,addr,size) -> byte array}.
-- C2.1 loads validation from overlay.trade.validation and its ROM table locator.
-- C4 supplies an admitted ROM reader. Tests may instead inject spec.validation
-- = {glyph_floor,nature_count,species_low_max,items}; items is the exact 256-byte
-- SlinkTradeAllowedItems array indexed 1..256, never a policy callback.
-- This validation is ADVISORY pre-screening only; passing stages a request for
-- SlinkTradeValidateIncomingStaged, which remains the native acceptance authority.
-- No server events are emitted. disposition() returns PENDING / NOT_PERFORMED /
-- CONSENTED / DECLINED / COMPLETE / UNCERTAIN plus a reason; COMPLETE requires native DONE 0.
-- A responder PROMPT's DONE 0 is consent, never a completed trade. Its RELEASE
-- must remain observable for a frame before APPLY. Any APPLY DONE other than 1
-- poisons the visit. phase() returns role, phase for a future, separately owned pump.
-- A write exception latches UNCERTAIN; do not retry it.
-- Every lease publication is read back before success, including its last ACK/
-- generation byte. A mismatch records the exact address/expected/observed bytes
-- and poisons the visit just like an attempted write exception.
-- reset() requires closure and no poison/unsettled APPLY. A pre-APPLY responder
-- can close on B/timeout without claiming a trade; no role switch occurs before reset.
-- Any matching APPLY DONE result other than 1 irreversibly poisons this visit, even
-- if a later frame says result 1 or the native service closes the lease.
-- T.writes is deliberately absent; write_log contains diagnostic receipts only.
-- Tests may opt into spec.test_hooks=true (still requires spec.dev=true).
-- T.test_hooks:write_bytes(addr,bytes) shares APPLY's accepted-offer, frame-gap,
-- lease-identity, attempted and poison checks. Any emission consumes the attempt.
-- Guard refusals return nil/disposition/reason; scoped write exceptions rethrow
-- only after permit cleanup and the same partial-emission poison latch.
-- Diagnostic bytes are also read back; silent partial writes poison the visit.
local function integer(n) return type(n) == "number" and n % 1 == 0 end
local function clone(t)
    if type(t) ~= "table" then return t end
    local out = {}; for k,v in pairs(t) do out[k] = clone(v) end; return out
end
local function slice(t, a, b)
    local out = {}; for i=a,b do out[#out+1] = t[i] end; return out
end

function PT.compose(spec)
    local ok, result = pcall(function()
        assert(type(spec) == "table", "trade spec required")
        local p = clone(assert(spec.profile, "profile required"))
        local t = assert(p.overlay and p.overlay.trade, "overlay.trade required")
        assert(t.schema == "polished-trade-v1", "trade schema unsupported")
        assert(type(t.production) == "boolean" and type(t.capabilities) == "table", "trade capabilities missing")
        for _, k in ipairs({'proposer_service','responder_service','commit'}) do
            assert(type(t.capabilities[k]) == 'boolean', 'trade capability missing: '..k)
        end
        assert(t.capabilities.proposer_service, "proposer service absent")
        local entries = assert(t.entries, "trade entries missing")
        local function site(name)
            local s = assert(entries[name], "trade entry missing: "..name)
            assert(s.symbol == name and integer(s.bank) and s.bank > 0 and integer(s.addr)
                and s.addr >= 0x4000 and s.addr < 0x8000, "invalid trade entry: "..name)
        end
        for _, name in ipairs({'SlinkTradeWaitGate','SlinkTradeTimeoutGate','SlinkTradeEntry',
            'SlinkTradeProposerService','SlinkTradeProposerServiceEnd','SlinkTradeDispatch','SlinkTradePromptEntry'}) do site(name) end
        local function extent(name)
            assert(entries[name].bank == entries[name..'End'].bank and
                   entries[name].addr < entries[name..'End'].addr, 'invalid trade extent: '..name)
        end
        extent('SlinkTradeProposerService')
        for cap,name in pairs({responder_service='SlinkTradeResponderService',commit='SlinkTradeCommit'}) do
            assert((entries[name] ~= nil) == t.capabilities[cap] and
                   (entries[name..'End'] ~= nil) == t.capabilities[cap], 'partial trade component: '..cap)
            if t.capabilities[cap] then site(name); site(name..'End'); extent(name) end
        end
        local pins = assert(t.dispatcher_stack_pin_names, "trade stack pin names missing")
        local pinset = {}; for _, name in ipairs(pins) do pinset[name] = true end
        for _, name in ipairs({'NextOverworldFrame','DelayFrame','NextOverworldFrame.gfx_done','HandleMap','OverworldLoop.loop'}) do
            assert(pinset[name], 'trade stack pin missing: '..name)
        end
        for _, name in ipairs({'QUERY','OFFER','PROMPT','APPLY','DONE','RELEASE'}) do
            assert(t.commands and t.commands[name] == Lease[name], 'trade command mismatch: '..name)
        end
        for _, name in ipairs({'QUERY','OFFER','APPLY','RELEASE'}) do
            assert(t.timeouts and integer(t.timeouts[name]) and t.timeouts[name] > 0, 'trade timeout missing: '..name)
        end
        local l = assert(t.lease, 'trade lease missing')
        assert(l.offset == Lease.OFF_LEASE and l.size == Lease.LEASE_SIZE and l.version == Lease.VERSION,
            'trade lease ABI mismatch')
        assert(l.symbol == 'wSlinkMailbox' and p.overlay.ram.wSlinkMailbox + l.offset == l.base,
            'trade lease base mismatch')
        for i=1,#Lease.MAGIC do assert(l.magic[i] == Lease.MAGIC[i], 'trade magic mismatch') end
        -- These are the shared helper's ABI positions, not vanilla game addresses.
        for name,offset in pairs({magic=0,version=4,command=5,generation=6,ack=7,result=8,slot=9,available=10,mask=11,token=12}) do
            assert(l.fields and l.fields[name] == offset, 'trade field mismatch: '..name)
        end
        local spans, protected = {}, {}
        local function span(s, label)
            assert(type(s) == 'table' and integer(s.addr) and integer(s.size) and s.size > 0,
                'trade span missing: '..label)
            assert((s.bank == 0 and s.addr >= 0xC000 and s.addr+s.size <= 0xD000) or
                   (s.bank == 1 and s.addr >= 0xD000 and s.addr+s.size <= 0xE000), 'trade span bank: '..label)
            return s
        end
        spans[1] = span({bank=l.bank,addr=l.base,size=l.size}, 'lease')
        local st, snap = assert(t.staging,'trade staging missing'), assert(t.snapshot,'trade snapshot missing')
        local d, c, r = p.derived, p.constants, p.structs.party
        local sizes = {party=d.party_struct_size,ot=d.name_length,nickname=d.mon_name_length,sender=d.name_length}
        local names = {party='wOTPartyMon1',ot='wOTPartyMonOTs',nickname='wOTPartyMonNicknames',sender='wOTPlayerName'}
        local snames = {party='wOTPartyMon2',ot='wOTPartyMon2OT',nickname='wOTPartyMon2Nickname'}
        for k,n in pairs(sizes) do
            local s = span(st[k], k); assert(s.symbol == names[k] and s.size == n, 'staging shape: '..k)
            spans[#spans+1] = s
            if k ~= 'sender' then
                s = span(snap[k], k); assert(s.symbol == snames[k] and s.size == n, 'snapshot shape: '..k)
                protected[#protected+1] = s
            end
        end
        local all = {}; for _,s in ipairs(spans) do all[#all+1]=s end
        for _,s in ipairs(protected) do all[#all+1]=s end
        for i,a in ipairs(all) do for j=i+1,#all do local b=all[j]
            assert(a.addr+a.size <= b.addr or b.addr+b.size <= a.addr, 'trade span overlap')
        end end
        local production = spec.dev ~= true and t.production == true
            and t.capabilities.proposer_service == true and t.capabilities.responder_service == true
            and t.capabilities.commit == true
        assert(spec.dev == true or production, 'development proposer trade disabled')
        if production then
            local gate = t.commit_gate
            assert(type(gate) == 'table' and gate.symbol == 'SlinkTradeCommitEnabled'
                   and type(spec.read_rom) == 'function', 'production trade commit gate missing')
            local bytes = spec.read_rom(gate.bank, gate.addr, 1)
            assert(type(bytes) == 'table' and bytes[1] == 1, 'production trade commit gate disabled')
        end
        local v
        if spec.validation ~= nil then
            v = clone(spec.validation) -- explicit override: malformed never falls back
        else
            v = clone(assert(t.validation, 'trade validation profile required'))
            local locator = assert(v.items, 'trade item table locator required')
            assert(locator.symbol == 'SlinkTradeAllowedItems' and integer(locator.bank) and locator.bank > 0
                   and integer(locator.addr) and locator.addr >= 0x4000 and locator.addr+256 <= 0x8000
                   and locator.size == 256, 'invalid trade item table locator')
            assert(type(spec.read_rom) == 'function', 'trade ROM reader required')
            v.items = clone(spec.read_rom(locator.bank,locator.addr,locator.size))
        end
        assert(integer(v.glyph_floor) and v.glyph_floor > p.overlay.panel.terminator and v.glyph_floor <= 255 and
               integer(v.nature_count) and v.nature_count > 0 and v.nature_count <= c.NATURE_MASK+1,
               'invalid validation facts')
        assert(integer(v.species_low_max) and v.species_low_max > 0 and v.species_low_max < 256,
               'invalid species bound')
        assert(Lease.valid_bytes(v.items,256), 'trade item table required')
        for _,value in ipairs(v.items) do assert(value == 0 or value == 1, 'invalid item table') end
        local io = assert(spec.io, 'trade IO required')
        for _,name in ipairs({'read_u8','read_range','write_u8','bank_valid','framecount'}) do
            assert(type(io[name]) == 'function','trade IO missing: '..name)
        end
        -- Optional diagnostics sink (cancel logs exactly once per cancelled visit). Production
        -- composition supplies a Lua log callback; standalone model callers may use print.
        local log = spec.log
        if log == nil then log = print end
        assert(type(log) == 'function', 'trade log must be a function')
        local function frame()
            local n=io.framecount(); assert(integer(n) and n >= 0, 'invalid frame counter'); return n
        end
        local function window(a,n)
            for _,s in ipairs(spans) do if a >= s.addr and a+n <= s.addr+s.size then return s end end
        end
        local emitted, poisoned, lease_written = 0, nil, nil
        local permit = Permit.new({write_u8=function(a,b,dom)
            emitted=emitted+1; return io.write_u8(a,b,dom)
        end, domains={['System Bus']={
            bounds=function(a,n,why) return why == 'trade' and window(a,n) ~= nil end,
            mapped=function(a,n) local s=window(a,n); return s and io.bank_valid(s.bank,a,n) == true end,
            pointer_stable=function() return true end,
        }}, lifetime={capture=frame,valid=function(token) return token == frame() end},
        provenance=function(dom,a,n,why) return {domain=dom,addr=a,n=n,why=why,frame=frame()} end})
        local writes = {write_bytes=function(_,a,b)
            assert(lease_written, 'lease write outside binder scope')
            -- Retain each publication's final value at every written lease
            -- address; APPLY overwrites its preimage generation LAST.
            for i=1,#b do
                local address=a+i-1
                if address >= l.base and address < l.base+l.size then lease_written[address-l.base+1]=b[i] end
            end
            return permit:write_bytes('System Bus',a,b)
        end}
        local function verify_lease(expected)
            for offset=0,l.size-1 do
                local address=l.base+offset
                local wanted=expected[offset+1]
                if wanted ~= nil then
                    local ok,observed=pcall(io.read_u8,address)
                    if not ok or observed ~= wanted then
                        local text=ok and tostring(observed) or ('unreadable: '..tostring(observed))
                        if ok and integer(observed) and observed >= 0 and observed <= 255 then
                            text=string.format('$%02X',observed)
                        end
                        error(string.format('lease readback mismatch at $%04X: expected $%02X, observed %s',
                                            address,wanted,text),0)
                    end
                end
            end
        end
        local function text(bytes, first, n)
            for i=first,first+n-1 do
                if bytes[i] == p.overlay.panel.terminator then return true end
                if bytes[i] < v.glyph_floor then return false end
            end
            return false
        end
        local function check(payload)
            if type(payload) ~= 'table' or not Lease.valid_bytes(payload.blob,st.party.size+st.ot.size+st.nickname.size)
                or not Lease.valid_bytes(payload.sender,st.sender.size) then return 'invalid incoming payload length' end
            local b=payload.blob
            local low=b[r.Species+1]
            local ext=band(b[r.Form+1],c.EXTSPECIES_MASK) ~= 0
            if low == 0 or low > (ext and (d.species_count-256) or v.species_low_max) then return 'invalid incoming species' end
            if v.items[b[r.Item+1]+1] ~= 1 then return 'held item refused' end
            if band(b[r.Personality+1],c.NATURE_MASK) >= v.nature_count then return 'invalid incoming nature' end
            if b[r.Level+1] < 1 or b[r.Level+1] > c.MAX_LEVEL then return 'invalid incoming level' end
            if not text(b,st.party.size+1,d.player_name_length) or
               not text(b,st.party.size+st.ot.size+1,st.nickname.size) or
               not text(payload.sender,1,st.sender.size) then return 'invalid incoming name' end
        end
        local function stage(payload)
            local b=payload.blob
            local batch={
                {domain='System Bus',addr=st.party.addr,bytes=slice(b,1,st.party.size)},
                {domain='System Bus',addr=st.ot.addr,bytes=slice(b,st.party.size+1,st.party.size+st.ot.size)},
                {domain='System Bus',addr=st.nickname.addr,bytes=slice(b,st.party.size+st.ot.size+1,#b)},
                {domain='System Bus',addr=st.sender.addr,bytes=payload.sender},
            }
            permit:write_batch(batch)
            for _,s in ipairs(batch) do
                local observed=io.read_range(s.addr,#s.bytes)
                assert(Lease.valid_bytes(observed,#s.bytes), 'staging readback unreadable')
                for i=1,#s.bytes do assert(observed[i] == s.bytes[i], 'staging readback mismatch') end
            end
        end
        local raw=Lease.new({lease=l.base,party_capacity=d.party_capacity,check=check,stage=stage},io,writes)
        local offered, answered_at, token, attempted, disposition, reason, cancelled
        local role, phase, prompt_visit, prompt_result, released_at
        local T={hooks=clone(entries),spans=clone(st),timeouts=clone(t.timeouts),write_log=permit.log}
        local function scoped(fn,...)
            if poisoned then return nil,'UNCERTAIN',poisoned end
            local before=emitted
            local expected={}
            lease_written=expected
            local out=table.pack(pcall(function(...)
                local result=table.pack(permit:scope('trade',nil,fn,...))
                verify_lease(expected) -- all written fields, not just the publication byte
                return table.unpack(result,1,result.n)
            end,...))
            lease_written=nil
            if not out[1] then
                if emitted > before then poisoned=tostring(out[2]); return nil,'UNCERTAIN',poisoned end
                return nil,'PENDING',tostring(out[2])
            end
            if out[2] == nil then return nil,'PENDING',tostring(out[3]) end
            return table.unpack(out,2,out.n)
        end
        function T:advertised() return production end -- explicit dev probes never advertise production trade
        function T:phase() return role,phase end
        function T:poll_query() if not poisoned and not attempted and not cancelled and role ~= 'responder' then return raw:poll_query() end end
        function T:poll_offer() if not poisoned and not attempted and not cancelled and role ~= 'responder' then return raw:poll_offer() end end
        function T:answer_query(gen,mask,visit)
            if poisoned then return nil,'UNCERTAIN',poisoned end
            if cancelled then return nil,'NOT_PERFORMED',reason end
            if role == 'responder' then return nil,'PENDING','responder visit active' end
            if attempted then return nil,'PENDING','APPLY already attempted' end
            local yes,a,b=scoped(raw.answer_query,raw,gen,mask,visit)
            if yes then
                role,phase='proposer','query'
                token=clone(visit); offered=nil; answered_at=nil; disposition=nil; reason=nil; return yes
            end
            return yes,a,b
        end
        function T:answer_offer(gen,accept)
            if poisoned then return nil,'UNCERTAIN',poisoned end
            if cancelled then return nil,'NOT_PERFORMED',reason end
            if role == 'responder' then return nil,'PENDING','responder visit active' end
            if attempted or offered then return nil,'PENDING','OFFER already answered' end
            local offer=raw:poll_offer()
            local yes,a,b=scoped(raw.answer_offer,raw,gen,accept)
            if yes then
                offered=clone(offer); offered.accepted=accept; answered_at=frame()
                phase='offer'
                if not accept then disposition='NOT_PERFORMED'; reason='offer rejected' end
                return yes
            end
            return yes,a,b
        end
        local function apply_ready(command,slot,visit)
            if poisoned then return nil,'UNCERTAIN',poisoned end
            if cancelled then return nil,'NOT_PERFORMED',reason end
            if command ~= t.commands.APPLY then return nil,'PENDING','proposer APPLY only' end
            if role == 'responder' then
                if attempted or phase ~= 'released' or prompt_result ~= 0 then
                    return nil,'PENDING','consent RELEASE required'
                end
                if frame() <= released_at then return nil,'PENDING','RELEASE frame gap required' end
                if slot ~= prompt_visit.slot or not Lease.valid_bytes(visit,4) then
                    return nil,'PENDING','prompt slot/token mismatch'
                end
                for i=1,4 do if visit[i] ~= token[i] then return nil,'PENDING','visit token mismatch' end end
                local bytes=io.read_range(l.base,l.size)
                if not Lease.valid_bytes(bytes,l.size) then return nil,'PENDING','unreadable lease' end
                for i=1,4 do if bytes[l.fields.token+i] ~= token[i] or bytes[l.fields.magic+i] ~= l.magic[i] then
                    return nil,'PENDING','lease identity changed' end end
                if bytes[l.fields.version+1] ~= l.version or bytes[l.fields.command+1] ~= t.commands.RELEASE or
                   bytes[l.fields.generation+1] ~= prompt_visit.gen or bytes[l.fields.ack+1] ~= prompt_visit.gen or
                   bytes[l.fields.slot+1] ~= slot or bytes[l.fields.result+1] ~= 0 then
                    return nil,'PENDING','consent release lease changed'
                end
                return true
            end
            if attempted or not offered or not offered.accepted then return nil,'PENDING','accepted OFFER required' end
            if frame() <= answered_at then return nil,'PENDING','OFFER frame gap required' end
            if slot ~= offered.slot or not Lease.valid_bytes(visit,4) then return nil,'PENDING','offer slot/token mismatch' end
            for i=1,4 do if visit[i] ~= token[i] then return nil,'PENDING','visit token mismatch' end end
            local bytes=io.read_range(l.base,l.size)
            if not Lease.valid_bytes(bytes,l.size) then return nil,'PENDING','unreadable lease' end
            for i=1,4 do if bytes[l.fields.token+i] ~= token[i] or bytes[l.fields.magic+i] ~= l.magic[i] then
                return nil,'PENDING','lease identity changed' end end
            if bytes[l.fields.version+1] ~= l.version or bytes[l.fields.command+1] ~= t.commands.OFFER or
               bytes[l.fields.generation+1] ~= offered.gen or bytes[l.fields.ack+1] ~= offered.gen or
               bytes[l.fields.slot+1] ~= slot or bytes[l.fields.result+1] ~= 0 then return nil,'PENDING','offer lease changed' end
            return true
        end
        local function apply_unpublished(slot)
            -- Fresh generation is the APPLY publication boundary. The full frame may already contain
            -- APPLY/result $FF, but both ROM services reject generation == ACK before pickup. Prove that
            -- BOTH still equal this visit's held generation, with its header/token/slot intact. A fresh
            -- generation (even already ACKed), or an unreadable lease, keeps the UNCERTAIN latch.
            local held_gen=role == 'responder' and prompt_visit.gen or offered.gen
            local ok,bytes=pcall(io.read_range,l.base,l.size)
            if not ok or not Lease.valid_bytes(bytes,l.size) then return false end
            local f=l.fields
            for i=1,4 do
                if bytes[f.magic+i] ~= l.magic[i] or bytes[f.token+i] ~= token[i] then return false end
            end
            return bytes[f.version+1] == l.version
               and bytes[f.generation+1] == held_gen and bytes[f.ack+1] == held_gen
               and bytes[f.slot+1] == slot
        end
        function T:arm(command,slot,visit,payload)
            if poisoned then return nil,'UNCERTAIN',poisoned end
            if cancelled then return nil,'NOT_PERFORMED',reason end
            if command == t.commands.PROMPT then
                if role ~= nil then return nil,'PENDING','visit already active; reset after closure' end
                if not t.capabilities.responder_service then return nil,'PENDING','responder service absent' end
                if not self:closed() then return nil,'PENDING','lease must close before PROMPT' end
                local gen,a,b=scoped(raw.arm,raw,command,slot,clone(visit),clone(payload))
                if gen then
                    role,phase='responder','prompt'
                    token=clone(visit); prompt_visit={gen=gen,slot=slot}
                    prompt_result,released_at,attempted,disposition,reason=nil,nil,nil,nil,nil
                    return gen
                end
                return gen,a,b
            end
            -- Preflight is read-only: a thrown read cannot publish APPLY. The caller may use
            -- the ordinary token cancellation; only the emitting scope below can poison.
            local ok,ready,a,b=pcall(apply_ready,command,slot,visit)
            if not ok then return nil,'NOT_PUBLISHED','preflight read failed: '..tostring(ready) end
            if not ready then return nil,a,b end
            local gen,a,b=scoped(raw.arm,raw,command,slot,clone(visit),clone(payload))
            if gen then attempted=true; phase='apply'; disposition=nil; reason=nil; return gen end
            if a == 'UNCERTAIN' and poisoned == b and apply_unpublished(slot) then
                -- Owner ruling 2026-10-07: emission failed before the fresh generation publication,
                -- and readback PROVES this visit is unpublished, so no commit can have
                -- started: pre-APPLY uncertainty closes the lease through the cancel path, logs once and
                -- reports NOT_PERFORMED. Anything the readback cannot prove stays poisoned (UNCERTAIN).
                poisoned=nil
                local closed_,ca,cb=self:cancel('APPLY not published: '..b)
                if closed_ or ca == 'NOT_PERFORMED' then return nil,'NOT_PERFORMED',reason end
                if ca ~= 'UNCERTAIN' then poisoned=b end -- the close did not emit either: keep the latch
                return nil,'UNCERTAIN',poisoned
            end
            return gen,a,b
        end
        local function matching_done_result()
            -- Preserve the shared helper's header/gen/ACK/slot/token binding,
            -- but do not ignore an out-of-enum result and later accept result 1.
            local expected=raw.expected
            if not expected then return nil end
            local bytes=io.read_range(l.base,l.size)
            if not Lease.valid_bytes(bytes,l.size) then return nil end
            for i=1,5 do if bytes[i] ~= expected[i] then return nil end end
            local generation=expected[l.fields.generation+1]
            if bytes[l.fields.command+1] ~= t.commands.DONE or
               bytes[l.fields.generation+1] ~= generation or bytes[l.fields.ack+1] ~= generation or
               bytes[l.fields.slot+1] ~= expected[l.fields.slot+1] then return nil end
            for i=1,4 do if bytes[l.fields.token+i] ~= expected[l.fields.token+i] then return nil end end
            return bytes[l.fields.result+1]
        end
        function T:poll_done()
            if poisoned then return {disposition='UNCERTAIN',reason=poisoned} end
            if cancelled then return nil end -- a DONE the ROM may still publish (decline) is not ours to claim
            local result=matching_done_result(); if result == nil then return nil end
            raw.phase='done'
            if role == 'responder' and (phase == 'prompt' or phase == 'consented' or phase == 'declined') then
                if (result ~= 0 and result ~= 1) or (prompt_result ~= nil and result ~= prompt_result) then
                    poisoned='unexpected or changed PROMPT result '..tostring(result)
                    disposition,reason='UNCERTAIN',poisoned
                else
                    prompt_result=result
                    phase=result == 0 and 'consented' or 'declined'
                    disposition=result == 0 and 'CONSENTED' or 'DECLINED'
                    reason=result == 0 and 'prompt consent only; no trade performed' or 'prompt declined'
                end
            elseif result == 0 then
                disposition,reason='COMPLETE','native commit saved and verified'
                phase='done'
            elseif result ~= 1 then
                poisoned='unexpected DONE result '..tostring(result)
                disposition,reason='UNCERTAIN',poisoned
            else
                disposition,reason='NOT_PERFORMED','commit disabled'
                phase='done'
            end
            return {result=result,disposition=disposition,reason=reason}
        end
        function T:release(gen)
            if poisoned then return nil,'UNCERTAIN',poisoned end
            if cancelled then return nil,'NOT_PERFORMED',reason end
            local done=self:poll_done()
            local prompt_done=role == 'responder' and (phase == 'consented' or phase == 'declined')
            if not done or (done.disposition ~= 'NOT_PERFORMED' and done.disposition ~= 'COMPLETE' and not prompt_done) then
                return nil,'UNCERTAIN',poisoned or 'not a safe DONE'
            end
            local yes,a,b=scoped(raw.release,raw,gen)
            if yes then
                if prompt_done then phase='released'; released_at=frame() end
                return yes
            end
            return yes,a,b
        end
        function T:closed() return io.read_u8(l.base+l.fields.command) == 0 end
        function T:disposition()
            if poisoned then return 'UNCERTAIN',poisoned end
            if cancelled then return 'NOT_PERFORMED',reason end
            if role == 'responder' and not attempted and self:closed() then
                if disposition == 'DECLINED' then return disposition,reason end
                return 'NOT_PERFORMED','responder closed before APPLY'
            end
            if disposition then return disposition,reason end
            if self:closed() then return attempted and 'UNCERTAIN' or 'NOT_PERFORMED','lease closed' end
            return 'PENDING','awaiting native service'
        end
        function T:reset()
            if poisoned then return nil,'UNCERTAIN',poisoned end
            if attempted and disposition ~= 'NOT_PERFORMED' and disposition ~= 'COMPLETE' then
                return nil,'UNCERTAIN','unsettled visit cannot reset'
            end
            if not self:closed() then return nil,'PENDING','lease must close before reset' end
            offered,answered_at,token,attempted,disposition,reason,cancelled=nil,nil,nil,nil,nil,nil,nil
            role,phase,prompt_visit,prompt_result,released_at=nil,nil,nil,nil,nil
            raw.expected,raw.phase,raw.visit_token,raw.entry_observed=nil,nil,nil,false
            return true
        end
        -- Host-side close of a visit BEFORE any APPLY is armed (owner ruling 2026-10-07: pre-APPLY
        -- uncertainty closes the lease, logs once and reports NOT_PERFORMED; UNCERTAIN stays reserved for an
        -- armed APPLY, whose path is untouched here). The bytes are the ROM's own documented host-side closes
        -- (patch/polished/src/trade_service.asm:20-21: "an OFFER reject, a token/slot/generation drift ...
        -- closes the lease with no DONE"):
        --   * the four visit-token bytes are ZEROED. SlinkTradeCheckToken refuses a zero token
        --     (trade_frame.asm:50,69-71) at every pre-APPLY inspection of both services -- proposer
        --     trade_service.asm:81 (after the QUERY answer, before the party menu), :114 (CheckHeldFrame after
        --     the native menus, before the snapshot and the OFFER), :140 (after the OFFER answer), :166 (every
        --     APPLY-wait frame); responder trade_responder.asm:70 (entry), :106 (CheckHeldFrame after the
        --     YesNoBox, before the consent snapshot/DONE), :150 (every RELEASE-owed / APPLY-wait frame) -- and
        --     each refusal is `jp SlinkTradeExit` / `SlinkTradeResponderExit` (release snapshot, SlinkTradeClose,
        --     balanced stack), never a DONE. Command and generation are NOT written: a cancel can never be read
        --     as a PROMPT, APPLY, RELEASE, consent or commit. A RELEASE would be ignored by the APPLY wait
        --     (trade_service.asm:168-170) and a command 0 likewise, so neither is a close.
        --   * a pending UNANSWERED OFFER is also rejected (result 1, then ACK = its generation LAST, exactly
        --     answer_offer(gen,false)): trade_service.asm:145-147 exits on any nonzero result, so the 600-frame
        --     OFFER wait ends on the next frame instead of at its timeout.
        -- Post-DONE waits ignore the token (trade_service.asm:227-229, trade_responder.asm:227-229 `jr c, .wait`)
        -- and close on their own 90-frame bound; a responder NO pressed after the cancel still publishes its
        -- decline DONE (:128-131), which poll_done no longer claims. Not reversible: the visit stays
        -- NOT_PERFORMED until reset() after the ROM's close. Returns true on success, or
        -- nil,'NOT_PERFORMED',reason for an incomplete close (token check/watchdog still owes closure).
        -- A refusal without a write returns nil,'PENDING',why; an existing poison stays UNCERTAIN.
        function T:cancel(why)
            if poisoned then return nil,'UNCERTAIN',poisoned end
            if cancelled then return true end -- idempotent: no second write, no second log
            if attempted then return nil,'PENDING','APPLY already armed: cancel refused' end
            if role == nil then return nil,'PENDING','no visit to cancel' end
            if type(why) ~= 'string' or why == '' then return nil,'PENDING','cancel reason required' end
            if disposition ~= nil and disposition ~= 'CONSENTED' then
                return nil,disposition,'visit already terminal: '..tostring(reason)
            end
            local pending=(role == 'proposer' and not offered) and raw:poll_offer() or nil
            local yes,a,b=scoped(function()
                if pending then writes:write_bytes(l.base+l.fields.result,{1}) end -- cancel: OFFER reject first
                writes:write_bytes(l.base+l.fields.token,{0,0,0,0}) -- cancel: token drift
                if pending then writes:write_bytes(l.base+l.fields.ack,{pending.gen}) end -- cancel: ACK publishes LAST
                return true
            end)
            -- These close bytes never publish APPLY. Even a failed/partial close cannot start a commit.
            -- The ROM observes token mismatch, or its watchdog closes if every token store was lost.
            local detail=why
            if not yes then
                poisoned=nil
                detail=why..'; close incomplete: '..tostring(b)..' (ROM token mismatch or watchdog will close lease)'
            end
            cancelled=true; raw.visit_token=nil
            disposition,reason='NOT_PERFORMED','cancelled before APPLY: '..detail
            local previous_phase=phase
            phase='cancelled' -- terminal state is consistent even inside a throwing optional log sink
            pcall(log,string.format('[SLink-polished] trade visit cancelled before APPLY (%s/%s): %s',
                                    tostring(role),tostring(previous_phase),detail))
            if not yes then return nil,'NOT_PERFORMED',reason end
            return true
        end
        if spec.test_hooks == true then
            T.test_hooks={write_bytes=function(_,a,bytes)
                local ready,kind,why=apply_ready(t.commands.APPLY,offered and offered.slot,token)
                if not ready then return nil,kind,why end
                local before=emitted
                local yes,disposition,reason=scoped(function()
                    local stable=clone(bytes)
                    writes:write_bytes(a,stable)
                    local observed=io.read_range(a,#stable)
                    assert(Lease.valid_bytes(observed,#stable), 'diagnostic readback malformed')
                    for i=1,#stable do
                        if observed[i] ~= stable[i] then
                            error(string.format('diagnostic readback mismatch at $%04X: expected $%02X, observed $%02X',
                                                a+i-1,stable[i],observed[i]),0)
                        end
                    end
                    return true
                end)
                if emitted > before then attempted=true end
                if not yes then error(reason or disposition,0) end
                return true
            end}
        end
        return T
    end)
    if not ok then return nil, tostring(result) end
    return result
end

return PT
