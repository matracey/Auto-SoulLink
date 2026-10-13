"""F-4 runner orchestration with fake UPR outputs and server receipts; no jar or emulator."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
from lupa import LuaRuntime

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import e2e_duo as duo  # noqa: E402
from run_gb_gate import GENS  # noqa: E402

from server import upr_pipeline  # noqa: E402
from server.adapters import (  # noqa: E402
    gen1_codec as codec,
    gen1_rom_scan as scan,
)
from server.upr_settings import build_categories  # noqa: E402

# The driver echoes the harness's `--idle-jitter` back in each result file, and the harness
# checks that echo (duo.jitter_problems): a jitter the harness asked for and the driver did not
# apply is a harness finding, not a scenario verdict.
_JITTER_1 = "JITTER requested=0 applied=0 attempt=1"
_JITTER_2 = "JITTER requested=37 applied=37 attempt=2"


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(duo, "REPO", str(tmp_path))
    monkeypatch.setattr(duo, "BUILD", str(tmp_path / "build"))
    source_dir = tmp_path / "patch" / "build"
    source_dir.mkdir(parents=True)
    (source_dir / "gen1_red.gb").write_bytes(b"clean-red")
    (source_dir / "gen1_blue.gb").write_bytes(b"clean-blue")
    # the companion builds the row's instances boot (GAMES["gen1_new"]["patched_saves"])
    companion_dir = tmp_path / "patch" / "gen1" / "build"
    companion_dir.mkdir(parents=True)
    (companion_dir / "slink_red.gb").write_bytes(b"companion-red")
    (companion_dir / "slink_blue.gb").write_bytes(b"companion-blue")
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario = "admit_randomized_new"
    run.cfg = dict(duo.SCENARIOS[run.scenario])
    # The stub drives the verdict path, so the scenario's real oracle is replaced by a no-op of
    # the same shape: a Gen 1 scenario with NO oracle is a deliberate failure (pinned below),
    # and each saved-state oracle has its own tests in the synthetic-oracle section.
    run.cfg["oracle"] = "assert_stub_oracle"
    run.assert_stub_oracle = lambda results, **kwargs: None
    run.gcfg = dict(duo.GAMES["gen1_new"])
    run.data_dir = str(tmp_path / "run")
    Path(run.data_dir).mkdir()
    run._saveram_dir = lambda inst: str(tmp_path / f"saves_{inst}")
    # State the real __init__ provides: the live legs are assumed complete unless a test is
    # about them, and the artifact clock starts now.
    run._live_complete = {run.scenario: True}
    run._started = time.time()
    run._launch_times = {}
    run._same_save_artifact = None
    return run


def _fake_pipeline(monkeypatch):
    """server.cartridges.provision, faked: the companion composition itself is
    test_cartridges.py's; here only what the scenario asks for and does with the result."""
    from server import cartridges

    called = []
    monkeypatch.setattr(upr_pipeline, "find_upr_jar", lambda: "fake.jar")
    mapping = {b"random-red": "a" * 64, b"random-blue": "b" * 64,
               b"clean-blue": "9" * 64, b"clean-red": "d" * 64,
               b"companion-blue": "c" * 64, b"companion-red": "e" * 64}
    monkeypatch.setattr(scan, "fingerprint_rom", lambda raw: mapping[raw])

    def provision(run_dir, sources, *, companion, randomize, jar=""):
        called.append((run_dir, sources, companion, randomize, jar))
        roms = Path(run_dir) / "roms"
        roms.mkdir(parents=True)
        players, contract_players = {}, {}
        for player, raw, seed in (("a", b"random-red", "1"), ("b", b"random-blue", "2")):
            output = roms / f"{player}.gb"
            output.write_bytes(raw)
            players[player] = {"output": str(output), "fingerprint": mapping[raw],
                               "rom_sha1": hashlib.sha1(raw).hexdigest(), "kind": "rand_companion"}
            contract_players[player] = {"fingerprint": mapping[raw], "seed": seed,
                                        "rom_sha1": hashlib.sha1(raw).hexdigest()}
        contract = {"upr_version": "4.6.1", "settings_sha256": "f" * 64, "categories": ["wild"],
                    "players": contract_players}
        (Path(run_dir) / "rom_contract.json").write_text(json.dumps(contract), encoding="utf-8")
        return {"family": "vanilla", "companion": companion, "randomizer": {}, "players": players}

    monkeypatch.setattr(cartridges, "provision", provision)
    monkeypatch.setattr(upr_pipeline, "prepare_pair",
                        lambda *args: pytest.fail("randomized outside the companion composition"))
    return called


def test_contract_and_staged_rom_are_ready_before_server(runner, monkeypatch):
    called = _fake_pipeline(monkeypatch)
    contract = runner.prepare_admit_randomized_new()
    assert len(called) == 1
    run_dir, sources, companion, randomize, jar = called[0]
    # the cartridges a randomized companion run hands out: companion ON, from the clean sources
    assert companion is True and jar == "fake.jar" and run_dir == runner.data_dir
    assert Path(randomize["settings_path"]).read_bytes() == build_categories({"wild"})
    assert sources == {p: os.path.join(duo.REPO, runner.gcfg["rom"][p]) for p in ("a", "b")}
    path = Path(runner.data_dir) / "rom_contract.json"
    assert json.loads(path.read_text(encoding="utf-8")) == contract
    assert set(contract) == {"upr_version", "settings_sha256", "categories", "players"}
    assert set(contract["players"]["a"]) == {"fingerprint", "seed", "rom_sha1"}
    assert contract["players"]["a"]["seed"] == "1"
    staged = Path(duo.REPO) / runner._admit_roms["a"]
    assert staged.suffix == ".gb" and staged.read_bytes() == b"random-red"
    # B boots its companion build (a clean cartridge is refused before any contract check), and
    # the reported fingerprint ("c") is the one read off THOSE bytes, not the clean source's ("9")
    assert set(runner._admit_roms) == {"a"}
    assert runner._rom_for("b") == "patch/gen1/build/slink_blue.gb"
    assert runner._gen1_save_name("a") == "slink red randomized.SaveRAM"
    assert runner._admit_fingerprints == {"expected_b": "b" * 64, "reported_b": "c" * 64}


def test_missing_jar_fails_before_any_contract_is_written(runner, monkeypatch):
    monkeypatch.setattr(upr_pipeline, "find_upr_jar", lambda: None)
    with pytest.raises(RuntimeError, match="PokeRandoZX.jar missing"):
        runner.prepare_admit_randomized_new()
    assert not (Path(runner.data_dir) / "rom_contract.json").exists()


def test_each_save_is_seeded_under_the_one_name_its_cartridge_boots(runner, monkeypatch):
    runner._admit_roms = {"a": "build/e2e_admit_randomized_new/slink_red_randomized.gb"}

    def seed(rom, target, dest_dir):
        assert target == "town"
        path = Path(dest_dir) / GENS["gen1"]["saveram_names"][rom]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{rom}-town".encode())
        return str(path)

    monkeypatch.setattr("run_gb_gate.seed_saveram", seed)
    runner._seed_instance_save("a")
    runner._seed_instance_save("b")
    a_dir = Path(runner._saveram_dir("a"))
    b_dir = Path(runner._saveram_dir("b"))
    # A boots the staged randomized cartridge, B its companion: each fixture lands under that
    # cartridge's name only -- no clean-named or companion-named copy is left beside A's save
    # for an oracle to read by mistake
    assert (a_dir / "slink red randomized.SaveRAM").read_bytes() == b"red-town"
    assert len(list(a_dir.iterdir())) == 1
    assert (b_dir / "slink blue.SaveRAM").read_bytes() == b"blue-town"
    assert len(list(b_dir.iterdir())) == 1


def _receipts(runner, *, b_reason=None, b_party=None):
    got, want = "c" * 64, "b" * 64
    reason = b_reason or ("this is not the cartridge built for player b "
                          f"(reported {got[:12]}, expected {want[:12]})")
    runner._admit_fingerprints = {"reported_b": got, "expected_b": want}
    runner._status = lambda: {"players": {
        "a": {"admission": "admitted", "admission_reason": "cartridge matches the contract",
              "party_keys": ["AAAA:BBBB:99"], "trainer_name": "RED"},
        "b": {"admission": "rejected", "admission_reason": reason,
              "party_keys": b_party or [], "trainer_name": "", "current_area_id": ""}}}
    Path(duo.BUILD).mkdir(parents=True, exist_ok=True)
    for player, variant in (("a", "red"), ("b", "blue")):
        (Path(duo.BUILD) / f"e2e_admit_randomized_new_{player}_result.txt").write_text(
            f"ADMIT_MAP 40 5 6\nADMIT_PARTY 1\nHELLO_RECEIPT {variant} 57 wild_maps\n",
            encoding="utf-8")
    (Path(runner.data_dir) / "events.json").write_text(json.dumps([
        {"player": "a", "type": "hello", "text": "Connected (Red, 1 mons)"},
        {"player": "b", "type": "hello", "text": "REJECTED — " + reason},
    ]), encoding="utf-8")
    (Path(runner.data_dir) / "slink.log").write_text(
        "[a] admission: admitted — cartridge matches the contract\n"
        "[b] admission: rejected — " + reason + "\n", encoding="utf-8")


def test_both_public_admission_verdicts_and_events(runner):
    _receipts(runner)
    runner.assert_admit_randomized_new()


def test_rejected_control_must_name_both_fingerprint_prefixes(runner):
    _receipts(runner, b_reason="not the cartridge built for player b")
    with pytest.raises(RuntimeError, match="fingerprint prefixes"):
        runner.assert_admit_randomized_new()


def test_rejected_control_must_not_adopt_party(runner):
    _receipts(runner, b_party=["DEAD:BEEF:01"])
    with pytest.raises(RuntimeError, match="adopted party"):
        runner.assert_admit_randomized_new()


def test_admission_prestep_runs_before_server_start(runner):
    order = []
    runner.prepare_admit_randomized_new = lambda: order.append("contract")
    runner.start_server = lambda: order.append("server")
    runner.start_instances = lambda: order.append("emulators")
    runner.orchestrate = lambda: order.append("verdicts")
    runner.wait_results = lambda: ("RESULT: PASS", "RESULT: PASS")
    runner.cleanup = lambda passed: order.append(f"cleanup:{passed}")
    runner.args = type("Args", (), {"keep_alive": False})()
    assert runner.run() is True
    assert order == ["contract", "server", "emulators", "verdicts", "cleanup:True"]


def test_python_oracle_receipt_persists_facts_and_terminal_result(runner, tmp_path):
    runner._pydec_path = str(tmp_path / "e2e_admit_randomized_new_pydec_result.txt")
    runner.attempt = 1
    runner.prepare_admit_randomized_new = lambda: None
    runner.start_server = lambda: None
    runner.start_instances = lambda: None
    runner.orchestrate = lambda: runner._pydec_note("public admission fact valid")
    runner.wait_results = lambda: ("RESULT: PASS", "RESULT: PASS")
    runner.cleanup = lambda passed: None
    runner.args = type("Args", (), {"keep_alive": False})()
    assert runner.run() is True
    # The header asks `scenario_attempt_limit`, which is keyed on the game; this stub carries
    # no `game` (the real gen1_new bound of 2 is pinned by its own test), so it reads "of 1".
    assert Path(runner._pydec_path).read_text(encoding="utf-8").splitlines() == [
        "attempt 1 of 1", "public admission fact valid", "PYDEC: PASS asserted scenario facts"]


def test_python_oracle_receipt_ends_fail_on_an_assertion_error(runner, tmp_path):
    runner._pydec_path = str(tmp_path / "e2e_admit_randomized_new_pydec_result.txt")
    runner.attempt = 1
    runner.prepare_admit_randomized_new = lambda: None
    runner.start_server = lambda: None
    runner.start_instances = lambda: None
    runner.orchestrate = lambda: (_ for _ in ()).throw(RuntimeError("bad checksum"))
    runner.cleanup = lambda passed: None
    with pytest.raises(RuntimeError, match="bad checksum"):
        runner.run()
    assert Path(runner._pydec_path).read_text(encoding="utf-8").splitlines()[-1] == (
        "PYDEC: FAIL bad checksum")


def _reconnect_snapshots():
    link = {"area_id": "route_1", "status": "alive",
            "a": {"key": "AAAA:1111:01"}, "b": {"key": "BBBB:2222:02"}}
    before = {"links": [link], "player_identity": {"a": {"ot_id": "1234"}}}
    after = {**before, "status": {"players": {
        "a": {"connected": True, "identity_error": "", "party_keys": ["AAAA:1111:01"]},
        "b": {"connected": True}}}}
    events = [{"player": "a", "type": "hello", "text": "Connected (Red, 2 mons)"},
              {"player": "b", "type": "hello", "text": "Connected (Blue, 2 mons)"},
              {"player": "a", "type": "capture", "text": "caught"},
              {"player": "b", "type": "linked", "text": "linked"}]
    return before, after, events


def _with_new_events(events, *new):
    """`events` as the server's ring actually stores it: NEWEST FIRST.

    server/server.py:1488 appends with `appendleft` and :1481 dumps
    `list(self._recent_events)`, so events.json is newest-first and the rows a reconnect added
    are the FRONT of the list. Ordering the fixture the other way is what made a tail slice
    look like "the new rows".
    """
    return list(new) + list(events)


def test_same_save_reconnect_keeps_identity_link_and_event_counts():
    before, after, events = _reconnect_snapshots()
    resumed = _with_new_events(events, {"player": "a", "type": "hello",
                                        "text": "Connected (Red, 2 mons)"})
    assert duo.reconnect_same_problems(before, after, events, resumed, "AAAA:1111:01", "1234") == []
    duplicated = _with_new_events(resumed, {"player": "a", "type": "capture", "text": "duplicate"})
    assert any("count changed" in p for p in duo.reconnect_same_problems(
        before, after, events, duplicated, "AAAA:1111:01", "1234"))
    mutated = {**after, "links": []}
    assert any("link changed" in p for p in duo.reconnect_same_problems(
        before, mutated, events, resumed, "AAAA:1111:01", "1234"))


def test_a_hello_spliced_into_the_history_is_not_a_reconnect():
    """Net +1 hello is not enough: the new hello has to be the newest row.

    A log that grew by one hello spliced into the middle keeps the counts identical, which is
    what a rewritten or replayed log produces. The rows carry distinct timestamps (server.py
    `_log_event` stamps `ts`), so a second hello an hour old is a different row from the one
    this reconnect just sent.
    """
    before, after, events = _reconnect_snapshots()
    spliced = [events[0], {"player": "a", "type": "hello", "text": "Connected (Red, 2 mons)",
                           "ts": "2026-09-17T11:00:07"}, *events[1:]]
    problems = duo.reconnect_same_problems(before, after, events, spliced, "AAAA:1111:01", "1234")
    assert any("shrank or was rewritten" in p for p in problems), problems

    _b, _a, events = _reconnect_snapshots()
    status = {"players": {"a": {"identity_error": "Identity mismatch for slot A: wrong OT"},
                          "b": {"connected": True}}}
    wrong_spliced = [events[0], {"player": "a", "type": "hello",
                                 "text": "REJECTED — wrong save/slot",
                                 "ts": "2026-09-17T11:00:09"}, *events[1:]]
    assert duo.reconnect_wrong_problems(b"same", b"same", status, events, wrong_spliced)


def test_two_new_accepted_hellos_are_not_one_reconnect():
    before, after, events = _reconnect_snapshots()
    doubled = _with_new_events(events, {"player": "a", "type": "hello",
                                        "text": "Connected (Red, 2 mons)"},
                               {"player": "a", "type": "hello",
                                "text": "Connected (Red, 2 mons)"})
    problems = duo.reconnect_same_problems(before, after, events, doubled, "AAAA:1111:01", "1234")
    assert any("exactly one accepted reconnect hello" in p for p in problems), problems


def test_gen3_reconnect_accepts_capability_refreshes_only_when_all_hellos_are_accepted():
    """The durable Gen 3 client may refresh HELLO after native capability settles."""
    before, after, events = _reconnect_snapshots()
    refreshed = _with_new_events(events, *[
        {"player": "a", "type": "hello", "text": "Connected (firered_rr, 2 mons)"}
        for _ in range(3)
    ])
    assert duo.reconnect_same_problems(before, after, events, refreshed,
                                       "AAAA:1111:01", "1234",
                                       allow_accepted_refreshes=True) == []
    rejected = _with_new_events(refreshed,
                                {"player": "a", "type": "hello", "text": "REJECTED — wrong OT"})
    problems = duo.reconnect_same_problems(before, after, events, rejected,
                                           "AAAA:1111:01", "1234",
                                           allow_accepted_refreshes=True)
    assert any("accepted reconnect hello" in p for p in problems), problems


