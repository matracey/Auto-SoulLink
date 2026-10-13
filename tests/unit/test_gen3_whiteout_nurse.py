"""Execute the real driver walk and whiteout prelude through lupa; no emulator."""
from pathlib import Path

import pytest
from lupa import lua54

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "lua/tests/duo/duo_gen3_main.lua"
SCENARIO = ROOT / "lua/tests/duo/scenario_gen3_whiteout.lua"


def world(*, emerald=False, fault="", full=False):
    lua = lua54.LuaRuntime(unpack_returned_tuples=True)
    lua.globals().EMERALD_ENGINE = emerald
    lua.globals().fault = fault
    lua.globals().initial_full = full
    lua.execute(r'''
        events = {}
        function record(s) events[#events + 1] = s end
        fmt, log, cp = string.format, record, {}
        local healed, locked, presses = initial_full, false, 0
        ctx = {player = "a"}
        ctx.party = function()
            if fault == "unreadable" then return nil end
            return {{hp = healed and 20 or 0, max_hp = 20}, {hp = 15, max_hp = 15}}
        end
        ctx.wait_go = function() return true end
        ctx.linked = function() return "linked" end
        ctx.find = function() return {slot = 1} end
        ctx.pc_deposit = function()
            assert(healed, "deposit before heal")
            assert(not locked, "deposit during nurse dialogue")
            record("deposit")
            return nil, "test-stop-after-deposit"
        end
        G = {
            tap = function(button)
                record(button)
                if button == "A" then
                    assert(not healed, "A after HealParty")
                    locked = true
                    presses = presses + 1
                    if presses == 3 and fault ~= "no_heal" then healed = true end
                elseif button == "B" and fault ~= "locked" then locked = false end
            end,
            pred_ok = function() return not locked end,
        }
        play = {
            follow = function(_, path) record(path) end,
            on_field = function() return true end,
            enter_warp = function() record("emerald-warp"); return true end,
        }
        SP = {
            return_to_grass_origin = function() end,
            warp_to = function() end,
            verify_destination = function() end,
            DEST = {},
        }
        em_center_walk = function(path) record(path) end
    ''')
    source = DRIVER.read_text(encoding="utf-8")
    # This driver boots BizHawk at top level. Execute its unchanged walking slice
    # with normal-input/reader stubs, and load the entire real scenario below.
    start = source.index("local function heal_party_at_nurse(label)")
    end = source.index("function ctx.walk_pc_to_grass(label)", start)
    lua.execute(source[start:end])
    return lua


def events(lua):
    return list(lua.globals().events.values())


def test_whiteout_prelude_heals_before_deposit():
    lua = world()
    run = lua.execute(SCENARIO.read_text(encoding="utf-8"))
    ok, why = run(lua.globals().ctx)
    assert not ok and "test-stop-after-deposit" in why
    trace = events(lua)
    assert trace.index("pokecenter_entrance_to_nurse") < trace.index("A")
    assert trace.count("A") == 3
    assert trace.index("HEAL_PARTY whiteout a via=nurse") < trace.index("B")
    assert trace.index("B") < trace.index("center_heal_spot_to_pc") < trace.index("deposit")
    assert "pokecenter_entrance_to_pc" not in trace


@pytest.mark.parametrize("label", [
    "borrowed school", "boxsync a", "center_controls a",
    "linked_faint_active whiteout", "nature", "release",
])
def test_walk_without_the_heal_option_keeps_original_route(label):
    lua = world()
    lua.globals().ctx.walk_to_pc(label)
    assert events(lua) == ["route1_grass_to_north_edge", "route1_edge_to_pokecenter_door",
                           "pokecenter_entrance_to_pc", "Up"]


def test_emerald_whiteout_keeps_original_route():
    lua = world(emerald=True)
    lua.globals().ctx.walk_to_pc("whiteout a", lua.table(heal_before_deposit=True))
    assert events(lua) == ["emerald-warp", "em_oldale_center_to_pc", "Up"]


@pytest.mark.parametrize("fault", ["no_heal", "unreadable", "locked"])
def test_failed_heal_or_dialogue_cannot_reach_pc(fault):
    lua = world(fault=fault)
    with pytest.raises(lua54.LuaError, match="nurse"):
        lua.globals().ctx.walk_to_pc("whiteout a", lua.table(heal_before_deposit=True))
    assert "center_heal_spot_to_pc" not in events(lua)


def test_already_full_party_does_not_claim_a_native_heal():
    lua = world(full=True)
    lua.globals().ctx.walk_to_pc("whiteout a", lua.table(heal_before_deposit=True))
    assert "HEAL_PARTY whiteout a via=already-full" in events(lua)
    assert "A" not in events(lua)


DEPOSITORS = {"scenario_gen3_whiteout": "whiteout a", "scenario_gen3_boxsync": "boxsync a",
              "scenario_gen3_release": "release",
              "scenario_gen3_linked_faint_active": "linked_faint_active whiteout"}
NON_DEPOSITORS = {"scenario_gen3_borrowed": "borrowed school", "scenario_gen3_center_controls": "center_controls a",
                  "scenario_gen3_nature": "nature"}


@pytest.mark.parametrize("module, label", sorted(DEPOSITORS.items()))
def test_every_walk_that_deposits_next_asks_for_the_nurse(module, label):
    text = (DRIVER.parent / f"{module}.lua").read_text(encoding="utf-8")
    assert f'ctx.walk_to_pc("{label}", {{heal_before_deposit = true}})' in text


@pytest.mark.parametrize("module, label", sorted(NON_DEPOSITORS.items()))
def test_walks_that_do_not_deposit_keep_the_plain_route(module, label):
    text = (DRIVER.parent / f"{module}.lua").read_text(encoding="utf-8")
    assert f'ctx.walk_to_pc("{label}")' in text and "heal_before_deposit" not in text


def test_the_label_alone_no_longer_opts_into_a_heal():
    lua = world()
    lua.globals().ctx.walk_to_pc("whiteout a")
    assert "pokecenter_entrance_to_pc" in events(lua)
    assert not any("HEAL_PARTY" in e for e in events(lua))
