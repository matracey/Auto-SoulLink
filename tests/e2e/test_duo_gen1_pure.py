"""Two-instance E2E: the pureRGB pairings through the real server (PLAN §13 P3b/P4/P5).

The same twenty `gen1_new` scenarios as test_duo_gen1_new.py, on the pure foundation, every one
on the companion OVERLAY (patch/dist/SLink-Pure*.ups applied to the pinned clean build; the clean
pure cartridges are refused, owner 2026-10-02):

* ``gen1_pure``          PureRed (A) vs PureBlue (B).
* ``gen1_pure_green``    PureRed vs PureGreen, the third title on the B side.

Selection follows the runner's own GAMES rows. A missing companion artifact FAILS
(test_duo_gen1_new.companion_problems); a skip here is a missing fixture or emulator, and the
release lane counts every skip as a failure.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tools"))

import gen1_playthrough as play  # noqa: E402
from e2e_duo import (  # noqa: E402
    GAMES as RUNNER_GAMES,
    SCENARIOS as RUNNER_SCENARIOS,
    scenario_attempt_limit,
)
from release_lanes import save_failure_output  # noqa: E402
from test_duo_gen1_new import SCENARIOS, companion_problems  # noqa: E402  (the canonical twenty)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("SLINK_E2E") != "1",
                       reason="two-instance E2E only runs with SLINK_E2E=1 (spawns EmuHawk twice)"),
]

PURE_GAMES = ("gen1_pure", "gen1_pure_green")


def pure_cases():
    """(game, scenario) for every scenario on every pure row: no row defers one any more."""
    return [(game, scenario) for game in PURE_GAMES for scenario in SCENARIOS]


def deadline_for(game, scenario):
    return (RUNNER_SCENARIOS[scenario]["timeout"]
            * scenario_attempt_limit(scenario, RUNNER_GAMES[game]["game"])) + 300


def required_fixtures(game, scenario):
    targets = RUNNER_SCENARIOS[scenario].get("target", "town")
    fixtures = RUNNER_GAMES[game]["fixture"]
    if isinstance(targets, dict):
        return [(fixtures[inst], targets[inst]) for inst in ("a", "b")]
    return [(fixtures[inst], targets) for inst in ("a", "b")]


@pytest.mark.parametrize("game,scenario", pure_cases())
def test_gen1_pure_duo(game, scenario):
    if not os.path.exists(play.EMUHAWK):
        pytest.skip(f"EmuHawk not found at {play.EMUHAWK}")
    problems = companion_problems(game)
    if problems:
        pytest.fail("; ".join(problems))
    for title, target in required_fixtures(game, scenario):
        fixture = os.path.join(play.FIXTURES, f"{title}_{target}.SaveRAM")
        if not os.path.exists(fixture):
            pytest.skip(f"missing fixture {fixture} — `python tools/gen1_fixtures.py {title} {target}`")
    cmd = [sys.executable, os.path.join(REPO, "tools", "e2e_duo.py"),
           "--game", game, "--scenario", scenario, "--lane", f"P_{game}"]
    if scenario == "reconnect_new":
        # C-1 on the pure pairing: the second-OT PureRed save (tests/fixtures/gen1).
        cmd.extend(("--wrong-save", os.environ.get("SLINK_PURE_WRONG_SAVE") or os.path.join(
            REPO, "tests", "fixtures", "gen1", "purered_town_ot2.SaveRAM")))
    try:
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=deadline_for(game, scenario))
    except subprocess.TimeoutExpired as exc:
        output_path = save_failure_output(
            exc.stdout, exc.stderr, repo=REPO, label=f"{game}-{scenario}")
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        pytest.fail(f"{game} duo {scenario} exceeded the wrapper deadline of "
                    f"{deadline_for(game, scenario)} s; full output: {output_path} "
                    f"-- partial stdout:\n{stdout[-4000:]}\npartial stderr:\n{stderr[-1000:]}")
    if proc.returncode != 0:
        output_path = save_failure_output(
            proc.stdout, proc.stderr, repo=REPO, label=f"{game}-{scenario}")
        raise AssertionError(
            f"{game} duo {scenario} failed; full output: {output_path}:\n"
            f"{proc.stdout[-4000:]}\n{proc.stderr[-1000:]}")
