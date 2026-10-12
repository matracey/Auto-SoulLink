-- RC1 single-cartridge qualification recorder. No story/party/register staging.
-- Fixture/logical link are SYNTH; handle_command is TEST HOST; game actions are native.
local M = {}
function M.bank_valid(bank, addr, n, read, hrom)
    local last=addr+n-1
    if last<0x4000 or (addr>=0xc000 and last<0xd000) or (addr>=0xff80 and last<0xffff) then return bank==0 end
    if addr>=0x4000 and last<0x8000 then return bank==read(hrom,"System Bus") end
    if addr>=0xd000 and last<0xe000 then
        local mapped=read(0xff70,"System Bus")%8
        return bank==(mapped==0 and 1 or mapped)
    end
    return false
end
function M.run()
    local ROOT=assert(os.getenv("SLINK_ROOT"))
    package.path=ROOT.."/lua/?.lua;"..package.path
    local L=dofile(ROOT.."/tools/polished_live/pol_lib.lua")
    local config=L.json.decode(L.slurp(assert(os.getenv("POL_EXPLODE_CONFIG"))))
    local K=config.contract
    assert(K.schema=="polished-explode-live-v1")
    local trace,write_log,ids={}, {}, {}
    local parts,c,target,queued,recorded,finished,command_row
    local errors,cap,last_write=0,2048,0
    local copy_seen=false
    local function add(kind,fields)
        assert(#trace<cap,"trace capacity exhausted")
        local e={kind=kind,ord=#trace+1,frame=emu.framecount()}
        for k,v in pairs(fields or {}) do e[k]=v end
        trace[#trace+1]=e
        return e
    end
    local function read(label,delta)
        local row=assert(K.symbols[label],label)
        assert(M.bank_valid(row[1],row[2]+(delta or 0),1,memory.read_u8,K.symbols.hROMBank[2]),"unmapped "..label)
        local value=memory.read_u8(row[2]+(delta or 0),"System Bus")
        assert(type(value)=="number" and value%1==0 and value>=0 and value<=255,"unreadable "..label)
        return value
    end
    local function hp(label,delta) return read(label,delta)*256+read(label,(delta or 0)+1) end
    local function snapshot()
        local out={}
        for domain,size in pairs(K.snapshot_sizes) do
            local bytes={}
            if domain~="HRAM" then assert(memory.getmemorydomainsize(domain)==size,"snapshot domain size "..domain) end
            for i=0,size-1 do bytes[#bytes+1]=memory.read_u8(domain=="HRAM" and 0xff80+i or i,domain=="HRAM" and "System Bus" or domain) end
            out[domain]=L.hex(bytes)
        end
        return out
    end
    local function state()
        return {pc=emu.getregister("PC"),bank=read("hROMBank"),mode=read("wBattleMode"),
            active=read("wCurBattleMon"),slot=read("wCurBattleMon"),action=read("wBattlePlayerAction"),turn=read("hBattleTurn"),
            battle_hp=hp("wBattleMonHP"),party_hp=target and hp("wPartyMons",target.slot*48+34) or -1,
            fainted=(read("wPlayerSubStatus2")&4)~=0,move=read("wCurPlayerMove"),
            map_status=read("wMapStatus"),script=read("wScriptRunning"),link=read("wLinkMode")}
    end
    local function mapped()
        for _,label in ipairs({'wBattleMode','wCurBattleMon','wBattlePlayerAction','wBattleMonHP','wPartyMons','wPlayerSubStatus2','wCurPlayerMove','wMapStatus','wScriptRunning','wLinkMode'}) do
            local r=K.symbols[label]
            if not M.bank_valid(r[1],r[2],1,memory.read_u8,K.symbols.hROMBank[2]) then return false end
        end
        return true
    end
    -- Identity is decoded from the requested party record, never from the active
    -- battler's hooks. The status/padding/HP image is an actual synchronous read.
    local function bench_image()
        local g=K.geometry
        local delta=target.slot*48+g.status
        local n=g.hp+2-g.status
        local bytes={}
        for i=0,n-1 do bytes[#bytes+1]=read("wPartyMons",delta+i) end
        local mon=assert(parts.reads.read_party().mons[target.slot+1],"bench record absent")
        local h=g.hp-g.status+1
        return {slot=target.slot,key=mon.key,addr=K.symbols.wPartyMons[2]+delta,
            bank=K.symbols.wPartyMons[1],domain="System Bus",n=n,bytes=L.json.array(bytes),
            hp=bytes[h]*256+bytes[h+1],status_byte=bytes[1],frame=emu.framecount()}
    end
    -- Same proxy boundary as live.lua's write_audit: tap the real API call, not
    -- the requested client plan, and read each touched byte on both sides.
    local raw_memory=memory
    local function byte_image(addr,domain)
        local ok,v=pcall(raw_memory.read_u8,addr,domain)
        if not ok then return L.json.null,tostring(v) end
        if type(v)~="number" or v%1~=0 or v<0 or v>255 then
            return L.json.null,"read_u8 returned a non-byte"
        end
        return string.format("%02x",v),L.json.null
    end
    local function physical(addr,domain)
        if domain=="WRAM" then return addr,math.floor(addr/0x1000) end
        if domain=="System Bus" and addr>=0xc000 and addr<0xd000 then return addr-0xc000,0 end
        if domain=="System Bus" and addr>=0xd000 and addr<0xe000 then
            local b=raw_memory.read_u8(0xff70,"System Bus")%8
            b=b==0 and 1 or b
            return b*0x1000+addr-0xd000,b
        end
    end
    memory=setmetatable({}, {__index=function(_,api)
        local fn=raw_memory[api]
        if type(api)~="string" or not api:match("^write")
            or (type(fn)~="function" and type(fn)~="userdata") then return fn end
        return function(...)
            local addr,value,domain=...
            if domain==nil and raw_memory.getcurrentmemorydomain then domain=raw_memory.getcurrentmemorydomain() end
            local at,bank=physical(addr,domain)
            local row=K.symbols.wPartyMons
            local base=row[2]<0xd000 and row[2]-0xc000 or row[1]*0x1000+row[2]-0xd000
            local slot=at and at>=base and at<base+6*48 and math.floor((at-base)/48) or nil
            local mon=slot and parts and parts.reads.read_party().mons[slot+1]
            local op={sequence=#write_log+1,api=api,addr=addr,value=value,domain=domain or L.json.null,
                bank=bank or L.json.null,frame=emu.framecount(),n=api=="write_u8" and 1 or L.json.null,
                party_slot=slot or L.json.null,party_key=mon and mon.key or L.json.null}
            if api=="write_u8" then op.pre_hex,op.pre_read_error=byte_image(addr,domain)
            else op.pre_hex,op.pre_read_error=L.json.null,"unsupported image API: "..api end
            write_log[#write_log+1]=op
            local result=table.pack(pcall(fn,...))
            if api=="write_u8" then op.post_hex,op.post_read_error=byte_image(addr,domain)
            else op.post_hex,op.post_read_error=L.json.null,"unsupported image API: "..api end
            op.frame_after=emu.framecount()
            op.status,op.error=result[1] and "returned" or "raised",result[1] and L.json.null or tostring(result[2])
            if not result[1] then error(result[2],0) end
            return table.unpack(result,2,result.n)
        end
    end})
    local function scoped(context,fn,self,...)
        if not queued or recorded then return fn(self,...) end
        if not mapped() then return fn(self,...) end
        local s=state()
        if context=='battle' and (s.pc~=K.sites.hold.addr or s.bank~=K.sites.hold.bank or s.mode~=1) then return fn(self,...) end
        if context=='overworld' and s.mode~=0 then return fn(self,...) end
        local scope=add('scope_begin',{context=context,key=target.key})
        local before=snapshot();local start=#write_log
        local bench_before=config.case=="bench-faint" and context=="overworld" and bench_image() or nil
        local out=table.pack(fn(self,...));local after=snapshot()
        s.scope_ord=scope.ord
        local writes={};for i=start+1,#write_log do writes[#writes+1]=write_log[i] end
        if #writes>0 then
            s.context,s.key,s.before,s.after,s.writes=context,target.key,before,after,writes
            if bench_before then s.bench_before,s.bench_after=bench_before,bench_image() end
            if c.faint_settle then
                for _,ob in ipairs(c.faint_settle.owed) do
                    local a=ob.attempts[#ob.attempts]
                    if a and ob.key==target.key then s.attempt={identity=a.identity,epoch=a.epoch,visit=a.visit,seq=a.seq,generation=ob.gen} end
                end
            end
            add("operation",s);recorded=true;last_write=#write_log
        elseif context=="battle" and config.case=="bench-faint" then
            local changed=false
            for domain in pairs(before) do if before[domain]~=after[domain] then changed=true end end
            s.key,s.writes,s.changed,s.before,s.after=target.key,writes,changed,before,after
            add("bench_hold",s)
        end
        return table.unpack(out,1,out.n)
    end
    local C=require("connector")
    C.init(assert(os.getenv("SLINK_HOST")),assert(tonumber(os.getenv("SLINK_PORT"))))
    local net={connected=C.connected,pump=C.pump,receive=C.receive,send=function(line)
        local yes=C.send(line)
        -- connector.send queues and returns nil; the client's send() treats that as accepted.
        add("wire",{message=L.json.decode(line),sent=C.connected()==true and yes~=false,transport="real connector queue"})
        return yes
    end}
    local hud={sanitize=function(s) return s end,nuzlocke_start=function() end,show=function(text)
        add("hud",{text=text,key=target and target.key or "",dead=target and c.dead_keys[target.key]==true or false,
                    fs_seq=c and c.faint_settle and c.faint_settle.seq or 0})
    end}
    local io_={cart_ram_linear=true,read_u8=function(a,d) return memory.read_u8(a,d or "System Bus") end,
        read_range=function(a,n,d) local b={} for i=0,n-1 do b[#b+1]=memory.read_u8(a+i,d or "System Bus") end return b end,
        write_u8=function(a,v,d) return memory.write_u8(a,v,d or "System Bus") end,
        bank_valid=function(b,a,n) return M.bank_valid(b,a,n,memory.read_u8,K.symbols.hROMBank[2]) end,
        stack_valid=function(sp,n) return sp>=0xc000 and sp+n<0xd000 end,
        -- BizHawk API members are userdata, not Lua functions; the client asserts type()=="function".
        domain_size=function(d) return memory.getmemorydomainsize(d) end,
        domains=function() return memory.getmemorydomainlist() end,
        register=function(r) return emu.getregister(r) end,framecount=function() return emu.framecount() end,
        on_bus_exec=function(fn,a,name,d) return event.on_bus_exec(fn,a,name,d or "System Bus") end,
        unregister=function(id) return event.unregisterbyid(id) end}
    add("begin",{provenance=config.provenance,disclosure=config.disclosure,case=config.case})
    local function finish(ok,why)
        if finished then return end
        finished=true
        if not ok then errors=errors+1;add("driver_error",{reason=tostring(why)}) end
        if c then c:stop() end
        for _,id in ipairs(ids) do event.unregisterbyid(id) end
        local window
        if config.case=="bench-faint" and command_row then
            window={from_ord=command_row.ord,to_ord=#trace,from_frame=command_row.frame,to_frame=emu.framecount(),
                file="bench_wire_window.jsonl"}
            local wf=assert(io.open(L.RUN.."/"..window.file,"wb"))
            for _,e in ipairs(trace) do
                if e.kind=="wire" and e.ord>=window.from_ord and e.ord<=window.to_ord then
                    wf:write(assert(L.json.encode(e)),"\n")
                end
            end
            wf:close()
        end
        add("final",{completed=ok and errors==0,driver_errors=errors,post_operation_writes=recorded and #write_log-last_write or -1,
            write_ops=L.json.array(write_log),bench_wire_window=window})
        local file=assert(io.open(L.RUN.."/trace.json","wb"));file:write(assert(L.json.encode(trace)));file:close()
        L.check("explode-live recording complete",ok,why)
        L.finish("explode-live "..config.case)
    end
    local ok,why=pcall(function()
        local Entry=dofile(ROOT.."/lua/gen2/entry.lua")
        parts,why=Entry.build({root=ROOT,title="polished",io=io_,net=net,hud=hud,player="a",
            rom_size=memory.getmemorydomainsize("ROM"),read_rom_u8=function(a) return memory.read_u8(a,"ROM") end,
            polished_active_faint=true,polished_faint_observer=true,
            log=function(text) L.log(text) end})
        assert(parts,why);assert(parts.runtime_rom_sha1==config.provenance.rom_sha1,"composed ROM differs")
        c=parts.client
        local hold,deferred,event_=c.at_battle_hold,c.run_deferred,c.on_event
        c.at_battle_hold=function(self,...) return scoped("battle",hold,self,...) end
        c.run_deferred=function(self,...) return scoped("overworld",deferred,self,...) end
        c.on_event=function(self,e)
            if queued and e.site_id=="battle_faint" and e.capture then
                add("consumer",{phase=e.phase,capture=e.capture,battle=e.battle,batch_generation=e.batch_generation})
            end
            return event_(self,e)
        end
        for name,site in pairs(K.sites) do
            if name~="hold" then
                local samples=0
                ids[#ids+1]=event.on_bus_exec(function()
                    if finished then return end
                    local good,err=pcall(function()
                        local bank,pc=read("hROMBank"),emu.getregister("PC")
                        if bank~=site.bank then
                            if samples<16 then samples=samples+1;add("wrong_bank",{site=name,bank=bank,pc=pc}) end
                            return
                        end
                        assert(pc==site.addr,"native hook PC drift")
                        if queued then
                            if not mapped() then return end
                            local s=state();s.site=name
                            local party=assert(parts.reads.read_party());local mon=assert(party.mons[target.slot+1])
                            s.key=mon.key
                            local bytes={};for i=0,#site.bytes/2-1 do bytes[#bytes+1]=memory.read_u8(site.rom_offset+i,"ROM") end
                            s.bytes=L.hex(bytes);add("native",s)
                            if name=="copy_return" then copy_seen=true end
                        end
                    end)
                    if not good then errors=errors+1;add("driver_error",{reason=tostring(err)}) end
                end,site.addr,"explode_live_"..name,"System Bus")
            end
        end
        c:start()
        ids[#ids+1]=event.onframeend(function()
            if finished then return end
            local good,err=pcall(function()
                c:frame_end()
                if not queued and mapped() and read("wBattleMode")==1 and c.writes_enabled and c.faint_settle.last_identity then
                    local party=assert(parts.reads.read_party())
                    local slot=config.case=="bench-faint" and config.target.slot or read("wCurBattleMon")
                    local mon=assert(party.mons[slot+1]);assert(mon.key==config.target.key and mon.hp>0,"target differs from linked SYNTH fixture")
                    target={key=mon.key,slot=slot};queued=true
                    local command=config.case=="explode" and "force_explode" or "force_faint"
                    command_row=add("command",{cmd=command,key=mon.key,slot=slot,mode=1})
                    c:handle_command({cmd=command,key=mon.key,nickname="QUAL_TARGET"})
                end
                assert(emu.framecount()<config.frame_cap,"frame cap")
            end)
            if not good then errors=errors+1;add("driver_error",{reason=tostring(err)}) end
        end,"explode_live_frame")
        client.speedmode(300)
        for n,step in ipairs(config.steps) do
            add("route",{step=n,frames=step.frames,buttons=step.buttons})
            local buttons={};for _,b in ipairs(step.buttons) do buttons[b]=true end
            for _=1,step.frames do L.frame(buttons) end
        end
        local after=0
        local walked=0
        while after<3600 and not (target and c.dead_keys[target.key] and recorded) do
            if not queued and mapped() and read("wBattleMode")==0 then
                -- Random encounters are not frame-deterministic across runs: the fixed route's 4 grass holds may
                -- end without a wild battle. Keep the route's own Left/Right grass walk (row 12) until one starts.
                L.frame({[math.floor(walked/56)%2==0 and "Left" or "Right"]=true});walked=walked+1
            else L.pulse("A") end
            after=after+1
        end
        if walked>0 then add("route_tail",{frames=walked,walk=true}) end
        assert(target and recorded and c.dead_keys[target.key],"command/write/settlement incomplete")
        -- Settlement can precede the native ResolveFaints copyback (an Explosion route ends mid-battle);
        -- keep the same native A pulses going until copy_return fires or the battle ends, bounded.
        if config.case~="bench-faint" then
            local extra=0
            while extra<2400 and not copy_seen and (not mapped() or read("wBattleMode")~=0) do L.pulse("A");extra=extra+1 end
            add("route_tail",{frames=extra,copy_seen=copy_seen,mode=mapped() and read("wBattleMode") or -1})
        end
        pcall(function() client.screenshot(L.RUN.."/final.png") end)
        L.idle(90) -- retain duplicate/quiet re-zero evidence; qualification demands one KO
        assert(errors==0,"callback failure")
    end)
    finish(ok,why)
end
if rawget(_G,"POL_EXPLODE_UNIT_TEST") then return M end
M.run()
