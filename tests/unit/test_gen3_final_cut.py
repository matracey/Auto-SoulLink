"""tools/gen3_final_cut.py (card G4-FINALCUT-RUNNER): the G4 final-cut pass as one command.
Pure Python -- no emulator is launched: every row runner is monkeypatched, and the only real
subprocesses are `git` against throwaway repos under tmp_path.
"""
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import gen3_final_cut as fc  # noqa: E402
import gen3_probe_receipt as receipts  # noqa: E402

LANE, MASTER = "L:/lane", "L:/master"
REAL_CLEAN_PLAN_TITLES = dict(fc.CLEAN_PLAN_TITLES)


@pytest.fixture(autouse=True)
def _clean_plans_run_as_machinery(monkeypatch):
    """The runner-mechanism tests (resume, carry, shard, cache) drive the default clean frlg plan with run_row
    mocked; the F1 up-front refusal of the clean titles is exercised by its own tests (REAL_CLEAN_PLAN_TITLES)."""
    monkeypatch.setattr(fc, "CLEAN_PLAN_TITLES", {})

# Runbook §1-§11 in order (G4_final_cut_runbook.md); §5 is mechanism P+H on both orientations.
EXPECTED_ROWS = [
    "states_firered_town", "states_firered_battle", "states_firered_trainer", "states_leafgreen_town",
    "states_leafgreen_battle", "states_leafgreen_trainer", "tutorials_firered", "tutorials_leafgreen",
    "faint_cmd_gen3_fr_as_a", "link_gen3_fr_as_a", "boxsync_gen3_fr_as_a",
    "reconnect_gen3_fr_as_a", "deadzone_gen3_fr_as_a",
    "whiteout_gen3_fr_as_a", "whiteout_gen3_lg_as_a",
    "center_controls_gen3_fr_as_a", "center_controls_gen3_lg_as_a",
    "linked_faint_active_gen3_fr_as_a", "linked_faint_active_gen3_lg_as_a",
    "active_end_gen3_fr_as_a", "active_end_gen3_lg_as_a",
    "linked_faint_active_whiteout_gen3_fr_as_a", "linked_faint_active_whiteout_gen3_lg_as_a",
    "linked_faint_active_trainer_gen3_fr_as_a", "linked_faint_active_trainer_gen3_lg_as_a",
    "checkpoint_firered", "checkpoint_leafgreen",
    "save_then_write_gen3_fr_as_a", "save_then_write_gen3_lg_as_a",
    "bootcheck_firered_party_town", "bootcheck_firered_party_town_b",
    "bootcheck_firered_party_battle", "bootcheck_firered_party_battle_b",
    "bootcheck_leafgreen_party_town", "bootcheck_leafgreen_party_town_b",
    "bootcheck_leafgreen_party_battle", "bootcheck_leafgreen_party_battle_b",
    "zip_build", "zip_check", "zip_boot_firered_refused",
    "item6_route_diff",
    "release_gate_quick", "probe_gates",
]


