-- lua/gen1/entry.lua — composition root for the Gen 1 client.
--
-- `Entry.build(deps)` wires reads/signals/writes/boxes/rom/safety/client together over the
-- injected BizHawk-shaped `deps.io` and transport `deps.net`, so production (slink_gen1.lua)
-- and the lupa harness build the identical client. Nothing here touches BizHawk globals
-- except `Entry.bizhawk_deps()`.
--
--   deps.root        repo root path (for dofile / data files)
--   deps.io          read_u8(addr, domain?), read_range(addr, len, domain?), write_u8(addr, v, domain?),
--                    on_bus_exec, unregister, framecount, register(name), domains(), saveram?
--   deps.net         init(host, port), send(line), receive(), pump(), connected()
--   deps.hud         show/prompt/set_game_over/set_rebuilding/clear_rebuilding
--   deps.pack        "gen1_rby" | "gen1_purergb" (the data/games directory; default gen1_rby)
--   deps.title       "red" | "blue" | "yellow" | "purered" | "pureblue" | "puregreen"
--   deps.kind        admission artifact kind ("clean" | "overlay" | "rand" | "rand_overlay"; default
--                    "clean"; the rand kinds load the base kind's pack files)
--   deps.player      "a" | "b"
--   deps.rom_sha1    sha1 of the running cartridge (may differ from clean when patched)
--   deps.log         function(text)
--
-- Foundation selection (PLAN §4 row 1, A3) is hash-first: `Entry.admit` compares the ROM's
-- sha1, case-insensitively, against the union of every pack's admission set; a sha1 in no
-- table is then admitted by ANCHORS (every engine site + checkpoint slice of exactly one
-- admitted pack/title/kind still reads as pinned: a randomized artifact, kinds rand /
-- rand_overlay). The header title only narrows candidates and words the refusal. This file
-- is the ONLY place a foundation is ever named; everything downstream sees pack data.
local Entry = {}
-- One registry instance and ONE bound signals factory own the production hook backend
-- across repeated builds: the factory holds the only reference to a failed service whose
-- cleanup is still outstanding, so a per-build rebind would drop that cleanup authority.
local shared_signals

local function load_json(json, path)
    local f = assert(io.open(path, "rb"), "cannot open " .. path)
    local text = f:read("*a")
    f:close()
    return assert(json.decode(text))
end

-- The packs and what each one cannot (yet) say for itself.
--   rom_type  the strings the server routes on (server/adapters/__init__.py)
--   trade     the companion-patch addresses: vanilla's are the shipped patch (slink.asm:38,
--             trade_service.asm:67-68, the receptionist dispatch at ROM0 $29C3); an overlay
--             pack carries its own `trade` block in profile.json and this default is unused.
--             pureRGB clean ships none, so its panel/trade capability is ABSENT (no $29C3 probe).
--   admission the pack ships an admission.json (sha1 -> {title, kind, ...}); gen1_rby has none
--             yet, so its clean sha1s come from profile.json's per-title rom_sha1
Entry.PACKS = {
    gen1_rby = {
        rom_type = { red = "Red", blue = "Blue", yellow = "Yellow" },
        trade = { receptionist_hook = 0x29C3, dispatch_hex = "21004C063F",
                  service = { bank = 0x3F, addr = 0x4500 }, mailbox = 0xDEE2 },
    },
    gen1_purergb = {
        rom_type = { purered = "PureRed", pureblue = "PureBlue", puregreen = "PureGreen" },
        admission = true,
    },
}
-- Every pack file Entry.build/Entry.admit reads, as literal repo-relative paths: the release
-- manifest (tools/make_release.py, tests/unit/test_make_release_manifest.py) derives what to
-- ship from these literals, so a pack file must be named here or a player never gets it.
Entry.PACK_FILES = {
    gen1_rby = {
        profile = "data/games/gen1_rby/profile.json",
        sites = "data/games/gen1_rby/engine_signals.json",
        checkpoint = "data/games/gen1_rby/write_checkpoint.json",
        area_map = "data/games/gen1_rby/area_map.json",
        statics = "data/games/gen1_rby/static_encounters.json",
    },
    gen1_purergb = {
        profile = "data/games/gen1_purergb/profile.json",
        sites = "data/games/gen1_purergb/engine_signals.json",
        checkpoint = "data/games/gen1_purergb/write_checkpoint.json",
        area_map = "data/games/gen1_purergb/area_map.json",
        statics = "data/games/gen1_purergb/static_encounters.json",
        admission = "data/games/gen1_purergb/admission.json",
        charmap = "data/games/gen1_purergb/charmap.lua",
        species_index = "data/games/gen1_purergb/species_index.json",
        -- The SLink companion overlay (PLAN M3): a distinct artifact with its own profile
        -- (+ the `trade` block), sites and checkpoint (ROM symbols relocate, RAM does not) and
        -- its own admission rows; `<key>_<kind>` is what Entry.build loads for that kind.
        profile_overlay = "data/games/gen1_purergb/profile_overlay.json",
        sites_overlay = "data/games/gen1_purergb/engine_signals_overlay.json",
        checkpoint_overlay = "data/games/gen1_purergb/write_checkpoint_overlay.json",
        admission_overlay = "data/games/gen1_purergb/admission_overlay.json",
    },
}
Entry.ROM_TYPE = {}
for _, pack in pairs(Entry.PACKS) do
    for title, rt in pairs(pack.rom_type) do Entry.ROM_TYPE[title] = rt end
