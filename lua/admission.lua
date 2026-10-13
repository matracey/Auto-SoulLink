-- Neutral actual-byte admission. Catalog, acquisition, anchors, eligibility,
-- artifact kinds and descriptive fields belong to the binder, never this core.
local Admission = {}
local unpack_values = table.unpack or unpack
local function integer(value) return type(value) == "number" and value % 1 == 0 end
local function byte(value) return integer(value) and value >= 0 and value <= 255 end

-- Pure-Lua SHA-1, extracted from Gen 1's FIPS 180-4 implementation. The source
-- is a flat byte reader; no emulator, title, header or memory domain is assumed.
function Admission.sha1(read_u8, n)
    assert(integer(n) and n >= 0, "SHA-1 byte length required")
    local M = 0xFFFFFFFF
    local function rol(x, k) return ((x << k) | (x >> (32 - k))) & M end
    local h0, h1, h2, h3, h4 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0
    local total = n + 1
    while total % 64 ~= 56 do total = total + 1 end
    total = total + 8
    local bits = n * 8
    local function byte_at(i)
        if i < n then local value = read_u8(i); assert(byte(value), "unavailable or invalid artifact byte"); return value end
        if i == n then return 0x80 end
        if i < total - 8 then return 0 end
        return (bits >> (8 * (total - 1 - i))) & 0xFF
    end
    local w = {}
    for chunk = 0, total - 1, 64 do
        for t = 0, 15 do
            local b = chunk + t * 4
            w[t] = (byte_at(b) << 24) | (byte_at(b + 1) << 16) | (byte_at(b + 2) << 8) | byte_at(b + 3)
        end
        for t = 16, 79 do w[t] = rol(w[t - 3] ~ w[t - 8] ~ w[t - 14] ~ w[t - 16], 1) end
        local a, b, c, d, e = h0, h1, h2, h3, h4
        for t = 0, 79 do
            local f, k
            if t < 20 then f, k = (b & c) | ((~b) & d), 0x5A827999
            elseif t < 40 then f, k = b ~ c ~ d, 0x6ED9EBA1
            elseif t < 60 then f, k = (b & c) | (b & d) | (c & d), 0x8F1BBCDC
            else f, k = b ~ c ~ d, 0xCA62C1D6 end
            local tmp = (rol(a, 5) + (f & M) + e + k + w[t]) & M
            e, d, c, b, a = d, c, rol(b, 30), a, tmp
        end
        h0, h1, h2, h3, h4 = (h0 + a) & M, (h1 + b) & M, (h2 + c) & M, (h3 + d) & M, (h4 + e) & M
    end
    return string.format("%08x%08x%08x%08x%08x", h0, h1, h2, h3, h4)
end

-- FIPS 180-4 SHA-256 over a flat byte reader, without emulator or game assumptions.
local K = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2}
function Admission.sha256(byte_at, n)
    assert(integer(n) and n >= 0 and n <= 2^40, "SHA-256 byte length required")
    local M = 0xFFFFFFFF
    local function ror(x, k) return ((x >> k) | (x << (32 - k))) & M end
    local H = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}
    local total = n + 1
    while total % 64 ~= 56 do total = total + 1 end
    total = total + 8
    local bits = n * 8
    local function b(i)
        if i < n then
            local value = byte_at(i)
            assert(byte(value), "unavailable or invalid hashed byte")
            return value
        end
        if i == n then return 0x80 end
        if i < total - 8 then return 0 end
        return (bits >> (8 * (total - 1 - i))) & 0xFF
    end
    local w = {}
    for chunk = 0, total - 1, 64 do
        for t = 0, 15 do
            local p = chunk + t * 4
            w[t] = (b(p) << 24) | (b(p + 1) << 16) | (b(p + 2) << 8) | b(p + 3)
        end
        for t = 16, 63 do
            local s0 = ror(w[t-15], 7) ~ ror(w[t-15], 18) ~ (w[t-15] >> 3)
            local s1 = ror(w[t-2], 17) ~ ror(w[t-2], 19) ~ (w[t-2] >> 10)
            w[t] = (w[t-16] + s0 + w[t-7] + s1) & M
        end
        local a, bb, c, d, e, f, g, h = H[1], H[2], H[3], H[4], H[5], H[6], H[7], H[8]
        for t = 0, 63 do
            local t1 = (h + (ror(e, 6) ~ ror(e, 11) ~ ror(e, 25)) + ((e & f) ~ ((~e) & g)) + K[t+1] + w[t]) & M
            local t2 = ((ror(a, 2) ~ ror(a, 13) ~ ror(a, 22)) + ((a & bb) ~ (a & c) ~ (bb & c))) & M
            h, g, f, e, d, c, bb, a = g, f, e, (d + t1) & M, c, bb, a, (t1 + t2) & M
        end
        for i, v in ipairs({a, bb, c, d, e, f, g, h}) do H[i] = (H[i] + v) & M end
    end
    return string.format("%08x%08x%08x%08x%08x%08x%08x%08x", H[1], H[2], H[3], H[4], H[5], H[6], H[7], H[8])