def _git(tree, *args):
    return subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(path):
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("one\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "one")
    return _git(path, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# --dry-run plan snapshot
# ---------------------------------------------------------------------------

def test_dry_run_prints_every_runbook_row_in_order(capsys):
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    assert fc.main(["--cut", cut, "--dry-run", "--lane", LANE, "--master", MASTER]) == 0
    out = capsys.readouterr().out
    ids = [ln.split()[1] for ln in out.splitlines() if ln.startswith("[")]
    assert ids == EXPECTED_ROWS
    assert f"rows={len(EXPECTED_ROWS)}" in out and f"# {len(EXPECTED_ROWS)} rows: RUN" in out
    # the runbook commands, verbatim
    assert "$ python tools/e2e_duo.py --game gen3_lgfr --scenario active_end_gen3" in out
    assert "$ python tools/e2e_duo.py --game gen3_frlg --scenario linked_faint_active_trainer_gen3" in out
    assert "--fixture tests/fixtures/gen3/leafgreen_party_battle_b.sav --title leafgreen" in out
    lane = os.path.abspath(LANE).replace("\\", "/")      # main() abspaths --lane; "L:/lane" is relative on POSIX (CI)
    assert f"check_release_zip.py {lane}/dist/SLink-player-g4-{cut[:8]}.zip --rev {cut}" in out
    assert "$ SLINK_LIVE=1 python -m pytest tests/live/test_gen3_probe_gates.py" in out
    assert f"--out checkpoint_lg_clean_{cut[:8]}.txt" in out
    # the superseded hold rows are gone (P+H, G4_request_draft.md 4aaaee7e)
    assert "trainer_bench_gen3" not in out


def test_dry_run_launches_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry-run must not provision or run")
    monkeypatch.setattr(fc, "provision", boom)
    monkeypatch.setattr(fc, "run_row", boom)
    monkeypatch.setattr(fc, "run_once", boom)
    assert fc.main(["--cut", "HEAD", "--dry-run", "--lane", LANE, "--master", MASTER]) == 0


def test_rows_selects_by_glob_or_item_keeping_order():
    rows = fc.build_plan("c" * 40, LANE, MASTER)
    got = [r.id for r in fc.select_rows(rows, "zip_*,item2b")]
    assert got == EXPECTED_ROWS[17:25] + ["zip_build", "zip_check", "zip_boot_firered_refused"]
    with pytest.raises(SystemExit):
        fc.select_rows(rows, "no_such_row")


# ---------------------------------------------------------------------------
# --title rr (card G5-RUNNER-RR): the G5 Radical Red plan, opt-in via --title
# ---------------------------------------------------------------------------

def _rr_scenario_ids():
    """The expected RR duo row ids, derived from e2e_duo.SCENARIOS itself (never hard-coded
    twice) -- every applicable scenario, including explicitly selected feature/recovery
    probes, minus the owner-signed limit."""
    import e2e_duo
    return [f"{s}_rr_as_a" for s, cfg in e2e_duo.SCENARIOS.items()
            if e2e_duo.scenario_applies(s, "gen3_rr") and not cfg.get("signed_limit")]


def test_rr_plan_includes_explicit_feature_and_recovery_probes():
    required = {"gift_gen3", "egg_hatch_gen3", "evolve_gen3", "npc_trade_gen3",
                "species_family_gen3", "shiny_bonus_gen3", "trade_lock_probe_gen3",
                "trade_reset_commit_gen3", "trade_reset_success_gen3"}
    rows = fc.build_plan_rr(CUT, LANE, MASTER)
    missing = required - {r.id.removesuffix("_rr_as_a") for r in rows}
    assert not missing, f"RR plan omits applicable probes: {sorted(missing)}"


def test_rr_plan_picks_up_future_explicit_rows_and_honors_applicability(monkeypatch):
    import e2e_duo
    monkeypatch.setitem(e2e_duo.SCENARIOS, "future_rr_probe", {
        "games": ("gen3_rr",), "explicit_only": True, "timeout": 1, "flags": []})
    monkeypatch.setitem(e2e_duo.SCENARIOS, "unavailable_rr_probe", {
        "games": ("gen3_rr",), "explicit_only": True, "timeout": 1, "flags": []})
    monkeypatch.setitem(e2e_duo.GAMES["gen3_rr"], "not_yet",
                        (*e2e_duo.GAMES["gen3_rr"].get("not_yet", ()), "unavailable_rr_probe"))
    names = fc.rr_scenarios()
    assert names[-1] == "future_rr_probe"
    assert "unavailable_rr_probe" not in names
    assert len(names) == len(set(names))


def test_rr_recovery_rows_keep_evidence_and_lock_probe_has_private_env(tmp_path):
    import e2e_duo
    lane = str(tmp_path / "lane")
    rows = fc.build_plan_rr(CUT, lane, MASTER)
    for name, cfg in e2e_duo.SCENARIOS.items():
        if not e2e_duo.scenario_applies(name, "gen3_rr") or cfg.get("signed_limit"):
            continue
        row = next(r for r in rows if r.id == name + "_rr_as_a")
        recovery = cfg.get("journal_lock_probe") or cfg.get("rr_reset_trade")
        assert ("--keep-data" in row.argv) == bool(recovery)
        if cfg.get("journal_lock_probe"):
            assert os.path.realpath(row.env["SLINK_JOURNAL_PROBE_ROOT"]) == os.path.realpath(lane)
            state = os.path.realpath(row.env["SLINK_STATE_DIR"])
            assert state != os.path.realpath(lane)
            assert os.path.commonpath([state, os.path.realpath(lane)]) == os.path.realpath(lane)
        else:
            assert row.env == {}


def test_title_defaults_to_frlg_and_leaves_the_default_plan_unchanged():
    # the --title flag's default plan is byte-for-byte the plan build_plan always produced
    # (EXPECTED_ROWS predates this card): row ids, order and count all unchanged.
    assert [r.id for r in fc.build_plan("c" * 40, LANE, MASTER)] == EXPECTED_ROWS


def test_dry_run_default_title_matches_frlg_plan(capsys):
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    assert fc.main(["--cut", cut, "--dry-run", "--lane", LANE, "--master", MASTER]) == 0
    out = capsys.readouterr().out
    ids = [ln.split()[1] for ln in out.splitlines() if ln.startswith("[")]
    assert ids == EXPECTED_ROWS


def test_rr_scenarios_excludes_the_signed_mega_limit():
    ids = fc.rr_scenarios()
    assert "linked_faint_active_mega_gen3" not in ids
    assert ids == [s[:-len("_rr_as_a")] for s in _rr_scenario_ids()]
    assert len(ids) >= 10   # a real plan, not an accidentally-empty filter


def test_rr_mega_skip_reason_does_not_match_the_stale_allowed_skips_entry():
    """The card's instruction: check whether ALLOWED_SKIPS already excuses mega's SKIP before
    reusing it. It does not -- the entry's reason substring is stale (an older draft's wording),
    so a live mega SKIP would still FAIL under it. Excluding the row (not launching it) is the
    only sound choice against the current ALLOWED_SKIPS, and this asserts why."""
    import e2e_duo
    mega = e2e_duo.SCENARIOS["linked_faint_active_mega_gen3"]
    assert mega.get("signed_limit")
    assert not fc.allowed_skip(
        "linked_faint_active_mega_gen3_rr_as_a",
        f"  linked_faint_active_mega_gen3: SKIP (allowed: signed limit) — "
        f"SIGNED LIMIT: {mega['signed_limit']}")


def test_build_plan_rr_row_ids_are_exactly_the_rr_scenarios_plus_gates_and_zip():
    rows = fc.build_plan_rr("c" * 40, LANE, MASTER)
    assert [r.id for r in rows] == _rr_scenario_ids() + ["rr_opcode_gates", "rr_zip_build", "rr_zip_check", "zip_boot_radicalred"]


def test_build_plan_rr_duo_rows_use_e2e_duo_with_the_rr_game():
    rows = fc.build_plan_rr("c" * 40, LANE, MASTER)
    faint = next(r for r in rows if r.id == "faint_cmd_gen3_rr_as_a")
    assert faint.command() == "python tools/e2e_duo.py --game gen3_rr --scenario faint_cmd_gen3"
    assert faint.cwd == LANE


def test_build_plan_rr_opcode_gates_row():
    rows = fc.build_plan_rr("c" * 40, LANE, MASTER)
    gates = next(r for r in rows if r.id == "rr_opcode_gates")
    assert gates.command() == \
        "python -m pytest tests/live/test_lua_gates.py -q -p no:randomly -rs"
    assert gates.env == {"SLINK_LIVE": "1"}


def test_build_plan_rr_boots_the_zip_on_the_rr_companion():
    rows = fc.build_plan_rr("c" * 40, LANE, MASTER)
    boot = next(r for r in rows if r.id == "zip_boot_radicalred")
    assert boot.argv[-2:] == ["--title", "radical_red"]
    rom, name, fixture, saveram, client, hello = fc.ZIP_BOOT["radical_red"]
    assert (rom, name, fixture, saveram) == ("patch/build/slink_RR.gba", "slink_RR.gba",
                                             "rr_town.sav", "slink RR.SaveRAM")
    import re
    line = "[SLink-gen3] gen3_rr/radical_red (companion by hash) player a -> 127.0.0.1:1 (rom ea5352f8)"
    assert re.search(client, line) and hello == "hello rom=firered_rr "


def test_the_fr_zip_rows_are_unchanged_and_disjoint_from_rr():
    import dataclasses
    fr = {r.id: dataclasses.asdict(r) for r in fc.build_plan("c" * 40, LANE, MASTER) if "zip" in r.id}
    cut8, z = "cccccccc", f"{LANE}/dist/SLink-player-g4-cccccccc.zip"
    assert {k: (v["item"], v["argv"], v["cwd"], v["budget"], v["emulator"], v["own_verdict"], v["env"])
            for k, v in fr.items()} == {
        "zip_build": ("§9 item5", [fc.PY, "tools/make_release.py", "--version", f"g4-{cut8}", "--out",
                                   f"{LANE}/dist", "--skip-generators"], LANE, 600, False, False, {}),
        "zip_check": ("§9 item5", [fc.PY, "tools/check_release_zip.py", z, "--rev", "c" * 40], LANE, 300,
                      False, False, {}),
        "zip_boot_firered_refused": ("§9 item5", [fc.PY, "tools/gen3_final_cut.py", "zip-boot", "--zip", z,
                                          "--lane", LANE], fc.REPO, 600, True, False, {})}
    rr = {r.id for r in fc.build_plan_rr("c" * 40, LANE, MASTER)}
    assert not (set(fr) & rr)   # receipts fc_<row>_<cut8>.txt never collide (OMP cx-f570e611)


def test_rr_rows_are_never_carried_until_their_rom_and_state_inputs_are_hashed():
    # OMP cx-42592031 F2-F4: row_inputs() hashes no RR ROM/fixture/gate state yet
    for r in fc.build_plan_rr("c" * 40, LANE, MASTER):
        assert r.deps is None, r.id


def test_rr_opcode_gates_owns_its_skip_policy():
    gates = next(r for r in fc.build_plan_rr("c" * 40, LANE, MASTER) if r.id == "rr_opcode_gates")
    assert fc.judge(gates.id, 0, "26 passed, 12 skipped in 900s", own_verdict=gates.own_verdict) == ("PASS", True)


def test_a_new_rr_signed_limit_fails_loudly(monkeypatch):
    import e2e_duo
    monkeypatch.setitem(e2e_duo.SCENARIOS["faint_cmd_gen3"], "signed_limit", "x")
    with pytest.raises(RuntimeError, match="new RR signed limit"):
        fc.rr_scenarios()


def test_dry_run_title_rr_plan(capsys):
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    assert fc.main(["--cut", cut, "--title", "rr", "--dry-run", "--lane", LANE,
                    "--master", MASTER]) == 0
    out = capsys.readouterr().out
    ids = [ln.split()[1] for ln in out.splitlines() if ln.startswith("[")]
    assert ids == _rr_scenario_ids() + ["rr_opcode_gates", "rr_zip_build", "rr_zip_check", "zip_boot_radicalred"]
    assert "$ python tools/e2e_duo.py --game gen3_rr --scenario faint_cmd_gen3" in out
    assert "$ SLINK_LIVE=1 python -m pytest tests/live/test_lua_gates.py" in out
    assert "zip-boot --zip" in out and "--title radical_red" in out
    assert "_rr.txt" in out.split("summary ")[-1]   # RR summary never overwrites FR's


def test_title_invalid_choice_rejected():
    with pytest.raises(SystemExit):
        fc.main(["--cut", "HEAD", "--title", "bogus", "--dry-run", "--lane", LANE,
                "--master", MASTER])


# ---------------------------------------------------------------------------
# --title emerald (card E4b-FINALCUT): the E4b Emerald plan, opt-in via --title
# ---------------------------------------------------------------------------

EMERALD_EXPECTED_ROWS = [
    "profile_generated_check", "write_checkpoint_generated_check", "area_map_generated_emerald",
    "unit_emerald",
    "states_emerald_town", "states_emerald_battle", "states_emerald_trainer",
    "checkpoint_emerald",
    "faint_cmd_gen3_em_as_a", "reconnect_gen3_em_as_a", "deadzone_gen3_em_as_a",
    "link_gen3_em_as_a", "boxsync_gen3_em_as_a", "linked_faint_active_gen3_em_as_a",
    "whiteout_gen3_em_as_a",   # E4c: Emerald's own outdoor-landing receipt
    "bootcheck_emerald_town", "bootcheck_emerald_town_b",
    "bootcheck_emerald_battle", "bootcheck_emerald_battle_b",
    "probe_gates_emerald", "shadow_negatives_emerald",
    "emerald_zip_build", "emerald_zip_check", "zip_boot_emerald_refused",
]


def test_build_plan_emerald_row_ids_are_exactly_the_expected_rows():
    assert [r.id for r in fc.build_plan_emerald("c" * 40, LANE, MASTER)] == EMERALD_EXPECTED_ROWS


def test_dry_run_title_emerald_plan(capsys):
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    assert fc.main(["--cut", cut, "--title", "emerald", "--dry-run", "--lane", LANE,
                    "--master", MASTER]) == 0
    out = capsys.readouterr().out
    ids = [ln.split()[1] for ln in out.splitlines() if ln.startswith("[")]
    assert ids == EMERALD_EXPECTED_ROWS
    assert "$ python tools/e2e_duo.py --game gen3_emerald --scenario faint_cmd_gen3" in out
    assert "$ python tools/gen_gen3_profile.py --check" in out
    assert "$ python tools/gen_area_map.py --game emerald" in out
    # unit_emerald (E7-SKIPS): selected by FILE, never -k -- a -k selector still collects every
    # module under tests/unit first, which fired unrelated module-level skips (purergb,
    # pokecrystal) and incidentally matched unrelated Gen 2 parametrize ids.
    assert "tests/unit/test_gen3_codec_emerald.py" in out
    assert "tests/unit/test_gen3_title_syms.py" in out
    assert "-k 'gen3 and emerald'" not in out
    assert "-k emerald" in out
    assert "zip-boot --zip" in out and "--title emerald" in out
    assert "_emerald.txt" in out.split("summary ")[-1]   # its own summary, never FR's or RR's


def test_title_emerald_pin_is_appended_only_to_emerald_provision_text(capsys):
    # frlg's own provision preamble is untouched by --title emerald existing at all (the falsifier
    # this card's brief asked for: capture frlg/rr --dry-run before vs after the edit).
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    fc.main(["--cut", cut, "--dry-run", "--lane", LANE, "--master", MASTER])
    frlg_provision = [ln for ln in capsys.readouterr().out.splitlines() if "pinned inputs" in ln]
    fc.main(["--cut", cut, "--title", "emerald", "--dry-run", "--lane", LANE, "--master", MASTER])
    emerald_provision = [ln for ln in capsys.readouterr().out.splitlines() if "pinned inputs" in ln]
    assert "Emerald" not in frlg_provision[0] and "emerald" not in frlg_provision[0]
    assert "emerald" in emerald_provision[0].lower()
    assert emerald_provision[0].startswith(frlg_provision[0])   # strictly appended, nothing removed


def test_emerald_pret_clone_is_a_shared_unpinned_input(capsys):
    """.cache/pret/pokeemerald/ was an Emerald-only UNPINNED input (card E4b-CKPT review, OMP
    cx-6b619663 F1) until EXPLODE-BIND made the shared unit lane read it too (the FR/LG
    release_gate_quick failed 3 tests in its lane without it); now every plan provisions it once."""
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    fc.main(["--cut", cut, "--dry-run", "--lane", LANE, "--master", MASTER])
    frlg = next(ln for ln in capsys.readouterr().out.splitlines() if "unpinned inputs" in ln)
    fc.main(["--cut", cut, "--title", "emerald", "--dry-run", "--lane", LANE, "--master", MASTER])
    emerald = next(ln for ln in capsys.readouterr().out.splitlines() if "unpinned inputs" in ln)
    assert ".cache/pret/pokeemerald/" in frlg
    assert emerald == frlg


def test_build_plan_emerald_duo_rows_use_e2e_duo_with_the_emerald_game():
    rows = fc.build_plan_emerald("c" * 40, LANE, MASTER)
    faint = next(r for r in rows if r.id == "faint_cmd_gen3_em_as_a")
    assert faint.command() == "python tools/e2e_duo.py --game gen3_emerald --scenario faint_cmd_gen3"
    assert faint.cwd == LANE


def test_build_plan_emerald_bootcheck_rows_reference_their_own_fixtures():
    rows = fc.build_plan_emerald("c" * 40, LANE, MASTER)
    town = next(r for r in rows if r.id == "bootcheck_emerald_town")
    assert town.command() == (
        "python tools/gen3_fixtures.py boot-check --rom 'Pokemon - Emerald Version (USA, Europe).gba' "
        "--fixture tests/fixtures/gen3/emerald_town.sav --title emerald "
        "--saveram-name 'Pokemon - Emerald Version (USA, Europe).SaveRAM'")
    assert town.deps == fc.row_deps(town)
    # F6 (E4b-CKPT review): never carried until row_inputs() actually hashes the Emerald ROM
    assert town.deps is None


def test_build_plan_emerald_boots_the_zip_on_emerald():
    rows = fc.build_plan_emerald("c" * 40, LANE, MASTER)
    boot = next(r for r in rows if r.id == "zip_boot_emerald_refused")
    assert boot.argv[-2:] == ["--title", "emerald"]
    rom, name, fixture, saveram, client, hello = fc.ZIP_BOOT["emerald"]
    assert (rom, fixture) == (fc.STAGED["emerald"], "emerald_town.sav")
    # patch-first: the shipped client REFUSES a clean Emerald, so this row proves the refusal (no hello)
    line = ("Unsupported Gen 3 cartridge: this emerald cartridge needs the SLink companion patch; "
            "prepare it through the Manager or /patcher. Supported: FireRed, LeafGreen, Radical Red and Emerald.")
    assert re.search(client, line) and hello is None
    assert not re.search(client, "[SLink-gen3] gen3_emerald/emerald (clean by hash) player a -> 127.0.0.1:1 (rom ea5352f8)")


def test_the_fr_and_rr_zip_rows_stay_disjoint_from_emeralds():
    fr = {r.id for r in fc.build_plan("c" * 40, LANE, MASTER) if "zip" in r.id}
    rr = {r.id for r in fc.build_plan_rr("c" * 40, LANE, MASTER) if "zip" in r.id}
    em = {r.id for r in fc.build_plan_emerald("c" * 40, LANE, MASTER) if "zip" in r.id}
    assert not (fr & em) and not (rr & em)   # receipts fc_<row>_<cut8>.txt never collide


def test_emerald_rows_are_never_carried_until_their_rom_and_state_inputs_are_hashed():
    for r in fc.build_plan_emerald("c" * 40, LANE, MASTER):
        assert r.deps is None or r.id.startswith(("bootcheck_", "checkpoint_")), r.id


def test_area_map_generated_emerald_row_uses_a_real_check_not_tracked_dirty():
    """card E4b-CKPT review (OMP cx-6b619663 F5/F8): the row now runs gen_area_map.py's own
    --check (writes nothing, exits 1 on drift) instead of writing in place and relying on the
    runner's generic tracked-dirty-after-the-row abort to catch a stale committed file."""
    row = next(r for r in fc.build_plan_emerald("c" * 40, LANE, MASTER)
               if r.id == "area_map_generated_emerald")
    assert row.argv == [fc.PY, "tools/gen_area_map.py", "--game", "emerald", "--check"]


def test_gen_area_map_check_behaviourally_detects_drift_and_writes_nothing_falsifier(tmp_path):
    """The actual behavioural falsifier F5/F8 asked for: a tampered committed file is reported
    STALE and exit 1; nothing on disk is touched by the check itself."""
    import gen_area_map as gam
    area_dir = tmp_path / "data" / "games" / "gen3_emerald"
    area_dir.mkdir(parents=True)
    committed = area_dir / "gen3_emerald_areas.lua"
    committed.write_text("stale content\n", encoding="utf-8")
    before = committed.stat().st_mtime_ns
    ok = gam._write_or_check(str(committed), "fresh content\n", check=True)
    assert ok is False
    assert committed.read_text(encoding="utf-8") == "stale content\n"   # never overwritten
    assert committed.stat().st_mtime_ns == before


def test_checkpoint_emerald_row_uses_the_emerald_pack_not_frlgs():
    """card E4b-CKPT: gen3_probe_receipt.py's build_env() now picks the checkpoint pack by title
    (CHECKPOINT_PACK), so a --title emerald run reads Emerald's own pack, not FRLG's -- the
    inverse of this test's former falsifier (test_no_checkpoint_emerald_row_because_the_receipt_
    tool_hardcodes_frlg), which is why the row can exist now."""
    env = receipts.build_env("emerald", LANE, ["script_running"], "clean")
    assert env["SLINK_GEN3_CHECKPOINT"] == f"{LANE}/data/games/gen3_emerald/write_checkpoint.json"
    row = next(r for r in fc.build_plan_emerald("c" * 40, LANE, MASTER) if r.id == "checkpoint_emerald")
    assert row.command() == (
        "python tools/gen3_probe_receipt.py --title emerald --lane " + LANE +
        " --out checkpoint_emerald_clean_cccccccc.txt")
    assert row.cwd == fc.REPO
    assert "--rows" not in row.argv   # no restriction: the pack's own artifacts table decides


def test_checkpoint_emerald_row_never_names_bw_rows():
    """A future --rows edit to this row must not reintroduce bw_* names for emerald -- gen3_probe_
    receipt.py refuses them by name (NO_BW_TITLES), so naming one here would just fail the row."""
    row = next(r for r in fc.build_plan_emerald("c" * 40, LANE, MASTER) if r.id == "checkpoint_emerald")
    assert not any(a.startswith("bw_") for a in row.argv)


# ---------------------------------------------------------------------------
# zip_boot_emerald_refused's EG4 precondition (ruling 24): BLOCKED-EG4, not PASS, not a silent skip
# ---------------------------------------------------------------------------

def _write_entry_lua(tmp_path, routed_emerald):
    lua_dir = tmp_path / "lua"
    (lua_dir / "gen3").mkdir(parents=True)
    routed = "gen3_frlg = true, gen3_rr = true" + (", gen3_emerald = true" if routed_emerald else "")
    (lua_dir / "gen3" / "entry.lua").write_text(f"Entry.ROUTED = {{ {routed} }}\n", encoding="utf-8")
    return lua_dir


def test_emerald_admission_blocker_true_before_eg4(tmp_path):
    lua_dir = _write_entry_lua(tmp_path, routed_emerald=False)
    kind, reason = fc.emerald_admission_blocker(str(lua_dir))
    assert kind == "BLOCKED-EG4" and "EG4" in reason


def test_emerald_admission_blocker_none_once_routed(tmp_path):
    lua_dir = _write_entry_lua(tmp_path, routed_emerald=True)
    assert fc.emerald_admission_blocker(str(lua_dir)) is None


def test_emerald_admission_blocker_missing_entry_lua(tmp_path):
    kind, _reason = fc.emerald_admission_blocker(str(tmp_path))
    assert kind == "ZIP-DEFECT"


def test_zip_boot_emerald_skips_blocked_before_launching_anything(tmp_path, monkeypatch, capsys):
    """zip_boot() must refuse Emerald BEFORE touching the server/emulator when the extracted
    zip's own entry.lua is not yet routed -- the whole point is never attempting a boot the
    shipped client would refuse by design."""
    zip_path = tmp_path / "z.zip"
    lua_dir = tmp_path / "extract" / "lua"
    (lua_dir / "gen3").mkdir(parents=True)
    (lua_dir / "gen3" / "entry.lua").write_text("Entry.ROUTED = { gen3_frlg = true }\n",
                                                encoding="utf-8")
    (lua_dir / "slink.lua").write_text("-- stub\n", encoding="utf-8")
    import zipfile
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in (lua_dir / "gen3" / "entry.lua", lua_dir / "slink.lua"):
            zf.write(f, f.relative_to(tmp_path / "extract"))

    def _boom(*a, **k):
        raise AssertionError("zip_boot must not reach the server/emulator launch")
    monkeypatch.setattr(fc.subprocess, "Popen", _boom)
    rc = fc.zip_boot(str(zip_path), str(tmp_path), title="emerald")
    out = capsys.readouterr().out
    assert rc == 3
    assert "BLOCKED-EG4" in out and "SKIP" in out


def test_the_zip_boot_emerald_skip_is_no_longer_allowed_now_that_eg4_has_landed():
    """EG4 (ruling 24) landed on this branch (lua/gen3/entry.lua:108's Entry.ROUTED already admits
    gen3_emerald), so the ("zip_boot_emerald_refused", "BLOCKED-EG4:") ALLOWED_SKIPS entry was retired: a
    zip reaching BLOCKED-EG4 now means a stale/pre-EG4 zip, a real FAIL, not an excused SKIP."""
    out = ("  zip_boot_emerald_refused: SKIP (not allowed: EG4 has landed) — BLOCKED-EG4: lua/gen3/"
           "entry.lua Entry.ROUTED has no gen3_emerald entry in this zip (ruling 24: it flips "
           "only at EG4)")
    verdict, ok = fc.judge("zip_boot_emerald_refused", 3, out)
    assert not ok and verdict == "FAIL skipped, and ALLOWED_SKIPS does not excuse it"


def test_an_unrelated_row_named_zip_boot_emerald_like_is_not_confused():
    # allowed_skip() fnmatches the row id; a real skip on a DIFFERENT row must not borrow this
    # ruling just because its output happens to mention BLOCKED-EG4.
    assert fc.allowed_skip("zip_build", "... BLOCKED-EG4: ...") is None


# ---------------------------------------------------------------------------
# card E4b-CKPT review (OMP cx-6b619663, coordinator-verified F2/F7): build_kind/row_inputs/
# chain_of only knew firered/leafgreen, so predicted_checkpoint_inputs (the --carry path)
# crashed for checkpoint_emerald; the zip chain and the states/checkpoint chain must group as one
# shard unit; zip_boot_emerald_refused must not confuse a malformed zip with the EG4 precondition.
# ---------------------------------------------------------------------------

def test_build_kind_accepts_emerald_states_rows_falsifier():
    """F7: build_kind's regex knew only firered/leafgreen, so build_key's `kind, title =
    build_kind(row.id)` raised TypeError (unpacking None) for every states_emerald_* /
    checkpoint_emerald row -- reachable from plan_decisions() whenever --carry is passed."""
    assert fc.build_kind("states_emerald_town") == ("town", "emerald")
    assert fc.build_kind("states_emerald_battle") == ("battle", "emerald")
    assert fc.build_kind("states_emerald_trainer") == ("trainer", "emerald")
    assert fc.build_kind("tutorials_emerald") is None   # no such row -- emerald has no tutorials


def test_predicted_checkpoint_inputs_does_not_crash_for_emerald_falsifier(tmp_path):
    lane = str(tmp_path)
    row = fc.Row("checkpoint_emerald", "§13.2b checkpoint", [], lane, 0)
    cut = _git(fc.REPO, "rev-parse", "HEAD")   # a real cut: tree_blobs() needs a real tree object
    # the lane (an empty tmp_path) has no staged ROM and no cache entries, so every build misses
    # -> None, not the F7 TypeError (unpacking build_kind(None) inside build_key)
    assert fc.predicted_checkpoint_inputs(row, cut, lane) is None


def test_row_inputs_checkpoint_emerald_uses_towns_battle_trainer_not_tutorials():
    """Emerald has no oldman/pokedude tutorial states; its checkpoint depends on the three
    states_emerald_* builds (town/battle/trainer) instead of FR/LG's town/battle/tutorials."""
    lane = "L:/lane"
    row = fc.Row("checkpoint_emerald", "§13.2b checkpoint", [], lane, 0)
    inputs = fc.row_inputs(row, lane)
    assert inputs["rom:emerald"] == os.path.join(lane, fc.STAGED["emerald"])
    joined = " ".join(inputs.values()).replace("\\", "/")
    assert "gen3_probe_states_c4p2/emerald/slink_pretrainer.State" in joined
    assert "gen3_probe_states_c4p2/emerald/slink_prefaint.State" in joined
    assert "slink_oldman" not in joined and "slink_pokedude" not in joined


def test_row_inputs_checkpoint_firered_is_unchanged_by_the_emerald_generalisation():
    lane = "L:/lane"
    row = fc.Row("checkpoint_firered", "§6 item3", [], lane, 0)
    inputs = fc.row_inputs(row, lane)
    joined = " ".join(inputs.values()).replace("\\", "/")
    assert "gen3_probe_states/firered/slink_oldman.State" in joined
    assert "gen3_probe_states/firered/slink_pokedude.State" in joined


def test_build_key_uses_emeralds_own_fixture_naming_falsifier(tmp_path):
    """F7 (parity with FR): Emerald's fixtures are emerald_<kind>.sav (no '_party_' infix, and
    'trainer' is not remapped to 'town' the way BUILD_FIXTURE does for FR/LG)."""
    lane = tmp_path
    rom_rel = fc.STAGED["emerald"]
    (lane / rom_rel).parent.mkdir(parents=True, exist_ok=True)
    (lane / rom_rel).write_bytes(b"rom-bytes")
    row = fc.Row("states_emerald_trainer", "§13.2 build", [], str(lane), 0)
    blobs = {"tests/fixtures/gen3/emerald_trainer.sav": "deadbeef" * 5}
    key, manifest = fc.build_key(row, "c" * 40, str(lane), blobs=blobs, bizhawk={})
    assert key is not None, manifest
    assert "tests/fixtures/gen3/emerald_trainer.sav" in manifest["git"]


def test_chain_of_groups_emerald_states_and_checkpoint_falsifier():
    assert fc.chain_of("states_emerald_town") == "probe_emerald"
    assert fc.chain_of("states_emerald_battle") == "probe_emerald"
    assert fc.chain_of("states_emerald_trainer") == "probe_emerald"
    assert fc.chain_of("checkpoint_emerald") == "probe_emerald"


def test_chain_of_frlg_states_are_unchanged():
    assert fc.chain_of("states_firered_town") == "probe_firered"
    assert fc.chain_of("checkpoint_leafgreen") == "probe_leafgreen"


def test_chain_of_groups_the_emerald_zip_rows_falsifier():
    """F2: the old startswith(("zip_", "rr_zip_")) test missed emerald_zip_build/
    emerald_zip_check entirely (they returned their OWN id, i.e. a singleton chain each),
    scattering them from zip_boot_emerald_refused across shards."""
    assert fc.chain_of("emerald_zip_build") == fc.chain_of("emerald_zip_check") == \
        fc.chain_of("zip_boot_emerald_refused")


def test_chain_of_fr_and_rr_zip_rows_are_unchanged():
    assert fc.chain_of("zip_build") == fc.chain_of("zip_check") == fc.chain_of("zip_boot_firered_refused")
    assert fc.chain_of("rr_zip_build") == fc.chain_of("rr_zip_check") == \
        fc.chain_of("zip_boot_radicalred")


@pytest.mark.parametrize("n", [2, 3])
def test_shard_rows_keeps_the_emerald_checkpoint_chain_together_falsifier(n):
    rows = fc.build_plan_emerald("c" * 40, LANE, MASTER)
    est = {r.id: r.budget for r in rows}
    shards = fc.shard_rows(rows, n, est)
    homes = {r.id: i for i, shard in enumerate(shards) for r in shard}
    chain = ["states_emerald_town", "states_emerald_battle", "states_emerald_trainer",
            "checkpoint_emerald"]
    assert len({homes[c] for c in chain}) == 1


@pytest.mark.parametrize("n", [2, 3])
def test_shard_rows_keeps_the_emerald_zip_chain_together_falsifier(n):
    rows = fc.build_plan_emerald("c" * 40, LANE, MASTER)
    est = {r.id: r.budget for r in rows}
    shards = fc.shard_rows(rows, n, est)
    homes = {r.id: i for i, shard in enumerate(shards) for r in shard}
    zips = ["emerald_zip_build", "emerald_zip_check", "zip_boot_emerald_refused"]
    assert len({homes[z] for z in zips}) == 1


# --- F3/F4: emerald_admission_blocker distinguishes a malformed zip from the EG4 precondition ---

def test_admission_blocker_missing_entry_lua_is_a_zip_defect_falsifier(tmp_path):
    kind, reason = fc.emerald_admission_blocker(str(tmp_path))
    assert kind == "ZIP-DEFECT"
    assert "BLOCKED-EG4" not in reason


def test_admission_blocker_no_routed_assignment_is_a_zip_defect_falsifier(tmp_path):
    lua_dir = tmp_path / "lua"
    (lua_dir / "gen3").mkdir(parents=True)
    (lua_dir / "gen3" / "entry.lua").write_text("-- no ROUTED table at all\n", encoding="utf-8")
    kind, reason = fc.emerald_admission_blocker(str(lua_dir))
    assert kind == "ZIP-DEFECT"
    assert "BLOCKED-EG4" not in reason


@pytest.mark.parametrize("routed_line", [
    "Entry.ROUTED = { gen3_frlg = true, gen3_rr = true }",
    'Entry.ROUTED = { gen3_frlg = true, ["gen3_emerald"] = false }',
])
def test_admission_blocker_unrouted_emerald_is_blocked_eg4_falsifier(tmp_path, routed_line):
    lua_dir = tmp_path / "lua"
    (lua_dir / "gen3").mkdir(parents=True)
    (lua_dir / "gen3" / "entry.lua").write_text(routed_line + "\n", encoding="utf-8")
    kind, reason = fc.emerald_admission_blocker(str(lua_dir))
    assert kind == "BLOCKED-EG4"
    assert "ruling 24" in reason


@pytest.mark.parametrize("routed_line", [
    "Entry.ROUTED = { gen3_frlg = true, gen3_emerald = true }",
    'Entry.ROUTED = { gen3_frlg = true, ["gen3_emerald"] = true }',
    "Entry.ROUTED = { gen3_frlg = true, ['gen3_emerald'] = true }",
])
def test_admission_blocker_accepts_both_spellings_falsifier(tmp_path, routed_line):
    lua_dir = tmp_path / "lua"
    (lua_dir / "gen3").mkdir(parents=True)
    (lua_dir / "gen3" / "entry.lua").write_text(routed_line + "\n", encoding="utf-8")
    assert fc.emerald_admission_blocker(str(lua_dir)) is None


def test_admission_blocker_anchors_on_the_routed_assignment_not_any_brace_falsifier(tmp_path):
    """F4: an unrelated {...} block earlier in the file must not be mistaken for ROUTED."""
    lua_dir = tmp_path / "lua"
    (lua_dir / "gen3").mkdir(parents=True)
    (lua_dir / "gen3" / "entry.lua").write_text(
        "local UNRELATED = { gen3_emerald = true }\n"
        "Entry.ROUTED = { gen3_frlg = true }\n", encoding="utf-8")
    kind, reason = fc.emerald_admission_blocker(str(lua_dir))
    assert kind == "BLOCKED-EG4"   # the real ROUTED table, not the decoy, is what is checked


def test_judge_on_a_zip_defect_is_a_plain_fail_falsifier():
    """F3: a malformed zip must FAIL, never read as the (allowed) EG4 skip."""
    out = "RESULT: FAIL ZIP-DEFECT: the extracted zip has no lua/gen3/entry.lua"
    verdict, ok = fc.judge("zip_boot_emerald_refused", 1, out)
    assert not ok
    assert not verdict.startswith("SKIP")


def test_zip_boot_defect_never_launches_the_server_falsifier(tmp_path, monkeypatch, capsys):
    zip_path = tmp_path / "z.zip"
    lua_dir = tmp_path / "extract" / "lua"
    lua_dir.mkdir(parents=True)
    (lua_dir / "slink.lua").write_text("-- stub\n", encoding="utf-8")   # no gen3/entry.lua at all
    import zipfile
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(lua_dir / "slink.lua", (lua_dir / "slink.lua").relative_to(tmp_path / "extract"))

    def _boom(*a, **k):
        raise AssertionError("zip_boot must not reach the server/emulator launch")
    monkeypatch.setattr(fc.subprocess, "Popen", _boom)
    rc = fc.zip_boot(str(zip_path), str(tmp_path), title="emerald")
    out = capsys.readouterr().out
    assert rc == 1
    assert "ZIP-DEFECT" in out and "BLOCKED-EG4" not in out


# ---------------------------------------------------------------------------
# the retry classifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("output,killed,want", [
    ("PYDEC: FAIL timed out after 900s waiting for both players connected\n"
     "  faint_cmd_gen3: FAIL (attempt 1 of 1) — TimeoutError: timed out after 900s", False,
     "contention"),
    ("[gate] TIMEOUT after 600s — killed\n[gate] mkstates_gen3: (no RESULT line)", False,
     "contention"),
    ("half-way output with no verdict", True, "contention"),
    ("[duo] RESULT_LINE a: RESULT: FAIL (the second save: SAVE failed)\n"
     "timed out after 900s waiting for b", False, "real"),
    ("[duo] x: a client finished before 'READY' (a: no RESULT; b: RESULT: PASS)", False, "real"),
    ("E   AssertionError: queued", False, "real"),
    ("Traceback (most recent call last):\nPermissionError: [WinError 32]", False, "real"),
    ("  faint_cmd_gen3: FAIL (attempt 1 of 1)", False, "real"),
])
def test_classify_failure(output, killed, want):
    assert fc.classify_failure(output, killed) == want


