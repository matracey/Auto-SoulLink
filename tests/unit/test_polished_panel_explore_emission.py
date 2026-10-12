"""Execute the live probes: evidence must preserve real stores, not callback payloads."""
import json
from pathlib import Path

import pytest
from lupa.lua55 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]


def run_probe(driver, fault="none"):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().root = ROOT.as_posix()
    lua.globals().probe = driver
    lua.globals().fault = fault
    lua.execute(r'''
        local original_dofile = dofile
        local J = original_dofile(root .. "/lua/json_codec.lua")
        local cmap = original_dofile(root .. "/data/games/polished_crystal/charmap.lua")
        local frame, previous, phase, current_bank = 0, {}, "list", 1
        local current_vram_bank=0
        local bytes, handlers, files, checks = {}, {}, {}, {}
        local sym = {
            wSlinkMailbox={0,0xc60b}, wSlinkMailboxEnd={0,0xc650}, wSlinkPanelText={0,0xc62d},
            wTilemap={0,0xc100}, wAttrmap={1,0xd400}, wPhoneList={0,0xc020},
            wPokegearFlags={0,0xc010}, wCurCaller={0,0xc030}, wPokegearCard={0,0xc031},
            wJumptableIndex={0,0xc032}, wPokegearPhoneCursorPosition={0,0xc033},
            wPokegearPhoneScrollPosition={0,0xc034}, wPokegearPhoneSelectedPerson={0,0xc035},
            wMenuItemsList={0,0xc040}, wMenuCursorY={0,0xc042}, wSpriteAnim1={0,0xc700},
            wShadowOAM={0,0xc800}, hROMBank={0,0xff9d}, hBGMapAddress={0,0xffaa},
        }
        local function offset(name, plus)
            local s = assert(sym[name], name)
            return (s[2] < 0xd000 and s[2]-0xc000 or (s[1]==0 and 1 or s[1])*0x1000+s[2]-0xd000)+(plus or 0)
        end
        local function read(a,d)
            if d == "System Bus" then
                if a == 0xff70 then return current_bank end
                if a == 0xff4f then return current_vram_bank end
                if a == 0xff9d then return 3 end
                if a >= 0xc000 and a < 0xd000 then return bytes[a-0xc000] or 0 end
                if a >= 0xd000 and a < 0xe000 then return bytes[current_bank*0x1000+a-0xd000] or 0 end
            end
            return bytes[(d or "WRAM") .. ":" .. a] or (d == "WRAM" and bytes[a]) or 0
        end
        local function put(name,v,plus) bytes[offset(name,plus)]=v end
        local function native(a,v,d)
            if d == "WRAM" then bytes[a]=v else bytes[d .. ":" .. a]=v end
            local bus = d == "WRAM" and (a < 0x1000 and a+0xc000 or a%0x1000+0xd000) or a%0x2000+0x8000
            for _,h in pairs(handlers) do
                if h.kind == "write" and h.addr == bus then h.fn(bus,249) end
            end
        end
        memory = {read_u8=read, write_u8=function(a,v,d)
            if d == "WRAM" then bytes[a]=v else bytes[d .. ":" .. a]=v end
            return "stored"
        end, getmemorydomainsize=function(d) return d=="WRAM" and 0x8000 or 0x4000 end,
        getcurrentmemorydomain=function() return "WRAM" end}
        event = {onmemorywrite=function(fn,a,name,d)
            local id=name .. ":" .. a; handlers[id]={kind="write",fn=fn,addr=a}; return id
        end, unregisterbyid=function(id) handlers[id]=nil end,
        on_bus_exec=function(fn,a,name,d)
            handlers[name]={kind="exec",fn=fn,addr=a}; return name
        end}
        emu = {framecount=function() return frame end,
            getregister=function(name) return name=="PC" and 0x4567 or 0xff00 end}
        client = {speedmode=function() end,screenshot=function() end}
        gameinfo = {getromhash=function() return "synthetic-ROM" end}
        L = {ROOT=root,RUN="memory",SYM=sym,json=J,hits={},hit={},ids={},failures=0}
        function L.woff(name,plus) return offset(name,plus) end
        function L.rw(name,plus) return bytes[offset(name,plus)] or 0 end
        function L.ww(name,plus,v) memory.write_u8(offset(name,plus),v,"WRAM") end
        function L.wbytes(name,plus,n)
            local out={} for i=0,n-1 do out[#out+1]=L.rw(name,(plus or 0)+i) end return out
        end
        function L.hex(t) local out={} for i,v in ipairs(t) do out[i]=string.format("%02x",v) end return table.concat(out) end
        function L.bus(a) return read(a,"System Bus") end
        function L.rombank() return 3 end
        function L.log() end
        function L.check(what,ok,detail) checks[#checks+1]={what=what,ok=not not ok}; if not ok then L.failures=L.failures+1 end end
        function L.finish(tag) finished=tag; error("finished",0) end
        function L.die(why) error(why,0) end
        function L.hook(name,fn)
            if not sym[name] then sym[name]={3,0x4567} end
            L.hits[name]=0
            local id="exec:" .. name; L.ids[name]=id
            handlers[id]={kind="exec",name=name,fn=fn,addr=sym[name][2]}
            return id
        end
        function L.unhook(name) handlers[L.ids[name]]=nil; L.ids[name]=nil end
        local function hit(name)
            L.hits[name]=(L.hits[name] or 0)+1;L.hit[name]=frame
            local h=handlers[L.ids[name]]; if h and h.fn then h.fn(true) end
        end
        function L.after(name,at) return (L.hit[name] or -1)>at end
        function L.to_overworld() return true end
        function L.slurp(path)
            local f=assert(io.open(path,"rb"));local text=f:read("*a");f:close()
            if fault=="outside" and path:match("profile.json$") then
                local profile=assert(J.decode(text));profile.titles.polished.overlay.panel.lines=3
                return assert(J.encode(profile))
            end
            return text
        end
        local function render()
            if probe ~= "panel" or L.rw("wSlinkMailbox",9) ~= 2 then return end
            for row=0,1 do for c=0,19 do put("wTilemap",0x7f,(12+row)*20+c) end end
            for row=0,1 do for c=0,15 do put("wTilemap",L.rw("wSlinkPanelText",row*17+c),(12+row)*20+c) end end
        end
        local function redraw(card)
            for _,name in ipairs({"wTilemap","wAttrmap"}) do
                for _,o in ipairs({8,9,28,29}) do
                    local a=offset(name,o);local v=40+card
                    if fault=="idempotent" and name=="wTilemap" and o==8 then v=0 end
                    if fault=="wrong_bank" and name=="wAttrmap" then current_bank=2;a=a+0x1000 end
                    native(a,v,"WRAM")
                    if fault=="multiple" and name=="wTilemap" and o==8 then native(a,v+1,"WRAM") end
                    current_bank=1
                end
            end
            if fault=="vram" then
                for bank=0,1 do
                    current_vram_bank=bank
                    for _,base in ipairs({0x1800,0x1c00}) do
                        for _,o in ipairs({8,9,40,41}) do native(bank*0x2000+base+o,140+bank+card,"VRAM") end
                    end
                end
                current_vram_bank=0
            end
        end
        function L.frame(buttons)
            frame=frame+1;buttons=buttons or {}
            if buttons.Start then hit("StartMenu") end
            if buttons.A and not previous.A then
                if not L.hit.PokeGear then
                    hit("PokeGear"); if probe=="explore" then put("wPokegearCard",0);put("wJumptableIndex",1);redraw(0) end
                elseif probe=="panel" then
                    if phase=="list" then phase="submenu";hit("PokegearPhoneContactSubmenu")
                    elseif phase=="submenu" then phase="panel";hit("SlinkPhone_CallGate");hit("SlinkPanel");native(offset("wSlinkMailbox",9),1,"WRAM")
                    else native(offset("wSlinkMailbox",10),1,"WRAM");native(offset("wSlinkMailbox",9),1,"WRAM") end
                end
            end
            if probe=="explore" and buttons.Right and not previous.Right then
                local card=L.rw("wPokegearCard")+1
                put("wPokegearCard",card);put("wJumptableIndex",({[0]=1,4,10,14})[card]);redraw(card)
            end
            if probe=="panel" and buttons.B and not previous.B and phase=="panel" then
                hit("SlinkPanel.close");native(offset("wSlinkMailbox",9),0,"WRAM")
                if fault=="multiple" then native(offset("wSlinkMailbox",9),0,"WRAM") end
                phase="list";put("wJumptableIndex",10);hit("PokegearPhone_Joypad")
            end
            render();previous=buttons
            if L.on_frame then L.on_frame() end
        end
        function L.idle(n) for _=1,n do L.frame() end end
        function L.pulse(button) L.frame(button and {[button]=true} or nil);L.frame() end
        put("wMenuItemsList",1);put("wMenuItemsList",7,1);put("wMenuCursorY",1)
        put("wPokegearCard",probe=="panel" and 2 or 0);put("wJumptableIndex",probe=="panel" and 10 or 1)
        put("wSlinkMailbox",7,8);put("wSlinkMailbox",83,9)
        local initial = "SLink:"
        for c=1,#initial do put("wTilemap",cmap.encoding[initial:sub(c,c)],4*20+c-1) end
        os.getenv=function(name) if name=="SLINK_ROOT" then return root elseif name=="POL_EXPLORE" then return "C" end end
        local real_open=io.open
        io.open=function(path,mode)
            if path:match("^memory/") then
                local buffer="";return {write=function(self,s) buffer=buffer..s end,
                    close=function() files[path]=buffer end}
            end
            return real_open(path,mode)
        end
        dofile=function(path) if path:match("pol_lib.lua$") then return L end return original_dofile(path) end
        local filename=probe=="panel" and "pol_panel.lua" or "explore.lua"
        local ok,why=pcall(original_dofile,root.."/tools/polished_live/"..filename)
        assert(not ok and why=="finished",tostring(why))
        evidence=assert(files["memory/"..(probe=="panel" and "panel.json" or "pokegear.json")])
        remaining_native_hooks=0
        for _,h in pairs(handlers) do if h.kind=="write" then remaining_native_hooks=remaining_native_hooks+1 end end
        check_results=checks
    ''')
    return json.loads(lua.globals().evidence), lua


