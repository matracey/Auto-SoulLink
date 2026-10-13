"""Two-instance Gen 1 E2E for the NEW client (lua/gen1/*): patched Red as player A, patched Blue
as player B (the SLink companion is required; a clean Red/Blue is refused).

    SLINK_E2E=1 pytest tests/e2e/test_duo_gen1_new.py -q

docs/gen1_requirements.md D-1 (encounter link) and D-3 (dead zone) from real play: a
throwaway server plus two EmuHawk instances running the production client built through
lua/gen1/entry.lua, walking Route 1's grass and throwing the fixture's single Poke Ball. The
runner (tools/e2e_duo.py, game gen1_new) reads the verdict off the SERVER, never off the client.

Skipped, never hung, when a prerequisite is missing: no EmuHawk, no cartridge dumps (they
are gitignored), or the battle fixtures not built. A missing COMPANION build next to present
dumps FAILS (`companion_problems`): the companion is the cartridge, not an optional extra.
"""
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

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("SLINK_E2E") != "1",
                       reason="two-instance E2E only runs with SLINK_E2E=1 (spawns EmuHawk twice)"),
]

GAME = "gen1_new"
def deadline_for(scenario):
    """How long one scenario may take: every attempt it may run, plus boot and teardown.

    species_clause_new runs up to three whole attempts (scenario_attempt_limit), so a deadline
    of one timeout would kill the subprocess in the middle of the third — the run would look
    like a crash rather than a scenario that took its budget.
    """
    return (RUNNER_SCENARIOS[scenario]["timeout"]
            * scenario_attempt_limit(scenario, GAME)) + 300


def required_fixtures(scenario):
    """The (title, target) fixture pairs this scenario boots, one per instance.

    `target` may be per instance (poison_new: A town, B battle), and each instance boots its own
    ROM (A red, B blue), so the pairs are per (instance, ROM) — checking the cross product would
    demand red_battle and blue_town for a scenario that never reads them.
    """
    targets = RUNNER_SCENARIOS[scenario].get("target", "town")
    fixtures = RUNNER_GAMES[GAME]["fixture"]
    if isinstance(targets, dict):
        return [(fixtures[inst], targets[inst]) for inst in ("a", "b")]
    return [(fixtures[inst], targets) for inst in ("a", "b")]


def missing_fixtures(scenario, exists=os.path.exists):
    """The pairs whose committed SaveRAM is absent; empty means the scenario can run."""
    return [(title, target) for title, target in required_fixtures(scenario)
            if not exists(os.path.join(play.FIXTURES, f"{title}_{target}.SaveRAM"))]


def companion_problems(game):
    """Why each instance's companion cartridge cannot boot; empty means both can.

    The companion is REQUIRED for Red/Blue/pureRGB (owner 2026-10-02): the launcher and server
    refuse the clean cartridge, so a row whose companion artifact is missing has no cartridge to
    run at all. That is a FAILURE, never a skip -- the release lane would otherwise count a
    machine without the builds as having nothing to prove. Resolved exactly as the runner
    launches it (`DuoRun._rom_for`): the vanilla build's literal path, or the overlay staged and
    sha1-verified by g1.staged_rom.
    """
    from run_gb_gate import PATCHED

    problems = []
    for inst, key in RUNNER_GAMES[game]["patched_saves"].items():
        rom_rel = PATCHED[key][1]
        try:
            if rom_rel is None:
                play.staged_rom(key)
            elif not os.path.exists(os.path.join(REPO, rom_rel)):
                raise FileNotFoundError(f"{rom_rel} not built -- `python patch/gen1/tools/build.py`")
        except Exception as exc:  # noqa: BLE001 - every reason is reported, and every one fails
            problems.append(f"{inst} companion {key}: {exc}")
    return problems


SCENARIOS = ("link_new", "deadzone_new", "linked_faint_bench_new",
             "linked_faint_active_new", "trade_new", "reconnect_new", "ball_gate_new",
             "admit_randomized_new", "soft_reset_new", "trade_decline_new", "explode_new",
             "pc_ops_new", "changebox_new", "whiteout_new", "type_clause_new",
             "species_clause_new", "poison_new", "rival_swap_new",
             "linked_faint_bench_battle_new", "explode_bench_battle_new")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_gen1_new_duo(scenario):
    admission = scenario == "admit_randomized_new"
    if not os.path.exists(play.EMUHAWK):
        if admission:
            pytest.fail(f"EmuHawk missing for admission gate: {play.EMUHAWK}")
        pytest.skip(f"EmuHawk not found at {play.EMUHAWK}")
    # The instances boot the companion builds (checked below), never the clean dumps -- so a
    # missing clean dump is no reason to skip. Only the admission gate reads them: its randomized
    # companion pair is provisioned FROM the clean sources (e2e_duo.prepare_admit_randomized_new).
    if admission:
        for inst, rel in RUNNER_GAMES[GAME]["rom"].items():
            if not os.path.exists(os.path.join(REPO, rel)):
                pytest.fail(f"clean {inst} source ROM missing for admission gate: {rel}")
    problems = companion_problems(GAME)
    if problems:
        pytest.fail("; ".join(problems))
    if not RUNNER_SCENARIOS[scenario].get("cold_boot"):
        missing = missing_fixtures(scenario)
        if missing:
            title, target = missing[0]
            fixture = os.path.join(play.FIXTURES, f"{title}_{target}.SaveRAM")
            if admission:
                pytest.fail(f"town fixture missing for admission gate: {fixture}")
            pytest.skip(f"missing fixture — build with `python tools/gen1_fixtures.py "
                        f"{title} {target}`")

    cmd = [sys.executable, os.path.join(REPO, "tools", "e2e_duo.py"),
           "--game", GAME, "--scenario", scenario]
    if scenario == "reconnect_new":
        # C-1 needs a second-OT Red save. The lane sets no environment, so the committed fixture
        # is the default and SLINK_WRONG_SAVE only overrides it; if the fixture is missing the
        # runner refuses it and the scenario fails, which is what a missing leg must do.
        cmd.extend(("--wrong-save", os.environ.get("SLINK_WRONG_SAVE") or os.path.join(
            REPO, "tests", "fixtures", "gen1", "red_town_ot2.SaveRAM")))
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
            # Always outlive every attempt the runner may take, plus boot and teardown.
            timeout=deadline_for(scenario))
    except subprocess.TimeoutExpired as exc:
        output_path = save_failure_output(
            exc.stdout, exc.stderr, repo=REPO, label=f"{GAME}-{scenario}")
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        pytest.fail(f"{GAME} duo {scenario} exceeded the wrapper deadline of "
                    f"{deadline_for(scenario)} s (every attempt's budget plus 300 s teardown); "
                    f"the runner's own _run_deadline should have ended it first; "
                    f"full output: {output_path} -- partial stdout:\n{stdout[-4000:]}"
                    f"\npartial stderr:\n{stderr[-1000:]}")
    if proc.returncode != 0:
        output_path = save_failure_output(
            proc.stdout, proc.stderr, repo=REPO, label=f"{GAME}-{scenario}")
        raise AssertionError(
            f"{GAME} duo {scenario} failed; full output: {output_path}:\n"
            f"{proc.stdout[-4000:]}\n{proc.stderr[-1000:]}")
