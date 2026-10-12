--[[
  lua/clients/gen5_bw_client.lua — SLink Soul Link Nuzlocke Client
  Gen 5: Pokémon Black / White / Black 2 / White 2 (NDS, BizHawk)

  EVENTS DETECTED AUTOMATICALLY
    hello        — on TCP connect / reconnect (party snapshot)
    area_enter   — zone ID changes to a mapped encounter zone
    capture      — new PID appears in party (battle context or gift)
    faint        — party mon HP transitions from > 0 to 0
    whiteout     — all living party mons transition to HP = 0
    no_catch     — wild battle ends without capture (gated by nuzlocke_active)
    safe         — first overworld frame after battle
    tick         — every 30 frames; carries ball_count + party snapshot

  COMMANDS DISPATCHED
    force_faint  — write HP = 0 to matching party slot (immediate; gated by writes_enabled)
    memorialize  — copy dead Pokémon's BoxPokemon to Box 24 (deferred: safe state)
    box_mon      — deposit partner's linked mon to PC (deferred: safe state)
    party_mon    — retrieve partner's linked mon from PC (deferred: safe state)
    pending_sync — logged as console warning

  Manual F keys:
    F1  → area_enter (current area_id)
    F2  → capture    (party slot 0)
    F3  → faint      (party slot 0)
    F4  → no_catch   (current area_id)
    F5  → whiteout
    F6  → safe
    F7  → tick       (with party snapshot)
    F8  → party_to_box (party slot 0)
    F9  → memorialize  (party slot 0 → Box 24, Lua-only)

  Identity key: "PID:OTID" — 8-hex-char personality ID + 8-hex-char OT ID.
  PKM struct: 220 bytes (0xDC); same LCRNG encryption as Gen 4.
  Battle stats at same offsets (+0x88) as Gen 4 — no memory_nds changes needed.

  Addresses confirmed via:
    PC_STORAGE_BASE  — Wi-Fi-Labs/PokeRNG-LuaScripts BW/B2W2 RNG scripts (boxAddr)
    BALLS_POCKET_OFF — PKHeX PlayerBag5BW.cs (Block25+0x000 = Items pocket)
    PLAYER_NAME_OFF  — Wi-Fi-Labs RNG scripts (trainerIDsAddr − 0x14 + 0x04)
  NOTE: PC_CURRENT_BOX_OFF is still estimated (0x17D80 = 24 × 0xFA0); VERIFY_ME via BizHawk.
--]]

-- ── CONFIGURE ─────────────────────────────────────────────────────────────────
local SERVER_HOST = SLINK_HOST   or "127.0.0.1"
local SERVER_PORT = SLINK_PORT   or 54321
local PLAYER_ID   = SLINK_PLAYER or "a"    -- "a" = Player 1, "b" = Player 2
SLINK_HOST = nil; SLINK_PORT = nil; SLINK_PLAYER = nil
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Module loading ─────────────────────────────────────────────────────────────
local _src = debug.getinfo(1, "S").source:match("@(.+[/\\])") or ""
local _lua_root = _src:match("(.+[/\\])clients[/\\]") or _src
local _proj_root = _lua_root:match("(.+[/\\])lua[/\\]") or (_lua_root .. "../")
package.path = _lua_root .. "?.lua;"
            .. _lua_root .. "games/?.lua;"
            .. _proj_root .. "data/games/gen5_bw/?.lua;"
            .. _src .. "?.lua;"
            .. package.path
package.loaded["memory_nds"]             = nil
package.loaded["gen5_bw_areas"]          = nil
package.loaded["gen5_bw_locations"]      = nil
package.loaded["connector"]              = nil
package.loaded["socket"]                 = nil
package.loaded["hud"]                    = nil

local M           = require("memory_nds")
local GAME_MODULE = require("gen5_bw")    -- profiles, gift areas, variant detection
local C           = require("connector")
local HUD         = require("hud")

local AREAS     = require("gen5_bw_areas")
local LOCATIONS = require("gen5_bw_locations")

-- ── Localized hot-path globals ────────────────────────────────────────────────
local _RAM = "Main RAM"
local function mem_u8 (a)   return memory.read_u8      (a, _RAM) end
local function mem_u16(a)   return memory.read_u16_le  (a, _RAM) end
local function mem_u32(a)   return memory.read_u32_le  (a, _RAM) end
local function mem_w16(a,v) return memory.write_u16_le (a, v, _RAM) end
local fmt     = string.format

-- ── Variant detection ─────────────────────────────────────────────────────────
local _ROM_TYPE = "pokemon_black"  -- default
do
    local variant = GAME_MODULE.detect_variant()
    _ROM_TYPE = GAME_MODULE.rom_type_for_variant(variant)
    console.log(fmt("[SLink] ROM variant: %s", variant))

    local profile = GAME_MODULE.profiles[variant]
    if profile then
        M.applyProfile(profile)
    else
        console.log("[SLink] WARNING: No memory profile for '" .. variant .. "' — keeping defaults")
    end
end

-- ── JSON encoder ──────────────────────────────────────────────────────────────
local _json_esc = {['\\']='\\\\', ['"']='\\"', ['\n']='\\n'}
local function json_encode(val)
    local t = type(val)
    if val == nil         then return "null"
    elseif t == "boolean" then return val and "true" or "false"
    elseif t == "number"  then
        if val == val and val % 1 == 0 and val >= -2147483648 and val <= 2147483647 then
            return fmt("%d", val)
        end
        return tostring(val)
    elseif t == "string"  then
        return '"' .. val:gsub('[\\"\n]', _json_esc) .. '"'
    elseif t == "table" then
        local n = #val
        local is_arr = (n > 0)
        if is_arr then
            local cnt = 0
            for _ in pairs(val) do cnt = cnt + 1; if cnt > n then is_arr = false; break end end
            if cnt ~= n then is_arr = false end
        end
        local p, pn = {}, 0
        if is_arr then
            for i = 1, n do pn = pn + 1; p[pn] = json_encode(val[i]) end
            return "[" .. table.concat(p, ",") .. "]"
        else
            for k, v in pairs(val) do
                pn = pn + 1
                p[pn] = '"' .. tostring(k) .. '":' .. json_encode(v)
            end
            return "{" .. table.concat(p, ",") .. "}"
        end
    else return '"['..t..']"' end
end