def test_a_log_at_the_entry_cap_still_finds_the_new_hello():
    """events.json is capped at 200 rows (server.py:79), so a reconnect at the cap drops the
    OLDEST row: the survivors are a prefix of the old list, not the whole of it."""
    before, after, _events = _reconnect_snapshots()
    old = [{"player": "b", "type": "area_enter", "text": f"row {i}"} for i in range(200)]
    after_events = _with_new_events(old[:-1], {"player": "a", "type": "hello",
                                               "text": "Connected (Red, 2 mons)"})
    assert len(after_events) == 200
    assert duo.reconnect_same_problems(before, after, old, after_events,
                                       "AAAA:1111:01", "1234") == []


def test_wrong_save_reconnect_only_adds_a_rejected_hello():
    _before, _after, events = _reconnect_snapshots()
    rejected = _with_new_events(events, {"player": "a", "type": "hello",
                                         "text": "REJECTED — wrong save/slot"})
    status = {"players": {"a": {"identity_error": "Identity mismatch for slot A: wrong OT"},
                          "b": {"connected": True}}}
    assert duo.reconnect_wrong_problems(b"unchanged", b"unchanged", status, events, rejected) == []
    assert any("bytes changed" in p for p in duo.reconnect_wrong_problems(
        b"before", b"after", status, events, rejected))
    assert any("a rejected save changed a capture/linked/no_catch/dead_zone count" in p
               for p in duo.reconnect_wrong_problems(
                   b"same", b"same", status, events,
                   _with_new_events(rejected, {"player": "a", "type": "no_catch", "text": "bad"})))


def test_gen3_wrong_save_allows_only_rejected_capability_refreshes():
    _before, _after, events = _reconnect_snapshots()
    status = {"players": {"a": {"identity_error": "Identity mismatch for slot A: wrong OT"},
                          "b": {"connected": True}}}
    rejected = _with_new_events(events, *[
        {"player": "a", "type": "hello", "text": "REJECTED — wrong save/slot"}
        for _ in range(3)
    ])
    assert duo.reconnect_wrong_problems(b"same", b"same", status, events, rejected,
                                        allow_rejected_refreshes=True) == []
    accepted = _with_new_events(rejected,
                                {"player": "a", "type": "hello", "text": "Connected (firered_rr, 2 mons)"})
    assert duo.reconnect_wrong_problems(b"same", b"same", status, events, accepted,
                                        allow_rejected_refreshes=True)
    assert duo.reconnect_wrong_problems(b"same", b"same", status, events, events,
                                        allow_rejected_refreshes=True)


def test_missing_second_ot_red_save_is_named_and_nonpassing(runner, tmp_path):
    runner._pydec_path = str(tmp_path / "reconnect_pydec.txt")
    runner._wrong_save_missing()
    assert runner._live_complete["reconnect_new"] is False
    assert "WRONG_SAVE_LEG NOT RUN" in Path(runner._pydec_path).read_text(encoding="utf-8")


# ── the harness's own --idle-jitter contract (A0-H1 part 3) ──────────────────────────────
# The harness writes `idle_jitter` into each stub's SLINK_DUO table; the driver echoes back
# what it requested, what it applied and which attempt it was. Only the echo is checkable
# without an emulator, and a mismatch is a harness finding — a retry that did not actually vary
# its timing is not the different-RNG retry the plan's retry rule claims to have run.


@pytest.mark.parametrize("text", ["RESULT: PASS\n", "", "JITTER requested=0 attempt=1\n"])
def test_jitter_marker_missing_is_a_finding(text):
    assert duo.jitter_problems(text, 0) == ["JITTER marker missing"]


@pytest.mark.parametrize(("text", "expected"), [
    (_JITTER_1.replace("applied=0", "applied=4"), 0),   # the driver did not apply its jitter
    (_JITTER_2, 0),                                     # attempt 2's jitter on an attempt-1 run
    (_JITTER_1, 37),                                    # a stale echo from the previous attempt
])
def test_jitter_that_is_not_what_the_harness_wrote_is_a_finding(text, expected):
    assert duo.jitter_problems(text, expected) == ["JITTER applied != requested"]


@pytest.mark.parametrize(("text", "expected"), [("RESULT: PASS\n" + _JITTER_1, 0), (_JITTER_2, 37)])
def test_jitter_matching_the_harness_value_passes(text, expected):
    assert duo.jitter_problems(text, expected) == []


def _gen1_client_receipt(run, inst, text):
    digest = hashlib.sha1((Path(duo.REPO) / run._rom_for(inst)).read_bytes()).hexdigest()
    title = run.gcfg["fixture"][inst]
    return (f"client built: title={title} pack=gen1_rby kind=named player={inst} "
            f"rom={digest[:8]} -> 127.0.0.1:54321\n{text}")

def test_a_gen1_new_run_without_the_jitter_echo_is_a_harness_finding(runner):
    """No echo means the check did not run; a silent pass would be the harness lying."""
    runner.scenario = "reconnect_new"
    runner.game = "gen1_new"
    runner.attempt = 1
    runner._live_complete = {"reconnect_new": True}
    runner.start_server = lambda: None
    runner.start_instances = lambda: None
    runner.orchestrate = lambda: None
    runner.wait_results = lambda: tuple(_gen1_client_receipt(runner, inst, "RESULT: PASS")
                                        for inst in ("a", "b"))
    runner.cleanup = lambda passed: None
    runner.args = type("Args", (), {"keep_alive": False, "idle_jitter": 0})()
    with pytest.raises(RuntimeError, match="JITTER marker missing"):
        runner.run()


def test_a_gen1_new_run_whose_echo_matches_is_unaffected(runner):
    runner.scenario = "reconnect_new"
    runner.game = "gen1_new"
    runner.attempt = 1
    runner._live_complete = {"reconnect_new": True}
    runner.start_server = lambda: None
    runner.start_instances = lambda: None
    runner.orchestrate = lambda: None
    runner.wait_results = lambda: tuple(_gen1_client_receipt(runner, inst, f"RESULT: PASS\n{_JITTER_1}")
                                        for inst in ("a", "b"))
    runner.cleanup = lambda passed: None
    runner.args = type("Args", (), {"keep_alive": False, "idle_jitter": 0})()
    assert runner.run() is True


def test_the_stub_carries_the_attempt_scaled_jitter(runner, tmp_path, monkeypatch):
    """Attempt 1 writes the CLI value; attempt 2 writes it + 37, and the echo must agree."""
    monkeypatch.setattr("gen1_playthrough.write_run_config", lambda *_a, **_k: None)
    # These tests assert stub/lua content and seeding, never the ROM path itself; staged_rom
    # only needs to resolve to SOME string so launch_instance's Popen argv can be built. Real
    # cartridge dumps are a dev-box artifact, not present on a clean checkout.
    monkeypatch.setattr("gen1_playthrough.staged_rom", lambda *_a, **_k: "fake.gb")
    monkeypatch.setattr(duo.subprocess, "Popen", lambda *_a, **_k: type("P", (), {"pid": 42})())
    runner.cfg = duo.SCENARIOS["link_new"]
    runner.gcfg = dict(duo.GAMES["gen1_new"])
    runner.battery_boot = True
    runner.tcp_port = 1234
    runner.go_files = {inst: str(tmp_path / f"{inst}.go") for inst in ("a", "b")}
    runner.emus, runner.emu_by_inst = [], {}
    runner.args = type("Args", (), {"idle_jitter": 11})()
    Path(duo.BUILD).mkdir(parents=True, exist_ok=True)

    for attempt, expected in ((1, 11), (2, 48)):
        runner.attempt = attempt
        runner.launch_instance("a", seed=False)
        stub = Path(runner.stub_path("a")).read_text(encoding="utf-8")
        assert f"idle_jitter = {expected}," in stub
        assert f"attempt = {attempt}," in stub


def test_the_stub_carries_the_scenario_timeout(runner, tmp_path, monkeypatch):
    """The bodies that wait on a partner with a bounded loop read it (poison_new's A half:
    `D.timeout_secs or 2400`), so the harness has to write it into every stub."""
    monkeypatch.setattr("gen1_playthrough.write_run_config", lambda *_a, **_k: None)
    # These tests assert stub/lua content and seeding, never the ROM path itself; staged_rom
    # only needs to resolve to SOME string so launch_instance's Popen argv can be built. Real
    # cartridge dumps are a dev-box artifact, not present on a clean checkout.
    monkeypatch.setattr("gen1_playthrough.staged_rom", lambda *_a, **_k: "fake.gb")
    monkeypatch.setattr(duo.subprocess, "Popen", lambda *_a, **_k: type("P", (), {"pid": 42})())
    runner.cfg = duo.SCENARIOS["poison_new"]
    runner.gcfg = dict(duo.GAMES["gen1_new"])
    runner.battery_boot = True
    runner.tcp_port = 1234
    runner.go_files = {inst: str(tmp_path / f"{inst}.go") for inst in ("a", "b")}
    runner.emus, runner.emu_by_inst = [], {}
    runner.args = type("Args", (), {"idle_jitter": 0})()
    runner.attempt = 1
    Path(duo.BUILD).mkdir(parents=True, exist_ok=True)
    runner.launch_instance("a", seed=False)
    stub = Path(runner.stub_path("a")).read_text(encoding="utf-8")
    assert f"timeout_secs = {runner.cfg['timeout']}," in stub


def test_reconnect_cannot_pass_on_two_client_passes_when_wrong_save_leg_was_not_run(runner):
    runner.scenario = "reconnect_new"
    runner.game = "gen1_new"
    runner.attempt = 1
    runner.start_server = lambda: None
    runner.start_instances = lambda: None
    runner.orchestrate = lambda: runner._live_complete.update(reconnect_new=False)
    runner.wait_results = lambda: tuple(_gen1_client_receipt(runner, inst, f"RESULT: PASS\n{_JITTER_1}")
                                        for inst in ("a", "b"))
    runner.cleanup = lambda passed: None
    runner.args = type("Args", (), {"keep_alive": False, "idle_jitter": 0})()
    assert runner.run() is False


def test_a_only_relaunch_does_not_reseed_the_flushed_save(runner, tmp_path, monkeypatch):
    runner.scenario = "reconnect_new"
    runner.cfg = duo.SCENARIOS[runner.scenario]
    runner.battery_boot = True
    runner.tcp_port = 1234
    runner.attempt = 1
    runner.go_files = {"a": str(tmp_path / "a.go"), "b": str(tmp_path / "b.go")}
    runner.emus, runner.emu_by_inst = [], {}
    runner.args = type("Args", (), {"idle_jitter": 0})()
    Path(duo.BUILD).mkdir()
    runner._seed_instance_save = lambda _inst: pytest.fail("relaunch reseeded SaveRAM")
    monkeypatch.setattr("gen1_playthrough.write_run_config", lambda *_args, **_kw: None)
    # These tests assert stub/lua content and seeding, never the ROM path itself; staged_rom
    # only needs to resolve to SOME string so launch_instance's Popen argv can be built. Real
    # cartridge dumps are a dev-box artifact, not present on a clean checkout.
    monkeypatch.setattr("gen1_playthrough.staged_rom", lambda *_args, **_kw: "fake.gb")

    class FakeProcess:
        pid = 42

    monkeypatch.setattr(duo.subprocess, "Popen", lambda *_args, **_kw: FakeProcess())
    runner.launch_instance("a", phase="same_save", seed=False, expected_key="AAAA:1111:01")
    stub = Path(runner.stub_path("a")).read_text(encoding="utf-8")
    assert 'phase = "same_save"' in stub and 'expected_key = "AAAA:1111:01"' in stub
    assert runner.emu_by_inst["a"].pid == 42 and len(runner.emus) == 1


def test_reconnect_kills_only_a_and_leaves_b_process_running(runner, monkeypatch):
    calls = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def poll(self):
            return None

        def wait(self, timeout):
            calls.append(("wait", self.pid, timeout))

    runner.emu_by_inst = {"a": Process(41), "b": Process(42)}
    monkeypatch.setattr(duo.subprocess, "run", lambda cmd, **_kwargs: calls.append(tuple(cmd)))
    runner.terminate_instance("a")
    assert calls == [("taskkill", "/PID", "41", "/T", "/F"), ("wait", 41, 15)]


def test_existing_red_town_is_not_a_second_ot_save():
    town = REPO / "tests/fixtures/gen1/red_town.SaveRAM"
    battle = REPO / "tests/fixtures/gen1/red_battle.SaveRAM"
    if not town.exists() or not battle.exists() or not (REPO / "patch/gen1/build/slink_red.gb").exists():
        pytest.skip("Red town/battle saves or the companion Red build absent")
    profile = json.loads((REPO / "data/games/gen1_rby/profile.json").read_text(
        encoding="utf-8"))["titles"]["red"]["ram"]
    offset = codec.SRAM_LAYOUT["sMainData"] + profile["wPlayerID"] - profile["wMainDataStart"]
    old_ot = int.from_bytes(battle.read_bytes()[offset:offset + 2], "big")
    assert int.from_bytes(town.read_bytes()[offset:offset + 2], "big") == old_ot
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.gcfg = duo.GAMES["gen1_new"]
    with pytest.raises(RuntimeError, match="original OT"):
        run._wrong_red_save_ot(str(town), old_ot)


def _ball_status(active=False, balls=0):
    return {"players": {inst: {"connected": True, "nuzlocke_active": active,
                               "ball_count": balls} for inst in ("a", "b")}}


def _ball_hellos():
    return {inst: {"ot_id": ot, "map": 0x26, "party_count": 0,
                   "has_pokeballs": False, "ball_count": 0}
            for inst, ot in (("a", 0x4190), ("b", 0xAFB9))}


def test_ball_gate_cold_hellos_require_distinct_ids_zero_balls_and_no_link_yet():
    hellos = _ball_hellos()
    assert duo.ball_gate_pre_problems(_ball_status(), [], [], hellos) == []
    same_ot = {**hellos, "b": {**hellos["b"], "ot_id": hellos["a"]["ot_id"]}}
    assert any("not distinct" in p for p in duo.ball_gate_pre_problems(
        _ball_status(), [], [], same_ot))
    assert any("before the first Poké Ball" in p for p in duo.ball_gate_pre_problems(
        _ball_status(), [{"status": "alive"}], [], hellos))
    assert any("gate opened" in p for p in duo.ball_gate_pre_problems(
        _ball_status(active=True), [], [], hellos))


def test_ball_gate_gift_link_forms_before_balls_but_does_not_open_the_gate():
    link = {"area_id": "gift_map_40", "status": "alive",
            "a": {"key": "AAAA:4190:99"}, "b": {"key": "BBBB:AFB9:B0"}}
    pre = {inst: {"key": link[inst]["key"], "capture_count": 1, "gift": True,
                  "ball_count": 0} for inst in ("a", "b")}
    events = [{"player": inst, "type": "linked"} for inst in ("a", "b")]
    assert duo.ball_gate_starters_problems(_ball_status(), [link], events, pre) == []
    assert any("server ball gate opened" in p for p in duo.ball_gate_starters_problems(
        _ball_status(active=True), [link], events, pre))
    assert any("wild encounter" in p for p in duo.ball_gate_starters_problems(
        _ball_status(), [link], events + [{"player": "a", "type": "no_catch"}], pre))


def test_ball_gate_lab_loss_preserves_gift_pair_and_partner_hp():
    link = {"area_id": "gift_map_40", "status": "alive"}
    labs = {inst: {"result": 1, "hp": 19, "faint_count": 1, "ball_count": 0,
                   "has_pokeballs": False, "force_faint": 0, "memorialize": 0}
            for inst in ("a", "b")}
    pre = {"b": {"hp": 20}}
    release = {"b": {"hp": 20, "force_faint": 0, "memorialize": 0}}
    events = ([{"player": inst, "type": "linked"} for inst in ("a", "b")]
              + [{"player": inst, "type": "faint"} for inst in ("a", "b")])
    log = "[a] faint key=AAAA\n[b] faint key=BBBB\n"
    assert duo.ball_gate_lab_problems(_ball_status(), [link], link, events, labs, pre, release, log) == []
    altered = {"b": {**release["b"], "hp": 0, "force_faint": 1}}
    problems = duo.ball_gate_lab_problems(_ball_status(), [link], link, events, labs, pre, altered, log)
    assert any("starter HP changed" in p for p in problems)
    assert any("death command" in p for p in problems)
    assert any("starter pair changed" in p for p in duo.ball_gate_lab_problems(
        _ball_status(), [], link, events, labs, pre, release, log))
    after = {inst: {"hp": 19, "force_faint": 0, "memorialize": 0}
             for inst in ("a", "b")}
    assert duo.ball_gate_after_labs_problems(labs, after) == []
    changed = {**after, "a": {**after["a"], "hp": 0}}
    assert any("starter HP changed" in p for p in duo.ball_gate_after_labs_problems(
        labs, changed))


def test_ball_gate_flip_requires_bag_signal_client_bit_and_server_bit():
    flips = {inst: {"signal_count": 1, "ball_count": 1, "has_pokeballs": True}
             for inst in ("a", "b")}
    assert duo.ball_gate_flip_problems(_ball_status(active=True, balls=1), flips) == []
    assert any("server did not activate" in p for p in duo.ball_gate_flip_problems(
        _ball_status(), flips))
    no_site = {**flips, "b": {**flips["b"], "signal_count": 0}}
    assert any("bag_received" in p for p in duo.ball_gate_flip_problems(
        _ball_status(active=True, balls=1), no_site))


