-- SYNTH fixture/link setup is disclosed by Python. Native buttons only here;
-- every guest write is through the real composed client's trade binder.
-- Strict receipt: qualified ordered commit/save/readback/publish/exit hooks,
-- full physical party + both name arrays and trainer ID/name after cold CONTINUE.
-- Shipped mode selects only the shipped binder; dev composition is explicit.
local L=dofile(assert(os.getenv("POL_DRIVER_ROOT")).."/tools/polished_live/pol_lib.lua")
local J=L.json
local role=os.getenv("SLINK_PLAYER")
local cold=os.getenv("POL_COLD")=="1"
-- run.lua/entry refuse by console diagnostic; preserve it before the generic graph assertion.
local original_console_log=console.log
console.log=function(...)
    local words={}
    for i=1,select('#',...) do words[#words+1]=tostring(select(i,...)) end
    local f=io.open(L.RUN.."/console.log","ab")
    if f then f:write(table.concat(words," ").."\n") f:close() end
    return original_console_log(...)
end
local function dump(name,value)
    local f=assert(io.open(L.RUN.."/"..name,"wb"))
    assert(f:write(J.encode(value))) f:close()
end
local function exists(name)
    local f=io.open(L.RUN.."/"..name,"rb")
    if f then f:close() return true end
    return false
end
local function milestone(kind, detail, witness)
    local row={kind=kind,frame=emu.framecount(),detail=detail}
    for key,value in pairs(witness or {}) do row[key]=value end
    local f=assert(io.open(L.RUN.."/milestones.jsonl","ab"))
    f:write(J.encode(row).."\n") f:close()
end
local ok,why=pcall(function()
console.log("[trade-duo] global print type="..type(print))
client.speedmode(300)
for _,name in ipairs({"OWPlayerInput","SetInitialOptions.joypad_loop","YesNoBox","NoYesBox"}) do L.hook(name) end
local sites=J.decode(L.slurp(L.RUN.."/trade-sites.json"))
local function matches(site)
    for i=1,#site.expected_hex/2 do
        local expected=tonumber(site.expected_hex:sub(i*2-1,i*2),16)
        if memory.read_u8(site.rom_offset+i-1,"ROM")~=expected then return false end
    end
    return true
end
for name,site in pairs(sites) do
    assert(matches(site),"native trade site bytes differ: "..name)
    L.hook_at(name,site.bank,site.pc,function(matched)
        if not matched then return end
        assert(emu.getregister("PC")==site.pc and matches(site),"native trade site changed: "..name)
        milestone("native",name,{qualified=true,pc=site.pc,bank=site.bank,expected_hex=site.expected_hex})
    end)
end
-- UI automation uses this separately, never as commit evidence.
L.hook("SlinkTradeResponderService")
-- Explicit dev option, through the existing Entry.build seam. Private root
-- contains the enabled build's provenance/profile, not changed shipped pins.
local original_dofile=dofile
if os.getenv("POL_TRADE_DEV")=="1" then dofile=function(path)
    local value=original_dofile(path)
    if path==L.ROOT.."/lua/gen2/entry.lua" then
        local build=value.build
        value.build=function(deps) deps.polished_trade_dev=true return build(deps) end
    end
    return value
end end
SLINK_HOST,SLINK_PORT,SLINK_PLAYER=os.getenv("SLINK_HOST"),tonumber(os.getenv("SLINK_PORT")),role
dofile(L.ROOT.."/lua/slink.lua")
dofile=original_dofile
local parts=assert(SLINK_GEN2_PARTS,"trade graph absent")
local binder
if os.getenv("POL_TRADE_DEV")=="1" then binder=parts.dev_polished_trade else binder=parts.polished_trade end
assert(SLINK_GEN2_CLIENT and binder,"selected trade graph failed to compose binder")
local C=assert(package.loaded.connector)
local hello,done,prompt=nil,nil,false
local queries,abort_reason=0,nil
local send,receive=C.send,C.receive
C.send=function(line,...)
    local msg=J.decode(line)
    if msg.event=="hello" then hello=msg end
    if msg.event=="trade_query" or msg.event=="trade_offer" or msg.event=="menu_result" or msg.event=="trade_done" then
        milestone("send",msg)
    end
    if msg.event=="trade_query" then
        queries=queries+1
        if queries>1 then abort_reason="native QUERY repeated without completing the first visit" end
    end
    if msg.event=="trade_done" then
        prompt=false
        done=msg
        L.log("TRADE_DONE "..line)
        dump("done.json",msg)
        if client.saveram then client.saveram() end
    end
    return send(line,...)
end
C.receive=function(...)
    local line=receive(...)
    if line then
        local msg=J.decode(line)
        for _,cmd in ipairs(msg.commands or {}) do
            if cmd.cmd=="apply_trade" or cmd.cmd=="trade_offer_ack" or cmd.cmd=="show_menu" then milestone("receive",cmd) end
            if cmd.refused then abort_reason="server refusal: "..tostring(cmd.refused) end
            if cmd.cmd=="show_menu" then prompt=true L.log("NATIVE_RESPONDER_PROMPT") end
        end
    end
    return line
end
    assert(L.to_overworld(20,1,60,10000,"cold Continue to retained receptionist fixture"),"CONTINUE did not reach POKECENTER_2F")
    local t=emu.framecount()
    while not hello and emu.framecount()-t<1800 do L.frame() end
    assert(hello,"hello absent")
    L.idle(180) -- let normal party ticks and any setup-link retrieval acknowledgment settle on both sides
    local physical={player_id_hex=L.hex(L.wbytes("wPlayerID",0,2)),
        player_name_hex=L.hex(L.wbytes("wPlayerName",0,8)),party_count=L.rw("wPartyCount"),party={}}
    for slot=0,physical.party_count-1 do
        physical.party[#physical.party+1]={slot=slot,
            record_hex=L.hex(L.wbytes("wPartyMons",slot*48,48)),
            ot_hex=L.hex(L.wbytes("wPartyMonOTs",slot*11,11)),
            nickname_hex=L.hex(L.wbytes("wPartyMonNicknames",slot*11,11))}
    end
    dump("ready.json",physical)
    L.log("READY "..role.." cold="..tostring(cold).." pos="..L.rw("wXCoord")..","..L.rw("wYCoord"))
    t=emu.framecount()
    while not exists("go") and not exists("stop") and emu.framecount()-t<90000 do L.frame() end
    if cold or exists("stop") then return end
    if role=="a" then
        assert(L.rw("wXCoord")==5 and L.rw("wYCoord")==3,"fixture is not in front of trade receptionist")
        for _=1,6 do L.frame({Up=true}) end
    end
    local noyes=L.hits.NoYesBox or 0 -- ignore any boot/CONTINUE prompt already passed
    t=emu.framecount()
    while not done and not abort_reason and not exists("stop") and emu.framecount()-t<18000 do
        if (L.hits.NoYesBox or 0)>noyes then
            noyes=L.hits.NoYesBox
            L.idle(24)
            for _=1,3 do L.frame({Down=true}) end
            L.idle(8)
            for _=1,3 do L.frame({A=true}) end
        -- A wire offer is not native UI: pressing A here can start the receptionist
        -- before the idle dispatcher picks up PROMPT. Wait for its native entry.
        elseif role=="a" or (prompt and (L.hits.SlinkTradeResponderService or 0)>0) then
            L.pulse("A")
        else L.frame() end
        if queries>0 and not done and binder:closed() and binder:disposition()~="COMPLETE" then
            abort_reason="native trade visit closed before completion"
        end
    end
    assert(done and not done.uncertain and done.new_key,abort_reason or ("trade did not complete: "..tostring(done and done.uncertain)))
    for _=1,180 do L.frame() end -- flush wire and native exit; no extra save or fabricated acknowledgment
    if client.saveram then client.saveram() end
    while not exists("stop") and emu.framecount()-t<90000 do L.frame() end
end)
if not ok then dump("failure.json",{reason=tostring(why)}) end
L.check("native trade / cold CONTINUE",ok,why)
L.finish("trade-duo-"..role)
