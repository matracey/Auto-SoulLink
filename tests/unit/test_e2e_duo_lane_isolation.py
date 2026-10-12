"""Lane isolation for `tools/e2e_duo.py`, and the pureRGB pairing row (P3b-e).

Two DuoRuns in ONE process must share no generated stub, no BizHawk config copy, no SaveRAM
directory and no window position — docs/purergb/research/p3/harness_literals_bbcd037.md table 3.
The stub is the worst of them: it bakes SLINK_PORT, so a collision makes the loser's emulator talk
to the winner's server, and the config copy carries that instance's SaveRAM directory and the window
pin. The receipts stay per scenario+instance ON PURPOSE (the admission scenarios write those names
themselves), which is stated here so a future lane id refactor knows it was a decision.

No emulator: `launch_instance` runs with `subprocess.Popen` recorded and BUILD pointed at a tmp dir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import e2e_duo as duo  # noqa: E402
import gen1_playthrough as g1  # noqa: E402


def test_gen2_seeds_distinct_fixtures_under_the_same_name_in_separate_dirs(monkeypatch, tmp_path):
    import tools.gen2_synth_fixtures as gen2_synth_fixtures  # noqa: PLC0415

    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    # DUO-CLOCK pins the boot copy's RTC (_seed_instance_save); this test's fake bytes are not a
    # real SaveRAM and this is not what it is testing, so day_clock is a no-op passthrough here.
    monkeypatch.setattr(gen2_synth_fixtures, "day_clock", lambda raw, **_kw: (raw, {}))
    run = duo.DuoRun("link", _args(game="gen2_new", scenario="link", lane="cc"))
    run._gen2_inputs, run._gen2_plans = {}, {}
    for side, raw in (("a", b"qualified A"), ("b", b"qualified B")):
        fixture = tmp_path / f"{side}.SaveRAM"
        fixture.write_bytes(raw)
        run._gen2_inputs[side] = {"fixture": fixture, "sha256": hashlib.sha256(raw).hexdigest(), "title": side}
        run._gen2_plans[side] = {"directory": Path(run._saveram_dir(side)),
                                 "saveram_name": "same-rom.SaveRAM"}
    a = Path(run._seed_instance_save("a"))
    b = Path(run._seed_instance_save("b"))
    assert a.name == b.name == "same-rom.SaveRAM"
    assert a.parent != b.parent
    assert a.read_bytes() == b"qualified A" and b.read_bytes() == b"qualified B"
    run._gen2_inputs["b"]["fixture"].write_bytes(b"changed after preflight")
    with pytest.raises(RuntimeError, match="changed after preflight"):
        run._seed_instance_save("b")


def test_gen2_launch_uses_cgb_300_percent_and_isolated_process_environment(monkeypatch, tmp_path):
    import run_gb_gate as gate

    import tools.gen2_synth_fixtures as gen2_synth_fixtures

    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    monkeypatch.setattr(duo, "REPO", str(tmp_path))
    monkeypatch.setattr(duo, "WT_FWD", tmp_path.as_posix())
    monkeypatch.setattr(gate, "BIZHAWK_CONFIG", str(_config_file(tmp_path)))
    monkeypatch.setattr(duo.importlib.import_module("gen2_code_digest"), "run_stamp", lambda *_a, **_k: {})
    # DUO-CLOCK pins the boot copy's RTC (_seed_instance_save, called by launch_instance below);
    # this test's fake bytes are not a real SaveRAM and this is not what it is testing, so
    # day_clock is a no-op passthrough here.
    monkeypatch.setattr(gen2_synth_fixtures, "day_clock", lambda raw, **_kw: (raw, {}))
    launched = []
    monkeypatch.setattr(duo.subprocess, "Popen",
                        lambda cmd, **kwargs: launched.append((cmd, kwargs)) or SimpleNamespace())
    run = duo.DuoRun("link", _args(game="gen2_new", scenario="link", lane="cc"))
    run._gen2_inputs, run._gen2_plans, run._gen2_env = {}, {}, {}
    for side in ("a", "b"):
        fixture = tmp_path / f"fixture_{side}.SaveRAM"
        fixture.write_bytes(side.encode())
        run._gen2_inputs[side] = {"fixture": fixture, "sha256": hashlib.sha256(side.encode()).hexdigest(), "title": side}
        run._gen2_plans[side] = {"directory": Path(run._saveram_dir(side)),
            "saveram_name": "same-rom.SaveRAM", "rom": tmp_path / "pinned.gbc", "speed_percent": 300,
            "env": {"SLINK_GEN2_SAVERAM_DIR": run._saveram_dir(side), "SLINK_GEN2_CORE_MODE": "CGB"}}
        run._gen2_env[side] = {"SLINK_GEN2_FIXTURE_CASE": f"qualified-{side}"}
        run.launch_instance(side)
        config = json.loads(Path(run.cfg_path(side)).read_text())
        assert config["SpeedPercent"] == config["SpeedPercentAlternate"] == 300
        assert config["ClockThrottle"] is True and config["Unthrottled"] is False
        assert config["CoreSyncSettings"]["BizHawk.Emulation.Cores.Nintendo.Gameboy.Gameboy"]["ConsoleMode"] == 2
        paths = config["PathEntries"]["Paths"]
        assert all(Path(row["Path"]) == Path(run._saveram_dir(side)) for row in paths)
        assert "mutate_otid = false" in Path(run.stub_path(side)).read_text()
    assert launched[0][0][-1] == launched[1][0][-1] == str(tmp_path / "pinned.gbc")
    for argv, _kwargs in launched:
        assert all(Path(arg.split("=", 1)[1]).is_absolute() for arg in argv[1:3])
    assert launched[0][1]["env"]["SLINK_GEN2_FIXTURE_CASE"] == "qualified-a"
    assert launched[1][1]["env"]["SLINK_GEN2_FIXTURE_CASE"] == "qualified-b"
    assert launched[0][1]["env"]["SLINK_GEN2_SAVERAM_DIR"] != launched[1][1]["env"]["SLINK_GEN2_SAVERAM_DIR"]


@pytest.mark.parametrize("game", ["gen2_new", "gen2_gold_silver", "gen2_crystal_gold"])
def test_gen2_server_uses_production_routing(monkeypatch, tmp_path, game):
    from server import adapters

    before = dict(adapters._ROM_TYPE_TO_GAME_ID)
    run = duo.DuoRun("link", _args(game=game, scenario="link", server_flags=[]))
    run.wait_for = lambda *args: True
    launched = []
    monkeypatch.setattr(duo.subprocess, "Popen",
                        lambda cmd, **kwargs: launched.append((cmd, kwargs)) or SimpleNamespace())
    run.start_server()
    cmd, kwargs = launched[0]
    kwargs["stdout"].close()
    assert cmd[1:3] == ["-m", "server.server"]
    assert "env" not in kwargs and "server_rom_routes" not in run.gcfg
    for title in ("Crystal", "Gold", "Silver"):
        assert adapters.game_id_for_rom_type(title) == "gen2_gsc"
    assert before == adapters._ROM_TYPE_TO_GAME_ID


def test_gen2_required_callbacks_delegate_original_results_to_h2(monkeypatch, tmp_path):
    seen = []
    results = {"a": "A receipt", "b": "B receipt"}
    module = SimpleNamespace(check_save_witness=lambda res: seen.append(("witness", res)),
        link_oracle=lambda res, **kwargs: seen.append((kwargs, res)))
    monkeypatch.setitem(sys.modules, "gen2_duo_oracles", module)
    run = duo.DuoRun("link", _args(game="gen2_new", scenario="link"))
    run._gen2_inputs = {"a": {"ot_id": 101, "fixture": tmp_path / "gold_battle.SaveRAM"},
                        "b": {"ot_id": 202, "fixture": tmp_path / "silver_battle.SaveRAM"}}
    run._run_oracle(results)
    assert seen[0][0] == "witness"
    assert seen[1][0] == {"data_dir": run.data_dir, "ot_ids": {"a": 101, "b": 202},
                           "on_verified": run._record_gen2_facts,
                           "boot_saveram": {"a": tmp_path / "gold_battle.SaveRAM",
                                            "b": tmp_path / "silver_battle.SaveRAM"}}
    assert all(entry[1] is results for entry in seen)
    run.check_gen2_save_witness = None
    with pytest.raises(RuntimeError, match="witness validator"):
        run._run_oracle(results)


@pytest.mark.parametrize("game,titles,names", [
    ("gen2_gold_silver", ("gold", "silver"), ("gold_battle", "silver_battle")),
    ("gen2_crystal_gold", ("crystal", "gold"), ("crystal_battle", "gold_battle")),
])
def test_gen2_prepares_each_title_plan_and_ui_origins(monkeypatch, tmp_path, game, titles, names):
    import run_gb_gate as gate

    from tests.live import test_gen2_frame_align as align, test_gen2_new_gates as inspect
    from tools import gen2_fixtures, gen2_source_data

    # the default Gen 2 artifact is the overlay now (the launcher refuses clean); this test is about per-title
    # plan/origin isolation and keeps driving the still-selectable clean harness path
    monkeypatch.setenv("SLINK_GEN2_ARTIFACT", "clean")
    emulator = tmp_path / "EmuHawk.exe"
    emulator.touch()
    monkeypatch.setattr(duo, "EMUHAWK", str(emulator))
    inputs = {}
    for side, title, name in zip(("a", "b"), titles, names, strict=True):
        fixture = tmp_path / f"{name}.SaveRAM"
        fixture.write_bytes(name.encode())
        inputs[side] = {"title": title, "name": name, "fixture": fixture,
                        "qualification_attempt_id": f"qualified-{name}"}
    selected, plans, loaded, facts, origins = [], [], [], [], []

    def preflight(**kwargs):
        selected.append(kwargs["game"])
        return inputs

    def plan(title, directory, fixture, speed):
        plans.append((title, directory, fixture, speed))
        return {"title": title}

    def context(title, **kwargs):
        loaded.append(title)
        return SimpleNamespace(title=title)

    def route(title, repo, **kwargs):
        facts.append(title)
        return {"title": title}

    def u1(ctx, route_facts, attempt):
        origins.append((ctx.title, route_facts["title"], attempt))
        return {"pack_ui": ctx.title}

    monkeypatch.setattr(duo, "gen2_preflight", preflight)
    monkeypatch.setitem(gate.GENS["gen2"], "plan", plan)
    monkeypatch.setattr(gen2_fixtures, "exec_context", lambda title, kind, root: context(title, root=root))
    monkeypatch.setattr(gen2_fixtures, "route_facts", route)
    monkeypatch.setattr(align, "u1_facts", u1)
    monkeypatch.setattr(inspect, "inspect_env", lambda *args, **kwargs: {
        "SLINK_GEN2_FIXTURE_CASE": "{}"})
    run = duo.DuoRun("link", _args(game=game, scenario="link"))
    run._prepare_gen2_lane()
    assert selected == [game] and loaded == facts == list(titles)
    assert plans == [(titles[index], run._saveram_dir(side), inputs[side]["fixture"], 300)
                     for index, side in enumerate(("a", "b"))]
    assert origins == [(title, title, f"qualified-{name}")
                       for title, name in zip(titles, names, strict=True)]
    assert [json.loads(run._gen2_env[side]["SLINK_GEN2_U1_FACTS"])["pack_ui"]
            for side in ("a", "b")] == list(titles)


def test_gen2_real_witness_refuses_two_client_passes_without_save_markers():
    run = duo.DuoRun("link", _args(game="gen2_new", scenario="link"))
    results = {"a": "RESULT: PASS", "b": "RESULT: PASS"}
    with pytest.raises(RuntimeError, match="missing SAVE_WITNESS"):
        run._run_oracle(results)
    run.cfg = {}
    with pytest.raises(RuntimeError, match="post-result oracle"):
        run._run_oracle(results)


def test_gen2_clears_old_results_before_the_server_startup_wait(monkeypatch, tmp_path):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    run = duo.DuoRun("link", _args(game="gen2_new", scenario="link"))
    stale = [Path(run._result_path(side)) for side in ("a", "b")]
    for path in stale:
        path.write_text("RESULT: FAIL previous attempt\n")
    run._prepare_gen2_lane = lambda: None

    def server():
        assert all(not path.exists() for path in stale), "old verdict can abort server startup"

    def stop():
        raise RuntimeError("stop before emulator")

    run.start_server = server
    run.start_instances = stop
    run.cleanup = lambda passed: None
    with pytest.raises(RuntimeError, match="stop before emulator"):
        run.run()


def test_gen2_cleanup_keeps_a_lane_whose_name_extends_this_one(monkeypatch, tmp_path):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    runs = [duo.DuoRun("gen2_faint", _args(game="gen2_new", scenario="gen2_faint", lane=lane))
            for lane in ("cc", "cc_more")]
    paths = []
    for run in runs:
        result = Path(run._result_path("a"))
        snapshot = result.with_name(result.name.replace("_result.txt", "_link_save.SaveRAM"))
        result.write_text("RESULT: PASS")
        snapshot.write_bytes(b"immutable link snapshot")
        paths.append((result, snapshot))
    runs[0]._clear_attempt_artifacts()
    assert all(not path.exists() for path in paths[0])
    assert all(path.exists() for path in paths[1])


def test_gen2_lane_results_go_files_and_clearing_do_not_cross_lanes(monkeypatch, tmp_path):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    first = duo.DuoRun("gen2_faint", _args(game="gen2_new", lane="cc"))
    second = duo.DuoRun("gen2_faint", _args(game="gen2_gold_silver", lane="gs"))
    for run in (first, second):
        for side in ("a", "b"):
            Path(run._result_path(side)).write_text("HELLO {}\nRESULT: PASS\n")
            Path(run._phase_result_path(side, "saved")).write_text("phase")
            Path(run.go_files[side]).write_text("go")
        Path(run._pydec_path).write_text("PYDEC: PASS stale\n")
    first._clear_attempt_artifacts()
    assert not Path(first._result_path("a")).exists()
    assert not Path(first._phase_result_path("a", "saved")).exists()
    assert not Path(first.go_files["a"]).exists()
    assert Path(second._result_path("a")).is_file()
    assert Path(second.go_files["a"]).is_file()
    assert Path(second._pydec_path).read_text() == "PYDEC: PASS stale\n"
    first.wait_for = lambda label, predicate, timeout: predicate()
    assert first.wait_results() is None
    assert first._read_receipt("a") == ""
    first._status = lambda: {"players": {side: {"connected": True, "admission": "admitted"}
                                        for side in ("a", "b")}}
    def refuse_other_lane(label, predicate, timeout):
        assert predicate() is False
        raise RuntimeError("own HELLO absent")
    first.wait_for = refuse_other_lane
    with pytest.raises(RuntimeError, match="own HELLO absent"):
        first.orchestrate()


@pytest.mark.parametrize("verified", (True, False))
def test_gen2_final_pydec_requires_verified_facts(monkeypatch, tmp_path, verified):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    run = duo.DuoRun("gen2_faint", _args(game="gen2_new", lane="cc", keep_alive=False))
    for method in ("_prepare_gen2_lane", "start_server", "start_instances", "orchestrate"):
        setattr(run, method, lambda: None)
    run.cleanup = lambda passed: None
    run.wait_results = lambda: ("RESULT: PASS", "RESULT: PASS")
    run._wait_gen2_exit_flush = lambda: None
    facts = {"a": "key-a", "b": "key-b", "area": "route_29",
             "titles": "crystal/gold", "status": "dead"}
    run._run_oracle = lambda results: run._record_gen2_facts(facts) if verified else None
    if verified:
        assert run.run() is True
        assert Path(run._pydec_path).read_text().splitlines()[-1] == (
            "PYDEC: PASS a=key-a b=key-b area=route_29 titles=crystal/gold status=dead")
    else:
        run._gen2_verified_facts = facts  # A previous callback must not qualify this verdict.
        with pytest.raises(RuntimeError, match="did not report verified facts"):
            run.run()
        assert "PYDEC: PASS" not in Path(run._pydec_path).read_text()


@pytest.mark.parametrize("fault", (None, "missing", "timeout", "crash"))
def test_gen2_oracle_waits_for_both_exit_flushes(monkeypatch, tmp_path, fault):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    run = duo.DuoRun("link", _args(game="gen2_new", lane="cc", keep_alive=False))
    for method in ("_prepare_gen2_lane", "start_server", "start_instances", "orchestrate"):
        setattr(run, method, lambda: None)
    run.wait_results = lambda: ("RESULT: PASS", "RESULT: PASS")
    run.cleanup = lambda passed: None
    actions = []
    files = {side: tmp_path / f"exit-{side}.SaveRAM" for side in ("a", "b")}
    for path in files.values():
        path.write_bytes(b"pre-exit")
    def wait(side, timeout):
        assert 0 < timeout <= 30 + 1e-6   # deadline - time.monotonic() of two large floats can round a hair above 30
        actions.append(side)
        if fault == "timeout" and side == "b":
            raise duo.subprocess.TimeoutExpired("emu", timeout)
        files[side].write_bytes(b"final-exit")
        return 1 if fault == "crash" and side == "b" else 0
    run.emu_by_inst = {side: SimpleNamespace(wait=lambda timeout, s=side: wait(s, timeout)) for side in ("a", "b")}
    if fault == "missing":
        del run.emu_by_inst["b"]
    def oracle(results):
        assert actions == ["a", "b"]
        assert all(path.read_bytes() == b"final-exit" for path in files.values())
        actions.append("oracle")
        run._record_gen2_facts({"a": "a", "b": "b", "area": "route_29", "titles": "crystal/crystal", "status": "alive"})
    run._run_oracle = oracle
    if fault:
        with pytest.raises(RuntimeError, match="exit|process"):
            run.run()
        assert "oracle" not in actions
    else:
        assert run.run()
        assert actions == ["a", "b", "oracle"]


def test_gen2_attempt_archive_reads_only_its_lane(monkeypatch, tmp_path):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    args = _args(game="gen2_new", lane="cc")
    run = duo.DuoRun("gen2_faint", args)
    for side in ("a", "b"):
        Path(run._result_path(side)).write_text("RESULT: PASS own\n")
        (tmp_path / f"e2e_gen2_faint_{side}_result.txt").write_text("RESULT: PASS other\n")
    Path(run._pydec_path).write_text("PYDEC: PASS own\n")
    run.run = lambda: True
    monkeypatch.setattr(duo, "DuoRun", lambda *a, **kw: run)
    assert duo.run_scenario_with_rng_retry("gen2_faint", args) == (True, 1)
    for side in ("a", "b", "pydec"):
        path = tmp_path / f"e2e_gen2_faint_cc_{side}_attempt1_result.txt"
        assert path.is_file()
        assert "own" in path.read_text() and "other" not in path.read_text()


@pytest.fixture(autouse=True)
def _fresh_lane_registry(monkeypatch):
    """The lane ordinal is process state; a fresh table keeps each test's expectation local."""
    monkeypatch.setattr(duo, "_LANE_ORDINAL", {})