def test_ball_gate_receipt_must_have_exactly_one_structured_milestone():
    text = 'BALL_HELLO {"ot_id":16784}\n'
    assert duo.ball_gate_fact(text, "BALL_HELLO") == {"ot_id": 16784}
    with pytest.raises(RuntimeError, match="got 2"):
        duo.ball_gate_fact(text + text, "BALL_HELLO")


def test_cold_ball_gate_uses_a_fresh_save_directory_without_seeding(runner, tmp_path, monkeypatch):
    runner.scenario = "ball_gate_new"
    runner.cfg = duo.SCENARIOS[runner.scenario]
    runner.battery_boot = True
    runner.tcp_port = 1234
    runner.attempt = 1
    runner.go_files = {inst: str(tmp_path / f"{inst}.go") for inst in ("a", "b")}
    runner.emus, runner.emu_by_inst = [], {}
    runner.args = type("Args", (), {"idle_jitter": 0})()
    Path(duo.BUILD).mkdir(exist_ok=True)
    runner._seed_instance_save = lambda _inst: pytest.fail("cold boot seeded a battery save")
    monkeypatch.setattr("gen1_playthrough.write_run_config", lambda *_args, **_kw: None)
    # These tests assert stub/lua content and seeding, never the ROM path itself; staged_rom
    # only needs to resolve to SOME string so launch_instance's Popen argv can be built. Real
    # cartridge dumps are a dev-box artifact, not present on a clean checkout.
    monkeypatch.setattr("gen1_playthrough.staged_rom", lambda *_args, **_kw: "fake.gb")

    class FakeProcess:
        pid = 42

    monkeypatch.setattr(duo.subprocess, "Popen", lambda *_args, **_kw: FakeProcess())
    runner.launch_instance("a", seed=False)
    assert Path(runner._saveram_dir("a")).is_dir()
    assert list(Path(runner._saveram_dir("a")).iterdir()) == []
    assert "cold_boot = true" in Path(runner.stub_path("a")).read_text(encoding="utf-8")


def test_ball_gate_driver_failure_never_gets_rng_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    calls = []

    class FakeRun:
        def __init__(self, name, _args, attempt):
            calls.append((name, attempt))

        def run(self):
            return False

    monkeypatch.setattr(duo, "DuoRun", FakeRun)
    monkeypatch.setattr(duo, "read_result", lambda *_args: duo.RNG_OUT_OF_BALLS)
    args = type("Args", (), {"game": "gen1_new", "idle_jitter": 0})()
    assert duo.run_scenario_with_rng_retry("ball_gate_new", args) == (False, 1)
    assert calls == [("ball_gate_new", 1)]


@pytest.mark.parametrize(
    ("a", "b", "attempt", "expected"),
    [
        ("RESULT: PASS (caught)", duo.RNG_OUT_OF_BALLS, 1, True),
        (duo.RNG_OUT_OF_BALLS, duo.RNG_OUT_OF_BALLS, 1, True),
        ("RESULT: FAIL (linked capture was not returned)",
         "RESULT: FAIL (link_new prerequisite failed: hunt ended out-of-balls)", 1, True),
        ("RESULT: FAIL (linked capture was not returned to party)",
         "RESULT: FAIL (link_new prerequisite failed: hunt ended out-of-balls)", 1, True),
        ("RESULT: FAIL (linked capture was not returned)",
         "RESULT: FAIL (force_faint never arrived)", 1, False),
        ("RESULT: FAIL (force_faint never arrived)", duo.RNG_OUT_OF_BALLS, 1, False),
        ("RESULT: FAIL (linked capture was not returned)", "RESULT: PASS (caught)", 1, False),
        ("RESULT: PASS (caught)", "RESULT: FAIL (timeout)", 1, False),
        (duo.RNG_OUT_OF_BALLS, "RESULT: FAIL (timeout)", 1, False),
        ("RESULT: PASS (caught)", duo.RNG_OUT_OF_BALLS, 2, False),
        ("RESULT: FAIL (linked capture was not returned)",
         "RESULT: FAIL (link_new prerequisite failed: hunt ended out-of-balls)", 2, False),
        # poison_new's two forest legs: the poisoning is a race between the wild table and the
        # starter's HP, so both are the game's RNG. Other poison leg failures are NOT.
        ("RESULT: PASS (caught)",
         "RESULT: FAIL (RNG: the forest hunt spent its encounter budget without a poisoning)",
         1, True),
        ("RESULT: PASS (caught)",
         "RESULT: FAIL (RNG: a wild foe knocked the starter out before the poisoning)", 1, True),
        ("RESULT: PASS (caught)", "RESULT: FAIL (the poison shuttle ended poison-fainted)",
         1, False),
        ("RESULT: PASS (caught)", "RESULT: FAIL (the walk to Viridian Forest ended unknown-map)",
         1, False),
    ],
)
def test_rng_retry_predicate_accepts_only_the_game_ball_miss(a, b, attempt, expected):
    assert duo.retryable_gen1_rng("gen1_new", {"a": a, "b": b}, attempt) is expected
    assert duo.retryable_gen1_rng("gen1", {"a": a, "b": b}, attempt) is False


@pytest.mark.parametrize(("line", "classification"), [
    ("RESULT: FAIL (hunt ended out-of-balls)", "CAUSE_RNG"),
    ("RESULT: FAIL (link_new prerequisite failed: hunt ended out-of-balls)", "CAUSE_RNG"),
    ("RESULT: FAIL (hunt ended first-catch-battle-lost)", "CAUSE_RNG"),
    ("RESULT: FAIL (link_new prerequisite failed: hunt ended first-catch-battle-lost)", "CAUSE_RNG"),
    ("RESULT: FAIL (linked capture was not returned)", "CONSEQUENCE"),
    ("RESULT: FAIL (linked capture was not returned to party)", "CONSEQUENCE"),
    ("RESULT: FAIL (force_faint never arrived)", "FINAL"),
    ("RESULT: FAIL (B could not hold its linked mon active: out-of-balls)", "FINAL"),
    ("RESULT: FAIL (link_new prerequisite failed: hunt ended stuck)", "FINAL"),
    ("RESULT: FAIL (RNG: the forest hunt spent its encounter budget without a poisoning)",
     "CAUSE_RNG"),
    ("RESULT: FAIL (RNG: a wild foe knocked the starter out before the poisoning)", "CAUSE_RNG"),
    # the same legs' non-RNG terminals stay FINAL
    ("RESULT: FAIL (the forest hunt ended hunt-stuck)", "FINAL"),
    # The route module's new terminal: an unexpected battle is a driver fault, not the game's
    # RNG, so it must NOT earn a retry (the reason carries a parenthetical, hence FINAL).
    ("RESULT: FAIL (the forest hunt ended unexpected-battle (wIsInBattle=1 party_hp=12))",
     "FINAL"),
    ("RESULT: FAIL (the walk to Viridian Forest ended unknown-map)", "FINAL"),
    ("RESULT: FAIL (the poison shuttle ended unknown-map)", "FINAL"),
])
def test_gen1_result_reason_table_is_exact(line, classification):
    assert duo.classify_gen1_result(line) == classification


@pytest.mark.parametrize(("other", "retry"), [
    ("RESULT: PASS (caught)\n", True),
    ("RESULT: FAIL (linked capture was not returned)\n", True),
    (None, True),
    ("RESULT: FAIL (force_faint never arrived)\n", False),
])
def test_first_catch_loss_uses_only_the_existing_fresh_attempt_budget(other, retry):
    cause = "RESULT: FAIL (link_new prerequisite failed: hunt ended first-catch-battle-lost)\n"
    receipts = {"a": other, "b": cause}
    name = "linked_faint_bench_battle_new"
    assert duo.scenario_attempt_limit(name, "gen1_pure") == 3
    assert duo.retryable_gen1_rng("gen1_pure", receipts, 1, 3, scenario=name) is retry
    assert duo.retryable_gen1_rng("gen1_pure", receipts, 2, 3, scenario=name) is retry
    assert not duo.retryable_gen1_rng("gen1_pure", receipts, 3, 3, scenario=name)


def test_the_poison_rng_phrases_are_the_bodies_own_return_strings():
    """The table has to match the body VERBATIM: a reworded Lua return would silently turn a
    real failure into a whole-run retry (or the reverse), and nothing else would notice."""
    body = (REPO / "lua" / "tests" / "duo" / "duo_gen1_main.lua").read_text(encoding="utf-8")
    for phrase in ("RNG: the forest hunt spent its encounter budget without a poisoning",
                   "RNG: a wild foe knocked the starter out before the poisoning"):
        assert duo.GEN1_RNG_REASON_CLASS[phrase] == "CAUSE_RNG"
        assert f'return false, "{phrase}"' in body, phrase


@pytest.mark.parametrize(("outcomes", "expected_attempts", "passed"), [
    ([(False, "RESULT: PASS (caught)", duo.RNG_OUT_OF_BALLS),
      (True, "RESULT: PASS (caught)", "RESULT: PASS (caught)")], 2, True),
    ([(False, "RESULT: PASS (caught)", "RESULT: FAIL (timeout)")], 1, False),
    ([(False, duo.RNG_OUT_OF_BALLS, duo.RNG_OUT_OF_BALLS),
      (False, duo.RNG_OUT_OF_BALLS, duo.RNG_OUT_OF_BALLS),
      (False, duo.RNG_OUT_OF_BALLS, duo.RNG_OUT_OF_BALLS)], 3, False),
])
def test_rng_retry_restarts_a_whole_run_once_with_labeled_receipts(
    tmp_path, monkeypatch, capsys, outcomes, expected_attempts, passed,
):
    monkeypatch.setattr(duo, "BUILD", str(tmp_path))
    built = []
    active = {"attempt": 0}

    class FakeRun:
        def __init__(self, name, args, attempt):
            built.append((name, attempt, object()))  # every attempt is a distinct run
            active["attempt"] = attempt

        def run(self):
            return outcomes[active["attempt"] - 1][0]

    monkeypatch.setattr(duo, "DuoRun", FakeRun)
    monkeypatch.setattr(duo, "read_result", lambda _name, inst: outcomes[active["attempt"] - 1][
        1 if inst == "a" else 2])
    args = type("Args", (), {"game": "gen1_new", "idle_jitter": 0})()
    assert duo.run_scenario_with_rng_retry("link_new", args) == (passed, expected_attempts)
    assert [attempt for _, attempt, _ in built] == list(range(1, expected_attempts + 1))
    assert len({id(run) for _, _, run in built}) == expected_attempts
    assert [line for line in capsys.readouterr().out.splitlines() if "JITTER" in line] == [
        f"[duo] JITTER requested={duo.jitter_for_attempt(0, attempt)} attempt={attempt}"
        for attempt in range(1, expected_attempts + 1)]


@pytest.mark.parametrize(("other", "raises"), [
    ("RESULT: PASS (caught)", False),
    ("RESULT: FAIL (unrelated)", True),
])
def test_early_orchestration_rng_miss_exits_without_waiting_for_both_catches(runner, other, raises):
    runner.scenario = "link_new"
    runner.game = "gen1_new"
    runner.attempt = 1
    runner.start_server = lambda: None
    runner.start_instances = lambda: None
    runner.orchestrate = lambda: (_ for _ in ()).throw(duo.GameRngMiss("sole ball missed"))
    runner.wait_results = lambda: (other + "\n" + _JITTER_1, duo.RNG_OUT_OF_BALLS + "\n" + _JITTER_1)
    runner.cleanup = lambda passed: None
    runner.args = type("Args", (), {"keep_alive": False, "idle_jitter": 0})()
    if raises:
        with pytest.raises(duo.GameRngMiss):
            runner.run()
    else:
        assert runner.run() is False


def _fixture_save(title):
    path = REPO / "tests" / "fixtures" / "gen1" / f"{title}_battle.SaveRAM"
    rom_path = REPO / "patch" / "build" / f"gen1_{title}.gb"
    if not path.exists() or not rom_path.exists():
        pytest.skip(f"{title} battle fixture or clean dump absent")
    return bytearray(path.read_bytes()), rom_path.read_bytes()


def _new_mon_from_starter(sram, rom):
    start = codec.SRAM_LAYOUT["sPartyData"] + codec.PARTY_LAYOUT["mons"]
    blob = bytearray(sram[start:start + codec.PARTY_MON_SIZE])
    old_dv = int.from_bytes(blob[27:29], "big")
    blob[27:29] = (old_dv ^ 0x0010).to_bytes(2, "big")
    mon = codec.decode_party_mon(blob)
    base = scan.scan_base_stats(rom)[codec.internal_to_natdex(mon["species"])]
    stats = codec.recompute_stats(mon, base)
    blob[1:3] = stats["max_hp"].to_bytes(2, "big")
    for index, name in enumerate(("max_hp", "atk", "def", "spd", "spc")):
        blob[34 + index * 2:36 + index * 2] = stats[name].to_bytes(2, "big")
    return bytes(blob)


def _seal_main(sram):
    start = codec.SRAM_LAYOUT["sPlayerName"]
    end = codec.SRAM_LAYOUT["sMainDataCheckSum"]
    sram[end] = codec.sav_checksum(sram[start:end])


def _add_caught_to_party(sram, rom):
    blob = _new_mon_from_starter(sram, rom)
    start = codec.SRAM_LAYOUT["sPartyData"]
    layout = codec.PARTY_LAYOUT
    sram[start] = 2
    sram[start + layout["species"] + 1] = blob[0]
    sram[start + layout["species"] + 2] = codec.SPECIES_END
    mon2 = start + layout["mons"] + codec.PARTY_MON_SIZE
    sram[mon2:mon2 + codec.PARTY_MON_SIZE] = blob
    for field in ("ot_names", "nicknames"):
        one = start + layout[field]
        sram[one + codec.NAME_SIZE:one + 2 * codec.NAME_SIZE] = sram[one:one + codec.NAME_SIZE]
    # The route pays for the catch: the fixture carries ONE Poke Ball and the saved bag has to
    # show it gone, which is exactly the baseline assert_link_new_saved compares against.
    sram[codec._BAG_COUNT] = 1
    sram[codec._BAG_COUNT + 1] = codec.POKE_BALL
    sram[codec._BAG_COUNT + 2] = 0
    _seal_main(sram)
    return codec.key(codec.decode_party_mon(blob))


def _put_fainted_in_box12(sram, rom):
    blob = bytearray(_new_mon_from_starter(sram, rom))
    blob[1:3] = b"\x00\x00"
    layout = codec.BOX_LAYOUT
    for info in codec.verify_boxes(sram)["boxes"].values():
        start = info["offset"]
        sram[start:start + codec.BOX_SIZE] = bytes(codec.BOX_SIZE)
        sram[start + layout["species"]] = codec.SPECIES_END
    start = codec.verify_boxes(sram)["boxes"][12]["offset"]
    sram[start] = 1
    sram[start + layout["species"]] = blob[0]
    sram[start + layout["species"] + 1] = codec.SPECIES_END
    sram[start + layout["mons"]:start + layout["mons"] + codec.BOX_MON_SIZE] = blob[:codec.BOX_MON_SIZE]
    party_start = codec.SRAM_LAYOUT["sPartyData"]
    for field in ("ot_names", "nicknames"):
        source = party_start + codec.PARTY_LAYOUT[field]
        target = start + layout[field]
        sram[target:target + codec.NAME_SIZE] = sram[source:source + codec.NAME_SIZE]
    sram[codec._CURRENT_BOX] |= codec._BOX_INITIALIZED
    for bank_index, bank_start in enumerate(codec.SRAM_LAYOUT["box_banks"]):
        for slot in range(6):
            box_start = bank_start + slot * codec.BOX_SIZE
            sram[codec.SRAM_LAYOUT["individual_checksums"][bank_index] + slot] = (
                codec.sav_checksum(sram[box_start:box_start + codec.BOX_SIZE]))
        end = codec.SRAM_LAYOUT["all_boxes_checksums"][bank_index]
        sram[end] = codec.sav_checksum(sram[bank_start:end])
    _seal_main(sram)
    return codec.key(codec.decode_party_mon(blob))


def _oracle_runner(tmp_path, scenario):
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario, run.game, run.attempt = scenario, "gen1_new", 1
    run.data_dir = str(tmp_path)
    run._pydec_path = str(tmp_path / "pydec.txt")
    run.cfg = duo.SCENARIOS[scenario]
    run.gcfg = duo.GAMES["gen1_new"]
    run._saveram_dir = lambda inst: str(tmp_path / f"save_{inst}")

    class Finished:
        def wait(self, timeout):
            assert timeout == 30

    run.emus = [Finished(), Finished()]
    paths = {}
    for inst in ("a", "b"):
        companion = Path(duo.REPO) / run._rom_for(inst)
        if not companion.is_file():
            pytest.skip(f"companion input absent: {companion}")
    for inst, title in (("a", "red"), ("b", "blue")):
        sram, rom = _fixture_save(title)
        if run.cfg.get("gen1_synth"):
            # The model starts from the same qualified/disclosed input as the live row.
            sram = bytearray(Path(run._prepare_gen1_explode_fixture(inst)).read_bytes())
        path = Path(run._saveram_dir(inst)) / run._gen1_save_name(inst)   # the companion's save, as the oracle reads it
        path.parent.mkdir(parents=True)
        paths[inst] = (path, sram, rom)
        start = codec.SRAM_LAYOUT["sPartyData"]
        run._boot_keys = getattr(run, "_boot_keys", {})
        run._boot_keys[inst] = codec.key(codec.decode_party(
            sram[start:start + codec.PARTY_LAYOUT["size"]])[0])
    return run, paths


