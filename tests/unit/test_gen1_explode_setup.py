"""O-6: qualified, disclosed explode setup and bounded runner policy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.adapters import gen1_codec as codec
from tools import e2e_duo as duo, gen1_synth_fixtures as synth

ROOT = Path(__file__).resolve().parents[2]
TITLES = ("red", "blue", "purered", "pureblue", "puregreen")
SYNTH_ROWS = ("explode_new", "linked_faint_active_new", "explode_bench_battle_new")


def _layout(title):
    return codec.for_foundation("gen1_purergb" if title.startswith("pure") else "gen1_rby")


@pytest.mark.parametrize("title", TITLES)
def test_explode_setup_changes_only_ball_quantity_and_checksum(monkeypatch, title):
    source = (ROOT / f"tests/fixtures/gen1/{title}_battle.SaveRAM").read_bytes()
    layout = _layout(title)
    seen = []
    monkeypatch.setattr(synth, "qualify", lambda raw, rom: seen.append((raw, rom)) or [])
    raw, disclosure = synth.build_explode_synth(title, source, b"companion")
    assert layout.verify_bank1(raw) and layout.bag_quantity(raw, codec.POKE_BALL) == 20
    assert len(source) == len(raw) == 0x8000
    count = source[layout.bag_count]
    quantity = next(layout.bag_count + 2 + 2 * slot for slot in range(count)
                    if source[layout.bag_count + 1 + 2 * slot] == codec.POKE_BALL)
    changed = {i for i, (a, b) in enumerate(zip(source, raw, strict=True)) if a != b}
    assert changed == {quantity, layout.sram_layout["sMainDataCheckSum"]}
    assert seen == [(source, b"companion"), (raw, b"companion")]
    assert disclosure["SYNTH"] is True and disclosure["behavior"] == "native"
    assert disclosure["base_sha256"] == hashlib.sha256(source).hexdigest()
    assert disclosure["fixture_sha256"] == hashlib.sha256(raw).hexdigest()
    assert disclosure["rom_sha1"] == hashlib.sha1(b"companion").hexdigest()
    assert disclosure["balls_before"] == layout.bag_quantity(source, codec.POKE_BALL)
    assert disclosure["balls_after"] == 20


@pytest.mark.parametrize("stage", ["base", "derived"])
def test_explode_setup_refuses_unqualified_bytes(monkeypatch, stage):
    source = (ROOT / "tests/fixtures/gen1/red_battle.SaveRAM").read_bytes()
    calls = []

    def qualify(raw, _rom):
        calls.append(raw)
        return ["broken save"] if (len(calls) == 1) == (stage == "base") else []

    monkeypatch.setattr(synth, "qualify", qualify)
    with pytest.raises(ValueError, match="broken save"):
        synth.build_explode_synth("red", source, b"companion")


@pytest.mark.parametrize("game", ["gen1_new", "gen1_pure", "gen1_pure_green"])
@pytest.mark.parametrize("scenario", SYNTH_ROWS)
def test_both_explode_halves_boot_the_disclosed_synth_and_oracles_use_it(monkeypatch, tmp_path, game, scenario):
    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(duo, "BUILD", str(tmp_path / "build"))
    monkeypatch.setattr(synth, "qualify", lambda *_args: [])
    rom = tmp_path / "companion.gb"
    rom.write_bytes(b"companion")
    run = duo.DuoRun(scenario, SimpleNamespace(game=game, lane=None, idle_jitter=0))
    monkeypatch.setattr(run, "_rom_for", lambda _inst: str(rom))
    for inst in ("a", "b"):
        title = run.gcfg["fixture"][inst]
        seeded = Path(run._seed_instance_save(inst))
        raw = seeded.read_bytes()
        assert _layout(title).bag_quantity(raw, codec.POKE_BALL) == 20
        baseline = Path(run._fixture_save_path(inst))
        assert baseline.is_relative_to(Path(run.data_dir))
        assert baseline != seeded and baseline.read_bytes() == raw
    lines = Path(run._pydec_path).read_text().splitlines()
    disclosures = [json.loads(line.removeprefix("GEN1_SYNTH_SETUP ")) for line in lines]
    assert {row["inst"] for row in disclosures} == {"a", "b"}
    assert all(row["SYNTH"] and row["balls_after"] == 20 for row in disclosures)


def test_duo_default_data_dir_uses_the_work_root_with_a_short_name(monkeypatch, tmp_path):
    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(duo, "BUILD", str(tmp_path / "build"))
    run = duo.DuoRun("explode_new", SimpleNamespace(game="gen1_new", lane=None, idle_jitter=0))
    assert Path(run.data_dir).parent == tmp_path / "work" / "tmp"
    assert len(Path(run.data_dir).name) <= 16


@pytest.mark.parametrize("game", ["gen1_new", "gen1_pure", "gen1_pure_green"])
def test_explode_can_reach_attempt_four_after_an_out_of_balls_miss(game):
    receipts = {"a": "RESULT: FAIL (link_new prerequisite failed: hunt ended out-of-balls)", "b": ""}
    limit = duo.scenario_attempt_limit("explode_new", game)
    assert limit == 4
    assert duo.retryable_gen1_rng(game, receipts, 3, limit, scenario="explode_new")
    assert not duo.retryable_gen1_rng(game, receipts, 4, limit, scenario="explode_new")
    assert not duo.retryable_gen1_rng(game, {**receipts, "b": "RESULT: FAIL (engine signals stopped)"},
                                     3, limit, scenario="explode_new")
    # Owner 2026-10-13: pureRGB species hunts now admit late ball misses; vanilla keeps the old rule.
    assert not duo.retryable_gen1_rng("gen1_new", receipts, 3, 8, scenario="species_clause_new")


def test_setup_snapshot_tampering_is_refused_by_the_oracle(monkeypatch, tmp_path):
    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(duo, "BUILD", str(tmp_path / "build"))
    monkeypatch.setattr(synth, "qualify", lambda *_args: [])
    rom = tmp_path / "companion.gb"
    rom.write_bytes(b"companion")
    run = duo.DuoRun("explode_new", SimpleNamespace(game="gen1_new", lane=None, idle_jitter=0))
    monkeypatch.setattr(run, "_rom_for", lambda _inst: str(rom))
    run._seed_instance_save("a")
    baseline = Path(run._fixture_save_path("a"))
    baseline.write_bytes(b"changed after qualification")
    with pytest.raises(RuntimeError, match="changed after qualification"):
        run._fixture_save_path("a")


def test_both_setup_saves_qualify_before_either_emulator_launches(monkeypatch, tmp_path):
    monkeypatch.setenv("SLINK_WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setattr(duo, "BUILD", str(tmp_path / "build"))
    rom = tmp_path / "companion.gb"
    rom.write_bytes(b"companion")
    run = duo.DuoRun("explode_new", SimpleNamespace(game="gen1_new", lane=None, idle_jitter=0))
    monkeypatch.setattr(run, "_rom_for", lambda _inst: str(rom))
    calls = []

    def qualify(_save, _rom):
        calls.append(True)
        return ["B base rejected"] if len(calls) == 3 else []

    monkeypatch.setattr(synth, "qualify", qualify)
    monkeypatch.setattr(run, "launch_instance", lambda *_a, **_kw: pytest.fail("launched before B qualified"))
    with pytest.raises(ValueError, match="B base rejected"):
        run.start_instances()


def test_generator_enforces_its_byte_diff_not_just_qualification(monkeypatch):
    source = (ROOT / "tests/fixtures/gen1/red_battle.SaveRAM").read_bytes()
    monkeypatch.setattr(synth, "qualify", lambda *_args: [])
    real = bytearray

    def corrupt(raw):
        changed = real(raw)
        changed[0x100] ^= 1
        return changed

    monkeypatch.setattr(synth, "bytearray", corrupt, raising=False)
    with pytest.raises(ValueError, match="byte.diff"):
        synth.build_explode_synth("red", source, b"rom")


def test_generator_refuses_a_noop_twenty_ball_source(monkeypatch):
    source = (ROOT / "tests/fixtures/gen1/red_battle.SaveRAM").read_bytes()
    monkeypatch.setattr(synth, "qualify", lambda *_args: [])
    first, _ = synth.build_explode_synth("red", source, b"rom")
    with pytest.raises(ValueError, match="byte.diff"):
        synth.build_explode_synth("red", first, b"rom")


def test_generator_gets_ball_ids_from_the_foundation_layout(monkeypatch):
    source = (ROOT / "tests/fixtures/gen1/purered_battle.SaveRAM").read_bytes()
    monkeypatch.setattr(synth, "qualify", lambda *_args: [])
    monkeypatch.setattr(codec, "POKE_BALL", 255)
    raw, _ = synth.build_explode_synth("purered", source, b"rom")
    assert _layout("purered").bag_quantity(raw, 4) == 20


@pytest.mark.parametrize("scenario", SYNTH_ROWS)
def test_each_synth_comparison_row_has_an_honest_attempt_budget(scenario):
    assert duo.SCENARIOS[scenario].get("gen1_synth") == "explode"
    expected = 4 if scenario == "explode_new" else 3
    assert duo.SCENARIOS[scenario].get("rng_attempts") == expected
    limit = duo.scenario_attempt_limit(scenario, "gen1_pure")
    miss = {"a": duo.RNG_OUT_OF_BALLS, "b": ""}
    assert limit == expected
    assert duo.retryable_gen1_rng("gen1_pure", miss, 2, limit, scenario=scenario)
    assert duo.retryable_gen1_rng("gen1_pure", miss, 3, limit, scenario=scenario) == (scenario == "explode_new")
    assert not duo.retryable_gen1_rng("gen1_pure", miss, 4, limit, scenario=scenario)


def test_late_retry_scope_belongs_to_the_tuple_entry(monkeypatch):
    monkeypatch.setattr(duo, "LATE_ATTEMPT_RNG",
                        ((duo.EXPLODE_BALL_MISS, frozenset({"model_row"})),))
    miss = {"a": duo.RNG_OUT_OF_BALLS, "b": ""}
    assert duo.retryable_gen1_rng("gen1_new", miss, 3, 4, scenario="model_row")
    assert not duo.retryable_gen1_rng("gen1_new", miss, 3, 4, scenario="explode_new")


def test_explode_ko_cannot_grant_late_retries_to_another_row():
    miss = {"a": f"RESULT: FAIL ({duo.EXPLODE_KO_MISS})", "b": ""}
    assert duo.retryable_gen1_rng("gen1_new", miss, 3, 4, scenario="explode_new")
    for other in ("linked_faint_active_new", "explode_bench_battle_new", "poison_new"):
        assert not duo.retryable_gen1_rng("gen1_new", miss, 3, 4, scenario=other)


def test_late_scopes_are_sets_and_missing_scenario_never_admits_scoped_phrase():
    assert all(isinstance(scope, frozenset) for _, scope in duo.LATE_ATTEMPT_RNG)
    for phrase in (duo.EXPLODE_KO_MISS, duo.EXPLODE_BALL_MISS):
        assert not duo.retryable_gen1_rng("gen1_new", {"a": f"RESULT: FAIL ({phrase})", "b": ""}, 3, 4)
