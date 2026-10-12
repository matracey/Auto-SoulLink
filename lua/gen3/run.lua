-- lua/gen3/run.lua — BizHawk entry for the Gen 3 (FRLG) production client.
--
-- Loaded by lua/slink.lua's Gen 3 route (mirrors lua/gen1/run.lua 1:1, per
-- docs/gen3/research/p4_gen1_contract_map.md §3.5). Everything game-related is built by
-- lua/gen3/entry.lua; this file only supplies the BizHawk-shaped io/ev, the LuaSocket
-- transport, the HUD and the frame loop.
--
-- The foundation (gen3_frlg vs gen3_rr, clean/named) is decided by Entry.admit from the
-- cartridge's hash/anchors/header, never guessed here.
local _dir = (debug.getinfo(1, "S").source:match("@(.+[/\\])") or "./")
local ROOT = _dir:gsub("[/\\]lua[/\\]gen3[/\\]?$", "")
if ROOT == _dir then ROOT = _dir .. "../.." end
package.path = ROOT .. "/lua/?.lua;" .. package.path

local Entry = dofile(ROOT .. "/lua/gen3/entry.lua")
local C = require("connector")
local H = require("hud")
local START_REFUSED = "SLINK COULD NOT START - SEE LOG"

local host = SLINK_HOST or os.getenv("SLINK_HOST") or "127.0.0.1"
local port = tonumber(SLINK_PORT or os.getenv("SLINK_PORT") or 54321)
local player = SLINK_PLAYER or os.getenv("SLINK_PLAYER") or "a"

-- >>> singleton guard >>>
local function acquire_instance_guard(socket_module, guard_port)
    local guard, why = socket_module.socket.tcp()
    if not guard then return nil, why end
    guard:settimeout(0)
    local ok, bind_why = guard:bind("127.0.0.1", guard_port)
    if not ok then guard:close(); return nil, bind_why end
    local listening, listen_why = guard:listen(1)
    if not listening then guard:close(); return nil, listen_why end
    return guard
end
-- <<< singleton guard <<<

-- The io/ev adapters (copied from lua/gen3/shadow_run.lua build_io:58-85 / build_ev:92-119,
-- not dofile'd from it: shadow_run.lua is P3's read-only observer bootstrap and is
-- deliberately not shipped in the player release -- tools/make_release.py _LUA_GEN3). This is
-- the same shape, plus the one real write sink and saveram P3 deliberately refused.
local io_ = {
    read_u8    = function(addr) return memory.read_u8(addr, "System Bus") end,
    read_u16   = function(addr) return memory.read_u16_le(addr, "System Bus") end,
    read_u32   = function(addr) return memory.read_u32_le(addr, "System Bus") end,
    read_bytes = function(addr, len)
        local out = {}
        for i = 1, len do out[i] = memory.read_u8(addr + i - 1, "System Bus") end
        return out
    end,
    rom_read = function(off, len)
        local out = {}
        for i = 1, len do out[i] = memory.read_u8(off + i - 1, "ROM") end
        return out
    end,
    framecount = function() return emu.framecount() end,
    register   = function(name) return emu.getregister(name) end,
    write_u8   = function(addr, v) return memory.write_u8(addr, v, "System Bus") end,
    saveram    = function()
        assert(client and client.saveram, "SaveRAM host API unavailable")
        return client.saveram()
    end,
}

-- Hook names prefixed "SLink-gen3-" (never "-shadow-": this instance mutates game state).
local ev = {
    on_bus_exec = function(fn, addr, name)
        return event.on_bus_exec(fn, addr, "SLink-gen3-" .. tostring(name))
    end,
    unregister = function(id) return event.unregisterbyid(id) end,
}