def test_synthetic_link_saved_oracle_passes_and_rejects_a_torn_checksum(tmp_path, capsys):
    run, paths = _oracle_runner(tmp_path, "link_new")
    run._link_keys = {}
    for inst, (path, sram, rom) in paths.items():
        run._link_keys[inst] = _add_caught_to_party(sram, rom)
        path.write_bytes(sram)
    run.assert_link_new_saved({"a": "", "b": ""})
    assert "saved slot 1 linked" in capsys.readouterr().out
    path, sram, _rom = paths["a"]
    sram[codec.SRAM_LAYOUT["sMainDataCheckSum"]] ^= 1
    path.write_bytes(sram)
    with pytest.raises(RuntimeError, match="saved game would not qualify"):
        run.assert_link_new_saved({"a": "", "b": ""})
    print("synthetic link_new: PASS; torn main checksum: FAIL")


def test_synthetic_deadzone_saved_oracle_passes_and_rejects_uninitialized_box(tmp_path, capsys):
    run, paths = _oracle_runner(tmp_path, "deadzone_new")
    a_path, a_sram, _a_rom = paths["a"]
    a_path.write_bytes(a_sram)
    b_path, b_sram, b_rom = paths["b"]
    run._deadzone_b_key = _put_fainted_in_box12(b_sram, b_rom)
    b_path.write_bytes(b_sram)
    run.assert_dead_zone_new_saved({"a": "", "b": ""})
    assert "B Box 12 holds" in capsys.readouterr().out
    b_sram[codec._CURRENT_BOX] &= ~codec._BOX_INITIALIZED
    _seal_main(b_sram)
    b_path.write_bytes(b_sram)
    with pytest.raises(RuntimeError, match="initialized flag invalid"):
        run.assert_dead_zone_new_saved({"a": "", "b": ""})
    print("synthetic deadzone_new: PASS; cleared saved initialization flag: FAIL")


def _linked_faint_fixture(tmp_path, scenario):
    run, paths = _oracle_runner(tmp_path, scenario)
    run.data_dir = str(tmp_path)
    run._link_keys = {}
    hunts = {}
    for inst, (path, sram, rom) in paths.items():
        if run.cfg.get("gen1_synth"):
            balls = codec.bag_quantity(sram, codec.POKE_BALL)
            hunts[inst] = (f"[hunt] encounter 1 at (10,35) mode=catch balls={balls}\n"
                           f"[hunt] threw ball index 0 -> caught balls_before={balls} balls_after={balls-1}\n")
            sram[codec._BAG_COUNT + 2] = balls - 1  # model one native prerequisite throw
            _seal_main(sram)
        run._link_keys[inst] = _put_fainted_in_box12(sram, rom)
        path.write_bytes(sram)
    (tmp_path / "links.json").write_text(json.dumps({"links": [{
        "area_id": "route_1", "status": "memorial", "cause": "battle",
        "a": {"key": run._link_keys["a"]}, "b": {"key": run._link_keys["b"]},
    }]}), encoding="utf-8")
    (tmp_path / "slink.log").write_text(
        f"[a] faint → force_faint b:{run._link_keys['b']}\n"
        "pair in route_1 fully memorialized\n", encoding="utf-8")
    a_text = (f'BATTLE_FAINT_SITE {run._link_keys["a"]} slot=1 battle_hp=0\n'
              f'TX {{"event":"faint","key":"{run._link_keys["a"]}","player":"a"}}\n'
              f'RX memorialize key={run._link_keys["a"]}\n'
              'TX {"event":"memorialize_done"}\n')
    b_text = (f'RX force_faint key={run._link_keys["b"]}\n'
              f'RX memorialize key={run._link_keys["b"]}\n'
              'GAME_OVER RX game_over\n'
              'TX {"event":"memorialize_done"}\n')
    return run, paths, hunts.get("a", "") + a_text, hunts.get("b", "") + b_text


def test_synthetic_bench_faint_oracle_passes_then_rejects_status_corruption(tmp_path, capsys):
    run, paths, a_text, b_text = _linked_faint_fixture(tmp_path, "linked_faint_bench_new")
    results = {"a": a_text,
               "b": b_text + "READY_BENCH map=12 x=8 y=31\nBENCH_HP_STATUS 0000 00\nTILEMAP_FNT row=2\n"}
    run.assert_linked_faint_saved(results, active=False)
    assert "Box 12" in capsys.readouterr().out
    fast_memorial = {"a": a_text, "b": b_text + "READY_BENCH map=12 x=8 y=31\n"
                     "BENCH_HP_STATUS 0000 00\n"
                     "TILEMAP_FNT unavailable: memorialised within 1 frames of the faint\n"}
    run.assert_linked_faint_saved(fast_memorial, active=False)
    path, sram, _rom = paths["b"]
    box = codec.verify_boxes(sram)["boxes"][12]["offset"]
    bank_index = 1

    def rewrite_status(value):
        sram[box + codec.BOX_LAYOUT["mons"] + 4] = value  # status, not HP; preserve structure
        sram[codec.SRAM_LAYOUT["individual_checksums"][bank_index] + 5] = (
            codec.sav_checksum(sram[box:box + codec.BOX_SIZE]))
        bank = codec.SRAM_LAYOUT["box_banks"][bank_index]
        end = codec.SRAM_LAYOUT["all_boxes_checksums"][bank_index]
        sram[end] = codec.sav_checksum(sram[bank:end])
        path.write_bytes(sram)

    # $80 is RemoveFaintedPlayerMon's low-health-alarm artifact (pokered
    # engine/battle/core.asm:1011-1023 + home/delay.asm:15-18), not a Gen 1 ailment bit: accepted.
    rewrite_status(0x80)
    run.assert_linked_faint_saved(results, active=False)
    rewrite_status(1)  # one turn of SLP is a real ailment and still fails
    with pytest.raises(RuntimeError, match="HP0000/status00"):
        run.assert_linked_faint_saved(results, active=False)
    print("synthetic bench faint: PASS; engine $80 accepted; boxed status corruption: FAIL")


def test_synthetic_active_faint_oracle_requires_loop_write_before_engine_site(tmp_path, capsys):
    run, _paths, a_text, b_text = _linked_faint_fixture(tmp_path, "linked_faint_active_new")
    proper = (b_text + "LOOP_HEAD_WRITE key=" + run._link_keys["b"] + "\n"
              + "BATTLE_FAINT_SITE " + run._link_keys["b"] + "\n"
              + f'TX {{"event":"faint","key":"{run._link_keys["b"]}"}}\n'
              + "TILEMAP_FAINTED offset=123\nBATTLE_RESULT b 2\n")
    run.assert_linked_faint_saved({"a": a_text, "b": proper}, active=True)
    assert "Box 12" in capsys.readouterr().out
    run.assert_linked_faint_saved({"a": a_text, "b": proper.replace(
        "TILEMAP_FAINTED offset=123\n", "TILEMAP_FAINTED unavailable: native faint text advanced before probe\n")},
        active=True)
    reversed_order = (b_text + "BATTLE_FAINT_SITE " + run._link_keys["b"] + "\n"
                      + "LOOP_HEAD_WRITE key=" + run._link_keys["b"] + "\n"
                      + f'TX {{"event":"faint","key":"{run._link_keys["b"]}"}}\n'
                      + "TILEMAP_FAINTED offset=123\nBATTLE_RESULT b 2\n")
    with pytest.raises(RuntimeError, match="not followed by engine battle_faint"):
        run.assert_linked_faint_saved({"a": a_text, "b": reversed_order}, active=True)
    print("synthetic active faint: PASS; reversed loop/engine order: FAIL")


@pytest.mark.parametrize("cmd", ["force_faint", "force_explode"])
def test_synthetic_bench_battle_oracle_requires_the_write_inside_the_battle(tmp_path, cmd):
    scenario = "explode_bench_battle_new" if cmd == "force_explode" else "linked_faint_bench_battle_new"
    run, _paths, a_text, b_text = _linked_faint_fixture(tmp_path, scenario)
    key = run._link_keys["b"]
    (tmp_path / "slink.log").write_text(
        f"[a] faint → {cmd} b:{key}\npair in route_1 fully memorialized\n", encoding="utf-8")
    b_text = b_text.replace(f"RX force_faint key={key}\n", "")
    ready = "READY_BENCH_BATTLE linked_slot=1 active_slot=0 in_battle=1 bench_hp=000F active_hp=0014 moves=21270000\n"
    rx = f"RX {cmd} key={key} in_battle=1\n"
    split = "EXPLODE_CMDS force_explode=1 force_faint=0\n" if cmd == "force_explode" else ""
    zero = (f"BENCH_ZERO_ON_RX key={key} cmd={cmd} in_battle=1 bench_hp=0000 status=00 frames=0 "
            "loop_heads=0 active_slot=0\n")
    write = (f"LOOP_HEAD_BENCH_SETTLED key={key} cmd={cmd} in_battle=1 bench_hp=0000 status=00 "
             "hp_before=0 landed=true moved=false active_slot=0 active_hp=0014 moves=21270000\n")
    readback = ("BENCH_HP_STATUS_IN_BATTLE 0000 00 in_battle=1 active_slot=0 active_hp=0014->0014 "
                "starter_hp=0014->0014 moves=21270000->21270000\n")
    end = "BATTLE_RESULT b 1\n"
    kwargs = {"active": False, "bench_battle": True, "explode": cmd == "force_explode"}
    good = ready + rx + split + zero + write + readback + end + b_text
    run.assert_linked_faint_saved({"a": a_text, "b": good}, **kwargs)
    # The checkpoint-deferring client: no receipt zero, no loop-head backstop, only the post-battle zero.
    with pytest.raises(RuntimeError, match="bench zero on receipt"):
        run.assert_linked_faint_saved({"a": a_text, "b": ready + rx + split + end + b_text}, **kwargs)
    # The loop-head-only client (6a8958fb): the loop head is what zeroed the slot.
    loop_only = (f"LOOP_HEAD_BENCH_SETTLED key={key} cmd={cmd} in_battle=1 bench_hp=0000 status=00 "
                 "hp_before=15 landed=false moved=true active_slot=0 active_hp=0014 moves=21270000\n")
    with pytest.raises(RuntimeError, match="loop-head bench backstop"):
        run.assert_linked_faint_saved({"a": a_text, "b": good.replace(write, loop_only)}, **kwargs)
    # a zero seen only after a loop head ran is not a receipt zero
    with pytest.raises(RuntimeError, match="bench zero on receipt"):
        run.assert_linked_faint_saved({"a": a_text, "b": good.replace("loop_heads=0", "loop_heads=1")}, **kwargs)
    with pytest.raises(RuntimeError, match="out of order"):
        run.assert_linked_faint_saved({"a": a_text, "b": ready + rx + split + write + zero + readback + end + b_text},
                                      **kwargs)
    with pytest.raises(RuntimeError, match="out of order"):
        run.assert_linked_faint_saved({"a": a_text, "b": ready + rx + split + end + zero + write + readback + b_text},
                                      **kwargs)
    with pytest.raises(RuntimeError, match="moves changed or carry EXPLOSION"):
        run.assert_linked_faint_saved({"a": a_text, "b": good.replace("->21270000", "->99999999")}, **kwargs)
    with pytest.raises(RuntimeError, match="outside a battle"):
        run.assert_linked_faint_saved({"a": a_text, "b": good.replace(rx, rx.replace("in_battle=1", "in_battle=0"))},
                                      **kwargs)


@pytest.mark.parametrize(("mode", "faint_after", "expected", "encounters", "start_active"), [
    ("sacrifice", 2, "linked-fainted", 1, False),
    ("sacrifice", 999, "linked-survived-3-battles", 3, False),
    ("switch-hold", 999, "linked-active-menu", 1, False),
    ("switch-hold", 999, "linked-active-menu", 1, True),
])
def test_hunt_switch_and_three_encounter_sacrifice_bound(mode, faint_after, expected, encounters, start_active):
    lua = LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{(REPO / "lua/tests/gen1_rb_hunt_inputs.lua").as_posix()}")')
    moves = {"count": 0}
    fainted = lua.eval("function(cb) return function() return cb() end end")(
        lambda: moves["count"] >= faint_after)

    def commit(_slot, _budget):
        moves["count"] += 1
        return lua.table(why="player_move" if faint_after < 999 else "battle_over")

    switches = []

    def switch_to(slot, _budget):
        switches.append(slot)
        return lua.table(ok=True, why="switched")

    driver = lua.table(wait_menu=lambda _budget: lua.table(ok=True, frames=1),
                       switch_to=switch_to,
                       choose=lambda _name: lua.table(ok=True), commit_move=commit)
    route = module.new(lua.table(player="a"), lua.table(
        driver=driver, step=lambda _buttons: None, rd=lambda _addr: 0,
        symbols=lua.table(wNumBagItems=1, wBagItems=2),
        mode=mode, move_slot=1, switch_slot=1, fainted=fainted, start_active=start_active))
    battle = lua.table(map=12, x=10, y=35, battle=1, battle_type=0, party_hp=10,
                       party_count=2, font_loaded=False, joy_ignore=0)
    overworld = lua.table(map=12, x=10, y=35, battle=0, battle_type=0, party_hp=10,
                          party_count=2, font_loaded=False, joy_ignore=0)
    phase = None
    for index in range(encounters):
        _buttons, phase = route.step(None, None, battle, 2 * index + 1)
        if phase == expected:
            break
        _buttons, phase = route.step(None, None, overworld, 2 * index + 2)
        if phase == expected:
            break
    assert phase == expected
    assert route.encounters == encounters
    assert switches == ([] if start_active else [1] * encounters)


@pytest.mark.parametrize(("opt_in", "party_after", "balls_after", "expected"), [
    (True, 1, 1, "first-catch-battle-lost"),
    (False, 1, 1, "pace-grass"),
    (True, 2, 1, "caught"),
    (True, 1, 0, "out-of-balls"),
])
def test_first_uncaught_route1_battle_is_terminal_only_when_opted_in(opt_in, party_after, balls_after, expected):
    lua = LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{(REPO / "lua/tests/gen1_rb_hunt_inputs.lua").as_posix()}")')
    balls = {"count": 1}
    def rd(address):
        return {1: 1, 2: 4, 3: balls["count"], 4: 0, 5: 14, 6: 0, 7: 14, 8: 255}.get(address, 0)
    driver = lua.table(wait_menu=lambda _budget: lua.table(ok=True, frames=1),
                       choose=lambda _name: lua.table(ok=True),
                       commit_move=lambda _slot: lua.table(ok=False, why="battle_over"))
    route = module.new(lua.table(player="b"), lua.table(driver=driver, step=lambda _buttons: None,
        rd=rd, symbols=lua.table(wNumBagItems=1, wBagItems=2, wEnemyMonHP=4, wEnemyMonMaxHP=6,
                                wEnemyMonActualCatchRate=8),
        mode="catch", stop_on_first_uncaught_battle=opt_in))
    battle = lua.table(map=12, x=10, y=35, battle=1, battle_type=0, party_hp=19,
                       party_count=1, font_loaded=False, joy_ignore=0)
    overworld = lua.table(map=12, x=10, y=35, battle=0, battle_result=0, party_hp=19,
                          party_count=party_after, font_loaded=False, joy_ignore=0)
    _, phase = route.step(None, None, battle, 1)
    assert phase == "wild-battle_over"
    balls["count"] = balls_after
    _, phase = route.step(None, None, overworld, 2)
    assert phase == expected and route.encounters == 1
    if expected == "first-catch-battle-lost":
        _, again = route.step(None, None, battle, 3)
        assert again == expected and route.encounters == 1  # no second grass encounter can qualify