@pytest.mark.parametrize("fault", (None, "digest", "missing", "stale", "cross_lane"))
def test_gen2_archives_reconnect_initial_witness_and_preserves_retry(monkeypatch, tmp_path, fault):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    name = "gen2_reconnect_cc"
    data = b"A" * 32790
    texts = {}
    for side in ("a", "b"):
        snapshot = tmp_path / f"e2e_{name}_{side}_witness.SaveRAM"
        snapshot.write_bytes(data)
        exit_save = tmp_path / f"mutable_{side}.SaveRAM"
        exit_save.write_bytes(b"E" * 32790)
        header = {"player": side, "attempt": 2 if fault == "stale" else 1, "scenario": "gen2_reconnect"}
        witness = {"snapshot_path": str(snapshot), "saveram_path": str(exit_save),
                   "cartram_sha256": hashlib.sha256(data[:32768]).hexdigest()}
        if fault == "cross_lane":
            other = tmp_path / "other_witness.SaveRAM"
            other.write_bytes(data)
            witness["snapshot_path"] = str(other)
        texts[side] = "DUO_GEN2 " + json.dumps(header) + "\nSAVE_WITNESS " + json.dumps(witness)
    (tmp_path / f"e2e_{name}_a_initial_result.txt").write_text(texts["a"])
    receipts = {"a": 'DUO_GEN2 {"player":"a","attempt":1,"scenario":"gen2_reconnect"}\nRESULT: PASS wrong_save',
                "b": texts["b"]}
    source = tmp_path / f"e2e_{name}_a_witness.SaveRAM"
    if fault == "digest":
        source.write_bytes(b"B" * 32790)
    elif fault == "missing":
        source.unlink()
    if fault:
        with pytest.raises((RuntimeError, FileNotFoundError)):
            duo._archive_attempt(name, 1, receipts)
        return
    duo._archive_attempt(name, 1, receipts)
    for side in ("a_initial", "b"):
        assert (tmp_path / f"e2e_{name}_{side}_attempt1_witness.SaveRAM").read_bytes() == data
    manifest = tmp_path / f"e2e_{name}_attempt1_manifest.json"
    rows = json.loads(manifest.read_text())["witnesses"]
    assert {row["sha256"] for row in rows} == {hashlib.sha256(data).hexdigest()}
    for row in rows:
        assert hashlib.sha256(Path(row["receipt"]).read_bytes()).hexdigest() == row["receipt_sha256"]
        assert Path(row["exit_archive"]).read_bytes() == b"E" * 32790
        assert row["exit_sha256"] == hashlib.sha256(b"E" * 32790).hexdigest()
    with pytest.raises(RuntimeError, match="already exists"):
        duo._archive_attempt(name, 1, receipts)
    assert (tmp_path / f"e2e_{name}_a_initial_attempt1_result.txt").read_text() == texts["a"]
    run = duo.DuoRun("gen2_reconnect", _args(game="gen2_new", lane="cc"), attempt=2)
    run._clear_attempt_artifacts()
    assert not source.exists()
    assert manifest.exists()
    assert (tmp_path / f"e2e_{name}_a_initial_attempt1_witness.SaveRAM").read_bytes() == data
    # A fresh invocation starts at 1 and must not inherit earlier invocation's archives.
    run.attempt = 1
    run._clear_attempt_artifacts()
    assert not manifest.exists()