def _stub_row_env(monkeypatch, tmp_path, outputs):
    calls = []

    def fake_run_once(row, deadline):
        calls.append(row.id)
        return outputs[len(calls) - 1]
    monkeypatch.setattr(fc, "run_once", fake_run_once)
    monkeypatch.setattr(fc, "tracked_clean", lambda tree: True)
    monkeypatch.setattr(fc, "load_snapshot", lambda: "cpu=99%")
    monkeypatch.setattr(fc, "rewind_violations", lambda tree, since: [])
    monkeypatch.setattr(fc, "PROBES", str(tmp_path))
    return calls


def test_contention_timeout_gets_exactly_one_retry(monkeypatch, tmp_path):
    timeout = (1, "timed out after 900s waiting for both players connected", False, False)
    calls = _stub_row_env(monkeypatch, tmp_path, [timeout, timeout, timeout])
    row = fc.build_plan("c" * 40, LANE, MASTER)[6]
    verdict, n, _clean = fc.run_row(row, "c" * 40, LANE, None)
    assert n == 2 and len(calls) == 2 and verdict.startswith("FAIL")
    with open(fc.receipt_path(row.id, "c" * 40), encoding="utf-8") as f:
        text = f.read()
    assert "--- attempt 2 of 2 ---" in text and "classification=contention" in text


def test_contention_then_pass(monkeypatch, tmp_path):
    outs = [(1, "timed out after 900s waiting for x", False, False), (0, "ok", False, False)]
    _stub_row_env(monkeypatch, tmp_path, outs)
    row = fc.build_plan("c" * 40, LANE, MASTER)[6]
    assert fc.run_row(row, "c" * 40, LANE, None)[:2] == ("PASS", 2)


def test_real_failure_is_never_retried(monkeypatch, tmp_path):
    calls = _stub_row_env(monkeypatch, tmp_path, [(1, "[duo] RESULT_LINE a: RESULT: FAIL (x)",
                                                   False, False)])
    row = fc.build_plan("c" * 40, LANE, MASTER)[6]
    verdict, n, _ = fc.run_row(row, "c" * 40, LANE, None)
    assert n == 1 and calls == [row.id] and verdict == "FAIL exit=1"


def test_a_row_that_dirties_the_lane_fails(monkeypatch, tmp_path):
    _stub_row_env(monkeypatch, tmp_path, [(0, "ok", False, False)])
    states = iter([True, False])
    monkeypatch.setattr(fc, "tracked_clean", lambda tree: next(states))
    row = fc.build_plan("c" * 40, LANE, MASTER)[0]
    verdict, _n, clean_after = fc.run_row(row, "c" * 40, LANE, None)
    assert verdict.startswith("FAIL") and not clean_after


def test_rewind_on_fails_a_passing_row(monkeypatch, tmp_path):
    _stub_row_env(monkeypatch, tmp_path, [(0, "ok", False, False)])
    monkeypatch.setattr(fc, "rewind_violations", lambda tree, since: ["x.ini"])
    row = fc.build_plan("c" * 40, LANE, MASTER)[0]
    assert fc.run_row(row, "c" * 40, LANE, None)[0].startswith("FAIL rewind on")


def test_rewind_violations_reads_back_the_configs(tmp_path):
    build = tmp_path / "patch" / "build" / "runs"
    build.mkdir(parents=True)
    (build / "on.ini").write_text(json.dumps({"Rewind": {"Enabled": True}}))
    (build / "off.ini").write_text(json.dumps({"Rewind": {"Enabled": False}}))
    (build / "missing.ini").write_text(json.dumps({"Other": 1}))
    (build / "notjson.ini").write_text("[section]\n")
    got = sorted(os.path.basename(p) for p in fc.rewind_violations(str(tmp_path), 0))
    assert got == ["missing.ini", "on.ini"]


# ---------------------------------------------------------------------------
# the SKIP policy
# ---------------------------------------------------------------------------

def test_a_duo_skip_is_a_failure():
    out = "[duo] faint_cmd_gen3: SKIP (gen3_frlg) — x\n  faint_cmd_gen3: SKIP — x"
    verdict, ok = fc.judge("faint_cmd_gen3_fr_as_a", 3, out)
    assert not ok and "ALLOWED_SKIPS" in verdict


def test_a_pytest_skip_is_a_failure_even_on_exit_0():
    assert fc.judge("probe_gates", 0, "3 passed, 1 skipped in 9s")[1] is False


def test_a_no_save_witness_line_is_not_a_skip():
    # reconnect_gen3 at 157e1ef7: A is no_save by design and PASSed, but "saves=0 skipped" read as a skip
    out = "[duo] SAVE_WITNESS_SHA256 inst=a site=- file=- match=- saves=0 skipped (no_save)\n"
    assert fc.judge("reconnect_gen3_fr_as_a", 0, out) == ("PASS", True)
    assert fc.detect_skip(0, "==== 3 passed, 1 skipped in 9s ====")


def test_the_r5_mega_skip_is_allowed_by_ruling_20():
    out = ("  linked_faint_active_mega_gen3: SKIP — BLOCKED: R5 needs an RR trainer route and a "
           "mega-capable party")
    verdict, ok = fc.judge("linked_faint_active_mega_gen3_rr_as_a", 3, out)
    assert ok and verdict.startswith("SKIP-ALLOWED") and "ruling 20" in verdict
    # the same reason on another row is not excused
    assert fc.judge("faint_cmd_gen3_fr_as_a", 3, out)[1] is False