def test_panel_host_records_actual_values_and_setup_separately():
    report, _ = run_probe("panel")
    audit = report["write_audit"]
    setup = [w for w in audit["write_ops"] if w["phase"] == "synth"]
    host = [w for w in audit["write_ops"] if w["phase"] == "panel"]
    assert len(setup) == 6
    assert len(host) == 72
    assert all(w["domain"] == "WRAM" and w["n"] == 1 for w in host)
    assert all(w["status"] == "returned" for w in host)
    text_stores = [w for w in host if w["addr"] == 0x62D]
    assert [(w["old"], w["new"]) for w in text_stores] == [(0, 0x92), (0x92, 0x8F)]
    assert [w["sequence"] for w in audit["write_ops"]] == list(range(1, 79))
    assert report["page2_rows"][0] == "PAIRS 2/3"
    assert report["page2_rows"][1] == "BADGES 4/8"


@pytest.mark.parametrize("fault", ["none", "multiple"])
def test_panel_native_clear_uses_images_not_callback_value(fault):
    report, lua = run_probe("panel", fault)
    close = report["close"]
    writes = close["writes"]
    assert close["before"]["panel_state"] == 2
    assert close["after"]["panel_state"] == 0
    assert close["after"]["phone_state"] == 10
    assert close["before"]["calls"] == close["after"]["calls"] == 0
    assert lua.globals().remaining_native_hooks == 0
    if fault == "none":
        assert len(writes) == 1
        assert (writes[0]["old"], writes[0]["new"]) == (2, 0)
        assert writes[0]["callback_value"] == 249
    else:
        assert len(writes) == 2
        assert all(w["old"] is None and w["new"] is None and w["reason"] for w in writes)


