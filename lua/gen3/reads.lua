-- lua/gen3/reads.lua — profile-driven Gen 3 (FRLG + Radical Red) record reads.
--
-- No BizHawk globals and no literal addresses: every address is a field of the pack's
-- profile.json (`profile.ram` / `profile.derived`), and every byte arrives through the
-- injected read-only `io`:
--
--   io.read_u8(addr)  io.read_u16(addr)  io.read_u32(addr)   -- GBA flat bus, little-endian
--   io.read_bytes(addr, len) -> {b1, ..., blen}
--
-- The numbers below are RECORD GEOMETRY (sizeof(struct Pokemon) and friends,
-- include/pokemon.h#L8-L141 at the pinned pret commit c75f3523), not addresses; a pack may
-- override any of them through profile.derived. The pack does not ship them today — see the
-- card report's "missing pack fields".
--
-- The Python twin of this file is server/adapters/gen3_codec.py (the PYDEC oracle, PLAN
-- §5.7): the two are written from pret independently and must agree byte for byte.
local R = {}

R.PARTY_MON_SIZE = 100          -- sizeof(struct Pokemon)
R.BOX_MON_SIZE = 80             -- sizeof(struct BoxPokemon)
R.COMPRESSED_MON_SIZE = 0x3A    -- CFRU CompressedPokemon (RR boxes)
R.SECURE_OFFSET = 0x20          -- BoxPokemon.secure
R.SUBSTRUCT_SIZE = 12           -- NUM_SUBSTRUCT_BYTES
R.NICKNAME_LEN, R.OT_NAME_LEN = 10, 7
R.NAME_EOS = 0xFF               -- charmap.txt: '$' = FF
R.PARTY_CAPACITY = 6            -- PARTY_SIZE
R.MONS_PER_BOX = 30             -- IN_BOX_COUNT

-- P4 card C4-2a: engine-fixed struct geometry, shared by every title (vanilla and CFRU/RR
-- alike -- archive/gen3-old-client:lua/memory_gba.lua's production isInBattle() uses this same HP offset on both).
-- include/pokemon.h:170-206 (pret/pokefirered@c75f3523) struct BattlePokemon.
R.BATTLE_MON_SIZE = 0x58        -- sizeof(struct BattlePokemon)
R.BATTLE_MON_HP_OFF = 0x28      -- BattlePokemon.hp (u16); .maxHP is HP_OFF + 4
-- include/global.h:400-404 struct ItemSlot { u16 itemId; u16 quantity; }.
R.ITEM_SLOT_SIZE = 4

-- Vanilla FR/LG relocate their save blocks on every load: SetSaveBlocksPointers
-- (pret/pokefirered src/load_save.c:68-78 at the pinned commit c75f3523) computes
--     offset = Random() & ((SAVEBLOCK_MOVE_RANGE - 1) & ~3)     -- :75
-- with SAVEBLOCK_MOVE_RANGE 128 (:15), i.e. a 4-byte-aligned offset in [0, 124], and adds it
-- to gSaveBlock2Ptr, gSaveBlock1Ptr AND gPokemonStoragePtr alike. The DMA pads that make the
-- slack are declared right there (:29, :32, :35). So a static base is never a live address:
-- the pointer is dereferenced on every read and the base is only a SANITY BOUND.
R.SAVEBLOCK_MOVE_RANGE = 128

-- src/pokemon.c GetSubstruct: entry [personality % 24] gives the POSITION of the
-- Growth / Attacks / EVs / Misc substruct inside the 48-byte secure block (0-based).
R.SUBSTRUCT_ORDER = {
    [0] = {0,1,2,3}, {0,1,3,2}, {0,2,1,3}, {0,3,1,2}, {0,2,3,1}, {0,3,2,1},
    {1,0,2,3}, {1,0,3,2}, {2,0,1,3}, {3,0,1,2}, {2,0,3,1}, {3,0,2,1},
    {1,2,0,3}, {1,3,0,2}, {2,1,0,3}, {3,1,0,2}, {2,3,0,1}, {3,2,0,1},
    {1,2,3,0}, {1,3,2,0}, {2,1,3,0}, {3,1,2,0}, {2,3,1,0}, {3,2,1,0},
}
-- CFRU (and therefore Radical Red) stores the four substructs in fixed order and does not
-- encrypt them: profile.derived.CFRU_NO_ENCRYPT.
R.FIXED_ORDER = {0, 1, 2, 3}

-- ── the one glyph table ──────────────────────────────────────────────────────────────
-- English FR font, pret charmap.txt at the pinned commit. Letters and digits are
-- contiguous runs ('A'=BB, 'a'=D5, '0'=A1) so they are built, not typed; everything else is
-- transcribed below. An unmapped byte decodes to a reversible <$XX> token.
local EXTRA = {
    [0x00] = " ",
    [0x01] = "À", [0x02] = "Á", [0x03] = "Â", [0x04] = "Ç", [0x05] = "È", [0x06] = "É",
    [0x07] = "Ê", [0x08] = "Ë", [0x09] = "Ì", [0x0B] = "Î", [0x0C] = "Ï", [0x0D] = "Ò",
    [0x0E] = "Ó", [0x0F] = "Ô", [0x10] = "Œ", [0x11] = "Ù", [0x12] = "Ú", [0x13] = "Û",
    [0x14] = "Ñ", [0x15] = "ß", [0x16] = "à", [0x17] = "á", [0x19] = "ç", [0x1A] = "è",
    [0x1B] = "é", [0x1C] = "ê", [0x1D] = "ë", [0x1E] = "ì", [0x20] = "î", [0x21] = "ï",
    [0x22] = "ò", [0x23] = "ó", [0x24] = "ô", [0x25] = "œ", [0x26] = "ù", [0x27] = "ú",
    [0x28] = "û", [0x29] = "ñ", [0x2A] = "º", [0x2B] = "ª",
    [0x2D] = "&", [0x2E] = "+", [0x35] = "=", [0x36] = ";", [0x51] = "¿", [0x52] = "¡",
    [0x5A] = "Í", [0x5B] = "%", [0x5C] = "(", [0x5D] = ")",
    [0x68] = "â", [0x6F] = "í", [0x85] = "<", [0x86] = ">",
    [0xAB] = "!", [0xAC] = "?", [0xAD] = ".", [0xAE] = "-", [0xAF] = "·", [0xB0] = "…",
    [0xB1] = "“", [0xB2] = "”", [0xB3] = "‘", [0xB4] = "’", [0xB5] = "♂", [0xB6] = "♀",
    [0xB7] = "¥", [0xB8] = ",", [0xB9] = "×", [0xBA] = "/",
    [0xEF] = "▶", [0xF0] = ":", [0xF1] = "Ä", [0xF2] = "Ö", [0xF3] = "Ü", [0xF4] = "ä",
    [0xF5] = "ö", [0xF6] = "ü",
}

-- {terminator, glyphs[0..255], codes[glyph] = byte}. Built once; a pack that ships its own
-- charmap.lua supplies profile.charmap and that object is used instead.
function R.charmap(profile)
    local cm = profile and profile.charmap
    if not cm then
        if not R.FR then
            local glyphs = {}
            for b = 0, 255 do
                if EXTRA[b] then glyphs[b] = EXTRA[b]
                elseif b >= 0xBB and b <= 0xD4 then glyphs[b] = string.char(65 + b - 0xBB)
                elseif b >= 0xD5 and b <= 0xEE then glyphs[b] = string.char(97 + b - 0xD5)
                elseif b >= 0xA1 and b <= 0xAA then glyphs[b] = string.char(48 + b - 0xA1)
                else glyphs[b] = string.format("<$%02X>", b) end
            end
            R.FR = { terminator = R.NAME_EOS, glyphs = glyphs }
        end
        cm = R.FR
    end
    assert(type(cm.glyphs) == "table" and type(cm.terminator) == "number",
           "charmap needs glyphs + terminator")
    if not cm.codes then
        local codes = {}
        for b = 0, 255 do
            local g = cm.glyphs[b]
            if g and codes[g] == nil then codes[g] = b end
        end
        cm.codes = codes
    end
    return cm
end

-- ── byte helpers (1-based arrays, 0-based struct offsets) ────────────────────────────
local function u(b, off, size)
    local value, mul = 0, 1
    for i = 0, size - 1 do
        local byte = b[off + i + 1]
        if type(byte) ~= "number" then return nil end
        value = value + byte * mul
        mul = mul * 256
    end
    return value
end
local function take(b, off, len)
    local out = {}
    for i = 1, len do out[i] = b[off + i] end
    return out
end
-- src/pokemon.c EncryptBoxMon/DecryptBoxMon: XOR every 32-bit word with personality ^ otId.
local function xor_words(bytes, key)
    local out = {}
    for i = 0, #bytes - 1, 4 do
        local word = u(bytes, i, 4) ~ key
        for j = 0, 3 do out[i + j + 1] = (word >> (8 * j)) & 0xFF end
    end
    return out
end
-- src/pokemon.c CalculateBoxMonChecksum: u16 sum of the 24 decrypted halfwords.
function R.secure_checksum(plain)
    local total = 0
    for i = 0, #plain - 1, 2 do total = total + u(plain, i, 2) end
    return total & 0xFFFF
end

-- CFRU CompressedPokemon (0x3A) -> BoxPokemon (0x50). Fields CFRU does not store
-- (checksum, PP, contest) stay zero. RR's builder sets the high ribbons byte bit7.
function R.expand_compressed_mon(raw)
    if #raw ~= R.COMPRESSED_MON_SIZE then
        return nil, "compressed record length disagrees with profile"
    end
    local out = {}
    for i = 1, R.BOX_MON_SIZE do out[i] = 0 end
    local function copy(dst, src, len)
        for i = 1, len do out[dst + i] = raw[src + i] end
    end
    copy(0x00, 0x00, 0x1C)      -- header verbatim
    copy(0x20, 0x1C, 0x0B)      -- Growth
    local packed = u(raw, 0x27, 5)
    for i = 0, 3 do             -- four 10-bit move ids -> u16 each
        local move = (packed >> (10 * i)) & 0x3FF
        out[0x2C + i * 2 + 1] = move & 0xFF
        out[0x2C + i * 2 + 2] = (move >> 8) & 0xFF
    end
    copy(0x38, 0x2C, 6)         -- EVs
    copy(0x44, 0x32, 8)         -- Misc head
    out[0x4F + 1] = 0x80       -- RR ROM 0x090B696A..76: OR bit7 at BoxPokemon+0x4F
    return out
end

-- BizHawk's API members are `userdata`, not `function` (and so are lupa's Python stubs), so
-- callability is checked by type class, never by `type(f) == "function"`.
function R.callable(v)
    local t = type(v)
    return t == "function" or t == "userdata" or t == "table"
end

-- `pointers` is the pack's write_checkpoint `pointers` block (Entry.build passes
-- parts.write_checkpoint.pointers), so the pointer symbols arrive as data like every other
-- address. When it is absent the equivalent profile.ram symbol is used instead.
-- XR-2 (review cx-fcdc11c5 F7/F9): validate an optional expansion bitfield/mask
-- block ONCE, at R.new, rather than crashing mid-decode (`nil >> shift`) or
-- silently honouring a malformed layout. A key that is absent (Lua nil) is
-- always fine; a key that IS present must be well-formed or R.new refuses by
-- name. The Python twin is validate_expansion_layout (gen3_codec.py).
local function bitfield(blob, field)
    return (u(blob, field.word_off, field.word_size) >> field.shift) & ((1 << field.width) - 1)