def test_a_gate_that_owns_its_skip_policy_is_judged_by_exit_code():
    rows = {r.id: r for r in fc.build_plan("c" * 40, LANE, MASTER)}
    assert rows["release_gate_quick"].own_verdict and rows["item6_route_diff"].own_verdict
    assert not rows["probe_gates"].own_verdict
    assert fc.judge("release_gate_quick", 0, "unit: 900 passed, 2 skipped", own_verdict=True)[1]


def test_pass_and_plain_failure():
    assert fc.judge("x", 0, "  x: PASS (attempt 1 of 1)") == ("PASS", True)
    assert fc.judge("x", 1, "boom") == ("FAIL exit=1", False)
    assert fc.judge("x", 0, "fine", budget_killed=True)[1] is False


# ---------------------------------------------------------------------------
# --resume receipt matching, and the never-re-run-a-failed-row rule across invocations
# ---------------------------------------------------------------------------

def _plan_row(row_id):
    return next((r for r in fc.build_plan("c" * 40, LANE, MASTER) if r.id == row_id), None)


def _receipt(tmp_path, row, cut, verdict, inputs=None):
    """A well-formed runner receipt: one attempt block that supports the verdict. Its `# inputs:` note is the
    lane's CURRENT non-git input hashes of the row (what a real run records), unless `inputs` overrides it."""
    plan_row = _plan_row(row)
    note = fc.inputs_note(inputs if inputs is not None else
                          fc.hash_inputs(fc.row_inputs(plan_row, LANE)) if plan_row else {})
    ok = verdict.startswith(("PASS", "SKIP-ALLOWED"))
    attempt = {"load": "cpu=1%", "start_utc": "2026-09-24T12:00:00Z",
               "end_utc": "2026-09-24T12:01:00Z", "rc": 0 if ok else 1, "tracked_before": True,
               "tracked_after": True, "classification": "pass" if ok else "real", "output": "out"}
    text = receipts.run_receipt_text(row=row, item="§x", cut=cut, lane=LANE, command="c",
                                     cwd=".", env={}, attempts=[attempt], verdict=verdict, note=note)
    (tmp_path / f"fc_{row}_{cut[:8]}.txt").write_text(text, encoding="utf-8")


@pytest.fixture
def pass_env(monkeypatch, tmp_path):
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    ran = []
    monkeypatch.setattr(fc, "PROBES", str(tmp_path))
    monkeypatch.setattr(fc, "provision", lambda tree, rev, root: rev)
    monkeypatch.setattr(fc, "copy_inputs", lambda tree, root, **kw: None)
    monkeypatch.setattr(fc, "run_row",
                        lambda row, cut, lane, deadline: (ran.append(row.id), ("PASS", 1, True))[1])
    return cut, ran, tmp_path


def _run(cut, *extra):
    return fc.main(["--cut", cut, "--lane", LANE, "--master", MASTER,
                    "--rows", "states_firered_town,states_firered_battle", *extra])