def _args(**overrides) -> argparse.Namespace:
    base = {"game": "gen1_new", "lane": None, "scenario": "link_new", "idle_jitter": 0}
    base.update(overrides)
    return argparse.Namespace(**base)


def _run(monkeypatch, tmp_path, **overrides):
    """A DuoRun that writes everything under tmp_path and launches nothing."""
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    monkeypatch.setattr(duo, "BIZHAWK_CONFIG", str(_config_file(tmp_path)))
    # Lane isolation only cares about the stub/config/window bookkeeping, never the ROM path
    # value; staged_rom just needs to resolve so launch_instance's Popen argv can be built. Real
    # cartridge dumps are a dev-box artifact, not present on a clean checkout.
    monkeypatch.setattr(g1, "staged_rom", lambda *_a, **_k: "fake.gb")
    # CODE-DIGEST: the stamp shells out to git, which the Popen stub below would swallow
    monkeypatch.setattr(duo.importlib.import_module("gen2_code_digest"), "run_stamp", lambda *_a, **_k: {})
    launched = []
    monkeypatch.setattr(duo.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    run = duo.DuoRun("link_new", _args(**overrides), attempt=1)
    run.launched = launched
    return run


def _config_file(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(json.dumps({
        "SoundEnabled": True,
        "MainWindowPosition": "1200, -1300",
        "CoreSyncSettings": {"BizHawk.Emulation.Cores.Nintendo.Gameboy.Gameboy": {}},
        # write_run_config refuses to redirect SaveRAM without these rows (it would let two
        # instances of one cartridge share a file), so a stand-in config must carry them.
        "PathEntries": {"Paths": [
            {"System": system, "Type": "Save RAM", "Path": ""}
            for system in ("GB_GBC_SGB", "GBL")
        ]},
    }), encoding="utf-8")
    return path


def test_required_family_gets_the_same_pydec_transport(monkeypatch, tmp_path):
    monkeypatch.setitem(duo.GAMES, "gen2_new", {**duo.GAMES["gen1_new"], "game": "gen2_new"})
    monkeypatch.setitem(duo.FAMILY_EVIDENCE, "gen2_new",
                        duo.EvidenceContract("future_witness", require_oracle=True))
    run = _run(monkeypatch, tmp_path, game="gen2_new")
    try:
        assert run._pydec_path == str(tmp_path / f"e2e_link_new_{run.lane}_pydec_result.txt")
        assert run.launched == []
    finally:
        os.rmdir(run.data_dir)


def _launch_both(run):
    for inst in ("a", "b"):
        run.launch_instance(inst, seed=False)
    return run.launched


def test_two_runs_in_one_process_share_no_stub_config_or_window(monkeypatch, tmp_path):
    """The lane id keys the generated stub and the config copy; the window moves with it."""
    # One run at a time: each `_run` installs its own Popen recorder, so a run's launches have to
    # happen while its patch is the active one.
    one = _run(monkeypatch, tmp_path)
    _launch_both(one)
    two = _run(monkeypatch, tmp_path)
    _launch_both(two)

    def _named(argv, flag, inst, ext):
        return next(a for a in argv if a.startswith(flag) and a.endswith(f"_{inst}.{ext}"))

    for inst in ("a", "b"):
        index = 0 if inst == "a" else 1
        a_stub = _named(one.launched[index], "--lua=", inst, "lua")
        b_stub = _named(two.launched[index], "--lua=", inst, "lua")
        a_cfg = _named(one.launched[index], "--config=", inst, "ini")
        b_cfg = _named(two.launched[index], "--config=", inst, "ini")
        assert a_stub != b_stub, (inst, a_stub)
        assert a_cfg != b_cfg, (inst, a_cfg)
        assert str(one.lane) in a_stub and str(two.lane) in b_stub

    positions = []
    for run in (one, two):
        for inst in ("a", "b"):
            with open(os.path.join(str(tmp_path), f"duo_cfg_{run.lane}_{inst}.ini"),
                      encoding="utf-8-sig") as handle:
                positions.append(json.load(handle)["MainWindowPosition"])
    assert len(set(positions)) == 2, positions          # one slot per lane, both instances aligned


def test_two_runs_keep_separate_saveram_directories(monkeypatch, tmp_path):
    one = _run(monkeypatch, tmp_path)
    two = _run(monkeypatch, tmp_path)
    assert one._saveram_dir("a") != two._saveram_dir("a")
    assert one._saveram_dir("a") != one._saveram_dir("b")
    for run in (one, two):
        for inst in ("a", "b"):
            assert str(run.lane) in run._saveram_dir(inst)


def test_a_lane_keeps_the_receipt_names_it_always_had(monkeypatch, tmp_path):
    """Receipts and go-files are per scenario+instance BY DESIGN: the admission scenarios write
    those exact names (`e2e_admit_randomized_new_{a,b}_result.txt` in test_e2e_duo_admission.py),
    and every launch removes its own receipt first, so a lane cannot inherit a stale verdict.
    Two lanes on the SAME scenario would still race on them — see the module docstring."""
    run = _run(monkeypatch, tmp_path)
    assert run._result_path("a") == os.path.join(str(tmp_path), "e2e_link_new_a_result.txt")
    assert run._phase_result_path("a", "reconnect") == os.path.join(
        str(tmp_path), "e2e_link_new_a_reconnect_result.txt")
    assert run.go_files["a"] == os.path.join(str(tmp_path), "duo_go_link_new_a.txt")


def test_the_lane_id_defaults_to_the_free_port_and_can_be_named(monkeypatch, tmp_path):
    default = _run(monkeypatch, tmp_path)
    assert default.lane == str(default.tcp_port)
    named = _run(monkeypatch, tmp_path, lane="pure-a")
    assert named.lane == "pure-a" and named.lane_index != default.lane_index


def test_the_window_offset_leaves_lane_zero_and_primary_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("SLINK_EMU_WINDOW", "1200,-1300")
    first = _run(monkeypatch, tmp_path, lane="lane-zero")
    assert first.lane_index == 0
    assert first._lane_window() is None                  # lane 0 is what write_run_config wrote
    second = _run(monkeypatch, tmp_path, lane="lane-one")
    assert second.lane_index == 1
    assert second._lane_window() == f"{1200 + duo._LANE_WINDOW_STEP}, -1300"
    monkeypatch.setenv("SLINK_EMU_WINDOW", "primary")
    assert second._lane_window() is None


# ── the pureRGB pairing row ─────────────────────────────────────────────────────────────────────


def test_the_pure_pairing_row_names_its_clean_sources_and_its_fixtures():
    """`rom` is the clean pinned build: the instances boot the overlay (`patched_saves`), and only
    admit_randomized_new reads these, as the sources its randomized companion pair is made from."""
    row = duo.GAMES["gen1_pure"]
    assert row["main"] == duo.GAMES["gen1_new"]["main"]      # same driver, same scenarios
    assert row["game"] == "gen1_new"                         # the scenario registry selects on this
    assert row["uses_savestate"] is False
    assert row["fixture"] == {"a": "purered", "b": "pureblue"}
    for inst, key in row["fixture"].items():
        assert g1.is_purergb(key), key
        assert row["rom"][inst] == os.path.join("patch", "build", f"gen1_{key}.gbc").replace("\\", "/")
        assert g1.fixture_path(key, "town").endswith(f"{key}_town.SaveRAM")


def test_a_scenario_that_stages_its_own_rom_wins_for_that_instance_only(monkeypatch, tmp_path):
    run = _run(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "_admit_roms",
                        {"a": "patch/build/e2e_admit_randomized_new/a.gb"}, raising=False)
    assert run._rom_for("a") == "patch/build/e2e_admit_randomized_new/a.gb"
    assert run._rom_for("b") == "patch/gen1/build/slink_blue.gb"   # B: the row's companion


# ── the companion cartridge is REQUIRED (owner 2026-10-02): every Gen 1 row boots it ──────────────

GEN1_ROWS = sorted(name for name, row in duo.GAMES.items() if row.get("game") == "gen1_new")


def test_the_gen1_rows_are_the_vanilla_pair_and_the_two_pure_pairs():
    # the clean-pure/overlay split is gone: gen1_pure runs every scenario on the overlay
    assert GEN1_ROWS == ["gen1_new", "gen1_pure", "gen1_pure_green"]


@pytest.mark.parametrize("game", GEN1_ROWS)
def test_every_gen1_row_names_a_companion_key_with_both_fixtures(game):
    """The row's `patched_saves` is what boots: each key is a run_gb_gate.PATCHED row whose seed
    base IS the row's clean fixture title (A4), with both the town and battle saves committed,
    and the gate harness answers it (named for the vanilla build, admitted for an overlay)."""
    from run_gb_gate import PATCHED, named_title

    row = duo.GAMES[game]
    assert set(row["patched_saves"]) == {"a", "b"}
    for inst, key in row["patched_saves"].items():
        base, rom_rel, save_name = PATCHED[key]
        assert base == row["fixture"][inst]
        assert save_name == g1.save_name_for(rom_rel or f"patch/build/gen1_{key}.gbc")
        for target in g1.TARGETS:
            assert os.path.isfile(g1.fixture_path(base, target)), (key, target)
        assert named_title(key) == (None if g1.is_purergb_overlay(key) else base)


@pytest.mark.parametrize("game", GEN1_ROWS)
def test_no_gen1_row_defers_a_scenario(game):
    scenarios = duo.scenarios_for(game)
    assert "not_yet" not in duo.GAMES[game]
    assert {"trade_new", "trade_decline_new", "explode_new"} <= set(scenarios)
    assert len(scenarios) == 20


@pytest.mark.parametrize("game,want", [
    ("gen1_new", {"a": "red_patched", "b": "blue_patched"}),
    ("gen1_pure", {"a": "purered_overlay", "b": "pureblue_overlay"}),
    ("gen1_pure_green", {"a": "purered_overlay", "b": "puregreen_overlay"}),
])
@pytest.mark.parametrize("scenario", ["link_new", "trade_new", "ball_gate_new"])
def test_patch_key_falls_back_to_the_game_row_for_every_scenario(game, want, scenario):
    """No scenario names a cartridge: rules-only, trade and cold-boot scenarios all boot the
    row's companion. (A scenario-level key used to apply only to the three trade scenarios, so
    every other scenario booted the CLEAN build.)"""
    assert "patched_saves" not in duo.SCENARIOS[scenario] and "rom" not in duo.SCENARIOS[scenario]
    run = duo.DuoRun(scenario, _args(game=game, scenario=scenario), attempt=1)
    assert {inst: run._patch_key(inst) for inst in ("a", "b")} == want


def test_rom_for_boots_the_vanilla_companion_build(monkeypatch):
    monkeypatch.setattr(g1, "staged_rom", lambda key: pytest.fail(f"staged clean {key}"))
    run = duo.DuoRun("link_new", _args(game="gen1_new"), attempt=1)
    assert run._rom_for("a") == "patch/gen1/build/slink_red.gb"
    assert run._rom_for("b") == "patch/gen1/build/slink_blue.gb"


@pytest.mark.parametrize("game,b_key", [("gen1_pure", "pureblue_overlay"),
                                        ("gen1_pure_green", "puregreen_overlay")])
def test_rom_for_stages_the_overlay_on_the_pure_rows(monkeypatch, game, b_key):
    """The INVERSE of the retired overlay-row pin: a rules-only scenario on a pure row used to
    stage the CLEAN pure cartridge; now it stages the overlay, through the applier that
    sha1-verifies it against admission_overlay.json."""
    monkeypatch.setattr(g1, "staged_rom", lambda key: f"STAGED:{key}")
    run = duo.DuoRun("link_new", _args(game=game), attempt=1)
    assert run._rom_for("a") == "STAGED:purered_overlay"
    assert run._rom_for("b") == f"STAGED:{b_key}"


def test_start_instances_refuses_a_missing_companion_build_before_any_launch(monkeypatch, tmp_path):
    run = _run(monkeypatch, tmp_path)
    monkeypatch.setattr(duo, "REPO", str(tmp_path))          # no patch/gen1/build here
    run._clear_attempt_artifacts = lambda: pytest.fail("got past the companion check")
    with pytest.raises(FileNotFoundError, match="slink_red.gb missing"):
        run.start_instances()
    assert run.launched == []


def _gen1_oracle_run(tmp_path, game="gen1_new"):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg = dict(duo.GAMES[game])
    run.cfg = dict(duo.SCENARIOS["link_new"])
    run._saveram_dir = lambda inst: str(tmp_path / f"saves_{inst}")
    run._pydec_note = lambda _fact: None
    return run


def test_the_oracle_reads_the_companion_save_and_rom_never_the_clean_seed(monkeypatch, tmp_path):
    """THE stale-data false green: `_saved_gen1_party`'s default used to read the clean title's
    gamedb-named SaveRAM -- the fixture seed, which the companion cartridge never writes -- and
    qualify it against the CLEAN ROM. Here the clean-named file holds different (and perfectly
    valid) bytes; the oracle must not see them, and must qualify against the companion build."""
    import gen1_fixtures
    from run_gb_gate import GENS

    battle = Path(g1.fixture_path("red", "battle")).read_bytes()
    town = Path(g1.fixture_path("red", "town")).read_bytes()
    assert battle != town
    for rel, raw in (("patch/gen1/build/slink_red.gb", b"COMPANION-RED"),
                     ("patch/build/gen1_red.gb", b"CLEAN-RED")):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(raw)
    saves = tmp_path / "saves_a"
    saves.mkdir()
    (saves / "slink red.SaveRAM").write_bytes(battle)                        # what the cart wrote
    (saves / GENS["gen1"]["saveram_names"]["red"]).write_bytes(town)        # a stale clean seed
    monkeypatch.setattr(duo, "REPO", str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    roms = []
    monkeypatch.setattr(gen1_fixtures, "qualify",
                        lambda sram, rom, notes=None: roms.append(rom) or [])
    sram, _party, _box, _codec = _gen1_oracle_run(tmp_path)._saved_gen1_party("a")
    assert sram == battle, "the oracle read the clean-named seed, not the companion's save"
    assert roms == [b"COMPANION-RED"], "the save was qualified against the clean ROM"


def test_the_wrong_save_leg_qualifies_against_the_companion_rom_never_the_clean_dump(monkeypatch, tmp_path):
    """The reconnect leg's second-OT save check (`_wrong_red_save_ot`) qualifies the save against
    the cartridge A boots -- its companion build -- not the clean dump the run refuses."""
    import gen1_fixtures

    for rel, raw in (("patch/gen1/build/slink_red.gb", b"COMPANION-RED"),
                     ("patch/build/gen1_red.gb", b"CLEAN-RED"),
                     ("data/games/gen1_rby/profile.json",
                      Path(_REPO, "data", "games", "gen1_rby", "profile.json").read_bytes())):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(raw)
    wrong = tmp_path / "red_town_ot2.SaveRAM"
    wrong.write_bytes(Path(g1.fixture_path("red", "battle")).read_bytes())
    monkeypatch.setattr(duo, "REPO", str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    roms = []
    monkeypatch.setattr(gen1_fixtures, "qualify",
                        lambda sram, rom, notes=None: roms.append(rom) or [])
    _gen1_oracle_run(tmp_path)._wrong_red_save_ot(str(wrong), expected_ot=-1)
    assert roms == [b"COMPANION-RED"], "the wrong save was qualified against the clean ROM"


def test_the_oracle_names_the_overlay_save_on_a_pure_row(tmp_path):
    run = _gen1_oracle_run(tmp_path, "gen1_pure_green")
    assert run._gen1_save_name("a") == "gen1 purered overlay.SaveRAM"
    assert run._gen1_save_name("b") == "gen1 puregreen overlay.SaveRAM"


@pytest.mark.parametrize("game", GEN1_ROWS)
@pytest.mark.parametrize("method", ["_rom_for", "_gen1_save_name"])
@pytest.mark.parametrize("staged", [False, True])
def test_gen1_row_without_companion_key_cannot_fall_back_to_clean(game, method, staged,
                                                                monkeypatch, tmp_path):
    run = duo.DuoRun("link_new", _args(game=game))
    run.gcfg = dict(run.gcfg, patched_saves={})
    # Neither the old scenario fallback nor a staged artifact excuses a broken game row.
    run.cfg = dict(run.cfg, rom={"a": "clean-red.gb", "b": "clean-blue.gb"})
    if staged:
        run._admit_roms = {"a": "randomized-companion.gb"}
    monkeypatch.setattr(g1, "staged_rom", lambda key: f"clean-{key}.gb")
    with pytest.raises(RuntimeError, match="Gen 1 row has no companion key"):
        getattr(run, method)("a")


@pytest.mark.parametrize("game", GEN1_ROWS)
def test_gen1_evidence_uses_real_companion_resolver_for_every_pairing(monkeypatch, game):
    from run_gb_gate import PATCHED

    staged = []

    def overlay_path(key):
        staged.append(key)
        assert key.endswith("_overlay")
        return f"patch/build/gen1_{key}.gbc"

    monkeypatch.setattr(g1, "staged_rom", overlay_path)
    run = duo.DuoRun("link_new", _args(game=game))
    for inst, key in duo.GAMES[game]["patched_saves"].items():
        expected = PATCHED[key][1] or f"patch/build/gen1_{key}.gbc"
        resolved = run._rom_for(inst)
        assert resolved == expected and resolved not in run.gcfg["rom"].values()
    assert staged == ([] if game == "gen1_new" else list(run.gcfg["patched_saves"].values()))
    assert duo.evidence_contract(game).witness_validator == "check_save_witness"