def test_actual_link_prerequisite_wording_is_the_retryable_hunt_terminal():
    """Compose the real Lua carrier around a phase returned by the real Route 1 hunter."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    hunt_source = REPO / "lua/tests/gen1_rb_hunt_inputs.lua"
    duo_source = (REPO / "lua/tests/duo/duo_gen1_main.lua").read_text(encoding="utf-8")
    hunter = lua.eval(f'dofile("{hunt_source.as_posix()}")')
    driver = lua.table(wait_menu=lambda _budget: lua.table(ok=True, frames=1),
                       choose=lambda _name: lua.table(ok=True),
                       commit_move=lambda _slot: lua.table(ok=False, why="battle_over"))
    route = hunter.new(lua.table(player="b"), lua.table(driver=driver, step=lambda _buttons: None,
        rd=lambda address: {1: 1, 2: 4, 3: 1, 4: 0, 5: 14, 6: 0, 7: 14, 8: 255}.get(address, 0),
        symbols=lua.table(wNumBagItems=1, wBagItems=2, wEnemyMonHP=4, wEnemyMonMaxHP=6,
                          wEnemyMonActualCatchRate=8),
        mode="catch", stop_on_first_uncaught_battle=True))
    battle = lua.table(map=12, x=10, y=35, battle=1, battle_type=0, party_hp=19,
                       party_count=1, font_loaded=False, joy_ignore=0)
    overworld = lua.table(map=12, x=10, y=35, battle=0, battle_result=0, party_hp=19,
                          party_count=1, font_loaded=False, joy_ignore=0)
    route.step(None, None, battle, 1)
    _, phase = route.step(None, None, overworld, 2)
    assert phase == "first-catch-battle-lost"

    wrap_source = re.search(r"(?ms)^local function link_prerequisite_failure\(why\).*?^end$", duo_source)
    scenario_source = re.search(r"(?ms)^function scenarios.link_new\(\).*?^end$", duo_source)
    assert wrap_source and scenario_source
    lua.globals().LOST_PHASE = phase
    prelude = '''
local scenarios, D = {}, {scenario="linked_faint_bench_battle_new"}
local function wait_go() return true end
local function hunt(mode, options)
    assert(mode == "catch" and options.stop_on_first_uncaught_battle == true)
    return LOST_PHASE
end
'''
    def compose(wrapper):
        return lua.execute(prelude + wrapper + "\n" + scenario_source.group() +
                           "\nreturn {scenarios.link_new, link_prerequisite_failure}")
    funcs = compose(wrap_source.group())
    passed, bare = funcs[1]()
    assert passed is False and bare == "hunt ended first-catch-battle-lost"
    nested = funcs[2](bare)
    assert nested == "link_new prerequisite failed: " + bare
    for reason in (bare, nested):
        assert duo.classify_gen1_result("RESULT: FAIL (" + reason + ")") == "CAUSE_RNG"
    pair = {"a": "RESULT: FAIL (linked capture was not returned)\n",
            "b": "RESULT: FAIL (" + nested + ")\n"}
    assert duo.retryable_gen1_rng("gen1_pure", pair, 1, 3, scenario="linked_faint_bench_battle_new")
    assert not duo.retryable_gen1_rng("gen1_pure", pair, 3, 3, scenario="linked_faint_bench_battle_new")

    changed = wrap_source.group().replace("link_new prerequisite failed: ", "link_new prerequisite changed: ")
    assert changed != wrap_source.group()
    altered = compose(changed)[2](bare)
    assert duo.classify_gen1_result("RESULT: FAIL (" + altered + ")") == "FINAL"
    assert not duo.retryable_gen1_rng("gen1_pure", {**pair, "b": "RESULT: FAIL (" + altered + ")\n"},
                                      1, 3, scenario="linked_faint_bench_battle_new")


# ── A0-H2: the post-result oracle registry, artifact provenance and the bag baseline ─────
# The verdict is a REGISTRY lookup, not an if-chain, and a Gen 1 scenario that declares no
# oracle FAILS: the saved-state readback is the independent half of every Gen 1 verdict, and
# printing PYDEC: PASS on the client's own word is the failure mode this closes.


def test_a_gen1_new_scenario_without_an_oracle_never_passes(runner):
    runner.scenario = "link_new"
    runner.game = "gen1_new"
    runner.attempt = 1
    runner.cfg = dict(duo.SCENARIOS["link_new"])
    runner.cfg.pop("oracle", None)  # being listed in the wrapper is not the oracle
    runner.start_server = lambda: None
    runner.start_instances = lambda: None
    runner.orchestrate = lambda: None
    runner.wait_results = lambda: ("RESULT: PASS", "RESULT: PASS")
    runner.cleanup = lambda passed: None
    runner.args = type("Args", (), {"keep_alive": False, "idle_jitter": 0})()
    with pytest.raises(RuntimeError, match="declares no post-result oracle"):
        runner.run()


def test_the_dispatcher_always_passes_results_and_the_oracle_kwargs(runner):
    seen = {}
    runner.cfg = {"oracle": "record_oracle", "oracle_kwargs": {"active": True}}
    runner.record_oracle = lambda results, **kwargs: seen.update(results=results, **kwargs)
    runner._run_oracle({"a": "ra", "b": "rb"})
    assert seen == {"results": {"a": "ra", "b": "rb"}, "active": True}


def test_a_non_gen1_new_scenario_without_an_oracle_keeps_the_legacy_path(runner):
    runner.game = "legacy"          # gen3_rr is the new battery row since ac448144 (oracle_required)
    runner.cfg = {"flags": []}
    runner._run_oracle({"a": "", "b": ""})  # a client RESULT is this scenario's whole verdict


def test_reconnect_saved_refuses_when_the_live_legs_did_not_complete(runner):
    runner._live_complete = {}
    runner._same_save_artifact = None
    with pytest.raises(RuntimeError, match="live legs did not complete"):
        runner.assert_reconnect_saved({"a": "", "b": ""})


def test_reconnect_saved_fails_on_a_missing_same_save_artifact(runner, tmp_path):
    runner._live_complete = {"reconnect_new": True}
    runner._same_save_artifact = str(tmp_path / "never_written.SaveRAM")
    runner.emus = []
    with pytest.raises(RuntimeError, match="artifact missing"):
        runner.assert_reconnect_saved({"a": "", "b": ""})


def test_reconnect_saved_fails_on_a_stale_same_save_artifact(runner, tmp_path):
    stale = tmp_path / "stale.SaveRAM"
    stale.write_bytes(bytes(0x8000))
    old = runner._started - 600
    os.utime(stale, (old, old))
    runner._live_complete = {"reconnect_new": True}
    runner._same_save_artifact = str(stale)
    runner.emus = []
    with pytest.raises(RuntimeError, match="stale"):
        runner.assert_reconnect_saved({"a": "", "b": ""})


def _fake_mon(key):
    dv, ot, species = key.split(":")
    return {"dvs": {"raw": int(dv, 16)}, "ot_id": int(ot, 16), "species": int(species, 16)}


def test_link_new_saved_requires_the_encounter_to_have_cost_one_ball(runner, tmp_path, monkeypatch):
    """The baseline is the FIXTURE's bag, so a run that spent nothing fails as loudly as one
    that spent two."""
    from server.adapters import gen1_codec as codec

    fixture = tmp_path / "red_battle.SaveRAM"
    fixture.write_bytes(b"F" + bytes(codec.SRAM_SIZE - 1))
    boot, link = "AAAA:1111:01", "CCCC:3333:03"
    runner.cfg = dict(duo.SCENARIOS["link_new"])
    runner._boot_keys = {"a": boot, "b": boot}
    runner._link_keys = {"a": link, "b": link}
    runner.emus = []
    monkeypatch.setattr(runner, "_fixture_save_path", lambda inst: str(fixture))
    monkeypatch.setattr(runner, "_saved_gen1_party", lambda inst, **kw: (
        b"S" + bytes(codec.SRAM_SIZE - 1), [_fake_mon(boot), _fake_mon(link)], [], codec))
    spent = {"n": 1}
    monkeypatch.setattr(codec, "bag_quantity",
                        lambda sram, item: 2 if sram[:1] == b"F" else spent["n"])
    runner.assert_link_new_saved({"a": "", "b": ""})  # fixture 2, saved 1: the ball was thrown
    spent["n"] = 2  # the same count survives the encounter: nothing was spent
    with pytest.raises(RuntimeError, match="Poke Balls"):
        runner.assert_link_new_saved({"a": "", "b": ""})


# ── A2: the soft-reset and trade-decline oracles ─────────────────────────────────────────
# Both read the driver's markers and refuse on anything absent or out of range; neither trusts
# the driver's own pass/fail, so a body that logs a bad number without failing still fails here.

_SOFT_RESET_A = "\n".join([
    "HELLO_AT_CHECKPOINT ot=4190 hellos=1 map=12",
    "RESET_SEEN frame=48 abs=1000",
    "HELLO_CLEARED frame=1100 delta=52",
    "WRITES_PAUSED frame=1290 delta=242",
    "WRITES_RESUMED frame=1400 delta=352",
    "CONTINUED frame=1800 map=12 party=1",
    "REHELLO ot=4190 hellos=2 frame=1900",
    "NO_WRITES_IN_WINDOW writes=0 cart_writes=0",
    "SAVE_WITNESS soft_reset_new_a frames=2000",
    "[SLink-gen1] writes PAUSED (hello withheld on a cleared WRAM)",
    "[SLink-gen1] writes re-enabled after a live validation",
])
_SOFT_RESET_B = "\n".join([
    "IDLE_PARTNER hellos=1 frame=1500",
    "SAVE_WITNESS soft_reset_new_b frames=1600",
])


def _reset_stub(tmp_path, monkeypatch):
    """assert_soft_reset_saved's state: real fixture bytes for the saved parties and the
    artifact links.json, synthetic receipts passed in by each test."""
    from server.adapters import gen1_codec as codec

    run = duo.DuoRun.__new__(duo.DuoRun)
    run.emus = []
    run.cfg = dict(duo.SCENARIOS["soft_reset_new"])
    run.gcfg = dict(duo.GAMES["gen1_new"])
    run.data_dir = str(tmp_path / "run")
    run._boot_keys = {"a": "AAAA:1111:01", "b": "BBBB:2222:02"}
    (tmp_path / "run").mkdir()
    links = b'{"links": [], "player_identity": {}}'
    (tmp_path / "run" / "links.json").write_bytes(links)
    run._reset_baseline = {"status": {}, "links": [], "links_bytes": links, "events": [],
                           "a_hellos": 1,
                           "links_baseline_path": str(tmp_path / "run" / "links_baseline.json")}
    run._reconnect_events = lambda: [
        {"player": "a", "type": "hello", "text": "Connected (Red, 1 mons)"},
        {"player": "b", "type": "hello", "text": "Connected (Blue, 1 mons)"},
        {"player": "a", "type": "hello", "text": "Connected (Red, 1 mons)"},
    ]
    run._fixture_save_path = lambda inst: str(
        REPO / "tests" / "fixtures" / "gen1" / f"{'red' if inst == 'a' else 'blue'}_battle.SaveRAM")

    def saved(inst, **_kwargs):
        sram = Path(run._fixture_save_path(inst)).read_bytes()
        start = codec.SRAM_LAYOUT["sPartyData"]
        party = codec.decode_party(sram[start:start + codec.PARTY_LAYOUT["size"]])
        return sram, party, [], codec

    monkeypatch.setattr(run, "_saved_gen1_party", saved)
    run._pydec_note = lambda fact: None
    return run


def test_soft_reset_oracle_reads_markers_ranges_and_server_invariance(tmp_path, monkeypatch):
    _reset_stub(tmp_path, monkeypatch).assert_soft_reset_saved(
        {"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})


@pytest.mark.parametrize(("old", "new", "message"), [
    ("SAVE_WITNESS soft_reset_new_a frames=2000\n", "", "A save witness"),
    ("RESET_SEEN frame=48", "RESET_SEEN frame=90", "outside 40..60"),
    ("WRITES_PAUSED frame=1290 delta=242", "WRITES_PAUSED frame=1290 delta=200",
     "outside 240..360"),
    ("REHELLO ot=4190", "REHELLO ot=4191", "not the pre-reset"),
    ("NO_WRITES_IN_WINDOW writes=0 cart_writes=0",
     "NO_WRITES_IN_WINDOW writes=0 cart_writes=0\nBOX_WRITE off=1234 n=3 frame=1200",
     "nothing may write"),
])
def test_soft_reset_oracle_refuses_a_broken_marker(tmp_path, monkeypatch, old, new, message):
    run = _reset_stub(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match=message):
        run.assert_soft_reset_saved({"a": _SOFT_RESET_A.replace(old, new), "b": _SOFT_RESET_B})


def test_soft_reset_oracle_refuses_a_changed_link_document(tmp_path, monkeypatch):
    run = _reset_stub(tmp_path, monkeypatch)
    (tmp_path / "run" / "links.json").write_bytes(b'{"links": [{"area_id": "route_1"}]}')
    with pytest.raises(RuntimeError, match="links.json changed"):
        run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})


def test_soft_reset_oracle_accepts_a_reordered_but_equal_document(tmp_path, monkeypatch):
    """The lane's actual diff: A's re-hello re-inserted its per-player entries, so the same
    state was written with `player_identity` before `links`. Canonical equality is the claim."""
    run = _reset_stub(tmp_path, monkeypatch)
    notes = []
    run._pydec_note = notes.append
    run._reset_baseline = dict(
        run._reset_baseline,
        links_bytes=b'{"links": [], "player_identity": {"a": {"ot_id": "1234"}, '
                    b'"b": {"ot_id": "5678"}}}')
    (tmp_path / "run" / "links.json").write_bytes(
        b'{"player_identity": {"b": {"ot_id": "5678"}, "a": {"ot_id": "1234"}}, "links": []}')
    run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})
    assert any("only in ORDER" in note and "player_identity (a|b -> b|a)" in note
               for note in notes), notes


def test_soft_reset_oracle_accepts_a_reordered_set_derived_list(tmp_path, monkeypatch):
    """`retry_areas`/`bonus_keys`/`pending_memorials` are `list(set)` (server/state.py:3077-3082):
    equal contents can serialize in either order, and only a value difference may fail."""
    run = _reset_stub(tmp_path, monkeypatch)
    run._reset_baseline = dict(run._reset_baseline,
                               links_bytes=b'{"links": [], "retry_areas": {"a": ["route_1", "route_2"]}}')
    (tmp_path / "run" / "links.json").write_bytes(
        b'{"links": [], "retry_areas": {"a": ["route_2", "route_1"]}}')
    run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})


def test_soft_reset_oracle_names_the_changed_path_and_both_values(tmp_path, monkeypatch):
    run = _reset_stub(tmp_path, monkeypatch)
    run._reset_baseline = dict(run._reset_baseline,
                               links_bytes=b'{"links": [], "run_over": false}')
    (tmp_path / "run" / "links.json").write_bytes(b'{"links": [], "run_over": true}')
    with pytest.raises(RuntimeError, match=r"at \$\.run_over: False -> True"):
        run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})


_TRADE_DECLINE_A = "\n".join([
    "CENTER_RECEPTIONIST map=40 (11,3)",
    "DECLINE_OVERWORLD",
    "SAVE_WITNESS trade_decline_new frames=900",
])
_TRADE_DECLINE_B = "\n".join([
    "TRADE_DECLINED",
    "DECLINE_OVERWORLD",
    "SAVE_WITNESS trade_decline_new frames=910",
])


def _trade_decline_stub(tmp_path, monkeypatch, moved=False):
    from server.adapters import gen1_codec as codec

    run = duo.DuoRun.__new__(duo.DuoRun)
    run.emus = []
    run.cfg = dict(duo.SCENARIOS["trade_decline_new"])
    run.gcfg = dict(duo.GAMES["gen1_new"])
    run.data_dir = str(tmp_path)
    run._boot_keys = {"a": "AAAA:1111:01", "b": "BBBB:2222:02"}
    run._link_keys = {"a": "CCCC:3333:03", "b": "DDDD:4444:04"}
    run._trade_before = (run._link_keys["a"], run._link_keys["b"])
    run._links_json = lambda: [{"area_id": "route_1", "status": "alive",
                                "a": {"key": run._link_keys["a"]},
                                "b": {"key": run._link_keys["b"]}}]

    def saved(inst, **_kwargs):
        keys = [run._boot_keys[inst], "EEEE:5555:05" if moved else run._link_keys[inst]]
        return b"", [_fake_mon(key) for key in keys], [], codec

    monkeypatch.setattr(run, "_saved_gen1_party", saved)
    run._pydec_note = lambda fact: None
    return run


def test_trade_decline_oracle_accepts_a_declined_run(tmp_path, monkeypatch):
    _trade_decline_stub(tmp_path, monkeypatch).assert_trade_decline_saved(
        {"a": _TRADE_DECLINE_A, "b": _TRADE_DECLINE_B})


def test_trade_decline_oracle_refuses_an_applied_trade(tmp_path, monkeypatch):
    run = _trade_decline_stub(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="RX apply_trade"):
        run.assert_trade_decline_saved(
            {"a": _TRADE_DECLINE_A, "b": _TRADE_DECLINE_B + "\nRX apply_trade slot=0"})


def test_trade_decline_oracle_refuses_a_saved_party_that_moved(tmp_path, monkeypatch):
    run = _trade_decline_stub(tmp_path, monkeypatch, moved=True)
    with pytest.raises(RuntimeError, match="staged a blob"):
        run.assert_trade_decline_saved({"a": _TRADE_DECLINE_A, "b": _TRADE_DECLINE_B})


# ── A3: the explode_new oracle ───────────────────────────────────────────────────────────
# W-3/D-11 is a companion-patched cartridge running one coerced turn. The saved-state half is
# assert_linked_faint_saved's, delegated with active=True and the patched-save resolver; the
# markers below are the ones only Explode Mode's path produces.

_EXPLODE_A = "\n".join([
    "A_ENGINE_FAINT CCCC:3333:03",
    "BATTLE_FAINT_SITE CCCC:3333:03 slot=0 battle_hp=0",
    "SAVE_WITNESS explode_new frames=2000",
])
_EXPLODE_B = "\n".join([
    "READY_ACTIVE linked_slot=0",
    "PANEL_COUNTER_IN_BATTLE a=1200 b=1201",
    "RX force_explode key=CCCC:3333:03",
    "EXPLODE_CMDS force_explode=1 force_faint=0",
    "MOVE_MENU_BEFORE @16728 TACKLE       | TAIL WHIP    | -            | -           ",
    "LOOP_HEAD_EXPLODE moves=99999999 pp=01010101",
    "MOVE_MENU_AFTER @16759 EXPLOSION    | EXPLOSION    | EXPLOSION    | EXPLOSION   ",
    "MOVE_MENU_EXPLOSION @16759 rows=4 key=CCCC:3333:03 moves=99999999 pp=01010101",
    "B_ACTIVE_COMMIT player_move selected=99 pp_before=01",
    "BATTLE_FAINT_SITE CCCC:3333:03 slot=0 battle_hp=0",
    "BATTLE_RESULT b",
    "SAVE_WITNESS explode_new frames=2100",
])


def _explode_stub(tmp_path, monkeypatch, calls=None):
    """The stub state assert_explode_saved reads: the shared faint half is replaced by a
    recorder, so these pins test the explode-specific markers and the delegation contract."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.emus = []
    run.data_dir = str(tmp_path)
    run._reconnect_events = lambda: [
        {"player": "b", "type": "force_explode", "text": "⚡ RATTATA exploded!"}]

    def shared(results, **kwargs):
        # **kwargs, not the exact signature: the delegate gained `explode=` (Explode Mode's
        # markers replace the faint pair's), and a recorder that pinned the old signature
        # turned every explode pin red for a keyword it was not even asserting on.
        (calls if calls is not None else []).append(kwargs)

    monkeypatch.setattr(run, "assert_linked_faint_saved", shared)
    run._pydec_note = lambda fact: None
    return run