end

local function validate_mask(label, value)
    if type(value) == "number" then
        assert(value > 0, label .. ": mask must be a positive number (got " .. tostring(value) .. ")")
    end
end

local function validate_bitfield(label, field, container_size)
    assert(type(field) == "table", label .. ": must be an object")
    for _, key in ipairs({ "word_off", "word_size", "shift", "width" }) do
        assert(type(field[key]) == "number", label .. ": missing or non-numeric " .. key)
    end
    assert(field.word_size == 1 or field.word_size == 2 or field.word_size == 4,
           label .. ": word_size must be 1, 2 or 4")
    assert(field.word_off >= 0 and field.word_off + field.word_size <= container_size,
           label .. ": word_off/word_size exceeds the " .. container_size .. "-byte container")
    assert(field.width >= 1 and field.shift >= 0 and field.shift + field.width <= field.word_size * 8,
           label .. ": shift+width exceeds the " .. (field.word_size * 8) .. "-bit word")
end

local function validate_fixed_bitfield(label, field, total_bits)
    assert(type(field) == "table", label .. ": must be an object")
    for _, key in ipairs({ "shift", "width" }) do
        assert(type(field[key]) == "number", label .. ": missing or non-numeric " .. key)
    end
    assert(field.width >= 1 and field.shift >= 0 and field.shift + field.width <= total_bits,
           label .. ": shift+width exceeds the " .. total_bits .. "-bit field")