local ok_hc, header_code = pcall(Entry.header_code, io_.rom_read)
-- admit_routed (not the bare Entry.admit): this file is dofile'd directly by the duo harness,
-- bypassing lua/slink.lua's launcher gate entirely, so the ROUTED/header-only policy must live
-- here too, not just in the launcher (OMP cx-dbabbd62 -- the duo evidence never proved it).
local admitted, why = Entry.admit_routed({
    root = ROOT, json = dofile(ROOT .. "/lua/json_codec.lua"),
    rom_hash = gameinfo and gameinfo.getromhash and gameinfo.getromhash() or "",
    rom_read = io_.rom_read, header_code = ok_hc and header_code or "",
})
if not admitted then
    local msg = "[SLink-gen3] refused: " .. tostring(why)
    console.log(msg)
    H.init({ screen_w = 240, screen_h = 160 })
    H.show(START_REFUSED, 255, 80, 80, 600)
    H.render()
    return
end

local socket_module = require("socket")
local guard_port = 55000 + ((port or 54321) % 5000) * 2 + (player == "b" and 1 or 0)
local instance_guard, instance_error = acquire_instance_guard(socket_module, guard_port)
if not instance_guard then
    local msg = "[SLink-gen3] refused: another Gen 3 client is already running"
    console.log(msg .. " (" .. tostring(instance_error) .. ")")
    H.init({ screen_w = 240, screen_h = 160 })
    H.show(START_REFUSED, 255, 80, 80, 600)
    H.render()
    return
end
local function release_instance_guard()
    if instance_guard then
        pcall(function() instance_guard:close() end)
        instance_guard = nil
    end
end

H.init({ screen_w = 240, screen_h = 160 })
C.init(host, port)

-- Battle request nonce seed (card C5-10b). This file is the BizHawk-facing bootstrap, so it is
-- where process-unique entropy is gathered: wall clock, the process CPU clock (sub-second), a
-- random draw seeded from both, and the admitted ROM's hash. Client.new itself never touches an
-- emulator global -- it just consumes the string this produces -- and a harness can override the
-- whole thing with $SLINK_GEN3_BATTLE_NONCE (see entry.lua) for determinism.
-- >>> session counter (C5-11b..C5-11d; keep this block self-contained -- tests/unit/
-- test_gen3_patch_sources.py extracts it by these markers, drives it through a fake filesystem) >>>
-- WHAT IT IS FOR. The session nonce must never repeat for one player across client processes: the
-- server keeps a queued replace_rival_team for a player slot across that slot's restart, and the
-- client accepts it only when cmd.session equals ITS nonce (docs/gen3/research/
-- rival_swap_refresh_window.md §3.3). os.time() repeats inside a second and stock BizHawk Lua has
-- no process id, so the unique part is a per-install counter, allocated here. It lives at the
-- INSTALL ROOT (ROOT, next to slink_lua.log), because a player's release has no patch/build/.
--
-- WHY IT IS UNIQUE. An OS-exclusive .lock handle serializes the ENTIRE allocation transaction.
-- Inside that guard, a caller renames the baton to a private name, reads n, and publishes n+1
-- from a fresh file; a failed write does not truncate the old counter. Installed Windows NLua
-- demonstrated that two concurrent C os.rename calls can both report success for the baton
-- while only one private destination survives, so rename alone is not an ownership proof.
--
-- FIRST CREATION is the one step where the OSes differ, because a missing baton is ambiguous
-- (never created, or held by someone right now). A baton is created only when the baton AND the
-- birth record are BOTH positively absent (errno ENOENT, never a guess from a failed open) and
-- this caller wins the birth record, which is decided exactly once per install:
--   * Windows: rename(tmp, born) -- C rename refuses an existing destination, so one caller wins,
--     ever.
--   * POSIX (Linux): rename would REPLACE, so the record is an append-only log instead: each
--     claimant appends its token with "a" (O_APPEND: every append lands at the then-current end,
--     atomically on a local filesystem) and the winner is the token on the FIRST line, which no
--     later append can change. The one non-structural step: two first-ever claimants in the same
--     instant must draw distinct tokens (wall second + CPU clock + a 31-bit draw).
-- The birth record is never removed, so a missing baton after birth may mean a holder, a crashed
-- holder, or a winner that died before publishing generation zero: wait, then fail closed.
-- Nothing ever recreates it.
--
-- FAIL CLOSED (C5-11d MAJOR 2). nil means the client mints no identity, declares no capability and
-- refuses every rival command. nil is returned for: a baton still missing after the bounded wait;
-- an unreadable, empty, non-integral or out-of-range baton, or a write, flush, close or rename that
-- does not report success (the old baton is restored when its rename succeeds; a failed restore
-- remains fail-closed and needs operator inspection). There is no
-- restart-at-1 path: the only initialisation is the won birth above.
--
-- RECOVERY (owner action, never automatic; Codex REV4): inspect EVERY generation artifact before
-- touching anything. Cleanup of a published holder's file can fail silently, so several
-- slink_gen3_session.baton.<token> files may exist and a filename's existence does not mean its
-- holder still owns it. Restore only the file holding the HIGHEST generation, and only when that
-- choice is unambiguous; never pick an arbitrary .baton.* file, never restore an older generation
-- (that reissues a value), and never remove slink_gen3_session.born. Every temporary name assumes
-- distinct tokens: a same-token collision on POSIX can replace another caller's held file.
-- On the installed Windows Lua runtime, two concurrent os.rename calls can both report success
-- for one baton source while only one private destination survives. The permanent .lock file is
-- only an OS-exclusive MUTEX around this existing transaction; .born/.baton remain the freshness
-- authority. An old client that ignores .lock must not run beside an updated one on one install.
local function acquire_session_guard(path)
    if not luanet or not luanet.import_type then return nil, "unavailable" end
    local ok, stream = pcall(function()
        local File = assert(luanet.import_type("System.IO.File"))
        local Mode = assert(luanet.import_type("System.IO.FileMode"))
        local Access = assert(luanet.import_type("System.IO.FileAccess"))
        local Share = assert(luanet.import_type("System.IO.FileShare"))
        return File.Open(path,Mode.OpenOrCreate,Access.ReadWrite,Share.None)
    end)
    if ok then return stream end
    local known, code = pcall(function() return stream.InnerException.HResult end)
    if known and (code == -2147024864 or code == -2147024863) then return nil, "busy" end
    return nil, "unavailable" -- ACL, absent parent, or unavailable CLR: never create a new counter
