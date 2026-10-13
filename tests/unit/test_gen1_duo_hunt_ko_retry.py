"""A first-battle KO ends the duo prerequisite instead of hunting a dead zone."""
import re
from pathlib import Path
from types import SimpleNamespace

import lupa
import pytest

from tools import e2e_duo as duo

ROOT = Path(__file__).resolve().parents[2]


def _ko_receipt(*, balls=1, scenario="link_new"):
    """Run the real hunt and link prerequisite over an emulator boundary double."""
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    source = (ROOT / "lua/tests/duo/duo_gen1_main.lua").read_text(encoding="utf-8")
    hunt = re.search(r"(?ms)^local function hunt\(mode, options\).*?^end$", source)
    link = re.search(r"(?ms)^function scenarios.link_new\(\).*?^end$", source)
    assert hunt and link
    lua.globals().HUNT_PATH = (ROOT / "lua/tests/gen1_rb_hunt_inputs.lua").as_posix()
    lua.globals().BALLS_AFTER = balls
    lua.globals().SCENARIO = scenario
    prelude = """
local Hunt = dofile(HUNT_PATH)
local FACTS = dofile(HUNT_PATH:gsub('gen1_rb_hunt_inputs.lua', 'gen1_rb_facts.lua'))
local frame, closed = 1, 0
local D = {player='b', scenario=SCENARIO, hunt_frames=4}
local symbols = {wNumBagItems=1, wBagItems=2, wEnemyMonHP=4,
                 wEnemyMonMaxHP=6, wEnemyMonActualCatchRate=8}
local function rd(addr)
    return ({[1]=1, [2]=4, [3]=frame == 1 and 1 or BALLS_AFTER,
             [4]=0, [5]=8, [6]=0, [7]=13, [8]=255})[addr] or 0
end
local rom = setmetatable({}, {__index=function() return {addr=1} end})
local Driver = {new=function()
    return {wait_menu=function() return {ok=true, frames=1} end,
            choose=function(name) assert(name == 'FIGHT'); return {ok=true} end,
            commit_move=function() return {ok=false, why='battle_over'} end,
            close=function() closed=closed+1 end}
end}
local play = {point=function()
    return {map=FACTS.MAP.ROUTE_1, x=10, y=35, battle=frame == 1 and 1 or 0,
            battle_type=0, battle_result=0, party_hp=19, party_count=1,
            joy_ignore=0, font_loaded=false}
end}
local emu = {framecount=function() return frame end}
local function yield_frame() frame=frame+1 end
local function yield_buttons() end
local function log() end
local fmt = string.format
local scenarios = {}
local function wait_go() return true end
"""
    passed, reason, frames, closed = lua.execute(
        prelude + hunt.group() + "\n" + link.group()
        + "\nlocal ok, why = scenarios.link_new(); return ok, why, frame, closed"
    )
    assert passed is False
    assert frames == 2, "must stop before another encounter or a pairing timeout"
    assert closed == 1
    return f"RESULT: FAIL ({reason})\n"


def test_ko_stops_the_real_duo_hunt_before_hunting_again():
    assert _ko_receipt() == "RESULT: FAIL (hunt ended first-catch-battle-lost)\n"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("scenario", ["link_new", "explode_new", "linked_faint_bench_battle_new"])
def test_ko_has_exactly_the_out_of_balls_retry_budget(scenario, nested):
    ko = _ko_receipt(scenario=scenario)
    if nested:
        ko = ko.replace("FAIL (", "FAIL (link_new prerequisite failed: ")
    for attempt in range(1, 9):
        for limit in (3, 4, 8):
            assert duo.retryable_gen1_rng("gen1_pure_green", {"a": None, "b": ko},
                                         attempt, limit, scenario=scenario) == duo.retryable_gen1_rng(
                "gen1_pure_green", {"a": None, "b": duo.RNG_OUT_OF_BALLS},
                attempt, limit, scenario=scenario)


def test_out_of_balls_still_wins_over_uncaught_battle():
    assert _ko_receipt(balls=0) == duo.RNG_OUT_OF_BALLS + "\n"


def test_ko_never_retries_an_unrelated_partner_failure():
    assert not duo.retryable_gen1_rng(
        "gen1_pure_green", {"a": "RESULT: FAIL (binding identity refused)", "b": _ko_receipt()},
        1, 3, scenario="link_new")


@pytest.mark.parametrize("game", ["gen2_new", "gen3_frlg", "gen3_rr", "gen3_emerald"])
def test_other_generations_do_not_gain_a_late_ko_retry(game):
    assert not duo.retryable_gen1_rng(
        game, {"a": None, "b": _ko_receipt()}, 3, 4, scenario="explode_new")


@pytest.mark.parametrize("cause", ["ko", "balls", "timeout", "always_ko"])
def test_attempt_consumption_and_jitter(tmp_path, monkeypatch, cause):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    attempts = []
    ko = _ko_receipt()
    original_run = duo.DuoRun

    class Run:
        artifact_name = "link_new"

        def __init__(self, name, args, attempt):
            self.attempt = attempt
            attempts.append((attempt, duo.jitter_for_attempt(args.idle_jitter, attempt)))

        def run(self):
            failed = self.attempt == 1 or cause == "always_ko"
            receipt = ko if cause in ("ko", "always_ko") else duo.RNG_OUT_OF_BALLS + "\n"
            if failed:
                runner = object.__new__(original_run)
                runner.game, runner.cfg, runner.emus = "gen1_pure_green", {"timeout": 1}, []
                runner._caught = lambda side: side + "_key"
                state = "pending" if cause == "timeout" else "dead_zone"
                runner._status = lambda: {"links": [], "area_states": {"route_1": state}}
                runner._read_receipt = lambda side: receipt if side == "b" and cause != "timeout" else ""
                if cause == "timeout":
                    runner.assert_link_new()
                else:
                    before = clock[0]
                    with pytest.raises(duo.ClientFinishedEarly):
                        runner.assert_link_new()
                    assert clock[0] == before
            for side, text in {"a": "", "b": receipt if failed else "RESULT: PASS\n"}.items():
                (tmp_path / f"e2e_link_new_{side}_result.txt").write_text(text, encoding="utf-8")
            return not failed

    # Deterministic clock: the real pairing wait expires without sleeping for 180 s.
    clock = [0]
    monkeypatch.setattr(duo.time, "time", lambda: clock[0])
    monkeypatch.setattr(duo.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + 60))
    monkeypatch.setattr(duo, "DuoRun", Run)
    outcome = duo.run_scenario_with_rng_retry(
        "link_new", SimpleNamespace(game="gen1_pure_green", idle_jitter=0))
    if cause == "timeout":
        assert outcome[:2] == (False, 1)
        assert "TimeoutError" in outcome[2] and "SERVER to pair" in outcome[2]
        assert attempts == [(1, 0)]
    elif cause == "always_ko":
        # Owner 2026-10-13: pureRGB opening catches get six attempts (PURERGB_ACQUISITION_*).
        assert outcome == (False, 6)
        assert attempts == [(1, 0), (2, 37), (3, 74), (4, 111), (5, 148), (6, 185)]
    else:
        assert outcome == (True, 2)
        assert attempts == [(1, 0), (2, 37)]
