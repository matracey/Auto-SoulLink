"""A Gen 2 opening-catch scenario gets ONE retry on the next pinned clock minute when its wild battle was lost.

On the pinned DUO-CLOCK a duo route is bit-for-bit deterministic, so re-running the same attempt reproduces a lost battle
(gen2_new/gen2_trade_refuse_item lost it at the same frame in two sweeps). Everything else still fails on the first attempt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import e2e_duo as duo  # noqa: E402

LOST = "link route failed: the battle ended without a catch"


def _driver(monkeypatch, tmp_path, outcomes, name="gen2_trade_refuse_item", game="gen2_new"):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    built, active = [], {"attempt": 0}

    class FakeRun:
        def __init__(self, scenario, args, attempt):
            assert scenario == name
            built.append(attempt)
            active["attempt"] = attempt

        def run(self):
            return outcomes[active["attempt"] - 1] is None

    def fake_result(_scenario, inst):
        reason = outcomes[active["attempt"] - 1]
        if isinstance(reason, dict):
            return reason.get(inst, "")
        if reason is None:
            return "RESULT: PASS (ok)" if inst == "a" else "RESULT: PASS (b ok)"
        return f"RESULT: FAIL ({reason})" if inst == "a" else ""

    monkeypatch.setattr(duo, "DuoRun", FakeRun)
    monkeypatch.setattr(duo, "read_result", fake_result)
    (tmp_path / f"e2e_{name}_pydec_result.txt").write_text(
        f"attempt 1 of {duo.scenario_attempt_limit(name, game)}\n", encoding="utf-8")
    return built, type("Args", (), {"game": game, "idle_jitter": 0})()


def test_the_limit_is_two_for_every_gen2_trade_scenario_and_unchanged_elsewhere():
    for name in duo.GEN2_TRADE_SCENARIOS:
        assert duo.scenario_attempt_limit(name, "gen2_new") == 2, name
    assert duo.scenario_attempt_limit("gen2_ball_gate", "gen2_new") == 2


def test_a_lost_route_battle_retries_once_and_a_pass_stands(capsys, tmp_path, monkeypatch):
    built, args = _driver(monkeypatch, tmp_path, [LOST, None])
    assert duo.run_scenario_with_rng_retry("gen2_trade_refuse_item", args) == (True, 2)
    assert built == [1, 2]
    assert "the route's wild battle was lost on attempt 1; retrying fresh lane" in capsys.readouterr().out


def test_a_second_lost_battle_fails_after_the_one_retry(tmp_path, monkeypatch):
    built, args = _driver(monkeypatch, tmp_path, [LOST, LOST])
    assert duo.run_scenario_with_rng_retry("gen2_trade_refuse_item", args) == (False, 2)
    assert built == [1, 2]


@pytest.mark.parametrize("reason", ["trade oracle: unexpected state", "no Poke Ball left in the pocket",
                                    "link route failed: something else"])
def test_any_other_failure_is_final_on_the_first_attempt(tmp_path, monkeypatch, reason):
    built, args = _driver(monkeypatch, tmp_path, [reason, None])
    assert duo.run_scenario_with_rng_retry("gen2_trade_refuse_item", args) == (False, 1)
    assert built == [1]


def test_the_retry_attempt_is_pinned_to_a_different_clock_minute():
    assert duo._duo_clock_minute(1) == 0 and duo._duo_clock_minute(2) == duo.DUO_CLOCK_RETRY_MINUTE != 0


OPENING_CATCH_SCENARIOS = (
    "gen2_faint", "gen2_whiteout", "gen2_pc_ops", "gen2_changebox", "gen2_poison", "gen2_whiteout_rebuild",
)


@pytest.mark.parametrize("name", OPENING_CATCH_SCENARIOS)
@pytest.mark.parametrize("game", ["gen2_new", "gen2_gold_silver", "gen2_crystal_gold"])
def test_opening_catch_loss_retries_once_then_accepts_a_pass(tmp_path, monkeypatch, name, game):
    built, args = _driver(monkeypatch, tmp_path, [LOST, None], name=name, game=game)
    assert duo.scenario_attempt_limit(name, game) == 2
    assert duo.run_scenario_with_rng_retry(name, args) == (True, 2)
    assert built == [1, 2]


@pytest.mark.parametrize("name", OPENING_CATCH_SCENARIOS)
def test_second_opening_catch_loss_exhausts_the_budget(tmp_path, monkeypatch, name):
    built, args = _driver(monkeypatch, tmp_path, [LOST, LOST], name=name)
    assert duo.run_scenario_with_rng_retry(name, args) == (False, 2)
    assert built == [1, 2]


@pytest.mark.parametrize("reason", ["wave-c: the box change was not reported", "link route failed: something else"])
def test_changebox_non_route_failure_does_not_retry(tmp_path, monkeypatch, reason):
    built, args = _driver(monkeypatch, tmp_path, [reason, None], name="gen2_changebox")
    assert duo.run_scenario_with_rng_retry("gen2_changebox", args) == (False, 1)
    assert built == [1]


@pytest.mark.parametrize("name", sorted(duo.GEN2_ROUTE_LOSS_SCENARIOS) + ["gen2_trade_refuse_item", "gen2_ball_gate"])
@pytest.mark.parametrize("rng_side", ["a", "b"])
def test_unrelated_failure_vetoes_catch_retry(tmp_path, monkeypatch, name, rng_side):
    reason = duo.GEN2_OUT_OF_BALLS if name == "gen2_ball_gate" else LOST
    other = "b" if rng_side == "a" else "a"
    receipts = {rng_side: f"RESULT: FAIL ({reason})", other: "RESULT: FAIL (binding identity refused)"}
    built, args = _driver(monkeypatch, tmp_path, [receipts, None], name=name)
    assert duo.run_scenario_with_rng_retry(name, args) == (False, 1)
    assert built == [1]


@pytest.mark.parametrize("name", ["gen2_changebox", "gen2_ball_gate"])
def test_only_recognized_failures_on_both_sides_retry(tmp_path, monkeypatch, name):
    reason = duo.GEN2_OUT_OF_BALLS if name == "gen2_ball_gate" else LOST
    receipts = dict.fromkeys(("a", "b"), f"RESULT: FAIL ({reason})")
    built, args = _driver(monkeypatch, tmp_path, [receipts, None], name=name)
    assert duo.run_scenario_with_rng_retry(name, args) == (True, 2)
    assert built == [1, 2]
