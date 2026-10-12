-- tools/polished_live/pol_lib.lua -- shared helpers for the Polished live drivers (setup.lua, live.lua).
-- Launched by tools/polished_live/harness.py, which exports SLINK_ROOT, POL_OUT (milestone log), POL_SYMS
-- (the needed rows of data/polished/polished_slink.sym as {name: [bank, addr]}) and POL_RUN (run dir).
-- Read-only probes: every hook only records frame/bank/registers. Milestones are logged, never per frame.
local L = {}
L.ROOT = assert(os.getenv("SLINK_ROOT"), "SLINK_ROOT unset: launch via tools/polished_live/harness.py")
L.OUT = assert(os.getenv("POL_OUT"), "POL_OUT unset")
L.RUN = assert(os.getenv("POL_RUN"), "POL_RUN unset")
L.json = dofile(L.ROOT .. "/lua/json_codec.lua")
local fmt = string.format

local function slurp(path)
    local fh = assert(io.open(path, "rb"), "cannot read " .. path)
    local s = fh:read("*a")
    fh:close()
    return s
end
L.slurp = slurp
L.SYM = L.json.decode(slurp(assert(os.getenv("POL_SYMS"), "POL_SYMS unset")))
L.failures = 0

function L.log(s)
    local f = io.open(L.OUT, "a")
    if f then f:write(s .. "\n") f:close() end
    console.log(s)
end
function L.check(what, ok, detail)
    if not ok then L.failures = L.failures + 1 end
    -- One check is one physical line; raw detail cannot forge another check.
    local function one_line(value)
        return (tostring(value):gsub("\r\n", "\n"):gsub("[\r\n]", "\\n"))
    end
    L.log(fmt("  [%s] %s%s", ok and "ok" or "FAIL", one_line(what), detail ~= nil and ("  -- " .. one_line(detail)) or ""))
    return ok
end
function L.finish(tag)
    L.log(fmt("RESULT: %s %s (%d checks failed) frame %d", L.failures == 0 and "PASS" or "FAIL", tag, L.failures,
              emu.framecount()))
    if client.saveram then pcall(client.saveram) end
    client.exit()
    error("pol-live-finished", 0)
end
function L.die(why) L.check(why, false) L.finish("aborted") end

-- WRAM through the flat CGB domain: bank 0 = addr-0xC000, bank n = n*0x1000 + addr-0xD000
function L.woff(name, plus)
    local row = assert(L.SYM[name], "unknown symbol " .. name)
    local addr = row[2] + (plus or 0)
    if addr < 0xD000 then return addr - 0xC000 end
    return (row[1] == 0 and 1 or row[1]) * 0x1000 + addr - 0xD000
end
function L.rw(name, plus) return memory.read_u8(L.woff(name, plus), "WRAM") end
function L.ww(name, plus, v) memory.write_u8(L.woff(name, plus), v, "WRAM") end
function L.wbytes(name, plus, n)
    local t = {}
    for i = 0, n - 1 do t[#t + 1] = L.rw(name, (plus or 0) + i) end
    return t
end
function L.cart(name, plus) local r = L.SYM[name] return r[1] * 0x2000 + r[2] - 0xA000 + (plus or 0) end
function L.rc(name, plus) return memory.read_u8(L.cart(name, plus), "CartRAM") end
function L.bus(addr) return memory.read_u8(addr, "System Bus") end
function L.rombank() return L.bus(L.SYM.hROMBank[2]) end
function L.hex(t)
    local o = {}
    for i = 1, #t do o[i] = fmt("%02x", t[i]) end
    return table.concat(o)
end
function L.unhex(s)
    local t = {}
    for i = 1, #s, 2 do t[#t + 1] = tonumber(s:sub(i, i + 1), 16) end
    return t
end

-- exec probes: L.hit[name] = last frame, L.hits[name] = count (ROMX rows only when hROMBank == the row's bank)
L.hit, L.hits, L.ids = {}, {}, {}
function L.hook_at(name, bank, addr, fn)
    L.hits[name] = 0
    L.ids[name] = event.on_bus_exec(function()
        if addr >= 0x4000 and addr < 0x8000 and L.rombank() ~= bank then
            if fn then fn(false) end
            return
        end
        L.hit[name] = emu.framecount()
        L.hits[name] = L.hits[name] + 1
        if fn then fn(true) end
    end, addr, "pol_" .. name, "System Bus")
    return L.ids[name]
end
function L.hook(name, fn) local r = assert(L.SYM[name], "unknown symbol " .. name) return L.hook_at(name, r[1], r[2], fn) end
function L.unhook(name) if L.ids[name] then event.unregisterbyid(L.ids[name]) L.ids[name] = nil end end
function L.recent(name, n) local h = L.hit[name] return h ~= nil and emu.framecount() - h <= n end
function L.after(name, frame) local h = L.hit[name] return h ~= nil and h > frame end

-- input: one call per frame; `pulse` presses btn for 2 of every 16 frames (native menus swallow single pulses)
L.on_frame = nil
function L.frame(buttons)
    joypad.set(buttons or {})
    emu.frameadvance()
    if L.on_frame then L.on_frame() end
end
function L.pulse(btn)
    local on = btn and (emu.framecount() % 16) < 2
    L.frame(on and {[btn] = true} or nil)
end
function L.idle(n) for _ = 1, n do L.frame() end end
function L.map() return L.rw("wMapGroup"), L.rw("wMapNumber") end
function L.ow_idle() return L.recent("OWPlayerInput", 2) end

-- wait until the overworld takes player input on (group, number) for `quiet` frames, pressing A/B as needed
function L.to_overworld(group, number, quiet, bound, label)
    local f0, since = emu.framecount(), nil
    while true do
        local f = emu.framecount()
        if f - f0 > bound then return false end
        local g, n = L.map()
        if L.ow_idle() and (group == nil or (g == group and n == number)) then
            since = since or f
            if f - since >= quiet then
                L.log(fmt("[%s] overworld idle on map %d:%d at frame %d (%d frames)", label, g, n, f, f - f0))
                return true
            end
            L.frame()
        else
            since = nil
            if L.recent("SetInitialOptions.joypad_loop", 3) then L.pulse("B")
            else L.pulse("A") end
        end
    end
end
return L