-- ── Response parsing ──────────────────────────────────────────────────────────
local function parse_command_list(raw)
    local cmds = {}
    local arr = raw:match('"commands"%s*:%s*(%b[])')
    if not arr then return cmds end
    for obj in arr:gmatch('%b{}') do
        local cmd   = obj:match('"cmd"%s*:%s*"([^"]+)"')
        local key   = obj:match('"key"%s*:%s*"([^"]+)"')
        local msg   = obj:match('"message"%s*:%s*"([^"]*)"')
        local text  = obj:match('"text"%s*:%s*"([^"]*)"')
        local r     = tonumber(obj:match('"r"%s*:%s*(%d+)'))
        local g     = tonumber(obj:match('"g"%s*:%s*(%d+)'))
        local b     = tonumber(obj:match('"b"%s*:%s*(%d+)'))
        local frames = tonumber(obj:match('"frames"%s*:%s*(%d+)'))
        local fb    = obj:match('"fb"%s*:%s*"([^"]*)"')   -- msgbox fallback style (prompt/hud)
        -- The server attaches the cached stat block to party_mon (state.py mon_stats).
        -- exec_party_mon falls back to `stats.level or 5` / `stats.maxHP or 1`, so
        -- without parsing this every restored mon came back at level 5 with 1 max HP.
        local stats = nil
        local sj = obj:match('"stats"%s*:%s*(%b{})')
        if sj then
            stats = {
                level   = tonumber(sj:match('"level"%s*:%s*(%d+)')),
                maxHP   = tonumber(sj:match('"maxHP"%s*:%s*(%d+)')),
                attack  = tonumber(sj:match('"attack"%s*:%s*(%d+)')),
                defense = tonumber(sj:match('"defense"%s*:%s*(%d+)')),
                speed   = tonumber(sj:match('"speed"%s*:%s*(%d+)')),
                spAtk   = tonumber(sj:match('"spAtk"%s*:%s*(%d+)')),
                spDef   = tonumber(sj:match('"spDef"%s*:%s*(%d+)')),
            }
        end
        local nickname = obj:match('"nickname"%s*:%s*"([^"]*)"')
        local area_id = obj:match('"area_id"%s*:%s*"([^"]*)"')
        local areas_arr = nil
        local areas_raw = obj:match('"areas"%s*:%s*(%b[])')
        if areas_raw then
            areas_arr = {}
            for a in areas_raw:gmatch('"([^"]+)"') do areas_arr[#areas_arr+1] = a end
        end
        if cmd then
            cmds[#cmds+1] = {cmd=cmd, key=key, message=msg, stats=stats, nickname=nickname,
                             text=text, fb=fb, r=r, g=g, b=b, frames=frames,
                             area_id=area_id, areas=areas_arr}
        end
    end
    return cmds
end

-- ── HUD overlay ──────────────────────────────────────────────────────────────
HUD.init({screen_w = 256, screen_h = 192, hud_x = 3, hud_y = 180, hud_right = 253,
          prompt_y = 53, prompt_h = 14, gameover_y = 70})
local hud_show    = HUD.show
local hud_render  = HUD.render
local prompt_show = HUD.prompt

-- ── Game-over persistent overlay ─────────────────────────────────────────────
local game_over_flag = false
local rebuild_active = false  -- true between rebuild_start and rebuild_done

-- ── Deferred sync state ────────────────────────────────────────────────────────
local pending_sync_cmds = {}
local resolved_areas    = {}
local resolved_seeded   = false
local pending_hud_area  = nil

-- ── Write safety ──────────────────────────────────────────────────────────────
local writes_enabled    = false
local SYNC_COOLDOWN_FRAMES = 120
local sync_cooldown        = 0
local sync_block_log_timer = 0

-- Nick label cache
local _nick_cache = {}
local function nick_label(key)
    if _nick_cache[key] then return _nick_cache[key] end
    return key and key:sub(1, 8) or "?"
end

-- Memorial box index (0-based) — set by applyProfile; Gen 5 = 23 (Box 24).
-- Read from M.MEMORIAL_BOX so it stays in sync with the profile.
local MEMORIAL_BOX = M.MEMORIAL_BOX  -- 23 for Gen 5, 17 for Gen 4

-- ── Command dispatcher ────────────────────────────────────────────────────────

local function _filter_pending(exclude_cmd, key)
    local filtered = {}
    for _, p in ipairs(pending_sync_cmds) do
        if not (p.key == key and p.cmd == exclude_cmd) then
            filtered[#filtered + 1] = p
        end
    end
    return filtered
end

local function dispatch_commands(cmds)
    for _, c in ipairs(cmds) do
        local short_key = c.key and c.key:sub(1, 8) or "?"
        if c.cmd == "force_faint" and c.key then
            if not writes_enabled then
                console.log("[SLink]   ↳ force_faint BLOCKED (writes disabled)")
                hud_show("!! force_faint: save invalid", 255, 80, 80, 360)
            else
                local count = M.readPartyCount()
                if count > 6 then count = 0 end
                local found = false
                for slot = 0, count - 1 do
                    local base = M.partyAddr(slot)
                    if base then
                        local pid = mem_u32(base)
                        if pid ~= 0 and fmt("%08X", pid) == short_key then
                            M.forceFaint(slot)
                            console.log(fmt("[SLink]   ↳ force_faint slot=%d key=%s", slot, c.key))
                            hud_show("!! " .. nick_label(c.key) .. " KO'd", 255, 80, 80, 360)
                            found = true
                            break
                        end
                    end
                end
                if not found then
                    console.log(fmt("[SLink]   ↳ force_faint: key %s not found in party", c.key))
                end
            end
        elseif c.cmd == "pending_sync" then
            console.log("[SLink]   ↳ ⚠ SYNC REQUIRED: " .. (c.message or "check partner at PC"))
            hud_show("!! SYNC REQUIRED", 255, 200, 60, 600)
        elseif c.cmd == "box_mon" and c.key then
            pending_sync_cmds = _filter_pending("party_mon", c.key)
            table.insert(pending_sync_cmds, {cmd="box_mon", key=c.key})
            console.log("[SLink]   ↳ box_mon QUEUED: " .. short_key)
        elseif c.cmd == "party_mon" and c.key then
            pending_sync_cmds = _filter_pending("box_mon", c.key)
            table.insert(pending_sync_cmds, {cmd="party_mon", key=c.key, stats=c.stats})
            console.log("[SLink]   ↳ party_mon QUEUED: " .. short_key)
        elseif c.cmd == "memorialize" and c.key then
            local already_queued = false
            for _, p in ipairs(pending_sync_cmds) do
                if p.cmd == "memorialize" and p.key == c.key then
                    already_queued = true; break
                end
            end
            if not already_queued then
                table.insert(pending_sync_cmds, {cmd="memorialize", key=c.key})
                console.log(fmt("[SLink]   ↳ memorialize QUEUED: %s", short_key))
            else
                console.log(fmt("[SLink]   ↳ memorialize DEDUPED: %s", short_key))
            end
        elseif c.cmd == "resolved_areas" and c.areas then
            for _, a in ipairs(c.areas) do resolved_areas[a] = true end
            resolved_seeded = true
            console.log(fmt("[SLink]   ↳ resolved_areas: %d areas seeded", #c.areas))
            if pending_hud_area and not resolved_areas[pending_hud_area] then
                local disp = pending_hud_area:gsub("_", " ")
                    :gsub("(%a)([%w]*)", function(a, b) return a:upper()..b end)
                hud_show(">> New encounter: " .. disp, 80, 255, 120, 180)
            end
            pending_hud_area = nil
        elseif c.cmd == "unresolve_area" and c.area_id then
            resolved_areas[c.area_id] = nil
            console.log("[SLink]   ↳ unresolve_area: " .. c.area_id)
        elseif c.cmd == "hud_show" and c.text then
            hud_show(c.text, c.r or 255, c.g or 255, c.b or 255, c.frames or 300)
        elseif (c.cmd == "msgbox" or c.cmd == "gui_prompt") and c.text then
            -- Gen 3's native message-box / prompt commands (link-formed / dead-zone / shiny
            -- notices are sent as msgbox to every gen) — render via the Lua overlay here.
            if c.fb == "hud" then
                hud_show(c.text, c.r or 255, c.g or 255, c.b or 255, c.frames or 300)
            else
                prompt_show(c.text, c.r or 255, c.g or 255, c.b or 255, c.frames or 300)
            end
            console.log("[SLink]   ↳ " .. c.cmd .. ": " .. c.text)
        elseif c.cmd == "game_over" then
            game_over_flag = true
            if M.playSE then M.playSE(M.SE_GAME_OVER) end
            HUD.set_game_over()
            console.log("[SLink]   ↳ GAME OVER — SOUL LINK")
        elseif c.cmd == "rebuild_start" then
            rebuild_active = true
            HUD.set_rebuilding(c.text or "REBUILDING TEAM")
            console.log("[SLink]   ↳ rebuild_start: " .. tostring(c.text))
        elseif c.cmd == "rebuild_done" then
            rebuild_active = false
            HUD.clear_rebuilding()
            console.log("[SLink]   ↳ rebuild_done")
        elseif c.cmd ~= "noop" then
            console.log("[SLink]   ↳ cmd: " .. tostring(c.cmd))
        end
    end
end

-- ── Send / receive ────────────────────────────────────────────────────────────
local seq            = 0
local pending_labels = {}

local function send(evt, label, is_auto, is_silent)
    if not C.connected() then
        console.log("[SLink] NOT CONNECTED — dropped: " .. (label or evt.event))
        return
    end
    seq = seq + 1; evt.seq = seq; evt.player = PLAYER_ID
    C.send(json_encode(evt))
    local prefix = is_auto and "AUTO" or "MANUAL"
    local lbl = (is_silent and "SILENT:" or "") .. (prefix .. " " .. seq .. ": " .. (label or evt.event))
    pending_labels[#pending_labels+1] = lbl
    if not is_silent then
        console.log(fmt("[SLink] [→] seq=%d  %s: %s", seq, prefix, label or evt.event))
    end
end

local function format_cmds(cmds)
    if #cmds == 0 then return "???" end
    local p = {}
    for _, c in ipairs(cmds) do
        if c.key then p[#p+1] = c.cmd.."("..c.key:sub(1,8)..")"
        else           p[#p+1] = c.cmd end
    end
    return table.concat(p, ", ")
end

-- ── Deferred sync execution (safe-state only) ─────────────────────────────────

local function exec_memorialize(key)
    -- Drain stale box_mon/party_mon for this key
    local filtered = {}
    for _, c in ipairs(pending_sync_cmds) do
        if not (c.key == key and (c.cmd == "box_mon" or c.cmd == "party_mon")) then
            filtered[#filtered + 1] = c
        end
    end
    pending_sync_cmds = filtered

    local pc_base = M.pcStorageBase()
    if not pc_base then
        console.log("[SLink]   ↳ memorialize EXEC FAIL: PC storage unreachable (VERIFY_ME addr)")
        hud_show("!! mem: PC unavailable", 255, 140, 40, 600)
        send({event="memorialize_failed", key=key, reason="pc_unavailable"},
             "memorialize_failed:"..key:sub(1,8), true, true)
        return
    end
    local pid_hex = key:sub(1, 8)

    -- First: scan party
    local count = M.readPartyCount()
    if count > 6 then count = 0 end
    local found_addr = nil
    local found_in = "party"
    for slot = 0, count - 1 do
        local addr = M.partyAddr(slot)
        if addr then
            local pid = mem_u32(addr)
            if pid ~= 0 and fmt("%08X", pid) == pid_hex then
                found_addr = addr
                break
            end
        end
    end

    -- Second: scan PC boxes 0-(MEMORIAL_BOX-1)
    if not found_addr then
        for box = 0, MEMORIAL_BOX - 1 do
            for slot = 0, 29 do
                local addr = M.pcBoxAddr(box, slot)
                if addr then
                    local pid = mem_u32(addr)
                    if pid ~= 0 and fmt("%08X", pid) == pid_hex then
                        found_addr = addr
                        found_in = fmt("box%d_s%d", box + 1, slot + 1)
                        break
                    end
                end
            end
            if found_addr then break end
        end
    end

    -- Third: check if already in memorial box
    if not found_addr then
        for slot = 0, 29 do
            local addr = M.pcBoxAddr(MEMORIAL_BOX, slot)
            if addr then
                local pid = mem_u32(addr)
                if pid ~= 0 and fmt("%08X", pid) == pid_hex then
                    console.log(fmt("[SLink]   ↳ memorialize: %s already in Box %d s%d",
                        pid_hex, MEMORIAL_BOX + 1, slot + 1))
                    hud_show("+ " .. nick_label(key) .. " in grave", 100, 255, 100, 360)
                    send({event="memorialize_done", key=key, box=MEMORIAL_BOX, slot=slot},
                         "memorialize_done:"..pid_hex, true)
                    return
                end
            end
        end
        console.log(fmt("[SLink]   ↳ memorialize: %s not found (retry)", pid_hex))
        return false
    end

    -- Find memorial slot: try MEMORIAL_BOX first, then overflow backwards
    local mem_box = nil
    local empty = nil
    for box = MEMORIAL_BOX, 0, -1 do
        local slot = M.pcBoxFirstEmpty(box)
        if slot then
            mem_box = box
            empty = slot
            break
        end
    end
    if not mem_box then
        console.log("[SLink]   ↳ memorialize EXEC FAIL: ALL PC boxes full!")
        hud_show("!! All boxes full", 255, 80, 80, 600)
        send({event="memorialize_failed", key=key, reason="box_full"},
             "memorialize_failed:"..key:sub(1,8), true, true)
        return
    end
    local pc_addr = M.pcBoxAddr(mem_box, empty)
    for i = 0, 0x87 do
        memory.write_u8(pc_addr + i, memory.read_u8(found_addr + i, _RAM), _RAM)
    end
    -- Clear decryption flags
    local pc_flags = mem_u16(pc_addr + 0x004)
    mem_w16(pc_addr + 0x004, pc_flags & 0xFFFC)
    local verify_pid = mem_u32(pc_addr)
    local expected_pid = mem_u32(found_addr)
    if verify_pid == expected_pid and verify_pid ~= 0 then
        if found_in == "party" then
            if count <= 1 then
                console.log("[SLink]   ↳ memorialize: last party mon — leaving in party")
                hud_show("!! Only mon!", 255, 200, 60, 240)
                return
            end
            local found_slot = nil
            for slot = 0, count - 1 do
                if M.partyAddr(slot) == found_addr then found_slot = slot; break end
            end
            if found_slot then
                for i = 0, M.MON_SIZE - 1 do
                    memory.write_u8(found_addr + i, 0, _RAM)
                end
                for s = found_slot, count - 2 do
                    local src = M.partyAddr(s + 1)
                    local dst = M.partyAddr(s)
                    for i = 0, M.MON_SIZE - 1 do
                        memory.write_u8(dst + i, memory.read_u8(src + i, _RAM), _RAM)
                    end
                end
                local last = M.partyAddr(count - 1)
                for i = 0, M.MON_SIZE - 1 do
                    memory.write_u8(last + i, 0, _RAM)
                end
                M.writePartyCount(count - 1)
                M.clearDebounce()
            end
        else
            for i = 0, 0x87 do
                memory.write_u8(found_addr + i, 0, _RAM)
            end
        end
        console.log(fmt("[SLink]   ↳ memorialize OK: %s (%s) → Box %d slot %d",
            pid_hex, found_in, mem_box + 1, empty + 1))
        hud_show(fmt("+ %s buried -> Box %d", nick_label(key), mem_box + 1), 255, 140, 40, 360)
        send({event="memorialize_done", key=key, box=mem_box, slot=empty},
             "memorialize_done:"..pid_hex, true)
    else
        console.log(fmt("[SLink]   ↳ memorialize VERIFY FAIL: Box %d s%d PID mismatch",
            mem_box + 1, empty + 1))
        hud_show("!! mem verify failed", 255, 80, 80, 600)
        send({event="memorialize_failed", key=key, reason="verify_mismatch"},
             "memorialize_failed:"..pid_hex, true, true)
    end
end

local function exec_box_mon(key)
    local count = M.readPartyCount()
    if count <= 1 then
        console.log("[SLink]   ↳ box_mon skipped: " .. key:sub(1,8) .. " (last mon)")
        hud_show("! Only mon!", 255, 200, 60, 240)
        return
    end
    local found_slot = nil
    local found_addr = nil
    for slot = 0, count - 1 do
        local addr = M.partyAddr(slot)
        if addr then
            local pid = mem_u32(addr)
            if pid ~= 0 and fmt("%08X", pid) == key:sub(1, 8) then
                found_slot = slot; found_addr = addr; break
            end
        end
    end
    if not found_slot then
        console.log("[SLink]   ↳ box_mon: " .. key:sub(1,8) .. " not found in party")
        sync_written_keys[key] = true
        return
    end
    local dst_box, dst_slot = nil, nil
    for box = 0, MEMORIAL_BOX - 1 do
        local s = M.pcBoxFirstEmpty(box)
        if s then dst_box = box; dst_slot = s; break end
    end
    if not dst_box then
        console.log("[SLink]   ↳ box_mon FAIL: all PC boxes full!")
        hud_show("!! Boxes full", 255, 80, 80, 600)
        return
    end
    local slot_data = M.readPartySlot(found_slot)
    local stats_tbl = slot_data and {level=slot_data.level, maxHP=slot_data.maxHP} or nil
    local pc_addr = M.pcBoxAddr(dst_box, dst_slot)
    M.writeBoxMonFromParty(found_addr, pc_addr)
    for i = 0, M.MON_SIZE - 1 do
        memory.write_u8(found_addr + i, 0, _RAM)
    end
    for s = found_slot, count - 2 do
        local src = M.partyAddr(s + 1)
        local dst = M.partyAddr(s)
        for i = 0, M.MON_SIZE - 1 do
            memory.write_u8(dst + i, memory.read_u8(src + i, _RAM), _RAM)
        end
    end
    local last = M.partyAddr(count - 1)
    for i = 0, M.MON_SIZE - 1 do
        memory.write_u8(last + i, 0, _RAM)
    end
    M.writePartyCount(count - 1)
    M.clearDebounce()
    if stats_tbl then
        send({event="stats_cache", key=key, stats=stats_tbl},
             "stats_cache:"..key:sub(1,8), true, true)
    end
    console.log(fmt("[SLink]   ↳ box_mon OK: %s → Box %d slot %d", key:sub(1,8), dst_box + 1, dst_slot + 1))
    hud_show(fmt("v %s boxed -> Box %d", nick_label(key), dst_box + 1), 100, 180, 255, 240)
end

local function exec_party_mon(key, stats)
    stats = stats or {}
    local count = M.readPartyCount()
    for slot = 0, count - 1 do
        local addr = M.partyAddr(slot)
        if addr and mem_u32(addr) ~= 0 and fmt("%08X", mem_u32(addr)) == key:sub(1, 8) then
            console.log("[SLink]   ↳ party_mon: " .. key:sub(1,8) .. " already in party")
            sync_written_keys[key] = true
            send({event="sync_retrieve_done", key=key},
                 "sync_retrieve_done:"..key:sub(1,8), true, true)
            return
        end
    end
    if count >= 6 then
        console.log("[SLink]   ↳ party_mon: party full for " .. key:sub(1,8))
        hud_show("! Unbox " .. nick_label(key), 255, 200, 60, 600)
        return false
    end
    local src_box, src_slot, src_addr = nil, nil, nil
    for box = 0, MEMORIAL_BOX do
        for slot = 0, 29 do
            local addr = M.pcBoxAddr(box, slot)
            if addr then
                local pid = mem_u32(addr)
                if pid ~= 0 and fmt("%08X", pid) == key:sub(1, 8) then
                    src_box = box; src_slot = slot; src_addr = addr; break
                end
            end
        end
        if src_box then break end
    end
    if not src_box then
        console.log("[SLink]   ↳ party_mon: " .. key:sub(1,8) .. " not found in any PC box")
        hud_show("! Unbox " .. nick_label(key), 255, 200, 60, 600)
        send({event="sync_retrieve_failed", key=key},
             "sync_retrieve_failed:"..key:sub(1,8), true, true)
        return
    end
    local dst_addr = M.partyAddr(count)
    if not dst_addr then
        console.log("[SLink]   ↳ party_mon: can't compute party slot address")
        send({event="sync_retrieve_failed", key=key},
             "sync_retrieve_failed:"..key:sub(1,8), true, true)
        return
    end
    for i = 0, 0x87 do
        memory.write_u8(dst_addr + i, memory.read_u8(src_addr + i, _RAM), _RAM)
    end
    local pid = mem_u32(dst_addr)
    local s_level = stats.level or 5
    local s_maxHP = stats.maxHP or 1
    local s_curHP = s_maxHP
    for i = 0x88, M.MON_SIZE - 1 do
        memory.write_u8(dst_addr + i, 0, _RAM)
    end
    M.encrypt_stats(dst_addr + 0x88, pid, 0, s_level, s_curHP, s_maxHP, 0, 0, 0, 0, 0)
    M.writePartyCount(count + 1)
    M.clearDebounce()
    for i = 0, 0x87 do
        memory.write_u8(src_addr + i, 0, _RAM)
    end
    sync_written_keys[key] = true
    all_known_keys[key]    = true
    console.log(fmt("[SLink]   ↳ party_mon OK: %s ← Box %d slot %d → party[%d]",
        key:sub(1,8), src_box + 1, src_slot + 1, count))
    hud_show(fmt("^ %s unboxed Box %d", nick_label(key), src_box + 1), 100, 255, 160, 240)
    send({event="sync_retrieve_done", key=key},
         "sync_retrieve_done:"..key:sub(1,8), true, true)
end

-- ── Party helpers ─────────────────────────────────────────────────────────────

local _mk_pid     = {}
local _mk_str     = {}
local _mk_species = {}

local function cachedMonKey(slot, slotAddr)
    local pid = mem_u32(slotAddr)
    if pid == _mk_pid[slot] and (_mk_species[slot] or 0) > 0 then
        return _mk_str[slot]
    end
    local species_id, ot_id = M.decrypt_block_a(slotAddr)
    local key
    if ot_id then
        key = fmt("%08X:%08X", pid, ot_id)
    else
        key = fmt("%08X", pid)
    end
    _mk_pid[slot]     = pid
    _mk_str[slot]     = key
    _mk_species[slot] = species_id or 0
    if key and species_id and species_id > 0 and not _nick_cache[key] then
        _nick_cache[key] = fmt("species#%d", species_id)
    end
    return key
end

local function slotOccupied(slotAddr)
    return mem_u32(slotAddr) ~= 0 and mem_u16(slotAddr + M.PKM.CHKSUM) ~= 0
end

local _ip_buf = {{}, {}}
local _ip_idx = 1
local _ip_pool = {{}, {}}
for _b = 1, 2 do
    for _s = 0, 5 do _ip_pool[_b][_s] = {hp=0, maxHP=0, level=0, slot=0, species_id=0, is_egg=false} end
end

-- is_egg cache keyed by PID — decrypt Block B only when a new key appears.
local _egg_cache = {}

local function index_party(in_battle)
    _ip_idx = (_ip_idx == 1) and 2 or 1
    local t    = _ip_buf[_ip_idx]
    local pool = _ip_pool[_ip_idx]
    for k in pairs(t) do t[k] = nil end

    local base_addr = M.init()
    if not base_addr and base_addr ~= 0 then return t, 0 end

    local count = M.readPartyCount()
    if count > 6 then return t, 0 end

    for i = 0, count - 1 do
        local base = M.partyAddr(i)
        if base and mem_u32(base) ~= 0 then
            local k = cachedMonKey(i, base)
            local s = in_battle and M.battleHP(i) or M.partyHP(i)
            if s then
                local entry = pool[i]
                entry.hp         = s.hp
                entry.maxHP      = s.maxHP
                entry.level      = s.level
                entry.slot       = i
                entry.species_id = _mk_species[i] or 0
                -- is_egg is stable for the lifetime of a mon's egg state;
                -- decrypt Block B once and cache by key.
                local is_egg = _egg_cache[k]
                if is_egg == nil then
                    local blk_b = M.decrypt_block_b(base)
                    is_egg = blk_b and blk_b.is_egg or false
                    _egg_cache[k] = is_egg
                end
                entry.is_egg = is_egg
                -- Capture events read this entry directly (see "── capture ──"
                -- branch below). Including nickname here matches the Gen 3
                -- client behaviour, so server-side pending_captures gets the
                -- real name immediately rather than waiting for the next tick.
                local cached_nick = _nick_cache[k]
                if cached_nick == nil or cached_nick:sub(1, 8) == "species#" then
                    local fresh = M.readNickname(base)
                    if fresh then
                        _nick_cache[k] = fresh
                        cached_nick = fresh
                    end
                end
                entry.nickname = cached_nick or ""
                t[k] = entry
            end
        end
    end
    return t, count
end

local function build_party_snapshot(in_battle)
    local count = M.readPartyCount()
    if count > 6 then return {} end
    local snap = {}
    for i = 0, count - 1 do
        local base = M.partyAddr(i)
        if base and mem_u32(base) ~= 0 then
            local k = cachedMonKey(i, base)
            local s = in_battle and M.battleHP(i) or M.partyHP(i)
            if s then
                local species, ot_id, held_item, ability = M.decrypt_block_a_ext(base)
                local nickname = M.readNickname(base)
                if k and nickname then _nick_cache[k] = nickname end
                local moves, pp, pp_bonuses, form, is_egg = nil, nil, 0, 0, false
                local blk_b = M.decrypt_block_b(base)
                if blk_b then
                    moves = blk_b.moves
                    pp    = blk_b.pp
                    local ups = blk_b.pp_ups
                    pp_bonuses = ((ups[1] & 0x3))
                               | ((ups[2] & 0x3) << 2)
                               | ((ups[3] & 0x3) << 4)
                               | ((ups[4] & 0x3) << 6)
                    form   = blk_b.form
                    is_egg = blk_b.is_egg
                end
                local base_species = species or (_mk_species[i] or 0)
                local display_species = GAME_MODULE.form_display_id(base_species, form)
                snap[#snap+1] = {
                    key          = k,
                    hp           = s.hp,
                    maxHP        = s.maxHP,
                    level        = s.level,
                    species_id   = display_species,
                    held_item_id = held_item or 0,
                    ability_id   = ability or 0,
                    nickname     = nickname or "",
                    status_cond  = s.status_cond or 0,
                    moves        = moves,
                    pp           = pp,
                    pp_bonuses   = pp_bonuses,
                    form         = form,
                    is_egg       = is_egg,
                }
            end
        end
    end
    return snap
end

-- ── Pokéball helpers ──────────────────────────────────────────────────────────
-- Gen 5 uses standard ball IDs 0x0001–0x0010 only (no HGSS Apricorn balls).

local function _is_ball_id(id)
    return id >= 0x0001 and id <= 0x0010
end

local function hasPokeballs()
    local addr = M.bagBallsAddr()
    if addr == nil then return false end
    for i = 0, M.BAG.BALLS_COUNT - 1 do
        local slot = M.readItemSlot(addr, i)
        if slot and slot.qty > 0 and _is_ball_id(slot.id) then return true end
    end
    return false
end

local function countPokeballs()
    local addr = M.bagBallsAddr()
    if addr == nil then return 0 end
    local total = 0
    for i = 0, M.BAG.BALLS_COUNT - 1 do
        local slot = M.readItemSlot(addr, i)
        if slot and slot.qty > 0 and _is_ball_id(slot.id) then total = total + slot.qty end
    end
    return total
end

-- Gift areas — BW2 variants have different gift sets from BW1.
local _is_bw2 = _ROM_TYPE == "pokemon_black_2" or _ROM_TYPE == "pokemon_white_2"
local GIFT_AREAS = _is_bw2 and GAME_MODULE.GIFT_AREAS_BW2 or GAME_MODULE.GIFT_AREAS_BW1

-- Unova encounter areas (all areas in the area map that aren't gift areas).
-- All entries in AREAS that map to an area_id are wild encounter areas.
local ENCOUNTER_AREAS = {
    route_1=true, route_2=true, route_3=true, route_4=true, route_5=true,
    route_6=true, route_7=true, route_8=true, route_9=true, route_10=true,
    route_11=true, route_12=true, route_13=true, route_14=true, route_15=true,
    route_16=true, route_17=true, route_18=true, route_19=true, route_20=true,
    route_21=true, route_22=true, route_23=true,
    wellspring_cave=true, pinwheel_forest=true, desert_resort=true,
    relic_castle=true, cold_storage=true, chargestone_cave=true,
    twist_mountain=true, dragonspiral_tower=true, victory_road=true,
    giant_chasm=true, village_bridge=true, mistralton_cave=true,
    celestial_tower=true, moor_of_icirrus=true, challengers_cave=true,
    abundant_shrine=true, lostlorn_forest=true, white_forest=true,
    castelia_sewers=true, relic_passage=true, clay_tunnel=true,
    seaside_cave=true, floccesy_ranch=true, reversal_mountain=true,
    strange_house=true, nature_preserve=true, virbank_complex=true,
    dreamyard=true,
}

-- ── Per-frame state ───────────────────────────────────────────────────────────
local initialized    = false
local was_connected  = false
local nuzlocke_active = false
local frame_count    = 0
local prev_keys      = {}

local prev_zone_id   = -1
local prev_area      = ""
local prev_loc       = ""
local prev_in_battle = false
local prev_party     = {}

local ZONE_DEBOUNCE_FRAMES = 8
local zone_debounce_cand   = -1
local zone_debounce_count  = 0
local zone_confirmed       = -1

local battle_area_id       = nil
local battle_is_wild       = false
local captured_this_battle = false
local post_battle_frames   = 0
local POST_BATTLE_GRACE    = 90
local pending_safe         = false
local battle_box_snapshot  = nil
local pending_box_captures = {}
local battle_enc_species   = 0
local battle_enc_level     = 0

local PARTY_DEBOUNCE_FRAMES  = 3
local pending_box_to_party   = {}
local pending_party_to_box   = {}
local sync_written_keys      = {}
local all_known_keys         = {}

local GIFT_BUFFER_WINDOW     = 45
local gift_capture_buffer    = {}

local TICK_INTERVAL          = 30

-- Gen 5 has 24 boxes
local BOX_SCAN_TOTAL    = 24
local box_scan_idx      = 0
local pc_box_cache      = {}
local memorial_box_renamed = false

-- ── Main frame handler ────────────────────────────────────────────────────────
local function on_frame()
    frame_count = frame_count + 1

    -- 1. Drive TCP pump
    C.pump()

    -- 2. Resolve base (DIRECT_ADDR: sets M._base = 0; Gen 4: pointer chain).
    local base_addr = M.init()
    if base_addr == nil then
        if writes_enabled then
            writes_enabled = false
            console.log("[SLink] writes DISABLED (init failed)")
        end
        return
    end

    -- 2b. Validate save
    if not writes_enabled then
        local ok, err = M.validateSave()
        if ok then
            writes_enabled = true
            console.log("[SLink] ✓ save validated — writes enabled")
        elseif frame_count % 300 == 0 then
            console.log("[SLink] validateSave FAIL: " .. (err or "unknown"))
        end
    end

    -- 3. Connection state change → hello
    local now_connected = C.connected()
    if now_connected ~= was_connected then
        if now_connected then
            console.log("[SLink] [TCP] connected to " .. SERVER_HOST .. ":" .. SERVER_PORT)
            if writes_enabled and hasPokeballs() then
                nuzlocke_active = true
                console.log("[SLink] nuzlocke ACTIVE (pokeballs in bag at startup)")
            end
            local snap = build_party_snapshot(false)
            for k in pairs(prev_party) do all_known_keys[k] = true end
            send({event="hello", area_id=prev_area, loc_name=prev_loc, rom_type=_ROM_TYPE,
                  writes_enabled=writes_enabled, has_pokeballs=nuzlocke_active,
                  ball_count=countPokeballs(), party=snap,
                  trainer_name=M.readTrainerName(),
                  badges=M.readBadges1()}, "hello", true)
            if #snap > 0 then
                for i, m in ipairs(snap) do
                    console.log(fmt("[SLink] party[%d] key=%s level=%d maxHP=%d",
                        i-1, m.key or "?", m.level or 0, m.maxHP or 0))
                end
            end
            initialized = true
        else
            console.log("[SLink] [TCP] disconnected — reconnecting…")
        end
        was_connected = now_connected
    end

    -- 4. Dispatch responses
    while true do
        local line = C.receive()
        if not line then break end
        local label = table.remove(pending_labels, 1) or "?"
        local cmds  = parse_command_list(line)
        if label:sub(1,7) ~= "SILENT:" then
            console.log("[SLink] [←] " .. label .. " → " .. format_cmds(cmds))
        end
        dispatch_commands(cmds)
    end

    if not initialized then return end

    -- 5. Read current state
    local raw_zone_id = M.readZoneID()
    local in_battle   = M.isInBattle()

    -- Zone ID debounce
    if raw_zone_id == zone_debounce_cand then
        zone_debounce_count = zone_debounce_count + 1
    else
        zone_debounce_cand  = raw_zone_id
        zone_debounce_count = 1
    end
    if zone_debounce_count >= ZONE_DEBOUNCE_FRAMES then
        zone_confirmed = zone_debounce_cand
    end
    local zone_id = zone_confirmed
    local area    = AREAS[zone_id] or ""
    local loc     = LOCATIONS[zone_id] or ""

    local battle_just_ended = prev_in_battle and not in_battle
    if battle_just_ended then
        sync_cooldown = SYNC_COOLDOWN_FRAMES
    elseif sync_cooldown > 0 then
        sync_cooldown = sync_cooldown - 1
    end

    -- 5b. Flush deferred sync cmds
    local is_overworld = not in_battle
    local safe_now = is_overworld and sync_cooldown == 0
                     and not battle_just_ended and post_battle_frames == 0
    if #pending_sync_cmds > 0 then
        if not safe_now or not writes_enabled then
            sync_block_log_timer = sync_block_log_timer - 1
            if sync_block_log_timer <= 0 then
                sync_block_log_timer = 120
                console.log(fmt("[SLink] SYNC BLOCKED: safe=%s writes=%s cooldown=%d pbf=%d cmd=%s",
                    tostring(safe_now), tostring(writes_enabled), sync_cooldown,
                    post_battle_frames, pending_sync_cmds[1].cmd))
            end
        else
            sync_block_log_timer = 0
        end
    end
    if safe_now and #pending_sync_cmds > 0 and writes_enabled then
        local cmd = pending_sync_cmds[1]  -- peek before removing
        -- Never memorialize the last party mon — emptying the party softlocks.
        local blocked = false
        if cmd.cmd == "memorialize" and cmd.key then
            local q_count = M.readPartyCount()
            if q_count <= 1 then
                for q_s = 0, q_count - 1 do
                    local addr = M.partyAddr(q_s)
                    if addr and M.monKey(addr) == cmd.key then
                        if game_over_flag then
                            table.remove(pending_sync_cmds, 1)
                            blocked = true
                            console.log("[SLink] memorialize dropped (game over, last party mon): "..cmd.key:sub(1,8))
                        else
                            -- Block the memorialize — the party must never be
                            -- empty (softlock). Normally party_mon arrives
                            -- before this memorialize and lifts the block
                            -- automatically. But when the party was full at
                            -- whiteout, party_mon retried to the end of the
                            -- queue. Rescue: promote the first party_mon to
                            -- front so it runs next frame.
                            blocked = true
                            for look = 2, #pending_sync_cmds do
                                if pending_sync_cmds[look].cmd == "party_mon" then
                                    local pm = table.remove(pending_sync_cmds, look)
                                    table.insert(pending_sync_cmds, 1, pm)
                                    console.log("[SLink] memorialize blocked: promoted party_mon to front ("..pm.key:sub(1,8)..")")
                                    break
                                end
                            end
                        end
                        break
                    end
                end
            end
        end
        if not blocked then
        local cmd = table.remove(pending_sync_cmds, 1)
        local exec_ok, exec_result = true, nil
        if cmd.cmd == "box_mon" then
            exec_ok, exec_result = pcall(exec_box_mon, cmd.key)
        elseif cmd.cmd == "party_mon" then
            exec_ok, exec_result = pcall(exec_party_mon, cmd.key, cmd.stats)
        elseif cmd.cmd == "memorialize" then
            exec_ok, exec_result = pcall(exec_memorialize, cmd.key)
        end
        if not exec_ok then
            console.log(fmt("[SLink] ✗ SYNC CMD ERROR (%s key=%s): %s",
                cmd.cmd, (cmd.key or "?"):sub(1,8), tostring(exec_result)))
            cmd._retries = (cmd._retries or 0) + 1
            if cmd._retries <= 3 then
                table.insert(pending_sync_cmds, 1, cmd)
            else
                console.log("[SLink]   ↳ DROPPED after 3 retries")
                hud_show("X " .. cmd.cmd .. " fail: " .. nick_label(cmd.key or ""), 255, 80, 80, 600)
            end
        elseif cmd.cmd == "party_mon" and exec_result == false then
            cmd._retries = (cmd._retries or 0) + 1
            if cmd._retries <= 3 then
                table.insert(pending_sync_cmds, cmd)
            else
                hud_show("! Unbox " .. nick_label(cmd.key or ""), 255, 200, 60, 600)
                send({event="sync_retrieve_failed", key=cmd.key},
                     "sync_retrieve_failed:"..(cmd.key or "?"):sub(1,8), true, true)
            end
        elseif cmd.cmd == "memorialize" and exec_result == false then
            cmd._retries = (cmd._retries or 0) + 1
            if cmd._retries <= 5 then
                table.insert(pending_sync_cmds, cmd)
            else
                hud_show("!! mem: not found", 255, 140, 40, 600)
                send({event="memorialize_failed", key=cmd.key, reason="not_found"},
                     "memorialize_failed:"..(cmd.key or "?"):sub(1,8), true, true)
            end
        end
        end -- not blocked
    end

    -- 6. area_enter
    if zone_id ~= prev_zone_id then
        if area ~= "" then
            send({event="area_enter", area_id=area, loc_name=loc}, "area_enter:" .. area, true)
            if nuzlocke_active and area ~= prev_area
                    and ENCOUNTER_AREAS[area] and not GIFT_AREAS[area] then
                if resolved_seeded then
                    if not resolved_areas[area] then
                        local disp = area:gsub("_", " ")
                            :gsub("(%a)([%w]*)", function(a, b) return a:upper()..b end)
                        hud_show(">> NEW ENCOUNTER <<  " .. disp, 255, 220, 60, 240)
                    end
                else
                    pending_hud_area = area
                end
            end
        end
    end

    -- 7. battle start
    if not prev_in_battle and in_battle then
        battle_area_id       = area
        captured_this_battle = false
        battle_is_wild       = M.isWildBattle()
        battle_box_snapshot  = nil
        battle_enc_species   = 0
        battle_enc_level     = 0
        M.clearDebounce()
        local pcount = M.readPartyCount()
        if pcount >= 6 then
            local cur_box = M.readCurrentBox()
            battle_box_snapshot = {box=cur_box, pids=M.readBoxPIDs(cur_box)}
        end
        console.log(fmt("[SLink] [battle] start  wild=%s  area=%s  party=%d",
            tostring(battle_is_wild), battle_area_id or "(none)", pcount))
        if battle_is_wild and nuzlocke_active and battle_area_id ~= ""
                and ENCOUNTER_AREAS[battle_area_id]
                and not resolved_areas[battle_area_id]
                and not GIFT_AREAS[battle_area_id] then
            local disp = battle_area_id:gsub("_", " ")
                :gsub("(%a)([%w]*)", function(a, b) return a:upper()..b end)
            hud_show(">> NEW ENCOUNTER <<  " .. disp, 255, 220, 60, 360)
        end
    end

    -- 8. battle end
    if battle_just_ended then
        M.clearDebounce()
        post_battle_frames = POST_BATTLE_GRACE
        pending_safe       = true
        console.log("[SLink] [battle] end  grace window started")
    end

    -- 8a. Cache enemy encounter data during battle
    if in_battle and battle_is_wild and battle_enc_species == 0 then
        local ebbase = M.enemyBattleAddr(0)
        if ebbase then
            local epid = mem_u32(ebbase)
            if epid ~= 0 and mem_u16(ebbase + M.PKM.CHKSUM) ~= 0 then
                local sid, _ = M.decrypt_block_a(ebbase)
                if sid and sid > 0 then
                    battle_enc_species = sid
                    local lv, _, _ = M.decrypt_stats(ebbase + M.PKM.STATUS, epid)
                    if lv and lv >= 1 and lv <= 100 then battle_enc_level = lv end
                end
            end
        end
    end

    -- 8b. Post-battle grace: detect party-full box captures
    if post_battle_frames == 1 and battle_box_snapshot and not captured_this_battle then
        local snap = battle_box_snapshot
        local cur_pids = M.readBoxPIDs(snap.box)
        local new_keys = {}
        for pid_hex, _ in pairs(cur_pids) do
            if not snap.pids[pid_hex] then new_keys[#new_keys + 1] = pid_hex end
        end
        if #new_keys == 1 then
            pending_box_captures[#pending_box_captures + 1] = {
                pid=new_keys[1], box=snap.box, area_id=battle_area_id or area,
            }
            captured_this_battle = true
        elseif #new_keys > 1 then
            captured_this_battle = true
        end
        battle_box_snapshot = nil
    end

    if not in_battle then
        local i, held_areas = 1, {}
        while pending_box_captures[i] do
            local pending = pending_box_captures[i]
            if held_areas[pending.area_id] then
                i = i + 1
            else
                local box_addr = nil
                for slot = 0, 29 do
                    local addr = M.pcBoxAddr(pending.box, slot)
                    if addr and fmt("%08X", mem_u32(addr)) == pending.pid then
                        box_addr = addr; break
                    end
                end
                local species_id, ot_id
                if box_addr then
                    species_id, ot_id = M.decrypt_block_a(box_addr)
                end
                if species_id and ot_id then
                    local new_key = pending.pid .. ":" .. fmt("%08X", ot_id)
                    resolved_areas[pending.area_id] = true
                    all_known_keys[new_key] = true
                    send({event="capture", key=new_key, species_id=species_id,
                          area_id=pending.area_id, box_capture=true},
                         "capture(box):" .. new_key:sub(1,8), true)
                    table.remove(pending_box_captures, i)
                else
                    if not pending.logged then
                        pending.logged = true
                        console.log("[SLink] box capture held: identity unavailable for " .. pending.pid)
                        hud_show("!! Box capture pending: identity unavailable", 255, 200, 60, 600)
                    end
                    held_areas[pending.area_id] = true
                    i = i + 1
                end
            end
        end
    end

    -- 9. Read current party
    local curr_party, party_count = index_party(in_battle)

    local party_diff_ok = in_battle or (post_battle_frames > 0 and post_battle_frames < (POST_BATTLE_GRACE - 10))
                          or (not in_battle and post_battle_frames == 0)

    if party_diff_ok then

    -- ── capture ──────────────────────────────────────────────────────────────
    for k, info in pairs(curr_party) do
        if not prev_party[k] and not sync_written_keys[k] then
            if in_battle or post_battle_frames > 0 then
                local evt_area = battle_area_id or area
                captured_this_battle     = true
                resolved_areas[evt_area] = true
                all_known_keys[k]        = true
                send({event="capture", key=k, hp=info.hp, maxHP=info.maxHP,
                      level=info.level, species_id=info.species_id or 0,
                      nickname=info.nickname or "",
                      area_id=evt_area, is_egg=info.is_egg or false},
                     "capture(battle):" .. k:sub(1,8), true)
            elseif all_known_keys[k] then
                if not pending_box_to_party[k] then
                    pending_box_to_party[k] = PARTY_DEBOUNCE_FRAMES
                end
            else
                if not gift_capture_buffer[k] then
                    local gift_area
                    if not nuzlocke_active then
                        gift_area = "intro"
                    elseif area ~= "" then
                        gift_area = area
                    else
                        gift_area = "gift_zone_" .. tostring(zone_id)
                    end
                    gift_capture_buffer[k] = {frame=frame_count, info=info, area=gift_area}
                end
            end
        end
    end

    -- ── gift capture buffer flush ─────────────────────────────────────────────
    for k, buf in pairs(gift_capture_buffer) do
        if not curr_party[k] then
            gift_capture_buffer[k] = nil
        elseif frame_count - buf.frame >= GIFT_BUFFER_WINDOW then
            all_known_keys[k] = true
            gift_capture_buffer[k] = nil
            local info = buf.info
            send({event="capture", key=k, hp=info.hp, maxHP=info.maxHP,
                  level=info.level, species_id=info.species_id or 0,
                  nickname=info.nickname or "",
                  area_id=buf.area, is_egg=info.is_egg or false},
                 "capture(gift):" .. k:sub(1,8), true)
        end
    end

    -- ── faint + party_to_box ─────────────────────────────────────────────────
    local had_alive = false
    local all_zero  = true

    for k, prev_info in pairs(prev_party) do
        local curr_info = curr_party[k]
        if curr_info then
            pending_party_to_box[k] = nil
            if prev_info.hp > 0 then had_alive = true end
            if prev_info.hp > 0 and curr_info.hp == 0 then
                send({event="faint", key=k, area_id=area}, "faint:" .. k:sub(1,8), true)
            end
            if curr_info.hp > 0 then all_zero = false end
        else
            if not in_battle and prev_info.hp > 0 and not sync_written_keys[k] then
                if not pending_party_to_box[k] then
                    pending_party_to_box[k] = {
                        frames = PARTY_DEBOUNCE_FRAMES,
                        info   = {hp=prev_info.hp, maxHP=prev_info.maxHP,
                                  level=prev_info.level, slot=prev_info.slot},
                    }
                end
            end
            all_zero = false
        end
    end

    -- ── debounce: party_to_box ────────────────────────────────────────────────
    for k, ptb in pairs(pending_party_to_box) do
        if curr_party[k] then
            pending_party_to_box[k] = nil
        else
            ptb.frames = ptb.frames - 1
            if ptb.frames <= 0 then
                pending_party_to_box[k] = nil
                local st = ptb.info
                local stats_tbl = st and {level=st.level, maxHP=st.maxHP} or nil
                send({event="party_to_box", key=k, stats=stats_tbl},
                     "party_to_box:" .. k:sub(1,8), true)
            end
        end
    end

    -- ── debounce: box_to_party ────────────────────────────────────────────────
    for k, remaining in pairs(pending_box_to_party) do
        if not curr_party[k] then
            pending_box_to_party[k] = nil
        else
            remaining = remaining - 1
            if remaining <= 0 then
                pending_box_to_party[k] = nil
                send({event="box_to_party", key=k, area_id=area}, "box_to_party:" .. k:sub(1,8), true)
            else
                pending_box_to_party[k] = remaining
            end
        end
    end

    -- ── whiteout ─────────────────────────────────────────────────────────────
    if had_alive and all_zero then
        send({event="whiteout"}, "whiteout", true)
    end

    end -- party_diff_ok

    -- 10. Post-battle grace → no_catch
    if post_battle_frames > 0 then
        post_battle_frames = post_battle_frames - 1
        if post_battle_frames == 0 then
            if nuzlocke_active and battle_is_wild and not captured_this_battle
                    and battle_area_id and battle_area_id ~= ""
                    and not resolved_areas[battle_area_id]
                    and not GIFT_AREAS[battle_area_id] then
                resolved_areas[battle_area_id] = true
                send({event="no_catch", area_id=battle_area_id,
                      species_id=battle_enc_species, level=battle_enc_level},
                     "no_catch:" .. battle_area_id, true)
            end
            captured_this_battle = false
        end
    end

    -- 11. Activate nuzlocke once Pokéballs appear
    if not nuzlocke_active and writes_enabled and frame_count % 15 == 0 and hasPokeballs() then
        nuzlocke_active = true
        console.log("[SLink] nuzlocke ACTIVE (pokeballs in bag)")
        HUD.nuzlocke_start("Nuzlocke Start!")
        if M.playSE then M.playSE(M.SE_NUZLOCKE_START) end
    end

    -- 12. safe — first overworld frame after battle
    if pending_safe and not in_battle then
        pending_safe = false
        send({event="safe"}, "safe", true)
    end

    -- 13. auto tick
    if frame_count % TICK_INTERVAL == 0 then
        local raw_count = M.readPartyCount()
        local save_ok   = raw_count >= 0 and raw_count <= 6
        local evt = {
            event         = "tick",
            ball_count    = countPokeballs(),
            has_pokeballs = nuzlocke_active,
            area_id       = area,
            loc_name      = loc,
            in_battle     = in_battle,
            -- doubleTripleFlag at BATTLE_MODE_ADDR (Gen 5 only): 1/2/3 = double/triple/rotation.
            is_doubles    = M.isDoubleBattle(),
            badges        = M.readBadges1(),
            trainer_name  = M.readTrainerName(),
        }
        if save_ok then
            local snap = build_party_snapshot(in_battle)
            if #snap > 0 then evt.party = snap end
        end
        if in_battle then
            evt.is_trainer_battle = not M.isWildBattle()
            evt.trainer_id = M.readEnemyTrainerId()
            evt.opponent_name = M.readEnemyTrainerName()
            local is_doubles_battle = evt.is_doubles
            local enemy_list = {}
            for ei = 0, 5 do
                local es = M.enemyHP(ei)
                if es then
                    local ea = M.enemyBattleAddr(ei)
                    local e_sid, e_item, e_ability = 0, 0, 0
                    local e_moves, e_pp, e_pp_bonuses, e_form, e_is_egg = nil, nil, 0, 0, false
                    if ea then
                        local sp, _, hi, abl = M.decrypt_block_a_ext(ea)
                        e_sid = sp or 0; e_item = hi or 0; e_ability = abl or 0
                        local blk_b = M.decrypt_block_b(ea)
                        if blk_b then
                            e_moves = blk_b.moves
                            e_pp    = blk_b.pp
                            -- Pack 4 × 2-bit PP Ups into a u8 (matches Gen 3 +0x3A pp_bonuses semantics)
                            local ups = blk_b.pp_ups
                            e_pp_bonuses = ((ups[1] & 0x3))
                                         | ((ups[2] & 0x3) << 2)
                                         | ((ups[3] & 0x3) << 4)
                                         | ((ups[4] & 0x3) << 6)
                            e_form  = blk_b.form
                            e_is_egg = blk_b.is_egg
                        end
                    end
                    -- Active in singles = slot 0; in doubles = slots 0 and 1.
                    local is_active = (ei == 0) or (is_doubles_battle and ei == 1)
                    local e_display = GAME_MODULE.form_display_id(e_sid, e_form)
                    enemy_list[#enemy_list + 1] = {
                        species_id=e_display, level=es.level, hp=es.hp, maxHP=es.maxHP,
                        held_item_id=e_item, ability_id=e_ability,
                        status_cond=es.status_cond or 0,
                        moves=e_moves, pp=e_pp, pp_bonuses=e_pp_bonuses,
                        form=e_form, is_egg=e_is_egg,
                        active=is_active,
                    }
                end
            end
            evt.enemy_party = enemy_list
        end
        -- Incremental PC box scan
        if not in_battle and writes_enabled then
            local box = box_scan_idx
            local entries = {}
            for slot = 0, 29 do
                local addr = M.pcBoxAddr(box, slot)
                if addr then
                    local pid = mem_u32(addr)
                    if pid ~= 0 then
                        local sp, ot, hi, abl = M.decrypt_block_a_ext(addr)
                        -- Skip slots whose block-A doesn't decrypt to a valid
                        -- species. Without this filter, garbage PIDs (e.g. from
                        -- a wrong PC_BOX_STRIDE landing in non-PC RAM) get sent
                        -- as ghost "species_id=0" entries that render as ????
                        -- in the PC UI and trigger spurious memorial-box
                        -- contamination warnings server-side.
                        if sp and sp >= 1 then
                            local key = ot and fmt("%08X:%08X", pid, ot) or fmt("%08X", pid)
                            local nick = M.readNickname(addr)
                            entries[#entries + 1] = {
                                box=box, slot=slot, key=key, species_id=sp,
                                held_item_id=hi or 0, ability_id=abl or 0,
                                nickname=nick or "",
                            }
                            if nick and nick ~= "" then _nick_cache[key] = nick
                            elseif not _nick_cache[key] then
                                _nick_cache[key] = fmt("species#%d", sp)
                            end
                        end
                    end
                end
            end
            pc_box_cache[box] = entries
            box_scan_idx = (box_scan_idx + 1) % BOX_SCAN_TOTAL
            local flat = {}
            for _, bx_entries in pairs(pc_box_cache) do
                for _, e in ipairs(bx_entries) do flat[#flat + 1] = e end
            end
            evt.pc_boxes = flat
        end
        send(evt, "tick(auto)", true, true)
    end

    -- 14. Manual F keys
    local keys = input.get()
    local function pressed(k) return keys[k] and not prev_keys[k] end

    if pressed("F1") then
        send({event="area_enter", area_id=area},
             "area_enter:" .. (area~="" and area or "(none)"), false)
    end
    if pressed("F2") then
        local base = M.partyAddr(0)
        if base and slotOccupied(base) then
            local k  = cachedMonKey(0, base)
            local hp = mem_u16(base + M.PKM.CUR_HP)
            local mx = mem_u16(base + M.PKM.MAX_HP)
            local lv = mem_u8 (base + M.PKM.LEVEL)
            send({event="capture", key=k, hp=hp, maxHP=mx, level=lv, area_id=area},
                 "capture(manual):" .. k:sub(1,8), false)
        else console.log("[SLink] F2: slot 0 empty") end
    end
    if pressed("F3") then
        local base = M.partyAddr(0)
        if base and slotOccupied(base) then
            local k = cachedMonKey(0, base)
            send({event="faint", key=k, area_id=area}, "faint:" .. k:sub(1,8), false)
        else console.log("[SLink] F3: slot 0 empty") end
    end
    if pressed("F4") then
        local area4 = area ~= "" and area or "(none)"
        send({event="no_catch", area_id=area4}, "no_catch:" .. area4, false)
    end
    if pressed("F5") then send({event="whiteout"}, "whiteout",    false) end
    if pressed("F6") then send({event="safe"},      "safe",        false) end
    if pressed("F7") then
        send({event="tick", ball_count=countPokeballs(), has_pokeballs=nuzlocke_active,
              area_id=area, in_battle=in_battle,
              party=build_party_snapshot(in_battle)}, "tick(manual)", false)
    end
    if pressed("F8") then
        local addr = M.partyAddr(0)
        if addr and mem_u32(addr) ~= 0 then
            local k = cachedMonKey(0, addr)
            send({event="party_to_box", key=k}, "party_to_box(manual):" .. k:sub(1,8), false)
        end
    end
    if pressed("F9") then
        if writes_enabled then
            local addr = M.partyAddr(0)
            local f9_count = M.readPartyCount()
            if f9_count <= 1 then
                console.log("[SLink] F9: blocked — would empty party")
            elseif addr and mem_u32(addr) ~= 0 then
                local k = cachedMonKey(0, addr)
                pcall(exec_memorialize, k)
            end
        else
            console.log("[SLink] F9: writes disabled — cannot memorialize")
        end
    end
    prev_keys = keys

    -- 17. Advance prev state
    sync_written_keys = {}
    if party_diff_ok then
        prev_party = curr_party
        for k, ptb in pairs(pending_party_to_box) do
            if not prev_party[k] then prev_party[k] = ptb.info end
        end
    end
    prev_zone_id   = zone_id
    prev_area      = area
    prev_loc       = loc
    prev_in_battle = in_battle
end

local function on_frame_safe()
    local ok, err = pcall(on_frame)
    if not ok then console.log("[SLink] ERROR (handler kept alive): " .. tostring(err)) end
    -- HUD render is protected separately so a tick error never skips clearGraphics
    -- (the surface must be cleared every render or stale HUD text never leaves the screen).
    local hok, herr = pcall(hud_render)
    if not hok then console.log("[SLink] HUD ERROR (handler kept alive): " .. tostring(herr)) end
end

-- ── Startup ────────────────────────────────────────────────────────────────────
console.clear()
C.init(SERVER_HOST, SERVER_PORT)
console.log(fmt("[SLink] TCP: %s:%d  Player: %s", SERVER_HOST, SERVER_PORT, PLAYER_ID))
console.log(fmt("[SLink] Game: %s (NDS Gen 5)  Profile: direct-addressing", _ROM_TYPE))
console.log("[SLink] VERIFY_ME: PC_STORAGE_BASE, BALLS_POCKET_OFF, PLAYER_NAME_OFF")
console.log("[SLink] Auto: hello area_enter capture(battle/gift) box_to_party party_to_box faint no_catch(wild) whiteout safe tick")
console.log("[SLink] Manual: F1=area F2=capture F3=faint F4=no_catch F5=whiteout F6=safe F7=tick F8=deposit F9=memorialize")
console.log("[SLink] --- monitoring started ---")

-- Seed prev state
do
    local b = M.init()
    if b ~= nil then
        prev_party, _ = index_party(false)
        for k in pairs(prev_party) do all_known_keys[k] = true end
        for box = 0, BOX_SCAN_TOTAL - 1 do
            for slot = 0, 29 do
                local addr = M.pcBoxAddr(box, slot)
                if addr then
                    local pid = mem_u32(addr)
                    if pid ~= 0 then
                        local sp, ot = M.decrypt_block_a_ext(addr)
                        if ot then all_known_keys[fmt("%08X:%08X", pid, ot)] = true end
                    end
                end
            end
        end
        prev_zone_id = M.readZoneID()
        prev_area    = AREAS[prev_zone_id] or ""
        prev_loc     = LOCATIONS[prev_zone_id] or ""
        prev_in_battle = M.isInBattle()
    end
end

event.onframeend(on_frame_safe, "slink_events")
console.log("[SLink] Running — play normally to trigger events…")
