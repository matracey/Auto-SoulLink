--[[
  lua/slink.lua — Universal SLink Entry Point
  ============================================
  Auto-detects the loaded ROM and starts the appropriate game client.
  The launcher sets SLINK_HOST, SLINK_PORT, SLINK_PLAYER as globals before
  running this file — those are consumed by whichever client gets loaded.
--]]

local _dir = debug.getinfo(1, "S").source:match([=[@(.+[/\])]=]) or ""
package.path = _dir .. "?.lua;" .. _dir .. "?/init.lua;" .. package.path

-- ── Console tee ──────────────────────────────────────────────────────────────
-- BizHawk's Lua console scrolls and drops old lines, so when something logs
-- a lot at startup (BizHawk's "Unable to find domain" warnings, our diagnostic
-- output, etc.) the user can't scroll back far enough to copy it. Mirror every
-- console.log() call to slink_lua.log in the project root for post-mortem.
-- The log is truncated on each run so old content doesn't accumulate.
do
    local log_path = _dir .. "../slink_lua.log"
    -- Close any handle left open by a previous script-load so reloads don't leak.
    if _G.__slink_log_fh then
        pcall(function() _G.__slink_log_fh:close() end)
        _G.__slink_log_fh = nil
    end
    -- Open once for the lifetime of this run (truncate-at-boot via "w").
    -- Keeping the handle open avoids per-call open/close, which is several ms
    -- per log line on Drive-synced paths and a primary source of event-frame stutter.
    local fh = io.open(log_path, "w")
    if fh then
        fh:write(string.format("=== SLink Lua boot %s ===\n", os.date()))
        fh:flush()
        _G.__slink_log_fh = fh
        _G.__slink_original_log = _G.__slink_original_log or console.log
        local orig_log = _G.__slink_original_log
        console.log = function(...)
            local h = _G.__slink_log_fh
            if h then
                local parts = {...}
                for i = 1, select("#", ...) do
                    parts[i] = tostring(parts[i])
                end
                local ok = pcall(function()
                    h:write(table.concat(parts, "\t") .. "\n")
                    h:flush()
                end)
                if not ok then
                    -- Drop the handle on first write failure so we don't
                    -- hot-loop reopening; reload the script to recover.
                    pcall(function() h:close() end)
                    _G.__slink_log_fh = nil
                end
            end
            orig_log(...)
        end
        console.log("[SLink] Console tee -> " .. log_path)
    end
end

-- ── Gen 1 route ──────────────────────────────────────────────────────────────
-- Red/Blue/Yellow run the rewritten client under lua/gen1/, which owns its own
-- cartridge detection (entry.lua Entry.detect_title). It is checked before the
-- game_detect registry so the Gen 1 entry is never decided in two places; Gen 2-5
-- fall through unchanged. emu is indexed inside the pcall because a broken/absent
-- core would otherwise error before the guard could refuse.
do
    local sys_ok, sys = pcall(function() return emu.getsystemid() end)
    -- "SGB" is what EmuHawk reports when GbAsSgb is on (Gambatte in Super Game Boy mode,
    -- opt-in, off in the stock config); the cartridge is the same Gen 1 ROM.
    if sys_ok and (sys == "GB" or sys == "GBC" or sys == "SGB") then
        local Entry = dofile(_dir .. "gen1/entry.lua")
        if Entry.detect_title(function(addr) return memory.read_u8(addr, "ROM") end) then
            -- The engine hooks (gen1/signals.lua) are only proven on 2.11.x; on 2.9.1 they
            -- latched a failure and every capture/battle went unreported (live run 2026-09-22).
            local ver = tostring(client.getversion and client.getversion() or "?")
            local maj, min = ver:match("^(%d+)%.(%d+)")
            if not maj or tonumber(maj) * 100 + tonumber(min) < 211 then
                error("[SLink] BizHawk " .. ver .. " is too old for Gen 1 -- install BizHawk 2.11 or newer", 0)
            end
            dofile(_dir .. "gen1/run.lua")
            return
        end
    end
end

