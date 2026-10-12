-- Durable, bounded gameplay reports and ordinary PC command execution fences.
local O = {}
local MAX_BYTES, MAX_REPORTS, MAX_KEYS = 16 * 1024 * 1024, 4096, 1024
local EVENTS = {capture=true, faint=true, no_catch=true, whiteout=true, area_enter=true,
    party_to_box=true, box_to_party=true, key_change=true, release=true, stats_cache=true,
    sync_retrieve_done=true, sync_retrieve_failed=true, box_mon_failed=true,
    memorialize_done=true, memorialize_failed=true}
O.events = EVENTS
local function digest(s)
    local h = 2166136261
    for i=1,#s do h = ((h ~ s:byte(i)) * 16777619) & 0xFFFFFFFF end
    return string.format("%08x",h)
end
local function equal(a,b)
    if not a or not b then return false end
    for _, k in ipairs({"run_id","player","ot_id","rom_sha1"}) do
        if a[k] ~= b[k] then return false end
    end
    return true
end
local function valid_binding(b)
    return type(b)=="table" and type(b.run_id)=="string" and #b.run_id>0
        and (b.player=="a" or b.player=="b") and type(b.ot_id)=="string"
        and b.ot_id:match("^%x%x%x%x%x%x%x%x$") and type(b.rom_sha1)=="string"
        and #b.rom_sha1==40 and not b.rom_sha1:find("[^0-9a-f]")
end
local function initial()
    return {schema=1, binding=false, stream="", counter=0, reports={}, commands={}, acquisitions={}}
end
local function encode(json,s)
    s.reports=json.array(s.reports)
    s.acquisitions=json.array(s.acquisitions)
    local body = assert(json.encode(s))
    return "SLINK-REPORT-1\n" .. digest(body) .. "\n" .. body
