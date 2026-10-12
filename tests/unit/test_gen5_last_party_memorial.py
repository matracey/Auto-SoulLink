"""Exercise the production memorial executor and queue against modeled RAM."""

from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
KEY = "89ABCDEF:FEDCBA98"

MODEL = r"""
local key = "89ABCDEF:FEDCBA98"
local fmt, _RAM, MEMORIAL_BOX = string.format, "Main RAM", 23
local bytes, writes, events = {}, {}, {}
local state = {count=1, readable=true}
local pending_sync_cmds, sync_written_keys, all_known_keys = {}, {}, {}
local in_battle, battle_just_ended, writes_enabled = false, false, true
local sync_cooldown, post_battle_frames, sync_block_log_timer = 0, 0, 0
local game_over_flag = false
local console = {log=function() end}
local function hud_show() end
local function nick_label(value) return value end
local function send(event) events[#events + 1] = event end
local memory = {}
function memory.read_u8(addr) return bytes[addr] or 0 end
function memory.write_u8(addr, value)
    writes[#writes + 1] = addr
    bytes[addr] = value
end
local function mem_u16(addr) return (bytes[addr] or 0) + (bytes[addr+1] or 0)*256 end
local function mem_u32(addr) return mem_u16(addr) + mem_u16(addr+2)*65536 end
local function mem_w16(addr, value)
    memory.write_u8(addr, value%256)
    memory.write_u8(addr+1, math.floor(value/256))
end
local function put_pid(addr, pid)
    for i=0,3 do bytes[addr+i] = math.floor(pid/256^i)%256 end
end
put_pid(100, 0x89ABCDEF)
local M = {MON_SIZE=220}
function M.pcStorageBase() return 1000 end
function M.readPartyCount() return state.count end
function M.writePartyCount(count) state.count = count end
function M.partyAddr(slot) return 100 + slot*220 end
function M.pcBoxAddr(box, slot) return 10000 + box*4096 + slot*136 end
function M.pcBoxFirstEmpty() return 0 end
function M.monKey(addr) return fmt("%08X", mem_u32(addr)) end
function M.decrypt_block_a(addr)
    if state.readable and addr == 100 then return 25, 0xFEDCBA98 end
end
function M.clearDebounce() end
local function exec_party_mon()
    state.count = 2
    put_pid(320, 0x12345678)
    events[#events + 1] = {event="retrieved"}
end
local function exec_box_mon() error("unexpected deposit") end
"""


def world():
    source = (ROOT / "lua/clients/gen5_bw_client.lua").read_text(encoding="utf-8")
    executor = source[source.index("local function exec_memorialize("):source.index("local function exec_box_mon(")]
    dispatcher = source[source.index("    -- 5b."):source.index("    -- 6.")]
    return LuaRuntime(unpack_returned_tuples=True).execute(
        MODEL + executor + "\nlocal function frame()\n" + dispatcher + r"""
end
return {
    frame=frame, execute=exec_memorialize, state=state, writes=writes, events=events,
    queue=function(retrieve)
        pending_sync_cmds = {{cmd="memorialize", key=key}}
        if retrieve then pending_sync_cmds[2] = {cmd="party_mon", key="12345678:00000001"} end
    end,
    pending=function() return pending_sync_cmds end,
    game_over=function() game_over_flag=true end,
    add_party=exec_party_mon,
    boxed=function()
        put_pid(100, 0x12345678)
        put_pid(M.pcBoxAddr(0,0), 0x89ABCDEF)
    end,
    pid=function(box) return mem_u32(M.pcBoxAddr(box,0)) end,
}
"""
    )


def test_executor_holds_last_party_mon_before_any_write():
    model = world()
    assert model.execute(KEY) == "held"
    assert len(model.writes) == 0 and len(model.events) == 0
    assert model.state.count == 1 and model.pid(23) == 0


def test_dispatcher_promotes_retrieval_before_burial():
    model = world()
    model.queue(True)
    model.frame()
    assert len(model.writes) == 0 and len(model.events) == 0
    assert [cmd.cmd for cmd in model.pending().values()] == ["party_mon", "memorialize"]
    model.frame()
    assert model.state.count == 2
    model.frame()
    assert model.state.count == 1 and model.pid(23) == 0x89ABCDEF
    assert [event.event for event in model.events.values()] == ["retrieved", "memorialize_done"]
    assert len(model.pending()) == 0


@pytest.mark.parametrize("readable", [True, False])
def test_held_memorial_does_not_exhaust_retries(readable):
    model = world()
    model.state.readable = readable
    model.queue(False)
    for _ in range(10):
        model.frame()
    assert len(model.pending()) == 1
    assert model.pending()[1]._retries is None
    assert len(model.writes) == 0 and len(model.events) == 0


def test_game_over_last_party_mon_is_not_copied():
    model = world()
    model.queue(False)
    model.game_over()
    model.frame()
    assert len(model.pending()) == 0 and len(model.writes) == 0


@pytest.mark.parametrize("location", ["party", "box"])
def test_non_last_party_and_boxed_burials_still_complete(location):
    model = world()
    if location == "party":
        model.add_party()
    else:
        model.boxed()
    model.execute(KEY)
    assert model.events[len(model.events)].event == "memorialize_done"
    assert model.state.count == 1 and model.pid(23) == 0x89ABCDEF
