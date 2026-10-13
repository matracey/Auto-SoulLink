"""Hunt odds include the encounter's rate without changing fight/throw inputs."""
import json
from pathlib import Path

import lupa
import pytest

ROOT = Path(__file__).resolve().parents[2]
LUA_TESTS = ROOT / "lua/tests"


@pytest.fixture(params=["rb", "pure"])
def hunt(request):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval("dofile")((LUA_TESTS / "gen1_rb_hunt_inputs.lua").as_posix())
    facts = lua.eval("dofile")((LUA_TESTS / f"gen1_{request.param}_facts.lua").as_posix())
    module.with_facts(facts)
    return lua, module, facts


@pytest.mark.parametrize(
    "hp,maxhp,rate,expected",
    [
        (7, 13, 150, 151 / 256),  # Eevee: HP/W is certain, species gate is not.
        (7, 13, 255, 1),  # Pidgey: both gates are certain.
        (8, 13, 150, 20989 / 65536),  # W=138, so 139 of 256 Rand2 values pass.
        (8, 13, 255, 139 / 256),
        (8, 24, 150, 151 / 256),  # W=255 includes every Rand2 value.
        (8, 24, 255, 1),
        (7, 13, 0, 1 / 256),  # A real zero rate is not a missing/default rate.
    ],
)
def test_catch_odds_include_both_random_gates(hunt, hp, maxhp, rate, expected):
    _, module, _ = hunt
    assert module.catch_odds(hp, maxhp, rate) == pytest.approx(expected)


def test_catch_odds_require_an_observed_rate(hunt):
    _, module, _ = hunt
    with pytest.raises(lupa.LuaError, match="catch rate"):
        module.catch_odds(7, 13)


# The actual Lua hunt runs over its public driver/read/step/log seams. Each driver action
# yields one input frame; memory changes describe a caught or missed throw, not hunt internals.
EXERCISE_HUNT = """
function(Hunt, facts, hp, species, rate, ball_count, caught)
    local S = { wEnemyMonHP=0xC100, wEnemyMonMaxHP=0xC102,
                wEnemyMonSpecies=0xC104, wEnemyMonActualCatchRate=0xC105,
                wNumBagItems=0xC110, wBagItems=0xC111 }
    local bus = { [S.wEnemyMonHP]=0, [S.wEnemyMonHP+1]=hp,
                  [S.wEnemyMonMaxHP]=0, [S.wEnemyMonMaxHP+1]=13,
                  [S.wEnemyMonSpecies]=species, [S.wEnemyMonActualCatchRate]=rate,
                  [S.wNumBagItems]=1, [S.wBagItems]=facts.ITEM.POKE_BALL,
                  [S.wBagItems+1]=ball_count }
    local actions, logs = {}, {}
    local battle, party = 1, 1
    local function step(buttons) coroutine.yield(buttons or {}) end
    local driver = {}
    function driver.wait_menu()
        return {ok=battle == 1, why=battle == 1 and "menu" or "battle_over", frames=1}
    end
    function driver.choose(name)
        actions[#actions+1] = name
        step({A=true})
        return {ok=true}
    end
    function driver.commit_move(slot)
        actions[#actions+1] = "move" .. slot
        step({A=true})
        bus[S.wEnemyMonHP+1] = 7
        return {ok=true, why="battle_menu_again"}
    end
    function driver.use_item(index)
        actions[#actions+1] = "throw" .. index
        step({A=true})
        local before = bus[S.wBagItems+1]
        bus[S.wBagItems+1] = before - 1
        if before == 1 then bus[S.wNumBagItems] = 0 end
        if caught then battle, party = 0, 2 end
        return {ok=caught, why=caught and "caught" or "missed",
                balls_before=before, balls_after=before-1}
    end
    function driver.run()
        actions[#actions+1] = "RUN"
        step({A=true})
        battle = 0
        return {ok=true, why="ran"}
    end
    local route = Hunt.new({player="a", facts=facts},
                          {driver=driver, step=step, rd=function(a) return bus[a] or 0 end,
                           symbols=S, log=function(line) logs[#logs+1]=line end})
    local phase
    for frame=1,20 do
        local point = {map=facts.MAP.ROUTE_1, x=10, y=35, battle=battle,
                       battle_type=0, party_count=party, party_hp=20,
                       joy_ignore=0, font_loaded=false}
        local _, next_phase = route.step(nil, nil, point, frame)
        phase = next_phase
        if phase == "caught" or phase == "out-of-balls" then break end
    end
    return {actions=actions, logs=logs, phase=phase}
end
"""


def exercise(hunt, hp, species, rate, balls=1, caught=True):
    lua, module, facts = hunt
    result = lua.eval(EXERCISE_HUNT)(module, facts, hp, species, rate, balls, caught)
    return list(result.actions.values()), list(result.logs.values()), result.phase


@pytest.mark.parametrize("balls", [1, 3])
def test_low_hp_eevee_throws_now_but_logs_species_limited_odds(hunt, balls):
    pack = json.loads((ROOT / "data/games/gen1_purergb/species_index.json").read_text(encoding="utf-8"))
    internal = pack["national_to_index"]["133"]
    rate = pack["species"][str(internal)]["catch_rate"]
    assert rate == 150
    actions, logs, phase = exercise(hunt, 7, internal, rate, balls=balls)
    assert actions == ["throw0"]  # No extra FIGHT and no last-ball RUN.
    assert phase == "caught"
    turn_logs = [line for line in logs if line.startswith("[hunt] turn ")]
    assert len(turn_logs) == 1
    assert "odds 0.59" in turn_logs[0]
    assert turn_logs[0].endswith("-> throw")


def test_pidgey_uses_its_own_observed_rate(hunt):
    pack = json.loads((ROOT / "data/games/gen1_purergb/species_index.json").read_text(encoding="utf-8"))
    internal = pack["national_to_index"]["16"]
    rate = pack["species"][str(internal)]["catch_rate"]
    assert rate == 255
    actions, logs, phase = exercise(hunt, 7, internal, rate)
    assert actions == ["throw0"]
    assert phase == "caught"
    assert any("odds 1.00" in line and line.endswith("-> throw") for line in logs)


def test_uncertain_hp_gate_keeps_the_existing_fight_then_throw_timing(hunt):
    actions, logs, phase = exercise(hunt, 8, 102, 150)
    assert actions == ["FIGHT", "move1", "throw0"]
    assert phase == "caught"
    turn_logs = [line for line in logs if line.startswith("[hunt] turn ")]
    assert len(turn_logs) == 2
    assert "odds 0.32" in turn_logs[0] and turn_logs[0].endswith("-> fight")
    assert "odds 0.59" in turn_logs[1] and turn_logs[1].endswith("-> throw")


def test_missed_last_ball_keeps_the_existing_out_of_balls_retry_result(hunt):
    actions, logs, phase = exercise(hunt, 7, 102, 150, caught=False)
    assert actions == ["throw0", "RUN"]  # RUN only after the thrown ball was consumed.
    assert phase == "out-of-balls"
    assert any("odds 0.59" in line and line.endswith("-> throw") for line in logs)