end
local function next_session_counter(root, open_file, remove_file, spin, rename_file, windows, acquire_guard)
    open_file, remove_file, rename_file = open_file or io.open, remove_file or os.remove,
                                          rename_file or os.rename
    if windows == nil then windows = package.config:sub(1, 1) == "\\" end
    spin = spin or function() local t = os.clock() + 0.005 while os.clock() < t do end end
    local ENOENT = 2                                -- the same errno in MSVC's CRT and on POSIX
    local base = root .. "/slink_gen3_session"
    local baton, born = base .. ".baton", base .. ".born"
    local token = string.format("%d-%d-%d", os.time(), math.floor(os.clock() * 1e6) % 1000000000,
                                math.random(0, 2147483647))
    local mine = baton .. "." .. token             -- the baton's name while this call holds it

    local function put(path, text, mode)           -- true only if write, flush and close succeed
        local f = open_file(path, mode or "w")
        if not f then return false end
        local ok = f:write(text) ~= nil
        ok = f:flush() ~= nil and ok
        return f:close() ~= nil and ok
    end
    local function get(path)                       -- content, or nil + errno
        local f, _, errno = open_file(path, "r")
        if not f then return nil, errno end
        local s = f:read("a")
        if f:close() == nil then return nil end
        return s
    end
    local function won_birth()
        if windows then
            local tmp = base .. ".born." .. token
            if put(tmp, token) and rename_file(tmp, born) then return true end
            remove_file(tmp)
            return false
        end
        if not put(born, token .. "\n", "a") then return false end
        local s = get(born)
        return s ~= nil and s:match("^[^\n]*") == token
    end

    local function allocate_under_guard()
        local birth_checked = false
        -- The OS guard serializes updated clients' birth and baton transactions. This bounded
        -- loop remains for a legacy holder that removed the baton without taking .lock; in
        -- production, spin advances an emulator frame instead of burning CPU. A crashed holder
        -- or birth winner still fails closed after the bound; no caller recreates a born baton.
        for _ = 1, 3000 do
            local took, _, errno = rename_file(baton, mine)
            if took then
                local s = get(mine)
                local n = s and s:match("^%d+$") and tonumber(s)
                local nxt = mine .. ".next"
                if n and n < 4294967295 and put(nxt, tostring(n + 1)) and rename_file(nxt, baton) then
                    remove_file(mine)
                    return n + 1
                end
                remove_file(nxt)
                rename_file(mine, baton)               -- put it back as found: fail closed, no reset
                return nil
            end
            if errno == ENOENT and not birth_checked then
                birth_checked = true
                local _, born_errno = get(born)
                if born_errno == ENOENT and won_birth() then
                    local tmp = base .. ".new." .. token
                    if not put(tmp, "0") then
                        remove_file(tmp)
                        return nil
                    end
                    -- Only this caller won the permanent birth record. A transient rename/share
                    -- failure gets a bounded retry of the SAME prepared generation. Exhaustion
                    -- remains fail-closed; no later caller may initialise the counter.
                    local published = false
                    for _ = 1, 3000 do
                        if rename_file(tmp, baton) then published = true; break end
                        spin()
                    end
                    if not published then
                        remove_file(tmp)
                        return nil
                    end
                end
            end
            spin()
        end
        return nil
    end
    local guard, why = (acquire_guard or acquire_session_guard)(base .. ".lock")
    if not guard then return nil, why end
    local ok, value = pcall(allocate_under_guard)
    local released = pcall(function() guard:Dispose() end)
    if not ok or not released then return nil, "unavailable" end
    return value
