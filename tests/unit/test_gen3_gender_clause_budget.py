"""Gender observation gets six attempts on FR/LG/Emerald; RR keeps its eight; neighbors stay pinned."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import e2e_duo as duo  # noqa: E402
import gen3_final_cut as final_cut  # noqa: E402


@pytest.mark.parametrize(
    ("game", "gender_limit", "species_limit", "type_limit"),
    [
        ("gen3_frlg", 6, 8, 3),
        ("gen3_lgfr", 6, 8, 3),
        ("gen3_emerald", 6, 8, 3),
        ("gen3_rr", 8, 8, 8),    # RR already had eight; this change must not lower it
    ],
)
def test_gender_clause_has_six_observation_attempts_only(game, gender_limit, species_limit, type_limit):
    assert duo.scenario_applies("gender_clause_gen3", game)
    assert duo.scenario_attempt_limit("gender_clause_gen3", game) == gender_limit
    assert duo.scenario_attempt_limit("species_clause_gen3", game) == species_limit
    assert duo.scenario_attempt_limit("type_clause_gen3", game) == type_limit
    assert final_cut.duo_budget("gender_clause_gen3", game) == 2400 * gender_limit + 900
    assert final_cut.duo_budget("species_clause_gen3", game) == 20100
    assert final_cut.duo_budget("type_clause_gen3", game) == (20100 if game == "gen3_rr" else 8100)
    assert [duo.jitter_for_attempt(11, attempt) for attempt in range(1, 7)] == [11, 48, 85, 122, 159, 196]
