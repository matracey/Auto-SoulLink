-- tools/polished_live/duo.lua -- ONE side of the two-instance Polished smoke (card C8), launched by duo.py.
-- Per instance: the REAL client through the real entry (lua/slink.lua -> gen2 -> compose_polished), CONTINUE into the
-- fixture overworld, wait for the client's own hello, then HOLD (client keeps ticking) until the runner drops
-- <POL_RUN>/stop. The driver presses buttons and reads; it writes no game memory. Evidence it prints:
--   DUO_CLIENT {...}   at hello + settle (client-side hello, command tally, write count)
--   DUO_FINAL  {...}   at stop
--   RESULT: PASS|FAIL duo-side-<role>   (the exit marker; absent when the runner kills the process)
-- The authoritative census/admission evidence is read from the SERVER by duo.py; this file only supplies the
-- client's view and the zero-write measurement (the same memory.write_* tap live.lua uses).
local L = dofile(os.getenv("SLINK_ROOT") .. "/tools/polished_live/pol_lib.lua")
local fmt, J = string.format, L.json
local ROLE = os.getenv("DUO_ROLE") or "?"
local SETTLE = tonumber(os.getenv("DUO_SETTLE")) or 600       -- frames after hello so ticks + server replies land
local HOLD_CAP = tonumber(os.getenv("DUO_HOLD_CAP")) or 90000 -- a driver that never gets `stop` must still end
client.speedmode(400)
L.log(fmt("[duo %s] boot frame %d rom %s", ROLE, emu.framecount(), gameinfo.getromhash()))

-- write tap (every Lua-originated memory write, client included; this driver writes nothing)
local lua_writes = {}
local G = dofile(L.ROOT .. "/tools/polished_live/recording_guard.lua")
memory = G.observe_memory(memory, function(k, ...)
    local a = {...}
    lua_writes[#lua_writes + 1] = fmt("%s(%s,%s) frame %d", k, tostring(a[1]), tostring(a[2]), emu.framecount())
end)

SLINK_HOST, SLINK_PORT, SLINK_PLAYER = os.getenv("SLINK_HOST"), tonumber(os.getenv("SLINK_PORT")), os.getenv("SLINK_PLAYER")
-- read-only exec probes L.to_overworld needs (L.ow_idle = OWPlayerInput seen; B on the options joypad loop), registered
-- BEFORE the client exactly as live.lua does
for _, name in ipairs({"OWPlayerInput", "SetInitialOptions.joypad_loop"}) do L.hook(name) end
dofile(L.ROOT .. "/lua/slink.lua")
if not SLINK_GEN2_CLIENT then L.die("the Gen 2 route did not start a client") end
local P = SLINK_GEN2_PARTS
L.check("admitted as the Polished overlay (DEV_OVERLAY_SHA1)", P.pack == "polished_crystal" and P.title == "polished"
        and P.artifact_kind == "overlay" and P.qualification == "DEV_OVERLAY_SHA1")

-- wire tap on the connector: first hello line + a tally of every command name the server sent back
local C = package.loaded["connector"]
local hello, tally, last_gen, last_n = nil, {}, nil, nil
local orig_send, orig_recv = C.send, C.receive
C.send = function(line, ...)
    local ok, msg = pcall(J.decode, line)
    if ok and type(msg) == "table" then
        if msg.event == "hello" and not hello then hello = msg end
        if msg.event == "tick" and msg.pc_boxes ~= nil then last_gen, last_n = msg.pc_boxes_generation, #msg.pc_boxes end
    end
    return orig_send(line, ...)
end
C.receive = function(...)
    local line = orig_recv(...)
    if line ~= nil then
        local ok, msg = pcall(J.decode, line)
        if ok and type(msg) == "table" and type(msg.commands) == "table" then
            for _, c in ipairs(msg.commands) do
                local n = type(c) == "table" and tostring(c.cmd) or "?"
                tally[n] = (tally[n] or 0) + 1
            end
        end
    end
    return line
end

if not L.to_overworld(24, 3, 60, 8000, "continue") then L.die("CONTINUE did not reach map 24:3") end
local f0 = emu.framecount()
while not SLINK_GEN2_CLIENT.hello_sent and emu.framecount() - f0 < 1800 do L.frame() end
L.check("client sent its hello", hello ~= nil)
L.idle(SETTLE)

local function summary()
    local keys = {}
    for _, e in ipairs(hello and hello.party or {}) do keys[#keys + 1] = tostring(e.key) end
    local panel = P.panel_writes and P.panel_writes.log or {}
    return J.encode({role = ROLE, frame = emu.framecount(), rom_sha1 = hello and hello.rom_sha1, runtime_sha1 = P.runtime_rom_sha1,
        rom_type = hello and hello.rom_type, foundation = hello and hello.foundation, artifact_kind = hello and hello.artifact_kind,
        party_count = #(hello and hello.party or {}), party_keys = table.concat(keys, ","),
        hello_pc_boxes = hello and #(hello.pc_boxes or {}), hello_pc_generation = hello and hello.pc_boxes_generation,
        tick_pc_boxes = last_n, tick_pc_generation = last_gen, ot_id = hello and hello.ot_id,
        trainer_name = hello and hello.trainer_name, commands = tally, lua_writes = #lua_writes, panel_writes = #panel})
end
L.check("hello rom_sha1 is the client rehash", hello ~= nil and hello.rom_sha1 == P.runtime_rom_sha1)
L.check("hello carried a complete census generation", hello ~= nil and hello.pc_boxes_generation ~= nil)
L.log("DUO_CLIENT " .. summary())

local stop, held = L.RUN .. "/stop", 0
while held < HOLD_CAP do
    if held % 120 == 0 then
        local fh = io.open(stop, "rb")
        if fh then fh:close() break end
    end
    L.frame()
    held = held + 1
end
local forbidden = 0
for _, n in ipairs({"box_mon", "party_mon", "force_faint", "force_explode", "memorialize", "replace_rival_team"}) do
    forbidden = forbidden + (tally[n] or 0)
end
L.check("no Lua-originated memory write (client included)", #lua_writes == 0,
        #lua_writes > 0 and table.concat(lua_writes, "; "):sub(1, 600) or 0)
L.check("no box/party/faint command received", forbidden == 0, forbidden)
L.check("stop file seen (not the hold cap)", held < HOLD_CAP, held)
L.log("DUO_FINAL " .. summary())
L.finish("duo-side-" .. ROLE)
