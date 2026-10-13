"""Launch refusals end before gameplay jitter; the refusal oracle still comes first."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import e2e_duo as duo  # noqa: E402

REFUSAL = "this radical_red cartridge needs the SLink companion patch; prepare it through the Manager or /patcher"
# The driver's six-line launch-refusal result, including its pre-launch headers.
REFUSED_B = (
    "duo instance b scenario=clean_rr_refused_gen3 phase=initial title=radical_red attempt=1\n"
    "speedmode requested=300 call=ok\n"
    f"[client] [SLink-gen3] refused: {REFUSAL}\n"
    f"REFUSED_AT_LAUNCH [SLink-gen3] refused: {REFUSAL}\n"
    "WRITES 0\n"
    "RESULT: PASS (refused at launch: the cartridge needs the SLink companion patch; no client was built)\n"
)
PARTNER_A = "TX hello - {}\nPROBE_SETTLED writes=0 rx=0\nWRITES 0\nRESULT: PASS\n"
JITTER = "JITTER requested=0 applied=0 attempt=1\n"


@pytest.fixture
def runner():
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario = "clean_rr_refused_gen3"
    run.cfg = dict(duo.SCENARIOS[run.scenario])
    run.game = "gen3_rr"
    run.gcfg = dict(duo.GAMES[run.game])    # play=gen3_fixtures, so is_gen3_battery holds
    run.args = SimpleNamespace(keep_alive=False)
    run.attempt = 1
    run.notes = []
    run._pydec_note = run.notes.append
    run._gen3_identity = lambda: "test cut"
    run.start_server = lambda: None
    run.start_instances = lambda: None
    run.orchestrate = lambda: None
    run.cleanup = lambda passed: None
    run.expected_idle_jitter = lambda: 0
    run._status = lambda: {"players": {"a": {"connected": True}}}
    run._links_json = lambda: []
    run.receipts = {"a": PARTNER_A + JITTER, "b": REFUSED_B}
    run.wait_results = lambda: (run.receipts["a"], run.receipts["b"])
    return run


def test_expected_launch_refusal_passes_without_gameplay_jitter(runner):
    assert len(REFUSED_B.splitlines()) == 6
    assert runner.run() is True
    assert any("REFUSAL PROOF" in note for note in runner.notes)


def test_non_refused_partner_still_requires_jitter(runner):
    runner.receipts["a"] = PARTNER_A
    with pytest.raises(RuntimeError, match="a: JITTER marker missing"):
        runner.run()


def test_unexpected_refusal_is_not_exempt(runner):
    # Keep the real refusal oracle, but remove the scenario's expectation and
    # select companion carts so preflight permits reaching the result checks.
    runner.cfg.pop("expect_refused")
    runner.cfg["rom_kind"] = "companion"
    with pytest.raises(RuntimeError, match="b: JITTER marker missing"):
        runner.run()


@pytest.mark.parametrize("bad_b, problem", [
    (REFUSED_B.replace("WRITES 0", "WRITES 1"), "WRITES 0"),
    (REFUSED_B.replace("REFUSED_AT_LAUNCH", "NO_REFUSAL"), "REFUSED_AT_LAUNCH"),
    (REFUSED_B + "TX hello - {}\n", "forbidden"),
])
def test_refusal_oracle_must_validate_before_jitter_exemption(runner, bad_b, problem):
    runner.receipts["b"] = bad_b
    with pytest.raises(RuntimeError, match=problem):
        runner.run()


@pytest.mark.parametrize("text", [
    REFUSED_B.replace("RESULT: PASS", "RESULT: FAIL"),
    REFUSED_B.replace("REFUSED_AT_LAUNCH", "NO_REFUSAL"),
    PARTNER_A,
])
def test_jitter_helper_requires_pass_and_launch_refusal_evidence(text):
    assert duo.jitter_problems(text, 0, validated_launch_refusal=True) == ["JITTER marker missing"]


def test_refusal_text_alone_does_not_exempt_jitter():
    assert duo.jitter_problems(REFUSED_B, 0) == ["JITTER marker missing"]


def test_partner_jitter_must_still_match_requested_count(runner):
    runner.receipts["a"] = PARTNER_A + JITTER.replace("applied=0", "applied=37")
    with pytest.raises(RuntimeError, match="a: JITTER applied != requested"):
        runner.run()
