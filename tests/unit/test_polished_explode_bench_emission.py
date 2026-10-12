"""Deferred bench proof is raw slot-qualified evidence, not active-battler hooks."""
import json
from pathlib import Path

import pytest
from lupa.lua55 import LuaError, LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
KEY = "BENCH"
STATUS_ADDR = 0xD180
HP_ADDR = 0xD182


def run_driver(run_dir, *, wrong_slot=False, echo=False, corrupt=False):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().driver_root = ROOT.as_posix()
    lua.globals().run_dir = run_dir.as_posix()
    lua.globals().wrong_slot = wrong_slot
    lua.globals().emit_echo = echo
    lua.globals().corrupt = corrupt
    lua.execute(r'''
        local real_dofile=dofile
        J=real_dofile(driver_root .. "/lua/json_codec.lua")
        frame=10
        ram={}; high={}
        local function flat(addr)
            if addr >= 0xd000 then return 0x1000+addr-0xd000 end
            return addr-0xc000
        end
        memory={
            read_u8=function(addr,domain)
                if domain=="System Bus" then
                    if addr==0xff70 then return 1 end
                    if addr>=0xff80 then return high[addr] or 0 end
                    return ram[flat(addr)] or 0
                end
                return ram[addr] or 0
            end,
            write_u8=function(addr,value,domain)
                assert(domain=="System Bus")
                ram[flat(addr)]=corrupt and addr==0xd183 and 42 or value
                return "sink-return", 17
            end,
            getmemorydomainsize=function(domain) return domain=="ROM" and 1024 or 0x1200 end,
            getmemorydomainlist=function() return {"WRAM"} end,
        }
        local sym={wBattleMode={0,0xc200},wCurBattleMon={0,0xc201},wBattlePlayerAction={0,0xc202},
            wPlayerSubStatus2={0,0xc203},wCurPlayerMove={0,0xc204},wMapStatus={0,0xc205},
            wScriptRunning={0,0xc206},wLinkMode={0,0xc207},wBattleMonHP={0,0xc208},
            wPartyMons={1,0xd100},hROMBank={0,0xff80},hBattleTurn={0,0xff81}}
        ram[0x200]=1; ram[0x205]=2; high[0xff80]=15
        ram[0x1123]=200
        ram[0x1180]=3; ram[0x1181]=85; ram[0x1183]=152
        config={case="bench-faint",target={slot=2,key="BENCH"},frame_cap=1000,
            provenance={rom_sha1="MODEL"},disclosure="MODEL memory and host; no physical proof",
            steps={{frames=3,buttons={"A"}}},
            contract={schema="polished-explode-live-v1",symbols=sym,
                geometry={status=32,hp=34},sites={hold={bank=15,addr=0x4000}},
                snapshot_sizes={WRAM=0x1200,HRAM=127}}}
        emu={framecount=function() return frame end,getregister=function() return 0x4000 end}
        client={speedmode=function() end,screenshot=function() end}
        event={on_bus_exec=function() return 1 end,
            onframeend=function(callback) frame_callback=callback; return 2 end,
            unregisterbyid=function() end}
        L={ROOT=driver_root,RUN=run_dir,json=J}
        function L.slurp() return J.encode(config) end
        function L.log() end
        function L.hex(bytes) local out={};for _,v in ipairs(bytes) do out[#out+1]=string.format("%02x",v) end;return table.concat(out) end
        function L.check() end
        function L.finish() error("finished",0) end
        function L.frame()
            frame=frame+1
            frame_callback()
            if held then ram[0x200]=0 end
        end
        function L.pulse() L.frame() end
        function L.idle(n) for _=1,n do L.frame() end end
        local function party()
            local mons={}
            for slot=0,2 do
                local base=0xd100+slot*48
                mons[slot+1]={key=slot==2 and "BENCH" or "ACTIVE",slot=slot,
                    hp=memory.read_u8(base+34,"System Bus")*256+memory.read_u8(base+35,"System Bus")}
            end
            return {mons=mons}
        end
        package.loaded.connector={init=function() end,connected=function() return true end,
            pump=function() end,receive=function() end,send=function() end}
        os.getenv=function(name)
            return ({SLINK_ROOT=driver_root,POL_EXPLODE_CONFIG="stub-config",
                     SLINK_HOST="127.0.0.1",SLINK_PORT="54321"})[name]
        end
        dofile=function(path)
            if path:match("pol_lib.lua$") then return L end
            if path:match("/lua/gen2/entry.lua$") then
                return {build=function(deps)
                    local c={dead_keys={},writes_enabled=true,faint_settle={last_identity="MODEL",owed={}}}
                    function c:start() end
                    function c:stop() end
                    function c:on_event() end
                    function c:at_battle_hold() held=true end
                    function c:handle_command() pending=true end
                    function c:run_deferred()
                        local slot=wrong_slot and 0 or 2
                        local base=0xd100+slot*48
                        for _,offset in ipairs({32,34,35}) do deps.io.write_u8(base+offset,0,"System Bus") end
                        self.dead_keys.BENCH=true
                        deps.hud.show("QUAL_TARGET KO'd")
                        if emit_echo then deps.net.send(J.encode({event="faint",key="BENCH"})) end
                        pending=false
                    end
                    function c:frame_end()
                        if pending then
                            if ram[0x200]==1 then self:at_battle_hold() else self:run_deferred() end
                        end
                        if self.dead_keys.BENCH then
                            deps.net.send(J.encode({event="tick",party=party().mons}))
                        end
                    end
                    return {client=c,reads={read_party=party},runtime_rom_sha1="MODEL"}
                end}
            end
            return real_dofile(path)
        end
    ''')
    with pytest.raises(LuaError, match="^finished"):
        lua.execute('dofile(driver_root .. "/tools/polished_live/explode_live.lua")')
    trace = json.loads((run_dir / "trace.json").read_text())
    assert not [row for row in trace if row["kind"] == "driver_error"]
    return trace


