"""Run the production retrieval and queue with a modeled boxed record."""

from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
KEY = "89ABCDEF:FEDCBA98"
STATS = {"level": 42, "maxHP": 123, "attack": 71, "defense": 72, "speed": 73, "spAtk": 74, "spDef": 75}

MODEL = r"""
local fmt, _RAM, MEMORIAL_BOX = string.format, "Main RAM", 23
local bytes, writes, events, notices, encoded = {}, {}, {}, {}, {}
local state = {count=1}
local pending_sync_cmds, sync_written_keys, all_known_keys = {}, {}, {}
local in_battle, battle_just_ended, writes_enabled = false, false, true
local sync_cooldown, post_battle_frames, sync_block_log_timer = 0, 0, 0
local game_over_flag = false
local console = {log=function() end}
local function hud_show(text) notices[#notices+1] = text end
local function nick_label(value) return value end
local function send(event) events[#events+1] = event end
local memory = {}
function memory.read_u8(addr) return bytes[addr] or 0 end
function memory.write_u8(addr, value)
    writes[#writes+1] = addr
    bytes[addr] = value
end
local function mem_u32(addr)
    local value = 0
    for i=0,3 do value = value + (bytes[addr+i] or 0)*256^i end
    return value
end
local function put_pid(addr, pid)
    for i=0,3 do bytes[addr+i] = math.floor(pid/256^i)%256 end
end
put_pid(100, 0x12345678)
put_pid(10000, 0x89ABCDEF)
local M = {MON_SIZE=220}
function M.readPartyCount() return state.count end
function M.writePartyCount(count) state.count = count end
function M.partyAddr(slot) return 100+slot*220 end
function M.pcBoxAddr(box, slot) return 10000+box*4096+slot*136 end
function M.clearDebounce() end
function M.encrypt_stats(...) encoded[#encoded+1] = {...} end
local function exec_box_mon() error("unexpected deposit") end
local function exec_memorialize() error("unexpected memorial") end
"""


def world():
    source = (ROOT / "lua/clients/gen5_bw_client.lua").read_text(encoding="utf-8")
    executor = source[source.index("local function exec_party_mon("):source.index("-- ── Party helpers")]
    commands = source[source.index("local function _filter_pending("):source.index("local function exec_memorialize(")]
    commands = commands[:commands.index("\nend", commands.index("local function dispatch_commands(")) + 4]
    queue = source[source.index("    -- 5b."):source.index("    -- 6.")]
    lua = LuaRuntime(unpack_returned_tuples=True)
    model = lua.execute(MODEL + commands + "\n" + executor + "\nlocal function frame()\n" + queue + r"""
end
return {
    execute=exec_party_mon, state=state, writes=writes, events=events, notices=notices,
    encoded=encoded, frame=frame, pending=function() return pending_sync_cmds end,
    dispatch=function(stats)
        dispatch_commands({{cmd="party_mon", key="89ABCDEF:FEDCBA98", stats=stats}})
    end,
    present=function() put_pid(100, 0x89ABCDEF) end,
    boxed_pid=function() return mem_u32(10000) end,
}
""")
    return lua, model


def test_retrieval_passes_all_supplied_stats_to_encoder():
    lua, model = world()
    model.dispatch(lua.table_from(STATS))
    model.frame()
    assert list(model.encoded[1].values()) == [320 + 0x88, 0x89ABCDEF, 0, 42, 123, 123, 71, 72, 73, 74, 75]
    assert model.state.count == 2 and model.boxed_pid() == 0
    assert [event.event for event in model.events.values()] == ["sync_retrieve_done"]
    assert len(model.pending()) == 0


@pytest.mark.parametrize("field", STATS)
@pytest.mark.parametrize("value", [None, 0, -1, 1.5, 65536, "75", True])
def test_invalid_stats_hold_before_any_write(field, value):
    lua, model = world()
    stats = lua.table_from(STATS)
    stats[field] = value
    model.dispatch(stats)
    for _ in range(10):
        model.frame()
    assert model.state.count == 1 and model.boxed_pid() == 0x89ABCDEF
    assert len(model.writes) == len(model.encoded) == len(model.events) == 0
    assert len(model.pending()) == 1 and model.pending()[1]._retries is None
    assert len(model.notices) == 1


def test_missing_stat_block_waits_for_refreshed_command():
    lua, model = world()
    model.dispatch(None)
    for _ in range(10):
        model.frame()
    assert len(model.writes) == 0 and len(model.pending()) == 1
    model.dispatch(lua.table_from(STATS))
    assert len(model.pending()) == 1
    model.frame()
    assert model.state.count == 2 and len(model.encoded) == 1
    assert len(model.pending()) == 0
    assert [event.event for event in model.events.values()] == ["sync_retrieve_done"]


def test_existing_party_copy_does_not_require_restoration_stats():
    _, model = world()
    model.present()
    model.dispatch(None)
    model.frame()
    assert len(model.writes) == 0 and len(model.pending()) == 0
    assert [event.event for event in model.events.values()] == ["sync_retrieve_done"]


def test_level_above_game_limit_is_held():
    lua, model = world()
    model.dispatch(lua.table_from({**STATS, "level": 101}))
    model.frame()
    assert len(model.writes) == 0 and len(model.pending()) == 1