def test_explode_oracle_reads_the_markers_and_delegates_the_shared_half(tmp_path, monkeypatch):
    calls = []
    run = _explode_stub(tmp_path, monkeypatch, calls)
    run.assert_explode_saved({"a": _EXPLODE_A, "b": _EXPLODE_B})
    assert len(calls) == 1, calls
    assert calls[0]["active"] is True, "the shared half must run in the ACTIVE window"
    assert calls[0]["explode"] is True, (
        "the delegate has to be told this is Explode Mode's half, or it demands the markers "
        "the scenario asserts absent")
    # no per-scenario save resolver any more: `_saved_gen1_party`'s own default reads the
    # companion cartridge's save (test_e2e_duo_lane_isolation pins that default)
    assert "saved_state" not in calls[0]


def test_explode_oracle_passes_a_synthetic_explode_receipt_through_the_REAL_delegate(
        tmp_path, monkeypatch):
    """No monkeypatched delegate: the explode receipt has to satisfy the shared faint half's
    own checks, which is exactly what the stub above cannot see. Both halves' flushes hold a
    living starter with the linked key in Box 12 at HP 0, links.json is a battle-caused
    memorial, and B's receipt carries force_explode + LOOP_HEAD_EXPLODE instead of the faint
    pair's two markers."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.emus = []
    run.data_dir = str(tmp_path)
    run._pydec_note = lambda fact: None
    run._reconnect_events = lambda: []

    a_sram, a_rom = _fixture_save("red")
    b_sram, b_rom = _fixture_save("blue")
    a_image, b_image = bytearray(a_sram), bytearray(b_sram)
    key_a = _put_fainted_in_box12(a_image, a_rom)
    key_b = _put_fainted_in_box12(b_image, b_rom)
    start = codec.SRAM_LAYOUT["sPartyData"]
    a_party = codec.decode_party(bytes(a_image)[start:start + codec.PARTY_LAYOUT["size"]])
    b_party = codec.decode_party(bytes(b_image)[start:start + codec.PARTY_LAYOUT["size"]])
    run._link_keys = {"a": key_a, "b": key_b}
    run._boot_keys = {"a": codec.key(a_party[0]), "b": codec.key(b_party[0])}
    run._saved_gen1_party = lambda inst: (
        bytes(a_image if inst == "a" else b_image),
        a_party if inst == "a" else b_party, [], codec)
    run._links_json = lambda: [{"area_id": "route_1", "status": "memorial", "cause": "battle",
                                "a": {"key": key_a}, "b": {"key": key_b}}]
    (tmp_path / "slink.log").write_text(
        f"[a] faint \u2192 force_explode b:{key_b}\n"
        f"[b] faint \u2192 force_explode a:{key_a}\n"
        f"pair in route_1 fully memorialized\n", encoding="utf-8")

    b_text = "\n".join([
        "READY_ACTIVE linked_slot=0",
        "PANEL_COUNTER_IN_BATTLE a=1200 b=1201",
        f"RX force_explode key={key_b}",
        f"RX memorialize key={key_b}",
        '"event":"memorialize_done"',
        "EXPLODE_CMDS force_explode=1 force_faint=0",
        "MOVE_MENU_BEFORE @16728 TACKLE       | TAIL WHIP    | -            | -           ",
        "LOOP_HEAD_EXPLODE moves=99999999 pp=01010101",
        "MOVE_MENU_AFTER @16759 EXPLOSION    | EXPLOSION    | EXPLOSION    | EXPLOSION   ",
        f"MOVE_MENU_EXPLOSION @16759 rows=4 key={key_b} moves=99999999 pp=01010101",
        "B_ACTIVE_COMMIT player_move selected=99 pp_before=01",
        f"BATTLE_FAINT_SITE {key_b} slot=0 battle_hp=0",
        "TILEMAP_FAINTED offset=1",
        f'"event":"faint","key":"{key_b}"',
        "BATTLE_RESULT b outcome=1 fainted=1",
        "GAME_OVER RX game_over",
        "SAVE_WITNESS explode_new frames=2100",
    ])
    a_text = "\n".join([
        "A_ENGINE_FAINT " + key_a,
        f"BATTLE_FAINT_SITE {key_a} slot=0 battle_hp=0",
        f'"event":"faint","key":"{key_a}"',
        f"RX memorialize key={key_a}",
        '"event":"memorialize_done"',
        "SAVE_WITNESS explode_new frames=2000",
    ])
    run.assert_explode_saved({"a": a_text, "b": b_text})


@pytest.mark.parametrize(("old", "new", "message"), [
    ("PANEL_COUNTER_IN_BATTLE a=1200 b=1201", "PANEL_COUNTER_IN_BATTLE a=1200 b=1200",
     "did not advance"),
    ("MOVE_MENU_BEFORE @16728 TACKLE       | TAIL WHIP    | -            | -           ",
     "MOVE_MENU_BEFORE @16728 EXPLOSION    | EXPLOSION    | EXPLOSION    | EXPLOSION   ",
     "before the write"),
    ("LOOP_HEAD_EXPLODE moves=99999999 pp=01010101\n", "", "B loop-head write"),
    ("BATTLE_RESULT b", "BATTLE_RESULT b\nRX force_faint key=CCCC:3333:03", "RX force_faint"),
    ("BATTLE_RESULT b",
     "BATTLE_RESULT b\nLOOP_HEAD_WRITE key=CCCC:3333:03 battle_hp=0000 selected=FF",
     "LOOP_HEAD_WRITE"),
])
def test_explode_oracle_refuses_a_broken_marker(tmp_path, monkeypatch, old, new, message):
    run = _explode_stub(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match=message):
        run.assert_explode_saved({"a": _EXPLODE_A, "b": _EXPLODE_B.replace(old, new)})


# ── A6: the PC scenarios' oracles ────────────────────────────────────────────────────────
# pc_ops_new drives Bill's PC by play and ends with a RELEASE that sends release{key}, which kills
# the pair (owner ruling O-35); changebox_new drives the deadzone half and then a real CHANGE BOX.

_PC_LINK_A = "CCCC:3333:03"
_PC_LINK_B = "DDDD:4444:04"
_PC_A = "\n".join([
    "PC_BOX_BEFORE box=1 count=0 init=false",
    "PC_OP deposit start",
    "PC_OP deposit done",
    "PC_OP withdraw start",
    "PC_OP withdraw done",
    'TX {"event":"party_to_box","player":"a","key":"' + _PC_LINK_A + '"}',
    'TX {"event":"box_to_party","player":"a","key":"' + _PC_LINK_A + '"}',
    "PC_DEPOSIT_KEY " + _PC_LINK_A,
    "PC_WITHDRAW_KEY " + _PC_LINK_A,
    "PC_MID party=2 box=1 count=0",
    "PC_OP deposit start",
    "PC_OP deposit done",
    "PC_OP release_box start",
    "PC_OP release_box done",
    'TX {"event":"party_to_box","player":"a","key":"' + _PC_LINK_A + '"}',
    "[SLink-gen1] RELEASE_SEEN key=" + _PC_LINK_A + " box=0",
    'TX {"event":"release","key":"' + _PC_LINK_A + '","player":"a"}',
    "PC_RELEASE_SEEN " + _PC_LINK_A,
    "PC_FINAL party=1 box=1 count=0 init=false",
    "SAVE_WITNESS pc_ops_new_a frames=900",
])
_PC_B = "\n".join([
    "PC_PARTNER_RX 1 box_mon " + _PC_LINK_B,
    "PC_PARTNER_RX 2 party_mon " + _PC_LINK_B,
    "SAVE_WITNESS pc_ops_new_b frames=910",
])


def _empty_box1(image):
    """Box 1 (SRAM box 0) written empty with its bank and individual checksums recomputed, as
    the game's own box close leaves it after pc_ops_new's deposit+release. The untouched banks
    stay $FF, which is why that oracle does not claim all twelve."""
    start = codec.SRAM_LAYOUT["box_banks"][0]
    image[start:start + codec.BOX_SIZE] = bytes(codec.BOX_SIZE)
    image[start + codec.BOX_LAYOUT["species"]] = codec.SPECIES_END
    for slot in range(6):
        offset = start + slot * codec.BOX_SIZE
        image[codec.SRAM_LAYOUT["individual_checksums"][0] + slot] = codec.sav_checksum(
            image[offset:offset + codec.BOX_SIZE])
    end = codec.SRAM_LAYOUT["all_boxes_checksums"][0]
    image[end] = codec.sav_checksum(image[start:end])
    _seal_main(image)
    return image


def _pc_stub(tmp_path, monkeypatch, box1_stored=None, box_index=0, initialised=False,
             memorial=False, active_box_holds=None, link_status="dead", link_cause="release"):
    """The pc_ops/changebox stub: real fixture bytes for both cartridges, the box state the
    scenario leaves behind, and the server surfaces the oracle reads."""
    a_sram, _a_rom = _fixture_save("red")
    b_sram, b_rom = _fixture_save("blue")
    start = codec.SRAM_LAYOUT["sPartyData"]
    a_image = _empty_box1(bytearray(a_sram))
    if box1_stored is not None:
        a_image[codec.SRAM_LAYOUT["individual_checksums"][0]] = box1_stored
    a_image[codec._CURRENT_BOX] = (codec._BOX_INITIALIZED if initialised else 0) | box_index
    _seal_main(a_image)
    a_party = codec.decode_party(bytes(a_image)[start:start + codec.PARTY_LAYOUT["size"]])
    boot_a = codec.key(a_party[0])
    b_image = bytearray(b_sram)
    link_b = _add_caught_to_party(b_image, b_rom)
    if memorial:
        link_b = _put_fainted_in_box12(b_image, b_rom)
    # The changebox oracle reads B's saved flag/index, so the stub writes the box state into
    # both images: pc_ops ignores the flag and changebox ignores A.
    b_image[codec._CURRENT_BOX] = (codec._BOX_INITIALIZED if initialised else 0) | box_index
    _seal_main(b_image)
    b_party = codec.decode_party(bytes(b_image)[start:start + codec.PARTY_LAYOUT["size"]])

    run = duo.DuoRun.__new__(duo.DuoRun)
    run.emus = []
    run.cfg = dict(duo.SCENARIOS["pc_ops_new"])
    run.gcfg = dict(duo.GAMES["gen1_new"])
    run.data_dir = str(tmp_path)
    run._boot_keys = {"a": boot_a, "b": codec.key(b_party[0])}
    run._link_keys = {"a": _PC_LINK_A, "b": link_b}
    run._deadzone_b_key = link_b
    # O-35: A's release{key} killed the pair on the server (state.py _handle_release).
    run._links_json = lambda: [{"area_id": "route_1", "status": link_status, "cause": link_cause,
                                "killer": None, "initiating_player": "a",
                                "a": {"key": _PC_LINK_A}, "b": {"key": link_b}}]

    def saved(inst, **_kwargs):
        # The ACTIVE box (sCurBoxData), which is what an ordinary save carries under the main
        # checksum — `active_box_holds` is the negative case (the release did not happen).
        active = ([_fake_mon(active_box_holds)] if inst == "a" and active_box_holds else [])
        if inst == "a":
            return bytes(a_image), a_party, active, codec
        return bytes(b_image), b_party, [], codec

    monkeypatch.setattr(run, "_saved_gen1_party", saved)
    run._pydec_note = lambda fact: None
    # B's linked key is derived from the fixture bytes, so the receipt template gets it here.
    return run, {"a": _PC_A, "b": _PC_B.replace(_PC_LINK_B, link_b)}


def _pc_fixture(tmp_path, monkeypatch, **kwargs):
    return _pc_stub(tmp_path, monkeypatch, **kwargs)[1]


def test_pc_ops_oracle_reads_the_cycle_and_the_release_kill(tmp_path, monkeypatch):
    run, receipts = _pc_stub(tmp_path, monkeypatch)
    run.assert_pc_ops_new_saved(receipts)


@pytest.mark.parametrize(("status", "cause"), [("alive", None), ("dead", "faint")])
def test_pc_ops_oracle_refuses_a_pair_the_release_did_not_kill(tmp_path, monkeypatch, status, cause):
    """O-35: the pre-ruling 'pair stays ALIVE' gap is now the failure, and so is any other cause."""
    run, receipts = _pc_stub(tmp_path, monkeypatch, link_status=status, link_cause=cause)
    with pytest.raises(RuntimeError, match="did not die by the release"):
        run.assert_pc_ops_new_saved(receipts)


def test_pc_ops_oracle_refuses_a_release_that_never_reached_the_wire(tmp_path, monkeypatch):
    run, receipts = _pc_stub(tmp_path, monkeypatch)
    silent = receipts["a"].replace('TX {"event":"release","key":"' + _PC_LINK_A + '","player":"a"}\n', "")
    with pytest.raises(RuntimeError, match="sent 0 release"):
        run.assert_pc_ops_new_saved({"a": silent, "b": receipts["b"]})


@pytest.mark.parametrize(("old", "new", "message"), [
    # a storage send never happened: the third TX line is gone
    # the THIRD storage send (the second deposit) is gone; .replace(1) keeps the first
    ("PC_OP release_box done\nTX {\"event\":\"party_to_box\",\"player\":\"a\",\"key\":\""
     + _PC_LINK_A + "\"}", "PC_OP release_box done", "sent 1 party_to_box"),
    ("PC_FINAL party=1", "PC_FINAL party=2", "not found in the receipt"),
    ("PC_MID party=2 box=1 count=0", "PC_MID party=2 box=1 count=1", "not found in the receipt"),
])
def test_pc_ops_oracle_refuses_a_broken_receipt(tmp_path, monkeypatch, old, new, message):
    run, receipts = _pc_stub(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match=message):
        run.assert_pc_ops_new_saved({"a": receipts["a"].replace(old, new, 1), "b": receipts["b"]})


def test_pc_ops_oracle_refuses_a_second_release(tmp_path, monkeypatch):
    run, receipts = _pc_stub(tmp_path, monkeypatch)
    twice = receipts["a"].replace(
        "PC_RELEASE_SEEN", "[SLink-gen1] RELEASE_SEEN key=" + _PC_LINK_A + " box=0\nPC_RELEASE_SEEN")
    with pytest.raises(RuntimeError, match="RELEASE_SEEN line"):
        run.assert_pc_ops_new_saved({"a": twice, "b": receipts["b"]})


def test_pc_ops_oracle_refuses_a_release_before_the_third_send(tmp_path, monkeypatch):
    """The wire stamp is what proves no release fired during the withdraw window."""
    run, receipts = _pc_stub(tmp_path, monkeypatch)
    line = "[SLink-gen1] RELEASE_SEEN key=" + _PC_LINK_A + " box=0"
    moved = receipts["a"].replace(line + "\n", "")           # out of its real place...
    moved = moved.replace('TX {"event":"party_to_box","player":"a","key":"' + _PC_LINK_A + '"}',
                          'TX {"event":"party_to_box","player":"a","key":"' + _PC_LINK_A + '"}\n'
                          + line, 1)                          # ...and before the third send
    with pytest.raises(RuntimeError, match="not after the third storage send"):
        run.assert_pc_ops_new_saved({"a": moved, "b": receipts["b"]})


def test_pc_ops_oracle_refuses_a_third_partner_command(tmp_path, monkeypatch):
    run, receipts = _pc_stub(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="third storage command"):
        run.assert_pc_ops_new_saved(
            {"a": receipts["a"], "b": receipts["b"] + "\nPC_PARTNER_RX 3 box_mon " + _PC_LINK_B})


def test_pc_ops_oracle_refuses_an_active_box_that_still_holds_the_key(tmp_path, monkeypatch):
    """The saved ACTIVE box (sCurBoxData) still holding the released key: the release never
    reached the cartridge, whatever the receipt's own markers said."""
    run, receipts = _pc_stub(tmp_path, monkeypatch, active_box_holds=_PC_LINK_A)
    with pytest.raises(RuntimeError, match="still holds the released key"):
        run.assert_pc_ops_new_saved(receipts)