def test_explore_emits_every_card_native_stores_and_full_images():
    report, lua = run_probe("explore")
    assert lua.globals().remaining_native_hooks == 0
    for card in report["cards"]:
        redraw = card["redraw"]
        assert len(redraw["before"]["tilemap"]) == len(redraw["after"]["tilemap"]) == 720
        assert len(redraw["before"]["attrmap"]) == len(redraw["after"]["attrmap"]) == 720
        assert {w["addr"] for w in redraw["writes"] if w["target"] != "bgmap"} == {
            0xC108, 0xC109, 0xC11C, 0xC11D, 0xD408, 0xD409, 0xD41C, 0xD41D,
        }
        assert any(w["new"] == 40 + card["card"] and w["callback_value"] == 249 for w in redraw["writes"])


def test_explore_multiple_stores_do_not_invent_intermediate_values():
    report, _ = run_probe("explore", "multiple")
    writes = report["cards"][0]["redraw"]["writes"]
    ambiguous = [w for w in writes if w["addr"] == 0xc108]
    assert len(ambiguous) == 2
    assert all(w["old"] is None and w["new"] is None and w["reason"] for w in ambiguous)


def test_panel_off_mailbox_store_is_present_in_operations_and_full_images():
    report, _ = run_probe("panel", "outside")
    audit = report["write_audit"]
    outside = [w for w in audit["write_ops"] if w["phase"] == "panel" and w["addr"] > audit["mailbox"]["last"]]
    assert any(w["addr"] == 0x650 and w["new"] == 0x7F for w in outside)
    batch = next(b for b in audit["batches"] if b["phase"] == "panel")
    assert bytes.fromhex(batch["before"]["hex"])[0x650] == 0
    assert bytes.fromhex(batch["after"]["hex"])[0x650] == 0x7F