end

local MASK_KEYS = { "MON_SPECIES_MASK", "MON_ITEM_MASK", "MON_MOVE_MASK",
                    "EXPERIENCE_MASK", "PP_MASK", "MARKINGS_MASK" }

local function validate_layout(d)
    for _, key in ipairs(MASK_KEYS) do validate_mask(key, d[key]) end
    if type(d.NICKNAME_EXTRA) == "table" then
        assert(type(d.NICKNAME_EXTRA.chars) == "table",
               "NICKNAME_EXTRA: must be an object with a 'chars' list")
        for i, char in ipairs(d.NICKNAME_EXTRA.chars) do
            validate_bitfield("NICKNAME_EXTRA.chars[" .. i .. "]", char, R.SUBSTRUCT_SIZE)
        end
    end
    if type(d.POKEBALL_FIELD) == "table" then
        validate_bitfield("POKEBALL_FIELD", d.POKEBALL_FIELD, R.SUBSTRUCT_SIZE)
    end
    if type(d.ABILITY_NUM_FIELD) == "table" then
        validate_bitfield("ABILITY_NUM_FIELD", d.ABILITY_NUM_FIELD, R.SUBSTRUCT_SIZE)
    end
    if type(d.SHINY_MODIFIER_FIELD) == "table" then
        validate_fixed_bitfield("SHINY_MODIFIER_FIELD", d.SHINY_MODIFIER_FIELD, 16)
    end
end