end


local function array_length(value, label)
    assert(type(value) == "table" and getmetatable(value) == nil, label .. ": plain array required")
    local count, maximum = 0, 0
    for key in next, value do
        assert(integer(key) and key >= 1, label .. ": integer indices required")
        count, maximum = count + 1, math.max(key, maximum)
    end
    assert(count == maximum, label .. ": sparse array refused")
    return count
end

local function readonly(data)
    return setmetatable({}, {
        __index=data, __newindex=function() error("admission decision is immutable", 2) end,
        __pairs=function()
            -- Generic-for receives iterator state publicly. Keep the backing table
            -- inside the closure so pairs() cannot become an ordinary write route.
            return function(_, previous) return next(data, previous) end, nil, nil
        end,
        __len=function() return #data end,
        __metatable="immutable admission value",
    })
end

local function freeze(value, active)
    local kind = type(value)
    if kind ~= "table" then
        assert(kind == "string" or kind == "boolean" or kind == "nil"
               or (kind == "number" and value == value and math.abs(value) ~= math.huge),
               "decision fields must be finite plain data")
        return value
    end
    assert(getmetatable(value) == nil and not active[value], "decision must be acyclic plain data")
    active[value] = true
    local data = {}
    for key, item in pairs(value) do
        assert(type(key) == "string" or integer(key), "invalid decision field key")
        data[key] = freeze(item, active)
    end
    active[value] = nil
    return readonly(data)
end