def test_resume_skips_a_pass_at_the_same_cut_only(pass_env):
    cut, ran, probes = pass_env
    _receipt(probes, "states_firered_town", cut, "PASS")
    # same sha8 in the name but a different full cut in the header: not a match
    _receipt(probes, "states_firered_battle", cut[:8] + "0" * 32, "PASS")
    assert _run(cut, "--resume") == 0
    assert ran == ["states_firered_battle"]
    summary = (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")
    assert "PASS (resumed)" in summary and "OVERALL: PASS (2/2 rows)" in summary


def test_without_resume_a_pass_receipt_is_re_taken(pass_env):
    cut, ran, probes = pass_env
    _receipt(probes, "states_firered_town", cut, "PASS")
    assert _run(cut) == 0
    assert ran == ["states_firered_town", "states_firered_battle"]


def test_a_fail_receipt_at_the_same_cut_blocks_the_row(pass_env):
    cut, ran, probes = pass_env
    _receipt(probes, "states_firered_town", cut, "FAIL exit=1")
    assert _run(cut) == 1
    assert _run(cut, "--resume") == 1
    assert ran == ["states_firered_battle", "states_firered_battle"]
    assert "not re-run" in (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")


def test_stop_at_in_the_past_runs_nothing(pass_env):
    cut, ran, _probes = pass_env
    assert _run(cut, "--stop-at", "2000-01-01T00:00Z") == 1
    assert ran == []


# ---------------------------------------------------------------------------
# lane provisioning: clean-check failure aborts
# ---------------------------------------------------------------------------

def test_provision_refuses_a_tracked_dirty_lane(tmp_path):
    sha = _repo(tmp_path / "lane")
    (tmp_path / "lane" / "a.txt").write_text("someone else's edit\n")
    with pytest.raises(fc.LaneError, match="tracked-dirty"):
        fc.provision(str(tmp_path / "lane"), sha, str(tmp_path / "lane"))
    assert (tmp_path / "lane" / "a.txt").read_text() == "someone else's edit\n"   # untouched


def test_provision_moves_a_clean_lane_to_the_cut_detached(tmp_path):
    lane = tmp_path / "lane"
    first = _repo(lane)
    (lane / "a.txt").write_text("two\n")
    _git(lane, "commit", "-qam", "two")
    assert fc.provision(str(lane), first, str(lane)) == first
    assert _git(lane, "rev-parse", "HEAD") == first
    assert (lane / "a.txt").read_text() == "one\n"
    assert _git(lane, "status", "--porcelain", "--untracked-files=no") == ""


def test_provision_repairs_a_lane_whose_checkout_died_after_updating(tmp_path):
    """W3's broken-ref case: the tree/index are at the cut but HEAD is not -> update-ref."""
    lane = tmp_path / "lane"
    first = _repo(lane)
    (lane / "a.txt").write_text("two\n")
    _git(lane, "commit", "-qam", "two")
    real_git = fc._git

    def dying_checkout(tree, *args, check=True):
        if args[:1] == ("checkout",):
            real_git(tree, "read-tree", "-u", "--reset", first)   # tree updated, HEAD not moved
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
        return real_git(tree, *args, check=check)
    fc_git = fc._git
    try:
        fc._git = dying_checkout
        assert fc.provision(str(lane), first, str(lane)) == first
    finally:
        fc._git = fc_git
    assert _git(lane, "rev-parse", "HEAD") == first


def test_a_dirty_lane_aborts_the_pass_before_any_row(monkeypatch, tmp_path):
    lane = tmp_path / "lane"
    sha = _repo(lane)
    (lane / "a.txt").write_text("dirty\n")
    monkeypatch.setattr(fc, "REPO", str(lane))
    monkeypatch.setattr(fc, "main_checkout", lambda: str(lane))
    monkeypatch.setattr(fc, "PROBES", str(tmp_path))

    def must_not_run(*a, **k):
        raise AssertionError("no row may run on a dirty lane")
    monkeypatch.setattr(fc, "run_row", must_not_run)
    assert fc.main(["--cut", sha, "--lane", str(lane), "--master", str(tmp_path / "m"),
                    "--rows", "states_*"]) == 2


def test_copy_inputs_fails_closed_on_a_missing_source(tmp_path):
    for d in ("root", "repo", "lane"):
        (tmp_path / d).mkdir()
    with pytest.raises(fc.LaneError, match="no source matches"):
        fc.copy_inputs(str(tmp_path / "lane"), str(tmp_path / "root"), str(tmp_path / "repo"),
                       pins=_pins())


# --- G4-FINALCUT-LIVEFIX: pinned inputs never lose to a stale root file ------------------------

GOOD = {"firered": b"FR dump", "leafgreen": b"LG dump", "radical_red_companion": b"RR companion new"}


def _pins():
    import hashlib
    pins = {k: hashlib.sha1(v).hexdigest() for k, v in GOOD.items()}
    pins["radical_red_companion:md5"] = hashlib.md5(GOOD["radical_red_companion"]).hexdigest()
    return pins


def _seed(base, companion=GOOD["radical_red_companion"], unpinned=b"u"):
    """A checkout holding every input copy_inputs looks for."""
    for t in ("firered", "leafgreen"):
        for rel in (fc.ROOT_DUMPS[t], fc.STAGED[t]):
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_bytes(GOOD[t])
    rr = base / "patch" / "build" / "slink_RR.gba"
    rr.parent.mkdir(parents=True, exist_ok=True)
    rr.write_bytes(companion)
    for rel in fc.UNPINNED_INPUTS:
        p = base / rel
        if rel.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
            (p / "cap.shadow.log").write_bytes(unpinned)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(unpinned)


def test_a_stale_root_companion_never_overwrites_the_lane(tmp_path):
    """The live rehearsal: root held the pre-rebuild companion (bf8e94a0), the lane the rebuilt
    one (6cf77ba4); the old size-compare copied root over the lane."""
    root, repo, lane = (tmp_path / d for d in ("root", "repo", "lane"))
    _seed(root, companion=b"RR companion OLD")          # same size, different content
    _seed(repo)
    _seed(lane)
    fc.copy_inputs(str(lane), str(root), str(repo), pins=_pins())
    assert (lane / "patch/build/slink_RR.gba").read_bytes() == GOOD["radical_red_companion"]


def test_a_wrong_lane_input_is_replaced_from_a_source_that_matches_its_pin(tmp_path):
    root, repo, lane = (tmp_path / d for d in ("root", "repo", "lane"))
    _seed(root, companion=b"RR companion OLD")
    _seed(repo)
    _seed(lane, companion=b"RR companion OLD")
    fc.copy_inputs(str(lane), str(root), str(repo), pins=_pins())
    assert (lane / "patch/build/slink_RR.gba").read_bytes() == GOOD["radical_red_companion"]


def test_no_source_matching_the_pin_aborts_without_touching_the_lane(tmp_path):
    root, repo, lane = (tmp_path / d for d in ("root", "repo", "lane"))
    for d in (root, repo, lane):
        _seed(d, companion=b"RR companion OLD")
    with pytest.raises(fc.LaneError, match="radical_red_companion"):
        fc.copy_inputs(str(lane), str(root), str(repo), pins=_pins())
    assert (lane / "patch/build/slink_RR.gba").read_bytes() == b"RR companion OLD"


def test_the_companion_md5_pin_is_checked_too(tmp_path):
    root, repo, lane = (tmp_path / d for d in ("root", "repo", "lane"))
    for d in (root, repo, lane):
        _seed(d)
    pins = _pins()
    pins["radical_red_companion:md5"] = "0" * 32         # server/patcher.py disagrees
    with pytest.raises(fc.LaneError, match="radical_red_companion"):
        fc.copy_inputs(str(lane), str(root), str(repo), pins=pins)


def test_an_unpinned_lane_input_is_kept_and_a_missing_one_copied(tmp_path):
    root, repo, lane = (tmp_path / d for d in ("root", "repo", "lane"))
    _seed(root, unpinned=b"root copy")
    _seed(repo, unpinned=b"repo copy")
    _seed(lane, unpinned=b"lane copy")
    kept = fc.UNPINNED_INPUTS[0]
    missing = next(r for r in fc.UNPINNED_INPUTS if r.endswith("/"))   # the shadow captures
    import shutil
    shutil.rmtree(lane / missing)
    fc.copy_inputs(str(lane), str(root), str(repo), pins=_pins())
    assert (lane / kept).read_bytes() == b"lane copy"
    assert (lane / missing / "cap.shadow.log").read_bytes() == b"repo copy"   # repo before root


def test_the_master_tree_gets_only_the_item6_inputs(tmp_path):
    root, repo, master = (tmp_path / d for d in ("root", "repo", "master"))
    _seed(root)
    master.mkdir()
    fc.copy_inputs(str(master), str(root), str(repo), only=fc.ITEM6_INPUTS)   # no pin tables
    assert (master / "patch/build/gen1_red.gb").exists()
    assert not (master / "patch/build/slink_RR.gba").exists()
    assert not (master / "patch/build/shadow_wire").exists()


def test_rom_pins_come_from_the_trees_own_pin_tables():
    pins = fc.rom_pins(fc.REPO)
    assert pins["firered"] == "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc"
    assert pins["leafgreen"] == "574fa542ffebb14be69902d1d36f1ec0a4afd71e"
    assert len(pins["radical_red_companion"]) == 40
    assert len(pins["radical_red_companion:md5"]) == 32     # server/patcher.py TARGETS["rr"]


def test_bootcheck_passes_the_root_dump_and_the_gamedb_battery_name():
    """The rehearsal: --rom <already staged name> was staged AGAIN (gen3_gen3_...) and seeded
    as 'gen3 gen3 ....SaveRAM' while BizHawk filed the battery under the gamedb title."""
    rows = {r.id: r for r in fc.build_plan("c" * 40, LANE, MASTER)}
    assert rows["bootcheck_leafgreen_party_battle_b"].command() == (
        "python tools/gen3_fixtures.py boot-check --rom 'Pokemon - LeafGreen Version (USA).gba' "
        "--fixture tests/fixtures/gen3/leafgreen_party_battle_b.sav --title leafgreen "
        "--saveram-name 'Pokemon - LeafGreen Version (USA).SaveRAM'")
    assert rows["bootcheck_firered_party_town"].command() == (
        "python tools/gen3_fixtures.py boot-check --rom 'Pokemon - FireRed Version (USA).gba' "
        "--fixture tests/fixtures/gen3/firered_party_town.sav --title firered "
        "--saveram-name 'Pokemon - FireRed Version (USA).SaveRAM'")


def test_the_unit_gate_row_gets_the_vendored_toolchain(tmp_path):
    for ver in ("xpack-arm-none-eabi-gcc-14.0.0-1", "xpack-arm-none-eabi-gcc-15.2.1-1.1"):
        b = tmp_path / "patch" / "vendor" / "armgcc" / ver / "bin"
        b.mkdir(parents=True)
        (b / ("arm-none-eabi-gcc" + (".exe" if os.name == "nt" else ""))).write_bytes(b"")
    got = fc.armgcc_bin(str(tmp_path))
    assert got.replace("\\", "/").endswith("xpack-arm-none-eabi-gcc-15.2.1-1.1/bin")
    assert fc.armgcc_bin(str(tmp_path / "nowhere")) is None
    row = {r.id: r for r in fc.build_plan("c" * 40, LANE, MASTER)}["release_gate_quick"]
    if fc.armgcc_bin(fc.main_checkout()):
        assert row.env["SLINK_ARMGCC"] == fc.armgcc_bin(fc.main_checkout())


# ---------------------------------------------------------------------------
# item 6
# ---------------------------------------------------------------------------

def test_item6_flags_only_a_regression_vs_master():
    table = {"gen1_ordering": {"master": False, "branch": False},     # identical FAIL: no delta
             "gen1_sfx_town": {"master": False, "branch": True},      # branch better
             "gen2_legacy_faint": {"master": True, "branch": False}}  # regression
    assert fc.item6_verdict(table) == ["gen2_legacy_faint"]


def test_item6_runs_the_three_cases_of_the_2026_09_24_receipts():
    names = [c[0] for c in fc.item6_cases()]
    assert names == ["gen1_ordering", "gen1_sfx_town", "gen2_legacy_faint",
                     "gen2_legacy_boxsync", "gen2_legacy_memorialize"]


# ---------------------------------------------------------------------------
# --carry (G4-FINALCUT-FAST, hardened by G4-FINALCUT-HARDEN)
# ---------------------------------------------------------------------------

X, CUT = "a" * 40, "b" * 40
ROMS = {"rom:firered": "1" * 64, "rom:leafgreen": "3" * 64}
DUO_ROWS = [r for r in EXPECTED_ROWS if r.endswith(("_fr_as_a", "_lg_as_a"))]


def _row(row_id):
    return {r.id: r for r in fc.build_plan(CUT, LANE, MASTER)}[row_id]


def _ev(row_id, cut=X, passed=True, inputs=None, **kw):
    return fc.Evidence(row_id, f"ph_{row_id}.txt", cut, passed,
                       inputs=dict(ROMS) if inputs is None else inputs, **kw)


def _decide(row, ev, changed=(), ancestor=True, inputs=None, master=None):
    return fc.carry_decision(row, CUT, ev, lambda x, c: list(changed), master,
                             lambda x, c: ancestor, dict(ROMS) if inputs is None else inputs)


def test_carry_when_nothing_blocks_it():
    row = _row("active_end_gen3_fr_as_a")
    d = _decide(row, [_ev(row.id)], ["docs/gen3/PLAN.md", "lua/gen1/client.lua"])
    assert d.kind == "CARRY" and d.reason == f"CARRIED from ph_{row.id}.txt @{X}"


# the coordinator's list (OMP review of d9a08f5b) plus the obvious client/carrier/server paths
FORCING = ["data/games/gen3_frlge/area_map.json", "data/games/gen3_frlge/gen3_frlge_locations.lua",
           "data/gen3/pret/pokefirered.sym", "lua/tests/playlib.lua",
           "lua/tests/mkstates_gen3_tutorials.lua", "lua/tests/duo/scenario_gen3_whiteout.lua",
           "lua/tests/duo/duo_gen3_main.lua", "tools/e2e_duo.py", "lua/gen3/client.lua",
           "lua/core/deferred.lua", "lua/slink.lua", "data/games/gen3_frlg/write_checkpoint.json",
           "server/state.py", "tests/fixtures/gen3/leafgreen_party_battle_b.sav",
           "lua/tests/gen3_gatelib.lua"]


@pytest.mark.parametrize("row_id", DUO_ROWS)
def test_every_listed_dependency_forces_every_duo_row_to_run(row_id):
    row = _row(row_id)
    for path in FORCING:
        d = _decide(row, [_ev(row_id)], ["docs/a.md", path])
        assert d.kind == "RUN" and path in d.reason, (row_id, path)


def test_probe_gates_depends_on_the_games_table():
    d = _decide(_row("probe_gates"), [_ev("probe_gates", inputs={})],
                ["lua/games/gen3_frlge.lua"], inputs={})
    assert d.kind == "RUN" and "lua/games/gen3_frlge.lua" in d.reason


def test_no_receipt_or_no_pass_or_same_cut_runs():
    row = _row("active_end_gen3_fr_as_a")
    assert _decide(row, []).kind == "RUN"
    assert _decide(row, [_ev(row.id, passed=False)]).kind == "RUN"
    assert _decide(row, [_ev(row.id, cut=CUT)]).kind == "RUN"
    assert _decide(row, [_ev(row.id, cut=None)]).kind == "RUN"
    assert fc.carry_decision(row, CUT, [_ev(row.id)], lambda x, c: None, None,
                             lambda x, c: True, dict(ROMS)).kind == "RUN"


def test_a_receipt_from_a_cut_that_is_not_an_ancestor_runs():
    row = _row("active_end_gen3_fr_as_a")
    d = _decide(row, [_ev(row.id)], ancestor=False)
    assert d.kind == "RUN" and "not an ancestor" in d.reason


def test_the_default_ancestor_check_is_git(tmp_path):
    row = _row("active_end_gen3_fr_as_a")    # "a"*40 is no commit: the real git check refuses it
    d = fc.carry_decision(row, CUT, [_ev(row.id)], lambda x, c: [], None, None, dict(ROMS))
    assert d.kind == "RUN" and "not an ancestor" in d.reason


@pytest.mark.parametrize("recorded,current", [
    ({"rom:firered": "9" * 64, "rom:leafgreen": "3" * 64}, ROMS),     # a different ROM
    ({}, ROMS),                                                       # nothing recorded
    (ROMS, {"rom:firered": "MISSING", "rom:leafgreen": "3" * 64}),   # missing in this lane
])
def test_non_git_inputs_must_match_the_lane(recorded, current):
    row = _row("active_end_gen3_fr_as_a")
    assert _decide(row, [_ev(row.id, inputs=recorded)], inputs=current).kind == "RUN"


def test_builds_zip_and_the_source_gate_are_never_carried():
    for rid in ("states_firered_town", "tutorials_leafgreen", "zip_boot_firered_refused",
                "release_gate_quick"):
        assert _decide(_row(rid), [_ev(rid)]).kind == "RUN"


def test_the_checkpoint_carries_under_the_input_hash_rule():
    row = _row("checkpoint_firered")               # G4-FINALCUT-CACHE: its states are known
    assert _decide(row, [_ev(row.id)]).kind == "CARRY"
    assert _decide(row, [_ev(row.id)], ["lua/tests/probe_gen3_checkpoint.lua"]).kind == "RUN"
    assert _decide(row, [_ev(row.id, inputs={"rom:firered": "9" * 64})]).kind == "RUN"


def test_item6_carries_only_while_master_has_not_moved():
    row = _row("item6_route_diff")
    ev = [_ev(row.id, master="c" * 8, inputs={})]
    assert _decide(row, ev, inputs={}, master="c" * 40).kind == "CARRY"
    assert _decide(row, ev, inputs={}, master="d" * 40).kind == "RUN"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_item6_carry_uses_resolved_primary_branch(tmp_path, monkeypatch, branch):
    repo = tmp_path / "repo"
    base = _repo(repo)
    if _git(repo, "branch", "--show-current") != branch:
        _git(repo, "branch", "-m", branch)
    _git(repo, "checkout", "-qb", "feature")
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-qam", "feature")
    cut = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(fc, "REPO", str(repo))
    row = _row("item6_route_diff")
    monkeypatch.setattr(fc, "collect_evidence", lambda: {row.id: [_ev(row.id, master=base, inputs={})]})
    monkeypatch.setattr(fc, "hash_inputs", lambda *_: {})
    monkeypatch.setattr(fc, "git_diff_names", lambda *_: [])
    monkeypatch.setattr(fc, "git_is_ancestor", lambda *_: True)
    assert fc.primary_revision() == base
    decisions, _ = fc.plan_decisions([row], cut, True, str(repo))
    assert decisions[row.id].kind == "CARRY"
    _git(repo, "branch", "-f", branch, cut)
    decisions, _ = fc.plan_decisions([row], cut, True, str(repo))
    assert decisions[row.id].kind == "RUN"


def test_item6_provisions_the_resolved_primary_commit(monkeypatch, tmp_path):
    baseline = "d" * 40
    monkeypatch.setattr(fc, "primary_revision", lambda: baseline)
    monkeypatch.setattr(fc, "collect_evidence", lambda: {})
    calls = []

    def provision(tree, rev, root):
        calls.append((tree, rev))
        if len(calls) == 2:
            raise fc.LaneError("stopped before emulator work")

    monkeypatch.setattr(fc, "provision", provision)
    monkeypatch.setattr(fc, "copy_inputs", lambda *args, **kwargs: None)
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    lane, master = str(tmp_path / "lane"), str(tmp_path / "baseline")
    assert fc.main(["--cut", cut, "--rows", "item6_route_diff", "--lane", lane, "--master", master]) == 2
    assert calls == [(lane, cut), (master, baseline)]


def test_item6_refuses_an_unknown_primary_branch_before_running(monkeypatch, capsys):
    def unknown():
        raise fc.LaneError("cannot resolve primary branch")

    monkeypatch.setattr(fc, "primary_revision", unknown)
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    assert fc.main(["--cut", cut, "--rows", "item6_route_diff", "--dry-run"]) == 2
    assert "ABORT: cannot resolve primary branch" in capsys.readouterr().err


def test_glob_star_stays_in_its_directory():
    assert fc.touched(["lua/slink.lua", "lua/gen1/client.lua", "lua/gen3/a/b.lua"],
                      ["lua/*.lua", "lua/gen3/**"]) == ["lua/slink.lua", "lua/gen3/a/b.lua"]


def test_row_inputs_name_the_staged_roms_and_the_rebuilt_states(tmp_path):
    sd = tmp_path / "patch" / "build" / "gen3_probe_states" / "firered"
    sd.mkdir(parents=True)
    (sd / "slink_oldman.State").write_bytes(b"s")
    got = fc.row_inputs(_row("checkpoint_firered"), str(tmp_path), root=str(tmp_path))
    assert "state:gen3_probe_states/slink_oldman.State" in got and "rom:firered" in got
    assert set(fc.row_inputs(_row("whiteout_gen3_lg_as_a"), str(tmp_path))) == set(ROMS)
    h = fc.hash_inputs(got)
    assert h["rom:firered"] == "MISSING" and len(h["state:gen3_probe_states/slink_oldman.State"]) == 64
    assert h["state:gen3_probe_states/slink_pokedude.State"] == "MISSING"   # declared, not listed
    assert fc.parse_inputs("# " + fc.inputs_note(h)) == h


# --- receipt -> row mapping ------------------------------------------------------------------

def _ph(scen, o, x=X, tail=""):
    title, other = ("firered", "leafgreen") if o == "fr" else ("leafgreen", "firered")
    game = "gen3_frlg" if o == "fr" else "gen3_lgfr"
    return (f"G4-LANE-2 P+H live row: {scen}\nnote: PASS, attempt 1 of 1\n"
            f"=== {scen} --game {game}  lane=L sha={x} tracked_dirty_before=0\n"
            f"start_utc=2026-09-24T12:00:00Z\n"
            f"[duo] IDENTITY a={title}:rom={ROMS['rom:' + title]}:fixture={'2' * 64} "
            f"b={other}:rom={ROMS['rom:' + other]}:fixture={'4' * 64} source={x}\n"
            f"  {scen}: PASS (attempt 1 of 1)\nexit=0\nend_utc=2026-09-24T12:02:00Z\n{tail}")


@pytest.mark.parametrize("name,row,cut8", [
    ("ph_active_end_gen3_fr_as_a_b0483efe.txt", "active_end_gen3_fr_as_a", "b0483efe"),
    ("ph_linked_faint_active_whiteout_gen3_lg_as_a_28e48c9c.txt",
     "linked_faint_active_whiteout_gen3_lg_as_a", "28e48c9c"),
])
def test_ph_receipts_map_to_their_rows(name, row, cut8):
    with open(os.path.join(fc.PROBES, name), encoding="utf-8") as f:
        ev = fc.receipt_evidence(name, f.read())
    assert ev.row == row and ev.passed and ev.cut.startswith(cut8) and len(ev.cut) == 40
    assert set(ev.inputs) == set(ROMS)          # the ROM sha256s from e2e_duo's IDENTITY line


def test_the_mixed_trainer_receipt_is_not_citable():
    """It PASSed, then an aborted overlapping launch appended a FAIL/exit=1: no single final
    verdict, so it cannot be carried (its duration still counts for the estimate)."""
    name = "ph_linked_faint_active_trainer_gen3_fr_as_a_b0483efe.txt"
    with open(os.path.join(fc.PROBES, name), encoding="utf-8") as f:
        ev = fc.receipt_evidence(name, f.read())
    assert ev.row == "linked_faint_active_trainer_gen3_fr_as_a" and not ev.passed
    assert ev.seconds == 42 * 60


@pytest.mark.parametrize("text,ok", [
    (_ph("active_end_gen3", "fr"), True),
    (_ph("active_end_gen3", "fr", tail="  active_end_gen3: FAIL (attempt 1 of 1)\nexit=1\n"), False),
    (_ph("active_end_gen3", "fr", tail="exit=1\n"), False),
    (_ph("active_end_gen3", "fr", tail="note: PASS again\n"), False),
    (_ph("active_end_gen3", "lg"), False),       # an LG-as-A run in an fr_as_a file
])
def test_ph_needs_one_unambiguous_final_verdict_for_its_orientation(text, ok):
    ev = fc.receipt_evidence("ph_active_end_gen3_fr_as_a_aaaaaaaa.txt", text)
    assert ev.passed is ok


def test_every_ph_receipt_maps_to_a_plan_row():
    ids = {r.id for r in fc.build_plan(CUT, LANE, MASTER)}
    names = [n for n in os.listdir(fc.PROBES)
             if re.fullmatch(r"ph_.+_(fr|lg)_as_a_[0-9a-f]{8}\.txt", n)]   # RR rows are G5
    assert names
    for name in names:
        with open(os.path.join(fc.PROBES, name), encoding="utf-8") as f:
            ev = fc.receipt_evidence(name, f.read())
        assert ev and ev.row in ids and ev.cut, name


def test_a_legacy_receipt_without_a_cut_sha_is_not_citable():
    text = "========== summary ==========\n  link_gen3: PASS (attempt 2 of 3)\n"
    ev = fc.receipt_evidence("duo_frlg_link_gen3_clean_2026-09-23b.txt", text)
    assert ev.row == "link_gen3_fr_as_a" and ev.cut is None


# --- CARRIED fc receipts must authenticate through their origin --------------------------------

def _carried(probes, row_id, origin, x=X, cut=CUT):
    d = fc.Decision("CARRY", f"CARRIED from {origin} @{x}", fc.Evidence(row_id, origin, x, True),
                    ["docs/a.md"], dict(ROMS))
    name = f"fc_{row_id}_{cut[:8]}.txt"
    (probes / name).write_text(fc.carried_receipt(_row(row_id), cut, LANE, d), encoding="utf-8")
    return name


def _ev_of(probes, name):
    return fc.receipt_evidence(name, (probes / name).read_text(encoding="utf-8"), str(probes))


def test_a_carried_receipt_cites_its_validated_origin(tmp_path):
    rid = "active_end_gen3_fr_as_a"
    (tmp_path / "ph_active_end_gen3_fr_as_a_aaaaaaaa.txt").write_text(_ph("active_end_gen3", "fr"))
    name = _carried(tmp_path, rid, "ph_active_end_gen3_fr_as_a_aaaaaaaa.txt")
    text = (tmp_path / name).read_text(encoding="utf-8")
    assert "none a dependency): docs/a.md" in text and "# inputs: rom:firered=" in text
    ev = _ev_of(tmp_path, name)
    assert (ev.receipt, ev.cut, ev.passed, ev.inputs) == (
        "ph_active_end_gen3_fr_as_a_aaaaaaaa.txt", X, True, ROMS)


def test_a_carried_receipt_with_a_bad_origin_is_not_citable(tmp_path):
    rid = "active_end_gen3_fr_as_a"
    # missing origin
    assert not _ev_of(tmp_path, _carried(tmp_path, rid, "ph_nope_fr_as_a_aaaaaaaa.txt")).passed
    # origin for another row
    (tmp_path / "ph_whiteout_gen3_fr_as_a_aaaaaaaa.txt").write_text(_ph("whiteout_gen3", "fr"))
    assert not _ev_of(tmp_path, _carried(tmp_path, rid, "ph_whiteout_gen3_fr_as_a_aaaaaaaa.txt")).passed
    # origin at a different cut than the one cited
    (tmp_path / "ph_active_end_gen3_fr_as_a_aaaaaaaa.txt").write_text(
        _ph("active_end_gen3", "fr", x="c" * 40))
    assert not _ev_of(tmp_path, _carried(tmp_path, rid, "ph_active_end_gen3_fr_as_a_aaaaaaaa.txt")).passed
    # origin that is a SKIP-ALLOWED runner receipt: never citable for carry
    _receipt(tmp_path, rid, X, "SKIP-ALLOWED owner ruling 20")
    assert not _ev_of(tmp_path, _carried(tmp_path, rid, f"fc_{rid}_{X[:8]}.txt")).passed


def test_a_header_only_or_wrong_row_fc_receipt_is_not_citable(tmp_path):
    rid = "active_end_gen3_fr_as_a"
    text = receipts.run_receipt_text(row=rid, item="x", cut=X, lane=LANE, command="c", cwd=".",
                                     env={}, attempts=[], verdict="PASS")
    (tmp_path / f"fc_{rid}_{X[:8]}.txt").write_text(text, encoding="utf-8")
    assert not _ev_of(tmp_path, f"fc_{rid}_{X[:8]}.txt").passed
    _receipt(tmp_path, "whiteout_gen3_fr_as_a", X, "PASS")
    os.replace(tmp_path / f"fc_whiteout_gen3_fr_as_a_{X[:8]}.txt", tmp_path / f"fc_{rid}_{X[:8]}.txt")
    assert not _ev_of(tmp_path, f"fc_{rid}_{X[:8]}.txt").passed
    _receipt(tmp_path, rid, X, "PASS")
    assert _ev_of(tmp_path, f"fc_{rid}_{X[:8]}.txt").passed


def test_a_carried_row_writes_its_receipt_and_is_counted_as_carried(pass_env, monkeypatch):
    cut, ran, probes = pass_env
    rid = "faint_cmd_gen3_fr_as_a"
    (probes / f"ph_{rid}_{X[:8]}.txt").write_text(_ph("faint_cmd_gen3", "fr"))
    monkeypatch.setattr(fc, "git_diff_names", lambda x, c: ["docs/gen3/PLAN.md"])
    monkeypatch.setattr(fc, "git_is_ancestor", lambda x, c: True)
    monkeypatch.setattr(fc, "hash_inputs", lambda paths: {k: ROMS.get(k, "0" * 64) for k in paths})
    assert fc.main(["--cut", cut, "--lane", LANE, "--master", MASTER, "--carry",
                    "--rows", "faint_cmd_*,states_firered_town"]) == 0
    assert ran == ["states_firered_town"]
    got = receipts.read_run_receipt(str(probes / f"fc_{rid}_{cut[:8]}.txt"))
    assert got["verdict"].startswith(f"CARRIED from ph_{rid}_{X[:8]}.txt @{X}")
    summary = (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")
    assert "# RUN 1 / CARRIED 1 / CACHED 0 / FAIL 0" in summary


# ---------------------------------------------------------------------------
# --shard i/n and --merge-summary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_sharding_covers_each_row_exactly_once(n):
    rows = fc.build_plan(CUT, LANE, MASTER)
    est = {r.id: r.budget for r in rows}
    shards = fc.shard_rows(rows, n, est)
    ids = [r.id for s in shards for r in s]
    assert sorted(ids) == sorted(r.id for r in rows) and len(ids) == len(set(ids))
    assert [[r.id for r in s] for s in fc.shard_rows(rows, n, est)] == \
        [[r.id for r in s] for s in shards]                     # deterministic
    order = [r.id for r in rows]
    for s in shards:                                            # plan order inside a shard
        assert [r.id for r in s] == sorted((r.id for r in s), key=order.index)


EDGES = [("states_firered_town", "checkpoint_firered"), ("states_firered_battle", "checkpoint_firered"),
         ("tutorials_firered", "checkpoint_firered"), ("states_leafgreen_town", "checkpoint_leafgreen"),
         ("states_leafgreen_battle", "checkpoint_leafgreen"),
         ("tutorials_leafgreen", "checkpoint_leafgreen"),
         ("zip_build", "zip_check"), ("zip_build", "zip_boot_firered_refused")]


@pytest.mark.parametrize("n", [2, 3, 4, 8])
def test_a_prerequisite_and_its_dependent_share_a_shard_in_order(n):
    rows = fc.build_plan(CUT, LANE, MASTER)
    # adversarial estimates: make the builds and the probes look like the biggest units
    est = {r.id: (10**6 if r.id.startswith(("states_", "tutorials_", "zip_")) else r.budget)
           for r in rows}
    for shard in fc.shard_rows(rows, n, est):
        ids = [r.id for r in shard]
        for pre, dep in EDGES:
            if dep in ids or pre in ids:
                assert pre in ids and dep in ids and ids.index(pre) < ids.index(dep), (pre, dep)


def test_two_shards_run_disjoint_rows_and_their_union_is_the_plan(pass_env):
    cut, ran, probes = pass_env
    base = ["--cut", cut, "--lane", LANE, "--master", MASTER,
            "--rows", "states_*,tutorials_*,faint_cmd_*,link_gen3_*"]
    assert fc.main(base + ["--shard", "1/2"]) == 0
    first = list(ran)
    assert fc.main(base + ["--shard", "2/2"]) == 0
    second = ran[len(first):]
    assert first and second and not set(first) & set(second)
    assert sorted(first + second) == sorted(EXPECTED_ROWS[:10])
    for i in (1, 2):
        assert (probes / f"fc_SUMMARY_{cut[:8]}_shard{i}of2.txt").exists()


def test_merge_summary_validates_every_row_receipt(pass_env):
    cut, _ran, probes = pass_env
    rows = "--rows", "faint_cmd_gen3_fr_as_a,link_gen3_fr_as_a"
    base = ["--cut", cut, "--lane", LANE, "--master", MASTER, *rows, "--merge-summary"]
    _receipt(probes, "faint_cmd_gen3_fr_as_a", cut, "PASS")
    (probes / f"ph_link_gen3_fr_as_a_{X[:8]}.txt").write_text(_ph("link_gen3", "fr"))
    _carried(probes, "link_gen3_fr_as_a", f"ph_link_gen3_fr_as_a_{X[:8]}.txt", cut=cut)
    assert fc.main(base) == 0
    s = (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")
    assert "# RUN 1 / CARRIED 1 / CACHED 0 / FAIL 0" in s
    # the origin disappears: the carried receipt no longer authenticates
    (probes / f"ph_link_gen3_fr_as_a_{X[:8]}.txt").unlink()
    assert fc.main(base) == 1
    # a header-only PASS
    text = receipts.run_receipt_text(row="link_gen3_fr_as_a", item="x", cut=cut, lane=LANE,
                                     command="c", cwd=".", env={}, attempts=[], verdict="PASS")
    (probes / f"fc_link_gen3_fr_as_a_{cut[:8]}.txt").write_text(text, encoding="utf-8")
    assert fc.main(base) == 1
    # a well-formed PASS for ANOTHER row under this row's name
    _receipt(probes, "whiteout_gen3_fr_as_a", cut, "PASS")
    os.replace(probes / f"fc_whiteout_gen3_fr_as_a_{cut[:8]}.txt",
               probes / f"fc_link_gen3_fr_as_a_{cut[:8]}.txt")
    assert fc.main(base) == 1
    assert "FAIL invalid receipt" in (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")
    # and a missing receipt is NOT RUN
    assert fc.main(["--cut", cut, "--lane", LANE, "--master", MASTER,
                    "--rows", "states_*", "--merge-summary"]) == 1


# --- G4-FINALCUT-LIVEFIX-2 ------------------------------------------------------------------

def test_the_pret_clones_the_unit_suite_reads_are_unpinned_inputs(tmp_path):
    """tests/unit/test_gen1_trade_patch.py (and the Gen 3 profile/route tests) read
    .cache/pret/{pokered,pokefirered,pokecrystal,pokeemerald} (pokeemerald since EXPLODE-BIND's
    Emerald checkpoint generator); a lane worktree has no .cache."""
    clones = [r for r in fc.UNPINNED_INPUTS if r.startswith(".cache/pret/")]
    assert sorted(clones) == [".cache/pret/pokecrystal/", ".cache/pret/pokeemerald/",
                              ".cache/pret/pokefirered/", ".cache/pret/pokered/"]
    root, repo, lane = (tmp_path / d for d in ("root", "repo", "lane"))
    _seed(root, unpinned=b"root copy")
    _seed(lane)
    import shutil
    shutil.rmtree(lane / ".cache")
    repo.mkdir()
    fc.copy_inputs(str(lane), str(root), str(repo), pins=_pins())
    assert (lane / ".cache/pret/pokered/cap.shadow.log").read_bytes() == b"root copy"
    assert not any(r.startswith(".cache/") for r in fc.ITEM6_INPUTS)


def test_provisioning_really_refreshes_the_index_before_the_clean_check(tmp_path, monkeypatch):
    """After a .gitattributes change a lane's index can be stat-only dirty; the pass must
    re-hash (git update-index --really-refresh) before judging it."""
    lane = tmp_path / "lane"
    sha = _repo(lane)
    calls = []
    real_git = fc._git

    def spy(tree, *args, check=True):
        calls.append(args[0])
        return real_git(tree, *args, check=check)
    monkeypatch.setattr(fc, "_git", spy)
    assert fc.provision(str(lane), sha, str(lane)) == sha
    assert "update-index" in calls and calls.index("update-index") < calls.index("status")
    assert "--really-refresh" in fc.provision_plan(str(lane), sha)[1]


# --- G4-FINALCUT-CACHE: content-addressed §1 builds ----------------------------------------

BLOBS = {"tools/mkstates_gen3.py": "b1", "tools/mkstates_gen3_tutorials.py": "b2",
         "tools/gen3_fixtures.py": "b3", "lua/tests/mkstates_gen3.lua": "b4",
         "lua/tests/gen3_boot_check.lua": "b5", "lua/gen3/reads.lua": "b6",
         "data/games/gen3_frlg/profile.json": "b7", "docs/gen3/PLAN.md": "b8",
         "tests/fixtures/gen3/firered_party_town.sav": "f1",
         "tests/fixtures/gen3/firered_party_battle.sav": "f2",
         "lua/gen1/client.lua": "g1"}
BIZ = {"EmuHawk.exe": "e" * 64, "config": "c" * 64}


def _key(row_id, blobs=None, rom=b"FR dump", biz=None, tmp=None):
    lane = tmp
    (lane / fc.STAGED["firered"]).parent.mkdir(parents=True, exist_ok=True)
    (lane / fc.STAGED["firered"]).write_bytes(rom)
    return fc.build_key(_row(row_id), CUT, str(lane), blobs=dict(BLOBS, **(blobs or {})),
                        bizhawk=dict(BIZ, **(biz or {})))[0]


def test_the_build_key_covers_every_input_and_nothing_else(tmp_path):
    base = _key("states_firered_town", tmp=tmp_path)
    assert base and base == _key("states_firered_town", tmp=tmp_path)            # deterministic
    assert _key("states_firered_town", {"docs/gen3/PLAN.md": "zz"}, tmp=tmp_path) == base
    assert _key("states_firered_town", {"lua/gen1/client.lua": "zz"}, tmp=tmp_path) == base
    assert _key("states_firered_town", {"tests/fixtures/gen3/firered_party_battle.sav": "zz"},
                tmp=tmp_path) == base                       # another kind's fixture
    for changed in ({"tests/fixtures/gen3/firered_party_town.sav": "zz"},   # the fixture
                    {"tools/mkstates_gen3.py": "zz"}, {"lua/tests/mkstates_gen3.lua": "zz"},
                    {"lua/tests/gen3_boot_check.lua": "zz"}, {"lua/gen3/reads.lua": "zz"},
                    {"data/games/gen3_frlg/profile.json": "zz"}):
        assert _key("states_firered_town", changed, tmp=tmp_path) != base, changed
    assert _key("states_firered_town", rom=b"another dump", tmp=tmp_path) != base
    assert _key("states_firered_town", biz={"EmuHawk.exe": "f" * 64}, tmp=tmp_path) != base
    assert _key("states_firered_town", biz={"config": "d" * 64}, tmp=tmp_path) != base
    assert _key("states_firered_battle", tmp=tmp_path) != base                  # per row
    assert _key("tutorials_firered", tmp=tmp_path) != base


def test_no_key_without_the_staged_rom(tmp_path):
    key, why = fc.build_key(_row("states_firered_town"), CUT, str(tmp_path), blobs=BLOBS,
                            bizhawk=BIZ)
    assert key is None and "ROM" in why


def test_bizhawk_fingerprint_hashes_the_exe_core_and_the_gba_config(tmp_path):
    exe = tmp_path / "EmuHawk.exe"
    exe.write_bytes(b"exe")
    (tmp_path / "dll").mkdir()
    (tmp_path / "dll" / "mgba.dll").write_bytes(b"core")
    cfg = tmp_path / "config.ini"
    mg = "BizHawk.Emulation.Cores.Nintendo.GBA.MGBAHawk"
    cfg.write_text(json.dumps({"PreferredCores": {"GBA": "mGBA"}, "WindowX": 1,
                               "CoreSyncSettings": {mg: {"SkipBios": True}},
                               "CoreSettings": {mg: {"x": 1}}}))
    a = fc.bizhawk_fingerprint(str(exe), str(cfg))
    cfg.write_text(json.dumps({"PreferredCores": {"GBA": "mGBA"}, "WindowX": 999,
                               "CoreSyncSettings": {mg: {"SkipBios": True}},
                               "CoreSettings": {mg: {"x": 1}}}))
    assert fc.bizhawk_fingerprint(str(exe), str(cfg)) == a      # window position: irrelevant
    cfg.write_text(json.dumps({"PreferredCores": {"GBA": "mGBA"}, "WindowX": 999,
                               "CoreSyncSettings": {mg: {"SkipBios": False}},
                               "CoreSettings": {mg: {"x": 1}}}))
    assert fc.bizhawk_fingerprint(str(exe), str(cfg)) != a      # a sync setting: relevant
    assert set(a) >= {"EmuHawk.exe", "dll/mgba.dll", "config"}


def _outputs(out_dir, kind, body=b"state"):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in fc.BUILD_OUTPUTS[kind]:
        (out_dir / name).write_bytes(body + name.encode())


def test_cache_store_then_lookup_round_trips_and_detects_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "state_cache_root", lambda: str(tmp_path / "cache"))
    row = _row("states_firered_town")
    out = tmp_path / "out"
    _outputs(out, "town")
    assert fc.cache_lookup("k" * 64) is None
    assert fc.cache_store("k" * 64, {"m": 1}, row, CUT, str(out), since=0)
    meta = fc.cache_lookup("k" * 64)
    assert meta["receipt"] == f"fc_states_firered_town_{CUT[:8]}.txt" and meta["cut"] == CUT
    assert set(meta["files"]) == set(fc.BUILD_OUTPUTS["town"])
    (tmp_path / "cache" / ("k" * 64) / "slink_door.State").write_bytes(b"tampered")
    assert fc.cache_lookup("k" * 64) is None


def test_cache_store_refuses_when_an_expected_output_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "state_cache_root", lambda: str(tmp_path / "cache"))
    out = tmp_path / "out"
    _outputs(out, "town")
    (out / "slink_script.State").unlink()
    assert not fc.cache_store("k" * 64, {}, _row("states_firered_town"), CUT, str(out), since=0)
    assert fc.cache_lookup("k" * 64) is None


@pytest.fixture
def cache_env(pass_env, monkeypatch, tmp_path):
    cut, ran, probes = pass_env
    lane = tmp_path / "lane"
    (lane / fc.STAGED["firered"]).parent.mkdir(parents=True, exist_ok=True)
    (lane / fc.STAGED["firered"]).write_bytes(b"FR dump")
    monkeypatch.setattr(fc, "state_cache_root", lambda: str(tmp_path / "cache"))
    monkeypatch.setattr(fc, "bizhawk_fingerprint", lambda *a: dict(BIZ))
    return cut, ran, probes, lane


def test_a_cache_miss_builds_live_and_populates_the_cache(cache_env, monkeypatch):
    cut, ran, probes, lane = cache_env

    def build(row, cut_, lane_, deadline):
        ran.append(row.id)
        _outputs(lane / "patch/build/gen3_probe_states_c4p2/firered", "town")
        _receipt(probes, row.id, cut_, "PASS")
        return "PASS", 1, True
    monkeypatch.setattr(fc, "run_row", build)
    assert fc.main(["--cut", cut, "--lane", str(lane), "--master", MASTER,
                    "--rows", "states_firered_town"]) == 0
    assert ran == ["states_firered_town"]
    key = fc.build_key(_row("states_firered_town"), cut, str(lane))[0]
    assert fc.cache_lookup(key)["receipt"] == f"fc_states_firered_town_{cut[:8]}.txt"


def test_a_cache_hit_copies_the_states_and_writes_a_cached_receipt(cache_env):
    cut, ran, probes, lane = cache_env
    row = [r for r in fc.build_plan(cut, str(lane), MASTER) if r.id == "states_firered_town"][0]
    key = fc.build_key(row, cut, str(lane))[0]
    src = lane.parent / "built"
    _outputs(src, "town", body=b"cached")
    _receipt(probes, row.id, "d" * 40, "PASS")           # the original build's receipt
    assert fc.cache_store(key, {}, row, "d" * 40, str(src), since=0)
    assert fc.main(["--cut", cut, "--lane", str(lane), "--master", MASTER,
                    "--rows", "states_firered_town"]) == 0
    assert ran == []                                       # no emulator
    out = lane / "patch/build/gen3_probe_states_c4p2/firered"
    assert (out / "slink_door.State").read_bytes() == b"cachedslink_door.State"
    rec = (probes / f"fc_states_firered_town_{cut[:8]}.txt").read_text(encoding="utf-8")
    hdr, ok, why = fc.fc_check(f"fc_states_firered_town_{cut[:8]}.txt", rec, str(probes))
    assert ok, why
    assert hdr["verdict"] == (f"CACHED key={key} from fc_states_firered_town_{'d' * 8}.txt "
                              f"@{'d' * 40}")
    summary = (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")
    assert "# RUN 0 / CARRIED 0 / CACHED 1 / FAIL 0" in summary


def test_a_cached_receipt_whose_cache_entry_is_gone_is_invalid(cache_env):
    cut, ran, probes, lane = cache_env
    test_a_cache_hit_copies_the_states_and_writes_a_cached_receipt(cache_env)
    import shutil
    shutil.rmtree(fc.state_cache_root())
    name = f"fc_states_firered_town_{cut[:8]}.txt"
    assert not fc.fc_check(name, (probes / name).read_text(encoding="utf-8"), str(probes))[1]


def test_checkpoint_inputs_are_exactly_its_builds_outputs(tmp_path):
    got = fc.row_inputs(_row("checkpoint_leafgreen"), str(tmp_path), root=str(tmp_path))
    assert set(got) == {"rom:leafgreen"} | {
        f"state:gen3_probe_states_c4p2/{n}" for n in
        fc.BUILD_OUTPUTS["town"] + fc.BUILD_OUTPUTS["battle"] + fc.BUILD_OUTPUTS["trainer"]} | {
        f"state:gen3_probe_states/{n}" for n in fc.BUILD_OUTPUTS["tutorials"]}


def test_checkpoint_is_carry_eligible_only_when_all_its_builds_hit_the_cache(cache_env,
                                                                              monkeypatch):
    cut, _ran, _probes, lane = cache_env
    rows = [r for r in fc.build_plan(cut, str(lane), MASTER)
            if fc.chain_of(r.id) == "probe_firered"]
    d, _est = fc.plan_decisions(rows, cut, True, str(lane))
    assert d["checkpoint_firered"].kind == "RUN" and "rebuilt live" in d["checkpoint_firered"].reason
    for r in rows[:-1]:                                    # warm the cache for all three builds
        kind = fc.build_kind(r.id)[0]
        src = lane.parent / f"src_{r.id}"
        _outputs(src, kind)
        assert fc.cache_store(fc.build_key(r, cut, str(lane))[0], {}, r, "d" * 40, str(src), 0)
    predicted = fc.predicted_checkpoint_inputs(rows[-1], cut, str(lane))
    assert predicted and all(v != "MISSING" for v in predicted.values())
    seen = {}
    monkeypatch.setattr(fc, "carry_decision",
                        lambda row, cut, ev, dn, m, a, inputs: seen.setdefault(row.id, inputs)
                        or fc.Decision("RUN", "x"))
    fc.plan_decisions(rows, cut, True, str(lane))
    assert seen["checkpoint_firered"] == predicted         # carry judged on the cached hashes
    assert d["states_firered_town"].kind == "RUN"
    d2, _ = fc.plan_decisions(rows[:-1], cut, True, str(lane))
    assert all(d2[r.id].kind == "CACHED" for r in rows[:-1])


def test_the_shard_plan_does_not_depend_on_carry_or_cache_decisions(pass_env):
    """Shards are cut over ALL selected rows, so two instances agree even when one of them
    finds a warmer cache or newer receipts."""
    rows = fc.build_plan(CUT, LANE, MASTER)
    est = {r.id: r.budget for r in rows}
    a = [[r.id for r in s] for s in fc.shard_rows(rows, 2, est)]
    assert a == [[r.id for r in s] for s in fc.shard_rows(list(rows), 2, dict(est))]


def test_no_emerald_row_is_carried_on_frlg_shaped_deps():
    """Coordinator review of E4b-CKPT: every Emerald row either names its own inputs or is never
    carried; PROBE_DEPS/DUO_DEPS know only the FR/LG pack and fixtures."""
    for r in fc.build_plan_emerald("c" * 40, LANE, MASTER):
        deps = fc.row_deps(r)
        assert deps is None or any("gen3_emerald" in d or "emerald" in d for d in deps), r.id


def test_default_lanes_follow_slink_work_root(tmp_path):
    """Owner space rule: with SLINK_WORK_ROOT set the lane/master defaults live under
    <root>/lanes/gen3 (not on the Drive or C:); without it the old default is unchanged."""
    import os
    import subprocess
    import sys

    def dry(env_extra, drop=()):
        env = {k: v for k, v in os.environ.items() if k not in drop}
        env.update(env_extra)
        out = subprocess.run([sys.executable, "tools/gen3_final_cut.py", "--cut", "HEAD", "--title", "exp",
                              "--dry-run"], cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), env=env, capture_output=True, text=True, timeout=120)
        return out.stdout.splitlines()[0]
    root = tmp_path.as_posix()
    assert f"lane={root}/lanes/gen3/gen3-lane-clean" in dry({"SLINK_WORK_ROOT": root})
    assert ".claude/worktrees/gen3-lane-clean" in dry({}, drop=("SLINK_WORK_ROOT",))


# ---- R1: the FR/LG/Emerald companion pins (patch/dist/gen3_companions.json), kept out of the default plans' need set ----

COMPANION_KEYS = {f"{t}_companion{s}" for t in ("firered", "leafgreen", "emerald") for s in ("", ":md5", ":canonical")}


def test_default_rom_pins_carry_the_gen3_companions_from_their_own_file_only():
    pins = fc.rom_pins(fc.REPO)
    row = json.loads(open(os.path.join(fc.REPO, "patch/dist/gen3_companions.json")).read())["titles"]["firered"]
    assert COMPANION_KEYS <= set(pins)
    assert pins["firered_companion"] == row["rom_sha1"] and pins["firered_companion:md5"] == row["rom_md5"]
    assert pins["firered_companion:canonical"] == row["canonical_sha1"]
    assert pins["firered"] == "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc"          # the clean pin is unchanged
    assert pins["firered_companion"] != pins["firered"]                          # companion never aliases clean
    rr = {k for k in pins if k.startswith("radical_red_companion")}              # RR still owned by companion_pins.json
    assert rr == {"radical_red_companion", "radical_red_companion:md5", "radical_red_companion:canonical",
                  "radical_red_companion:equivalent_sha1s"}


def _tree_without_gen3_companions(tmp_path, with_file=None):
    import shutil
    tree = tmp_path / "tree"
    (tree / "tools").mkdir(parents=True)
    (tree / "patch/dist").mkdir(parents=True)
    shutil.copyfile(os.path.join(fc.REPO, "tools/gen_gen3_write_checkpoint.py"), tree / "tools/gen_gen3_write_checkpoint.py")
    shutil.copyfile(os.path.join(fc.REPO, "patch/dist/companion_pins.json"), tree / "patch/dist/companion_pins.json")
    if with_file is not None:
        (tree / "patch/dist/gen3_companions.json").write_text(json.dumps(with_file))
    return tree


def test_default_rom_pins_work_without_gen3_companions_and_strict_refuses(tmp_path):
    tree = _tree_without_gen3_companions(tmp_path)
    pins = fc.rom_pins(str(tree))                                                # default path: absent file is fine
    assert not [k for k in pins if k.startswith(("firered_companion", "leafgreen_companion", "emerald_companion"))]
    with pytest.raises(fc.LaneError, match="gen3_companions"):
        fc.rom_pins(str(tree), require_companions=True)                          # a --title frlgc plan fails closed


def test_strict_companion_pins_refuse_a_missing_or_malformed_title(tmp_path):
    real = json.loads(open(os.path.join(fc.REPO, "patch/dist/gen3_companions.json")).read())
    ok = fc.rom_pins(fc.REPO, require_companions=True)
    assert COMPANION_KEYS <= set(ok)
    for title, field in (("leafgreen", None), ("firered", "rom_sha1"), ("emerald", "rom_md5")):
        bad = json.loads(json.dumps(real))
        if field is None:
            del bad["titles"][title]
        else:
            bad["titles"][title][field] = "zz"
        tree = _tree_without_gen3_companions(tmp_path / title, bad)
        fc.rom_pins(str(tree))                                                   # the default path is unaffected
        with pytest.raises(fc.LaneError, match=title):
            fc.rom_pins(str(tree), require_companions=True)


# ---- R3: RR receipts record the companion hash; companion (patched FR/LG/E) rows never share a clean row's carry/cache identity ----

def test_every_rr_duo_row_records_the_companion_rom_hash(tmp_path):
    rows = [r for r in fc.build_plan_rr(CUT, str(tmp_path), MASTER) if r.id.endswith("_rr_as_a")]
    assert rows
    for r in rows:
        assert fc.row_inputs(r, str(tmp_path)) == {"rom:slink_RR": os.path.join(str(tmp_path), "patch/build/slink_RR.gba")}, r.id
        assert r.deps is None                                       # still always RUN (NEVER_CARRIED)
    (tmp_path / "patch/build").mkdir(parents=True)
    (tmp_path / "patch/build/slink_RR.gba").write_bytes(b"rr companion")
    assert fc.hash_inputs(fc.row_inputs(rows[0], str(tmp_path)))["rom:slink_RR"] != "MISSING"


def test_companion_rows_have_their_own_ids_inputs_and_are_never_carried_or_cached(tmp_path):
    lane = str(tmp_path)
    clean = {r.id: r for r in fc.build_plan(CUT, LANE, MASTER)}
    for base in ("whiteout_gen3_fr_as_a", "whiteout_gen3_lg_as_a", "checkpoint_firered", "states_firered_town",
                 "bootcheck_firered_party_town"):
        cid = fc.companion_row_id(base)
        assert cid != base and cid.startswith(fc.COMPANION_ROW_PREFIX) and fc.is_companion_row(cid)
        assert not fc.is_companion_row(base)
        assert not cid.endswith(("_fr_as_a", "_lg_as_a", "_em_as_a"))
        row = fc.Row(cid, "companion", [], lane, 0)
        assert fc.row_deps(row) is None                              # always RUN: no clean receipt can be carried in
        assert fc.build_kind(cid) is None                            # not a cacheable build row
        got = fc.row_inputs(row, lane)
        assert got and set(got) <= {f"rom:{t}_companion" for t in ("firered", "leafgreen", "emerald")}
        assert not set(got) & set(fc.row_inputs(clean[base], lane))  # keys never collide with the clean row's
    hand_made = fc.Row("frlgc_whiteout_gen3_fr_as_a", "companion", [], lane, 0)       # a suffix that WOULD match a clean row
    assert fc.row_deps(hand_made) is None and set(fc.row_inputs(hand_made, lane)) <= {f"rom:{t}_companion" for t in ("firered", "leafgreen", "emerald")}
    assert fc.companion_row_inputs(("firered",), lane) == {"rom:firered_companion": os.path.join(lane, "patch/build/slink_FireRed.gba")}
    assert fc.companion_row_inputs(("emerald",), lane) == {"rom:emerald_companion": os.path.join(lane, "patch/build/slink_Emerald.gba")}
    with pytest.raises(KeyError):
        fc.companion_row_inputs(("radical_red",), lane)


def test_the_state_cache_never_serves_a_companion_row_and_keys_follow_the_rom_bytes(tmp_path):
    (tmp_path / fc.STAGED["firered"]).parent.mkdir(parents=True)
    (tmp_path / fc.STAGED["firered"]).write_bytes(b"clean")
    blobs, biz = dict(BLOBS), dict(BIZ)
    k1 = fc.build_key(_row("states_firered_town"), CUT, str(tmp_path), blobs=blobs, bizhawk=biz)[0]
    (tmp_path / fc.STAGED["firered"]).write_bytes(b"patched")
    k2 = fc.build_key(_row("states_firered_town"), CUT, str(tmp_path), blobs=blobs, bizhawk=biz)[0]
    assert k1 and k2 and k1 != k2                                    # a different ROM is a different key
    crow = fc.Row(fc.companion_row_id("states_firered_town"), "companion", [], str(tmp_path), 0)
    key, why = fc.build_key(crow, CUT, str(tmp_path), blobs=blobs, bizhawk=biz)
    assert key is None and "companion" in why                       # refused, not keyed on the clean STAGED path
    crow = fc.Row(fc.companion_row_id("checkpoint_firered"), "companion", [], str(tmp_path), 0)
    assert fc.predicted_checkpoint_inputs(crow, CUT, str(tmp_path)) is None


# ---------------------------------------------------------------------------
# patch-first: a clean FireRed / Emerald zip-boot is a REFUSAL proof, with its own row id
# ---------------------------------------------------------------------------

def test_the_clean_zip_boot_rows_are_refusal_proofs_with_new_ids():
    assert fc.ZIP_BOOT_REFUSED == ("firered", "emerald")
    fr = {r.id for r in fc.build_plan("c" * 40, LANE, MASTER)}
    em = {r.id for r in fc.build_plan_emerald("c" * 40, LANE, MASTER)}
    assert "zip_boot_firered_refused" in fr and "zip_boot_firered" not in fr
    assert "zip_boot_emerald_refused" in em and "zip_boot_emerald" not in em
    # RR boots its companion: unchanged
    assert "zip_boot_radicalred" in {r.id for r in fc.build_plan_rr("c" * 40, LANE, MASTER)}
    assert fc.ZIP_BOOT["firered"][5] is None and fc.ZIP_BOOT["radical_red"][5]


def test_zip_boot_refusal_needs_the_clients_own_refusal_and_no_connect_and_no_hello():
    client_re = re.compile(fc.ZIP_BOOT["firered"][4])
    refused = ("Unsupported Gen 3 cartridge: this firered cartridge needs the SLink companion patch; "
               "prepare it through the Manager or /patcher")
    ok = fc.zip_boot_refusal_ok
    assert ok(client_re, "[zip-boot] entry raised: [SLink] " + refused, "")
    assert not ok(client_re, "", "")                                                    # nothing logged
    assert not ok(client_re, refused.replace("firered", "leafgreen"), "")               # another title
    assert not ok(client_re, refused + "\nTCP connected", "")                           # it connected anyway
    assert not ok(client_re, refused, "hello rom=firered ")                             # the server saw it
    assert "pcall(dofile" in fc._BOOT_LUA_REFUSED and "pcall(dofile" not in fc._BOOT_LUA


def test_resume_at_a_cut_never_adopts_the_old_row_names_receipt(monkeypatch, tmp_path):
    """Row-id reuse: the renamed refusal proofs must not resume from the old clean-client PASS receipts."""
    import e2e_duo

    new = "clean_rr_refused_gen3_rr_as_a"
    assert "native_absent_gen3" not in e2e_duo.SCENARIOS and "clean_rr_refused_gen3" in e2e_duo.SCENARIOS
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    ids = [r.id for r in fc.build_plan_rr(cut, LANE, MASTER)]
    assert new in ids and not any(i.startswith(("native_absent_gen3", "linked_faint_active_clean_gen3",
                                                "faint_cmd_clean_gen3")) for i in ids)
    monkeypatch.setattr(fc, "PROBES", str(tmp_path))
    for old in ("native_absent_gen3_rr_as_a", "linked_faint_active_clean_gen3_rr_as_a",
                "faint_cmd_clean_gen3_rr_as_a"):
        _receipt(tmp_path, old, cut, "PASS")
        assert fc.prior_verdict(old, cut) == "PASS"        # a valid old receipt exists at this very cut ...
    assert fc.prior_verdict(new, cut) is None              # ... and is not the new row's
    # even the old receipt copied under the new name is rejected: its header names the old row
    (tmp_path / f"fc_{new}_{cut[:8]}.txt").write_text(
        (tmp_path / f"fc_native_absent_gen3_rr_as_a_{cut[:8]}.txt").read_text(encoding="utf-8"), encoding="utf-8")
    assert fc.prior_verdict(new, cut) is None


# ---------------------------------------------------------------------------
# F1: the clean plans stage clean dumps the launcher refuses -- refuse them BY NAME up front
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", ["frlg", "emerald"])
def test_the_clean_plans_are_refused_by_name_before_anything_runs(title, monkeypatch, capsys):
    monkeypatch.setattr(fc, "CLEAN_PLAN_TITLES", REAL_CLEAN_PLAN_TITLES)
    def boom(*a, **k):
        raise AssertionError("a refused plan launched or provisioned something")
    for name in ("run_row", "run_once", "provision", "copy_inputs"):
        monkeypatch.setattr(fc, name, boom)
    cut = _git(fc.REPO, "rev-parse", "HEAD")
    assert fc.main(["--cut", cut, "--title", title, "--lane", LANE, "--master", MASTER]) == 2
    err = capsys.readouterr().err
    assert "needs the SLink companion patch" in err and "--title frlgc" in err and f"--title {title}" in err
    # the default title is frlg: no --title at all is the same refusal
    assert fc.main(["--cut", cut, "--lane", LANE, "--master", MASTER]) == 2
    # inspection still works
    assert fc.main(["--cut", cut, "--title", title, "--lane", LANE, "--master", MASTER, "--list"]) == 0


def test_the_companion_exp_and_rr_titles_are_not_refused(monkeypatch):
    monkeypatch.setattr(fc, "CLEAN_PLAN_TITLES", REAL_CLEAN_PLAN_TITLES)
    assert set(fc.CLEAN_PLAN_TITLES) == {"frlg", "emerald"}
    for title in ("rr", "exp", "frlgc"):
        assert fc.clean_plan_refusal(title) is None, title
    # exp really has no companion_required (the launcher would refuse it too otherwise)
    entry = (os.path.join(fc.REPO, "lua", "gen3", "entry.lua"))
    text = open(entry, encoding="utf-8").read()
    exp_block = text[text.index("gen3_exp = {"):]
    assert "companion_required" not in exp_block[:exp_block.index("}")]


# ---------------------------------------------------------------------------
# F4: --resume adopts a prior receipt only when its recorded inputs are this lane's current ones
# ---------------------------------------------------------------------------

def test_resume_reruns_a_row_whose_recorded_inputs_differ(pass_env):
    cut, ran, probes = pass_env
    row = _plan_row("states_firered_town")
    assert fc.row_inputs(row, LANE)                      # this row really has non-git inputs
    now = fc.hash_inputs(fc.row_inputs(row, LANE))
    _receipt(probes, "states_firered_town", cut, "PASS", inputs={k: "f" * 64 for k in now})   # another ROM's hashes
    _receipt(probes, "states_firered_battle", cut, "PASS")                                     # current inputs
    assert _run(cut, "--resume") == 0
    assert ran == ["states_firered_town"]                # re-run, never adopted; the unchanged row resumed


def test_resume_reruns_a_receipt_with_no_recorded_inputs(pass_env):
    cut, ran, probes = pass_env
    _receipt(probes, "states_firered_town", cut, "PASS", inputs={})                # an old receipt: unrecorded
    _receipt(probes, "states_firered_battle", cut, "PASS")
    assert _run(cut, "--resume") == 0 and ran == ["states_firered_town"]


def test_resume_inputs_problem_names_what_differs():
    row = _plan_row("states_firered_town")
    now = fc.hash_inputs(fc.row_inputs(row, LANE))
    same = "# " + fc.inputs_note(now)
    assert fc.resume_inputs_problem(row, same, LANE) is None
    problem = fc.resume_inputs_problem(row, "# " + fc.inputs_note({**now, "rom:extra": "1"}), LANE)
    assert problem and "rom:extra" in problem
    assert "unrecorded" in fc.resume_inputs_problem(row, "no inputs line", LANE)


# ---------------------------------------------------------------------------
# row-scoped, KEPT e2e result files (the shared e2e_<scenario>_* names are overwritten by the next row of the scenario)
# ---------------------------------------------------------------------------

def _duo_rows(lane, *ids):
    plan = {r.id: r for r in fc.build_plan("c" * 40, lane, MASTER)}
    return [plan[i] for i in ids]


def _writing_run_once(lane, results):
    """A fake e2e_duo: it writes the scenario-named result files the real harness writes, with the row's verdict inside."""
    def fake(row, deadline):
        scenario = row.argv[row.argv.index("--scenario") + 1]
        build = os.path.join(lane, "patch", "build")
        os.makedirs(build, exist_ok=True)
        rc, tag = results[row.id]
        for name in (f"e2e_{scenario}_a_result.txt", f"e2e_{scenario}_b_result.txt", f"e2e_{scenario}_pydec_result.txt",
                     f"e2e_{scenario}_a_attempt1_result.txt", f"e2e_{scenario}_a_1_witness.bin"):
            with open(os.path.join(build, name), "w", encoding="utf-8") as handle:
                handle.write(f"{tag}: {name}")
        return rc, f"[duo] {tag}", False, False
    return fake


def test_two_rows_of_one_scenario_keep_distinct_result_files_and_a_failing_row_keeps_its_own(monkeypatch, tmp_path):
    lane = str(tmp_path / "lane")
    fr, lg = _duo_rows(lane, "active_end_gen3_fr_as_a", "active_end_gen3_lg_as_a")
    _stub_row_env(monkeypatch, tmp_path / "probes", [])
    (tmp_path / "probes").mkdir()
    monkeypatch.setattr(fc, "run_once", _writing_run_once(lane, {fr.id: (1, "FR-FAILED b: missing WRITES 0"), lg.id: (0, "LG-PASSED")}))
    assert fc.run_row(fr, "c" * 40, lane, None)[0] == "FAIL exit=1"
    assert fc.run_row(lg, "c" * 40, lane, None)[0] == "PASS"
    shared = os.path.join(lane, "patch", "build", "e2e_active_end_gen3_b_result.txt")
    assert "LG-PASSED" in open(shared, encoding="utf-8").read()            # the shared name WAS overwritten by the later row ...
    kept = {r.id: os.path.join(lane, "patch", "build", "fc_rows", r.id) for r in (fr, lg)}
    assert kept[fr.id] != kept[lg.id]
    assert "FR-FAILED b: missing WRITES 0" in open(os.path.join(kept[fr.id], "e2e_active_end_gen3_b_result.txt"), encoding="utf-8").read()
    assert "LG-PASSED" in open(os.path.join(kept[lg.id], "e2e_active_end_gen3_b_result.txt"), encoding="utf-8").read()
    for r in (fr, lg):                                                      # every artifact kind is kept, per row
        assert sorted(os.listdir(kept[r.id])) == [
            "e2e_active_end_gen3_a_1_witness.bin", "e2e_active_end_gen3_a_attempt1_result.txt",
            "e2e_active_end_gen3_a_result.txt", "e2e_active_end_gen3_b_result.txt", "e2e_active_end_gen3_pydec_result.txt"]
        text = open(fc.receipt_path(r.id, "c" * 40), encoding="utf-8").read()
        assert f"# kept: {kept[r.id].replace(os.sep, '/')}" in text         # and the receipt header names where


def test_keeping_the_files_changes_neither_the_inputs_note_nor_the_verdict(monkeypatch, tmp_path):
    lane = str(tmp_path / "lane")
    (row,) = _duo_rows(lane, "faint_cmd_gen3_fr_as_a")
    (tmp_path / "probes").mkdir()
    _stub_row_env(monkeypatch, tmp_path / "probes", [])
    monkeypatch.setattr(fc, "run_once", _writing_run_once(lane, {row.id: (0, "ok")}))
    assert fc.run_row(row, "c" * 40, lane, None)[:2] == ("PASS", 1)
    text = open(fc.receipt_path(row.id, "c" * 40), encoding="utf-8").read()
    assert fc.parse_inputs(text) == fc.hash_inputs(fc.row_inputs(row, lane))      # the same inputs line as before
    assert fc.prior_verdict(row.id, "c" * 40, lane) == "PASS"                      # the receipt is still a valid, resumable one
    assert fc.resume_inputs_problem(row, text, lane) is None


def test_a_row_that_is_not_an_e2e_duo_run_or_left_nothing_keeps_nothing(tmp_path):
    lane = str(tmp_path / "lane")
    build = tmp_path / "lane" / "patch" / "build"
    build.mkdir(parents=True)
    (build / "e2e_faint_cmd_gen3_a_result.txt").write_text("x", encoding="utf-8")
    plan = {r.id: r for r in fc.build_plan("c" * 40, lane, MASTER)}
    assert fc.keep_row_artifacts(plan["states_firered_town"], lane) is None          # not a duo row
    assert fc.keep_row_artifacts(plan["whiteout_gen3_fr_as_a"], lane) is None        # a duo row that left no file
    assert fc.keep_row_artifacts(plan["faint_cmd_gen3_fr_as_a"], lane).endswith("/fc_rows/faint_cmd_gen3_fr_as_a")
    # a re-run of the SAME row replaces only its own copy
    (build / "e2e_faint_cmd_gen3_a_result.txt").write_text("second run", encoding="utf-8")
    fc.keep_row_artifacts(plan["faint_cmd_gen3_fr_as_a"], lane)
    assert (build / "fc_rows" / "faint_cmd_gen3_fr_as_a" / "e2e_faint_cmd_gen3_a_result.txt").read_text(encoding="utf-8") == "second run"


def test_the_kept_directory_is_gitignored_so_the_lane_stays_tracked_clean():
    ignored = subprocess.run(["git", "-C", fc.REPO, "check-ignore", "-q", "patch/build/fc_rows/some_row/e2e_x_a_result.txt"])
    assert ignored.returncode == 0, "patch/build/fc_rows must be ignored (patch/.gitignore build/)"


# ---------------------------------------------------------------------------
# per-invocation summaries never clobber an earlier pass's
# ---------------------------------------------------------------------------

def _run_files(probes, cut):
    return sorted(p.name for p in probes.glob(f"fc_SUMMARY_{cut[:8]}_run*.txt"))


def test_a_later_invocation_never_overwrites_an_earlier_invocations_summary(pass_env, monkeypatch):
    cut, ran, probes = pass_env
    monkeypatch.setattr(fc, "utcnow", lambda: __import__("datetime").datetime(2026, 10, 3, 12, 0, 0, tzinfo=__import__("datetime").UTC))
    assert _run(cut) == 0                                              # pass 1: both rows
    first = _run_files(probes, cut)
    assert len(first) == 1
    text1 = (probes / first[0]).read_text(encoding="utf-8")
    assert "states_firered_town" in text1 and "states_firered_battle" in text1 and "OVERALL: PASS (2/2 rows)" in text1
    assert fc.main(["--cut", cut, "--lane", LANE, "--master", MASTER, "--rows", "states_firered_town"]) == 0   # pass 2: a subset
    runs = _run_files(probes, cut)
    assert len(runs) == 2 and len(set(runs)) == 2                      # even within the same UTC second: distinct names
    assert (probes / first[0]).read_text(encoding="utf-8") == text1    # the first pass's summary is byte-for-byte untouched
    plain = (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")
    assert "states_firered_battle" not in plain and "OVERALL: PASS (1/1 rows)" in plain     # the plain name = the latest pass
    second = next(r for r in runs if r != first[0])
    assert (probes / second).read_text(encoding="utf-8") == plain


def test_one_invocation_keeps_one_run_file_updated_after_every_row(pass_env):
    cut, ran, probes = pass_env
    assert _run(cut) == 0
    (name,) = _run_files(probes, cut)
    assert name.startswith(f"fc_SUMMARY_{cut[:8]}_run") and (probes / name).read_text(encoding="utf-8") ==         (probes / f"fc_SUMMARY_{cut[:8]}.txt").read_text(encoding="utf-8")


def test_a_title_summary_keeps_its_suffix_in_both_names(pass_env):
    cut, _ran, probes = pass_env
    fc.write_summary(cut, [], "_frlgc", "20261003T120000Z")
    assert (probes / f"fc_SUMMARY_{cut[:8]}_frlgc.txt").is_file()
    assert (probes / f"fc_SUMMARY_{cut[:8]}_frlgc_run20261003T120000Z.txt").is_file()


def test_merge_summary_also_keeps_its_own_copy_and_the_readers_ignore_run_files(pass_env):
    cut, _ran, probes = pass_env
    assert _run(cut) == 0
    (first,) = _run_files(probes, cut)
    before = (probes / first).read_text(encoding="utf-8")
    _receipt(probes, "states_firered_town", cut, "PASS")
    _receipt(probes, "states_firered_battle", cut, "PASS")
    assert fc.main(["--cut", cut, "--lane", LANE, "--master", MASTER, "--merge-summary",
                    "--rows", "states_firered_town,states_firered_battle"]) == 0
    runs = _run_files(probes, cut)
    assert len(runs) == 2 and (probes / first).read_text(encoding="utf-8") == before
    # summaries are never evidence: no reader treats a run file as a receipt
    for name in runs:
        assert fc.receipt_evidence(name, (probes / name).read_text(encoding="utf-8"), str(probes)) is None
    assert set(fc.collect_evidence(str(probes))) == {"states_firered_town", "states_firered_battle"}
    assert fc.prior_verdict("states_firered_town", cut) == "PASS"


def test_an_aborted_invocation_leaves_no_empty_summary_file(pass_env):
    cut, _ran, probes = pass_env
    stamp = fc.new_run_stamp(cut, "")
    assert not list(probes.glob(f"fc_SUMMARY_{cut[:8]}_run*.txt"))          # reserving a stamp creates nothing
    fc.write_summary(cut, [], "", stamp)
    assert fc.new_run_stamp(cut, "") != stamp                               # but a written one is never reused
