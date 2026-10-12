-- lua/core/identity.lua -- the key_change alias machinery (D-13), shared by every generation.
--
-- Lifted from lua/gen1/client.lua:367-440 (find_party_slot / observe_alias / alias_departure),
-- :526-548 (key_change_ack / key_change_rejected) and :1852-1858 (every readable frame observes
-- every alias). Semantics are unchanged; only the storage moved from the client table here.
--
-- Can Gen 1 bind it unchanged? It needs key(m), m.slot, m.nickname_bytes and m.moves, which
-- gen1/reads.lua already supplies (the code below is its own). gen3/reads.lua supplies the same
-- four (key :269, nickname_bytes :242, moves via decode_secure; slot from read_party). The
-- two Gen 1 fields map to: client.key_alias -> Id.pending, client.retired_alias -> Id.retired_alias
-- (a stable table: clear() empties it in place, so an alias of it stays valid).
--
-- Rules (review ids in lua/gen1/client.lua comments):
--   * two party records answering to one key: refuse, never guess (boxes.lua:121-130 rule);
--   * a REJECTED key_change leaves the server naming the OLD key while the cartridge holds the
--     NEW one: the old key resolves to the one record carrying the new key AND matching the
--     evidence (nickname bytes + moves) frozen at the change;
--   * ambiguity and loss are LATCHED: a shrinking candidate set never authorises a retirement.
local Identity = {}
Identity.__index = Identity

local AMBIGUOUS = "ambiguous key (indistinguishable duplicate)"
local LOST = "retired record left the party"

local function same_bytes(a, b)
    if not a or not b or #a ~= #b then return false end
    for i = 1, #a do if a[i] ~= b[i] then return false end end
    return true
end

-- What tells the changed record apart from a duplicate of its (new) key: the fields a
-- key_change never touches and a party swap cannot forge. A slot is NOT evidence (the player
-- can reorder the party between the change and the retirement).
local function evidence_of(m) return { nick = m.nickname_bytes, moves = m.moves } end
local function matches(m, e) return same_bytes(m.nickname_bytes, e.nick) and same_bytes(m.moves, e.moves) end

-- p.key(m) -> string: the generation's identity key for a decoded mon.
function Identity.new(p)
    return setmetatable({ key = assert(p and p.key, "identity: key(m) required"),
                          pending = nil, pending_aliases = {}, retired_alias = {} }, Identity)
end

-- Observe one alias against a readable party: latches ambiguous/lost, returns the sole match.
function Identity:observe_one(a, party)
    local slot, mon, n = nil, nil, 0
    for _, m in ipairs(party) do
        if self.key(m) == (a.target_key or a.new_key) and matches(m, a.evidence) then
            n = n + 1
            slot, mon = m.slot, m
        end
    end
    if n > 1 then a.ambiguous = true end
    if n == 0 then a.lost = true end
    if a.ambiguous then return nil, nil, AMBIGUOUS end
    if a.lost then return nil, nil, LOST end
    return slot, mon
end

-- Every readable frame (pure pass, nothing refreshed): an edit interval in which the changed
-- record stops matching, however briefly, latches `lost` before a twin could become the match.
function Identity:observe(party)
    for _, a in pairs(self.pending_aliases) do self:observe_one(a, party) end
    for _, r in pairs(self.retired_alias) do self:observe_one(r, party) end
end

function Identity:active() return next(self.pending_aliases) ~= nil or next(self.retired_alias) ~= nil end

function Identity:pending_for(key) return self.pending_aliases[key] end

-- A key_change was observed (and sent): hold the old->new alias until the server answers.
-- `party` (optional) is observed at once, so a twin present now latches ambiguity for good.
function Identity:begin_alias(old_key, new_key, mon, party, since)
    for _, aliases in ipairs({self.pending_aliases, self.retired_alias}) do
        for _, a in pairs(aliases) do
            if (a.target_key or a.new_key) == old_key then
                if matches(mon, a.evidence) then a.target_key = new_key else a.lost = true end
            end
        end
    end
    self.pending = { old_key = old_key, new_key = new_key, evidence = evidence_of(mon), since = since }
    self.pending_aliases[old_key] = self.pending
    if party then self:observe_one(self.pending, party) end
    return self.pending
end

-- key_change_ack: the rename is committed server-side; the alias ends. Returns it, or nil.
function Identity:ack(old_key)
    local a = self.pending_aliases[old_key]
    self.pending_aliases[old_key] = nil
    if self.pending == a then self.pending = next(self.pending_aliases) and select(2, next(self.pending_aliases)) or nil end
    return a
end

-- key_change_rejected: the cartridge cannot be rolled back, so the retirement the server queues
-- under the OLD key must find the record by the key it physically holds. Returns the retired
-- alias, or nil when the rejection is not for the pending alias.
function Identity:reject(old_key, party)
    local a = self:pending_for(old_key)
    if not a then return nil end
    local r = { new_key = a.new_key, target_key = a.target_key,
                evidence = a.evidence, ambiguous = a.ambiguous, lost = a.lost }
    self.retired_alias[old_key] = r
    if party then self:observe_one(r, party) end
    self:ack(old_key)
    return r
end

-- A record carrying an aliased key left the party natively (PC, daycare, release, trade): the
-- alias's authority ends for good. key == nil (unreadable departure) hits every alias.
function Identity:departure(key)
    local function hit(a) if a and (key == nil or key == (a.target_key or a.new_key)) then a.lost = true end end
    for _, a in pairs(self.pending_aliases) do hit(a) end
    for _, r in pairs(self.retired_alias) do hit(r) end
end

function Identity:retired(key) return self.retired_alias[key] end
function Identity:forget(key) self.retired_alias[key] = nil end    -- its memorial landed

-- The save was cleared (reset / new game): every alias belonged to the session that ended.
function Identity:clear()
    self.pending = nil
    for k in pairs(self.pending_aliases) do self.pending_aliases[k] = nil end
    for k in pairs(self.retired_alias) do self.retired_alias[k] = nil end
end

-- The one resolver: returns slot, mon, party, why. why = "ambiguous key" for a duplicated plain
-- key, or an alias reason for a retired key; party == nil (unreadable) returns nothing at all.
-- A key NO record answers to, with no reason of its own, is the OLD key of a change still in
-- flight: the server names the mon by the old key until it answers, and the cartridge already
-- holds the new one. So the pending alias resolves it on the same terms as a retired one (the
-- branch above): without this every command the server sends under the old key is dropped as
-- "key not in party" for the whole refusal window (review cx-876c8b77 MAJOR-1).
function Identity:find_party_slot(key, party)
    if not party then return nil end
    local r = self.retired_alias[key]
    if r then
        local slot, mon, why = self:observe_one(r, party)
        return slot, mon, party, why
    end
    local slot, mon
    for _, m in ipairs(party) do
        if self.key(m) == key then
            if slot then return nil, nil, party, "ambiguous key" end
            slot, mon = m.slot, m
        end
    end
    if not slot then
        local a = self:pending_for(key)
        if a and a.old_key == key then
            local aslot, amon, why = self:observe_one(a, party)
            if aslot or why then return aslot, amon, party, why end
        end
    end
    return slot, mon, party, nil
end

return Identity