end
-- <<< session counter <<<

local session_counter, counter_reason
-- A sharing collision is retried only after BizHawk advances a frame, so both windows keep
-- progressing while the other owns the lock. No loser ever recreates a born baton.
for _ = 1, 600 do
    session_counter, counter_reason = next_session_counter(ROOT, nil, nil, function() emu.frameadvance() end)
    if session_counter or counter_reason ~= "busy" then break end
    emu.frameadvance()
end
if not session_counter then
    console.log("[SLink-gen3] session counter unavailable (slink_gen3_session.baton held or unreadable at the install root); native trade is off this session")
end
local battle_nonce_seed = nil
if session_counter then
    math.randomseed(os.time() + math.floor(os.clock() * 1000))
    local rom_bits = tonumber((admitted.rom_hash or ""):sub(1, 8), 16) or 0
    battle_nonce_seed = string.format("%08X%08X", session_counter % 4294967296,
                                      (math.floor(os.clock() * 1e6)
                                       + math.random(0, 4294967295) + rom_bits) % 4294967296)
end

-- >>> durable trade storage (T3-R5; self-contained host-adapter MODEL seam) >>>
-- NLua's CLR bridge gives us OS-exclusive handles and FileStream.Flush(true),
-- rather than treating Lua fflush/close as a disk-durability promise. No fallback
-- weakens this contract: absent CLR/file APIs leave the trade journal unavailable.
local function trade_file_adapter(import_type)
    local File = assert(import_type("System.IO.File"), "System.IO.File unavailable")
    local Mode = assert(import_type("System.IO.FileMode"))
    local Access = assert(import_type("System.IO.FileAccess"))
    local Share = assert(import_type("System.IO.FileShare"))
    local Seek = assert(import_type("System.IO.SeekOrigin"))
    local UTF8Encoding = assert(import_type("System.Text.UTF8Encoding"))
    local utf8 = UTF8Encoding(false,true)
    local Reader = assert(import_type("System.IO.StreamReader"))
    local Writer = assert(import_type("System.IO.StreamWriter"))
    -- NLua wraps CLR exceptions from File.Open in LuaScriptException. Its printed message
    -- contains an opaque number; the inner exception preserves the actual Win32 verdict.
    local function lock_cause(err)
        local ok, kind, code, message = pcall(function()
            local inner = err.InnerException
            return tostring(inner:GetType().FullName), tonumber(inner.HResult), tostring(inner.Message)
        end)
        if ok then return kind, code, message end
        return nil, nil, tostring(err)
    end
    local function lock_contention(err)
        local _, code = lock_cause(err)
        -- Sharing/lock violation (32/33), or CreateNew seeing an existing guard (80/183).
        return code == -2147024864 or code == -2147024863
            or code == -2147024816 or code == -2147024713
    end
    local function finish(stream, fn)
        local ok, result = pcall(fn)
        local closed, why = pcall(function() stream:Dispose() end)
        assert(closed, why)
        if not ok then error(result) end
        return result
    end
    local function write(stream, value, truncate)
        if truncate then stream:Seek(0,Seek.Begin); stream:SetLength(0) end
        local writer = Writer(stream,utf8,4096,true)
        return finish(writer,function()
            writer:Write(value)
            writer:Flush()
            stream:Flush(true)
            return true
        end)
    end
    return {
        lock=function(path)
            local fresh = not File.Exists(path)
            local ok, stream = pcall(function()
                return File.Open(path,fresh and Mode.CreateNew or Mode.Open,Access.ReadWrite,Share.None)
            end)
            if ok then return stream,fresh end
            if lock_contention(stream) then
                return nil,false,"busy" -- next emulator frame retries; no journal bytes were touched
            end
            local kind, code, message = lock_cause(stream)
            message = tostring(message):gsub("[\r\n]", " ")
            error(string.format("exclusive trade journal lock unavailable: guard=%s type=%s hresult=%s message=%s",
                                tostring(path), tostring(kind or "unknown"), tostring(code or "unknown"), message))
        end,
        close=function(stream) stream:Dispose(); return true end,
        read_handle=function(stream)
            stream:Seek(0,Seek.Begin)
            local reader = Reader(stream,utf8,false,4096,true)
            return finish(reader,function() return reader:ReadToEnd() end)
        end,
        write_handle=function(stream,value) return write(stream,value,true) end,
        read_file=function(path) if not File.Exists(path) then return nil end; return File.ReadAllText(path,utf8) end,
        create_file=function(path,value)
            local stream = File.Open(path,Mode.CreateNew,Access.Write,Share.None)
            return finish(stream,function() return write(stream,value,false) end)
        end,
        append_file=function(path,value)
            local stream = File.Open(path,Mode.Open,Access.Write,Share.None)
            return finish(stream,function() stream:Seek(0,Seek.End); return write(stream,value,false) end)
        end,
    }