def operation(trace):
    return next(row for row in trace if row["kind"] == "operation")


def test_deferred_bench_record_has_ordered_sink_ops_and_exact_target_pre_post(tmp_path):
    trace = run_driver(tmp_path)
    op = operation(trace)
    assert op["context"] == "overworld" and op["mode"] == 0
    assert op["slot"] == 0  # active-battler observation is deliberately not relabelled.
    before, after = op["bench_before"], op["bench_after"]
    assert (before["slot"], before["key"], before["addr"], before["n"], before["bytes"], before["hp"]) == (
        2, KEY, STATUS_ADDR, 4, [3, 85, 0, 152], 152,
    )
    assert (after["slot"], after["key"], after["addr"], after["bytes"], after["hp"]) == (
        2, KEY, STATUS_ADDR, [0, 85, 0, 0], 0,
    )
    assert before["domain"] == after["domain"] == "System Bus"
    assert [(w["sequence"], w["addr"], w["value"], w["domain"], w["frame"], w["party_slot"], w["party_key"], w["pre_hex"], w["post_hex"])
            for w in op["writes"]] == [
        (1, STATUS_ADDR, 0, "System Bus", 13, 2, KEY, "03", "00"),
        (2, HP_ADDR, 0, "System Bus", 13, 2, KEY, "00", "00"),
        (3, HP_ADDR+1, 0, "System Bus", 13, 2, KEY, "98", "00"),
    ]


def test_wrong_slot_write_cannot_be_reported_as_target_slot_hp0(tmp_path):
    op = operation(run_driver(tmp_path, wrong_slot=True))
    assert [w["party_slot"] for w in op["writes"]] == [0, 0, 0]
    assert [w["party_key"] for w in op["writes"]] == ["ACTIVE"] * 3
    assert op["bench_after"]["key"] == KEY
    assert op["bench_after"]["slot"] == 2
    assert op["bench_after"]["hp"] == 152
    assert op["bench_after"]["bytes"] == [3, 85, 0, 152]


def test_corrupt_sink_postimage_is_read_not_reconstructed_from_zero_requests(tmp_path):
    op = operation(run_driver(tmp_path, corrupt=True))
    assert op["writes"][-1]["value"] == 0
    assert op["writes"][-1]["post_hex"] == "2a"
    assert op["bench_after"]["hp"] == 42
    assert op["bench_after"]["bytes"] == [0, 85, 0, 42]


@pytest.mark.parametrize("echo", [False, True], ids=["no-echo", "echo-retained"])
def test_wire_window_retains_all_events_and_bounds_without_a_zero_count_claim(tmp_path, echo):
    trace = run_driver(tmp_path, echo=echo)
    window = trace[-1]["bench_wire_window"]
    command = next(row for row in trace if row["kind"] == "command")
    assert window["from_ord"] == command["ord"]
    assert window["from_frame"] == command["frame"] == 11
    assert window["to_frame"] == trace[-1]["frame"] == 103
    events = [json.loads(line) for line in (tmp_path / window["file"]).read_text().splitlines()]
    originals = [row for row in trace if row["kind"] == "wire" and row["ord"] >= command["ord"]]
    assert events == originals
    assert any(row["message"]["event"] == "tick" and row["message"]["party"][2]["hp"] == 0 for row in events)
    faint = [row["message"] for row in events if row["message"]["event"] == "faint"]
    assert faint == ([{"event": "faint", "key": KEY}] if echo else [])