end

-- ── admission ────────────────────────────────────────────────────────────────────────

-- The public digest helper is retained for existing callers; the implementation
-- and actual-byte validation live in the shared admission module.
local entry_dir = debug.getinfo(1, "S").source:match("^@(.+[/\\])[^/\\]+$")
local cached_admission
local function admission_core(root)
    if root then return dofile(root .. "/lua/admission.lua") end
    if not cached_admission then cached_admission = dofile(assert(entry_dir) .. "../admission.lua") end
    return cached_admission
end
function Entry.sha1(read_u8, n)
    return admission_core().sha1(read_u8, n)
end
-- sha1 (lowercase) -> { pack, title, kind, rom_type } over every pack's admission set.
-- gen1_purergb: admission.json rows; gen1_rby: the per-title profile rom_sha1 (clean).
function Entry.admission_table(root, json)
    local table_ = {}
    for pack, def in pairs(Entry.PACKS) do
        local files = Entry.PACK_FILES[pack]
        if files.admission then
            for _, key in ipairs({ "admission", "admission_overlay" }) do
                if files[key] then
                    for sha, row in pairs(load_json(json, root .. "/" .. files[key])) do
                        local hit = { pack = pack, title = row.title, kind = row.kind or "clean",
                                      rom_type = def.rom_type[row.title] }
                        table_[sha:lower()] = hit
                        -- earlier stamps of this same canonical build (only the menu-version field
                        -- differs): still on players' cartridges, so they admit as this row
                        for _, old in ipairs(row.equivalent_sha1s or {}) do
                            if not table_[old:lower()] then table_[old:lower()] = hit end
                        end
                    end
                end
            end
        else
            for title, t in pairs(load_json(json, root .. "/" .. files.profile).titles) do
                if t.rom_sha1 and not table_[t.rom_sha1:lower()] then
                    table_[t.rom_sha1:lower()] = { pack = pack, title = title, kind = "clean",
                                                   rom_type = def.rom_type[title] }
                end
            end
        end
    end
    return table_
end

