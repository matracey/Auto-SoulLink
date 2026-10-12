"""Raw panel memory receipts and native close state, without an emulator."""
import json
from pathlib import Path

import pytest
from lupa.lua55 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]


def rig():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().root = ROOT.as_posix()
    lua.execute(r'''
        POL_ORACLE_ONLY = true
        O = dofile(root .. "/tools/polished_live/panel_host_live.lua")
        J = dofile(root .. "/lua/json_codec.lua")
        local Panel = dofile(root .. "/lua/gen2/panel.lua")
        local Permit = dofile(root .. "/lua/write_permit.lua")
        ram = {[0xC610]=17, [0xC611]=34, [0xC612]=51, [0xC613]=68, [0xC614]=85}
        memory = {
            read_u8 = function(addr) return ram[addr] end,
            write_u8 = function(addr, value)
                ram[addr] = corrupt_sink and 42 or value
            end,
        }
        frame = 50
        native = {wPokegearPhoneCursorPosition=2, wPokegearPhoneScrollPosition=3,
                  wCurCaller=7, wJumptableIndex=10, wSlinkMailbox=2}
        phone_list = {9, 8, 7, 6, 5}
        L = {json=J, hits={MakePhoneCallFromPokegear=4}}
        function L.bus(addr) return memory.read_u8(addr, "System Bus") end
        function L.rw(name) return native[name] end
        function L.wbytes(name) assert(name == "wPhoneList"); return phone_list end
        emu = {framecount=function() return frame end}
        local io = {framecount=emu.framecount,
                    write_u8=function(a,v,d) return memory.write_u8(a,v,d) end}
        writes = Panel.writes(io, Permit, {base=0xC610, size=5})
    ''')
    return lua


def payload(lua, expression):
    return json.loads(lua.eval(f"J.encode({expression})"))


def test_receipt_records_actual_sink_calls_span_images_and_whole_mailbox():
    lua = rig()
    lua.execute('''
        memory, audit = O.observe_panel_writes(writes, L, memory, 0xC610, 0xC614)
        writes:arm("panel")
        writes:write_bytes(0xC612, {171, 205})
        evidence = {client_receipts=O.receipts(writes.log, audit, J)}
    ''')
    assert payload(lua, "evidence")["client_receipts"] == [{
        "addr": "C612", "n": 2, "why": "panel", "frame": 50,
        "status": "written", "domain": "System Bus",
        "values": [171, 205], "before": [51, 68], "after": [171, 205],
        "mailbox_before": [17, 34, 51, 68, 85],
        "mailbox_after": [17, 34, 171, 205, 85],
        "ops": [
            {"addr": 0xC612, "value": 171, "before": 51, "after": 171, "domain": "System Bus"},
            {"addr": 0xC613, "value": 205, "before": 68, "after": 205, "domain": "System Bus"},
        ],
    }]


def test_values_follow_emission_not_the_callers_requested_bytes():
    lua = rig()
    lua.execute('''
        local original = writes.write_bytes
        writes.write_bytes = function(self, addr, bytes)
            return original(self, addr, {201, 202})
        end
        memory, audit = O.observe_panel_writes(writes, L, memory, 0xC610, 0xC614)
        writes:arm("panel")
        writes:write_bytes(0xC612, {171, 205})
    ''')
    receipt = payload(lua, "O.receipts(writes.log, audit, J)")[0]
    assert receipt["values"] == [201, 202]
    assert receipt["before"] == [51, 68]
    assert receipt["after"] == [201, 202]


def test_corrupt_sink_is_observed_not_replaced_with_requested_payload():
    lua = rig()
    lua.execute('''
        memory, audit = O.observe_panel_writes(writes, L, memory, 0xC610, 0xC614)
        corrupt_sink = true
        writes:arm("panel")
        writes:write_bytes(0xC612, {171, 205})
    ''')
    receipt = payload(lua, "O.receipts(writes.log, audit, J)")[0]
    assert receipt["values"] == [171, 205]
    assert receipt["after"] == [42, 42]
    assert receipt["mailbox_after"] == [17, 34, 42, 42, 85]
    assert [op["after"] for op in receipt["ops"]] == [42, 42]


def test_empty_receipts_are_an_explicit_json_array():
    lua = rig()
    lua.execute('memory, audit = O.observe_panel_writes(writes, L, memory, 0xC610, 0xC614)')
    assert payload(lua, "O.receipts(writes.log, audit, J)") == []


@pytest.mark.parametrize("changed", [False, True], ids=["intact", "collateral-changed"])
def test_close_snapshots_keep_independent_native_pre_and_post_state(changed):
    lua = rig()
    lua.execute('before = O.phone_snapshot(L)')
    if changed:
        lua.execute('''
            native.wPokegearPhoneCursorPosition=1
            native.wPokegearPhoneScrollPosition=0
            native.wCurCaller=11
            L.hits.MakePhoneCallFromPokegear=5
            phone_list[1]=12
        ''')
    lua.execute('native.wSlinkMailbox=0; frame=60; after = O.phone_snapshot(L)')
    assert payload(lua, "before") == {
        "frame": 50, "cursor": 2, "scroll": 3, "phone_calls": 4,
        "caller": 7, "phone_list": [9, 8, 7, 6, 5], "phone_state": 10, "panel_state": 2,
    }
    assert payload(lua, "after") == {
        "frame": 60, "cursor": 1 if changed else 2, "scroll": 0 if changed else 3,
        "phone_calls": 5 if changed else 4, "caller": 11 if changed else 7,
        "phone_list": [12 if changed else 9, 8, 7, 6, 5], "phone_state": 10, "panel_state": 0,
    }