def test_pc_ops_oracle_refuses_an_active_box_holding_anything(tmp_path, monkeypatch):
    """Anything left in the active box means the withdraw/release pair did not finish."""
    run, receipts = _pc_stub(tmp_path, monkeypatch, active_box_holds="EEEE:5555:05")
    with pytest.raises(RuntimeError, match="the release left it empty"):
        run.assert_pc_ops_new_saved(receipts)


# ── changebox_new ────────────────────────────────────────────────────────────────────────

_CHANGEBOX_B = "\n".join([
    "CHANGEBOX_TO 12 initialised=true count=1",
    "CHANGEBOX_BACK 1",
    "SAVE_WITNESS changebox_new_b frames=800",
])


def _changebox_stub(tmp_path, monkeypatch, **kwargs):
    run, receipts = _pc_stub(tmp_path, monkeypatch, memorial=True, **kwargs)
    run.cfg = dict(duo.SCENARIOS["changebox_new"])
    monkeypatch.setattr(run, "assert_dead_zone_new_saved", lambda results: None)
    return run, receipts


def test_changebox_oracle_reads_the_change_and_the_saved_index(tmp_path, monkeypatch):
    run, _receipts = _changebox_stub(tmp_path, monkeypatch, initialised=True, box_index=0)
    run.assert_changebox_new_saved({"a": "", "b": _CHANGEBOX_B})


@pytest.mark.parametrize(("old", "new", "message"), [
    ("CHANGEBOX_TO 12 initialised=true count=1", "CHANGEBOX_TO 12 initialised=true count=0",
     "has to still be there"),
    ("CHANGEBOX_BACK 1", "CHANGEBOX_BACK 2", "not found in the receipt"),
    ("CHANGEBOX_BACK 1",
     'CHANGEBOX_BACK 1\nTX {"event":"party_to_box","player":"b","key":"X"}',
     "carries party_to_box"),
])
def test_changebox_oracle_refuses_a_broken_receipt(tmp_path, monkeypatch, old, new, message):
    run, _receipts = _changebox_stub(tmp_path, monkeypatch, initialised=True, box_index=0)
    with pytest.raises(RuntimeError, match=message):
        run.assert_changebox_new_saved({"a": "", "b": _CHANGEBOX_B.replace(old, new)})


def test_changebox_oracle_refuses_a_cleared_saved_flag(tmp_path, monkeypatch):
    run, _receipts = _changebox_stub(tmp_path, monkeypatch, initialised=False, box_index=0)
    with pytest.raises(RuntimeError, match="has-changed-boxes bit is clear"):
        run.assert_changebox_new_saved({"a": "", "b": _CHANGEBOX_B})


def test_changebox_oracle_refuses_a_current_box_that_is_not_one(tmp_path, monkeypatch):
    run, _receipts = _changebox_stub(tmp_path, monkeypatch, initialised=True, box_index=2)
    with pytest.raises(RuntimeError, match="current box is 2"):
        run.assert_changebox_new_saved({"a": "", "b": _CHANGEBOX_B})


def test_pc_ops_oracle_refuses_an_initialised_box_flag(tmp_path, monkeypatch):
    """H-2 (n): this route never runs ChangeBox, so the saved box-initialised flag has to stay
    clear; the oracle reads it through the codec rather than trusting the receipt."""
    run, receipts = _pc_stub(tmp_path, monkeypatch, initialised=True)
    with pytest.raises(RuntimeError, match="box-initialised flag is set"):
        run.assert_pc_ops_new_saved(receipts)


def test_soft_reset_oracle_reads_a_missing_mon_stats_document_as_empty(tmp_path, monkeypatch):
    """The pydec note's key list has to survive a links.json that is absent or unparseable —
    it is read before the baseline, i.e. before any client has been released."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.data_dir = str(tmp_path)
    (tmp_path / "links.json").write_text(
        json.dumps({"mon_stats": {"BBBB:2222:02": {"level": 5}}}), encoding="utf-8")
    assert run._mon_stats_keys() == ["BBBB:2222:02"]
    (tmp_path / "links.json").write_text(json.dumps({"mon_stats": {           # KEY-SCOPE-2 shape
        "a": {"AAAA:1111:01": {"level": 5}}, "b": {"BBBB:2222:02": {"level": 5}}}}), encoding="utf-8")
    assert run._mon_stats_keys() == ["AAAA:1111:01", "BBBB:2222:02"]
    (tmp_path / "links.json").unlink()
    assert run._mon_stats_keys() == []


def test_soft_reset_oracle_refuses_a_stat_that_changed(tmp_path, monkeypatch):
    """A stat that MOVED across the reset is a real finding, and it fails on its own message —
    mon_stats is reconciled before the canonical compare, not diffed by it."""
    run = _reset_stub(tmp_path, monkeypatch)
    run._reset_baseline = dict(
        run._reset_baseline,
        links_bytes=b'{"links": [], "mon_stats": {"AAAA:1111:01": {"level": 5}}}')
    (tmp_path / "run" / "links.json").write_bytes(
        b'{"links": [], "mon_stats": {"AAAA:1111:01": {"level": 6}}}')
    with pytest.raises(RuntimeError, match=r"mon_stats changed across the soft reset"):
        run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})


def test_soft_reset_oracle_refuses_a_stat_that_vanished(tmp_path, monkeypatch):
    """The flush only ever ADDS. A key the baseline held that is gone at the end is a loss."""
    run = _reset_stub(tmp_path, monkeypatch)
    run._reset_baseline = dict(
        run._reset_baseline,
        links_bytes=b'{"links": [], "mon_stats": {"AAAA:1111:01": {"level": 5}}}')
    (tmp_path / "run" / "links.json").write_bytes(b'{"links": [], "mon_stats": {}}')
    with pytest.raises(RuntimeError, match=r"mon_stats changed across the soft reset"):
        run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})


def test_soft_reset_oracle_accepts_the_deferred_stats_flush(tmp_path, monkeypatch):
    """DIAG-SR2: server.py's hello handler mutates state.mon_stats AFTER its only `_save()`, so
    hello N persists hello N-1's stats and the last half's reach disk on the next save of any
    kind — A's re-hello. The baseline is pre-reset and holds ONE boot key; the end state holds
    both. That arrival is a write that was already owed, not a change, and it is accepted with
    the flushed key named in the receipt."""
    run = _reset_stub(tmp_path, monkeypatch)
    notes = []
    run._pydec_note = notes.append
    run._reset_baseline = dict(
        run._reset_baseline,
        links_bytes=b'{"links": [], "mon_stats": {"AAAA:1111:01": {"level": 5, "maxHP": 19}}}')
    (tmp_path / "run" / "links.json").write_bytes(
        b'{"links": [], "mon_stats": {"AAAA:1111:01": {"level": 5, "maxHP": 19}, '
        b'"BBBB:2222:02": {"level": 5, "maxHP": 19}}}')
    run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})
    assert any("deferred mon_stats flush for ['BBBB:2222:02']" in note for note in notes), notes


def test_soft_reset_oracle_refuses_a_stat_for_a_mon_that_was_never_booted(tmp_path, monkeypatch):
    """The flush tolerance is bounded by the boot keys: nothing was caught during this scenario,
    so a third mon_stats entry is a capture that should not exist, not a deferred write."""
    run = _reset_stub(tmp_path, monkeypatch)
    (tmp_path / "run" / "links.json").write_bytes(
        b'{"links": [], "mon_stats": {"CCCC:3333:03": {"level": 7}}}')
    with pytest.raises(RuntimeError, match=r"mon_stats gained \['CCCC:3333:03'\]"):
        run.assert_soft_reset_saved({"a": _SOFT_RESET_A, "b": _SOFT_RESET_B})


def test_admit_randomized_launches_b_first_and_waits_for_its_contract_verdict():
    """F-4 is B's CONTRACT verdict. If A's randomized hello commits the run's artifact kind
    first, an un-randomized B is refused earlier by the mixed-kinds gate (server.py
    _mixed_games_error), which records no admission verdict, and the live wait times out
    (gen1_pure lane, 2026-09-25). B hellos alone first; A launches only after B's verdict."""
    run = duo.DuoRun.__new__(duo.DuoRun)
    run.scenario = "admit_randomized_new"
    run.cfg = dict(duo.SCENARIOS["admit_randomized_new"])
    run.battery_boot = False
    run._clear_attempt_artifacts = lambda: None
    run._timed = lambda phase: contextlib.nullcontext()
    order = []
    run.launch_instance = lambda inst, **_kw: order.append(inst)

    def status():
        order.append("status")
        verdict = "rejected" if order.count("b") and order.count("status") > 1 else "admitted"
        return {"players": {"b": {"admission": verdict, "admission_reason": "x"}}}

    run._status = status
    run.wait_for = lambda desc, pred, timeout, **_kw: duo.wait_for(desc, pred, timeout, interval=0)
    run.start_instances()
    assert order[0] == "b" and order[-1] == "a" and order.index("a") > order.index("status")
    assert order.count("a") == order.count("b") == 1


@pytest.mark.parametrize("game,scenario,expected", [
    ("gen1_new", "link_new", []),
    ("gen1_new", "admit_randomized_new", ["red", "blue"]),
    ("gen1_pure", "link_new", ["purered", "pureblue", "purered_overlay", "pureblue_overlay"]),
    ("gen1_pure_green", "link_new",
     ["purered", "puregreen", "purered_overlay", "puregreen_overlay"]),
])
def test_gen1_start_stages_clean_dumps_only_when_they_are_inputs(runner, monkeypatch,
                                                              tmp_path, game, scenario, expected):
    import gen1_playthrough as play

    runner.game, runner.scenario = game, scenario
    runner.gcfg, runner.cfg = dict(duo.GAMES[game]), dict(duo.SCENARIOS[scenario])
    runner.battery_boot = True
    staged, launched = [], []

    def stage(key):
        if game == "gen1_new" and scenario != "admit_randomized_new":
            raise FileNotFoundError(f"clean dump is absent: {key}")
        staged.append(key)
        rel = f"patch/build/{key}.gb"
        (tmp_path / rel).write_bytes(key.encode())
        return rel

    monkeypatch.setattr(play, "staged_rom", stage)
    runner._clear_attempt_artifacts = lambda: None
    runner._timed = lambda _phase: contextlib.nullcontext()
    runner.launch_instance = lambda inst, **_kw: launched.append(inst)
    runner.wait_for = lambda *_args, **_kwargs: None
    runner.start_instances()
    assert staged == expected
    assert launched == (["b", "a"] if scenario == "admit_randomized_new" else ["a", "b"])


@pytest.mark.parametrize("game,scenario", [
    ("gen1_new", "admit_randomized_new"), ("gen1_pure", "link_new"),
])
def test_gen1_start_still_requires_real_randomization_or_ups_bases(runner, monkeypatch, game, scenario):
    import gen1_playthrough as play

    runner.game, runner.scenario = game, scenario
    runner.gcfg, runner.cfg = dict(duo.GAMES[game]), dict(duo.SCENARIOS[scenario])
    runner.battery_boot = True

    def absent(key):
        raise FileNotFoundError(f"required clean input missing: {key}")

    monkeypatch.setattr(play, "staged_rom", absent)
    runner.launch_instance = lambda *_a, **_kw: pytest.fail("launched without its input")
    with pytest.raises(FileNotFoundError, match="required clean input missing"):
        runner.start_instances()


def test_saved_gen1_party_reports_rom_scan_failure_as_named_qualification(runner, monkeypatch, tmp_path):
    import gen1_fixtures

    rom = tmp_path / "randomized-overlay.gbc"
    rom.write_bytes(b"randomized overlay with damaged anchors")
    runner._admit_roms = {"a": str(rom)}
    save = Path(runner._saveram_dir("a")) / runner._gen1_save_name("a")
    save.parent.mkdir()
    save.write_bytes(b"saved party")
    seen = []

    def qualify(sram, rom_bytes, notes):
        seen.append((sram, rom_bytes))
        raise scan.RomScanError("neither the clean nor the overlay anchor set holds")

    monkeypatch.setattr(gen1_fixtures, "qualify", qualify)
    with pytest.raises(RuntimeError, match="qualification: neither the clean nor the overlay anchor set holds"):
        runner._saved_gen1_party("a")
    assert seen == [(b"saved party", rom.read_bytes())]


def test_prepared_admission_cartridge_remains_selected_at_oracle_time(runner, monkeypatch):
    _fake_pipeline(monkeypatch)
    runner.prepare_admit_randomized_new()
    expected = runner._admit_roms["a"]
    runner.game = "gen1_new"
    runner.emus = []
    observed = []
    runner.assert_stub_oracle = lambda _results: observed.append(runner._rom_for("a"))
    results = {}
    for inst in ("a", "b"):
        rom = Path(duo.REPO) / runner._rom_for(inst)
        prefix = hashlib.sha1(rom.read_bytes()).hexdigest()[:8]
        kind = "named"  # Entry's vanilla companion fallback, including randomized companions
        results[inst] = (f"client built: title=red pack=gen1_rby kind={kind} player={inst} "
                         f"rom={prefix} -> 127.0.0.1:54321")
    runner._run_oracle(results)
    assert observed == [expected] and runner._admit_roms == {"a": expected}
    assert (Path(duo.REPO) / expected).read_bytes() == b"random-red"


@pytest.mark.parametrize("scenario", ["explode_new", "linked_faint_active_new", "explode_bench_battle_new"])
@pytest.mark.parametrize("fault", ["untouched_save", "untouched_receipt", "missing_receipt"])
def test_synth_catch_oracle_requires_spent_balls(tmp_path, scenario, fault):
    run, paths, a_text, b_text = _linked_faint_fixture(tmp_path, scenario)
    proper = (b_text + "LOOP_HEAD_WRITE key=" + run._link_keys["b"] + "\n"
              + "BATTLE_FAINT_SITE " + run._link_keys["b"] + "\n"
              + f'TX {{"event":"faint","key":"{run._link_keys["b"]}"}}\n'
              + "TILEMAP_FAINTED offset=123\nBATTLE_RESULT b 2\n")
    def receipt(text):
        return text if "balls_before=" in text else text + "[hunt] threw ball index 0 -> caught balls_before=20 balls_after=19\n"
    results = {"a": receipt(a_text), "b": receipt(proper)}
    run.assert_linked_faint_saved(results, active=True)
    if fault == "untouched_save":
        path, sram, _rom = paths["a"]
        sram[codec._BAG_COUNT + 2] = 20
        _seal_main(sram)
        path.write_bytes(sram)
    elif fault == "untouched_receipt":
        results["a"] = results["a"].replace("balls_after=19", "balls_after=20")
    else:
        results["a"] = "\n".join(line for line in results["a"].splitlines() if "threw ball" not in line)
    with pytest.raises(RuntimeError, match="SYNTH catch"):
        run.assert_linked_faint_saved(results, active=True)



def test_oracle_fixture_skips_when_private_companion_input_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(duo, "REPO", str(tmp_path))
    with pytest.raises(pytest.skip.Exception, match="companion input absent"):
        _oracle_runner(tmp_path, "link_new")


def test_hunt_logs_driver_ball_counts_for_the_oracle():
    lua = LuaRuntime(unpack_returned_tuples=True)
    module = lua.eval(f'dofile("{(REPO / "lua/tests/gen1_rb_hunt_inputs.lua").as_posix()}")')
    logs, menus = [], []
    def menu(_budget):
        menus.append(True)
        return lua.table(ok=len(menus) == 1, frames=1, why="battle_over")
    driver = lua.table(wait_menu=menu,
                       use_item=lambda *_: lua.table(why="caught", balls_before=20, balls_after=19))
    route = module.new(lua.table(player="a"), lua.table(driver=driver, step=lambda _: None,
        rd=lambda a: {1: 1, 2: 4, 3: 20, 4: 0, 5: 1, 6: 0, 7: 14, 8: 255}.get(a, 0),
        symbols=lua.table(wNumBagItems=1, wBagItems=2, wEnemyMonHP=4, wEnemyMonMaxHP=6,
                          wEnemyMonActualCatchRate=8),
        mode="catch", log=lambda line: logs.append(str(line))))
    battle = lua.table(map=12, x=10, y=35, battle=1, battle_type=0, party_hp=19,
                       party_count=1, font_loaded=False, joy_ignore=0)
    route.step(None, None, battle, 1)
    assert "[hunt] threw ball index 0 -> caught balls_before=20 balls_after=19" in logs