end

local function trade_battery_path(import_type, config, name, system, movie_active)
    assert(system == "GBA" and movie_active == false, "battery proof requires a non-movie GBA session")
    assert(type(name) == "string" and name ~= "", "loaded cartridge name unavailable")
    local GameInfo = assert(import_type("BizHawk.Emulation.Common.GameInfo"))
    local Paths = assert(import_type("BizHawk.Client.Common.PathEntryExtensions"))
    local info = GameInfo()
    info.Name, info.System = name, system
    -- The same public path builder used by EmuHawk, including FilesystemSafeName
    -- and configured Save RAM directory. Do not guess a filename or flush RAM here.
    return Paths.SaveRamAbsolutePath(config.PathEntries,info,nil)
end

local function trade_live_snapshot(io_, profile, layout)
    local a, d = profile.ram, profile.derived
    local sb1, sb2 = io_.read_u32(layout.sb1_ptr or a.SB1_PTR_ADDR), io_.read_u32(layout.sb2_ptr or a.SB2_PTR_ADDR)
    assert(sb1 >= 0x02000000 and sb1 < 0x02040000 and sb2 >= 0x02000000 and sb2 < 0x02040000,
           "live save blocks unavailable")
    local count = io_.read_u8(a.PARTY_COUNT_ADDR)
    assert(count >= 1 and count <= 6, "live party unavailable")
    local base = d.PARTY_IN_SB1 and sb1 + d.SB1_PARTY_BASE_OFFSET or a.PARTY_BASE
    local bytes, out = io_.read_bytes(base,count*100), {}
    for i=1,#bytes do out[i] = string.char(bytes[i]) end
    return {sb1=sb1, sb2=sb2, party_count=count, party=table.concat(out),
            counter=io_.read_u32(layout.counter), ot_id=io_.read_u32(sb2+d.SB2_OT_ID_OFFSET)}