-- The pack file for an artifact kind: `<key>` for clean, `<key>_<base kind>` otherwise; a
-- randomized artifact (rand / rand_overlay) reads its base kind's files, and a header-named
-- vanilla family (run.lua's fallback for a patched vanilla cartridge, kind "named") reads the
-- clean ones — the companion patch adds code, it does not move WRAM.
Entry.BASE_KIND = { rand = "clean", rand_overlay = "overlay", named = "clean" }
local function pack_file(files, key, kind)
    local base = Entry.BASE_KIND[kind] or kind
    return base == "clean" and files[key] or files[key .. "_" .. base]
end
Entry.pack_file = pack_file

-- Anchor admission (A3 rand / rand_overlay). The UPR fork writes data tables only, so a
-- randomized artifact keeps every code byte: its sha1 is in no table, but every engine
-- site (+ prelude) and checkpoint slice of exactly one admitted pack/title/kind reads as
-- pinned. Candidates are the admission rows whose header_title matches; each is read slice
-- by slice through the flat ROM domain and dropped at the first differing byte. Only packs
-- that ship admission rows take part: a vanilla build with an unknown sha1 keeps run.lua's
-- named-family fallback.
local function anchor_specs(root, json, files, title, kind)
    local anchors = {}
    local sites = load_json(json, root .. "/" .. pack_file(files, "sites", kind)).titles[title].sites
    for _, site in pairs(sites) do
        anchors[#anchors + 1] = {offset=site.rom_offset, hex=site.expected_hex}
        if site.prelude then anchors[#anchors + 1] = {offset=site.prelude.rom_offset, hex=site.prelude.expected_hex} end
    end
    local ws = load_json(json, root .. "/" .. pack_file(files, "checkpoint", kind))[title].write_safe
    for key, hex in pairs(ws.expected_hex or {}) do
        anchors[#anchors + 1] = {offset=ws[key], hex=hex}
    end
    return anchors
end
local function anchors_hold(root, json, files, title, kind, read_u8, size)
    return admission_core(root).anchors_match(anchor_specs(root, json, files, title, kind),
                                             {read_u8=read_u8, size=size}, "anchors")
end
-- Every admitted (pack, title, base kind) whose anchors all hold in this ROM.
function Entry.anchor_matches(args, header)
    local seen, matches = {}, {}
    for pack, def in pairs(Entry.PACKS) do
        local files = Entry.PACK_FILES[pack]
        for _, key in ipairs({ "admission", "admission_overlay" }) do
            if files[key] then
                for _, row in pairs(load_json(args.json, args.root .. "/" .. files[key])) do
                    local kind = row.kind or "clean"
                    local id = pack .. "/" .. row.title .. "/" .. kind
                    if not seen[id] and row.header_title and header:find(row.header_title, 1, true) == 1 then
                        seen[id] = true
                        if anchors_hold(args.root, args.json, files, row.title, kind, args.read_rom_u8, args.rom_size) then
                            matches[#matches + 1] = { pack = pack, title = row.title, kind = kind,
                                                      rom_type = def.rom_type[row.title] }
                        end
                    end
                end
            end
        end
    end
    table.sort(matches, function(a, b) return a.pack .. a.title .. a.kind < b.pack .. b.title .. b.kind end)
    return matches
end

-- Decide the foundation for the loaded cartridge.
--   args.root, args.json           pack lookup
--   args.rom_sha1, args.indatabase  legacy diagnostics; neither bypasses actual-byte hashing
--   args.read_rom_u8, args.rom_size   required actual flat ROM domain for every admission
--   args.header                    the header title (Entry.detect_title's second value), for the reason
-- Returns { pack, title, kind, rom_type, rom_sha1, rehashed, admitted_by } or nil, reason;
-- admitted_by is "sha1" or "anchors" (kind rand / rand_overlay, rom_sha1 = the hash of the bytes).
-- Keep Gen 1 catalog/header/randomized policy here. The shared mechanism owns
-- actual acquisition, hashing, anchor validation, unique-match and immutable result.
local function admission_candidates(root, json)
    local by_id, candidates = {}, {}
    local function add(pack, title, kind, sha, header)
        local id = pack .. "/" .. title .. "/" .. kind
        local candidate = by_id[id]
        if not candidate then
            candidate = {pack=pack, title=title, kind=kind, hashes={}, headers={},
                rom_type=Entry.PACKS[pack].rom_type[title]}
            by_id[id], candidates[#candidates + 1] = candidate, candidate
        end
        candidate.hashes[#candidate.hashes + 1] = sha
        if header then candidate.headers[#candidate.headers + 1] = header end
    end
    for pack in pairs(Entry.PACKS) do
        local files = Entry.PACK_FILES[pack]
        if files.admission then
            for _, key in ipairs({"admission", "admission_overlay"}) do
                if files[key] then
                    for sha, row in pairs(load_json(json, root .. "/" .. files[key])) do
                        add(pack, row.title, row.kind or "clean", sha, row.header_title)
                        for _, equivalent in ipairs(row.equivalent_sha1s or {}) do
                            add(pack, row.title, row.kind or "clean", equivalent)
                        end
                    end
                end
            end
        else
            for title, profile in pairs(load_json(json, root .. "/" .. files.profile).titles) do
                if profile.rom_sha1 then add(pack, title, "clean", profile.rom_sha1) end
            end
        end
    end
    table.sort(candidates, function(a,b) return a.pack .. a.title .. a.kind < b.pack .. b.title .. b.kind end)
    return candidates
end
-- Patch-first (owner 2026-10-02): every title below REQUIRES the SLink companion patch, so Entry.admit
-- never admits its CLEAN artifacts: the pinned stock dump (kind clean), nor a randomized stock build
-- (an anchors match of a clean row, kind rand). Yellow has no free WRAM for the mailbox and is exempt,
-- like Archipelago builds (those are in no catalog). A patched Red/Blue is in no sha1 table either and
-- boots as the header-named family: admit_routed tells it from a stock one by the patch itself.
Entry.COMPANION_REQUIRED = { red = true, blue = true, purered = true, pureblue = true, puregreen = true }
local COMPANION_NEEDED = "needs the SLink companion patch"
local function companion_reason(title)
    return "this " .. tostring(title) .. " cartridge " .. COMPANION_NEEDED .. "; "
           .. "prepare it through the Manager or /patcher"
end
function Entry.admit(args)
    local actual_sha
    local ok, decision, why = pcall(function()
        local Admission = admission_core(args.root)
        local policy = {
            allow_unknown_hash=true, -- Gen 1's explicit existing pureRGB randomizer policy.
            acquire=function(request)
                return {size=request.rom_size, read_u8=request.read_rom_u8}
            end,
            catalog=function(request, artifact)
                actual_sha = artifact.sha1
                return admission_candidates(request.root, request.json)
            end,
            hashes=function(candidate) return candidate.hashes end,
            eligible=function(candidate, mode, _request, artifact)
                if candidate.kind == "clean" and Entry.COMPANION_REQUIRED[candidate.title] then
                    return false, companion_reason(candidate.title)
                end
                if mode == "sha1" then return true end
                local header = Entry.header_title(artifact.read_u8)
                for _, expected in ipairs(candidate.headers) do
                    if header:find(expected, 1, true) == 1 then return true end
                end
                return false, "header does not select an anchor-admitted Gen 1 candidate"
            end,
            anchors=function(candidate, mode, request)
                if mode == "sha1" then return {} end -- exact catalog hash pins every byte
                return anchor_specs(request.root, request.json, Entry.PACK_FILES[candidate.pack],
                                    candidate.title, candidate.kind)
            end,
            kind=function(candidate, mode)
                if mode == "sha1" then return candidate.kind end
                return candidate.kind == "overlay" and "rand_overlay" or "rand"
            end,
            describe=function(candidate, kind)
                return {pack=candidate.pack, title=candidate.title, kind=kind, rom_type=candidate.rom_type}
            end,
        }
        return Admission.new(policy):admit(args)
    end)
    if not ok then why, decision = tostring(decision), nil end
    if decision then return decision end
    local header, sha = tostring(args.header or ""), actual_sha or tostring(args.rom_sha1 or "")
    if tostring(why):find(COMPANION_NEEDED, 1, true) then return nil, tostring(why) end
    if tostring(why):find("ambiguous", 1, true) then
        return nil, "ambiguous: header " .. header .. ": sha1 " .. sha .. ": " .. tostring(why)
    end
    if header:find("POKEMON RED", 1, true) or header:find("POKEMON BLUE", 1, true)
        or header:find("POKEMON GREEN", 1, true) then
        return nil, "header " .. header .. " matches no admitted cartridge (a pureRGB or vanilla build): sha1 "
            .. sha .. ": " .. tostring(why)
    end
    return nil, "header " .. header .. " is not an admitted Gen 1 cartridge: sha1 " .. sha .. ": " .. tostring(why)
end
-- THE companion detector for a vanilla Red/Blue cartridge (also called by lua/tests harnesses). The
-- companion's bank-$3F module writes the 'SLNK' beacon into its mailbox, and that code is in the ROM
-- from frame 0 -- unlike the beacon itself, which only exists in WRAM once frames have run, so an
-- admission decision cannot see it. The writer is `ld a, "S" / ld [mailbox], a` = 3E 53 EA lo hi
-- (patch/gen1/tools/inject.py _BEACON_WRITER emits it). A stock cartridge, randomized or not, has
-- no such bytes in bank $3F. `mailbox` defaults to the shipped vanilla patch's (PACKS.gen1_rby.trade).
-- The pureRGB overlay's own mailbox (patch/gen1/purergb/overlay/slink.asm:20), not the vanilla patch's $DEE2.
Entry.PURE_MAILBOX = 0xDEEA
function Entry.has_companion_beacon(read_rom_u8, mailbox)
    if not read_rom_u8 then return false end
    mailbox = mailbox or Entry.PACKS.gen1_rby.trade.mailbox
    local want = { 0x3E, 0x53, 0xEA, mailbox & 0xFF, (mailbox >> 8) & 0xFF }
    local first = 0x3F * 0x4000                       -- ROM offset of bank $3F, the last bank of 1 MiB
    for offset = first, first + 0x4000 - #want do
        if read_rom_u8(offset) == want[1] then
            local hit = true
            for i = 2, #want do
                if read_rom_u8(offset + i - 1) ~= want[i] then hit = false; break end
            end
            if hit then return true end
        end
    end
    return false
end

-- The LAUNCHER's admission (lua/gen1/run.lua): Entry.admit, the vanilla named-family fallback,
-- and the companion rule. One place, so a caller that dofiles run.lua and a lupa test are held
-- to the same gate. args are Entry.admit's plus args.family (Entry.detect_title's first value);
-- args.log is optional. Returns the admitted table or nil, reason.
function Entry.admit_routed(args)
    local log = args.log or function() end
    local admitted, why = Entry.admit(args)
    -- A refusal for lack of the companion is final: never retried as the named family below.
    if not admitted and tostring(why):find(COMPANION_NEEDED, 1, true) then return nil, tostring(why) end
    if not admitted then
        -- The vanilla companion-patch and randomized artifacts are vanilla-layout by construction
        -- and predate the admission table (their sha1s are not pinned anywhere): a recognised
        -- vanilla header still boots the vanilla pack exactly as before admission existed
        -- (kind "named"). A non-pinned pureRGB build carries the same header and DOES reach this
        -- path (its sha1 and anchors admit nothing): it is then refused by the vanilla pack's own
        -- engine-site verification (signals.lua "engine sites differ from the ROM"), never booted
        -- against the wrong addresses. The pinned pure builds never get here.
        local family = args.family
        if family == "red" or family == "blue" or family == "yellow" then
            log("[SLink-gen1] no admission row for this cartridge (" .. tostring(why) .. "); booting the vanilla "
                .. family .. " pack by header (named family)")
            admitted = { title = family, pack = "gen1_rby", kind = "named",
                         rom_sha1 = tostring(args.rom_sha1 or ""):lower() }
        else
            return nil, tostring(why)
        end
    end
    -- The named family is a vanilla cartridge with no admission row: a patched Red/Blue (its bank-$3F
    -- beacon writer is what makes the cartridge a companion one) or a stock one. Only the patched one
    -- boots; the server asks the same question again from the hello's `panel`.
    if admitted.kind == "named" and Entry.COMPANION_REQUIRED[admitted.title]
       and not Entry.has_companion_beacon(args.read_rom_u8) then
        -- a pureRGB overlay shares the vanilla header but writes its beacon to ITS mailbox ($DEEA, review F6):
        -- an unpinned pure overlay must not be told to "patch your red cartridge"
        if Entry.has_companion_beacon(args.read_rom_u8, Entry.PURE_MAILBOX) then
            return nil, "this is a pureRGB overlay build that is not a pinned release, so it is not admitted"
        end
        return nil, companion_reason(admitted.title)
    end
    return admitted
end

-- ── build ────────────────────────────────────────────────────────────────────────────

-- Reads of $D000-$DFFF go through the flat WRAM domain (bank 1 at 0x1000) when the profile
-- says the foundation banks WRAM (derived.wram_bank_gate): System Bus follows the live
-- SVBK mapping and pureRGB's palette fade runs with bank 2 selected (PLAN §4 row 13, Live 3).
-- Writes are NOT rerouted: writes.lua refuses them outside banks {0,1} instead.
local function bank_safe_io(bio, d)
    if not d.wram_bank_gate then return bio end
    local LO, HI, FLAT = 0xD000, 0xDFFF, 0x1000
    local function is_bus(dom) return dom == nil or dom == "System Bus" end
    local wrapped = {}
    for k, v in pairs(bio) do wrapped[k] = v end
    wrapped.read_u8 = function(addr, dom)
        if is_bus(dom) and addr >= LO and addr <= HI then return bio.read_u8(FLAT + (addr - LO), "WRAM") end
        return bio.read_u8(addr, dom)
    end
    wrapped.read_range = function(addr, len, dom)
        if is_bus(dom) and addr >= LO and addr + len - 1 <= HI then
            return bio.read_range(FLAT + (addr - LO), len, "WRAM")
        end
        return bio.read_range(addr, len, dom)
    end
    return wrapped
end
Entry.bank_safe_io = bank_safe_io
-- A bus reader for the test harness: the same rerouting, unconditionally (the flat WRAM domain
-- holds bank 1 at 0x1000 in DMG mode too), so no driver reads banked WRAM through the System Bus.
function Entry.harness_bus_u8()
    local bio = bank_safe_io(Entry.bizhawk_deps(), { wram_bank_gate = true })
    return function(addr) return bio.read_u8(addr, "System Bus") end
end

function Entry.build(deps)
    local root = assert(deps.root, "deps.root required")
    local L = function(rel) return dofile(root .. "/" .. rel) end
    local json = L("lua/json_codec.lua")
    local R, W, B, Rom = L("lua/gen1/reads.lua"), L("lua/gen1/writes.lua"),
                         L("lua/gen1/boxes.lua"), L("lua/gen1/rom.lua")
    local T, P = L("lua/gen1/trade_overlay.lua"), L("lua/gen1/panel.lua")
    local Safety = L("lua/gen1_write_safety.lua")
    local Permit = L("lua/write_permit.lua")
    local Checkpoint = L("lua/gb_checkpoint.lua")
    local Scanner = L("lua/token_scanner.lua")
    local HelloSession, ReplyDispatch = L("lua/hello_session.lua"), L("lua/reply_dispatch.lua")
    local OwedReports = L("lua/owed_reports.lua")
    if not shared_signals then
        shared_signals = L("lua/gen1/signals.lua").bind({registry=L("lua/hook_registry.lua"),
                                                         gb_binding=L("lua/gb_hook_binding.lua"), owner="SLink-gen1"})
    end
    local S = shared_signals
    local safety = Safety.new(Checkpoint)
    local Client = L("lua/gen1/client.lua")

    local pack = deps.pack or "gen1_rby"
    local pack_def = assert(Entry.PACKS[pack], "unknown pack " .. tostring(pack))
    local files = Entry.PACK_FILES[pack]
    local title = assert(deps.title, "deps.title required")
    -- the pack file for this artifact kind: profile.json for clean/rand, profile_overlay.json
    -- for overlay/rand_overlay
    local kind = deps.kind or "clean"
    local function kind_file(key)
        local rel = pack_file(files, key, kind)
        return root .. "/" .. assert(rel, pack .. " ships no " .. key .. " for artifact kind " .. kind)
    end
    local profile = assert(load_json(json, kind_file("profile")).titles[title], "unknown title " .. title)
    local sites = assert(load_json(json, kind_file("sites")).titles[title]).sites
    local write_checkpoint = assert(load_json(json, kind_file("checkpoint"))[title])
    local area_map = load_json(json, root .. "/" .. files.area_map)
    -- static (scripted, fixed-species) encounters get their own area id; generated from pret
    local statics = load_json(json, root .. "/" .. files.statics).statics
    -- the pack's glyph table (charmap.lua) and species index, when it ships them
    if files.charmap then profile.charmap = dofile(root .. "/" .. files.charmap) end
    local species_index = files.species_index and load_json(json, root .. "/" .. files.species_index) or nil
    -- companion-patch/overlay addresses: the pack's own block, else the shipped vanilla patch
    profile.trade = profile.trade or pack_def.trade

    local bio = bank_safe_io(deps.io, profile.derived)
    -- reads: System Bus, no domain argument
    local reads_io = {
        read_u8 = function(addr) return bio.read_u8(addr, "System Bus") end,
        read_range = function(addr, len) return bio.read_range(addr, len, "System Bus") end,
    }
    local reads = R.new(profile, reads_io, Scanner)
    profile.charmap = reads.charmap -- the ONE glyph object; the panel reads it from here
    local writes = W.new(profile, bio, Permit)
    -- boxes write through the same armed gate; CartRAM is the flat SRAM image
    local box_io = {
        read_range = reads_io.read_range,
        read_cart = function(off) return bio.read_u8(off, "CartRAM") end,
        write_bytes = function(addr, bytes) return writes:write_bytes(addr, bytes) end,
        write_cart_bytes = function(off, bytes)
            return writes:write_cart_bytes(off, bytes)
        end,
    }
    local boxes = B.new(profile, reads, box_io)
    local rom = Rom.new(profile, bio, species_index)
    local trade, panel
    if profile.trade then
        trade = T.new(profile, reads_io, writes)
        -- the panel reads the mailbox every frame and needs the clock for its own deadline
        local panel_io = { read_u8 = reads_io.read_u8, framecount = function() return bio.framecount() end }
        panel = P.new(profile, panel_io, writes, deps.hud and deps.hud.sanitize or function(s) return s end)
    end

    local client = Client.new({
        reads = reads, signals = S, writes = writes, boxes = boxes, rom = rom, safety = safety,
        net = deps.net, json = json, hud = deps.hud, io = bio,
        profile = profile, sites = sites, write_checkpoint = write_checkpoint, area_map = area_map,
        statics = statics, trade = trade, panel = panel,
        player = assert(deps.player, "deps.player required"), rom_type = pack_def.rom_type[title],
        rom_sha1 = deps.rom_sha1, log = deps.log or function() end,
        foundation = pack, artifact_kind = deps.kind or "clean",
        hello_session = HelloSession, reply_dispatch = ReplyDispatch, owed_reports = OwedReports,
    })
    return client, { profile = profile, sites = sites, reads = reads, writes = writes, boxes = boxes,
                     rom = rom, json = json, panel = panel, box_io = box_io, pack = pack,
                     signals = S }
end

-- Header family from the cartridge header (ROM $0134..$0143): "POKEMON RED"/"BLUE"/"YELLOW"/
-- "GREEN". This NARROWS candidates and gates the launcher route; admission is Entry.admit.
-- PureRed/PureBlue share vanilla's header bytes, so "red" here never means vanilla Red.
function Entry.header_title(read_rom_u8)
    local chars = {}
    for i = 0, 15 do
        local b = read_rom_u8(0x134 + i)
        if b == 0 then break end
        chars[#chars + 1] = string.char(b)
    end
    return table.concat(chars)
end
function Entry.detect_title(read_rom_u8)
    local name = Entry.header_title(read_rom_u8)
    if name:find("RED", 1, true) then return "red" end
    if name:find("BLUE", 1, true) then return "blue" end
    if name:find("YELLOW", 1, true) then return "yellow" end
    if name:find("GREEN", 1, true) then return "green" end
    return nil, name
end

function Entry.bizhawk_deps()
    local function dom(d) return d or "System Bus" end
    return {
        read_u8 = function(addr, d) return memory.read_u8(addr, dom(d)) end,
        read_range = function(addr, len, d)
            local out = {}
            for i = 1, len do out[i] = memory.read_u8(addr + i - 1, dom(d)) end
            return out
        end,
        write_u8 = function(addr, v, d) return memory.write_u8(addr, v, dom(d)) end,
        on_bus_exec = function(fn, addr, name, d) return event.on_bus_exec(fn, addr, name, dom(d)) end,
        unregister = function(id) return event.unregisterbyid(id) end,
        framecount = function() return emu.framecount() end,
        register = function(name) return emu.getregister(name) end,
        set_register = function(name, value) return emu.setregister(name, value) end,
        domains = function() return memory.getmemorydomainlist() end,
        saveram = function() if client and client.saveram then return client.saveram() end end,
    }
end

return Entry
