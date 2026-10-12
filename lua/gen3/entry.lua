-- lua/gen3/entry.lua — composition root for the Gen 3 (FRLG + Radical Red) client.
--
-- `Entry.build(deps)` wires the parts together over the injected, BizHawk-shaped `deps.io`
-- and `deps.ev`, so every bootstrap (P3's shadow_run.lua, P4's run.lua) and the lupa harness
-- build the identical graph. Nothing in lua/gen3/ touches a BizHawk global; a bootstrap
-- builds the io/ev tables and injects them.
--
--   deps.root     repo root path (for dofile / pack files)
--   deps.mode     "observer" (P3: reads + signals over a read-only io, returns nil, parts)
--                 or "production" (P4: returns client, parts; see build_production below)
--   deps.io       read_u8/read_u16/read_u32(addr), read_bytes(addr, len),
--                 rom_read(off, len), framecount(), register(name); production adds
--                 write_u8(addr, value) (the ONLY write sink, reached only through writes.lua)
--                 and optionally saveram() (flush the battery at the save site)
--   production only: deps.net, deps.hud, deps.player, deps.rom_sha1 (the admitted hash),
--                 deps.native (an injected companion part; nil by default)
--                 (function(snapshot, reason) -> ok, why for the battle reasons: the C4-B seam),
--                 deps.boxes_new (a harness replacement for lua/gen3/boxes.lua's B.new)
--   deps.ev       on_bus_exec(fn, addr, name) -> id, unregister(id)
--   deps.pack     "gen3_frlg" | "gen3_rr"
--   deps.title    "firered" | "leafgreen" | "radical_red"
--   deps.kind     admission artifact kind ("clean" | "named" | "companion"; default "clean")
--   deps.on_fire  optional kind -> function(signal), run inside the signal hook
--   deps.log      function(text)
--
-- Returns `nil, parts` in observer mode: the leading slot is the client production mode will
-- return, so a caller written against either mode reads `local client, parts = ...`.
--
-- Foundation selection (PLAN §5.1) is hash-first: `Entry.admit` compares the cartridge hash,
-- case-insensitively, against the union of every pack's admission set (the per-artifact
-- rom_sha1/rom_md5 pins in engine_signals.json). A hash in no table is admitted by ANCHORS
-- (every engine site of exactly one admitted pack/title/kind still reads as pinned in ROM),
-- and only then by the header-named family fallback. This file is the ONLY place a
-- foundation is ever named; everything downstream sees pack data.
--
-- There is deliberately NO pure-Lua rehash here (Gen 1 has one): a GBA cartridge is up to
-- 32 MiB and a Lua SHA-1 over it costs minutes at boot. The anchor pass IS the byte-level
-- proof, and it reads a few hundred bytes.
local Entry = {}

local function load_json(json, path)
    local f = assert(io.open(path, "rb"), "cannot open " .. path)
    local text = f:read("*a")
    f:close()
    return assert(json.decode(text))
end

local function file_exists(path)
    local f = io.open(path, "rb")
    if not f then return false end
    f:close()
    return true
end