end
-- <<< durable trade storage <<<

local recovery_json = dofile(ROOT .. "/lua/json_codec.lua")
if Entry.trade_journal_supported(ROOT, recovery_json, admitted) then
    local Journal = dofile(ROOT .. "/lua/gen3/trade_journal.lua")
    local json = recovery_json
    local ok, store = pcall(function()
        assert(luanet and luanet.import_type, "CLR durability adapter unavailable")
        luanet.load_assembly("BizHawk.Emulation.Common")
        luanet.load_assembly("BizHawk.Client.Common")
        return Journal.file_store({json=json, fs=trade_file_adapter(luanet.import_type),
                                   path=ROOT .. "/slink_gen3_trade"})
    end)
    if not ok then
        local why = tostring(store)
        store = {read=function() error(why) end, update=function() error(why) end}
    end
    io_.trade_journal = Journal.new({json=json, store=store, rom_sha1=admitted.rom_hash:lower(), player=player,
                                    frame=io_.framecount, log=function(message) console.log(message) end})
    io_.trade_reload_proof = function(title, profile, boot_seen)
        local layout = Journal.RELOAD_LAYOUTS[title]
        if not layout then return nil, "reload layout remains unqualified for " .. tostring(title) end
        local function path()
            assert(gameinfo.getromhash():lower() == admitted.rom_hash:lower(), "loaded cartridge changed")
            return trade_battery_path(luanet.import_type,client.getconfig(),gameinfo.getromname(),
                                      emu.getsystemid(),movie.isloaded())
        end
        local function read(file)
            local handle = assert(io.open(file,"rb"), "on-disk battery unavailable")
            local bytes = handle:read("a")
            assert(handle:close(), "battery read close failed")
            return bytes
        end
        local battery = path()
        local before = trade_live_snapshot(io_,profile,layout)
        local flash_before = read(battery)
        local after = trade_live_snapshot(io_,profile,layout)
        local flash_after = read(battery)
        assert(path() == battery, "selected battery changed during proof")
        return Journal.verify_reload({title=title, rom_sha1=admitted.rom_hash:lower(), boot_seen=boot_seen,
                                      ram_before=before, ram_after=after,
                                      flash_before=flash_before, flash_after=flash_after})
    end
end

local ok_build, client = pcall(Entry.build, {
    root = ROOT, mode = "production", io = io_, ev = ev, net = C, hud = H,
    pack = admitted.pack, title = admitted.title, kind = admitted.kind, player = player,
    rom_sha1 = admitted.rom_hash, log = function(t) console.log(t) end,
    battle_nonce_seed = battle_nonce_seed,
})
if not ok_build then
    local msg = "[SLink-gen3] refused to start: " .. tostring(client)
    console.log(msg)
    H.show(START_REFUSED, 255, 80, 80, 600)
    H.render()
    release_instance_guard()
    return
end
local ok, err = pcall(function() client:start() end)
if not ok then
    local msg = "[SLink-gen3] refused to start: " .. tostring(err)
    console.log(msg)
    H.show(START_REFUSED, 255, 80, 80, 600)
    H.render()
    release_instance_guard()
    return
end
console.log(string.format("[SLink-gen3] %s/%s (%s by %s) player %s -> %s:%d (rom %s)",
                          admitted.pack, admitted.title, admitted.kind, admitted.admitted_by,
                          player, host, port, tostring(admitted.rom_hash):sub(1, 8)))

-- Exposed for live gates and the console.
SLINK_GEN3_CLIENT = client

event.onframeend(function()
    local fok, ferr = pcall(function() client:frame_end() end)
    if not fok then console.log("[SLink-gen3] frame error: " .. tostring(ferr)) end
    H.render()
end)
event.onexit(function()
    pcall(function() client:stop() end)
    release_instance_guard()
end)