local function acquire(policy, request)
    local source = policy.acquire(request)
    assert(type(source) == "table", "artifact acquisition unavailable")
    if source.bytes ~= nil then
        assert(type(source.bytes) == "string" and #source.bytes > 0, "nonempty actual artifact bytes required")
        if source.size ~= nil then assert(source.size == #source.bytes, "artifact size disagrees with bytes") end
        return source.bytes
    end
    assert(integer(source.size) and source.size > 0, "positive actual artifact size required")
    assert(type(source.read_u8) == "function" or type(source.read_u8) == "userdata", "actual artifact reader required")
    local chunks, buffer, used = {}, {}, 0
    for offset = 0, source.size - 1 do
        local value = source.read_u8(offset)
        if not byte(value) then error("unavailable or invalid artifact byte at " .. offset) end
        used = used + 1
        buffer[used] = value
        if used == 512 then chunks[#chunks + 1], used = string.char(unpack_values(buffer, 1, used)), 0 end
    end
    if used > 0 then chunks[#chunks + 1] = string.char(unpack_values(buffer, 1, used)) end
    return table.concat(chunks)
end

function Admission.anchors_match(anchors, artifact, mode)
    assert(mode == "sha1" or mode == "anchors", "explicit anchor mode required")
    assert(type(artifact) == "table" and integer(artifact.size) and artifact.size > 0
           and (type(artifact.read_u8) == "function" or type(artifact.read_u8) == "userdata"),
           "bounded artifact reader required")
    local count = array_length(anchors, "anchors")
    assert(mode ~= "anchors" or count > 0, "unknown-hash admission requires nonempty anchors")
    for _, anchor in ipairs(anchors) do
        assert(type(anchor) == "table" and integer(anchor.offset) and anchor.offset >= 0
               and type(anchor.hex) == "string" and #anchor.hex > 0 and #anchor.hex % 2 == 0
               and anchor.hex:match("^[0-9a-fA-F]+$"), "malformed required anchor")
        local length = #anchor.hex // 2
        assert(anchor.offset <= artifact.size - length, "anchor outside actual artifact")
        for index = 0, length - 1 do
            if artifact.read_u8(anchor.offset + index) ~= tonumber(anchor.hex:sub(2*index+1, 2*index+2), 16) then
                return false
            end
        end
    end
    return true
end

function Admission.new(policy)
    assert(type(policy) == "table", "admission policy required")
    for _, name in ipairs({"acquire", "catalog", "hashes", "eligible", "anchors", "kind", "describe"}) do
        assert(type(policy[name]) == "function", "admission policy." .. name .. " required")
    end
    assert(policy.allow_unknown_hash == nil or type(policy.allow_unknown_hash) == "boolean",
           "allow_unknown_hash must be an explicit boolean")
    local self = {}
    local function evaluate(request)
        local bytes = acquire(policy, request)
        local function read_u8(offset)
            assert(integer(offset) and offset >= 0 and offset < #bytes, "snapshot read outside artifact")
            return bytes:byte(offset + 1)
        end
        local digest = Admission.sha1(read_u8, #bytes)
        local artifact = readonly({bytes=bytes, size=#bytes, sha1=digest, read_u8=read_u8})
        local catalog = policy.catalog(request, artifact)
        array_length(catalog, "catalog")
        local known, found_known = {}, false
        for index, candidate in ipairs(catalog) do
            local hashes = policy.hashes(candidate)
            array_length(hashes, "candidate hashes")
            for _, hash in ipairs(hashes) do
                assert(type(hash) == "string" and #hash == 40 and hash:match("^[0-9a-fA-F]+$"), "invalid catalog SHA-1")
                if hash:lower() == digest then known[index], found_known = true, true end
            end
        end
        if not found_known and policy.allow_unknown_hash ~= true then
            return nil, "unknown artifact SHA-1: " .. digest
        end
        local mode, matches, refusal = found_known and "sha1" or "anchors", {}, "no eligible admission candidate"
        for index, candidate in ipairs(catalog) do
            if not found_known or known[index] then
                local eligible, why = policy.eligible(candidate, mode, request, artifact)
                if eligible == true then
                    local kind, kind_why = policy.kind(candidate, mode, request, artifact)
                    if type(kind) == "string" and kind ~= "" then
                        if Admission.anchors_match(policy.anchors(candidate, mode, request, artifact), artifact, mode) then
                            matches[#matches + 1] = {candidate=candidate, kind=kind}
                        else refusal = "required artifact anchor mismatch" end
                    else refusal = kind_why or "artifact mode/kind refused" end
                else refusal = why or "candidate eligibility refused" end
            end
        end
        if #matches == 0 then return nil, refusal end
        if #matches ~= 1 then return nil, "ambiguous admission: " .. #matches .. " eligible candidates" end
        local match = matches[1]
        local supplied = policy.describe(match.candidate, match.kind, mode, request, artifact)
        assert(type(supplied) == "table" and getmetatable(supplied) == nil, "plain decision fields required")
        local decision = {}
        for key, value in pairs(supplied) do decision[key] = value end
        -- Reserved proof fields cannot be supplied from a database/reporting hash.
        decision.rom_sha1, decision.rehashed, decision.admitted_by = digest, true, mode
        decision.kind = match.kind
        local result = freeze(decision, {})
        assert(acquire(policy, request) == bytes, "artifact changed during admission")
        return result
    end
    function self:admit(request)
        local ok, result, reason = pcall(evaluate, request)
        if not ok then return nil, tostring(result) end
        if result == nil then return nil, reason end
        return result
    end
    return self
end

return Admission