@pytest.mark.parametrize("scenario", ["explode_new", "linked_faint_active_new", "explode_bench_battle_new"])
@pytest.mark.parametrize("case", ["one", "three", "wrong_delta", "untouched", "saved_disagrees",
                                  "no_caught", "two_caught", "noncontiguous", "extra_miss",
                                  "later_miss", "later_hunt_no_throw"])
def test_synth_catch_decrement_matches_recorded_throws(tmp_path, scenario, case):
    run, paths, a_text, b_text = _linked_faint_fixture(tmp_path, scenario)
    proper = (b_text + "LOOP_HEAD_WRITE key=" + run._link_keys["b"] + "\n"
              + "BATTLE_FAINT_SITE " + run._link_keys["b"] + "\n"
              + f'TX {{"event":"faint","key":"{run._link_keys["b"]}"}}\n'
              + "TILEMAP_FAINTED offset=123\nBATTLE_RESULT b 2\n")
    throws = [("caught", 20, 19)] if case == "one" else [
        ("missed", 20, 19), ("missed", 19, 18), ("caught", 18, 17)]
    saved = 19 if case == "one" else 17
    if case == "wrong_delta":
        throws[-1] = ("caught", 18, 16)
        saved = 16  # receipt agrees with save, but four balls for three throws is false
    elif case == "untouched":
        saved = 20
    elif case == "saved_disagrees":
        saved = 18
    elif case == "no_caught":
        throws[-1] = ("missed", 18, 17)
    elif case == "two_caught":
        throws[0] = ("caught", 20, 19)
    elif case == "extra_miss":
        throws = [("missed", 20, 20), ("caught", 20, 19)]
        saved = 19
    elif case == "later_miss":
        saved = 16
    elif case == "noncontiguous":
        throws[1] = ("missed", 18, 17)  # total decrement still equals count; chain is impossible
    path, sram, _rom = paths["a"]
    sram[codec._BAG_COUNT + 2] = saved
    _seal_main(sram)
    path.write_bytes(sram)
    lines = []
    for line in a_text.splitlines():
        if line.startswith("[hunt] threw ball"):
            lines.extend(f"[hunt] threw ball index 0 -> {why} balls_before={before} balls_after={after}"
                         for why, before, after in throws)
            if case in ("later_miss", "later_hunt_no_throw"):
                # These rows' later A hunt is sacrifice; it cannot reach use_item.
                lines.append("[hunt] encounter 1 at (10,35) mode=sacrifice balls=17")
                if case == "later_miss":
                    lines.append("[hunt] threw ball index 0 -> missed balls_before=17 balls_after=16")
        else:
            lines.append(line)
    results = {"a": "\n".join(lines) + "\n", "b": proper}
    if case in ("one", "three", "later_hunt_no_throw"):
        run.assert_linked_faint_saved(results, active=True)
    else:
        with pytest.raises(RuntimeError, match="SYNTH catch"):
            run.assert_linked_faint_saved(results, active=True)


# ── frlgc 2026-10-03: a companion cartridge's 2-3 accepted A hellos in ONE same-save reconnect ──────────
# Server events as events.json stores them (NEWEST FIRST, area_id differs between refreshes, same "Connected (rom, N mons)" text).
def _hello_row(text="Connected (firered, 2 mons)", area="viridian_city", player="a"):
    return {"player": player, "type": "hello", "text": text, "area_id": area, "key": ""}


def _multi(events, n=3, **kw):
    areas = ["viridian_city", "viridian_city_mart", "pallet_town"]
    return _with_new_events(events, *[_hello_row(area=areas[k % 3], **kw) for k in range(n)])


@pytest.mark.parametrize("n", [1, 2, 3])
def test_two_or_three_accepted_refresh_hellos_pass_every_downstream_step(n):
    before, after, events = _reconnect_snapshots()
    resumed = _multi(events, n)
    assert duo.accepted_reconnect_hellos(events, resumed) is True
    assert duo.reconnect_same_problems(before, after, events, resumed, "AAAA:1111:01", "1234",
                                       allow_accepted_refreshes=True) == []
    # B's hello rows and area_enter rows between them are not A's reconnect hellos
    noisy = _with_new_events(resumed, {"player": "b", "type": "hello", "text": "Connected (leafgreen, 2 mons)"},
                             {"player": "a", "type": "area_enter", "text": "Entered Viridian City"})
    assert duo.accepted_reconnect_hellos(events, noisy) is True
    assert duo.reconnect_same_problems(before, after, events, noisy, "AAAA:1111:01", "1234",
                                       allow_accepted_refreshes=True) == []


def test_the_strict_one_hello_form_still_rejects_the_multi_hello_shape():
    before, after, events = _reconnect_snapshots()
    problems = duo.reconnect_same_problems(before, after, events, _multi(events, 3), "AAAA:1111:01", "1234")
    assert any("exactly one accepted reconnect hello" in p for p in problems)


@pytest.mark.parametrize("name, mutate, expect", [
    ("a refused hello among them",
     lambda ev: _with_new_events(_multi(ev, 2), _hello_row("REJECTED — wrong save/slot")), "only accepted reconnect hellos"),
    ("a hello from another cartridge",
     lambda ev: _with_new_events(_multi(ev, 2), _hello_row("Connected (leafgreen, 2 mons)")), "another identity"),
    ("a hello with another party size",
     lambda ev: _with_new_events(_multi(ev, 2), _hello_row("Connected (firered, 3 mons)")), "another identity"),
    ("a duplicated gameplay event",
     lambda ev: _with_new_events(_multi(ev, 3), {"player": "a", "type": "capture", "text": "duplicate"}), "count changed"),
    ("a duplicated link event",
     lambda ev: _with_new_events(_multi(ev, 2), {"player": "b", "type": "linked", "text": "again"}), "count changed"),
    ("no hello at all",
     lambda ev: _with_new_events(ev, {"player": "b", "type": "hello", "text": "Connected (leafgreen, 2 mons)"}),
     "only accepted reconnect hellos"),
    ("a rewritten log",
     lambda ev: [_hello_row(), *ev[2:]], "shrank or was rewritten"),
])
def test_the_failing_multi_hello_shapes_are_each_rejected(name, mutate, expect):
    before, after, events = _reconnect_snapshots()
    mutated = mutate(events)
    problems = duo.reconnect_same_problems(before, after, events, mutated, "AAAA:1111:01", "1234",
                                           allow_accepted_refreshes=True)
    assert any(expect in p for p in problems), (name, problems)
    if "gameplay" not in name and "link event" not in name:
        assert duo.accepted_reconnect_hellos(events, mutated) is False, name


def test_the_multi_hello_shape_still_needs_the_servers_accepted_identity():
    before, after, events = _reconnect_snapshots()
    resumed = _multi(events, 3)
    refused = {**after, "status": {"players": {**after["status"]["players"],
                                               "a": {**after["status"]["players"]["a"], "identity_error": "Identity mismatch"}}}}
    assert any("accepted identity" in p for p in duo.reconnect_same_problems(
        before, refused, events, resumed, "AAAA:1111:01", "1234", allow_accepted_refreshes=True))
    assert any("OT ID changed" in p for p in duo.reconnect_same_problems(
        before, after, events, resumed, "AAAA:1111:01", "9999", allow_accepted_refreshes=True))


# ── a WITHHELD (party_hidden) refresh hello reports 0 mons and is accepted; nothing else that differs is ──────────────
# Evidence: duo_9ghb41vi (fc_frlgc_reconnect_gen3_fr_as_a_companion_d5a26da9): A's hellos 2, then 0 mons ("hello ... party=0",
# server.py:2228 counts len(msg.get("party", []))). The Gen 3 client always sends `party` (empty when withheld, lua/gen3/client.lua:1171-1195)
# plus party_hidden=true; server/state.py:1958-1959 and 2065 leave the stored party intact for that, but an EMPTY party that is not
# withheld replaces it (:2067, :2087). The 200-char TX hello cannot show party_hidden, so the driver logs HELLO_FACTS per hello.
FACTS_VISIBLE = {"party": 2, "hidden": False}
FACTS_WITHHELD = {"party": 0, "hidden": True}
FACTS_EMPTY = {"party": 0, "hidden": False}


def _baselined(events):
    return _with_new_events(events, _hello_row("Connected (firered, 2 mons)", "viridian_city"))


def _after(base, *rows):
    return _with_new_events(base, *rows)          # `rows` given NEWEST FIRST, as the server stores them


def _verdict(base, after, facts):
    before, aft, _events = _reconnect_snapshots()
    return (duo.accepted_reconnect_hellos(base, after, facts),
            duo.reconnect_same_problems(before, aft, base, after, "AAAA:1111:01", "1234",
                                        allow_accepted_refreshes=True, hello_facts=facts))


def test_hello_facts_are_read_in_order_from_the_clients_log():
    text = ("booted\nHELLO_FACTS party=2 party_hidden=false\nTX hello - {}\nHELLO_FACTS party=0 party_hidden=true\n"
            "HELLO_FACTS party=absent party_hidden=false\n")
    assert duo.hello_facts_from(text) == [{"party": 2, "hidden": False}, {"party": 0, "hidden": True},
                                          {"party": None, "hidden": False}]
    assert duo.hello_facts_from("") == [] and duo.hello_facts_from("HELLO_FACTS party=x party_hidden=true") == []


def test_a_withheld_zero_mon_refresh_hello_is_accepted():
    _b, _a, events = _reconnect_snapshots()
    base = _baselined(events)
    after = _after(base, _hello_row("Connected (firered, 0 mons)", "viridian_city_mart"), _hello_row("Connected (firered, 2 mons)"))
    assert _verdict(base, after, [FACTS_VISIBLE, FACTS_WITHHELD]) == (True, [])
    three = _after(base, _hello_row("Connected (firered, 0 mons)"), _hello_row("Connected (firered, 2 mons)"),
                   _hello_row("Connected (firered, 2 mons)"))
    assert _verdict(base, three, [FACTS_VISIBLE, FACTS_VISIBLE, FACTS_WITHHELD]) == (True, [])
    assert _verdict(base, _after(base, _hello_row("Connected (firered, 2 mons)")), []) == (True, [])   # no odd row: no facts needed


@pytest.mark.parametrize("name, rows, facts, expect", [
    ("present-and-empty, not withheld (the server empties party_keys)",
     ["Connected (firered, 0 mons)", "Connected (firered, 2 mons)"], [FACTS_VISIBLE, FACTS_EMPTY], "was not withheld"),
    ("a hello whose party field was absent and not withheld",
     ["Connected (firered, 0 mons)", "Connected (firered, 2 mons)"], [FACTS_VISIBLE, {"party": None, "hidden": False}],
     "was not withheld"),
    ("a different non-zero count",
     ["Connected (firered, 3 mons)", "Connected (firered, 2 mons)"], [FACTS_VISIBLE, FACTS_VISIBLE], "baseline party is 2"),
    ("a withheld hello that reports a count",
     ["Connected (firered, 1 mons)", "Connected (firered, 2 mons)"], [FACTS_VISIBLE, {"party": 1, "hidden": True}],
     "baseline party is 2"),
    ("facts that cannot be matched to the rows",
     ["Connected (firered, 0 mons)", "Connected (firered, 2 mons)"], [FACTS_WITHHELD], "cannot be matched"),
    ("no facts at all for an odd row",
     ["Connected (firered, 0 mons)", "Connected (firered, 2 mons)"], [], "cannot be matched"),
    ("another rom",
     ["Connected (leafgreen, 2 mons)", "Connected (firered, 2 mons)"], [FACTS_VISIBLE, FACTS_VISIBLE], "another identity"),
    ("only withheld hellos: nothing re-asserted the party",
     ["Connected (firered, 0 mons)"], [FACTS_WITHHELD], "baseline party"),
    ("the withheld fact belongs to a different hello",
     ["Connected (firered, 2 mons)", "Connected (firered, 0 mons)"], [FACTS_VISIBLE, FACTS_WITHHELD], "was not withheld"),
])
def test_every_other_zero_or_different_party_hello_still_fails(name, rows, facts, expect):
    _b, _a, events = _reconnect_snapshots()
    base = _baselined(events)
    after = _after(base, *[_hello_row(text) for text in rows])
    ok, problems = _verdict(base, after, facts)
    assert ok is False and any(expect in p for p in problems), (name, problems)


def test_the_relaxation_leaves_every_other_gate_of_the_multi_hello_shape_in_place():
    before, aft, events = _reconnect_snapshots()
    base = _baselined(events)
    mix = [_hello_row("Connected (firered, 0 mons)"), _hello_row("Connected (firered, 2 mons)")]
    facts = [FACTS_VISIBLE, FACTS_WITHHELD]

    def problems(after, status=aft, ot="1234"):
        return duo.reconnect_same_problems(before, status, base, after, "AAAA:1111:01", ot, allow_accepted_refreshes=True,
                                           hello_facts=facts)
    assert problems(_after(base, *mix)) == []
    assert any("only accepted" in p for p in problems(_after(base, _hello_row("REJECTED — wrong save/slot"), *mix)))
    assert any("count changed" in p for p in problems(_after(base, {"player": "a", "type": "capture", "text": "dup"}, *mix)))
    refused = {**aft, "status": {"players": {**aft["status"]["players"],
                                             "a": {**aft["status"]["players"]["a"], "identity_error": "x"}}}}
    assert any("accepted identity" in p for p in problems(_after(base, *mix), status=refused))
    assert any("OT ID changed" in p for p in problems(_after(base, *mix), ot="9999"))

@pytest.mark.parametrize("scenario", ["explode_new", "linked_faint_active_new", "explode_bench_battle_new"])
@pytest.mark.parametrize("case", ["timeout_catch", "miss_then_timeout", "timeout_then_caught", "post_catch_reorder", "no_capture", "wrong_key", "duplicate_capture", "saved_disagrees"])
def test_synth_timeout_throw_requires_later_capture_and_durable_ball_spend(tmp_path, scenario, case):
    run, paths, a_text, b_text = _linked_faint_fixture(tmp_path, scenario)
    proper = (b_text + "LOOP_HEAD_WRITE key=" + run._link_keys["b"] + "\n"
              + "BATTLE_FAINT_SITE " + run._link_keys["b"] + "\n"
              + f'TX {{"event":"faint","key":"{run._link_keys["b"]}"}}\n'
              + "TILEMAP_FAINTED offset=123\nBATTLE_RESULT b 2\n")
    # Producer-shaped replay of 5be32214's explode_new A receipt, lines 33-50:
    # the 600-frame wait returns before PARTY_COUNT and capture TX; the eventual
    # after-battle observation and saved bag agree that one ball was consumed.
    throws = "[hunt] threw ball index 0 -> timeout balls_before=20 balls_after=20\n"
    spent = 1
    if case == "miss_then_timeout":
        throws = ("[hunt] threw ball index 0 -> missed balls_before=20 balls_after=19\n"
                  "[hunt] threw ball index 0 -> timeout balls_before=19 balls_after=19\n")
        spent = 2
    elif case == "timeout_then_caught":
        throws += "[hunt] threw ball index 0 -> caught balls_before=19 balls_after=18\n"
        spent = 2
    capture_key = "FFFF:FFFF:FF" if case == "wrong_key" else run._link_keys["a"]
    capture = 'TX ' + json.dumps({"event": "capture", "gift": False, "key": capture_key,
                                 "player": "a", "area_id": "route_1"}) + "\n"
    if case == "no_capture":
        capture = ""
    elif case == "duplicate_capture":
        capture *= 2
    observed = (throws + "PARTY_COUNT 1 -> 2 @4986\n" + capture
                + f"[hunt] after battle: outcome=battle_over result=2 party=2 balls={20-spent} -> caught\n")
    if case == "post_catch_reorder":
        observed += "CAUGHT " + run._link_keys["a"] + "\nPARTY_COUNT 2 -> 1 @5615\nPARTY_COUNT 1 -> 2 @5616\n"
    a_text = a_text.replace("[hunt] threw ball index 0 -> caught balls_before=20 balls_after=19\n", observed)
    path, sram, _rom = paths["a"]
    sram[codec._BAG_COUNT + 2] = 20 - spent + (case == "saved_disagrees")
    _seal_main(sram)
    path.write_bytes(sram)
    results = {"a": a_text, "b": proper}
    if case in ("timeout_catch", "miss_then_timeout", "timeout_then_caught", "post_catch_reorder"):
        run.assert_linked_faint_saved(results, active=True)
    else:
        with pytest.raises(RuntimeError, match="SYNTH catch"):
            run.assert_linked_faint_saved(results, active=True)