def test_explore_idempotent_store_is_not_erased_by_equal_images():
    report, _ = run_probe("explore", "idempotent")
    writes = report["cards"][0]["redraw"]["writes"]
    same = next(w for w in writes if w["addr"] == 0xC108)
    assert (same["old"], same["new"], same["callback_value"]) == (0, 0, 249)


def test_explore_wrong_wram_bank_does_not_claim_a_target_store():
    report, _ = run_probe("explore", "wrong_bank")
    redraw = report["cards"][0]["redraw"]
    wrong = [w for w in redraw["writes"] if w["addr"] == 0xD408]
    assert len(wrong) == 1
    assert not wrong[0]["qualified"]
    assert wrong[0]["old"] is None and wrong[0]["new"] is None
    assert wrong[0]["reason"] == "wrong_wram_bank"
    assert bytes.fromhex(redraw["after"]["attrmap"])[8] == 0


def test_explore_bg_transfer_uses_native_bank_and_32_cell_stride():
    report, _ = run_probe("explore", "vram")
    writes = report["cards"][0]["redraw"]["writes"]
    bg = [w for w in writes if 0x9800 <= w["addr"] < 0xA000]
    assert {(w["addr"], w["vram_bank"]) for w in bg} == {
        (address, bank)
        for address in (0x9808, 0x9809, 0x9828, 0x9829, 0x9C08, 0x9C09, 0x9C28, 0x9C29)
        for bank in (0, 1)
    }
    assert all(w["qualified"] and w["old"] == 0 and w["new"] == 140 + w["vram_bank"] for w in bg)
