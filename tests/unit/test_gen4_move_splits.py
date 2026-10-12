"""Inherited move categories agree with the calculator's Gen 4 data."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from server.adapters.gen4_hgsspt import Gen4Adapter
from server.data.moves import gen3_vanilla, move_data

ROOT = Path(__file__).resolve().parents[2]
ALIASES = {
    "vicegrip": "visegrip",
    "faintattack": "feintattack",
    "hijumpkick": "highjumpkick",
    "smellingsalt": "smellingsalts",
}


def canonical(name):
    return re.sub("[^a-z0-9]", "", name.lower())


@pytest.fixture(scope="module")
def dpp_moves():
    bun = shutil.which("bun")
    if not bun:
        pytest.skip("Bun is required to read the calculator's Gen 4 table")
    result = subprocess.run(
        [bun, "-e", 'import {MOVES} from "./calc/calc/src/data/moves.ts"; '
         "console.log(JSON.stringify(MOVES[4]));"],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=30,
    )
    return {canonical(name): row for name, row in json.loads(result.stdout).items()}


@pytest.mark.parametrize("move_id", range(1, 355))
def test_inherited_move_uses_its_gen4_category(move_id, dpp_moves):
    name = canonical(gen3_vanilla.MOVE_NAMES[move_id])
    category = dpp_moves[ALIASES.get(name, name)]["category"]
    expected = {"Physical": 0, "Special": 1, "Status": 2}[category]
    assert move_data(move_id, generation=4)["split"] == expected
    assert Gen4Adapter().move_data(move_id)["split"] == expected


@pytest.mark.parametrize("move_id,expected", [(7, 0), (127, 0), (247, 1), (174, 2)])
def test_category_anchors_do_not_require_calculator_runtime(move_id, expected):
    assert move_data(move_id, generation=4)["split"] == expected


def test_category_overrides_preserve_other_stats_and_gen3():
    assert move_data(7, generation=3)["split"] == 1
    assert move_data(247, generation=3)["split"] == 0
    assert move_data(7, generation=4) is not gen3_vanilla.MOVE_DATA[7]
    for move_id in range(355):
        original = gen3_vanilla.MOVE_DATA[move_id]
        actual = move_data(move_id, generation=4)
        assert {k: v for k, v in actual.items() if k != "split"} == {
            k: v for k, v in original.items() if k != "split"
        }
    assert move_data(9999, generation=4) is None