end
local function decode(json,bytes)
    assert(type(bytes)=="string" and #bytes<=MAX_BYTES,"missing or oversized report journal")
    local checksum,body = bytes:match("^SLINK%-REPORT%-1\n([0-9a-f]+)\n(.*)$")
    assert(checksum and #checksum==8 and digest(body)==checksum,"corrupt report journal")
    local s = json.decode(body,{bytes=MAX_BYTES,items=1000000})
    assert(type(s)=="table" and s.schema==1 and type(s.counter)=="number"
        and s.counter>=0 and s.counter==math.floor(s.counter) and type(s.stream)=="string"
        and (s.binding==false or valid_binding(s.binding)) and type(s.reports)=="table"
        and type(s.commands)=="table" and type(s.acquisitions)=="table" and #s.acquisitions<=64,
        "invalid report journal state")
    local previous = nil
    assert(#s.reports<=MAX_REPORTS,"report journal capacity exceeded")
    for _,r in ipairs(s.reports) do
        assert(type(r)=="table" and EVENTS[r.event] and type(r.fields)=="table"
            and type(r.number)=="number" and r.number>=1 and r.number<=s.counter
            and (not previous or r.number==previous+1),"invalid report journal ordering")
        previous=r.number
    end
    local n=0
    for key,c in pairs(s.commands) do
        n=n+1
        assert(type(key)=="string" and type(c)=="table" and type(c.intent_id)=="string"
            and type(c.revision)=="number" and (c.phase=="prepared" or c.phase=="done" or c.phase=="cancelled")
            and (c.cmd=="box_mon" or c.cmd=="party_mon"),"invalid command fence")
    end
    assert(n<=MAX_KEYS,"command fence capacity exceeded")
    return s
end
function O.file_store(d)
    local fs,json,path = assert(d.fs),assert(d.json),assert(d.path)
    local function seal(bytes) return "SLINK-REPORT-GUARD-1\n"..digest(bytes) end
    local function access(fn)
        local guard,fresh,why = fs.lock(path..".guard")
        assert(guard,"report journal lock unavailable: "..tostring(why))
        local ok,result=pcall(function()
            local bytes=fs.read_file(path..".json")
            if fresh then
                assert(bytes==nil,"report guard missing beside existing journal")
                bytes=encode(json,initial())
                assert(fs.create_file(path..".json",bytes)==true,"report genesis not durable")
                assert(fs.write_handle(guard,seal(bytes))==true,"report guard not durable")
            end
            assert(fs.read_handle(guard)==seal(bytes),"missing, torn or rolled-back report journal")
            local s=decode(json,bytes)
            if not fn then return s end
            local value=fn(s)
            local output=encode(json,s)
            assert(#output<=MAX_BYTES,"report journal capacity exceeded")
            if output~=bytes then
                assert(fs.replace_file(path..".json",output)==true,"report replacement not durable")
                assert(fs.read_file(path..".json")==output,"report readback mismatch")
                assert(fs.write_handle(guard,seal(output))==true,"report seal not durable")
                assert(fs.read_handle(guard)==seal(output),"report seal readback mismatch")
            end
            return value
        end)
        local closed,value=pcall(fs.close,guard)
        assert(closed and value==true,"report guard close failed")
        if not ok then error(result) end
        return result
    end
    return {read=function() return access() end, update=access}
end
function O.new(p)
    local self={store=assert(p.store), json=assert(p.json), player=p.player, rom_sha1=p.rom_sha1, events=EVENTS,
        new_id=assert(p.new_id), binding=nil, accepted=false, failure=nil}
    local function access(fn)
        local ok,value=pcall(fn)
        if not ok then self.failure=tostring(value); error("gameplay journal: "..self.failure) end
        return value
    end
    function self:ready() return self.binding~=nil and self.failure==nil end
    function self:disconnect() self.accepted=false end
    function self:load(ot)
        if self.binding then
            assert(self.binding.ot_id==ot,"gameplay journal save changed")
            return
        end
        access(function()
            local s=self.store.read()
            if s.binding~=false then
                assert(s.binding.ot_id==ot and s.binding.player==self.player
                    and s.binding.rom_sha1==self.rom_sha1,"gameplay journal save/player changed")
                self.binding=s.binding
            end
        end)
    end
    function self:bind(b,ot)
        assert(valid_binding(b) and b.player==self.player and b.ot_id==ot
            and b.rom_sha1==self.rom_sha1,"incompatible report binding")
        access(function() return self.store.update(function(s)
            if s.binding~=false and not equal(s.binding,b) then
                assert(#s.reports==0 and #s.acquisitions==0,"report journal belongs to another run/save")
                for _,c in pairs(s.commands) do
                    assert(c.phase=="done" or c.phase=="cancelled","unsettled PC command belongs to another run/save")
                end
            end
            if not equal(s.binding,b) then
                s.binding=b; s.stream=self.new_id(); s.counter=0; s.commands={}
                assert(type(s.stream)=="string" and s.stream:match("^[0-9a-f]+$") and #s.stream==32,
                       "installation report identity unavailable")
            end
        end) end)
        self.binding,self.accepted=b,true
    end
    function self:append(event,fields)
        assert(self:ready(),"gameplay report binding unavailable")
        assert(EVENTS[event],"transient event in report journal")
        local copy=self.json.decode(self.json.encode(fields or {}))
        local acquisition_id=copy._acquisition_id
        copy._acquisition_id=nil
        access(function() return self.store.update(function(s)
            assert(equal(s.binding,self.binding),"report binding changed")
            assert(#s.reports<MAX_REPORTS,"gameplay report capacity exceeded")
            s.counter=s.counter+1
            s.reports[#s.reports+1]={event=event,fields=copy,number=s.counter}
            if acquisition_id then
                for i,r in ipairs(s.acquisitions) do
                    if r.id==acquisition_id then table.remove(s.acquisitions,i); break end
                end
            end
        end) end)
        return true
    end
    function self:head()
        if not self.accepted or not self:ready() then return nil end
        return access(function()
            local s=self.store.read()
            assert(equal(s.binding,self.binding),"report binding changed")
            local r=s.reports[1]
            if not r then return nil end
            local fields=self.json.decode(self.json.encode(r.fields))
            fields.report={stream=s.stream,number=r.number,binding=s.binding}
            return {event=r.event,fields=fields}
        end)
    end
    function self:acquisitions()
        return access(function() return self.store.read().acquisitions end)
    end
    function self:pending_count()
        return access(function() return #self.store.read().reports end)
    end
    function self:acquisition_id() return self.new_id() end
    function self:save_acquisitions(rows)
        assert(self:ready(),"acquisition report binding unavailable")
        access(function() return self.store.update(function(s)
            assert(equal(s.binding,self.binding) and #rows<=64,"acquisition journal binding/capacity")
            s.acquisitions=rows
        end) end)
    end
    function self:ack(cmd)
        access(function() return self.store.update(function(s)
            local r=s.reports[1]
            if r and cmd.stream==s.stream and cmd.number==r.number then table.remove(s.reports,1) end
        end) end)
    end
    function self:command(cmd,stats)
        assert(self.accepted and self:ready(),"gameplay command binding unavailable")
        return access(function() return self.store.update(function(s)
            local c=s.commands[cmd.key]
            for k,row in pairs(s.commands) do
                if row.intent_id==cmd.intent_id and k~=cmd.key then
                    s.commands[k]=nil; s.commands[cmd.key]=row; c=row; break
                end
            end
            if c and cmd.intent_revision<=c.revision then
                if cmd.intent_id~=c.intent_id then return "stale" end
                return c.phase,c.event,c.fields
            end
            local n=0; for _ in pairs(s.commands) do n=n+1 end
            assert(c or n<MAX_KEYS,"gameplay command fence capacity exceeded")
            s.commands[cmd.key]={intent_id=cmd.intent_id,revision=cmd.intent_revision,cmd=cmd.cmd,
                phase="prepared",stats=stats}
            return "new"
        end) end)
    end
    function self:command_stats(cmd)
        return access(function()
            local s=self.store.read()
            local c=s.commands[cmd.key]
            return c and c.intent_id==cmd.intent_id and c.stats or nil
        end)
    end
    function self:cancel(cmd)
        assert(self.accepted and self:ready(),"gameplay cancellation binding unavailable")
        access(function() return self.store.update(function(s)
            local old=s.commands[cmd.key]
            if old and old.revision>cmd.intent_revision then return end
            s.commands[cmd.key]={intent_id="cancel:"..tostring(cmd.intent_revision),
                revision=cmd.intent_revision,cmd="box_mon",phase="cancelled"}
        end) end)
    end
    function self:complete(cmd,event,fields)
        fields.intent_id,fields.intent_revision=cmd.intent_id,cmd.intent_revision
        access(function() return self.store.update(function(s)
            local c=assert(s.commands[cmd.key],"missing command fence")
            assert(c.intent_id==cmd.intent_id,"superseded command completion")
            assert(#s.reports<MAX_REPORTS,"gameplay report capacity exceeded")
            c.phase,c.event,c.fields="done",event,fields
            s.counter=s.counter+1
            s.reports[#s.reports+1]={event=event,fields=fields,number=s.counter}
        end) end)
        return true
    end
    return self
end
return O