-- The packs and what each one cannot (yet) say for itself.
--   rom_type     the strings the server routes on (server/adapters/__init__.py:39-41)
--   header_code  GBA header game code -> title, for the named-family fallback only. RR is a
--                FireRed hack and carries FireRed's code, so it takes no part: an RR build
--                with an unknown hash is admitted by anchors or not at all.
-- trade_policy is host protocol policy, in frames. APPLY gets its own dispatch
-- budget; native owns the timeout once a scene has actually been published.
Entry.PACKS = {
    gen3_frlg = {
        randomizable = true,
        companion_required = true,
        trade_policy = {prepare_frames=600, apply_frames=1800},
        rom_type = { firered = "firered", leafgreen = "leafgreen" },
        header_code = { BPRE = "firered", BPGE = "leafgreen" },
    },
    gen3_rr = {
        companion_required = true,
        trade_policy = {prepare_frames=600, apply_frames=1800},
        rom_type = { radical_red = "firered_rr" },
    },
    -- Registered so the packs/admission tables build and the hash is recognized (E2-ENTRY);
    -- joined Entry.ROUTED at EG4 (docs/gen3_emerald/PLAN.md §5 E3 row, owner ruling 24).
    gen3_emerald = {
        randomizable = true,
        companion_required = true,
        trade_policy = {prepare_frames=600, apply_frames=1800},
        rom_type = { emerald = "emerald" },
        header_code = { BPEE = "emerald" },
    },
    -- X3: the pokeemerald-expansion reference build (ROM 28877d73), registered so its hash names
    -- its own pack (never gen3_emerald's). Only the known reference digest is admitted;
    -- no header_code or anchor fallback can admit another expansion build.
    gen3_exp = {
        rom_type = { emerald_expansion_28877d73 = "emerald_expansion_28877d73" },
        hash_only = true,
    },
}
-- Every pack file Entry.build/Entry.admit reads, as literal repo-relative paths: the release
-- manifest derives what to ship from these literals, so a pack file must be named here or a
-- player never gets it.
Entry.PACK_FILES = {
    gen3_frlg = {
        profile = "data/games/gen3_frlg/profile.json",
        sites = "data/games/gen3_frlg/engine_signals.json",
        checkpoint = "data/games/gen3_frlg/write_checkpoint.json",
        area_map = "data/games/gen3_frlge/area_map.json",
        locations = "data/games/gen3_frlge/gen3_frlge_locations.lua",
    },
    -- RR is a FireRed map hack: the old client resolves its areas from the same FRLG tables
    -- (lua/games/gen3_frlge.lua:641-651, the non-Emerald branch)
    gen3_rr = {
        profile = "data/games/gen3_rr/profile.json",
        sites = "data/games/gen3_rr/engine_signals.json",
        checkpoint = "data/games/gen3_rr/write_checkpoint.json",
        area_map = "data/games/gen3_frlge/area_map.json",
        locations = "data/games/gen3_frlge/gen3_frlge_locations.lua",
    },
    -- Emerald keeps its own area map/locations (E1-PACK): it is not a FRLG map hack.
    gen3_emerald = {
        profile = "data/games/gen3_emerald/profile.json",
        sites = "data/games/gen3_emerald/engine_signals.json",
        checkpoint = "data/games/gen3_emerald/write_checkpoint.json",
        area_map = "data/games/gen3_emerald/area_map.json",
        locations = "data/games/gen3_emerald/gen3_emerald_locations.lua",
    },
    -- X3: one directory per onboarded expansion build (its generated pack, TEMPLATES.md T6)
    gen3_exp = {
        profile = "data/games/gen3_exp/28877d73/profile.json",
        sites = "data/games/gen3_exp/28877d73/engine_signals.json",
        checkpoint = "data/games/gen3_exp/28877d73/write_checkpoint.json",
        area_map = "data/games/gen3_exp/28877d73/area_map.json",
        locations = "data/games/gen3_exp/28877d73/gen3_exp_locations.lua",
    },
}
-- Which packs lua/slink.lua's Gen 3 route sends to the rewritten client. The route reads this
-- table; the launcher keeps no copy of it. gen3_rr joined at G5 (C5-6), gen3_emerald at EG4
-- (owner ruling 24): every admitted pack is routed, and anything else on a GBA core is
-- refused by the launcher.
Entry.ROUTED = { gen3_frlg = true, gen3_rr = true, gen3_emerald = true, gen3_exp = true }

Entry.ROM_TYPE = {}
for _, pack in pairs(Entry.PACKS) do
    for title, rt in pairs(pack.rom_type) do Entry.ROM_TYPE[title] = rt end
end
-- A header-named vanilla family (an unknown-hash cartridge that still says BPRE/BPGE) reads
-- the clean artifact's pack data; if its bytes really differ, the site check refuses it.
Entry.BASE_KIND = { named = "clean", rand = "clean", rand_companion = "companion" }

-- ── admission ────────────────────────────────────────────────────────────────────────

-- The artifact table for one pack: title -> kind -> artifact (rom_sha1, rom_md5, sites).
function Entry.artifacts(root, json, pack)
    local files = assert(Entry.PACK_FILES[pack], "unknown pack " .. tostring(pack))
    local out = {}
    for title, entry in pairs(load_json(json, root .. "/" .. files.sites).titles) do
        out[title] = entry.artifacts
    end
    return out
end

-- Bootstrap storage is meaningful only for a companion with the durable witness: the
-- ABI2 arena, or RR's isolated durable descriptor (RR-DURABLE: native.TRADE_BASE, the shadow
-- block of patch/src/rr_trade_relay.h). An ABI1 profile without it gets no journal.
function Entry.trade_journal_supported(root, json, artifact)
    if artifact.kind ~= "companion" and artifact.kind ~= "rand_companion" then return false end
    local files = assert(Entry.PACK_FILES[artifact.pack], "unknown pack")
    local full = load_json(json, root .. "/" .. files.profile)
    return type(full.native) == "table" and (full.native.ABI == 2
        or (artifact.pack == "gen3_rr" and type(full.native.TRADE_BASE) == "number"))
