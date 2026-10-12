-- SYNTH fixture + scripted TCP partner; native route and REAL composed client.
-- Only the production client writes. This recorder observes, never stages RAM.
local L = dofile(assert(os.getenv("SLINK_ROOT")).."/tools/polished_live/pol_lib.lua")
local J = L.json
local config = J.decode(L.slurp(assert(os.getenv("POL_SWAP_CONFIG"))))
local trace, overflow, writes, calls = {}, 0, 0, 0
local pos_rows = {}
local active, post_seen, continued = false, false, false
local function emit(kind, fields)
    if #trace >= 5000 then overflow=overflow+1 return end
    local row={kind=kind,ord=#trace+1,frame=emu.framecount()}
    for k,v in pairs(fields or {}) do row[k]=v end
    trace[#trace+1]=row
end
local function context()
    return {bank=L.rombank(),pc=emu.getregister("PC"),side=L.bus(L.SYM.hBattleTurn[2]),
            mode=L.rw("wBattleMode"),trainer=L.rw("wOtherTrainerClass")*256+L.rw("wOtherTrainerID")}
end
local function wram()
    assert(memory.getmemorydomainsize("WRAM")==32768,"unexpected WRAM domain")
    local b={}
    for i=0,32767 do b[#b+1]=memory.read_u8(i,"WRAM") end
    return L.hex(b)
end
local function image()
    local out={}
    for _,s in ipairs(config.spans) do
        local bytes={}
        for i=0,#s.bytes-1 do bytes[#bytes+1]=memory.read_u8(s.addr+i-0xC000,"WRAM") end
        out[#out+1]=bytes
    end
    return out
end
-- Audit every Lua mutation API, including unsupported APIs; CPU writes are native.
local raw_emu = emu
local G = dofile(L.ROOT .. "/tools/polished_live/recording_guard.lua")
memory = G.observe_memory(memory, function(name, addr, value, domain)
    writes=writes+1
    local c=context()
    c.api,c.addr,c.value,c.domain,c.active=name,addr,value,domain,active
    if name~="write_u8" then c.value=tostring(value) end
    emit("write",c)
end)
emu=setmetatable({}, {__index=function(_,name)
    if name=="setregister" then return function() emit("error",{error="CPU mutation attempted"}) error("CPU mutation denied") end end
    return raw_emu[name]
end})

local ok,err=pcall(function()
    emit("start",{rom_sha1=gameinfo.getromhash():lower(),setup="SYNTH",fixture_sha256=config.fixture_sha256,
                  route_sha256=config.route_sha256,disclosure_sha256=config.disclosure_sha256})
    client.speedmode(300)
    SLINK_HOST,SLINK_PORT,SLINK_PLAYER=os.getenv("SLINK_HOST"),tonumber(os.getenv("SLINK_PORT")),"a"
    dofile(L.ROOT.."/lua/slink.lua")
    local P=assert(SLINK_GEN2_PARTS,"client composition missing")
    assert(P.pack=="polished_crystal" and P.qualification=="DEV_OVERLAY_SHA1","wrong composition")
    local C=assert(package.loaded.connector)
    local send,receive=C.send,C.receive
    C.send=function(line,...)
        local msg=J.decode(line)
        if msg.event=="rival_team_replaced" then emit("reply",{msg=msg}) end
        if msg.event=="trainer_battle_start" then emit("battle_start",{msg=msg}) end
        return send(line,...)
    end
    C.receive=function(...)
        local line=receive(...)
        if line then
            local msg=J.decode(line)
            for _,cmd in ipairs(msg.commands or {}) do
                if cmd.cmd=="replace_rival_team" then emit("command",{msg=cmd}) end
            end
        end
        return line
    end
    -- Observe the exact facade already captured by the real client, preserving return values/errors.
    local writer=assert(P.battle.rival.writes)
    local original=writer.write_enemy_party
    writer.write_enemy_party=function(self,mons,ctx)
        calls=calls+1
        assert(calls<=16,"writer call overflow")
        local before=context()
        before.declared_spans=P.battle.rival.ranges(#mons)
        before.wram=wram()
        emit("writer_begin",before)
        active=true
        local result=table.pack(pcall(original,self,mons,ctx))
        active=false
        emit("writer_end",{wram=wram(),ok=result[1]})
        if not result[1] then error(result[2],0) end
        return table.unpack(result,2,result.n)
    end
    -- 480D is after species/moves/level/HP copy; do not replace the production 47DD callback.
    L.hook_at("swap_post",15,0x480D,function(matched)
        if not matched or calls==0 then return end
        local c=context()
        if c.side~=1 or c.mode~=2 or c.trainer~=0x1B03 then return end
        c.image=image()
        emit("post",c)
        post_seen=true
    end)
    L.hook("LoadBattleMenu",function(matched)
        if not matched or not post_seen or continued then return end
        local c=context()
        if c.mode~=2 or c.trainer~=0x1B03 then return end
        continued=true
        c.enemy={species=L.rw("wEnemyMonSpecies")+((L.rw("wEnemyMonForm") & 0x20)~=0 and 256 or 0),
                 level=L.rw("wEnemyMonLevel"),moves=L.wbytes("wEnemyMonMoves",0,4),
                 hp=L.rw("wEnemyMonHP")*256+L.rw("wEnemyMonHP",1)}
        emit("continued",c)
    end)
    local elapsed=0
    -- Diagnostic only (never part of the oracle trace): where the native route actually is.
    local pos=pos_rows
    local function sample()
        if elapsed%30==0 and #pos<1500 then
            pos[#pos+1]={elapsed=elapsed,frame=emu.framecount(),group=L.rw("wMapGroup"),map=L.rw("wMapNumber"),
                         x=L.rw("wXCoord"),y=L.rw("wYCoord"),mode=L.rw("wBattleMode")}
        end
    end
    local function frame(button)
        L.frame(button and {[button]=true} or {}) elapsed=elapsed+1 sample()
    end
    if config.feedback then
        -- Feedback navigation INSIDE the client session. A fixed-frame replay of a route calibrated by the
        -- probe (no SLink client) desyncs on the first wild encounter (random encounters differ once the
        -- companion/client is live). Same source-derived tile route as rival_gate_probe.lua calibrate();
        -- every target is verified from WRAM, battles are pulsed through, nothing is written.
        local function bounded(predicate,action,label)
            local start=elapsed
            while not predicate() and not continued do
                if elapsed-start>=1800 then error("1800-frame stall: "..label) end
                action()
            end
        end
        local function pulse() frame(elapsed%16<2 and "A" or nil) end
        local function idle(n) for _=1,n do frame() end end
        local function wild()
            bounded(function() return L.rw("wBattleMode")==0 and L.ow_idle() end,pulse,"wild ended")
            idle(30)
        end
        L.hook("OWPlayerInput")
        bounded(function() return L.ow_idle() and L.rw("wMapGroup")==24 and L.rw("wMapNumber")==3 end,pulse,"boot Route29")
        idle(30)
        assert(L.rw("wXCoord")==48 and L.rw("wYCoord")==12,"unexpected fixture coordinates")
        local route={{"Left",4},{"Down",2},{"Left",6},{"Down",2},{"Left",7},{"Up",6},
            {"Right",5},{"Up",3},{"Left",13},{"Up",1},{"Left",2},{"Up",2},{"Left",5},
            {"Down",2},{"Left",5},{"Down",4},{"Left",7},{"Up",3},{"Left",11}}
        local dirs={Left={-1,0},Right={1,0},Up={0,-1},Down={0,1}}
        for seg,row in ipairs(route) do
            for tile=1,row[2] do
                if L.rw("wBattleMode")==1 then wild() end
                local x,y=L.rw("wXCoord"),L.rw("wYCoord")
                local tx,ty=x+dirs[row[1]][1],y+dirs[row[1]][2]
                local group,map=L.rw("wMapGroup"),L.rw("wMapNumber")
                if tx==-1 then group,map,tx=26,4,39 end
                bounded(function()
                    return L.rw("wMapGroup")==group and L.rw("wMapNumber")==map
                        and L.rw("wXCoord")==tx and L.rw("wYCoord")==ty
                end,function()
                    if L.rw("wBattleMode")==1 then wild() else frame(row[1]) end
                end,"segment "..seg.." tile "..tile)
                idle(24)
            end
        end
        -- Rival encounter: pulse A until the production writer and LoadBattleMenu hooks have fired.
        bounded(function() return continued end,pulse,"rival continued")
    else
        for _,step in ipairs(config.steps) do
            local buttons={}
            for _,b in ipairs(step.buttons) do buttons[b]=true end
            for _=1,step.frames do
                L.frame(buttons) elapsed=elapsed+1 sample()
                if continued then break end
            end
            if continued then break end
        end
    end
    -- One bounded attempt only; no alternate route, memory staging or retry.
    local idle=0
    while elapsed<config.frames and not continued and idle<1800 do
        L.frame() elapsed=elapsed+1 idle=idle+1
    end
    -- Flush the production frame-end reply and TCP send queue with neutral input.
    for _=1,120 do L.frame() end
end)
if not ok then emit("error",{error=tostring(err)}) end
pcall(function()
    local f=assert(io.open(L.RUN.."/positions.json","wb"))
    f:write(J.encode(pos_rows or {}))
    f:close()
end)
-- Final is retained even if the bounded row cap was reached; overflow is never PASS.
trace[#trace+1]={kind="final",ord=#trace+1,frame=emu.framecount(),completed=ok,writes=writes,overflow=overflow}
local dumped,dump_error=pcall(function()
    local text=J.encode(trace)
    local file=assert(io.open(L.RUN.."/trace.json","wb"))
    local success,why=file:write(text)
    file:close()
    assert(success,why)
end)
L.check("complete SYNTH rival swap recording",ok and dumped and overflow==0,err or dump_error)
L.finish("rival-swap recording")
