"""Run the production boxed-capture and grace-period blocks without a cartridge."""

from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
PID = "89ABCDEF"
AREA = "route_1"

MODEL = r"""
local fmt = string.format
local events, logs, notices = {}, {}, {}
local console = {log=function(text) logs[#logs + 1] = text end}
local function hud_show(text) notices[#notices + 1] = text end
local function send(event) events[#events + 1] = event end
local area, battle_area_id = "route_2", "route_1"
local battle_enc_species, battle_enc_level = 519, 10
local nuzlocke_active, battle_is_wild = true, true
local in_battle = false
local captured_this_battle, post_battle_frames = false, 1
local resolved_areas, all_known_keys, GIFT_AREAS = {}, {}, {}
local battle_box_snapshot = {box=3, pids={["00000001"]=true}}
local pending_box_captures = {}
local state = {species=519, ot=0xFEDCBA98, address=true, candidates=1, extra={}}
local M = {}
function M.readBoxPIDs(box)
    assert(box == 3)
    local pids = {["00000001"]=true}
    if state.candidates > 0 then pids["89ABCDEF"] = true end
    if state.candidates > 1 then pids["12345678"] = true end
    return pids
end
function M.pcBoxAddr(box, slot)
    assert(box == 3)
    if state.address then return 1000 + slot*136 end
end
local function mem_u32(addr)
    if state.extra[addr] then return state.extra[addr].pid end
    return addr == 1000 and 0x89ABCDEF or 0
end
function M.decrypt_block_a(addr)
    if state.extra[addr] then return 519, 0xFEDCBA98 end
    assert(addr == 1000)
    return state.species, state.ot
end
"""


def world():
    source = (ROOT / "lua/clients/gen5_bw_client.lua").read_text(encoding="utf-8")
    capture = source[source.index("    -- 8b."):source.index("    -- 9.")]
    grace = source[source.index("    -- 10."):source.index("    -- 11.")]
    lua = LuaRuntime(unpack_returned_tuples=True)
    return lua.execute(MODEL + "\nlocal function frame()\n" + capture + grace + r"""
end
return {
    frame=frame, state=state, events=events, logs=logs, notices=notices,
    resolved=resolved_areas, known=all_known_keys,
    pending=function() return #pending_box_captures > 0 end,
    grace=function() return post_battle_frames end,
    battle=function(active)
        in_battle, battle_area_id = active, "route_3"
        if active then battle_box_snapshot = nil end
    end,
    queue=function(pid, capture_area, slot)
        battle_box_snapshot, captured_this_battle = nil, true
        pending_box_captures[#pending_box_captures + 1] = {pid=pid, box=3, area_id=capture_area}
        if slot then state.extra[1000 + slot*136] = {pid=tonumber(pid, 16)} end
    end,
}
""")


@pytest.mark.parametrize("ot", [0, 0xFEDCBA98])
def test_box_capture_publishes_full_identity_once(ot):
    model = world()
    model.state.ot = ot
    model.frame()
    assert len(model.events) == 1
    event = model.events[1]
    key = f"{PID}:{ot:08X}"
    assert (event.event, event.key, event.species_id, event.area_id) == (
        "capture", key, 519, AREA,
    )
    assert event.box_capture is True
    assert model.known[key] is True and model.known[PID] is None
    assert model.resolved[AREA] is True and model.resolved["route_2"] is None
    assert model.pending() is False
    model.frame()
    assert len(model.events) == 1


@pytest.mark.parametrize("missing", ["address", "species", "ot"])
def test_unreadable_capture_waits_without_publishing_or_losing_the_area(missing):
    model = world()
    model.state[missing] = False if missing == "address" else None
    for _ in range(3):
        model.frame()
        assert len(model.events) == 0
        assert not list(model.known.keys()) and not list(model.resolved.keys())
        assert model.pending() is True and model.grace() == 0
    assert len(model.logs) == 1 and len(model.notices) == 1
    model.state.address, model.state.species, model.state.ot = True, 519, 0xFEDCBA98
    model.frame()
    assert len(model.events) == 1
    assert model.events[1].key == f"{PID}:FEDCBA98"
    assert model.pending() is False and model.grace() == 0


def test_pending_identity_survives_a_later_battle_without_changing_its_area():
    model = world()
    model.state.ot = None
    model.frame()
    model.battle(True)
    model.state.ot = 0xFEDCBA98
    model.frame()
    assert len(model.events) == 0 and model.pending() is True
    model.battle(False)
    model.frame()
    assert len(model.events) == 1
    assert (model.events[1].event, model.events[1].area_id) == ("capture", AREA)
    assert model.resolved["route_3"] is None


def test_unchanged_box_still_finishes_as_no_catch():
    model = world()
    model.state.candidates = 0
    model.frame()
    assert len(model.events) == 1
    assert (model.events[1].event, model.events[1].area_id) == ("no_catch", AREA)
    assert model.pending() is False and model.grace() == 0


def test_multiple_new_records_do_not_guess_an_identity():
    model = world()
    model.state.candidates = 2
    model.frame()
    assert len(model.events) == 0
    assert not list(model.known.keys()) and not list(model.resolved.keys())
    assert model.pending() is False and model.grace() == 0


@pytest.mark.parametrize("missing", ["record", "identity"])
def test_held_capture_does_not_block_other_areas(missing):
    model = world()
    if missing == "record":
        model.queue("DEADBEEF", AREA, None)
    else:
        model.state.ot = None
        model.frame()
    model.queue("12345678", "route_2", 1)
    model.queue("11111111", "route_3", 2)
    for _ in range(3):
        model.frame()
    assert [model.events[i].area_id for i in range(1, len(model.events) + 1)] == ["route_2", "route_3"]
    assert model.pending() is True and model.resolved[AREA] is None
    assert len(model.logs) == 1 and len(model.notices) == 1


def test_held_capture_preserves_same_area_order_while_other_areas_progress():
    model = world()
    model.state.ot = None
    model.frame()
    model.queue("12345678", AREA, 1)
    model.queue("11111111", "route_2", 2)
    model.frame()
    assert len(model.events) == 1 and model.events[1].area_id == "route_2"
    assert model.pending() is True and model.resolved[AREA] is None
    model.state.ot = 0xFEDCBA98
    model.frame()
    assert [model.events[i].key for i in range(1, 4)] == [
        "11111111:FEDCBA98", f"{PID}:FEDCBA98", "12345678:FEDCBA98",
    ]
    assert model.pending() is False
