"""Gen 5 powers match BW, not subsequent move rebalances."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from server.adapters.gen5_bw import Gen5Adapter
from server.data.moves import gen5, move_data


@pytest.fixture(scope="module")
def bw_moves():
    bun = shutil.which("bun")
    if not bun:
        pytest.skip("Bun is required to read the calculator's BW move table")
    result = subprocess.run(
        [bun, "-e", 'import {MOVES} from "./calc/calc/src/data/moves.ts"; '
         "console.log(JSON.stringify(MOVES[5]));"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return {re.sub("[^a-z0-9]", "", name.lower()): row
            for name, row in json.loads(result.stdout).items()}


@pytest.mark.parametrize("move_id", range(355, 560))
def test_native_power_matches_gen5(move_id, bw_moves):
    name = re.sub("[^a-z0-9]", "", gen5.MOVE_NAMES[move_id].lower())
    expected = bw_moves[name]["bp"]
    assert move_data(move_id, generation=5)["power"] == expected
    assert Gen5Adapter().move_data(move_id)["power"] == expected


@pytest.mark.parametrize("move_id,power", [
    (396, 90), (412, 80), (434, 140), (485, 70),
    (510, 30), (518, 50), (524, 40), (542, 120), (546, 85),
])
def test_historical_power_anchors_without_calculator_runtime(move_id, power):
    assert move_data(move_id, generation=5)["power"] == power