function R.new(profile, io, pointers)
    assert(type(profile) == "table" and type(profile.ram) == "table"
           and type(profile.derived) == "table", "Gen 3 title profile required")
    assert(type(io) == "table" and R.callable(io.read_u8) and R.callable(io.read_u32)
           and R.callable(io.read_bytes), "injected read-only byte reader required")
    local a, d = profile.ram, profile.derived
    validate_layout(d)
    local cm = R.charmap(profile)
    -- Radical Red / CFRU: fixed substruct order, no XOR, no BoxPokemon checksum.
    local rr = d.CFRU_NO_ENCRYPT == true
    local party_capacity = d.PARTY_CAPACITY or R.PARTY_CAPACITY
    local mons_per_box = d.MONS_PER_BOX or R.MONS_PER_BOX
    local r = { charmap = cm, rr = rr, party_capacity = party_capacity,
                mons_per_box = mons_per_box }
    -- X2: gBattleMons geometry from the pack when it proves its own (the expansion reference
    -- build's 140-byte BattlePokemon, hp at +42: its compiler facts); vanilla keeps pret's.
    r.BATTLE_MON_SIZE = d.BATTLE_MON_SIZE or R.BATTLE_MON_SIZE
    r.BATTLE_MON_HP_OFF = d.BATTLE_MON_HP_OFF or R.BATTLE_MON_HP_OFF
    r.expand_compressed_mon = R.expand_compressed_mon
    r.PARTY_MON_SIZE = R.PARTY_MON_SIZE
    r.BOX_MON_SIZE = R.BOX_MON_SIZE

    function r.decode_name(bytes)
        local out = {}
        for i = 1, #bytes do
            if bytes[i] == cm.terminator then break end
            out[#out + 1] = cm.glyphs[bytes[i]] or string.format("<$%02X>", bytes[i])
        end
        return table.concat(out)
    end

    -- XR-1 (pokeemerald-expansion, include/pokemon.h at expansion/1.17.0
    -- e8bd1cd7b03fc032ea37e3ecd38b379b5d01a1e7): species/heldItem/move1-4 are
    -- masked bitfields sharing their vanilla u16 lane with new bits (teraType,
    -- evolutionTracker, hyperTrained); a pack pins the surviving-bits mask so
    -- vanilla packs (no derived.MON_*_MASK) decode byte-for-byte as before.
    -- All of these are computed ONCE here (not per record) and already
    -- validate_layout()-checked above (XR-2 F2/F7).
    local species_mask = d.MON_SPECIES_MASK or 0xFFFF
    local item_mask = d.MON_ITEM_MASK or 0xFFFF
    local move_mask = d.MON_MOVE_MASK or 0xFFFF
    -- First-class key (XR-2 F2): experience:21 shares its u32 lane with the
    -- new nickname11:8 (include/pokemon.h#L138-140) -- no longer inferred by
    -- scanning NICKNAME_EXTRA.chars for word_off == 4.
    local experience_mask = d.EXPERIENCE_MASK or 0xFFFFFFFF
    -- Each PP byte's top bit is a hyperTrained* flag (#L160-167).
    local pp_mask = d.PP_MASK or 0xFF
    -- {word_off, word_size, shift, width} relative to the Growth substruct:
    -- pokeball moved there (pokeball:6, #L143); absent = vanilla (Misc met u16).
    local pokeball_field = type(d.POKEBALL_FIELD) == "table" and d.POKEBALL_FIELD or nil
    -- {word_off, word_size, shift, width} relative to the Misc substruct:
    -- abilityNum moved into the ribbons u32 (abilityNum:2, #L221); absent =
    -- vanilla (bit31 of the IVs u32).
    local ability_field = type(d.ABILITY_NUM_FIELD) == "table" and d.ABILITY_NUM_FIELD or nil
    -- markings:4 shares its byte with compressedStatus:4 (#L272-273).
    local markings_mask = d.MARKINGS_MASK or 0xFF
    -- {shift, width} relative to BoxPokemon.unknown (hpLost:14 + shinyModifier:1
    -- + unused_1E:1, #L275-276); absent = not decoded (server's shiny rule is
    -- key-based, docs/gen3_emerald/research/expansion_record_layout.md).
    local shiny_field = type(d.SHINY_MODIFIER_FIELD) == "table" and d.SHINY_MODIFIER_FIELD or nil
    -- Chars validated once above; hoisted so decode_mon does not re-check per record.
    local nickname_extra = type(d.NICKNAME_EXTRA) == "table" and d.NICKNAME_EXTRA or nil

    -- include/pokemon.h: the four 12-byte substructs, already put back in
    -- Growth / Attacks / EVs / Misc order.
    local function decode_secure(g, at, e, m)
        local ivs, met = u(m, 4, 4), u(m, 2, 2)
        local moves, pp, contest = {}, {}, {}
        for i = 0, 3 do moves[i + 1] = u(at, i * 2, 2) & move_mask end
        for i = 0, 3 do pp[i + 1] = at[9 + i] & pp_mask end
        for i = 0, 5 do contest[i + 1] = e[7 + i] end
        local pokeball = pokeball_field and bitfield(g, pokeball_field) or (met >> 11) & 0x0F
        local ability_num = ability_field and bitfield(m, ability_field) or (ivs >> 31) & 1
        return {
            species = u(g, 0, 2) & species_mask, held_item = u(g, 2, 2) & item_mask,
            experience = u(g, 4, 4) & experience_mask,
            pp_bonuses = g[9], friendship = g[10], growth_filler = u(g, 10, 2),
            moves = moves, pp = pp,
            evs = { hp = e[1], attack = e[2], defense = e[3], speed = e[4],
                    sp_attack = e[5], sp_defense = e[6] },
            contest = contest,
            pokerus = m[1], met_location = m[2],
            met_level = met & 0x7F, met_game = (met >> 7) & 0x0F,
            pokeball = pokeball, ot_gender = (met >> 15) & 1,
            ivs = { hp = ivs & 0x1F, attack = (ivs >> 5) & 0x1F,
                    defense = (ivs >> 10) & 0x1F, speed = (ivs >> 15) & 0x1F,
                    sp_attack = (ivs >> 20) & 0x1F, sp_defense = (ivs >> 25) & 0x1F },
            is_egg = (ivs >> 30) & 1, ability_num = ability_num,
            ribbons = u(m, 8, 4),
        }
    end

    -- include/pokemon.h: the party-only tail after the 80-byte BoxPokemon.
    local PARTY_TAIL = { { "status", 0x50, 4 }, { "level", 0x54, 1 }, { "mail", 0x55, 1 },
                         { "hp", 0x56, 2 }, { "max_hp", 0x58, 2 }, { "attack", 0x5A, 2 },
                         { "defense", 0x5C, 2 }, { "speed", 0x5E, 2 },
                         { "sp_attack", 0x60, 2 }, { "sp_defense", 0x62, 2 } }

    local function decode_mon(raw, party)
        local want = party and R.PARTY_MON_SIZE or R.BOX_MON_SIZE
        if #raw ~= want then return nil, "record length disagrees with profile" end
        local personality, ot_id = u(raw, 0x00, 4), u(raw, 0x04, 4)
        if not personality or not ot_id then return nil, "record is not a byte array" end
        local secure = take(raw, R.SECURE_OFFSET, 4 * R.SUBSTRUCT_SIZE)
        local plain = rr and secure or xor_words(secure, personality ~ ot_id)
        local order = rr and R.FIXED_ORDER or R.SUBSTRUCT_ORDER[personality % 24]
        local ordered = {}
        for kind = 1, 4 do
            ordered[kind] = take(plain, order[kind] * R.SUBSTRUCT_SIZE, R.SUBSTRUCT_SIZE)
        end
        local flags, stored = raw[0x13 + 1], u(raw, 0x1C, 2)
        local nick = take(raw, 0x08, R.NICKNAME_LEN)
        -- XR-1: chars 11/12 live in Growth-substruct bitfields (nickname11 at the
        -- experience u32's bits 21-28, nickname12 at the pokeball u16's bits 6-13),
        -- not in the raw 10-byte BoxPokemon.nickname array. pret's own
        -- GetBoxMonData3 (src/pokemon.c) treats "both zero" as a vanilla (10-char)
        -- record and omits them; only append when at least one is non-zero.
        if nickname_extra then
            local growth = ordered[1]
            local chars, any_nonzero = {}, false
            for _, char in ipairs(nickname_extra.chars) do
                local value = bitfield(growth, char)
                chars[#chars + 1] = value
                if value ~= 0 then any_nonzero = true end
            end
            if any_nonzero then
                for _, value in ipairs(chars) do nick[#nick + 1] = value end
            end
        end
        local ot = take(raw, 0x14, R.OT_NAME_LEN)
        local unknown = u(raw, 0x1E, 2)
        local mon = {
            personality = personality, ot_id = ot_id,
            nickname = r.decode_name(nick), nickname_bytes = nick,
            language = raw[0x12 + 1],
            is_bad_egg = flags & 1, has_species = (flags >> 1) & 1,
            is_egg_flag = (flags >> 2) & 1, block_box_rs = (flags >> 3) & 1,
            flags_unused = (flags >> 4) & 0x0F,
            ot_name = r.decode_name(ot), ot_name_bytes = ot,
            markings = raw[0x1B + 1] & markings_mask, checksum = stored, unknown = unknown,
            box = not party,
        }
        if shiny_field then
            mon.shiny_modifier = (unknown >> shiny_field.shift) & ((1 << shiny_field.width) - 1)
        end
        for key, value in pairs(decode_secure(ordered[1], ordered[2], ordered[3], ordered[4])) do
            mon[key] = value
        end
        -- CFRU never validates the BoxPokemon checksum and leaves the field zero, so in RR
        -- mode there is nothing to check: the key is absent, not false.
        if not rr then mon.checksum_ok = R.secure_checksum(plain) == stored end
        if party then
            for _, field in ipairs(PARTY_TAIL) do
                mon[field[1]] = u(raw, field[2], field[3])
            end
        end
        return mon
    end
    function r.decode_party_mon(raw) return decode_mon(raw, true) end
    function r.decode_box_mon(raw) return decode_mon(raw, false) end

    -- docs/protocol.md mon key, as the shipped Gen 3 client spells it
    -- (archive/gen3-old-client:lua/memory_gba.lua:651): PERSONALITY:OTID, upper-case hex, eight digits each.
    function r.key(mon) return string.format("%08X:%08X", mon.personality, mon.ot_id) end

    -- SaveBlock / storage pointers. SetSaveBlocksPointers relocates all three on every load,
    -- so each is dereferenced on the call and never cached across frames.
    --   symbol    the write_checkpoint pointers key (gSaveBlock1Ptr, ...)
    --   ram_key   the equivalent profile.ram symbol, used when the pack ships no pointers
    --   base      optional static base for the cross-check; nil = null + alignment only
    local move_range = d.SAVEBLOCK_MOVE_RANGE or R.SAVEBLOCK_MOVE_RANGE
    local max_offset = move_range - 4        -- Random() & ((RANGE - 1) & ~3)
    local function pointer_address(symbol, ram_key)
        local entry = pointers and pointers[symbol]
        if entry and entry.address then return entry.address end
        return a[ram_key]
    end
    local function deref(symbol, ram_key, base)
        local addr = pointer_address(symbol, ram_key)
        if not addr then
            return nil, "neither write_checkpoint.pointers." .. symbol
                        .. " nor profile.ram." .. ram_key .. " names the pointer"
        end
        local ptr = io.read_u32(addr)
        if not ptr or ptr == 0 then return nil, symbol .. " is null" end
        if ptr % 4 ~= 0 then return nil, symbol .. " is not word aligned" end
        if base then
            local offset = ptr - base
            if offset < 0 or offset > max_offset then
                return nil, symbol .. " is outside the relocation window of its base"
            end
        end
        return ptr
    end
    r.deref = deref
    -- †The pack names no static base for SaveBlock1/2, so these get the null + alignment
    -- check only; add SB1_BASE/SB2_BASE to profile.ram to bound them the way storage is.
    function r.read_sb1() return deref("gSaveBlock1Ptr", "SB1_PTR_ADDR", a.SB1_BASE) end
    function r.read_sb2() return deref("gSaveBlock2Ptr", "SB2_PTR_ADDR", a.SB2_BASE) end
    function r.read_storage()
        return deref("gPokemonStoragePtr", "PSP_PTR_ADDR", a.POKEMON_STORAGE_BASE)
    end

    local function party_base()
        if d.PARTY_IN_SB1 then
            local sb1, why = r.read_sb1()
            if not sb1 then return nil, why end
            local off = d.SB1_PARTY_BASE_OFFSET
            if not off then return nil, "profile has no derived.SB1_PARTY_BASE_OFFSET" end
            return sb1 + off
        end
        if not a.PARTY_BASE then return nil, "profile has no ram.PARTY_BASE" end
        return a.PARTY_BASE
    end
    r.party_base = party_base

    -- occupied: every slot with a species, not 0..count-1 -- deliberately NOT
    -- CalculatePlayerPartyCount's rule, which counts only the contiguous prefix up to the first
    -- SPECIES_NONE (pret pokemon.c:3742-3752) and so would miss a slot past a gap.
    -- Inside the PC gPlayerPartyCount is stale: the storage system recounts only on box exit
    -- (pret pokemon_storage_system_tasks.c Task_OnBPressed/Task_OnCloseBoxPressed state 4), so
    -- a withdrawn mon sits at slot == count and a deposited slot is a zeroed record under it.
    function r.read_party(occupied)
        if not a.PARTY_COUNT_ADDR then return nil, "profile has no ram.PARTY_COUNT_ADDR" end
        local count = io.read_u8(a.PARTY_COUNT_ADDR)
        if count > party_capacity then return nil, "party count exceeds capacity" end
        local base, why = party_base()
        if not base then return nil, why end
        local out = {}
        for slot = 0, (occupied and party_capacity or count) - 1 do
            local mon, bad = r.decode_party_mon(
                io.read_bytes(base + slot * R.PARTY_MON_SIZE, R.PARTY_MON_SIZE))
            if not mon then return nil, bad end
            if not occupied or mon.species ~= 0 then
                mon.slot = slot
                out[#out + 1] = mon
            end
        end
        return out
    end

    -- One PC box, by zero-based index. RR keeps 25 boxes of 30 CFRU-compressed records at
    -- the scattered EWRAM bases in derived.CFRU_BOX_BASES; vanilla FRLG keeps 14 boxes of
    -- 80-byte records inside gPokemonStorage, which SetSaveBlocksPointers RELOCATES, so its
    -- live address is gPokemonStoragePtr and ram.POKEMON_STORAGE_BASE is only the bound.
    function r.read_box(index)
        if type(index) ~= "number" or index ~= math.floor(index) or index < 0 then
            return nil, "box index must be a non-negative integer"
        end
        local base, stride, compressed
        if d.CFRU_COMPRESSED_BOX then
            local bases = d.CFRU_BOX_BASES
            if type(bases) ~= "table" then
                return nil, "profile has no derived.CFRU_BOX_BASES"
            end
            base = bases[index + 1]
            if not base then return nil, "box index outside profile" end
            stride, compressed = d.COMPRESSED_MON_SIZE or R.COMPRESSED_MON_SIZE, true
        else
            if not d.BOX_DATA_OFFSET then
                return nil, "profile has no derived.BOX_DATA_OFFSET"
            end
            if index >= (d.BOXES_PER_STORE or 0) then return nil, "box index outside profile" end
            local storage, why = r.read_storage()
            if not storage then return nil, why end
            base = storage + d.BOX_DATA_OFFSET + index * mons_per_box * R.BOX_MON_SIZE
            stride, compressed = R.BOX_MON_SIZE, false
        end
        local raw = io.read_bytes(base, mons_per_box * stride)
        local out = {}
        for slot = 0, mons_per_box - 1 do
            local record = take(raw, slot * stride, stride)
            if compressed then
                local expanded, why = R.expand_compressed_mon(record)
                if not expanded then return nil, why end
                record = expanded
            end
            local mon, bad = r.decode_box_mon(record)
            if not mon then return nil, bad end
            mon.slot, mon.box_index = slot, index
            out[#out + 1] = mon
        end
        return out
    end

    -- ── P4 card C4-2a: trainer / location / badges / bag / battle ────────────────────
    -- Every field here returns nil, reason when the pack has not pinned the address or
    -- offset it needs (so the gen3_rr pack, which does not carry most of these yet, keeps
    -- working exactly as it did before this card until its own evidence lands).

    -- SaveBlock2.playerTrainerId (u32 LE) + playerName (pret include/global.h:327-332;
    -- src/pokemon.c:1798-1801 for the little-endian assembly of the OT id).
    function r.read_trainer()
        local ot_off, name_off = d.SB2_OT_ID_OFFSET, d.SB2_NAME_OFFSET
        if not ot_off or not name_off then
            return nil, "profile has no derived.SB2_OT_ID_OFFSET/SB2_NAME_OFFSET"
        end
        local sb2, why = r.read_sb2()
        if not sb2 then return nil, why end
        local name = io.read_bytes(sb2 + name_off, R.OT_NAME_LEN)
        local out = { ot_id = io.read_u32(sb2 + ot_off), name = r.decode_name(name), name_bytes = name }
        -- Optional title fact. Emerald's generator pins SaveBlock2.playerGender;
        -- other packs make no claim. Unreadable/unknown values stay absent.
        if type(d.SB2_PLAYER_GENDER_OFFSET) == "number" then
            local gender = io.read_u8(sb2 + d.SB2_PLAYER_GENDER_OFFSET)
            if gender == 0 or gender == 1 then out.player_gender = gender end
        end
        return out
    end

    -- SaveBlock1.location (struct WarpData, pret include/global.h:392-398,759-762): signed
    -- mapGroup/mapNum bytes.
    function r.read_location()
        local group_off, num_off = d.SB1_LOCATION_MAP_GROUP_OFFSET, d.SB1_LOCATION_MAP_NUM_OFFSET
        if not group_off or not num_off then
            return nil, "profile has no derived.SB1_LOCATION_MAP_GROUP_OFFSET/MAP_NUM_OFFSET"
        end
        local sb1, why = r.read_sb1()
        if not sb1 then return nil, why end
        local function s8(v) return v >= 128 and v - 256 or v end
        return { map_group = s8(io.read_u8(sb1 + group_off)), map_num = s8(io.read_u8(sb1 + num_off)) }
    end

    -- SaveBlock1.flags[]: the 8 FLAG_BADGE0x_GET bits sit consecutively inside one byte
    -- (pret include/constants/flags.h:1324,1364-1371) -- bit i (0-based) is badge i+1.
    --
    -- Emerald's badges (FLAG_BADGE01_GET = SYSTEM_FLAGS+7 = 0x867, flags.h:1359) straddle a
    -- byte instead (0x867..0x86E = byte 0x10C bit 7, then byte 0x10D bits 0-6), so a pack that
    -- cannot say it with one byte carries profile.derived.BADGE_FIRST_FLAG (the flag id) and
    -- leaves SB1_BADGE_BYTE_OFFSET null; each bit is then read from its own flags byte. The
    -- Python twin is tools/gen3_reads_pydec.py's decode_badges_straddle.
    function r.read_badges()
        -- json_codec.lua decodes a JSON null to the sentinel M.null = {} (json_codec.lua:4,174),
        -- not Lua nil, and the real Emerald profile carries SB1_BADGE_BYTE_OFFSET: null. A plain
        -- `~= nil` check treats that sentinel table as "set" and misfires the refusal below on
        -- every real Emerald load; only a number means the field is actually set (F-A).
        local first_flag_set = type(d.BADGE_FIRST_FLAG) == "number"
        local byte_offset_set = type(d.SB1_BADGE_BYTE_OFFSET) == "number"
        if first_flag_set then
            -- A profile that carries both fields is self-contradictory (one says "straddled,
            -- read per-bit", the other says "shares one byte"): refuse by name rather than
            -- picking a side (F10).
            if byte_offset_set then
                return nil, "profile derived sets both BADGE_FIRST_FLAG and SB1_BADGE_BYTE_OFFSET"
            end
            if not d.SB1_FLAGS_OFFSET then
                return nil, "profile has no derived.SB1_FLAGS_OFFSET"
            end
            local sb1, why = r.read_sb1()
            if not sb1 then return nil, why end
            local out = 0
            for i = 0, 7 do
                local bit_index = d.BADGE_FIRST_FLAG + i
                local byte = io.read_u8(sb1 + d.SB1_FLAGS_OFFSET + (bit_index >> 3))
                if (byte >> (bit_index & 7)) & 1 == 1 then out = out | (1 << i) end
            end
            return out
        end
        if not d.SB1_FLAGS_OFFSET or not byte_offset_set then
            return nil, "profile has no derived.SB1_FLAGS_OFFSET/SB1_BADGE_BYTE_OFFSET"
        end
        local sb1, why = r.read_sb1()
        if not sb1 then return nil, why end
        return io.read_u8(sb1 + d.SB1_FLAGS_OFFSET + d.SB1_BADGE_BYTE_OFFSET)
    end

    -- Poke Ball pocket: ItemSlot{u16 itemId, u16 quantity} (pret include/global.h:400-404).
    -- Vanilla FR/LG keep the pocket inside SaveBlock1 and XOR every quantity with the low 16
    -- bits of SaveBlock2.encryptionKey (src/item.c GetBagItemQuantity: `encryptionKey ^ *ptr`,
    -- truncated to u16 by the return type). CFRU/RR relocate the pocket to a fixed EWRAM
    -- address and leave quantities unencrypted (profile.derived.BAG_IN_EWRAM / BALL_POCKET_ENC).
    function r.read_balls()
        local count = d.SB1_BALL_POCKET_COUNT
        if not count then return nil, "profile has no derived.SB1_BALL_POCKET_COUNT" end
        local base
        if d.BAG_IN_EWRAM then
            if not a.BALL_POCKET_ADDR then return nil, "profile has no ram.BALL_POCKET_ADDR" end
            base = a.BALL_POCKET_ADDR
        else
            if not d.SB1_BALL_POCKET_OFFSET then
                return nil, "profile has no derived.SB1_BALL_POCKET_OFFSET"
            end
            local sb1, why = r.read_sb1()
            if not sb1 then return nil, why end
            base = sb1 + d.SB1_BALL_POCKET_OFFSET
        end
        local encrypted = d.BALL_POCKET_ENC ~= false
        local key = 0
        if encrypted then
            if not d.SB2_ENC_KEY_OFFSET then return nil, "profile has no derived.SB2_ENC_KEY_OFFSET" end
            local sb2, why = r.read_sb2()
            if not sb2 then return nil, why end
            key = io.read_u32(sb2 + d.SB2_ENC_KEY_OFFSET) & 0xFFFF
        end
        local total = 0
        for i = 0, count - 1 do
            local item = io.read_u16(base + i * R.ITEM_SLOT_SIZE)
            local qty = io.read_u16(base + i * R.ITEM_SLOT_SIZE + 2)
            if encrypted then qty = qty ~ key end
            if item ~= 0 then total = total + qty end
        end
        return { ball_count = total, has_pokeballs = total > 0 }
    end

    -- The enemy party, decoded exactly like r.read_party() (ram.ENEMY_BASE instead of the
    -- player's; the enemy array is never save-block-relocated).
    --
    -- Read by OCCUPANCY, never by ram.ENEMY_COUNT_ADDR (gEnemyPartyCount): the engine does not
    -- maintain that byte in battle. pret writes it only from CalculateEnemyPartyCount
    -- (pokefirered src/pokemon.c:3756-3767) and calls that only from trade.c:942,1139, so it
    -- stays at its boot 0 through a wild or a trainer battle and a count-bounded loop returns
    -- {} -- which is the item-15 violation the live link_gen3/boxsync goldens recorded.
    -- The array is filled BEFORE the battle flag rises (wild_encounter.c:391-397 GenerateWildMon
    -- -> StartWildBattle; battle_main.c:707 CreateNPCTrainerParty -> :711 gMain.inBattle = TRUE),
    -- so this scan is populated on the first in-battle tick. Terminate on species == 0 or
    -- max_hp == 0: the rule the old client used (archive/gen3-old-client:lua/memory_gba.lua:1545-1553) and the one the
    -- companion patch's staging preserves (patch/src/handlers.c:1813-1815 zeroes maxHP on the
    -- trailing slots). ZeroEnemyPartyMons clears all six slots first in both battle kinds.
    function r.read_enemy_party()
        if not a.ENEMY_BASE then return nil, "profile has no ram.ENEMY_BASE" end
        local out = {}
        for slot = 0, party_capacity - 1 do
            local mon, bad = r.decode_party_mon(
                io.read_bytes(a.ENEMY_BASE + slot * R.PARTY_MON_SIZE, R.PARTY_MON_SIZE))
            if not mon then return nil, bad end
            if mon.species == 0 or mon.max_hp == 0 then break end
            mon.slot = slot
            out[#out + 1] = mon
        end
        return out
    end

    -- The 7 wire stat stages {ATK, DEF, SPD, SATK, SDEF, ACC, EVA} (raw 0-12, 6 neutral) of one
    -- battler, from gBattleMons[battler] + derived.BATTLE_MON_STAT_STAGES_OFF. That offset is
    -- 0x19 on every title and never 0x18: +0x18 is the vanilla HP stage (pret
    -- include/pokemon.h:187) but CFRU's type3 byte on RR (test_stat_stages.py guards it).
    function r.read_stat_stages(battler)
        local off = d.BATTLE_MON_STAT_STAGES_OFF
        if type(a.BATTLE_MONS_ADDR) ~= "number" or not off then
            return nil, "profile has no ram.BATTLE_MONS_ADDR/derived.BATTLE_MON_STAT_STAGES_OFF"
        end
        return io.read_bytes(a.BATTLE_MONS_ADDR + battler * r.BATTLE_MON_SIZE + off, 7)
    end

    -- True only while gBattleMons[battler] IS `mon` (a decoded party/enemy record): battler <
    -- gBattlersCount and the same personality and otId (derived.BATTLE_MON_PERSONALITY_OFF /
    -- BATTLE_MON_OT_ID_OFF). gBattlerPartyIndexes alone is not enough: a switch writes the
    -- incoming index first and copies the mon into gBattleMons (resetting its stages) later
    -- (pret src/battle_script_commands.c:4452-4463 vs :4470-4503; the intro has the same order).
    function r.battler_holds(battler, mon)
        local pers, otid = d.BATTLE_MON_PERSONALITY_OFF, d.BATTLE_MON_OT_ID_OFF
        if type(a.BATTLE_MONS_ADDR) ~= "number" or type(a.BATTLERS_COUNT_ADDR) ~= "number"
           or not pers or not otid then
            return nil, "profile has no ram.BATTLE_MONS_ADDR/BATTLERS_COUNT_ADDR/"
                       .. "derived.BATTLE_MON_PERSONALITY_OFF/BATTLE_MON_OT_ID_OFF"
        end
        if type(mon) ~= "table" or battler >= io.read_u8(a.BATTLERS_COUNT_ADDR) then return false end
        local base = a.BATTLE_MONS_ADDR + battler * r.BATTLE_MON_SIZE
        return io.read_u32(base + pers) == mon.personality and io.read_u32(base + otid) == mon.ot_id
    end

    -- In-battle state, type flags, the trainer opponent id, the battler->party-slot mapping
    -- and the enemy party. Booleans whose mask/address the pack has not pinned come back
    -- absent rather than failing the whole read (gen3_rr today: is_trainer/is_doubles).
    function r.read_battle()
        if not (a.BATTLE_TYPE_ADDR and a.BATTLE_OUTCOME_ADDR and a.BATTLERS_COUNT_ADDR
                and a.BATTLER_PARTY_INDEXES_ADDR and a.ENEMY_COUNT_ADDR and a.ENEMY_BASE) then
            return nil, "profile has no ram.BATTLE_TYPE_ADDR/BATTLE_OUTCOME_ADDR/"
                       .. "BATTLERS_COUNT_ADDR/BATTLER_PARTY_INDEXES_ADDR/ENEMY_*"
        end
        local out = {}
        -- Two production-proven in-battle detectors (archive/gen3-old-client:lua/memory_gba.lua M.isInBattle),
        -- selected by the pack: vanilla reads a gMain bit, CFRU/RR has no reliable gMain so
        -- it reads the live battler-0 BattlePokemon instead.
        -- profile.json's JSON `null` (e.g. RR's ram.GMAIN_ADDR) decodes to a truthy sentinel
        -- table, not Lua nil (lua/json_codec.lua), so "is this address present" is a type
        -- check, never a plain truthiness check.
        if d.OVERWORLD_MODE == "battle_outcome" and type(a.BATTLE_MONS_ADDR) == "number" then
            local max_hp = io.read_u16(a.BATTLE_MONS_ADDR + r.BATTLE_MON_HP_OFF + 4)
            out.in_battle = max_hp > 0 and io.read_u8(a.BATTLE_OUTCOME_ADDR) == 0
        elseif type(a.GMAIN_ADDR) == "number" and d.GMAIN_INBATTLE_OFFSET and d.GMAIN_INBATTLE_MASK then
            out.in_battle = (io.read_u8(a.GMAIN_ADDR + d.GMAIN_INBATTLE_OFFSET) & d.GMAIN_INBATTLE_MASK) ~= 0
        end
        out.outcome = io.read_u8(a.BATTLE_OUTCOME_ADDR)
        local type_flags = io.read_u32(a.BATTLE_TYPE_ADDR)
        if d.BATTLE_TYPE_TRAINER_MASK then out.is_trainer = (type_flags & d.BATTLE_TYPE_TRAINER_MASK) ~= 0 end
        if d.BATTLE_TYPE_DOUBLE_MASK then out.is_doubles = (type_flags & d.BATTLE_TYPE_DOUBLE_MASK) ~= 0 end
        if d.BATTLE_TYPE_LINK_MASK then out.is_link = (type_flags & d.BATTLE_TYPE_LINK_MASK) ~= 0 end
        if type(a.TRAINER_OPPONENT_ADDR) == "number" then
            out.trainer_id = io.read_u16(a.TRAINER_OPPONENT_ADDR)
        end
        local count = io.read_u8(a.BATTLERS_COUNT_ADDR)
        local battlers = {}
        for i = 0, count - 1 do
            battlers[i + 1] = io.read_u16(a.BATTLER_PARTY_INDEXES_ADDR + i * 2)
        end
        out.battlers_count, out.battler_party_indexes = count, battlers
        -- Player battlers are position 0 (single) and additionally 2 (doubles): outside link
        -- battles the battler id equals the position id (pret include/constants/battle.h:10-19).
        out.active_player_battler_slots = { battlers[1] }
        if count >= 4 then out.active_player_battler_slots[2] = battlers[3] end
        local enemy, why = r.read_enemy_party()
        if not enemy then return nil, why end
        out.enemy_party = enemy
        return out
    end

    if type(profile.abilities) == "table" then
        local spec, anchors_ok = profile.abilities, nil
        local function rom_range(address, size)
            return type(address) == "number" and address >= spec.rom_start
                and address + size <= spec.rom_end
        end

        function r.read_abilities(mon)
            if spec.mode ~= "rr_4_1" then return nil, "unsupported ability reader" end
            if type(mon) ~= "table" then return nil, "ability record unavailable" end
            if anchors_ok == nil then
                anchors_ok = true
                for _, anchor in ipairs(spec.anchors) do
                    local expected = anchor.expected_hex
                    local bytes = io.rom_read(anchor.rom_offset, #expected // 2)
                    if type(bytes) ~= "table" then anchors_ok = false; break end
                    for i = 1, #expected // 2 do
                        if bytes[i] ~= tonumber(expected:sub(i * 2 - 1, i * 2), 16) then
                            anchors_ok = false
                        end
                    end
                end
            end
            if not anchors_ok then return nil, "ability reader ROM anchor mismatch" end
            if not mon.species or mon.species < 1 or mon.species >= spec.species_limit then
                return nil, "ability species outside profile"
            end
            if type(mon.ot_id) ~= "number" or type(mon.personality) ~= "number" then
                return nil, "ability identity unavailable"
            end
            local base = io.read_u32(spec.base_stats_pointer)
            if not rom_range(base, spec.species_limit * spec.stride) then
                return nil, "ability base-stat pointer outside ROM"
            end
            local original = {}
            for i, off in ipairs(spec.slot_offsets) do
                original[i] = io.read_u8(base + mon.species * spec.stride + off)
                if type(original[i]) ~= "number" then return nil, "unreadable ability slot" end
            end
            local flags = {}
            for name, id in pairs(spec.flags) do
                local byte = io.read_u8(spec.flags_base + (id - spec.flags_start) // 8)
                if type(byte) ~= "number" then return nil, "unreadable ability flags" end
                flags[name] = (byte & (1 << (id % 8))) ~= 0
            end
            local sb2, why = r.read_sb2()
            if not sb2 then return nil, why end
            local trainer_id = io.read_u32(sb2 + d.SB2_OT_ID_OFFSET)
            if type(trainer_id) ~= "number" then return nil, "unreadable ability seed" end

            local randomize = flags.randomizer and not flags.facility
                and not flags.temporary_team and not flags.link
            if randomize then
                -- RR exempts opponents in certain scripted fights, but still randomizes
                -- mons sharing an OT with any of the six player-party records.
                local own_ot = false
                local party, bad = party_base()
                if not party then return nil, bad end
                for slot = 0, party_capacity - 1 do
                    local ot = io.read_u32(party + slot * R.PARTY_MON_SIZE + 4)
                    if type(ot) ~= "number" then return nil, "unreadable ability party identity" end
                    if ot == mon.ot_id then own_ot = true end
                end
                if not own_ot then
                    local battle_flags = io.read_u32(a.BATTLE_TYPE_ADDR)
                    if type(battle_flags) ~= "number" then return nil, "unreadable ability battle flags" end
                    if (battle_flags & d.BATTLE_TYPE_TRAINER_MASK) ~= 0 then
                        local tid = io.read_u16(a.TRAINER_OPPONENT_ADDR)
                        local trainers = io.read_u32(spec.trainer_table_pointer)
                        if type(tid) ~= "number" or not rom_range(trainers, spec.trainer_stride) then
                            return nil, "ability trainer pointer outside ROM"
                        end
                        local at = trainers + tid * spec.trainer_stride + spec.trainer_class_offset
                        if not rom_range(at, 1) then return nil, "ability trainer pointer outside ROM" end
                        local class = io.read_u8(at)
                        if type(class) ~= "number" then return nil, "unreadable ability trainer class" end
                        for _, fixed in ipairs(spec.fixed_trainer_classes) do
                            if class == fixed then randomize = false end
                        end
                        if flags.hardcore then
                            for i = 0, spec.hardcore_trainer_count - 1 do
                                local fixed = io.read_u32(spec.hardcore_trainers + i * spec.hardcore_trainer_stride
                                    + spec.hardcore_trainer_offset)
                                if type(fixed) ~= "number" then return nil, "unreadable fixed ability trainer" end
                                if fixed == tid then randomize = false; break end
                            end
                        end
                        if flags.scripted_battle then randomize = false end
                    end
                end
            end
            local restricted = flags.hardcore or flags.restricted
            local pool, count
            if randomize then
                pool = restricted and spec.restricted_pool or spec.normal_pool
                count = io.read_u8(pool)
                if type(count) ~= "number" or count < 1 or count > 255 then
                    return nil, "invalid ability randomizer pool"
                end
            end
            local slots = {}
            for i, original_id in ipairs(original) do
                local aid = original_id
                if aid ~= 0 and randomize then
                    local seed = math.max(trainer_id, 1)
                    local value = ((seed & 0xFFFF) % count + mon.species + aid) & 0xFFFF
                    if value >= count then value = (value - count + 2) & 0xFFFF end
                    local index = (value ~ ((seed >> 16) % 255)) % count
                    aid = io.read_u8(pool + 1 + index)
                    if type(aid) ~= "number" or aid < 1 then return nil, "unreadable randomized ability" end
                end
                if aid ~= 0 and restricted and mon.ot_id == trainer_id then
                    local exceptions = spec.difficulty_exceptions[tostring(aid)]
                    aid = (exceptions and exceptions[tostring(mon.species)])
                        or spec.difficulty_remap[tostring(aid)] or aid
                end
                slots[i] = aid
            end
            -- CFRU bit 31 selects hidden; ordinary slots use personality parity.
            local selected = 1
            if mon.ability_num == 1 and original[3] ~= 0 then selected = 3
            elseif (mon.personality & 1) ~= 0 and original[2] ~= 0 then selected = 2 end
            return { ability_id = slots[selected], ability_slot = selected,
                     ability_slots = slots, ability_randomized = randomize }
        end
    end

    return r
end

return R