-- ── Gen 2 route ──────────────────────────────────────────────────────────────
-- Crystal, Gold and Silver all run the rewritten client under lua/gen2/, which owns its
-- own cartridge detection (entry.lua Entry.detect_title) -- routed by TITLE, not by
-- per-title admission: run.lua itself admits or refuses through Entry.build (Entry.admit
-- is sha1-first, never by header), so a recognised title always reaches it and an
-- unadmitted build (a PENDING revision, or any unknown hash) is refused there with no
-- fallback (O-22/O-23, docs/gen2/reviews/OMP_U5_CUTOVER_FACTS_2026-09-23.md).
--
-- Every other Game Boy cartridge is REFUSED here, by name, before game_detect: the legacy
-- Gen 2 client that used to catch them is gone (P3b.8), and game_detect holds no Game Boy
-- module any more. The Archipelago fork's header ("AP_CRYSTAL") gets its own message:
-- owner ruling O-25 refuses Archipelago Crystal (docs/gen2/REVIEW_RECORD.md).
do
    local sys_ok, sys = pcall(function() return emu.getsystemid() end)
    if sys_ok and (sys == "GB" or sys == "GBC" or sys == "SGB") then
        local Entry = dofile(_dir .. "gen2/entry.lua")
        local title, header = Entry.detect_title(function(addr) return memory.read_u8(addr, "ROM") end)
        if title then
            dofile(_dir .. "gen2/run.lua")
            return
        end
        header = header or ""
        if header:sub(1, 10) == "AP_CRYSTAL" then
            error("[SLink] This Crystal build is not supported -- load a vanilla "   -- AP_CRYSTAL, owner ruling O-25
                  .. "Gold, Silver or Crystal cartridge", 0)
        end
        error("[SLink] Unsupported Game Boy cartridge " .. string.format("%q", header)
              .. " -- SLink runs Red, Blue, Yellow, Gold, Silver and Crystal", 0)
    end
end

-- ── Gen 3 route ──────────────────────────────────────────────────────────────
-- FireRed/LeafGreen (pack gen3_frlg), Radical Red (pack gen3_rr) and Emerald (pack
-- gen3_emerald, EG4) admitted by HASH or ANCHORS run under lua/gen3/, the rewritten client.
-- The old Gen 3 client was archived at C5-6 (tag archive/gen3-old-client, owner ruling 24):
-- every other GBA cartridge -- the Archipelago FireRed/LeafGreen builds, a header-only
-- admission (an unpinned hack or a bad dump that merely says BPRE/BPGE/BPEE) -- is refused
-- here by name, never handed to game_detect. RR carries FireRed's header code, so a
-- header-only admission is refused rather than routed: the new client's site check would
-- refuse it anyway, less legibly.
--
-- The routed set lives in entry.lua (Entry.ROUTED); the launcher keeps no copy of it.
do
    local sys_ok, sys = pcall(function() return emu.getsystemid() end)
    -- Fail closed: an unidentifiable system must never fall through to game_detect (the Gen 5
    -- registry), which could silently misroute a GBA cartridge that briefly failed to identify
    -- itself. GB/GBC/SGB (Gen 1 and Gen 2) and NDS (Gen 4, refused or routed above; Gen 5, the only
    -- row left in that registry) are the only known systems it is ever asked to route.
    if not sys_ok then
        error("[SLink] could not determine the loaded system (emu.getsystemid failed): "
              .. tostring(sys), 0)
    elseif sys ~= "GBA" and sys ~= "GB" and sys ~= "GBC" and sys ~= "SGB" and sys ~= "NDS" then
        error("[SLink] could not determine the loaded system: emu.getsystemid() returned "
              .. tostring(sys) .. " (expected GB, GBC, SGB, GBA or NDS)", 0)
    end
    if sys == "GBA" then
        -- Admission itself is isolated in a pcall so an admission error becomes a named
        -- refusal. The BizHawk-version guard below is deliberately OUTSIDE this pcall so it
        -- propagates like the Gen 1 route's does. Entry.admit_routed folds in the launcher's
        -- own policy (header-only admissions and packs not in Entry.ROUTED are refused, not
        -- routed) so lua/gen3/run.lua enforces the identical gate when a caller dofiles it
        -- directly.
        local admit_ok, Entry, admitted, why = pcall(function()
            local E = dofile(_dir .. "gen3/entry.lua")
            assert(E, "lua/gen3/entry.lua did not load")
            local function rom_read(off, n)
                local t = {}
                for i = 1, n do t[i] = memory.read_u8(off + i - 1, "ROM") end
                return t
            end
            local hash = gameinfo and gameinfo.getromhash and gameinfo.getromhash() or ""
            local ok_hc, hc = pcall(E.header_code, rom_read)
            local header_code = ok_hc and hc or ""
            local a, reason = E.admit_routed({ root = _dir .. "..", json = dofile(_dir .. "json_codec.lua"),
                                        rom_hash = hash, rom_read = rom_read,
                                        header_code = header_code })
            return E, a, reason
        end)
        if admit_ok and Entry and admitted then
            -- The engine hooks are only proven on 2.11.x (same guard as the Gen 1 route,
            -- slink.lua Gen 1 block above).
            local ver = tostring(client.getversion and client.getversion() or "?")
            local maj, min = ver:match("^(%d+)%.(%d+)")
            if not maj or tonumber(maj) * 100 + tonumber(min) < 211 then
                error("[SLink] BizHawk " .. ver .. " is too old for Gen 3 -- install BizHawk 2.11 or newer", 0)
            end
            dofile(_dir .. "gen3/run.lua")
            return
        end
        local what = admit_ok and tostring(why) or ("admission failed (" .. tostring(Entry) .. ")")
        error("[SLink] Unsupported Gen 3 cartridge: " .. what
              .. ". Supported: FireRed, LeafGreen, Radical Red and Emerald.", 0)
    end