end

-- hash (lowercase sha1 or md5) -> { pack, title, kind, rom_type } over every pack's
-- admission set. Both digests are indexed: they cannot collide (40 vs 32 hex digits) and
-- BizHawk's gameinfo hash is not the same digest on every core. A digest repeated across two
-- rows would otherwise silently keep whichever row Lua's unordered pairs() visited last, so a
-- collision is a hard build-time error naming both rows rather than a silent mis-admission.
function Entry.admission_table(root, json)
    local table_ = {}
    for pack, def in pairs(Entry.PACKS) do
        for title, artifacts in pairs(Entry.artifacts(root, json, pack)) do
            for kind, artifact in pairs(artifacts) do
                local row = { pack = pack, title = title, kind = kind,
                              rom_type = def.rom_type[title], production = artifact.production }
                -- equivalent_sha1s: earlier stamps of this same canonical build (only the menu-version
                -- field differs), still on players' cartridges, so they admit as this artifact.
                local digests = { { "rom_sha1", artifact.rom_sha1 }, { "rom_md5", artifact.rom_md5 } }
                for _, sha1 in ipairs(artifact.equivalent_sha1s or {}) do
                    digests[#digests + 1] = { "equivalent_sha1", sha1 }
                end
                for _, pair in ipairs(digests) do
                    local key, digest = pair[1], pair[2]
                    if digest then
                        digest = digest:lower()
                        local prior = table_[digest]
                        if prior then
                            error(string.format(
                                "[gen3/entry] duplicate %s %s shared by %s/%s/%s and %s/%s/%s",
                                key, digest, prior.pack, prior.title, prior.kind,
                                row.pack, row.title, row.kind), 0)
                        end
                        table_[digest] = row
                    end
                end
            end
        end
    end
    return table_
end

-- Every engine site of one artifact still reads as pinned in this ROM.
local function anchors_hold(artifact, rom_read)
    for _, site in pairs(artifact.sites) do
        local hex = site.expected_hex
        local n = #hex // 2
        if type(site.rom_offset) ~= "number" then return false end
        local bytes = rom_read(site.rom_offset, n)
        if type(bytes) ~= "table" or #bytes ~= n then return false end
        for i = 1, n do
            if bytes[i] ~= tonumber(hex:sub(2 * i - 1, 2 * i), 16) then return false end
        end
    end
    return true
end

-- Every admitted (pack, title, kind) whose anchors all hold in this ROM.
function Entry.anchor_matches(args)
    local matches = {}
    for pack, def in pairs(Entry.PACKS) do
        for title, artifacts in pairs(Entry.artifacts(args.root, args.json, pack)) do
            for kind, artifact in pairs(artifacts) do
                if anchors_hold(artifact, args.rom_read) then
                    matches[#matches + 1] = { pack = pack, title = title, kind = kind,
                                              rom_type = def.rom_type[title], production = artifact.production }
                end
            end
        end
    end
    table.sort(matches, function(a, b)
        return a.pack .. a.title .. a.kind < b.pack .. b.title .. b.kind
    end)
    return matches
end

-- Decide the foundation for the loaded cartridge.
--   args.root, args.json   pack lookup
--   args.rom_hash          gameinfo.getromhash() (any case; sha1 or md5)
--   args.rom_read          function(offset, length) -> {bytes} over the flat ROM
--   args.header_code       the GBA header game code ("BPRE"), for the named fallback
-- Returns { pack, title, kind, rom_type, rom_hash, admitted_by } or nil, reason;
-- admitted_by is "hash", "anchors" or "header".
function Entry.admit(args)
    local hash = tostring(args.rom_hash or ""):lower()
    local hit = Entry.admission_table(args.root, args.json)[hash]
    if hit then
        if hit.production == false then return nil, "non-production cartridge is not admitted" end
        return { pack = hit.pack, title = hit.title, kind = hit.kind,
                 rom_type = hit.rom_type, rom_hash = hash, admitted_by = "hash" }
    end
    local code = tostring(args.header_code or "")
    if args.rom_read then
        local matches = {}
        for _, match in ipairs(Entry.anchor_matches(args)) do
            if not Entry.PACKS[match.pack].hash_only then matches[#matches + 1] = match end
        end
        if #matches == 1 then
            local m = matches[1]
            if m.production == false then return nil, "non-production cartridge is not admitted" end
            local kind = m.kind
            if kind == "clean" and Entry.PACKS[m.pack].randomizable == true then kind = "rand" end
            if kind == "companion" and Entry.PACKS[m.pack].randomizable == true then kind = "rand_companion" end
            return { pack = m.pack, title = m.title, kind = kind, rom_type = m.rom_type,
                     rom_hash = hash, admitted_by = "anchors" }
        elseif #matches > 1 then
            local names = {}
            for i, m in ipairs(matches) do names[i] = m.pack .. "/" .. m.title .. "/" .. m.kind end
            return nil, "ambiguous: the anchors of " .. table.concat(names, ", ")
                        .. " all hold: header " .. code .. ", hash " .. hash
        end
    end
    for pack, def in pairs(Entry.PACKS) do
        local title = def.header_code and def.header_code[code]
        if title then
            return { pack = pack, title = title, kind = "named", rom_type = def.rom_type[title],
                     rom_hash = hash, admitted_by = "header" }
        end
    end
    return nil, "header " .. code .. " is not an admitted Gen 3 cartridge: hash " .. hash
end

-- Entry.admit_routed(args) -- Entry.admit plus the LAUNCHER's own policy: a header-only
-- admission (an unpinned hack or a bad dump that merely says BPRE/BPGE/BPEE) and a pack not
-- (yet) in Entry.ROUTED are both refused here, never routed. This is the ONE place every
-- caller enforces that policy -- lua/slink.lua and lua/gen3/run.lua both call it -- so a
-- caller that dofiles run.lua directly (the duo harness, tests/unit) is held to the same gate
-- the real launcher is. Before this, run.lua called Entry.admit alone and never re-checked
-- ROUTED/admitted_by, so the duo evidence never actually exercised the launcher's refusal
-- (OMP cx-dbabbd62).
-- Returns the same shape as Entry.admit, or nil, reason.
function Entry.admit_routed(args)
    local admitted, why = Entry.admit(args)
    if not admitted then return nil, why end
    if admitted.admitted_by == "header" then
        return nil, "this " .. tostring(admitted.title) .. " build (header "
                    .. tostring(args.header_code) .. ") is not a pinned cartridge -- "
                    .. "other builds and hacks are not supported yet"
    end
    if not Entry.ROUTED[admitted.pack] then
        return nil, "the " .. tostring(admitted.pack) .. " pack is not yet routed to the Gen 3 client"
    end
    -- Patch-first (owner 2026-10-02): a pack that marks `companion_required` refuses its CLEAN
    -- artifacts (kind clean / rand; header-only "named" was refused above). gen3_exp, which has
    -- no companion artifact, never sets it, so the Emerald Expansion keeps booting clean.
    -- (Not `production = false` in the generated engine_signals: that also makes Entry.build refuse
    -- the clean artifact, which every clean-world unit test builds, and is the Gen 3 lane's call.)
    if Entry.PACKS[admitted.pack].companion_required and (admitted.kind == "clean" or admitted.kind == "rand") then
        return nil, "this " .. tostring(admitted.title) .. " cartridge needs the SLink companion patch; "
                    .. "prepare it through the Manager or /patcher"
    end
    return admitted
end

-- The GBA cartridge header: 12-byte game title at $A0, 4-byte game code at $AC (GBATEK 3.2).
-- These are the two platform constants in this file; they are not game addresses.
function Entry.header_code(rom_read)
    local bytes = rom_read(0xAC, 4)
    local out = {}
    for i = 1, 4 do out[i] = string.char(bytes[i]) end
    return table.concat(out)
end
function Entry.header_title(rom_read)
    local bytes, out = rom_read(0xA0, 12), {}
    for i = 1, 12 do
        if bytes[i] == 0 then break end
        out[i] = string.char(bytes[i])
    end
    return table.concat(out)
end

-- ── build ────────────────────────────────────────────────────────────────────────────

-- Production (P4, docs/gen3/research/p4_gen1_contract_map.md §3.1). The same pack data as
-- observer mode, plus: the checkpoint instance over the injected io, the write policy, the
-- one write sink, the box mover and the client (lua/gen3/client.lua over lua/core). Signals
-- are NOT built here: client:start() builds them, so a refused site check fails start() by
-- name (the Gen 1 run.lua pattern) instead of the build.
local function build_production(deps, c)
    local L, io_, pack = c.L, c.io, c.pack
    assert(io_.write_u8 ~= nil, "production io needs write_u8")
    local wc = assert(c.write_checkpoint, "pack " .. pack .. " ships no write checkpoint for " .. c.title)
    local Safety, Writes = L("lua/gen3/safety.lua"), L("lua/gen3/writes.lua")
    local log = deps.log or function() end
    local native = deps.native
    local safety = Safety.new(wc, {
        io = {
            read_u8 = function(addr, domain)
                if domain == "ROM" then return io_.rom_read(addr, 1)[1] end
                return io_.read_u8(addr)
            end,
            read_u16_le = function(addr) return io_.read_u16(addr) end,
            read_u32_le = function(addr) return io_.read_u32(addr) end,
        },
        -- R14 is the CURRENT mode's bank (R14_irq at an IRQ entry; G5-RR-CPU-IRQ live probe)
        regs = function()
            return { R15 = io_.register("R15"), CPSR = io_.register("CPSR"), R14 = io_.register("R14") }
        end,
        native_idle = function()
            if native and native.idle then return native:idle() end
            return true                                    -- no native part: nothing in flight
        end,
    }, c.artifact_kind)
    -- The write policy: a straight pass-through. Every reason and its args reach the library,
    -- which owns the clause sets (overworld is the G3-signed predicate; battle_faint /
    -- battle_commit / native / sound are the C4-B2 sets); an unknown reason is refused by name
    -- there. The client turns any refusal into a HOLD, never a silent write.
    local policy = {}
    function policy:snapshot() return safety:snapshot() end
    function policy:check(snapshot, reason, args) return safety:check(snapshot, reason, args) end
    -- G4-PH: the pack's proven controller hand-off entry ({address, 4, value}) or nil; shape
    -- "explode" (G5-EXPLODE-HANDOFF) only where the pack also proves the Explode+H plan
    function policy:handoff_entry(battler, shape) return safety:handoff_entry(battler, shape) end
    local writes = Writes.new({
        safety = policy, frame = io_.framecount,
        io = io_,                                          -- writes.lua is the only caller of write_u8
        log = function(r)
            log(string.format("[SLink-gen3] write %s 0x%08X +%d frame %d", r.reason, r.address, r.len, r.frame))
        end,
    })
    local reads = c.reads
    local boxes_rel = "lua/gen3/boxes.lua"
    -- deps.boxes_new(profile, reads, box_io) replaces the box mover (a harness seam; the
    -- release build never passes it)
    local boxes_new = deps.boxes_new
    if not boxes_new and file_exists(c.root .. "/" .. boxes_rel) then boxes_new = L(boxes_rel).new end
    local boxes = boxes_new and boxes_new(c.profile, reads, {
        read_bytes = io_.read_bytes, rom_read = io_.rom_read, writes = writes }) or nil
    local files = Entry.PACK_FILES[pack]
    local core = { Session = L("lua/core/session.lua"), Identity = L("lua/core/identity.lua"),
                   Deferred = L("lua/core/deferred.lua") }
    -- ── native: the RR companion ABI (P4 C4-7) ─────────────────────────────────────────
    -- Constructed BEFORE the client and passed INTO it, so one instance answers every seam: the
    -- client's own service/commands, Safety's native_idle closure below, and (through it) the
    -- writes policy. Attaching late left deps.native nil, so native_idle could report idle while
    -- the real mailbox was busy (Codex REV2). The session does not exist yet, so send resolves it
    -- lazily. The FULL pack profile is read here: the arena lives at profile.native, outside
    -- titles (Codex REV on C5-1). FR/LG/Emerald's ABI2 binding additionally requires explicit
    -- production metadata (their companion rows and patch/dist/gen3_companions.json carry it).
    local session   -- not `client`: that is a BizHawk global name (test_gen3_signals BizHawk-globals scan)
    local full_profile = load_json(c.json, c.root .. "/" .. files.profile)
    local fr_native = ((pack == "gen3_frlg" and (c.title == "firered" or c.title == "leafgreen"))
        or (pack == "gen3_emerald" and c.title == "emerald")) and c.production == true
        and type(full_profile.native) == "table" and full_profile.native.ABI == 2
    if not native and (pack == "gen3_rr" or fr_native) and c.artifact_kind == "companion"
       and type(full_profile.native) == "table" then
        local status = c.write_checkpoint and c.write_checkpoint.predicates
            and c.write_checkpoint.predicates.script_context_status
        -- panel_closed: the SOULLINK panel is a start-menu row, so it is over when the script
        -- context that owned the menu is back to SHUTDOWN (the pack's own predicate). The result
        -- byte: the patch's SlinkInfo has no result field (patch/src/handlers.c:224-234), so this
        -- binding can only report "closed" (1), never "next page" (0).
        -- TODO(C4-7): wire the page-turn result once the patch publishes one (P5/P6 panel scope).
        local function panel_closed()
            if not status then return true, 1 end
            local value = io_.read_u8(status.address + (status.offset or 0))
            if status.mask then value = value & status.mask end
            return value == status.expect, 1
        end
        native = L("lua/gen3/native.lua").new(full_profile, {
            -- The exact io surface native.lua reads.
            io = {
                read_u8 = function(addr) return io_.read_u8(addr) end,
                read_u16 = function(addr) return io_.read_u16(addr) end,
                read_u32 = function(addr) return io_.read_u32(addr) end,
                read_bytes = function(addr, len) return io_.read_bytes(addr, len) end,
                framecount = function() return io_.framecount() end,
            },
            writes = writes, reads = reads, array = c.json.array,
            send = function(event, fields)
                if session then return session.send(event, fields) end
            end,
            in_battle = function() return session and session.driver.in_battle() or false end,
            trade_safe = function()
                if not session or session.driver.in_battle() then return false,"trade field unavailable" end
                return safety:check(nil,"overworld")
            end,
            trade_recovery_clear = function()
                local journal = io_.trade_journal
                return journal ~= nil and journal:ready() == true and journal:hidden() == false
            end,
            trade_capability_changed = function()
                if session then session.hello_sent = false end
            end,
            panel_stage_safe = function()
                if not session or not session:eligible() or session.driver.in_battle() then return false end
                local ok = safety:check(nil, "overworld")
                return ok == true
            end,
            title = c.title, player = deps.player, production = c.production,
            artifact_kind = c.artifact_kind, log = log,
            panel_closed = panel_closed,
        })
        local rr_native = pack == "gen3_rr" and c.title == "radical_red" and c.production == true
            and type(full_profile.native.TRADE_BASE) == "number"
        if fr_native or rr_native then
            -- Same persisted boot counter the client uses for its battle nonce.
            -- The wire connection counter is not the native/journal epoch.
            local seed = os.getenv("SLINK_GEN3_BATTLE_NONCE") or deps.battle_nonce_seed
            local epoch
            if type(seed) == "string" and not seed:find("[^%x]") then
                if #seed == 16 then epoch = tonumber(seed:sub(1,8),16)
                elseif #seed > 0 and #seed <= 8 then epoch = tonumber(seed,16) end
            end
            if epoch then native:bind_trade_session(epoch) end
        end
    end
    session = L("lua/gen3/client.lua").new({
        Trade = L("lua/gen3/trade.lua"), owed_reports = L("lua/owed_reports.lua"),
        reads = reads, R = c.Reads, profile = c.profile, sites = c.sites, Signals = c.Signals,
        writes = writes, boxes = boxes, policy = policy, net = assert(deps.net, "deps.net required"),
        hud = assert(deps.hud, "deps.hud required"), json = c.json, io = io_, ev = c.ev,
        area_map = load_json(c.json, c.root .. "/" .. files.area_map),
        locations = dofile(c.root .. "/" .. files.locations),
        player = deps.player, rom_type = c.parts.rom_type, rom_sha1 = deps.rom_sha1 or c.parts.rom_hash,
        foundation = pack, artifact_kind = (c.parts.kind == "rand" or c.parts.kind == "rand_companion") and c.parts.kind or c.artifact_kind,
        native = native, log = deps.log, core = core,
        -- the hello's companion evidence (server companion_refusal): the pack's pinned ABI when this
        -- cartridge's own mailbox is live, whatever the launcher's artifact kind says
        companion_abi = function() return L("lua/gen3/native.lua").companion_live(full_profile.native, io_) end,
        trade_policy = Entry.PACKS[pack].trade_policy,
        rom_size = deps.rom_size,
        rom_content_new = deps.rom_content_new or function(tbl, rom_io)
            return L("lua/gen3/rom_content.lua").new(tbl, rom_io)
        end,
        -- the battle request nonce seed (card C5-10b): the bootstrap's entropy, or the harness
        -- seam for determinism. Client.new validates it and mints NO identity without it.
        -- the env seam wins over the bootstrap so a harness can pin a session deterministically
        battle_nonce_seed = os.getenv("SLINK_GEN3_BATTLE_NONCE") or deps.battle_nonce_seed,
        -- the m4a fact (SE1 player / gSoundInfo pointer, field offsets) lives in the checkpoint
        -- pack's sound block, the same block safety's sound clauses judge: one source of truth
        sound = wc.sound,
        -- title facts the client must not hard-code (E3-CLIENT): the committed battle state
        -- (safety's own commit_guard) and the gift areas; the client fails closed without them
        commit_guard = wc.battle and wc.battle.commit_guard, gift_areas = wc.gift_areas,
    })
    local parts = c.parts
    parts.writes, parts.boxes, parts.safety, parts.policy, parts.native = writes, boxes, safety, policy, native
    parts.native_present = native ~= nil
    return session, parts
end

function Entry.build(deps)
    local root = assert(deps.root, "deps.root required")
    local mode = deps.mode or "observer"
    assert(mode == "observer" or mode == "production", "deps.mode " .. tostring(mode)
           .. " is not a Gen 3 build mode (observer | production)")
    local L = function(rel) return dofile(root .. "/" .. rel) end
    local json = L("lua/json_codec.lua")
    local Reads, Signals = L("lua/gen3/reads.lua"), L("lua/gen3/signals.lua")

    local pack = assert(deps.pack, "deps.pack required")
    local pack_def = assert(Entry.PACKS[pack], "unknown pack " .. tostring(pack))
    local files = Entry.PACK_FILES[pack]
    local title = assert(deps.title, "deps.title required")
    local kind = deps.kind or "clean"
    local artifact_kind = Entry.BASE_KIND[kind] or kind

    local profile = assert(load_json(json, root .. "/" .. files.profile).titles[title],
                           "unknown title " .. title .. " in " .. pack)
    -- The one exception (Gen 3 grant 2026-09-26, Emerald EG2): an OBSERVER build of an
    -- unadmitted title when the caller names exactly that "<pack>/<title>". Observer parts carry
    -- no writer, native or net; production never honours it, whatever the environment says.
    local observe_unadmitted = profile.admitted == false and mode == "observer"
        and deps.allow_unadmitted == pack .. "/" .. title
    assert(profile.admitted ~= false or observe_unadmitted,
           title .. " is a known but unadmitted Gen 3 title in " .. pack)
    if observe_unadmitted then
        (deps.log or function() end)("[SLink-gen3] OBSERVER building unadmitted " .. pack .. "/" .. title)
    end
    local title_sites = assert(load_json(json, root .. "/" .. files.sites).titles[title],
                               "pack " .. pack .. " ships no engine sites for " .. title)
    local artifact = assert(title_sites.artifacts[artifact_kind],
                            pack .. "/" .. title .. " ships no artifact of kind " .. artifact_kind)
    if mode == "production" then
        assert(artifact.production ~= false, "non-production cartridge cannot build a production client")
        if (pack == "gen3_frlg" or pack == "gen3_emerald") and artifact_kind == "companion" then
            assert(artifact.production == true,
                   "vanilla companion requires explicit production cartridge metadata")
        end
    end
    local sites = assert(artifact.sites, "artifact " .. artifact_kind .. " ships no sites")
    local write_checkpoint = load_json(json, root .. "/" .. files.checkpoint)[title]

    local io_ = assert(deps.io, "deps.io required")
    -- The pointer symbols (gSaveBlock1Ptr / gSaveBlock2Ptr / gPokemonStoragePtr) live in the
    -- checkpoint pack, so reads gets them as data rather than naming an address itself.
    local reads = Reads.new(profile, io_, write_checkpoint and write_checkpoint.pointers)
    if mode == "production" then
        local parts = {
            pack = pack, title = title, kind = kind, artifact_kind = artifact_kind,
            rom_type = pack_def.rom_type[title], rom_hash = artifact.rom_sha1,
            profile = profile, sites = sites, write_checkpoint = write_checkpoint,
            reads = reads, json = json, mode = mode, log = deps.log or function() end,
        }
        return build_production(deps, {
            L = L, root = root, io = io_, ev = assert(deps.ev, "deps.ev required"), pack = pack,
            title = title, profile = profile, sites = sites, write_checkpoint = write_checkpoint,
            artifact_kind = artifact_kind, reads = reads, Reads = Reads, Signals = Signals,
            json = json, parts = parts, production = artifact.production,
        })
    end
    local signals = Signals.new(profile, sites, io_, assert(deps.ev, "deps.ev required"),
                                deps.on_fire)
    -- The checkpoint predicate is a sibling card (lua/gen3/safety.lua, P3 C3-2); bind it
    -- when it has landed so no bootstrap has to know whether it exists yet.
    local safety_rel = "lua/gen3/safety.lua"
    local safety = file_exists(root .. "/" .. safety_rel) and L(safety_rel) or nil

    local parts = {
        pack = pack, title = title, kind = kind, artifact_kind = artifact_kind,
        rom_type = pack_def.rom_type[title], rom_hash = artifact.rom_sha1,
        profile = profile, sites = sites, write_checkpoint = write_checkpoint,
        reads = reads, signals = signals, safety = safety, json = json,
        mode = mode, log = deps.log or function() end,
    }
    return nil, parts
end

return Entry
