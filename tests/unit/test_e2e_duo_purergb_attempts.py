"""Owner 2026-10-13: six attempts for pureRGB's opening-catch scenarios."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import e2e_duo as duo

PURE_GAMES = ("gen1_pure", "gen1_pure_green")
ACQUISITION_SCENARIOS = (
    "link_new", "deadzone_new", "linked_faint_bench_new",
    "linked_faint_bench_battle_new", "reconnect_new", "trade_new",
    "trade_decline_new", "pc_ops_new", "changebox_new",
    "whiteout_new", "type_clause_new", "species_clause_new", "rival_swap_new",
)
# The gen1_synth explode controls keep their own honest budgets (tests/unit/test_gen1_explode_setup.py).
SYNTH_CONTROLS = {"explode_new": 4, "linked_faint_active_new": 3, "explode_bench_battle_new": 3}


@pytest.mark.parametrize("game", PURE_GAMES)
@pytest.mark.parametrize("name", ACQUISITION_SCENARIOS)
def test_purergb_acquisition_budget_and_unchanged_vanilla_twin(game, name):
    vanilla = {"species_clause_new": 8}.get(name, 3)
    assert duo.scenario_attempt_limit(name, game) == max(vanilla, 6)   # never lowers a larger budget
    assert duo.scenario_attempt_limit(name, "gen1_new") == vanilla


@pytest.mark.parametrize("game", PURE_GAMES)
@pytest.mark.parametrize("name, limit", sorted(SYNTH_CONTROLS.items()))
def test_synth_controls_keep_their_own_budgets(game, name, limit):
    assert name not in duo.PURERGB_ACQUISITION_SCENARIOS
    assert duo.scenario_attempt_limit(name, game) == limit


@pytest.mark.parametrize("game", PURE_GAMES)
@pytest.mark.parametrize("name, limit", [
    ("soft_reset_new", 3), ("poison_new", 4),
    ("admit_randomized_new", 3), ("ball_gate_new", 1),
])
def test_non_acquisition_budgets_are_unchanged(game, name, limit):
    assert duo.scenario_attempt_limit(name, game) == limit


def test_scope_covers_exactly_the_registered_purergb_pairings():
    assert {g for g, row in duo.GAMES.items()
            if any(f.startswith("pure") for f in row.get("fixture", {}).values())} == set(PURE_GAMES)
    assert frozenset(PURE_GAMES) == duo.PURERGB_ACQUISITION_GAMES
    assert frozenset(ACQUISITION_SCENARIOS) == duo.PURERGB_ACQUISITION_SCENARIOS


@pytest.mark.parametrize("game", PURE_GAMES)
@pytest.mark.parametrize("name", ACQUISITION_SCENARIOS)
@pytest.mark.parametrize("reason", ["out-of-balls", "first-catch-battle-lost"])
@pytest.mark.parametrize("prefix", ["", "link_new prerequisite failed: "])
def test_only_purergb_acquisition_gets_late_catch_retries(game, name, reason, prefix):
    receipts = {"a": f"RESULT: FAIL ({prefix}hunt ended {reason})", "b": None}
    for attempt in (3, 4, 5):
        assert duo.retryable_gen1_rng(game, receipts, attempt, 6, scenario=name)
    assert not duo.retryable_gen1_rng(game, receipts, 6, 6, scenario=name)
    # Use the same artificial limit to isolate late-phrase scope from budget exhaustion.
    assert not duo.retryable_gen1_rng("gen1_new", receipts, 4, 6, scenario=name)
    assert not duo.retryable_gen1_rng("gen1_new", receipts, 4,
                                     duo.scenario_attempt_limit(name, "gen1_new"), scenario=name)
    assert not duo.retryable_gen1_rng(game, receipts, 4, 6, scenario="soft_reset_new")
    assert not duo.retryable_gen1_rng(game, receipts, 4, 6)


@pytest.mark.parametrize("game", PURE_GAMES)
@pytest.mark.parametrize("attempt", range(1, 7))
@pytest.mark.parametrize("other", [None, "RESULT: FAIL (hunt ended out-of-balls)"])
def test_non_rng_failure_is_final_even_alongside_rng(game, attempt, other):
    receipts = {"a": "RESULT: FAIL (force_faint never arrived)", "b": other}
    assert not duo.retryable_gen1_rng(game, receipts, attempt, 6, scenario="link_new")


@pytest.mark.parametrize("game", PURE_GAMES)
def test_other_rng_causes_do_not_gain_late_retries(game):
    receipts = {"a": "RESULT: FAIL (hunt ended whiteout)", "b": None}
    assert not duo.retryable_gen1_rng(game, receipts, 4, 6, scenario="link_new")


def test_all_six_attempts_keep_advancing_jitter():
    assert [duo.jitter_for_attempt(0, n) for n in range(1, 7)] == [0, 37, 74, 111, 148, 185]