end

-- ── Gen 4 route ──────────────────────────────────────────────────────────────
-- HeartGold and SoulSilver (pack gen4_hgss) and the hg-engine HeartGold build (pack gen4_hge),
-- admitted by HASH, run under lua/gen4/, the rewritten client. The legacy Gen 4 client is deleted
-- (G3a, docs/gen4/reviews/DECISIONS_2026-10-03_launcher.md), so a recognised-but-unpinned
-- cartridge -- a hack, another region, or a bad dump that still carries a Gen 4 header -- is
-- REFUSED here by name and never handed to game_detect: after G3a no module there can route it
-- (the games.gen4_hgsspt row is gone), and "no module matched" is not an admission decision.
--
-- No Gen 4 header code is listed here: the set is read out of Entry.admission_table, the same
-- table Entry.admit_routed consults, so the packs stay the only place a Gen 4 cartridge is named.
-- That costs a second decode of the packs, so it is built only on the refusal path -- an admitted
-- cartridge decodes them once, inside admit_routed.
--
-- Gen 5 (Black / White / BW2) is the one NDS route still delegated to game_detect, and only
-- Gen 5: an NDS cartridge that pins no Gen 4 header is not this block's to judge. game_detect
-- routes it, or refuses it by name without advertising Platinum.
do
    local sys_ok, sys = pcall(function() return emu.getsystemid() end)
    if sys_ok and sys == "NDS" then
        -- Named, not a bare dofile traceback: a missing codec is a broken release, and this is
        -- the player-readable shape every other refusal here takes.
        local ok_json, json = pcall(dofile, _dir .. "json_codec.lua")
        if not ok_json then
            error("[SLink] could not load lua/json_codec.lua: " .. tostring(json), 0)
        end
        -- Where the NDS boot leaves the cartridge header copy, and which bus the vanilla anchor
        -- bytes are read over, are PLATFORM facts: take them from the client's own table (run.lua
        -- reads them the same way) instead of repeating an address in the launcher.
        local ok_admit, Entry, admitted, why, header_code = pcall(function()
            local E = dofile(_dir .. "gen4/entry.lua")
            assert(E, "lua/gen4/entry.lua did not load")
            local Client = dofile(_dir .. "gen4/client.lua")
            local plat = assert(Client and Client.PLATFORM,
                                "lua/gen4/client.lua ships no platform facts")
            local function ram_read(addr, len)
                local out = {}
                for i = 1, len do out[i] = memory.read_u8(addr + i - 1, plat.bus_domain) end
                return out
            end
            local ok_hc, hc = pcall(E.header_code, function(off, len)
                return ram_read(plat.header_copy + off, len)
            end)
            local code = ok_hc and hc or ""
            local hash = gameinfo and gameinfo.getromhash and gameinfo.getromhash() or ""
            local a, reason = E.admit_routed({ root = _dir .. "..", json = json, rom_hash = hash,
                                               header_code = code, read_ram = ram_read })
            return E, a, reason, code
        end)
        if ok_admit and Entry and admitted then
            -- The Gen 4 hooks are the same on_bus_exec path the Gen 1 live run found silently
            -- latching on 2.9.1 (the Gen 1 block above): refuse an emulator we have never seen
            -- carry them rather than start a client that reports nothing. Gen 4 itself has never
            -- run, so this floor is inherited from that run, not measured here.
            local ver = tostring(client.getversion and client.getversion() or "?")
            local maj, min = ver:match("^(%d+)%.(%d+)")
            if not maj or tonumber(maj) * 100 + tonumber(min) < 211 then
                error("[SLink] BizHawk " .. ver .. " is too old for Gen 4 -- install BizHawk 2.11 or newer", 0)
            end
            dofile(_dir .. "gen4/run.lua")
            return
        end
        local code = header_code or ""
        local what = ok_admit and tostring(why) or ("admission failed (" .. tostring(Entry) .. ")")
        local ok_codes, codes = pcall(function()
            local set = {}
            for _, row in pairs(Entry.admission_table({ root = _dir .. "..", json = json })) do
                -- a set, not header -> pack: two packs pin the same header (IPKE is HGSS and hge
                -- both), and a table's iteration order must not decide what the message says.
                if row.header_code then set[row.header_code] = true end
            end
            return set
        end)
        if ok_codes and codes[code] then
            error("[SLink] Unsupported Gen 4 cartridge (header " .. code .. "): " .. what
                  .. ". Supported on NDS: HeartGold, SoulSilver and the hg-engine HeartGold build."
                  .. " Use an unmodified pinned cartridge.", 0)
        end
        -- Falling through is the right call when this block cannot tell whose cartridge it is
        -- (Gen 5 is game_detect's), but the reason must not be lost: game_detect's own message
        -- would otherwise say Gen 4 is "routed or refused by lua/slink.lua" when it was not.
        -- Console only, like the Gen 1 route's engine-signal diagnostics.
        if not ok_admit then
            console.log("[SLink] Gen 4 admission machinery failed (" .. tostring(Entry)
                        .. "); handing the cartridge to game detection")
        elseif code == "" then
            console.log("[SLink] could not read the NDS cartridge header copy ("
                        .. "UNVERIFIED LIVE: lua/gen4/client.lua PLATFORM.header_copy); "
                        .. "handing the cartridge to game detection")
        end
    end
end

-- Detect which game is loaded
package.loaded["game_detect"]       = nil
local game_detect = require("game_detect")
local detected    = game_detect.detect()

-- Map game_id to client script path
-- No Game Boy or GBA rows, and no Gen 4 row: the Gen 1, Gen 2, Gen 3 and Gen 4 routes above return
-- or refuse for every GB/GBC/SGB/GBA core and for every Gen 4 NDS cartridge before this table is
-- reached (the legacy gen2_crystal row went with P3b.8, the old gen3_frlge row with C5-6, the
-- gen4_hgsspt row with G3a). Gen 5 is the only route game_detect still owns.
local _CLIENT_MAP = {
    gen5_bw       = "clients/gen5_bw_client.lua",
}

local client_path = _CLIENT_MAP[detected.game_id]
if not client_path then
    error("[SLink] No client available for detected game: " .. detected.game_id
          .. " (" .. detected.module.display_name .. ")")
end

dofile(_dir .. client_path)
