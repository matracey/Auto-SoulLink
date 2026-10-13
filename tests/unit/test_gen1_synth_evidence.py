"""SYNTH disclosure must be observed in the cartridge, including RNG-failure paths."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import e2e_duo as duo, gen1_synth_fixtures as synth

ROWS = ("explode_new", "linked_faint_active_new", "explode_bench_battle_new")
REAL_RUN = duo.DuoRun


def _model(monkeypatch, tmp_path, scenario="explode_new", *, balls=20, kind="normal", attempt=1, game="gen1_new"):
    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(duo, "BUILD", str(tmp_path / "build"))
    monkeypatch.setattr(synth, "qualify", lambda *_args: [])
    rom = tmp_path / "companion.gb"
    rom.write_bytes(b"companion")
    args = SimpleNamespace(game=game, lane=None, idle_jitter=0, keep_alive=False)
    run = REAL_RUN(scenario, args, attempt=attempt)
    run.cfg = dict(run.cfg, gen1_synth="explode")  # isolate evidence checks from row-wiring tests
    run._rom_for = lambda _inst: str(rom)
    results = {
        "a": f"[hunt] encounter 1 at (10,35) mode=catch balls={balls}\n"
             "RESULT: FAIL (link_new prerequisite failed: hunt ended out-of-balls)",
        "b": "",
    }
    run.start_server = lambda: None
    run.start_instances = lambda: [run._seed_instance_save(inst) for inst in ("a", "b")]
    run.wait_results = lambda: (results["a"], results["b"])
    run._read_receipt = lambda inst: results[inst]
    run.cleanup = lambda _passed: None

    def orchestrate():
        if kind == "early":
            raise duo.ClientFinishedEarly({"a": results["a"], "b": ""}, "partner catch")
        if kind == "rng":
            raise duo.GameRngMiss("hunt failed")

    run.orchestrate = orchestrate
    return run, results, args


@pytest.mark.parametrize("scenario", ROWS)
@pytest.mark.parametrize("kind", ["normal", "early", "rng"])
def test_wrong_synth_count_fails_before_any_rng_decision(monkeypatch, tmp_path, scenario, kind):
    run, _results, _args = _model(monkeypatch, tmp_path, scenario, balls=1, kind=kind)
    with pytest.raises(RuntimeError, match="SYNTH"):
        run.run()
    assert "PYDEC: FAIL" in Path(run._pydec_path).read_text()


@pytest.mark.parametrize("kind", ["normal", "early", "rng"])
def test_wrong_synth_setup_is_never_retried(monkeypatch, tmp_path, kind):
    built = []
    last = {}

    def factory(name, args, attempt):
        built.append(attempt)
        run, results, _args = _model(monkeypatch, tmp_path, name, balls=1, kind=kind, attempt=attempt)
        last.update(results)
        return run

    monkeypatch.setattr(duo, "DuoRun", factory)
    monkeypatch.setattr(duo, "read_result", lambda _name, inst: last.get(inst, ""))
    monkeypatch.setattr(duo, "_archive_attempt", lambda *_args, **_kwargs: None)
    args = SimpleNamespace(game="gen1_new", lane=None, idle_jitter=0, keep_alive=False)
    verdict = duo.run_scenario_with_rng_retry("explode_new", args)
    assert verdict[0] is False and verdict[1] == 1 and "SYNTH" in verdict[2]
    assert built == [1]


@pytest.mark.parametrize("fault", ["missing_hunt", "missing_disclosure", "duplicate", "wrong_declared",
                                  "wrong_hash", "wrong_attempt"])
def test_synth_oracle_requires_current_unambiguous_disclosure_and_observation(monkeypatch, tmp_path, fault):
    run, results, _args = _model(monkeypatch, tmp_path)
    run.start_instances()
    results.update(dict.fromkeys(("a", "b"), "[hunt] encounter 1 at (10,35) mode=catch balls=20\nRESULT: PASS"))
    path = Path(run._pydec_path)
    rows = [json.loads(line.removeprefix("GEN1_SYNTH_SETUP ")) for line in path.read_text().splitlines()]
    if fault == "missing_hunt":
        results["b"] = "RESULT: PASS"
    elif fault == "missing_disclosure":
        rows.pop()
    elif fault == "duplicate":
        rows.append(dict(rows[0]))
    elif fault == "wrong_declared":
        rows[0]["balls_after"] = 1
        results["a"] = results["a"].replace("balls=20", "balls=1")
    elif fault == "wrong_hash":
        rows[0]["fixture_sha256"] = "0" * 64
    else:
        rows[0]["attempt"] = 999
    path.write_text("".join("GEN1_SYNTH_SETUP " + json.dumps(row) + "\n" for row in rows))
    with pytest.raises(RuntimeError, match="SYNTH"):
        run.assert_gen1_synth_setup(results)


@pytest.mark.parametrize("game", ["gen1_new", "gen1_pure", "gen1_pure_green"])
def test_only_initial_catch_count_is_bound_later_hunts_can_spend_balls(monkeypatch, tmp_path, game):
    run, results, _args = _model(monkeypatch, tmp_path, game=game)
    run.start_instances()
    results.update(dict.fromkeys(("a", "b"), "[hunt] encounter 1 at (10,35) mode=catch balls=20\n" "[hunt] encounter 2 at (10,34) mode=catch balls=19\n" "[hunt] encounter 1 at (10,35) mode=switch-hold balls=18\nRESULT: PASS"))
    run.assert_gen1_synth_setup(results)
    assert Path(run._pydec_path).read_text().count("GEN1_SYNTH_OBSERVED ") == 2


def test_a_verified_rng_cause_can_finish_before_the_partner_starts_hunting(monkeypatch, tmp_path):
    run, _results, _args = _model(monkeypatch, tmp_path, kind="early")
    assert run.run() is False  # an unfinished partner cannot qualify, but is not a bogus setup
