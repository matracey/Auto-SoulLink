#!/usr/bin/env python3
"""gen3_final_cut.py -- the G4 final-cut pass as one command (card G4-FINALCUT-RUNNER).

    python tools/gen3_final_cut.py --cut <sha>                     # the whole pass
    python tools/gen3_final_cut.py --cut <sha> --dry-run           # print the plan, launch nothing
    python tools/gen3_final_cut.py --cut <sha> --resume            # skip rows already PASSed at <sha>
    python tools/gen3_final_cut.py --cut <sha> --rows 'item2b*,checkpoint_*' --stop-at 2026-09-25T06:00Z
    python tools/gen3_final_cut.py --cut <sha> --list              # the row ids
    python tools/gen3_final_cut.py --cut <sha> --title rr          # the G5 (Radical Red) plan
    python tools/gen3_final_cut.py --cut <sha> --title emerald     # the E4b (Emerald) plan
    python tools/gen3_final_cut.py --cut <sha> --title exp         # test-only expansion plan
    python tools/gen3_final_cut.py --cut <sha> --title frlgc       # the PATCHED FR/LG/Emerald re-cut
    python tools/gen3_final_cut.py --cut <sha> --title frlgc-rand  # the randomized-COMPANION rows + the clean zip-boot refusals

docs/gen3/G4_final_cut_runbook.md §0-§11 is the source of every row; §12 names the gaps this closes.
--title rr (card G5-RUNNER-RR) is a separate, opt-in plan: the RR duo rows (every
tools/e2e_duo.py SCENARIOS entry whose games includes gen3_rr, minus the owner-signed
linked_faint_active_mega_gen3 limit), the RR opcode gates, and the release zip built, checked
and booted on the RR companion (rr_zip_build, rr_zip_check, zip_boot_radicalred); its summary is
fc_SUMMARY_<cut8>_rr.txt. --title emerald (card E4b-FINALCUT, docs/gen3_emerald/PLAN.md §5 E4b) is
a third, opt-in plan: the SOURCE-lane generator checks, the Emerald states/duo/bootcheck rows, the
Emerald-only slice of the probe-gates live suite, the shadow-negatives manifest check, and the
release zip built, checked and booted on Emerald (emerald_zip_build, emerald_zip_check,
zip_boot_emerald); its summary is fc_SUMMARY_<cut8>_emerald.txt. §13 of the runbook is its source.
--title exp is XG3's test-only reference-build plan: source checks, expansion units, every
applicable duo registry entry, and ZIP build/check/boot. Its summary is
fc_SUMMARY_<cut8>_exp.txt. Runtime rows must log the server's --test-only-route; ZIP boot also
logs the existing duo client-admission seam. Source/build rows use no runtime route. This
plan does not open production admission or replace XG3's separate observer/calc obligations.
EG4 (ruling 24) landed lua/gen3/entry.lua:108's Entry.ROUTED gen3_emerald admission, so
zip_boot_emerald now actually boots Emerald like any other title. zip_boot() still checks the
EXTRACTED zip's own entry.lua before attempting the boot (emerald_admission_blocker) and reports
SKIP-ALLOWED BLOCKED-EG4 (not a silent skip, not a PASS) if that particular zip predates EG4 --
a real possibility when re-running an old --cut, just no longer the expected outcome on a current
one. --title defaults to "frlg", so the default plan and its row ids are unchanged.
--title frlgc is the PATCHED (companion) FR/LG/Emerald re-cut (owner decision: one cut on the companion ROMs, adding the
clause / ball-gate / shiny rows): 65 rows, ALL always RUN (frlgc_ ids, never carried or cached), summary fc_SUMMARY_<cut8>_frlgc.txt.
Per title a companion-check row proves the staged patch/build/slink_*.gba is the pinned composition of the clean dump and the
shipped UPS; the duo rows are the frlg and emerald plans' duo rows run with e2e_duo.py --gen3-companion plus the species / gender /
type clause, ball-gate, shiny-bonus and species-family rows on FR, LG and Emerald; a green duo or zip-boot receipt must carry the
CLIENT's own `<title> (companion by hash)` admission line with the pinned rom prefix (companion_attempt_problem, in run_row and
fc_check; it owes nothing extra to frlgc_source_* (their own companion-check verdict is the proof), frlgc_bootcheck_* (bound by
the F2 sha1 pin in gen3_fixtures boot-check --companion) and frlgc_release_gate_quick (emulator-free source lanes)). Not in it:
states_/tutorials_/checkpoint_ (the probe wrapper is wired to the clean dump), probe_gates, item6.
--title frlgc-rand is the small opt-in companion of frlgc (frlgc stays at its 65 rows): the randomized-companion admission /
link / nearby-trainer-panel rows (FR+LG and Emerald) and the clean zip-boot REFUSAL proofs, 13 rows, ids frlgcr_*, summary
fc_SUMMARY_<cut8>_frlgc_rand.txt. Its rows need the randomized ROMs of SLINK_GEN3_RAND_ROMS; a row whose ROM is absent BLOCKS the pass
by name (exit 2), never skips. --dry-run prints each row's estimated wall time (from retained receipts, see RAND_ROW_EST_NOTE).
Sequential, one emulator lane (docs/gen3/PLAN.md:23). Order: provision the lane at --cut (and the
primary-branch tree when item 6 is selected), then every selected row in runbook order. Each row writes
docs/gen3/probes/fc_<row>_<cut8>.txt through gen3_probe_receipt.run_receipt_text, and the pass
rewrites docs/gen3/probes/fc_SUMMARY_<cut8>.txt after every row (the latest invocation's rows) and the same text in
fc_SUMMARY_<cut8>_run<UTC stamp>.txt, which no later invocation overwrites.

Rules this runner enforces itself:
  - the lane must be at --cut with `git status --porcelain --untracked-files=no` empty, or the
    pass aborts before any row; a row that leaves the lane tracked-dirty fails and aborts the pass;
  - an unchanged failed row is never re-run automatically: one retry only when the failure is a
    CPU-contention timeout (classify_failure), and a FAIL receipt at the same cut blocks the row
    on later invocations too (move the receipt aside to re-run it deliberately);
  - it kills only the process trees it launched (taskkill /PID <its own child> /T), never by name;
  - rewind off: every BizHawk config a row wrote under the lane's patch/build is read back and a
    `Rewind.Enabled` that is not false fails the row;
  - a SKIP is a failure unless ALLOWED_SKIPS names the row with an owner ruling.

Helper subcommands the plan itself calls (they are rows like any other):
    python tools/gen3_final_cut.py zip-boot --zip <zip> --lane <lane>        (runbook §9 boot step)
    python tools/gen3_final_cut.py stage-companions [--tree <main checkout>] (UPS on the clean dumps -> patch/build/slink_*.gba)
    python tools/gen3_final_cut.py item6 --branch <lane> --master <tree>     (runbook §10)
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import gen3_probe_receipt as receipts  # noqa: E402

REPO = os.path.dirname(_HERE)
PROBES = os.path.join(REPO, "docs", "gen3", "probes")
PY = sys.executable

# "emerald" keys added for --title emerald (card E4b-FINALCUT); only ever looked up by explicit
# key (never iterated as a whole), so this is inert for --title frlg/rr.
STAGED = {"firered": "patch/build/gen3_Pokemon_-_FireRed_Version_(USA).gba",
          "leafgreen": "patch/build/gen3_Pokemon_-_LeafGreen_Version_(USA).gba",
          "emerald": "patch/build/gen3_Pokemon_-_Emerald_Version_(USA,_Europe).gba",
          "exp": "patch/build/gen3_pokeemerald.gba"}
# The patched (companion) FR/LG/Emerald cartridges of the --title frlgc re-cut: patch/tools/build.py --target X output names,
# byte-pinned to patch/dist/gen3_companions.json (rom_pins(..., require_companions=True)).
COMPANION_ROMS = {"firered": "patch/build/slink_FireRed.gba", "leafgreen": "patch/build/slink_LeafGreen.gba",
                  "emerald": "patch/build/slink_Emerald.gba"}
COMPANION_PINNED_INPUTS = {rel: (f"{title}_companion", [rel]) for title, rel in COMPANION_ROMS.items()}
ROOT_DUMPS = {"firered": "Pokemon - FireRed Version (USA).gba",
              "leafgreen": "Pokemon - LeafGreen Version (USA).gba",
              # tools/gen3_fixtures.py:452 PARTY_TITLES["emerald"]["rom"]
              "emerald": "Pokemon - Emerald Version (USA, Europe).gba",
              "exp": ".cache/expansion-output/reference/pokeemerald.gba"}
EMERALD_SAVERAM = "Pokemon - Emerald Version (USA, Europe).SaveRAM"   # tools/gen3_fixtures.py:918
# The gitignored inputs a lane needs (runbook §0.3, item 6's inputs per
# item6_route_diff_{branch,master}_2026-09-24.txt). PINNED ones are verified against the tree's
# own pins (rom_pins) and taken from the first source whose content matches -- the lane itself,
# then this runner's worktree, then the main checkout -- so a stale root file can never win
# (G4-FINALCUT-LIVEFIX: root's pre-rebuild companion bf8e94a0 once overwrote the lane's
# 6cf77ba4). No match anywhere aborts the pass. UNPINNED ones are copied only when the lane lacks
# them (the lane's own copy is never overwritten), from this worktree first, then the main
# checkout. No .cache item is read by any row here (the only .cache consumer in e2e_duo is the
# Gen 1 randomizer's UPR jar).
PINNED_INPUTS = {   # lane path -> (pin key, relative paths a source may hold it under)
    ROOT_DUMPS["firered"]: ("firered", [ROOT_DUMPS["firered"], STAGED["firered"]]),
    STAGED["firered"]: ("firered", [STAGED["firered"], ROOT_DUMPS["firered"]]),
    ROOT_DUMPS["leafgreen"]: ("leafgreen", [ROOT_DUMPS["leafgreen"], STAGED["leafgreen"]]),
    STAGED["leafgreen"]: ("leafgreen", [STAGED["leafgreen"], ROOT_DUMPS["leafgreen"]]),
    # probe_gates and the unit gate's pinned-dump tests read the RR companion build
    "patch/build/slink_RR.gba": ("radical_red_companion", ["patch/build/slink_RR.gba"]),
}
# --title emerald (card E4b-FINALCUT): kept OUT of PINNED_INPUTS/GITIGNORED_INPUTS so frlg/rr's
# printed provision-plan text (and every --title's copy_inputs() call that doesn't ask for it) is
# untouched; provision_plan()/copy_inputs() merge this in only when the caller passes it (run_pass
# does, only for --title emerald). Pin key "emerald" is rom_pins()'s own regex output for
# ("gen3_emerald", "emerald", "clean") now that "emerald" is a STAGED title.
EMERALD_PINNED_INPUTS = {
    ROOT_DUMPS["emerald"]: ("emerald", [ROOT_DUMPS["emerald"], STAGED["emerald"]]),
    STAGED["emerald"]: ("emerald", [STAGED["emerald"], ROOT_DUMPS["emerald"]]),
}
# card E4b-CKPT review (OMP cx-6b619663 F1): the unit_emerald row's own tests (test_gen3_codec_
# emerald.py, test_gen3_emerald_{areas,badges,moves_items,pack}.py, test_gen3_title_syms.py) read
# the pret CLONE at .cache/pret/pokeemerald/. It was an Emerald-only extra until EXPLODE-BIND made
# the SHARED "unit" lane read it too (the Emerald write-checkpoint generator in
# test_gen3_write_checkpoint.py / test_gen3_explode_bind.py): release_gate_quick failed 3 tests in
# the FR/LG lane without it (fc 66184e35). It now lives in the shared UNPINNED_INPUTS below, so the
# Emerald-only extra is empty (kept as the extension point).
EMERALD_UNPINNED_INPUTS: list[str] = []
EXPANSION_TITLE = "emerald_expansion_28877d73"
EXPANSION_PACK = "data/games/gen3_exp/28877d73"
EXPANSION_PINNED_INPUTS = {
    STAGED["exp"]: ("exp", [STAGED["exp"], ROOT_DUMPS["exp"]]),
    ROOT_DUMPS["exp"]: ("exp", [ROOT_DUMPS["exp"], STAGED["exp"]]),
}
# Expansion-only provisioning avoids unrelated FR/RR/Gen1 input requirements. The source
# remains a junction/symlink; an available compile.json is preserved beside the offline object.
EXPANSION_UNPINNED_INPUTS = [".cache/expansion-output/", ".cache/pret/pokeemerald/"]
EXPANSION_ZIP_PACK_FILES = ("profile.json", "engine_signals.json", "write_checkpoint.json",
                           "area_map.json", "gen3_exp_locations.lua")
EXPANSION_UNIT_EXTRAS = (
    "test_build_expansion.py", "test_extract_expansion_data.py",
    "test_extract_expansion_config.py", "test_gen_expansion_facts.py",
    "test_gen3_codec_expansion.py", "test_e2e_duo_gen3_exp.py",
    "test_gen3_fixture_exp.py", "test_gen3_title_syms_exp.py",
    "test_gen3_final_cut.py", "test_gen3_final_cut_exp.py",
)


def expansion_unit_files(repo=REPO):
    """Collect expansion modules by filename, so new coverage joins the frozen row."""
    unit = Path(repo) / "tests/unit"
    matched = {p.name for pattern in ("test_gen3_exp_*.py", "test_gen3_expansion_*.py")
               for p in unit.glob(pattern)}
    return [f"tests/unit/{name}" for name in sorted(matched | set(EXPANSION_UNIT_EXTRAS))]


EXPANSION_UNIT_FILES = expansion_unit_files()
# E7-SKIPS: unit_emerald's own tests, selected by FILE, never `-k` (release_lanes.py's own
# rule, verify_gen3_release.py's _UNIT_FILES). `-k "gen3 and emerald"` over the whole tests/unit
# tree still IMPORTS every module under the path first -- a `-k` selector filters ITEMS, not
# which files get collected -- so test_gen1_purergb_rom_content.py's own module-level
# `pytest.skip(..., allow_module_level=True)` and tests/conftest.py's pokecrystal-clone guard
# both fired unconditionally, regardless of -k (reproduced: they still fire under a -k that
# cannot possibly match anything). The substring filter also incidentally pulled in unrelated
# Gen 2 parametrize ids, e.g. test_gen2_pairing_matrix.py's
# test_a_gen2_half_beside_a_gen1_or_gen3_half_is_refused[...-emerald] ("or_gen3" in the function
# name, "emerald" as one of three parametrized opponents) -- nothing to do with this row.
EMERALD_UNIT_FILES = [
    "tests/unit/test_gen3_codec_emerald.py",
    "tests/unit/test_gen3_emerald_areas.py",
    "tests/unit/test_gen3_emerald_badges.py",
    "tests/unit/test_gen3_emerald_checkpoint.py",
    "tests/unit/test_gen3_emerald_client.py",
    "tests/unit/test_gen3_emerald_entry.py",
    "tests/unit/test_gen3_emerald_fixture_qualify.py",
    "tests/unit/test_gen3_emerald_moves_items.py",
    "tests/unit/test_gen3_emerald_pack.py",
    "tests/unit/test_gen3_emerald_play_legs.py",
    "tests/unit/test_gen3_emerald_server.py",
    "tests/unit/test_gen3_fixture_qualify_emerald.py",
    "tests/unit/test_gen3_title_syms.py",
]
UNPINNED_INPUTS = ["Pokemon - Crystal Version (USA).gbc",
                   # the unpatched RR ROM clean_rr_refused_gen3's refusal-proof side and test_mailbox_absent.lua need; a
                   # lane outside the main checkout's folder can't find it by walking up
                   "Pokemon - Radical Red.gba",
                   "patch/build/gen1_red.gb", "patch/build/gen1_blue.gb",
                   "patch/build/gen1_yellow.gbc", "patch/build/gen2_crystal.gbc",
                   "patch/gen1/build/slink_red.gb", "patch/gen1/build/slink_blue.gb",
                   "patch/gen1/build/slink.sym",
                   # tests/unit/test_gen3_shadow_diff.py parses these physical P3 captures
                   "patch/build/shadow_wire/",
                   # the pret clones the unit suite reads (test_gen1_trade_patch, the Gen 3
                   # profile/route/tutorial tests, and since EXPLODE-BIND the Emerald write
                   # checkpoint generator); only these four, not all ~630 MB of .cache/pret
                   ".cache/pret/pokered/", ".cache/pret/pokefirered/", ".cache/pret/pokecrystal/",
                   ".cache/pret/pokeemerald/",
                   # release_gate_quick counts a skip as a failure: the T5 private candidate builds
                   # (tests/unit/test_gen3_trade_duo*.py), the pinned expansion reference build
                   # (test_gen3_title_syms_exp.py) and the fetched RR sources
                   # (test_gen3_rr_generators.py; `python tools/fetch_rr_sources.py`)
                   "patch/build/candidate-firered-trade/", "patch/build/candidate-leafgreen-trade/",
                   ".cache/expansion-output/", "data/.rr_src_cache/"]
GITIGNORED_INPUTS = list(PINNED_INPUTS) + UNPINNED_INPUTS
ITEM6_INPUTS = [p for p in UNPINNED_INPUTS if not p.endswith("/")]   # the Gen 1/2 dumps and builds

# (row-id glob, reason substring the output must carry, the owner ruling that signs it)
ALLOWED_SKIPS = [
    ("linked_faint_active_mega_gen3_*", "R5 needs an RR trainer route",
     "owner ruling 20 (G4_request_draft.md §6): RR row R5 is a signed G5 limit, kept as a named SKIP"),
    # "zip_boot_emerald" / "BLOCKED-EG4:" lived here while ruling 24 was unsigned; EG4 landed
    # (lua/gen3/entry.lua:108's Entry.ROUTED admits gen3_emerald) and zip_boot_emerald now PASSes
    # for real (fc_SUMMARY_9c96e745_emerald.txt row 24), so the entry is retired. zip_boot()'s own
    # emerald_admission_blocker still reports BLOCKED-EG4 for a zip built before that signature
    # (e.g. re-running an old --cut) -- a real FAIL on a current cut, not a named skip anymore.
]

ORIENT = {"gen3_frlg": "fr_as_a", "gen3_lgfr": "lg_as_a", "gen3_rr": "rr_as_a",
          "gen3_emerald": "em_as_a", "gen3_exp": "exp_as_a"}


@dataclass
class Row:
    id: str
    item: str              # runbook section tag, e.g. "§5 item2b"
    argv: list
    cwd: str
    budget: int            # seconds before the runner kills the row's own process tree
    env: dict = field(default_factory=dict)
    emulator: bool = True
    # True: the tool applies its own fail-closed skip policy and its exit code is the verdict
    # (verify_gen3_release's ALLOWED_SKIPS; item6, whose case outputs may legitimately skip)
    own_verdict: bool = False
    deps: list | None = None   # --carry: dependency globs (row_deps); None = never carried
    outputs_dir: str | None = None   # §1 build rows: where the .State files land
    est: float | None = None   # seconds: the plan's own evidence-derived estimate, used only when no receipt history prices the row
    rand_roms: tuple = ()      # randomized-ROM files (in SLINK_GEN3_RAND_ROMS) the row needs; absent => the pass BLOCKS

    def command(self):
        return shlex.join(["python" if a == PY else a for a in self.argv])


# ---------------------------------------------------------------------------
# the plan (runbook §1-§11, in order)
# ---------------------------------------------------------------------------

FALLBACK_DUO_BUDGET = 4 * 3600


def duo_budget(scenario, game):
    """The runner's backstop for one duo row: e2e_duo's own per-attempt timeout x its attempt
    limit, plus 15 min of boot/teardown. e2e_duo enforces the real bound itself; this only has to
    be above it. Read lazily from this repo's copy -- if that copy does not import (another worker
    mid-edit), a flat 4 h backstop keeps --dry-run and the plan usable."""
    try:
        import e2e_duo
        limit = e2e_duo.scenario_attempt_limit(scenario, e2e_duo.GAMES[game]["game"])
        return e2e_duo.SCENARIOS[scenario]["timeout"] * limit + 900
    except Exception:
        return FALLBACK_DUO_BUDGET


def _duo(scenario, game, item, lane):
    return Row(f"{scenario}_{ORIENT[game]}", item,
               [PY, "tools/e2e_duo.py", "--game", game, "--scenario", scenario], lane,
               duo_budget(scenario, game))


def build_plan(cut, lane, master):
    cut8 = cut[:8]
    rows = []
    # §1.1 probe states, §1.2 tutorial states (the bw rows' SLINK_BW_HASHES is hashed from these
    # by gen3_bw_hashes.py, which gen3_probe_receipt.py runs before each probe row)
    for title in ("firered", "leafgreen"):
        # trainer = slink_pretrainer/slink_prefaint, which the checkpoint probe's battle_input_trainer
        # and battle_faint_prompt rows read; the dress rehearsal at 157e1ef7 only passed on stale
        # copies left in lane 1, and lane 2 at c0f6101b had none ("state missing")
        for kind in ("town", "battle", "trainer"):
            out = f"{lane}/patch/build/gen3_probe_states_c4p2/{title}"
            rows.append(Row(f"states_{title}_{kind}", "§1.1 build",
                            [PY, "tools/mkstates_gen3.py", "--title", title, "--kind", kind,
                             "--out-dir", out, "--rom", STAGED[title]], lane,
                            1200 if kind == "trainer" else 600,
                            outputs_dir=out))
    for title in ("firered", "leafgreen"):
        out = f"{lane}/patch/build/gen3_probe_states/{title}"
        rows.append(Row(f"tutorials_{title}", "§1.2 build",
                        [PY, "tools/mkstates_gen3_tutorials.py", "--title", title,
                         "--out-dir", out], lane, 900, outputs_dir=out))
    # §2 item 1 + §3 item 2 (faint_cmd is §2's row; whiteout moves to §4, linked_faint_active to §5)
    for s in ("faint_cmd_gen3", "link_gen3", "boxsync_gen3", "reconnect_gen3", "deadzone_gen3"):
        rows.append(_duo(s, "gen3_frlg", "§2-3 item1-2", lane))
    # §4 item 2a, both orientations
    for s in ("whiteout_gen3", "center_controls_gen3"):
        for game in ("gen3_frlg", "gen3_lgfr"):
            rows.append(_duo(s, game, "§4 item2a", lane))
    # §5 item 2b: mechanism P+H (owner rulings 15-16/19/21) supersedes the T2/A2 hold plan
    # (G4_request_draft.md, 4aaaee7e): A1, A2 and the new whiteout/trainer rows, both orientations
    for s in ("linked_faint_active_gen3", "active_end_gen3", "linked_faint_active_whiteout_gen3",
              "linked_faint_active_trainer_gen3"):
        for game in ("gen3_frlg", "gen3_lgfr"):
            rows.append(_duo(s, game, "§5 item2b", lane))
    # §6 item 3: the full FRLG checkpoint probe (base + bw rows) via the receipt wrapper
    for title, short in (("firered", "fr"), ("leafgreen", "lg")):
        rows.append(Row(f"checkpoint_{title}", "§6 item3",
                        [PY, "tools/gen3_probe_receipt.py", "--title", title, "--lane", lane,
                         "--out", f"checkpoint_{short}_clean_{cut8}.txt"], REPO, 1800))
    # §7 §3.2 save rows (rows 3/6 ride §4's center_controls runs)
    for game in ("gen3_frlg", "gen3_lgfr"):
        rows.append(_duo("save_then_write_gen3", game, "§7 save-rows", lane))
    # §8 item 4: cold-boot admission 8/8
    for title in ("firered", "leafgreen"):
        for scene in ("town", "battle"):
            for side in ("", "_b"):
                fx = f"{title}_party_{scene}{side}"
                # the ROOT dump name (copy_inputs puts the pinned dump in the lane root), so
                # stage_rom yields patch/build/gen3_Pokemon_... once, and the gamedb battery name
                # BizHawk files a clean dump under (PARTY_TITLES saveram); an already-staged
                # --rom was staged AGAIN as gen3_gen3_... and seeded under the wrong name
                rows.append(Row(f"bootcheck_{fx}", "§8 item4",
                                [PY, "tools/gen3_fixtures.py", "boot-check",
                                 "--rom", ROOT_DUMPS[title],
                                 "--fixture", f"tests/fixtures/gen3/{fx}.sav", "--title", title,
                                 "--saveram-name", ROOT_DUMPS[title][:-len(".gba")] + ".SaveRAM"],
                                lane, 600))
    # §9 item 5: the zip built FROM the cut, checked AT the cut, then booted
    rows += zip_rows(cut, lane, "firered")
    # §10 item 6: route-differential against master
    rows.append(Row("item6_route_diff", "§10 item6",
                    [PY, "tools/gen3_final_cut.py", "item6", "--branch", lane, "--master", master],
                    REPO, 5400, own_verdict=True))
    # §11: the release gate's source lanes, then its P1 hook-probe lane (the duo lane is the
    # rows above, run one scenario at a time instead of through its pytest wrapper)
    gcc = armgcc_bin(main_checkout())
    rows += [Row("release_gate_quick", "§11 gate",
                 [PY, "tools/verify_gen3_release.py", "--quick"], lane, 3600, emulator=False,
                 own_verdict=True, env={"SLINK_ARMGCC": gcc} if gcc else {}),
             Row("probe_gates", "§11 gate",
                 [PY, "-m", "pytest", "tests/live/test_gen3_probe_gates.py", "-q", "-p",
                  "no:randomly", "-rs"], lane, 3600, env={"SLINK_LIVE": "1"})]
    for r in rows:
        r.deps = row_deps(r)
    return rows


# ---------------------------------------------------------------------------
# --title rr: the G5 (Radical Red) final-cut plan (card G5-RUNNER-RR). Opt-in and separate from
# build_plan's FR/LG rows above -- --title defaults to "frlg" so the default plan is untouched.
# ---------------------------------------------------------------------------

def rr_scenarios():
    """The RR duo rows: every tools/e2e_duo.py SCENARIOS entry whose `games` includes gen3_rr,
    minus any owner-signed limit (ruling 20 excludes linked_faint_active_mega_gen3: no Mega Ring
    or stone holder is reachable by normal inputs early in RR). Derived from e2e_duo's own table
    -- never duplicated here -- so a new RR scenario there is picked up automatically."""
    import e2e_duo
    # Final-cut selects every applicable probe explicitly, including feature and recovery
    # rows excluded from e2e_duo's ordinary --scenario all sweep. Keep its applicability
    # rules (including not_yet), registry order, and the signed-limit guard below.
    names = [s for s in e2e_duo.SCENARIOS if e2e_duo.scenario_applies(s, "gen3_rr")]
    limited = {s for s in names if e2e_duo.SCENARIOS[s].get("signed_limit")}
    if limited - RR_SIGNED_LIMITS:
        raise RuntimeError(f"new RR signed limit(s) {sorted(limited - RR_SIGNED_LIMITS)}: "
                           "add the owner ruling to RR_SIGNED_LIMITS or run the row")
    return [s for s in names if s not in limited]


RR_SIGNED_LIMITS = {"linked_faint_active_mega_gen3"}   # ruling 20 (R5)


def zip_rows(cut, lane, title):
    """§9 item 5: the zip built FROM the cut, checked AT the cut, then booted on `title`."""
    cut8 = cut[:8]
    zip_path = f"{lane}/dist/SLink-player-g4-{cut8}.zip"
    # "" / "rr_" preserved exactly for firered/radical_red (byte-identical frlg/rr plans); a new
    # title (emerald, card E4b-FINALCUT) gets its own prefix so its zip rows never collide with
    # FR's or RR's receipts (OMP cx-f570e611's rule, generalised past the original two titles).
    tag = "§9 item5" if title == "firered" else "§9 item5 RR" if title == "radical_red" else \
        f"§9 item5 {title.upper()}"
    boot = [PY, "tools/gen3_final_cut.py", "zip-boot", "--zip", zip_path, "--lane", lane]
    if title != "firered":
        boot += ["--title", title]
    pre = "" if title == "firered" else "rr_" if title == "radical_red" else f"{title}_"
    return [Row(f"{pre}zip_build", tag,
                [PY, "tools/make_release.py", "--version", f"g4-{cut8}", "--out", f"{lane}/dist",
                 "--skip-generators"], lane, 600, emulator=False),
            Row(f"{pre}zip_check", tag,
                [PY, "tools/check_release_zip.py", zip_path, "--rev", cut], lane, 300,
                emulator=False),
            Row(f"zip_boot_{title.replace('_', '')}" + ("_refused" if title in ZIP_BOOT_REFUSED else ""),
                tag, boot, REPO, 600)]


def build_plan_rr(cut, lane, master):
    """The G5 final-cut pass: the RR duo rows (docs/gen3/PLAN.md §14 P5), the RR opcode gates
    (rr_gates_live_06724759_2026-09-24.txt's SLINK_LIVE=1 pytest tests/live/test_lua_gates.py),
    and the release zip built, checked and booted on the RR companion build (zip_rows, ZIP_BOOT).
    `master` is accepted for CLI-signature parity with build_plan but unused (no item6 row)."""
    del master
    import e2e_duo
    rows = []
    for s in rr_scenarios():
        row = _duo(s, "gen3_rr", "§14 P5 RR duo", lane)
        cfg = e2e_duo.SCENARIOS[s]
        if cfg.get("journal_lock_probe") or cfg.get("rr_reset_trade"):
            row.argv.append("--keep-data")
        if cfg.get("journal_lock_probe"):
            # The contention oracle intentionally locks the install-root journal; its
            # prerequisite requires an exact private lane and a distinct state directory.
            row.env.update(SLINK_JOURNAL_PROBE_ROOT=os.path.realpath(lane),
                           SLINK_STATE_DIR=os.path.realpath(os.path.join(lane, "patch", "build",
                                                                      "rr_journal_probe_states")))
        rows.append(row)
    rows.append(Row("rr_opcode_gates", "G5-GATES-LIVE",
                    [PY, "-m", "pytest", "tests/live/test_lua_gates.py", "-q", "-p", "no:randomly",
                     "-rs"], lane, 3600, env={"SLINK_LIVE": "1"},
                    # the suite owns its skip policy (12 deferred gates skip by design; ported
                    # gates never skip): 26 passed / 12 skipped is its PASS (OMP cx-42592031)
                    own_verdict=True))
    rows += zip_rows(cut, lane, "radical_red")
    for r in rows:
        r.deps = row_deps(r)
    return rows


# ---------------------------------------------------------------------------
# --title emerald: the E4b final-cut plan (card E4b-FINALCUT, docs/gen3_emerald/PLAN.md §5 E4b).
# Opt-in and separate from build_plan/build_plan_rr's rows -- --title defaults to "frlg" so the
# default plan is untouched. docs/gen3/G4_final_cut_runbook.md §13 is this plan's own source.
# ---------------------------------------------------------------------------

# The seven scenarios E4-DUO/E4c wired for gen3_emerald (E<->E). Named here, not derived from
# tools/e2e_duo.py (unlike rr_scenarios()): that file's gen3_emerald wiring is owned by the
# parallel worktree em-legs (card E4-DUO), and this list must stay stable regardless of when that
# lands. duo_budget() still reads e2e_duo lazily per row and falls back to FALLBACK_DUO_BUDGET if
# the game isn't registered there yet. whiteout_gen3 joined at E4c with Emerald's own receipt:
# DoWhiteOut lands outdoors at lastHealLocation Oldale (6,17), healed (LANDING_STATE /
# WRITE_AT_LANDING / the START-menu control), not FR's Center landing.
EMERALD_DUO_SCENARIOS = ("faint_cmd_gen3", "reconnect_gen3", "deadzone_gen3", "link_gen3",
                         "boxsync_gen3", "linked_faint_active_gen3", "whiteout_gen3")


def build_plan_emerald(cut, lane, master):
    """The E4b final-cut pass: the SOURCE-lane generator checks (tools/gen_gen3_profile.py,
    tools/gen_gen3_write_checkpoint.py, tools/gen_area_map.py --game emerald), the Emerald unit
    suite, the §1-equivalent probe-state builds, the full checkpoint probe, the seven duo
    scenarios, the cold-boot admission matrix, the Emerald slice of the live probe-gates suite,
    the shadow-negatives manifest check, and the release zip built, checked and booted on Emerald
    (zip_rows, ZIP_BOOT["emerald"]).

    checkpoint_emerald (card E4b-CKPT) runs tools/gen3_probe_receipt.py --title emerald with no
    --rows: that tool's build_env() now picks the checkpoint pack by title (CHECKPOINT_PACK in
    tools/gen3_probe_receipt.py) instead of always reading FRLG's, and --title emerald's default
    is "no restriction" rather than BASE_ROWS+BW_ROWS, since Emerald has no oldman/pokedude
    tutorial states for the bw_* rows and its pack's own artifacts table already decides what is
    admitted (lua/tests/probe_gen3_checkpoint.lua's P.planned() treats a nil/empty
    SLINK_CHECKPOINT_ROWS as "everything the pack admits") -- exactly what the E2 evidence
    (docs/gen3_emerald/probes/checkpoint_emerald_battle_2026-09-26.txt, taken through the ad hoc
    scratch driver C:/slink-wt/emerald-e2/run_probe.py) actually relied on: that script never set
    SLINK_CHECKPOINT_ROWS either. The launch mechanism itself stays run_gate.py (this tool's
    existing, uniform path for every title), not the scratch driver's
    gen3_fixtures._prepare_run/_launch: the probe loads a specific .State file for every phase
    immediately, so the pre-loadstate SaveRAM content run_gate.py leaves unmanaged is no more a
    risk for Emerald than it already is for the FR/LG checkpoint rows sharing this same path.
    `master` is accepted for CLI-signature parity with build_plan but unused (no item6 row)."""
    del master
    cut8 = cut[:8]
    rows = [
        Row("profile_generated_check", "§13.1 source",
            [PY, "tools/gen_gen3_profile.py", "--check"], lane, 120, emulator=False),
        Row("write_checkpoint_generated_check", "§13.1 source",
            [PY, "tools/gen_gen3_write_checkpoint.py", "--check"], lane, 120, emulator=False),
        Row("area_map_generated_emerald", "§13.1 source",
            [PY, "tools/gen_area_map.py", "--game", "emerald", "--check"], lane, 120,
            emulator=False),
        Row("unit_emerald", "§13.1 source",
            [PY, "-m", "pytest", *EMERALD_UNIT_FILES, "-q", "-p", "no:randomly", "-rs"], lane, 900,
            emulator=False),
    ]
    # §13.2: the probe-state builds a future checkpoint row would need. town/battle/trainer are
    # the only EMERALD_KINDS entries with both a committed *_party-equivalent fixture and scripted
    # normal-input coverage in tools/mkstates_gen3.py; pc/lowhp/badges/evolve/poison/gift are E2's
    # own SYNTH play-leg seeds (tools/gen3_fixtures.py EMERALD_KINDS), not this builder's inputs.
    for kind in ("town", "battle", "trainer"):
        out = f"{lane}/patch/build/gen3_probe_states_c4p2/emerald"
        rows.append(Row(f"states_emerald_{kind}", "§13.2 build",
                        [PY, "tools/mkstates_gen3.py", "--title", "emerald", "--kind", kind,
                         "--out-dir", out, "--rom", STAGED["emerald"]], lane,
                        1200 if kind == "trainer" else 600, outputs_dir=out))
    # §13.2b: the full checkpoint probe (card E4b-CKPT) -- no --rows, so the pack's own artifacts
    # table decides what runs, same as the E2 scratch driver's invocation.
    rows.append(Row("checkpoint_emerald", "§13.2b checkpoint",
                    [PY, "tools/gen3_probe_receipt.py", "--title", "emerald", "--lane", lane,
                     "--out", f"checkpoint_emerald_clean_{cut8}.txt"], REPO, 1800))
    # §13.3: the seven duo scenarios, Emerald vs itself (the parallel worktree em-legs owns
    # tools/e2e_duo.py's gen3_emerald wiring -- named here by string, never imported from there).
    for s in EMERALD_DUO_SCENARIOS:
        rows.append(_duo(s, "gen3_emerald", "§13.3 duo", lane))
    # §13.4: cold-boot admission, mirroring §8's FR/LG matrix on Emerald's own fixture names
    # (tests/fixtures/gen3/emerald_{town,battle}[_b].sav -- no "_party_" infix, unlike FR/LG).
    for scene in ("town", "battle"):
        for side in ("", "_b"):
            fx = f"emerald_{scene}{side}"
            rows.append(Row(f"bootcheck_{fx}", "§13.4 item4",
                            [PY, "tools/gen3_fixtures.py", "boot-check",
                             "--rom", ROOT_DUMPS["emerald"],
                             "--fixture", f"tests/fixtures/gen3/{fx}.sav", "--title", "emerald",
                             "--saveram-name", EMERALD_SAVERAM],
                            lane, 600))
    # §13.5: the Emerald-only slice of the live probe-gates suite (probe_hooks_emerald,
    # test_gen3_emerald_frameend_census -- tests/live/test_gen3_probe_gates.py) and the
    # shadow-negatives manifest check against the committed observer receipts
    # (docs/gen3_emerald/negatives_manifest.json, tools/gen3_shadow_negatives.py). NOTE:
    # tests/live/test_gen3_shadow_gates.py, named in docs/gen3_emerald/PLAN.md's E2 row, does not
    # exist in this tree -- no live pytest wrapper drives shadow_run.lua yet; only this manifest
    # check against receipts already taken and committed under docs/gen3_emerald/probes/.
    rows.append(Row("probe_gates_emerald", "§13.5 probe",
                    [PY, "-m", "pytest", "tests/live/test_gen3_probe_gates.py", "-q", "-p",
                     "no:randomly", "-rs", "-k", "emerald"], lane, 3600, env={"SLINK_LIVE": "1"}))
    rows.append(Row("shadow_negatives_emerald", "§13.5 probe",
                    [PY, "tools/gen3_shadow_negatives.py",
                     "docs/gen3_emerald/negatives_manifest.json"], lane, 120, emulator=False))
    # §13.6: the release zip built FROM the cut, checked AT the cut, then booted on Emerald
    # (EG4/ruling 24 landed, so this boots for real now; zip_boot()'s own precondition check,
    # emerald_admission_blocker, only still returns BLOCKED-EG4 -- a real FAIL, no longer an
    # allowed skip -- for a zip built before that signature).
    rows += zip_rows(cut, lane, "emerald")
    for r in rows:
        r.deps = row_deps(r)
    return rows


def expansion_env(lane):
    return {"SLINK_EXPANSION_SRC": os.path.join(lane, ".cache", "expansion-src"),
            "SLINK_EXPANSION_ARTIFACTS": os.path.join(lane, ".cache", "expansion-output", "reference"),
            "SLINK_EXPANSION_PROBE_OBJECT": os.path.join(lane, ".cache", "x1-probe", "probe.o")}


def build_plan_exp(cut, lane, master):
    """XG3's test-only plan. Every applicable registry entry, including explicit-only probes.

    Reuse Emerald's applicable source checks with build-specific flags. Its vanilla state,
    checkpoint and boot-check tools do not accept this title, so they are not mislabeled as
    expansion coverage. Future expansion duos automatically join; no unsigned limit vanishes.
    """
    del master
    import e2e_duo
    names = [s for s in e2e_duo.SCENARIOS if e2e_duo.scenario_applies(s, "gen3_exp")]
    if not names:
        raise RuntimeError("expansion registry has no applicable duo rows")
    limited = [s for s in names if e2e_duo.SCENARIOS[s].get("signed_limit")]
    if limited:
        raise RuntimeError(f"new expansion signed limit(s) {limited}: obtain an explicit disposition")
    build_flags = ["--expansion", "28877d73", "--check"]
    rows = [Row(f"{name}_exp", "XG3 SOURCE", [PY, f"tools/{tool}", *flags], lane, 300,
                emulator=False) for name, tool, flags in (
        ("profile_generated_check", "gen_gen3_profile.py", build_flags),
        ("write_checkpoint_generated_check", "gen_gen3_write_checkpoint.py", build_flags),
        ("engine_signals_generated_check", "gen_gen3_engine_signals.py", build_flags),
        ("area_map_generated_check", "gen_area_map.py",
         ["--game", "emerald", *build_flags, "--source", expansion_env(lane)["SLINK_EXPANSION_SRC"]]),
        ("gift_census_check", "gen_gen3_exp_gifts.py",
         ["--check", "--src", expansion_env(lane)["SLINK_EXPANSION_SRC"]]),
        # verify_gen3_exp_wild has no source CLI; its canonical source is the lane's
        # .cache/expansion-src junction, checked against the lock around every row.
        ("wild_rom_check", "verify_gen3_exp_wild.py", ["--check"]),
    )]
    # Same semantic receipt checker as Emerald; raw PC-window liveness and positive
    # siblings are separately bound by the expansion's observer tests/manifest.
    rows.append(Row("shadow_negatives_exp", "XG3 SOURCE", [PY, "tools/gen3_shadow_negatives.py",
                    "docs/gen3_exp/negatives_manifest.json"], lane, 120, emulator=False))
    rows.append(Row("unit_exp", "XG3 MODEL", [PY, "-m", "pytest", *EXPANSION_UNIT_FILES,
                    "-q", "-p", "no:randomly", "-rs"], lane, 1800, emulator=False))
    rows += [_duo(s, "gen3_exp", "XG3 production duo", lane) for s in names]
    rows += zip_rows(cut, lane, "exp")
    for row in rows:
        row.cwd = lane  # even the boot helper must execute the frozen lane's runner
        row.env.update(expansion_env(lane))
        row.deps = None  # all expansion rows RUN; no historical qualification is carried
    return rows


# ---------------------------------------------------------------------------
# --title frlgc: the PATCHED (companion) FR/LG/Emerald re-cut. Opt-in beside frlg/rr/emerald/exp (whose plans are untouched).
# Owner decision: re-run the FR/LG/Emerald final cut on the companion ROMs, adding the clause / ball-gate / shiny rows, as ONE cut.
# Every row ALWAYS RUNs (companion_row_id: its own identity, never carried or cached) and runs the frozen lane's own code. The
# duo rows are derived from the frlg and emerald plans, so a duo row added there joins the re-cut automatically.
# NOT in it, on purpose: states_*/tutorials_*/checkpoint_* (tools/gen3_probe_receipt.py is wired to the clean STAGED dump and has no
# ROM argument; a companion build of its states would be a new tool -- the duo rows are battery-boot, they use no savestate),
# probe_gates (tests/live/test_gen3_probe_gates.py loads the clean dumps / the RR build) and item6_route_diff (a branch-vs-master
# route diff on the Gen 1/2 side, already settled by the clean cut).
# ---------------------------------------------------------------------------

# the new-rule rows (explicit_only in e2e_duo, so never reached by `--scenario all`); each on FR-as-A, LG-as-A and Emerald.
# species_family_gen3 is the species clause's family variant: kept in the re-cut, droppable by deleting its name here.
FRLGC_RULE_SCENARIOS = ("species_clause_gen3", "gender_clause_gen3", "type_clause_gen3", "ball_gate_gen3",
                        "shiny_bonus_gen3", "species_family_gen3")


def _companion_duo(row):
    """A clean duo row as its companion twin: --gen3-companion, its own id, always RUN."""
    twin = Row(companion_row_id(row.id), "FRLGC-DUO", [*row.argv, "--gen3-companion"], row.cwd, row.budget)
    twin.deps = None
    return twin


# ---------------------------------------------------------------------------
# --title frlgc-rand: the randomized-COMPANION rows + the clean zip-boot refusal proofs (opt-in; frlgc keeps its 65 rows)
# ---------------------------------------------------------------------------

RAND_ROW_PREFIX = "frlgcr_"
RAND_LANE_ID = "frlgcr"        # e2e_duo --lane: its stage dir is patch/build/rand_<id> in the lane
RAND_ROW_EST_NOTE = (
    "est = launches x 9 s + the prepare step + the row's own tail, from retained receipts (docs/gen3/probes/fc_*): a 3-launch "
    "reconnect_gen3 row takes 26 s (fr) / 25.5 s (em) median => ~9 s per emulator launch; the R4/E-RAND physical receipts for "
    "link_gen3_rand (orchestrate 28.8 s + results 1.8 s), trainer_panel_gen3_rand (4.7 + 2.4 s) and admit_randomized_* (server 0.6 s, "
    "orchestrate 4-5 s after the control legs); _prepare_gen3_rand (overlay + table facts) measured 1.7-3.3 s per row; zip_boot_* "
    "33.5/34 s median, zip_build 2 s, zip_check 6 s. admit_randomized_* now has 8 launches (rules_changed, equivalent+companion "
    "partner, wrong_rom, initial a, mixed_kind, clean_refused, initial b).")
# (scenario, game key, row-id suffix, wall-clock estimate in seconds)
RAND_DUO_ROWS = (("admit_randomized_frlg", "gen3_frlg", "", 90),
                 ("admit_randomized_emerald", "gen3_emerald", "", 90),
                 ("link_gen3_rand", "gen3_frlg", "_frlg", 60),
                 ("link_gen3_rand", "gen3_emerald", "_emerald", 60),
                 ("trainer_panel_gen3_rand", "gen3_frlg", "_frlg", 45),
                 ("trainer_panel_gen3_rand", "gen3_emerald", "_emerald", 45))
RAND_SOURCE_EST, RAND_ZIP_EST = 3, {"zip_build": 2, "zip_check": 6, "zip_boot_firered_refused": 34, "zip_boot_emerald_refused": 34}


def rand_rom_dir():
    """SLINK_GEN3_RAND_ROMS (the randomized ROMs are never committed), or None when unset."""
    return os.environ.get("SLINK_GEN3_RAND_ROMS") or None


def rand_roms_for(scenario, game):
    """The randomized ROM files e2e_duo._prepare_gen3_rand reads for this row, derived from e2e_duo's own tables: each side's
    `<Label>_allowed[_b].gba`, plus the A title's `<Label>_widest.gba` for the admission rows."""
    import e2e_duo
    label = {"firered": "FireRed", "leafgreen": "LeafGreen", "emerald": "Emerald"}
    files = []
    for side in ("a", "b"):
        title = e2e_duo.GAMES[game]["sides"][side][0]
        files.append(f"{label[title]}_allowed{'_b' if title == 'emerald' and side == 'b' else ''}.gba")
    if scenario in e2e_duo.GEN3_RAND_ADMISSION:
        files.append(f"{label[e2e_duo.GAMES[game]['sides']['a'][0]]}_widest.gba")
    return tuple(files)


def rand_row_scenario(rid):
    """(scenario, game) of a randomized duo row id, or None."""
    for scenario, game, suffix, _est in RAND_DUO_ROWS:
        if rid == RAND_ROW_PREFIX + scenario + suffix:
            return scenario, game
    return None


def rand_row_overlays(scenario, game):
    """The overlay ROM file names _prepare_gen3_rand stages for this scenario, derived from its sides and kind: A's and B's
    randomized+companion ROM, and (admission only) A's clean-equivalent and rule-changed controls."""
    import e2e_duo
    sides = e2e_duo.GAMES[game]["sides"]
    names = [f"a_{sides['a'][0]}.gba", f"b_{sides['b'][0]}.gba"]
    if scenario in e2e_duo.GEN3_RAND_ADMISSION:
        names += [f"equivalent_{sides['a'][0]}.gba", f"forbidden_{sides['a'][0]}.gba"]
    return tuple(names)


def _row_zip_path(row):
    """The release zip a zip build / check / boot row reads or writes, from its own argv; None when it names none."""
    argv = list(getattr(row, "argv", None) or ())
    if "--zip" in argv:
        return argv[argv.index("--zip") + 1]
    if "tools/check_release_zip.py" in argv:
        return argv[argv.index("tools/check_release_zip.py") + 1]
    if "tools/make_release.py" in argv and "--out" in argv and "--version" in argv:
        return f"{argv[argv.index('--out') + 1]}/SLink-player-{argv[argv.index('--version') + 1]}.zip"
    return None


def rand_row_titles(rid):
    """(clean titles the row stages/overlays, randomized-ROM files) by row id, or ((), ())."""
    for scenario, game, suffix, _est in RAND_DUO_ROWS:
        base = RAND_ROW_PREFIX + (scenario if not suffix else f"{scenario}{suffix}")
        if rid == base:
            import e2e_duo
            return (tuple(dict.fromkeys(e2e_duo.GAMES[game]["sides"][s][0] for s in "ab")),
                    rand_roms_for(scenario, game))
    return (), ()


def is_rand_row(row_id):
    return row_id.startswith(RAND_ROW_PREFIX)


def rand_row_inputs(rid, lane, row=None):
    """{key: path} of everything a randomized-companion row consumes that git does not see: the clean dump and the staged
    companion of each title it uses, each randomized ROM, and the overlay ROMs _prepare_gen3_rand stages in the lane
    (recorded AFTER the run, so the receipt names the exact companion-randomized bytes that booted)."""
    out = {}
    titles, files = rand_row_titles(rid)
    m = re.fullmatch(RAND_ROW_PREFIX + r"source_(firered|leafgreen|emerald)", rid)
    if m:
        titles = (m[1],)
    zip_path = _row_zip_path(row) if rid.startswith(RAND_ROW_PREFIX + "zip_") else None
    z = re.fullmatch(RAND_ROW_PREFIX + r"zip_boot_(firered|emerald)_refused", rid)
    if z:
        return {f"rom:{z[1]}": os.path.join(lane, STAGED[z[1]]), **({"zip:dist": zip_path} if zip_path else {})}
    if zip_path:        # zip_build writes, zip_check reads: the release zip is the input a swapped file would change
        return {"zip:dist": zip_path}
    for t in titles:
        out[f"rom:{t}"] = os.path.join(lane, STAGED[t])
        out[f"rom:{t}_companion"] = os.path.join(lane, COMPANION_ROMS[t])
    rand_dir = rand_rom_dir() or "<SLINK_GEN3_RAND_ROMS unset>"
    for f in files:
        out[f"rand:{f}"] = os.path.join(rand_dir, f)
    which = rand_row_scenario(rid)
    if which:
        stage = os.path.join(lane, "patch", "build", f"rand_{RAND_LANE_ID}")
        for name in rand_row_overlays(*which):
            out[f"overlay:{name[:-len('.gba')]}"] = os.path.join(stage, name)
    return out


def rand_blockers(rows):
    """{row id: why} for every row whose randomized ROM input is absent -- a BLOCK, never a silent skip."""
    out = {}
    root = rand_rom_dir()
    for r in rows:
        if not r.rand_roms:
            continue
        if root is None:
            out[r.id] = "BLOCKED: SLINK_GEN3_RAND_ROMS is not set (the randomized ROMs are never committed)"
            continue
        missing = [f for f in r.rand_roms if not os.path.isfile(os.path.join(root, f))]
        if missing:
            out[r.id] = f"BLOCKED: randomized ROM absent from {root}: {', '.join(missing)}"
    return out


def rand_estimate_lines(rows, est, shards=3):
    """Dry-run lines: each row's estimate, the serial total and the `shards`-way (longest-first, chains atomic) total."""
    secs = {r.id: est.get(r.id) or r.est or r.budget for r in rows}
    lines = [f"# estimate (frlgc-rand): {RAND_ROW_EST_NOTE}"]
    lines += [f"#   {r.id:<46} ~{secs[r.id]:.0f}s" for r in rows]
    lines.append(f"# serial total ~{sum(secs.values()):.0f}s ({sum(secs.values()) / 60:.1f} min)")
    loads = [sum(secs[r.id] for r in part) for part in shard_rows(rows, shards, secs)]
    lines.append(f"# {shards} shards ~{max(loads):.0f}s ({max(loads) / 60:.1f} min) longest, loads "
                 + ", ".join(f"{x:.0f}s" for x in loads))
    return lines


def build_plan_frlgc_rand(cut, lane, master):
    """13 rows, ids frlgcr_*, all always RUN: per-title companion-check (emulator-free), zip build/check, the six randomized-companion
    duo rows (admission with its pair / wrong_rom / rules_changed / equivalent_pair / mixed_kind / clean_refused legs, link,
    nearby-trainer panel; FR+LG and Emerald), and the two clean zip-boot refusal proofs."""
    import e2e_duo
    del master
    rows = [Row(RAND_ROW_PREFIX + f"source_{t}", "FRLGCR-SOURCE",
                [PY, "tools/gen3_final_cut.py", "companion-check", "--lane", lane, "--title", t], lane, 300,
                emulator=False, est=RAND_SOURCE_EST) for t in GEN3_COMPANION_TITLES]
    build, check, fr_boot = zip_rows(cut, lane, "firered")
    em_boot = zip_rows(cut, lane, "emerald")[2]
    for row in (build, check, fr_boot, em_boot):
        row.id = RAND_ROW_PREFIX + row.id
        row.item = "FRLGCR-ZIP"
        row.est = RAND_ZIP_EST[row.id[len(RAND_ROW_PREFIX):]]
    zips = [build, check]
    rand_dir = rand_rom_dir()
    for scenario, game, suffix, seconds in RAND_DUO_ROWS:
        if not e2e_duo.scenario_applies(scenario, game):
            raise RuntimeError(f"{scenario} does not apply to {game}")
        rows.append(Row(RAND_ROW_PREFIX + scenario + suffix, "FRLGCR-RAND",
                        [PY, "tools/e2e_duo.py", "--game", game, "--scenario", scenario, "--lane", RAND_LANE_ID], lane,
                        e2e_duo.SCENARIOS[scenario]["timeout"] * e2e_duo.scenario_attempt_limit(
                            scenario, e2e_duo.GAMES[game]["game"]) + 900,
                        env={"SLINK_GEN3_RAND_ROMS": rand_dir} if rand_dir else {}, est=seconds,
                        rand_roms=rand_roms_for(scenario, game)))
    rows += zips + [fr_boot, em_boot]
    for r in rows:
        r.cwd, r.deps = lane, None
    return rows


def build_plan_frlgc(cut, lane, master):
    """The patched re-cut: the staged-companion source check per title, the companion cold-boot admission matrix, every duo row
    of the frlg and emerald plans on the companion cartridges, the clause / ball-gate / shiny rows on FR, LG and Emerald, and the
    release zip built, checked and booted on each companion title. 65 rows."""
    import e2e_duo
    base = build_plan(cut, lane, master)
    emerald = build_plan_emerald(cut, lane, master)
    rows = [Row(companion_row_id(f"source_{t}"), "FRLGC-SOURCE",
                [PY, "tools/gen3_final_cut.py", "companion-check", "--lane", lane, "--title", t], lane, 300,
                emulator=False) for t in GEN3_COMPANION_TITLES]
    # cold-boot admission on the patched ROM (tools/gen3_fixtures.py boot-check takes --rom; stage_rom files the copy as
    # gen3_<stem>.gba, so FR/LG derive "gen3 slink <Title>.SaveRAM"; Emerald's override defaults to the clean dump's name)
    for title in ("firered", "leafgreen"):
        for scene in ("town", "battle"):
            for side in ("", "_b"):
                fx = f"{title}_party_{scene}{side}"
                rows.append(Row(companion_row_id(f"bootcheck_{fx}"), "FRLGC-BOOT",
                                [PY, "tools/gen3_fixtures.py", "boot-check", "--rom", COMPANION_ROMS[title],
                                 "--fixture", f"tests/fixtures/gen3/{fx}.sav", "--title", title, "--companion"], lane, 600))
    battery = "gen3 " + os.path.basename(COMPANION_ROMS["emerald"])[:-len(".gba")].replace("_", " ") + ".SaveRAM"
    for scene in ("town", "battle"):
        for side in ("", "_b"):
            fx = f"emerald_{scene}{side}"
            rows.append(Row(companion_row_id(f"bootcheck_{fx}"), "FRLGC-BOOT",
                            [PY, "tools/gen3_fixtures.py", "boot-check", "--rom", COMPANION_ROMS["emerald"],
                             "--fixture", f"tests/fixtures/gen3/{fx}.sav", "--title", "emerald",
                             "--saveram-name", battery, "--companion"], lane, 600))
    rows += [_companion_duo(r) for r in base if r.id.endswith(("_fr_as_a", "_lg_as_a"))]
    rows += [_companion_duo(r) for r in emerald if r.id.endswith("_em_as_a")]
    for scenario in FRLGC_RULE_SCENARIOS:
        if e2e_duo.SCENARIOS[scenario].get("signed_limit"):
            raise RuntimeError(f"{scenario}: a signed limit needs an explicit disposition in the frlgc plan")
        for game in ("gen3_frlg", "gen3_lgfr", "gen3_emerald"):
            if not e2e_duo.scenario_applies(scenario, game):
                raise RuntimeError(f"{scenario} does not apply to {game}")
            rows.append(_companion_duo(_duo(scenario, game, "FRLGC-RULES", lane)))
    build, check, _clean_boot = zip_rows(cut, lane, "firered")
    zip_path = f"{lane}/dist/SLink-player-g4-{cut[:8]}.zip"
    for row in (build, check):
        row.id, row.item = companion_row_id(row.id), "FRLGC-ZIP"
    rows += [build, check] + [
        Row(companion_row_id(f"zip_boot_{t}"), "FRLGC-ZIP",
            [PY, "tools/gen3_final_cut.py", "zip-boot", "--zip", zip_path, "--lane", lane, "--title", f"{t}_companion"],
            lane, 600) for t in GEN3_COMPANION_TITLES]
    gate = next(r for r in base if r.id == "release_gate_quick")    # emulator-free source lanes; builds no companion
    rows.append(Row(companion_row_id(gate.id), "FRLGC-GATE", gate.argv, lane, gate.budget, env=dict(gate.env),
                    emulator=False, own_verdict=True))
    for r in rows:
        r.cwd, r.deps = lane, None    # even the boot helper must execute the frozen lane's runner; every row RUNs
    return rows


def select_rows(rows, spec):
    """--rows: comma list of row-id globs or item tags (e.g. 'item2b'); runbook order kept."""
    if not spec:
        return rows
    pats = [p.strip() for p in spec.split(",") if p.strip()]
    picked = [r for r in rows
              if any(fnmatch.fnmatch(r.id, p) or p in r.item.split() for p in pats)]
    unknown = [p for p in pats
               if not any(fnmatch.fnmatch(r.id, p) or p in r.item.split() for r in rows)]
    if unknown:
        raise SystemExit(f"--rows: nothing matches {', '.join(unknown)}")
    return picked


# ---------------------------------------------------------------------------
# verdicts: the SKIP policy and the retry classifier
# ---------------------------------------------------------------------------

_TIMEOUT_SIGNS = ("timed out after", "[gate] TIMEOUT after", "[final_cut] BUDGET EXCEEDED")
# a side or oracle that reached a verdict failed: the run was not starved, it was wrong
_REAL_SIGNS = ("RESULT: FAIL", "AssertionError", "a client finished before", "BOOT-CHECK FAIL")


def classify_failure(output, budget_killed=False):
    """'contention' only for a timeout (a harness wait or the runner's own budget) with no
    failing verdict anywhere in the output; everything else is 'real' and is never retried.
    ponytail: CPU load is recorded in the receipt, not gated on -- the shared machine sits at
    90%+ on good runs too; gate on it if a real failure ever gets classified as contention."""
    text = output or ""
    timed_out = budget_killed or any(s in text for s in _TIMEOUT_SIGNS)
    # a crash is real too, unless the traceback is the harness's own TimeoutError
    real = any(s in text for s in _REAL_SIGNS) or \
        ("Traceback (most recent call last)" in text and "TimeoutError" not in text)
    return "contention" if timed_out and not real else "real"


def detect_skip(rc, output):
    """True when the row skipped instead of running: e2e_duo's exit 3 / 'name: SKIP' summary
    line, or a pytest summary that counts a skip."""
    text = output or ""
    return (rc == 3 or bool(re.search(r"^\s+\S+: SKIP\b", text, re.M))
            # a pytest count, not e2e_duo's "SAVE_WITNESS_SHA256 ... saves=0 skipped (no_save)"
            or bool(re.search(r"(?:^|[\s,])\d+ skipped\b", text, re.M)))


def allowed_skip(row_id, output):
    """The ruling that excuses this row's SKIP, or None (then the SKIP is a failure)."""
    for pat, reason, ruling in ALLOWED_SKIPS:
        if fnmatch.fnmatch(row_id, pat) and reason in (output or ""):
            return ruling
    return None


def judge(row_id, rc, output, budget_killed=False, own_verdict=False):
    """(verdict, passed). PASS needs exit 0 with no skip; a SKIP passes only via ALLOWED_SKIPS."""
    if budget_killed:
        return "FAIL budget exceeded (runner killed its own process tree)", False
    if not own_verdict and detect_skip(rc, output):
        ruling = allowed_skip(row_id, output)
        if ruling and rc in (0, 3):
            return f"SKIP-ALLOWED {ruling}", True
        return "FAIL skipped, and ALLOWED_SKIPS does not excuse it", False
    if rc == 0:
        return "PASS", True
    return f"FAIL exit={rc}", False


def receipt_path(row_id, cut):
    return os.path.join(PROBES, f"fc_{row_id}_{cut[:8]}.txt")


def prior_verdict(row_id, cut, lane=None):
    """The verdict of this row's runner receipt at this exact cut, or None -- only a receipt that
    fc_check validates counts (a header-only or wrong-row file never resumes as a PASS)."""
    path = receipt_path(row_id, cut)
    if not os.path.isfile(path):
        return None
    hdr, ok, _why = fc_check(os.path.basename(path), _read(path), PROBES, lane=lane)
    return hdr["verdict"] if ok and hdr["row"] == row_id and hdr["cut"] == cut else None


# ---------------------------------------------------------------------------
# the lane: provision/verify, gitignored inputs
# ---------------------------------------------------------------------------

class LaneError(RuntimeError):
    pass


def _git(tree, *args, check=True):
    p = subprocess.run(["git", "-C", tree, *args], capture_output=True, text=True)
    if check and p.returncode:
        raise LaneError(f"git -C {tree} {' '.join(args)}: {p.stderr.strip() or p.returncode}")
    return p


def tracked_clean(tree):
    return _git(tree, "status", "--porcelain", "--untracked-files=no").stdout.strip() == ""


def head(tree):
    return _git(tree, "rev-parse", "HEAD").stdout.strip()


def main_checkout():
    common = _git(REPO, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return os.path.dirname(common)


def primary_revision():
    from slink_space import primary_branch

    try:
        branch = primary_branch(REPO)
    except RuntimeError as exc:
        raise LaneError(str(exc)) from exc
    return _git(REPO, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").stdout.strip()


def provision_plan(tree, rev, extra_pinned=None, extra_unpinned=None, base_inputs=True):
    """The commands provision() may run, for --dry-run. `extra_pinned`/`extra_unpinned` (--title
    emerald only: EMERALD_PINNED_INPUTS / EMERALD_UNPINNED_INPUTS) are appended so frlg/rr's
    printed plan is untouched at their default of None -- every existing call site keeps calling
    this with the same two positional arguments it always did."""
    pinned = {**(PINNED_INPUTS if base_inputs else {}), **(extra_pinned or {})}
    unpinned = (UNPINNED_INPUTS if base_inputs else []) + list(extra_unpinned or [])
    return [f"git worktree add --detach {tree} {rev}   (only if {tree} does not exist)",
            f"git -C {tree} update-index -q --really-refresh && git -C {tree} status --porcelain "
            f"--untracked-files=no   (must be empty, else abort)",
            f"git -C {tree} checkout --detach {rev}   (if HEAD differs)",
            f"git -C {tree} update-ref --no-deref HEAD {rev}   (fallback: the broken shared ref)",
            f"git -C {tree} read-tree {rev} && git -C {tree} checkout-index -a -f   (fallback)",
            f"pinned inputs (sha1 vs the tree's own pins; lane, then this worktree, then the main "
            f"checkout; no match aborts): {', '.join(pinned)}",
            f"unpinned inputs, copied only when missing: {', '.join(unpinned)}"]


def _refreshed_clean(tree):
    """tracked_clean after `git update-index -q --really-refresh`: a .gitattributes change (e.g.
    the LF pins of 17d190ce..f6400e74) can leave the index stat-only dirty; re-hashing every
    tracked file settles it, and only real content differences still count as dirty."""
    _git(tree, "update-index", "-q", "--really-refresh", check=False)
    return tracked_clean(tree)


def provision(tree, rev, root):
    """Put `tree` detached at `rev`, tracked-clean. A tree that is already tracked-dirty is never
    touched (someone else's work, or a broken lane): LaneError, and the pass aborts.

    The fallbacks are W3's (item6_route_diff_branch_2026-09-24.txt): the shared repo's broken ref
    refs/heads/codex/gen2-foundation (1) makes checkout die AFTER it updated index and tree, so
    HEAD is moved with update-ref; a tree that is still off afterwards is re-read from the commit."""
    sha = _git(root, "rev-parse", f"{rev}^{{commit}}").stdout.strip()
    if not os.path.isdir(tree):
        _git(root, "worktree", "add", "--detach", tree, sha, check=False)
        if not os.path.isdir(tree):
            raise LaneError(f"git worktree add could not create {tree}")
    elif not _refreshed_clean(tree):
        raise LaneError(f"{tree} is tracked-dirty before provisioning; refusing to touch it")
    elif head(tree) != sha:
        _git(tree, "checkout", "--detach", sha, check=False)
    if head(tree) != sha:
        _git(tree, "update-ref", "--no-deref", "HEAD", sha)
    if not _refreshed_clean(tree) or _git(tree, "diff", "--quiet", sha, check=False).returncode:
        _git(tree, "read-tree", sha)
        _git(tree, "checkout-index", "-a", "-f")
    if head(tree) != sha or not _refreshed_clean(tree):
        raise LaneError(f"{tree} is not a clean detached checkout of {sha} after provisioning")
    return sha


_HASHES = {}


def file_digest(path, algo="sha256"):
    """A file's hex digest, memoised on (path, size, mtime, algo); None when absent."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    key = (path, st.st_size, st.st_mtime_ns, algo)
    if key not in _HASHES:
        h = hashlib.new(algo)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _HASHES[key] = h.hexdigest()
    return _HASHES[key]


def rom_pins(tree, include_expansion=False, require_companions=False):
    """{pin key: digest} from the tree's OWN pin tables: tools/gen_gen3_write_checkpoint.py ROMS
    (sha1 of the clean dumps) and patch/dist/companion_pins.json ("rr"): the companion's exact sha1 and md5, its canonical sha1 and
    the earlier builds it lists as canonical-equal (version-masked identity, patch/tools/rom_identity.py).
    Missing tables raise LaneError (fail closed).
    patch/dist/gen3_companions.json also adds `<title>_companion[:md5|:canonical]` for firered/leafgreen/emerald; they are NOT in
    the default `need` set (the clean-ROM plans must not depend on them), `require_companions=True` (a companion re-cut) makes
    them mandatory: absent or malformed raises LaneError."""
    src = _read(os.path.join(tree, "tools", "gen_gen3_write_checkpoint.py"))
    pins = {}
    # each entry reads up to the next key only: an entry whose sha1 is not text (the RR companion's, taken from
    # patch/dist/companion_pins.json below) must not borrow the next entry's
    for title, kind, sha in re.findall(
            r'\("gen3_\w+", "(\w+)", "(\w+)"\):\s*\((?:(?!\("gen3_).)*?"([0-9a-f]{40})"\)', src, re.S):
        pins[title if kind == "clean" and title in STAGED else f"{title}_{kind}"] = sha
    # the companion's md5: patch/dist/companion_pins.json (server/patcher.py reads the same file); trees that predate it
    # still carry the literal in patcher.py TARGETS["rr"]
    pins_path = os.path.join(tree, "patch", "dist", "companion_pins.json")
    if os.path.exists(pins_path):
        try:
            row = (json.loads(_read(pins_path)).get("pins") or {}).get("rr") or {}
        except ValueError as exc:
            raise LaneError(f"{pins_path}: not JSON ({exc})") from exc
        if re.fullmatch(r"[0-9a-f]{32}", str(row.get("patched_md5", ""))):
            pins["radical_red_companion:md5"] = row["patched_md5"]
        # the exact cartridge: the one data source (tools/gen_gen3_write_checkpoint.py ROMS reads this same row, so its table no
        # longer carries the sha1 as text for the regex above to find)
        if re.fullmatch(r"[0-9a-f]{40}", str(row.get("rom_sha1", ""))):
            pins["radical_red_companion"] = row["rom_sha1"]
        # Version-masked identity (patch/tools/rom_identity.py): the canonical sibling of the exact pin, and the exact hashes of
        # earlier builds proven canonical-equal to it (a version stamp only). A listed hash is the same qualified build.
        if re.fullmatch(r"[0-9a-f]{40}", str(row.get("canonical_sha1", ""))):
            pins["radical_red_companion:canonical"] = row["canonical_sha1"]
        pins["radical_red_companion:equivalent_sha1s"] = tuple(
            h for h in row.get("equivalent_sha1s") or () if re.fullmatch(r"[0-9a-f]{40}", str(h)))
    else:
        md5 = re.search(r'"rr":\s*\{.*?"patched_md5":\s*"([0-9a-f]{32})"',
                        _read(os.path.join(tree, "server", "patcher.py")), re.S)
        if md5:
            pins["radical_red_companion:md5"] = md5[1]
    need = {"firered", "leafgreen", "radical_red_companion", "radical_red_companion:md5"}
    if not need <= set(pins):
        raise LaneError(f"{tree}: pin tables incomplete (have {sorted(pins)})")
    pins.update(gen3_companion_pins(tree, strict=require_companions))
    if include_expansion:
        pins["exp"] = expansion_rom_pin(tree)
    return pins


GEN3_COMPANION_TITLES = ("firered", "leafgreen", "emerald")


def gen3_companion_pins(tree, strict=False):
    """{<title>_companion: rom_sha1, <title>_companion:md5, <title>_companion:canonical} from patch/dist/gen3_companions.json
    (the one owner of these keys; RR's come from companion_pins.json). Lenient: absent file or a malformed title row adds nothing.
    strict: every title needs a valid sha1 + md5 + canonical sha1 + production flag, else LaneError."""
    path = os.path.join(tree, "patch", "dist", "gen3_companions.json")
    if not os.path.exists(path):
        if strict:
            raise LaneError(f"{path}: gen3_companions.json absent (companion pins required)")
        return {}
    return _companion_pins_of(_read(path), path, strict)


def gen3_companion_pins_at(cut):
    """The companion pins AS OF `cut` (git show <cut>:patch/dist/gen3_companions.json), lenient: {} when the cut or the file
    is unreadable, so companion evidence then fails closed ("no pin to compare") instead of borrowing another tree's pins."""
    shown = _git(REPO, "show", f"{cut}:patch/dist/gen3_companions.json", check=False)
    if shown.returncode:
        return {}
    try:
        return _companion_pins_of(shown.stdout, f"{cut[:8]}:patch/dist/gen3_companions.json", False)
    except LaneError:
        return {}


def _companion_pins_of(text, path, strict):
    try:
        titles = json.loads(text).get("titles") or {}
    except (ValueError, AttributeError) as exc:
        raise LaneError(f"{path}: not JSON ({exc})") from exc
    out = {}
    for title in GEN3_COMPANION_TITLES:
        row = titles.get(title)
        row = row if isinstance(row, dict) else {}
        got = {f"{title}_companion": row.get("rom_sha1"), f"{title}_companion:md5": row.get("rom_md5"),
               f"{title}_companion:canonical": row.get("canonical_sha1")}
        bad = [k for k, v in got.items()
               if not re.fullmatch(r"[0-9a-f]{32}" if k.endswith(":md5") else r"[0-9a-f]{40}", str(v))]
        if strict and (bad or row.get("production") is not True):
            raise LaneError(f"{path}: {title} companion pin missing or malformed ({bad or 'not production'})")
        out.update({k: v for k, v in got.items() if k not in bad})
    return out


def expansion_rom_pin(tree):
    try:
        profile = json.loads(_read(os.path.join(tree, EXPANSION_PACK, "profile.json")))
        sha = profile["source"]["rom_sha1"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LaneError("expansion profile ROM pin missing or malformed") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not sha.startswith("28877d73"):
        raise LaneError("expansion profile has no valid registered ROM pin")
    return sha


def _matches_pin(path, key, pins):
    sha1 = file_digest(path, "sha1")
    if sha1 in pins.get(f"{key}:equivalent_sha1s", ()):      # an earlier exact build proven canonical-equal: same qualified build
        return True
    return sha1 == pins[key] and \
        (f"{key}:md5" not in pins or file_digest(path, "md5") == pins[f"{key}:md5"])


def copy_inputs(tree, root, repo=None, pins=None, only=None, extra_pinned=None,
                extra_unpinned=None, base_inputs=True):
    """Bring the tree's gitignored inputs in line (see PINNED_INPUTS / UNPINNED_INPUTS).
    `only`: just these unpinned inputs, no pinned ones (item 6's master tree needs only the
    Gen 1/2 inputs, and an older master may not carry the Gen 3 pin tables at all).
    `extra_pinned`/`extra_unpinned` (--title emerald only: EMERALD_PINNED_INPUTS /
    EMERALD_UNPINNED_INPUTS): merged in on top of PINNED_INPUTS/UNPINNED_INPUTS; every other call
    site keeps its default of None, so frlg/rr's provisioning is unchanged."""
    repo = repo or REPO
    pins = {} if only is not None else (pins or rom_pins(tree))
    for dst_rel, (key, rels) in ({} if only is not None else
                                  {**(PINNED_INPUTS if base_inputs else {}),
                                   **(extra_pinned or {})}).items():
        dst = os.path.join(tree, dst_rel)
        if _matches_pin(dst, key, pins):
            continue
        cands = [os.path.join(base, rel) for base in (tree, repo, root) for rel in rels]
        src = next((c for c in cands if _matches_pin(c, key, pins)), None)
        if src is None:
            raise LaneError(f"{dst_rel}: no source matches pin {key} (sha1 {pins[key]}); "
                            f"checked {', '.join(cands)}")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    for rel in ((UNPINNED_INPUTS if base_inputs else []) + list(extra_unpinned or [])) \
            if only is None else only:
        dst = os.path.join(tree, rel)
        if os.path.exists(dst):
            continue                     # the lane's own copy is never overwritten
        src = next((os.path.join(b, rel) for b in (repo, root)
                    if os.path.exists(os.path.join(b, rel))), None)
        if src is None:
            raise LaneError(f"gitignored input missing from {repo} and {root}: {rel}")
        if rel.endswith("/"):
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)


def _link_dir(source, destination):
    """A junction (Windows) or symlink to `source` at `destination`; never a copy of hundreds of MB."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(os.path.realpath(source), destination)
    else:
        os.symlink(os.path.realpath(source), destination, target_is_directory=True)


def link_expansion_source(tree, root, repo=None):
    """Junction `tree`/.cache/expansion-src to the pinned expansion checkout (SLINK_EXPANSION_SRC, the tree's own, this worktree's or the
    main checkout's) after proving it is tracked-clean at the locked commit with the locked config headers; the source stays read-only
    and in place. Any missing or differing source raises LaneError. Returns the lock document."""
    repo = repo or REPO
    destination = os.path.join(tree, ".cache", "expansion-src")
    supplied = os.environ.get("SLINK_EXPANSION_SRC")
    source = supplied or (destination if os.path.isdir(destination) else next(
        (os.path.join(base, ".cache", "expansion-src") for base in (repo, root)
         if os.path.isdir(os.path.join(base, ".cache", "expansion-src"))), None))
    lock = json.loads(_read(os.path.join(tree, "data", "gen3_exp_sources.lock.json")))
    if not source or not os.path.isdir(source):
        raise LaneError("expansion source missing (SLINK_EXPANSION_SRC or .cache/expansion-src)")
    if head(source) != lock["source"]["commit"] or not tracked_clean(source):
        raise LaneError("expansion source must be tracked-clean at its locked commit")
    for rel, sha in lock["config_headers"].items():
        if file_digest(os.path.join(source, rel)) != sha:
            raise LaneError(f"expansion source header differs from lock: {rel}")
    if os.path.isdir(destination) and os.path.realpath(destination) != os.path.realpath(source):
        raise LaneError("existing expansion source link differs from SLINK_EXPANSION_SRC")
    if not os.path.isdir(destination):
        _link_dir(source, destination)
    return lock


# The vendored FireRed decomp (gitignored: patch/.gitignore `vendor/`, ~58 MB) is read by tests/unit/test_gen3_menu_version.py
# (charmap.txt). It is provisioned as a junction to the main checkout's copy, like the expansion source.
VENDORED_DECOMP = "patch/vendor/pokefirered"
VENDORED_DECOMP_MARKER = "charmap.txt"


def link_vendored_decomp(tree, root, repo=None):
    """Junction `tree`/patch/vendor/pokefirered to the first source (the tree's own, this worktree's, the main checkout's) that holds
    the decomp (its charmap.txt). Missing everywhere raises LaneError; an existing different link is refused."""
    repo = repo or REPO
    destination = os.path.join(tree, *VENDORED_DECOMP.split("/"))
    if os.path.isfile(os.path.join(destination, VENDORED_DECOMP_MARKER)):
        return destination
    if os.path.exists(destination):
        raise LaneError(f"{VENDORED_DECOMP} exists in the lane but holds no {VENDORED_DECOMP_MARKER}")
    source = next((c for c in (os.path.join(base, *VENDORED_DECOMP.split("/")) for base in (repo, root))
                   if os.path.isfile(os.path.join(c, VENDORED_DECOMP_MARKER))), None)
    if source is None:
        raise LaneError(f"{VENDORED_DECOMP} is missing from {repo} and {root} (the vendored decomp lives in the root checkout)")
    _link_dir(source, destination)
    return destination


def is_gate_row(row):
    """A row that runs verify_gen3_release's unit lane (release_gate_quick, frlgc_release_gate_quick)."""
    return "tools/verify_gen3_release.py" in row.argv


def provision_gate_inputs(tree, root, repo=None):
    """What `release_gate_quick`'s unit lane reads that a plan's own provisioning does not supply (each was a counted skip, and a skip
    fails the gate): the pinned expansion reference ROM at patch/build/gen3_pokeemerald.gba and its artifact bundle, the locked
    expansion source junction, and the vendored FireRed decomp. Fail closed: a missing source or pin raises LaneError, never skips."""
    repo = repo or REPO
    copy_inputs(tree, root, repo, only=[".cache/expansion-output/"])      # the artifact bundle BEFORE its pinned ROM makes a partial dir
    copy_inputs(tree, root, repo, pins={"exp": expansion_rom_pin(tree)}, extra_pinned=EXPANSION_PINNED_INPUTS, base_inputs=False)
    link_expansion_source(tree, root, repo)
    link_vendored_decomp(tree, root, repo)


def copy_expansion_inputs(tree, root, repo=None):
    """Reuse pinned copying; keep source read-only and offline compiler proof explicitly bound."""
    repo = repo or REPO
    # Copy the whole artifact bundle BEFORE its pinned ROM creates a partial directory.
    copy_inputs(tree, root, repo, only=EXPANSION_UNPINNED_INPUTS)
    copy_inputs(tree, root, repo, pins={"exp": expansion_rom_pin(tree)},
                extra_pinned=EXPANSION_PINNED_INPUTS, base_inputs=False)
    link_expansion_source(tree, root, repo)
    probe = os.environ.get("SLINK_EXPANSION_PROBE_OBJECT") or next(
        (os.path.join(base, ".cache", "x1-probe", "probe.o") for base in (repo, root)
         if os.path.isfile(os.path.join(base, ".cache", "x1-probe", "probe.o"))), None)
    target = expansion_env(tree)["SLINK_EXPANSION_PROBE_OBJECT"]
    if not os.path.isfile(target):
        if not probe or not os.path.isfile(probe):
            raise LaneError("expansion probe missing (SLINK_EXPANSION_PROBE_OBJECT)")
        compile_file = os.path.join(os.path.dirname(probe), "compile.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(probe, target)
        if os.path.isfile(compile_file):
            shutil.copyfile(compile_file, os.path.join(os.path.dirname(target), "compile.json"))
    facts = json.loads(_read(os.path.join(tree, EXPANSION_PACK, "facts.json")))
    if probe and file_digest(probe) != facts["provenance"]["compile"]["object_sha256"]:
        raise LaneError("provided expansion probe object differs from facts")
    if file_digest(target) != facts["provenance"]["compile"]["object_sha256"]:
        raise LaneError("expansion probe object differs from facts")
    for kind in ("pc", "catch"):
        for suffix in ("", "_b"):
            rel = f"tests/fixtures/gen3/exp_{kind}{suffix}.sav"
            if not os.path.isfile(os.path.join(tree, rel)):
                raise LaneError(f"expansion tracked fixture missing: {rel}")


def armgcc_bin(root):
    """The vendored arm-none-eabi bin dir under the main checkout (patch/tools/build.py's search:
    patch/vendor/armgcc/*/bin, newest first), or None. A lane worktree has no patch/vendor, so
    the unit gate's patch-source tests skip there unless $SLINK_ARMGCC points here."""
    exe = "arm-none-eabi-gcc" + (".exe" if os.name == "nt" else "")
    for d in sorted(glob.glob(os.path.join(root, "patch", "vendor", "armgcc", "*", "bin")),
                    reverse=True):
        if os.path.isfile(os.path.join(d, exe)):
            return d
    return None


# ---------------------------------------------------------------------------
# running one row
# ---------------------------------------------------------------------------

def utcnow():
    return dt.datetime.now(dt.UTC)


def parse_utc(text):
    t = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def load_snapshot():
    """cpu%, EmuHawk and python counts, the ph_* receipts' LOAD line. Best effort."""
    ps = ("$c=(Get-CimInstance Win32_Processor|Measure-Object LoadPercentage -Average).Average;"
          "$e=@(Get-Process EmuHawk -EA SilentlyContinue).Count;"
          "$p=@(Get-Process python -EA SilentlyContinue).Count;"
          "\"cpu=$c% emuhawk=$e python=$p\"")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=20).stdout.strip()
    except Exception:
        out = ""
    return f"{dt.datetime.now():%Y-%m-%dT%H:%M:%S} {out or '(unavailable)'}"


def kill_tree(pid):
    """Only the tree rooted at a PID this runner launched -- never an image-name kill (a
    `taskkill /IM EmuHawk.exe` wiped five Gen 2 runs on 2026-09-23)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        with contextlib.suppress(OSError):
            os.killpg(pid, 9)


def rewind_violations(tree, since):
    """BizHawk configs under <tree>/patch/build written since `since` whose rewind is not off."""
    bad = []
    for dirpath, _dirs, files in os.walk(os.path.join(tree, "patch", "build")):
        for name in files:
            path = os.path.join(dirpath, name)
            if not name.endswith(".ini") or os.path.getmtime(path) < since:
                continue
            try:
                with open(path, encoding="utf-8-sig") as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                continue           # not a BizHawk JSON config
            if (cfg.get("Rewind") or {}).get("Enabled") is not False:
                bad.append(path)
    return bad


def run_once(row, deadline):
    """(rc, output, budget_killed, stopped). Streams the child's output to our stdout."""
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" \
        else {"start_new_session": True}
    proc = subprocess.Popen(row.argv, cwd=row.cwd, env=dict(os.environ, **row.env),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", **kw)
    chunks = []

    def pump():
        for line in proc.stdout:
            chunks.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    budget_end = time.time() + row.budget
    killed = stopped = False
    while proc.poll() is None:
        now = time.time()
        if now >= budget_end or (deadline and now >= deadline):
            stopped = not (now >= budget_end)
            killed = not stopped
            kill_tree(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=60)
            break
        time.sleep(1.0)
    reader.join(timeout=30)
    out = "".join(chunks)
    if killed:
        out += f"\n[final_cut] BUDGET EXCEEDED after {row.budget}s: killed PID {proc.pid} tree\n"
    if stopped:
        out += f"\n[final_cut] --stop-at reached: killed PID {proc.pid} tree\n"
    return proc.returncode, out, killed, stopped


def expansion_source_problem(row, lane):
    """The source junction is outside lane Git status, so check its own locked tree."""
    source = row.env.get("SLINK_EXPANSION_SRC")
    if not source:
        return None  # non-expansion plans and isolated row tests have no source binding
    try:
        lock = json.loads(_read(os.path.join(lane, "data/gen3_exp_sources.lock.json")))
        if head(source) != lock["source"]["commit"] or not tracked_clean(source):
            return "source checkout is not tracked-clean at its locked commit"
    except (OSError, ValueError, KeyError, TypeError, LaneError):
        return "source checkout identity cannot be verified"
    return None


def keep_row_artifacts(row, lane):
    """Copy the duo row's e2e result artifacts (e2e_<scenario>_*: the per-side and per-phase result files, the attempt archives, the
    PYDEC file, the witness dumps, kept SaveRAMs) out of the SHARED, scenario-named files into patch/build/fc_rows/<row id>/ of the
    lane, so a later row of the same scenario (FR then LG, or a retry) cannot overwrite them -- a failing row's evidence is exactly
    what a rewrite used to lose. patch/build is gitignored (patch/.gitignore), so the lane stays tracked-clean. Returns the kept
    directory (posix), or None for a row that is not an e2e_duo scenario run or left no artifact."""
    argv = list(row.argv)
    if "tools/e2e_duo.py" not in argv or "--scenario" not in argv:
        return None
    build = os.path.join(lane, "patch", "build")
    files = [f for f in sorted(glob.glob(os.path.join(build, f"e2e_{argv[argv.index('--scenario') + 1]}_*"))) if os.path.isfile(f)]
    if not files:
        return None
    dest = os.path.join(build, "fc_rows", row.id)
    shutil.rmtree(dest, ignore_errors=True)          # only THIS row's own earlier copy (a re-run of the same row)
    os.makedirs(dest)
    for f in files:
        shutil.copy2(f, os.path.join(dest, os.path.basename(f)))
    return dest.replace("\\", "/")


def run_row(row, cut, lane, deadline):
    """Run one row (plus at most one contention retry), write its receipt, return the verdict."""
    attempts, verdict = [], ""
    for attempt in (1, 2):
        before = tracked_clean(lane)
        start, t0 = utcnow(), time.time()
        a = {"load": load_snapshot(), "start_utc": f"{start:%Y-%m-%dT%H:%M:%SZ}",
             "tracked_before": before}
        print(f"\n[final_cut] === {row.id} attempt {attempt}  $ {row.command()}  (cwd={row.cwd})")
        if is_expansion_row(row.id):
            actual = head(lane)
            stamp = f"EXPANSION_CUT requested={cut} before={actual}"
            source_before = expansion_source_problem(row, lane)
            if actual != cut or not before or source_before:
                reason = source_before or "lane is not clean at exact cut"
                rc, out, killed, stopped = 1, f"FAIL expansion {reason}", False, False
            else:
                rc, out, killed, stopped = run_once(row, deadline)
            out = stamp + "\n" + out + f"\nEXPANSION_CUT after={head(lane)}\n"
            source_after = expansion_source_problem(row, lane)
            if source_after:
                out += f"FAIL expansion {source_after} after row\n"
                rc = 1
            problem = expansion_attempt_problem(row.id, cut, out)
            if problem:
                out += "FAIL expansion evidence: " + problem + "\n"
                rc = 1
        else:
            rc, out, killed, stopped = run_once(row, deadline)
            if is_companion_row(row.id) and rc == 0 and not (killed or stopped):
                problem = companion_attempt_problem(row.id, out, gen3_companion_pins(lane), lane)
                if problem:
                    out += "FAIL companion evidence: " + problem + "\n"
                    rc = 1
        a.update(rc=rc, output=out, end_utc=f"{utcnow():%Y-%m-%dT%H:%M:%SZ}",
                 tracked_after=tracked_clean(lane))
        rewind = rewind_violations(lane, t0 - 2)
        if stopped:
            verdict, cls = "STOPPED (--stop-at)", "stopped"
        else:
            verdict, ok = judge(row.id, rc, out, killed, row.own_verdict)
            cls = "pass" if ok else classify_failure(out, killed)
            if ok and rewind:
                verdict, cls = f"FAIL rewind on in {', '.join(rewind)}", "real"
            if not a["tracked_after"]:
                verdict, cls = "FAIL the lane is tracked-dirty after the row", "real"
        a["classification"] = cls
        attempts.append(a)
        if cls != "contention" or attempt == 2 or (deadline and time.time() >= deadline):
            break
        print(f"[final_cut] {row.id}: contention timeout -> the one allowed retry")
    note = inputs_note(hash_inputs(row_inputs(row, lane)))   # after the run: what it used
    kept = keep_row_artifacts(row, lane)                     # pass or fail: the row's own e2e result files
    if kept:
        note += f"\nkept: {kept}"
    with open(receipt_path(row.id, cut), "w", encoding="utf-8") as f:
        f.write(receipts.run_receipt_text(row=row.id, item=row.item.replace(" ", "_"), cut=cut,
                                          lane=lane, command=row.command(), cwd=row.cwd,
                                          env=row.env, attempts=attempts, verdict=verdict,
                                          note=note))
    return verdict, len(attempts), attempts[-1]["tracked_after"]


def new_run_stamp(cut, suffix=""):
    """A UTC stamp no existing per-invocation summary uses. Nothing is created here: the file appears at the first write_summary, so an
    aborted invocation leaves no empty file behind. (ponytail: two invocations started in the same second before either has written
    could share a stamp; a lock/reservation is not worth an empty file on abort.)"""
    base = utcnow().strftime("%Y%m%dT%H%M%SZ")
    stamp, n = base, 0
    while os.path.exists(os.path.join(PROBES, f"fc_SUMMARY_{cut[:8]}{suffix}_run{stamp}.txt")):
        n += 1
        stamp = f"{base}-{n}"
    return stamp


def write_summary(cut, results, suffix="", run_stamp=None):
    """The summary of THIS invocation's rows. fc_SUMMARY_<cut8><suffix>.txt keeps its name and meaning (the latest invocation); with
    `run_stamp` (new_run_stamp) the same text is also written to fc_SUMMARY_<cut8><suffix>_run<stamp>.txt, which no later invocation
    overwrites -- a later pass over a subset of the rows no longer erases the earlier pass's summary."""
    counts = {k: sum(status_of(v) == k for _r, v, _n, _p in results)
              for k in ("RUN", "CARRIED", "CACHED", "FAIL")}
    lines = [f"# G4 final cut {cut} -- tools/gen3_final_cut.py summary "
             f"(written {utcnow():%Y-%m-%dT%H:%M:%SZ})",
             f"# RUN {counts['RUN']} / CARRIED {counts['CARRIED']} / CACHED {counts['CACHED']} "
             f"/ FAIL {counts['FAIL']}", "",
             "| # | row | runbook | verdict | attempts | receipt |", "|---|---|---|---|---|---|"]
    for i, (row, verdict, n, rec) in enumerate(results, 1):
        lines.append(f"| {i} | {row.id} | {row.item} | {verdict} | {n} | {rec} |")
    passed = counts["FAIL"] == 0
    lines += ["", f"OVERALL: {'PASS' if passed and results else 'FAIL'} "
                  f"({counts['RUN'] + counts['CARRIED'] + counts['CACHED']}/{len(results)} rows)"]
    path = os.path.join(PROBES, f"fc_SUMMARY_{cut[:8]}{suffix}.txt")
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if run_stamp:
        with open(os.path.join(PROBES, f"fc_SUMMARY_{cut[:8]}{suffix}_run{run_stamp}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    return path, passed


# ---------------------------------------------------------------------------
# helper subcommand: §9's zip boot
# ---------------------------------------------------------------------------

_BOOT_LUA = """-- gen3_final_cut zip-boot bootstrap: tap A through the title/CONTINUE, then the shipped entry
for f = 1, 1500 do
  if f >= 300 and f % 30 < 3 then joypad.set({A = true}) end
  emu.frameadvance()
end
dofile(os.getenv("SLINK_ZIPBOOT_ENTRY"))
"""
# a REFUSAL boot (ZIP_BOOT_REFUSED): the shipped entry raises on the refused cartridge, so catch the error text
# into the console (slink.lua tees console.log into slink_lua.log) -- that line is the evidence
_BOOT_LUA_REFUSED = _BOOT_LUA.replace(
    'dofile(os.getenv("SLINK_ZIPBOOT_ENTRY"))',
    'local ok, err = pcall(dofile, os.getenv("SLINK_ZIPBOOT_ENTRY"))\n'
    'console.log("[zip-boot] entry " .. (ok and "returned" or "raised: " .. tostring(err)))')


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# zip-boot per title: (ROM in the lane, launched filename, fixture, battery name, client identity
# line, server hello). RR boots the companion build players get from SLink-RR.ups; BizHawk has no
# gamedb entry for it and names the battery from the launched filename ("slink_RR.gba" ->
# "slink RR.SaveRAM", tools/e2e_duo.py GEN3_TITLES); its hello says rom_type firered_rr
# (lua/gen3/entry.lua) and its identity line is gen3_rr/radical_red (companion by hash).
# Patch-first (owner 2026-10-02): the shipped client REFUSES a clean FireRed / Emerald (slink.lua raises the
# `Unsupported Gen 3 cartridge: ... needs the SLink companion patch` error), so their zip-boot rows prove that
# refusal -- no identity line, no TCP connect, no server hello -- and carry a `_refused` row id, so a receipt of the
# old clean-client PASS (same cut, old name) can never resume as this row's verdict.
ZIP_BOOT_REFUSED = ("firered", "emerald")


def _zip_refusal_re(title):
    return (rf"Unsupported Gen 3 cartridge: this {title} cartridge needs the SLink companion patch; "
            r"prepare it through the Manager or /patcher")


ZIP_BOOT = {
    "firered": (STAGED["firered"], "fr.gba", "firered_party_town.sav",
                "Pokemon - FireRed Version (USA).SaveRAM", _zip_refusal_re("firered"), None),
    "radical_red": ("patch/build/slink_RR.gba", "slink_RR.gba", "rr_town.sav", "slink RR.SaveRAM",
                    r"\[SLink-gen3\] gen3_rr/radical_red \(companion by hash\) player a ",
                    "hello rom=firered_rr "),
    # emerald (card E4b-FINALCUT): a clean dump BizHawk's gamedb knows (like FR/LG, unlike RR's
    # companion), so the launched filename is arbitrary and the battery files under the gamedb
    # title (EMERALD_SAVERAM); rom_type "emerald" and pack "gen3_emerald" per
    # lua/gen3/entry.lua:72 (rom_type), :98 (pack). EG4 (ruling 24) has landed, so this row is
    # reachable for real now -- see zip_boot()'s own precondition check, emerald_admission_blocker.
    "emerald": (STAGED["emerald"], "emerald.gba", "emerald_town.sav", EMERALD_SAVERAM,
                _zip_refusal_re("emerald"), None),
    # --title frlgc: the PATCHED cartridges. BizHawk has no gamedb entry for them, so the battery follows the launched filename
    # ("slink_FireRed.gba" -> "slink FireRed.SaveRAM", the RR rule above); the client's identity line must say `companion by
    # hash` (zip_boot adds the pinned rom prefix) while the server hello still reports the vanilla rom_type
    # (lua/gen3/entry.lua:66-78).
    "firered_companion": (COMPANION_ROMS["firered"], "slink_FireRed.gba", "firered_party_town.sav", "slink FireRed.SaveRAM",
                          r"\[SLink-gen3\] gen3_frlg/firered \(companion by hash\) player a ", "hello rom=firered "),
    "leafgreen_companion": (COMPANION_ROMS["leafgreen"], "slink_LeafGreen.gba", "leafgreen_party_town.sav",
                            "slink LeafGreen.SaveRAM",
                            r"\[SLink-gen3\] gen3_frlg/leafgreen \(companion by hash\) player a ", "hello rom=leafgreen "),
    "emerald_companion": (COMPANION_ROMS["emerald"], "slink_Emerald.gba", "emerald_town.sav", "slink Emerald.SaveRAM",
                          r"\[SLink-gen3\] gen3_emerald/emerald \(companion by hash\) player a ", "hello rom=emerald "),
    "exp": (STAGED["exp"], "gen3_pokeemerald.gba", "exp_pc.sav", "gen3 pokeemerald.SaveRAM",
            r"\[SLink-gen3\] gen3_exp/emerald_expansion_28877d73 \(clean by hash\) player a ",
            f"hello rom={EXPANSION_TITLE} "),
}


def emerald_admission_blocker(lua_dir):
    """(kind, reason) once something blocks Emerald's admission in the EXTRACTED zip's own
    lua/gen3/entry.lua, checked against the artifact actually being booted (not the lane's own
    checkout, so a hand-edited zip is judged on what it would really do); None once it admits
    gen3_emerald (Entry.ROUTED, ruling 24).

    kind distinguishes a real defect from the expected pre-EG4 state (card E4b-CKPT review F3/F4):
      - "ZIP-DEFECT": the zip itself is malformed -- no lua/gen3/entry.lua, or no Entry.ROUTED
        assignment at all. A real failure, never the allowed EG4 skip.
      - "BLOCKED-EG4": the zip is well-formed but ROUTED does not (yet) admit gen3_emerald --
        exactly ruling 24's pre-signature state, the allowed skip.
    The search anchors on the actual `Entry.ROUTED = {...}` assignment (DOTALL, non-greedy to the
    first closing brace), not the first `{...}` anywhere in the file, so an unrelated table earlier
    in entry.lua is never mistaken for it. Both `gen3_emerald = true` and the bracket-string
    spelling (`["gen3_emerald"] = true` / `['gen3_emerald'] = true`) count as admitted."""
    text = _read(os.path.join(lua_dir, "gen3", "entry.lua"))
    if not text:
        return "ZIP-DEFECT", "the extracted zip has no lua/gen3/entry.lua"
    m = re.search(r"Entry\.ROUTED\s*=\s*\{(.*?)\}", text, re.S)
    if not m:
        return "ZIP-DEFECT", "the extracted zip's lua/gen3/entry.lua has no Entry.ROUTED assignment"
    admitted = re.search(r"""(?:\bgen3_emerald\b|\[\s*["']gen3_emerald["']\s*\])\s*=\s*true\b""",
                         m.group(1))
    if not admitted:
        return ("BLOCKED-EG4", "lua/gen3/entry.lua Entry.ROUTED has no gen3_emerald entry in "
                "this zip (ruling 24: it flips only at EG4)")
    return None


def zip_boot(zip_path, lane, timeout=300, title="firered"):
    """Extract the zip to a space-free temp dir, run the cut's server from the lane, boot
    `title`'s ROM (ZIP_BOOT) on the extracted lua/slink.lua with its town fixture seeded, and PASS
    on the client's identity line + `TCP connected` and the server's hello
    (release_zip_boot_fr_rehearsal_2026-09-23.txt; RR: fc_zip_boot_radicalred_58a8951f.txt).
    Kills only the two PIDs it launched."""
    import gen3_fixtures
    import run_gate
    _tmp_root = os.path.join(os.environ.get("SLINK_WORK_ROOT", ""), "tmp", "gen3") if os.environ.get("SLINK_WORK_ROOT") else None
    if _tmp_root:
        os.makedirs(_tmp_root, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="slink_zipboot_", dir=_tmp_root)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(os.path.join(tmp, "extract"))
    entry = next((os.path.join(d, "slink.lua") for d, _s, fs in os.walk(os.path.join(tmp, "extract"))
                  if "slink.lua" in fs and d.replace("\\", "/").endswith("/lua")), None)
    if not entry:
        print("RESULT: FAIL the zip has no lua/slink.lua")
        return 1
    if title in ("emerald", "emerald_companion"):
        blocked = emerald_admission_blocker(os.path.dirname(entry))
        if blocked:
            kind, reason = blocked
            if kind == "BLOCKED-EG4":
                # ruling 24 (EG4) has landed on this branch, so a zip reaching this path was built
                # before that signature -- re-running an old --cut, not the current one. No longer
                # an ALLOWED_SKIPS entry: this SKIP is unexcused and fails the row, same as any
                # other unexplained skip.
                print(f"  zip_boot_emerald: SKIP (not allowed: EG4 has landed) — "
                      f"BLOCKED-EG4: {reason}")
                return 3
            # ZIP-DEFECT: the zip itself is malformed -- a real failure, never the allowed skip.
            print(f"RESULT: FAIL ZIP-DEFECT: {reason}")
            return 1
    lua_log = os.path.join(os.path.dirname(os.path.dirname(entry)), "slink_lua.log")
    saveram = os.path.join(tmp, "saveram")
    os.makedirs(saveram)
    rom_rel, rom_name, fixture_name, saveram_name, client_pat, hello = ZIP_BOOT[title]
    if title.endswith("_companion"):
        # a patched cartridge boots only if it IS the pinned build, and the client must have admitted exactly that hash
        base = title[:-len("_companion")]
        try:
            pin = rom_pins(lane, require_companions=True)[f"{base}_companion"]
        except LaneError as exc:
            print(f"RESULT: FAIL companion pins unavailable: {exc}")
            return 1
        got = file_digest(os.path.join(lane, rom_rel), "sha1")
        if got != pin:
            print(f"RESULT: FAIL {rom_rel} sha1 {got} is not the {base} companion pin {pin}")
            return 1
        client_pat += rf"-> \S+:\d+ \(rom {pin[:8]}\)"
    fixture = os.path.join(lane, "tests", "fixtures", "gen3", fixture_name)
    with open(fixture, "rb") as f:
        body = gen3_fixtures.codec.split_rtc(f.read())[0]
    with open(os.path.join(saveram, saveram_name), "wb") as f:
        f.write(body)
    cfg = os.path.join(tmp, "config.ini")
    gen3_fixtures.write_gba_run_config(run_gate.BIZHAWK_CONFIG, cfg, saveram)
    with open(cfg, encoding="utf-8") as f:
        if (json.load(f).get("Rewind") or {}).get("Enabled") is not False:
            print("RESULT: FAIL the generated config does not have rewind off")
            return 1
    shutil.copyfile(os.path.join(lane, rom_rel), os.path.join(tmp, rom_name))
    refused = title in ZIP_BOOT_REFUSED
    with open(os.path.join(tmp, "boot.lua"), "w", encoding="utf-8") as f:
        f.write(_BOOT_LUA_REFUSED if refused else _BOOT_LUA)
    tcp, http = _free_port(), _free_port()
    server_log = os.path.join(tmp, "server.log")
    print(f"zip={zip_path}\nextract={tmp}\\extract entry={entry}\nfixture={fixture}\n"
          f"server: python -m server.server --port {tcp} --http-port {http} (cwd={lane})")
    procs = []
    try:
        with open(server_log, "w", encoding="utf-8") as log:
            procs.append(subprocess.Popen(
                [PY, "-m", "server.server", "--host", "127.0.0.1", "--port", str(tcp),
                 "--http-port", str(http), "--data-dir", os.path.join(tmp, "data")],
                cwd=lane, stdout=log, stderr=subprocess.STDOUT))
        time.sleep(3)
        env = dict(os.environ, SLINK_HOST="127.0.0.1", SLINK_PORT=str(tcp), SLINK_PLAYER="a",
                   SLINK_ZIPBOOT_ENTRY=entry.replace("\\", "/"))
        if title == "exp":
            env["SLINK_STATE_DIR"] = os.path.join(tmp, "state")
        cmd = [run_gate.EMUHAWK, "--config=config.ini", "--lua=boot.lua", rom_name]
        print(f"[zip-boot] {' '.join(cmd)}  (cwd={tmp})")
        procs.append(subprocess.Popen(cmd, cwd=tmp, env=env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL))
        client_re = re.compile(client_pat)
        end, ok = time.time() + timeout, False
        while time.time() < end and not ok:
            time.sleep(2)
            ltxt = _read(lua_log)
            if refused:
                ok = zip_boot_refusal_ok(client_re, ltxt, _read(server_log))
                continue
            ok = bool(client_re.search(ltxt)) and "TCP connected" in ltxt and \
                hello in _read(server_log)
            if title == "exp":
                ok = ok and expansion_route_logged(_read(server_log), ltxt, sides=("a",))
    finally:
        for p in reversed(procs):
            if p.poll() is None:
                kill_tree(p.pid)
    print(f"--- {lua_log} ---\n{_read(lua_log)}\n--- server log ---\n{_read(server_log)}")
    if refused:
        # one last look at the final logs: a refusal that was followed by a connect is no refusal
        ok = zip_boot_refusal_ok(client_re, _read(lua_log), _read(server_log))
        print(f"RESULT: PASS the extracted zip's client REFUSED the clean {title} (needs the SLink companion patch); "
              f"it never connected" if ok else
              f"RESULT: FAIL no companion-patch refusal evidence for the clean {title} within {timeout}s")
        return 0 if ok else 1
    print(f"RESULT: PASS the extracted zip booted {title} on the new client" if ok else
          f"RESULT: FAIL no client/server boot evidence within {timeout}s")
    return 0 if ok else 1


def zip_boot_refusal_ok(client_re, lua_text, server_text):
    """The shipped client refused the cartridge: its own refusal error is in the log, it never reached
    `TCP connected`, and the server never saw a hello."""
    return bool(client_re.search(lua_text)) and "TCP connected" not in lua_text and "hello rom=" not in server_text


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def is_expansion_row(row_id):
    return row_id.endswith(("_exp_as_a", "_exp")) or row_id.startswith("exp_zip_")


def expansion_route_logged(server_text, client_text=None, *, sides=("a", "b")):
    """Require native client identities and accepted production routes for each player."""
    from e2e_duo import gen3_production_route_lines

    client_text = server_text if client_text is None else client_text
    rom_hash = expansion_rom_pin(REPO)
    return all(gen3_production_route_lines(server_text, client_text, side,
                                           "gen3_exp", EXPANSION_TITLE, rom_hash)
               for side in sides)


def expansion_attempt_problem(row_id, cut, text):
    """Actual full lane identity and the route evidence this runtime row requires."""
    before = re.findall(rf"^EXPANSION_CUT requested=({_SHA}) before=({_SHA})$", text, re.M)
    after = re.findall(rf"^EXPANSION_CUT after=({_SHA})$", text, re.M)
    if before != [(cut, cut)] or after != [cut]:
        return "lane identity is not the exact requested full SHA"
    if (row_id.endswith("_exp_as_a") or row_id == "zip_boot_exp") and not \
            expansion_route_logged(text, sides=("a",) if row_id == "zip_boot_exp" else ("a", "b")):
        return "missing logged production server route or client admission"
    return None


_COMPANION_PACK = {"firered": "gen3_frlg", "leafgreen": "gen3_frlg", "emerald": "gen3_emerald"}


def companion_attempt_problem(row_id, text, pins, lane=None):
    """Why a companion row's attempt output is not proof it ran the companion, or None. A duo row owes one COMPANION_ADMISSION
    line per side (e2e_duo's --gen3-companion: the CLIENT's own `<title> (companion by hash)` admission with the pinned rom
    prefix, the sides' titles taken from the orientation in the id); a zip-boot row owes that same client line in the lua log it
    prints. Rows with no client (source, boot-check, zip build/check, the release gate) owe none. `pins`: gen3_companion_pins()."""
    if is_rand_row(row_id):
        return rand_attempt_problem(row_id, text, pins, lane)
    duo = re.fullmatch(r"frlgc_.+_(fr|lg|em)_as_a_companion", row_id)
    boot = re.fullmatch(r"frlgc_zip_boot_(firered|leafgreen|emerald)", row_id)
    if duo:
        titles = {"fr": ("firered", "leafgreen"), "lg": ("leafgreen", "firered"), "em": ("emerald", "emerald")}[duo[1]]
        sides = dict(zip("ab", titles, strict=True))
    elif boot:
        sides = {"a": boot[1]}
    else:
        return None
    problems = []
    for side, title in sides.items():
        pin = (pins or {}).get(f"{title}_companion")
        if not pin:
            return f"no {title}_companion pin to compare the admission hash with"
        want = (rf"\[SLink-gen3\] {_COMPANION_PACK[title]}/{title} \(companion by hash\) player {side} -> \S+:\d+ "
                rf"\(rom {pin[:8]}\)")
        prefix = rf"^\[duo\] COMPANION_ADMISSION {side}: \[client\] " if duo else r"^.*"
        if not re.search(rf"(?m){prefix}{want}\r?$", text):
            problems.append(f"no `{title} (companion by hash)` admission with rom {pin[:8]} for player {side}")
        stray = [ln for ln in re.findall(rf"(?m)^.*\[SLink-gen3\] \S+/\S+ \(\w+ by \w+\) player {side} .*$", text)
                 if not re.search(want, ln)]
        problems += [f"player {side} admitted something else: {ln.strip()[:160]}" for ln in stray]
    return "; ".join(problems) or None


def rand_attempt_problem(row_id, text, pins, lane=None):
    """Why a frlgcr_ row's attempt output is not proof of its claim, or None. A randomized duo row owes, per side, a RAND_INPUT
    line saying the launched cartridge is the randomized ROM WITH the published companion overlay (`companion=overlay`) whose base
    is the pinned companion build (`companion_pin=` the first 12 hex of the pin); the admission row also owes its clean_refused leg.
    A clean zip-boot row owes the shipped client's refusal. Source / zip build / zip check rows owe nothing extra.
    NOT PARITY WITH frlgc: a randomized cartridge cannot print the client's `(companion by hash)` admission line (its hash is
    unpinned), so this evidence is the HARNESS's own RAND_INPUT self-attestation. With `lane` (run_row, and fc_check when it is given
    one) the note's sha1 must also equal the sha1 of the overlay ROM actually staged in that lane."""
    boot = re.fullmatch(RAND_ROW_PREFIX + r"zip_boot_(firered|emerald)_refused", row_id)
    if boot:
        want = rf"RESULT: PASS the extracted zip's client REFUSED the clean {boot[1]}"
        return None if re.search(want, text) else f"no `{want}` line (the shipped client must refuse the clean {boot[1]})"
    titles, _files = rand_row_titles(row_id)
    if not titles:
        return None
    import e2e_duo
    scenario = next(s for s, g, sfx, _e in RAND_DUO_ROWS if RAND_ROW_PREFIX + s + sfx == row_id)
    game = next(g for s, g, sfx, _e in RAND_DUO_ROWS if RAND_ROW_PREFIX + s + sfx == row_id)
    problems = []
    for side in "ab":
        title = e2e_duo.GAMES[game]["sides"][side][0]
        pin = (pins or {}).get(f"{title}_companion")
        if not pin:
            return f"no {title}_companion pin to compare the overlay base with"
        pattern = rf"(?m)^\[duo\] RAND_INPUT {side} .*title={title} companion=overlay companion_pin={pin[:12]} sha1=([0-9a-f]{{40}})"
        found = re.search(pattern, text)
        if not found:
            problems.append(f"no RAND_INPUT {side} line with companion=overlay on the {title} companion pin {pin[:12]}")
        elif lane:
            staged = os.path.join(lane, "patch", "build", f"rand_{RAND_LANE_ID}", f"{side}_{title}.gba")
            if file_digest(staged, "sha1") != found[1]:
                problems.append(f"RAND_INPUT {side} sha1 {found[1][:12]} is not the overlay ROM staged in the lane ({staged})")
    if scenario in e2e_duo.GEN3_RAND_ADMISSION and not re.search(r"(?m)^\[duo\] RAND_REFUSED phase=clean_refused side=b ", text):
        problems.append("no RAND_REFUSED clean_refused leg (the clean cartridge must be refused at launch)")
    return "; ".join(problems) or None


def estimate_twin(row_id):
    """The clean row whose retained receipts price a companion row (a clean row is its own twin)."""
    if not row_id.startswith(COMPANION_ROW_PREFIX):       # a frlgcr_ row has no clean twin: its own receipts price it
        return row_id
    return re.sub(r"_as_a_companion$", "_as_a", row_id[len(COMPANION_ROW_PREFIX):])


def stage_companions(tree, titles=GEN3_COMPANION_TITLES):
    """Write `tree`/COMPANION_ROMS[title] (the lane inputs --title frlgc / frlgc-rand copy from the main checkout) by applying the
    SHIPPED UPS to the clean dump at the tree's root -- no toolchain. The pins and the UPS (patch/dist/gen3_companions.json) are THIS
    runner's tree's (the cut's code), the dump and the destination are `tree`'s. The dump must equal the clean pin, the composition
    must equal the companion pin, and an existing file that differs is never overwritten.
    Exit 0 when every title is staged and pinned, else 1 with the reason."""
    from tools.gen3_companions import published
    try:
        pins = rom_pins(REPO, require_companions=True)
    except LaneError as exc:
        print(f"RESULT: FAIL stage-companions: {exc}")
        return 1
    for title in titles:
        dump_path, dst = os.path.join(tree, ROOT_DUMPS[title]), os.path.join(tree, COMPANION_ROMS[title])
        try:
            dump = Path(dump_path).read_bytes()
        except OSError as exc:
            print(f"RESULT: FAIL stage-companions {title}: cannot read the clean dump {dump_path}: {exc}")
            return 1
        if hashlib.sha1(dump).hexdigest() != pins[title]:
            print(f"RESULT: FAIL stage-companions {title}: {ROOT_DUMPS[title]} is not the pinned clean dump")
            return 1
        try:
            composed = published(title, dump, root=REPO)
        except ValueError as exc:
            print(f"RESULT: FAIL stage-companions {title}: {exc}")
            return 1
        if composed is None or hashlib.sha1(composed[0]).hexdigest() != pins[f"{title}_companion"]:
            print(f"RESULT: FAIL stage-companions {title}: the composition is not the {title} companion pin")
            return 1
        try:                              # ONE read decides: absent => stage, equal => leave alone, differing => never overwrite
            existing = Path(dst).read_bytes()
        except FileNotFoundError:
            existing = None
        if existing is not None and existing != composed[0]:
            print(f"RESULT: FAIL stage-companions {title}: {COMPANION_ROMS[title]} exists and differs from the pinned build; "
                  f"remove it deliberately")
            return 1
        if existing is None:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = f"{dst}.tmp{os.getpid()}"      # next to the destination: os.replace is atomic, a partial file is never the ROM
            try:
                Path(tmp).write_bytes(composed[0])
                os.replace(tmp, dst)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        print(f"{title}: {COMPANION_ROMS[title]} sha1 {pins[f'{title}_companion']}")
    print("RESULT: PASS stage-companions")
    return 0


# ---------------------------------------------------------------------------
# helper subcommand: the staged companion cartridge is the pinned composition (no rebuild, nothing written)
# ---------------------------------------------------------------------------

def companion_check(lane, title):
    """Fail closed unless the lane's staged companion (COMPANION_ROMS[title]) IS the pinned build AND recomposing it in memory
    from the pinned clean dump and the shipped UPS (patch/dist/gen3_companions.json `patch`) gives the same bytes: sha1 and md5
    equal the pins, the dump equals the clean pin and `base_sha1`, the UPS applies (its own CRCs), staged == composed.
    Exit 0 PASS / 1 FAIL; the RESULT line is the verdict."""
    def fail(why):
        print(f"RESULT: FAIL {title} companion: {why}")
        return 1
    try:
        pins = rom_pins(lane, require_companions=True)
        row = json.loads(_read(os.path.join(lane, "patch", "dist", "gen3_companions.json")))["titles"][title]
        sys.path.insert(0, REPO)
        from patch.tools.make_ups import ups_apply
        staged_path = os.path.join(lane, COMPANION_ROMS[title])
        dump_path = os.path.join(lane, ROOT_DUMPS[title])
        for path in (staged_path, dump_path):
            if not os.path.isfile(path):
                return fail(f"{os.path.relpath(path, lane)} is missing")
        staged, dump = Path(staged_path).read_bytes(), Path(dump_path).read_bytes()
        ups = Path(os.path.join(lane, "patch", "dist", row["patch"])).read_bytes()
        composed = ups_apply(dump, ups)
    except (LaneError, ValueError, KeyError, OSError) as exc:
        return fail(f"{type(exc).__name__}: {exc}")
    pin, md5 = pins[f"{title}_companion"], pins[f"{title}_companion:md5"]
    checks = {"clean dump sha1 == the clean pin": hashlib.sha1(dump).hexdigest() == pins[title],
              "clean dump sha1 == base_sha1": hashlib.sha1(dump).hexdigest() == row.get("base_sha1"),
              "staged sha1 == the companion pin": hashlib.sha1(staged).hexdigest() == pin,
              "staged md5 == the companion pin": hashlib.md5(staged).hexdigest() == md5,
              "composed sha1 == the companion pin": hashlib.sha1(composed).hexdigest() == pin,
              "composed md5 == the companion pin": hashlib.md5(composed).hexdigest() == md5,
              "staged bytes == composed bytes": staged == composed}
    bad = [name for name, ok in checks.items() if not ok]
    if bad:
        return fail("; ".join(bad))
    print(f"{title}: {row['patch']} on the clean dump ({pins[title][:12]}) == {COMPANION_ROMS[title]} sha1 {pin} md5 {md5}")
    print(f"RESULT: PASS {title} staged companion is the pinned composition")
    return 0


# ---------------------------------------------------------------------------
# helper subcommand: §10's item 6 route-differential
# ---------------------------------------------------------------------------

ITEM6_FIX = "3941198c"      # the Gen 1 ordering fix that stays with the post-G4 convergence card
ITEM6_SCRATCH = "tests/unit/test_zz_item6_gen1_ordering_scratch.py"


def item6_scratch_source(root):
    """3941198c's named test, verbatim, importing the tree's own `world` fixture -- W3's case 1."""
    src = _git(root, "show", f"{ITEM6_FIX}:tests/unit/test_gen1_client.py").stdout
    m = re.search(r'@pytest\.mark\.parametrize\("special", \[False, True\]\)\n'
                  r"def test_a_battle_held_force_faint_keeps_its_place.*?(?=\n\n\n)", src, re.S)
    if not m:
        raise LaneError(f"{ITEM6_FIX}'s ordering test not found")
    return ("import pytest\n\nfrom server.adapters import gen1_codec as codec  # noqa: F401\n"
            "from tests.unit.test_gen1_client import world  # noqa: F401  (this tree's fixture)\n"
            "\n\n" + m.group(0) + "\n")


def item6_cases():
    """(name, argv, env, restore-after) -- the three cases of item6_route_diff_*_2026-09-24.txt."""
    cases = [("gen1_ordering", [PY, "-m", "pytest", ITEM6_SCRATCH, "-q", "-p", "no:randomly"], {}),
             ("gen1_sfx_town", [PY, "-m", "pytest", "tests/live/test_gen1_gates.py::test_gen1_sfx_matrix",
                                "-k", "town", "-q", "-p", "no:randomly", "-rs"], {"SLINK_LIVE": "1"})]
    cases += [(f"gen2_legacy_{s}", [PY, "tools/e2e_duo.py", "--game", "gen2", "--scenario", s], {})
              for s in ("faint", "boxsync", "memorialize")]
    return cases


def item6_verdict(table):
    """table: {case: {"master": ok, "branch": ok}}. A regression is master PASS, branch FAIL;
    the claim is no regression vs master, not full correctness (owner ruling 10)."""
    return [c for c, sides in table.items() if sides["master"] and not sides["branch"]]


def item6(branch, master):
    root = main_checkout()
    scratch = item6_scratch_source(root)
    table = {}
    for name, argv, env in item6_cases():
        table[name] = {}
        for side, tree in (("master", master), ("branch", branch)):   # paired back to back
            print(f"\n[item6] {name} on {side} ({head(tree)[:8]})  $ "
                  f"{shlex.join(['python' if a == PY else a for a in argv])}")
            scratch_path = os.path.join(tree, ITEM6_SCRATCH)
            if name == "gen1_ordering":
                with open(scratch_path, "w", encoding="utf-8") as f:
                    f.write(scratch)
            try:
                p = subprocess.run(argv, cwd=tree, env=dict(os.environ, **env),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
            finally:
                if name == "gen1_ordering" and os.path.exists(scratch_path):
                    os.remove(scratch_path)
                if name == "gen1_sfx_town":   # the gate rewrites tracked fixture receipts
                    _git(tree, "checkout", "--", "tests/fixtures/gen1/receipts", check=False)
            out = p.stdout + p.stderr
            print(out[-4000:])
            table[name][side] = p.returncode == 0 and not detect_skip(p.returncode, out)
    print("\n[item6] case                  master  branch")
    for name, sides in table.items():
        print(f"[item6] {name:<22}{'PASS' if sides['master'] else 'FAIL':<8}"
              f"{'PASS' if sides['branch'] else 'FAIL'}")
    regressed = item6_verdict(table)
    print(f"RESULT: {'FAIL regression in ' + ', '.join(regressed) if regressed else 'PASS no case regressed vs master'}")
    return 1 if regressed else 0


# ---------------------------------------------------------------------------
# --carry: synthetic evidence (owner-approved, card G4-FINALCUT-FAST, hardened by
# G4-FINALCUT-HARDEN after OMP's review of d9a08f5b). A row is CARRIED, not run, only when ALL of:
#   - a citable PASS receipt for the same row+orientation exists at a cut X (one unambiguous final
#     verdict; a CARRIED fc receipt is citable only through its validated origin);
#   - X is an ancestor of the cut (`git merge-base --is-ancestor X cut`);
#   - `git diff --name-only X cut` touches none of the row's dependency globs (CONSERVATIVE: the
#     client closure, the packs it loads, the harness and carriers, the server, the fixtures, the
#     pret syms -- when in doubt the path is in; `**` crosses directories, `*` does not);
#   - the non-git inputs (staged ROMs, ...) hash the same in this lane as the receipt recorded.
# ---------------------------------------------------------------------------

GEN3_CLIENT = ["lua/*.lua", "lua/x64/**", "lua/gen3/**", "lua/core/**", "lua/games/**",
               "data/games/gen3_frlg/**", "data/games/gen3_frlge/**"]   # entry.lua:74-78
GEN3_HARNESS = ["tools/run_gate.py", "tools/gen1_playthrough.py", "tools/gen3_fixtures.py",
                "lua/tests/gen3_*.lua", "lua/tests/playlib.lua", "lua/tests/mkstates_gen3*.lua",
                "server/adapters/**", "data/gen3/pret/**"]
FRLG_FIXTURES = ["tests/fixtures/gen3/firered_party_*", "tests/fixtures/gen3/leafgreen_party_*"]
DUO_DEPS = GEN3_CLIENT + GEN3_HARNESS + FRLG_FIXTURES + [
    "server/**", "tools/e2e_duo.py", "lua/tests/duo/**"]
PROBE_DEPS = GEN3_CLIENT + GEN3_HARNESS + FRLG_FIXTURES + [
    "data/games/gen3_rr/**",            # gen3_gatelib.lua / probe_gen3_checkpoint.lua read it
    "lua/tests/probe_gen3_checkpoint.lua", "tools/gen3_probe_receipt.py", "tools/gen3_bw_hashes.py",
    "tools/mkstates_gen3.py", "tools/mkstates_gen3_tutorials.py"]
PROBE_GATES_DEPS = GEN3_CLIENT + GEN3_HARNESS + [
    "data/games/gen3_*/**", "lua/tests/*gen3*", "lua/tests/duo/**", "tools/e2e_duo.py",
    "tests/live/test_gen3_probe_gates.py", "tests/conftest.py"]
# --title rr (card G5-RUNNER-RR): the RR profile pack duo_gen3_main.lua/gen3_gatelib.lua read
# (GAMES["gen3_rr"]["sides"], tools/e2e_duo.py:1975) plus the RR opcode gate test itself.
RR_DEPS = ["data/games/gen3_rr/**"]
RR_GATES_DEPS = GEN3_CLIENT + GEN3_HARNESS + [
    "data/games/gen3_*/**", "lua/tests/*gen3*", "tests/live/test_lua_gates.py", "tests/conftest.py"]
ITEM6_DEPS = ["lua/*.lua", "lua/gen1/**", "lua/gen2/**", "lua/core/**", "lua/clients/**",
              "lua/games/**", "lua/tests/*gen1*", "lua/tests/*gb*", "lua/tests/duo/**", "server/**",
              "tools/e2e_duo.py", "tools/run_gate.py", "tools/gen1_*.py", "tools/gen3_final_cut.py",
              "tests/conftest.py", "tests/unit/test_gen1_client.py", "tests/unit/protocol_schema.py",
              "tests/live/test_gen1_gates.py", "tests/fixtures/gen1/**", "tests/fixtures/gen2/**",
              "data/games/gen1_rby/**", "data/games/gen2_crystal/**", "patch/gen1/**"]
# builds are content-addressed instead (the §1 build cache), the zip is built from the cut and
# the source gate IS the cut, so none of these is ever carried. The checkpoint probe carries once
# its states come from the cache with known hashes (predicted_checkpoint_inputs).
NEVER_CARRIED = ("states_*", "tutorials_*", "zip_*", "rr_zip_*", "release_gate_quick",
                 # ponytail: RR rows always RUN -- row_inputs() hashes no RR companion/clean ROM,
                 # RR fixture or gate savestate yet, so a carry could cite a PASS on other
                 # artifacts (OMP cx-42592031 F2-F4); carry them once those inputs are hashed
                 "*_rr_as_a", "rr_opcode_gates")


def row_deps(row):
    """The row's dependency globs, or None for a row that is never carried."""
    if is_companion_row(row.id) or any(fnmatch.fnmatch(row.id, p) for p in NEVER_CARRIED):
        return None
    if row.id.endswith(("_fr_as_a", "_lg_as_a")):
        return DUO_DEPS
    if row.id.endswith("_rr_as_a"):
        return DUO_DEPS + RR_DEPS
    if row.id == "rr_opcode_gates":
        return RR_GATES_DEPS
    if row.id == "checkpoint_emerald":
        # PROBE_DEPS names neither data/games/gen3_emerald/** nor the emerald fixtures, so an
        # Emerald-only change would carry a stale receipt: never carried until it does.
        return None
    if row.id.startswith("checkpoint_"):
        return PROBE_DEPS
    # card E4b-CKPT review (OMP cx-6b619663 F6): row_inputs() has no ROM/pack hash wired for
    # Emerald's bootcheck rows yet, so giving them a real dep list here would let a stale carry
    # decision skip re-checking their non-git ROM byte content -- never carry until row_inputs()
    # actually hashes it (the smaller, honest fix over half-wiring row_inputs for one title).
    if row.id.startswith("bootcheck_emerald_"):
        return None
    if row.id.startswith("bootcheck_"):
        return GEN3_CLIENT + GEN3_HARNESS + [f"tests/fixtures/gen3/{row.id[len('bootcheck_'):]}.sav"]
    if row.id == "item6_route_diff":
        return ITEM6_DEPS
    if row.id == "probe_gates":
        return PROBE_GATES_DEPS
    return None


# Companion (patched FR/LG/Emerald) rows, for the --title frlgc re-cut. Their ids carry COMPANION_ROW_PREFIX and never end in
# _fr_as_a/_lg_as_a/_em_as_a, so none of the clean row_deps/row_inputs/build_kind shapes matches; the prefix is also checked FIRST
# in row_deps, row_inputs, build_key and predicted_checkpoint_inputs. A clean receipt is therefore never carried into a companion
# row, and companion states are never cached (the state cache is keyed on the clean STAGED[title] ROM; refusing is smaller and
# safer than threading a second ROM path through build_key/predicted_checkpoint_inputs and the cache layout).
COMPANION_ROW_PREFIX = "frlgc_"


def is_companion_row(row_id):
    return row_id.startswith((COMPANION_ROW_PREFIX, RAND_ROW_PREFIX))


def companion_row_id(base_id):
    """The companion twin of a clean row id (the later --title frlgc plan names its rows with this)."""
    return COMPANION_ROW_PREFIX + re.sub(r"_as_a$", "_as_a_companion", base_id)


def companion_row_inputs(titles, lane):
    """{rom:<title>_companion: path}: the companion ROM keys never collide with the clean rows' rom:<title>."""
    return {f"rom:{t}_companion": os.path.join(lane, COMPANION_ROMS[t]) for t in titles}


def _companion_row_titles(rid):
    """The companion ROMs a companion row boots: the titles its id names, else (duo rows, unnamed) all three.
    Over-inclusive is safe: extra hashes only add inputs to the receipt."""
    if "_as_a_" in rid:      # a duo row: FR<->LG for the frlg pairing, Emerald<->Emerald for the em row
        return ("emerald",) if "_em_as_a_" in rid else ("firered", "leafgreen")
    tokens = {"firered": "firered", "leafgreen": "leafgreen", "emerald": "emerald"}
    named = {tokens[t] for t in rid.split("_") if t in tokens}
    return tuple(t for t in COMPANION_ROMS if t in named) or tuple(COMPANION_ROMS)


def row_inputs(row, lane, root=None):
    """{key: path} of the NON-git inputs that can change the row's outcome (git sees the rest)."""
    def at(rel):
        return os.path.join(lane, rel)
    rid = row.id
    if is_rand_row(rid):
        return rand_row_inputs(rid, lane, row)
    if is_companion_row(rid):
        return companion_row_inputs(_companion_row_titles(rid), lane)
    if is_expansion_row(rid):
        return {"rom:exp": at(STAGED["exp"]),
                "probe:exp": expansion_env(lane)["SLINK_EXPANSION_PROBE_OBJECT"],
                **{f"artifact:exp:{name}": at(f".cache/expansion-output/reference/{name}")
                   for name in ("pokeemerald.sym", "pokeemerald.map", "pokeemerald.elf", "receipt.json")},
                **{f"fixture:exp:{side}:{kind}": at(f"tests/fixtures/gen3/exp_{kind}{side}.sav")
                   for side in ("", "_b") for kind in ("pc", "catch")}}
    if rid.endswith(("_fr_as_a", "_lg_as_a")):
        return {f"rom:{t}": at(STAGED[t]) for t in ("firered", "leafgreen")}
    m = re.match(r"(states|tutorials|checkpoint|bootcheck)_(firered|leafgreen|emerald)", rid)
    if m:
        out = {f"rom:{m[2]}": at(STAGED[m[2]])}
        if m[1] == "checkpoint":     # exactly the states its title's builds produce
            for kind in CHECKPOINT_BUILD_KINDS.get(m[2], ("town", "battle", "trainer", "tutorials")):
                for n in BUILD_OUTPUTS[kind]:
                    out[f"state:{BUILD_DIRS[kind]}/{n}"] = at(f"patch/build/{BUILD_DIRS[kind]}/{m[2]}/{n}")
        return out
    if rid.endswith("_rr_as_a"):      # the RR companion build the duo row boots (the receipt must name its hash)
        return {"rom:slink_RR": at("patch/build/slink_RR.gba")}
    if rid == "probe_gates":   # tests/live/test_gen3_probe_gates.py: the RR build + the root FR dump
        return {"rom:slink_RR": at("patch/build/slink_RR.gba"),
                "rom:firered_root": os.path.join(root or main_checkout(), ROOT_DUMPS["firered"])}
    if rid == "item6_route_diff":
        return {f"input:{p}": at(p) for p in ITEM6_INPUTS if p.startswith("patch/")}
    return {}


def file_sha256(path):
    return file_digest(path, "sha256")


def hash_inputs(paths):
    return {k: file_sha256(p) or "MISSING" for k, p in paths.items()}


def resume_inputs_problem(row, receipt_text, lane):
    """Why a prior receipt's `# inputs:` are not THIS lane's current non-git input hashes (so --resume must RUN the row),
    or None -- the same rule carry_decision applies to a carried receipt."""
    now, then = hash_inputs(row_inputs(row, lane)), parse_inputs(receipt_text)
    differ = sorted(k for k in set(now) | set(then) if now.get(k) != then.get(k))
    if not differ:
        return None
    return f"non-git inputs differ or unrecorded since the prior receipt ({', '.join(differ[:3])})"


def inputs_note(hashes):
    return "inputs: " + (" ".join(f"{k}={v}" for k, v in sorted(hashes.items())) or "(none)")


def parse_inputs(text):
    m = re.search(r"^# inputs: (.*)$", text, re.M)
    if not m or m[1] == "(none)":
        return {}
    return dict(kv.split("=", 1) for kv in m[1].split() if "=" in kv)


def _glob_re(glob):
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            out += {"*": "[^/]*", "?": "[^/]"}.get(glob[i], re.escape(glob[i]))
            i += 1
    return re.compile(out + r"\Z")


def touched(changed, deps):
    """The changed paths that match any dependency glob."""
    pats = [_glob_re(g) for g in deps]
    return [f for f in changed if any(p.match(f) for p in pats)]


@dataclass
class Evidence:
    row: str
    receipt: str               # basename under docs/gen3/probes/ (a carry's ORIGIN)
    cut: str | None            # the full sha the receipt was taken at (None: not citable)
    passed: bool               # one unambiguous final PASS (never SKIP-ALLOWED)
    seconds: float | None = None
    master: str | None = None  # item 6 only: the master sha8 its baseline side ran at
    inputs: dict = field(default_factory=dict)   # non-git input hashes the receipt recorded


_SHA = r"[0-9a-f]{40}"
_TITLE = {"fr": "firered", "lg": "leafgreen"}
_GAME = {"fr": "gen3_frlg", "lg": "gen3_lgfr"}


def _one_sha(text, keys):
    """The one sha the receipt names under `keys` (source=/sha=/lane=), else None."""
    shas = set(re.findall(rf"\b(?:{'|'.join(keys)})=({_SHA})\b", text))
    return shas.pop() if len(shas) == 1 else None


def _seconds(text, pass_re=None):
    """Summed start_utc..end_utc spans (only spans that show `pass_re`, if given)."""
    total = None
    for sec in re.split(r"(?=start_utc=)", text)[1:]:
        start, end = re.match(r"start_utc=(\S+)", sec), re.search(r"end_utc=(\S+)", sec)
        if end and (pass_re is None or pass_re.search(sec)):
            total = (total or 0) + (parse_utc(end[1]) - parse_utc(start[1])).total_seconds()
    return total


def _identity_roms(text):
    """{rom:<title>: sha256} from e2e_duo's IDENTITY lines; {} when absent or inconsistent."""
    seen = set()
    for line in re.findall(r"IDENTITY (a=\S+ b=\S+)", text):
        seen.add(tuple(re.findall(r"[ab]=(\w+):rom=([0-9a-f]{64})", line)))
    if len(seen) != 1:
        return {}
    return {f"rom:{t}": h for t, h in seen.pop()}


def duo_verdict_ok(text, scenario, orient):
    """One unambiguous final PASS for this scenario and orientation: every summary line for the
    scenario says PASS (a later FAIL/SKIP anywhere in the file makes it not citable), every
    recorded exit is 0, and every IDENTITY names A as the orientation's title."""
    verdicts = re.findall(rf"^  {re.escape(scenario)}: (PASS|FAIL|SKIP)\b", text, re.M)
    exits = re.findall(r"^exit=(\S+)$", text, re.M)
    a_titles = set(re.findall(r"IDENTITY a=(\w+):", text))
    games = set(re.findall(rf"^=== {re.escape(scenario)} --game (\S+)", text, re.M))
    return (bool(verdicts) and set(verdicts) == {"PASS"} and all(e == "0" for e in exits)
            and a_titles == {_TITLE[orient]} and games <= {_GAME[orient]})


def fc_check(name, text, probes, depth=0, lane=None):
    """(header, ok, why) for a runner receipt: the file name, header row and cut agree; a PASS,
    SKIP-ALLOWED or FAIL has well-formed attempt blocks whose last one supports the verdict; a
    CARRIED one cites an origin that is itself a citable PASS for the same row at the cited cut.
    A companion row's evidence is judged against the CUT's own companion pins (the `lane` checkout when given, else
    `git show <cut>:...`), never the main checkout's: a differing main gen3_companions.json cannot vouch for it."""
    m = re.fullmatch(r"fc_(.+)_([0-9a-f]{8})\.txt", name)
    hdr = receipts.parse_run_receipt(text)
    if not (m and hdr):
        return hdr, False, "not a runner receipt"
    if hdr["row"] != m[1] or not re.fullmatch(_SHA, hdr["cut"] or "") or \
            not hdr["cut"].startswith(m[2]):
        return hdr, False, "header row/cut disagree with the file name"
    v = hdr["verdict"]
    expansion = is_expansion_row(hdr["row"])
    if expansion and v.startswith(("CARRIED", "CACHED", "SKIP-ALLOWED")):
        return hdr, False, "expansion qualification must run at the exact cut"
    if is_companion_row(hdr["row"]) and v.startswith(("CARRIED", "CACHED", "SKIP-ALLOWED")):
        return hdr, False, "companion qualification must run at the exact cut"
    c = re.match(rf"CARRIED from (\S+) @({_SHA})", v)
    if c:
        return (hdr, *origin_ok(c[1], hdr["row"], c[2], probes, depth + 1))
    c = re.match(rf"CACHED key=([0-9a-f]{{64}}) from (\S+) @({_SHA})$", v)
    if c:
        meta = cache_lookup(c[1])
        if not meta or meta.get("row") != hdr["row"] or meta.get("receipt") != c[2] or \
                meta.get("cut") != c[3]:
            return hdr, False, f"cache entry {c[1][:12]} missing or not this build's"
        return (hdr, *origin_ok(c[2], hdr["row"], c[3], probes, depth + 1))
    heads = re.findall(r"^--- attempt \d+ of \d+ ---$", text, re.M)
    ends = re.findall(r"^exit=(\S+) end_utc=\S+ tracked_clean_after=(\w+) classification=(\S+)$",
                      text, re.M)
    if not heads or len(heads) != len(ends):
        return hdr, False, "no well-formed attempt blocks"
    rc, clean, cls = ends[-1]
    if v.startswith(("PASS", "SKIP-ALLOWED")) and not (
            cls == "pass" and clean == "True" and (rc == "0" or v.startswith("SKIP-ALLOWED"))):
        return hdr, False, "the last attempt does not support the verdict"
    if is_companion_row(hdr["row"]) and v.startswith("PASS"):
        problem = companion_attempt_problem(hdr["row"], re.split(r"^--- attempt \d+ of \d+ ---$", text, flags=re.M)[-1],
                                            gen3_companion_pins(lane) if lane else gen3_companion_pins_at(hdr["cut"]))
        if problem:
            return hdr, False, "companion evidence: " + problem
    if expansion and v.startswith("PASS"):
        last = re.split(r"^--- attempt \d+ of \d+ ---$", text, flags=re.M)[-1]
        if not re.search(r"^start_utc=\S+ tracked_clean_before=True$", last, re.M):
            return hdr, False, "expansion lane was not clean before its final attempt"
        problem = expansion_attempt_problem(hdr["row"], hdr["cut"], last)
        if problem:
            return hdr, False, problem
    return hdr, True, ""


def origin_ok(name, row, x, probes, depth):
    """A carry's origin must exist, be a citable PASS for the same row at the cited cut X."""
    if depth > 8:
        return False, "carry chain too deep"
    path = os.path.join(probes, name)
    if os.path.basename(name) != name or not os.path.isfile(path):
        return False, f"origin {name} missing"
    ev = receipt_evidence(name, _read(path), probes, depth)
    if not ev or ev.row != row or ev.cut != x or not ev.passed:
        return False, f"origin {name} is not a citable PASS for {row} @{x[:8]}"
    return True, ""


def receipt_evidence(name, text, probes=None, depth=0):
    """Map one receipt file to Evidence (row, cut, citable PASS?, duration, inputs), or None.
    The shapes in the tree: fc_<row>_<cut8> (this runner), ph_<scenario>_<fr|lg>_as_a_<cut8>
    (G4-LANE-2), duo_frlg_<scenario>[_clean]_<date>, {center_controls,save_then_write}_<o>_as_a_*,
    center_receipt_whiteout_<o>_as_a_*, and, for durations only, checkpoint_<fr|lg>_clean_* and
    mkstates_gen3_<title>_<kind>_*. A receipt that names no single cut sha is not citable."""
    probes = probes or PROBES
    m = re.fullmatch(r"fc_(.+)_[0-9a-f]{8}\.txt", name)
    if m and not name.startswith("fc_SUMMARY_"):
        hdr, ok, _why = fc_check(name, text, probes, depth)
        if not ok:
            return Evidence(m[1], name, None, False)
        v = hdr["verdict"]
        c = re.match(rf"CARRIED from (\S+) @({_SHA})", v)
        if c:   # a carry cites its (validated) origin, never itself
            return Evidence(hdr["row"], c[1], c[2], True, inputs=parse_inputs(text))
        mm = re.search(r"on master \(([0-9a-f]{8})\)", text)
        return Evidence(hdr["row"], name, hdr["cut"], v.startswith("PASS"), _seconds(text),
                        mm[1] if mm else None, parse_inputs(text))
    m = re.fullmatch(r"ph_(.+)_(fr|lg)_as_a_[0-9a-f]{8}\.txt", name)
    if m:
        scen, o = m[1], m[2]
        notes = re.findall(r"^note: (.*)$", text, re.M)
        passed = len(notes) == 1 and notes[0].startswith("PASS") and duo_verdict_ok(text, scen, o)
        secs = _seconds(text, re.compile(rf"^  {re.escape(scen)}: PASS", re.M))
        note = re.search(r"~(\d+) min", notes[0]) if notes else None
        if secs is None and note:
            secs = int(note[1]) * 60
        return Evidence(f"{scen}_{o}_as_a", name, _one_sha(text, ("sha", "source")), passed,
                        secs, inputs=_identity_roms(text))
    for pat, scen_of in (
            (r"duo_frlg_(.+?)_(?:clean_)?\d{4}-\d\d-\d\d[a-z]?\.txt", lambda m: (m[1], "fr")),
            (r"(center_controls|save_then_write)_(fr|lg)_as_a_.*\.txt",
             lambda m: (m[1] + "_gen3", m[2])),
            (r"center_receipt_whiteout_(fr|lg)_as_a_.*\.txt", lambda m: ("whiteout_gen3", m[1]))):
        m = re.fullmatch(pat, name)
        if m:
            scen, o = scen_of(m)
            return Evidence(f"{scen}_{o}_as_a", name, _one_sha(text, ("source",)),
                            duo_verdict_ok(text, scen, o), inputs=_identity_roms(text))
    m = re.fullmatch(r"mkstates_gen3_(firered|leafgreen)_(town|battle|trainer)_.*\.txt", name)
    if m:
        g = re.search(r"\[gate\] \S+: RESULT: PASS.*\((\d+)s\)", text)
        return Evidence(f"states_{m[1]}_{m[2]}", name, None, False, int(g[1]) if g else None)
    return None


def collect_evidence(probes=None):
    """{row: [Evidence, ...]} over every receipt in docs/gen3/probes/."""
    probes = probes or PROBES
    out = {}
    for name in sorted(os.listdir(probes)):
        if name.endswith(".txt"):
            ev = receipt_evidence(name, _read(os.path.join(probes, name)), probes)
            if ev:
                out.setdefault(ev.row, []).append(ev)
    return out


@dataclass
class Decision:
    kind: str                       # "CARRY" or "RUN"
    reason: str
    evidence: Evidence | None = None
    checked: list = field(default_factory=list)   # the diff X..cut that was checked
    inputs: dict = field(default_factory=dict)    # this lane's non-git input hashes
    cache: tuple | None = None      # §1 build rows: (key, manifest, meta or None)


def git_is_ancestor(x, cut):
    return subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", x, cut],
                          capture_output=True).returncode == 0


def carry_decision(row, cut, evidence, diff_names, master_sha=None, ancestor=None, inputs=None):
    """CARRY when some citable PASS receipt at a cut X != `cut` has X an ancestor of `cut`, the
    same non-git input hashes as `inputs` (this lane's), and a diff X..cut that touches none of
    the row's dependency globs; RUN otherwise, with the reason. `diff_names(x, cut)` returns the
    changed paths, or None when x is not in the repo. Receipts at `cut` itself are resume
    material, not carry material."""
    ancestor = ancestor or git_is_ancestor
    inputs = inputs or {}
    if row.deps is None:
        return Decision("RUN", "never carried (built/checked at the cut itself)")
    missing = sorted(k for k, v in inputs.items() if v == "MISSING")
    if missing:
        return Decision("RUN", f"non-git input missing in this lane: {', '.join(missing)}")
    cands = [e for e in evidence if e.passed and e.cut and e.cut != cut]
    if not cands:
        return Decision("RUN", "no citable PASS receipt")
    blocked = []
    for e in cands:
        if row.id == "item6_route_diff" and not (e.master and master_sha
                                                  and master_sha.startswith(e.master)):
            blocked.append(f"{e.receipt}: master moved or unrecorded")
            continue
        if not ancestor(e.cut, cut):
            blocked.append(f"{e.receipt}: {e.cut[:8]} is not an ancestor of the cut")
            continue
        differ = sorted(k for k, v in inputs.items() if e.inputs.get(k) != v)
        if differ:
            blocked.append(f"{e.receipt}: non-git inputs differ or unrecorded "
                           f"({', '.join(differ[:3])})")
            continue
        changed = diff_names(e.cut, cut)
        if changed is None:
            blocked.append(f"{e.receipt}: cut {e.cut[:8]} not in this repo")
            continue
        hit = touched(changed, row.deps)
        if not hit:
            return Decision("CARRY", f"CARRIED from {e.receipt} @{e.cut}", e, changed, inputs)
        blocked.append(f"{e.receipt} @{e.cut[:8]}: {', '.join(hit[:3])}"
                       f"{f' (+{len(hit) - 3})' if len(hit) > 3 else ''}")
    return Decision("RUN", "cannot carry -- " + "; ".join(blocked))


_DIFFS = {}


def git_diff_names(x, cut):
    if (x, cut) not in _DIFFS:
        p = subprocess.run(["git", "-C", REPO, "diff", "--name-only", x, cut],
                           capture_output=True, text=True)
        _DIFFS[x, cut] = p.stdout.split() if p.returncode == 0 else None
    return _DIFFS[x, cut]


def estimate_seconds(evidence, cut):
    """The row's historical duration: the longest recorded span among its receipts at other
    cuts (receipts at `cut` are left out, so two shards compute the same estimate)."""
    spans = [e.seconds for e in evidence if e.seconds and e.cut != cut]
    return max(spans) if spans else None


def carried_receipt(row, cut, lane, d):
    """The CARRIED row's fc receipt: the cited receipt, its cut, the checked diff and inputs."""
    deps = row.deps or []
    x = d.evidence.cut
    note = (f"CARRIED from {d.evidence.receipt} @{x}; diff {x[:8]}..{cut[:8]} touches no "
            f"dependency (list checked)\n"
            f"dependencies checked ({len(deps)}): {' '.join(deps)}\n"
            f"diff {x[:8]}..{cut[:8]} ({len(d.checked)} paths, none a dependency): "
            f"{' '.join(d.checked) or '(empty)'}\n" + inputs_note(d.inputs))
    return receipts.run_receipt_text(row=row.id, item=row.item.replace(" ", "_"), cut=cut,
                                     lane=lane, command=row.command(), cwd=row.cwd, env=row.env,
                                     attempts=[], verdict=d.reason, note=note)


# ---------------------------------------------------------------------------
# the §1 build cache (card G4-FINALCUT-CACHE): a state build is content-addressed. Its key is
# the sha256 of every input -- the git blobs AT THE CUT of the builder scripts, the Lua drivers,
# the packs and syms they read, and the row's own fixture; the staged ROM's sha256; the BizHawk
# exe/core hashes and the GBA config fields that can change emulation. A hit copies the cached
# .State files into the lane and writes a CACHED receipt citing the original build receipt and
# the key; a miss builds live and populates the cache. The checkpoint probe then runs on
# known-hash states, which is what makes it carry-eligible.
# ---------------------------------------------------------------------------

# the tool docstrings: mkstates_gen3.py (town/battle) and mkstates_gen3_tutorials.py
BUILD_OUTPUTS = {"town": ["slink_overworld.State", "slink_door.State", "slink_script.State"],
                 "battle": ["slink_preintro.State", "slink_prebattle.State",
                            "slink_postbattle.State"],
                 "trainer": ["slink_pretrainer.State", "slink_prefaint.State"],
                 "tutorials": ["slink_oldman.State", "slink_pokedude.State"]}
BUILD_DIRS = {"town": "gen3_probe_states_c4p2", "battle": "gen3_probe_states_c4p2",
              "trainer": "gen3_probe_states_c4p2",
              "tutorials": "gen3_probe_states"}
BUILD_FIXTURE = {"town": "town", "battle": "battle", "trainer": "town", "tutorials": "town"}
# what the builders read (mkstates_gen3*.lua -> gen3_boot_check / gen3_scripted_play / playlib /
# gen3_title_syms / lua/gen3/reads.lua / json_codec, the title's profile.json via PROFILE_PACK;
# gen3_fixtures.py / run_gate.py / gen1_playthrough.py launch and configure the run)
BUILD_KEY_GLOBS = ["tools/mkstates_gen3.py", "tools/mkstates_gen3_tutorials.py",
                   "tools/gen3_fixtures.py", "tools/run_gate.py", "tools/gen1_playthrough.py",
                   "lua/tests/mkstate*.lua", "lua/tests/gen3_*.lua", "lua/tests/playlib.lua",
                   "lua/json_codec.lua", "lua/gen3/**", "data/games/gen3_frlg/**",
                   "data/games/gen3_frlge/**", "data/gen3/pret/**", "server/adapters/**"]
_MGBA = "BizHawk.Emulation.Cores.Nintendo.GBA.MGBAHawk"


# card E4b-CKPT review (OMP cx-6b619663 F7): the per-title kinds a checkpoint row's builds cover.
# FR/LG: town/battle (states_*) + tutorials (a separate tutorials_* row, oldman/pokedude). Emerald
# has no tutorials row at all -- its third build is states_emerald_trainer.
CHECKPOINT_BUILD_KINDS = {"firered": ("town", "battle", "trainer", "tutorials"),
                          "leafgreen": ("town", "battle", "trainer", "tutorials"),
                          "emerald": ("town", "battle", "trainer")}


def checkpoint_build_row_id(title, kind):
    return f"tutorials_{title}" if kind == "tutorials" else f"states_{title}_{kind}"


def build_kind(row_id):
    """(kind, title) of a §1 build row -- kind town / battle / trainer / tutorials -- else None."""
    m = re.fullmatch(r"states_(firered|leafgreen|emerald)_(town|battle|trainer)", row_id)
    if m:
        return m[2], m[1]
    m = re.fullmatch(r"tutorials_(firered|leafgreen)", row_id)   # no tutorials_emerald row exists
    return ("tutorials", m[1]) if m else None


_BLOBS = {}


def tree_blobs(cut):
    """{path: blob sha} of every file at `cut` (git ls-tree), memoised per cut."""
    if cut not in _BLOBS:
        out = _git(REPO, "ls-tree", "-r", cut).stdout
        _BLOBS[cut] = {ln.split("\t", 1)[1]: ln.split()[2] for ln in out.splitlines() if "\t" in ln}
    return _BLOBS[cut]


def bizhawk_fingerprint(exe=None, config=None):
    """The emulator side of a build key: EmuHawk.exe and the mGBA core's hashes, and a hash of
    the base config's GBA fields (preferred core, mGBA settings and sync settings). Window
    positions, recent files and the like are left out on purpose."""
    import run_gate
    exe = exe or run_gate.EMUHAWK
    config = config or run_gate.BIZHAWK_CONFIG
    base = os.path.dirname(exe)
    fp = {"EmuHawk.exe": file_sha256(exe) or "MISSING"}
    for rel in ("dll/mgba.dll", "dll/libmgba.dll.so"):
        h = file_sha256(os.path.join(base, rel))
        if h:
            fp[rel] = h
    try:
        with open(config, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    subset = {"PreferredCores.GBA": (cfg.get("PreferredCores") or {}).get("GBA"),
              "CoreSettings": (cfg.get("CoreSettings") or {}).get(_MGBA),
              "CoreSyncSettings": (cfg.get("CoreSyncSettings") or {}).get(_MGBA)}
    fp["config"] = hashlib.sha256(json.dumps(subset, sort_keys=True).encode()).hexdigest()
    return fp


def build_key(row, cut, lane, blobs=None, bizhawk=None):
    """(key, manifest) for a §1 build row, or (None, why) when an input is unavailable."""
    if is_companion_row(row.id):
        return None, "companion states are never cached (the cache keys the clean STAGED ROM)"
    kind, title = build_kind(row.id)
    blobs = tree_blobs(cut) if blobs is None else blobs
    pats = [_glob_re(g) for g in BUILD_KEY_GLOBS]
    # Emerald fixtures are emerald_<kind>.sav (tools/gen3_fixtures.py PARTY_TITLES["emerald"]
    # "fixture", tools/mkstates_gen3.py) -- no "_party_" infix, and "trainer" is its OWN fixture,
    # not remapped to "town" the way BUILD_FIXTURE does for FR/LG's shared town battery.
    fixture = (f"tests/fixtures/gen3/{title}_{kind}.sav" if title == "emerald" else
               f"tests/fixtures/gen3/{title}_party_{BUILD_FIXTURE[kind]}.sav")
    if fixture not in blobs:
        return None, f"fixture {fixture} not in the cut"
    rom = file_sha256(os.path.join(lane, STAGED[title]))
    if not rom:
        return None, f"staged ROM {STAGED[title]} missing in the lane"
    manifest = {"row": row.id, "rom": rom,
                "git": {p: b for p, b in blobs.items() if p == fixture or any(x.match(p) for x in pats)},
                "bizhawk": bizhawk_fingerprint() if bizhawk is None else bizhawk}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(), manifest


def state_cache_root():
    """Outside git, in the MAIN checkout, shared by every lane."""
    return os.path.join(main_checkout(), "patch", "build", "state_cache")


def cache_lookup(key):
    """The cache entry's meta for `key`, or None -- only when every stored file still hashes
    to what was recorded."""
    d = os.path.join(state_cache_root(), key)
    try:
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    files = meta.get("files") or {}
    if meta.get("key") != key or not files or any(
            file_sha256(os.path.join(d, n)) != h for n, h in files.items()):
        return None
    return dict(meta, dir=d)


def cache_store(key, manifest, row, cut, out_dir, since):
    """After a live PASS: copy the row's outputs (each must exist and be written since `since`)
    into state_cache/<key>/ with a meta.json naming the build receipt. False if incomplete."""
    kind, _title = build_kind(row.id)
    srcs = [os.path.join(out_dir, n) for n in BUILD_OUTPUTS[kind]]
    if not all(os.path.isfile(p) and os.path.getmtime(p) >= since for p in srcs):
        return False
    final = os.path.join(state_cache_root(), key)
    tmp = final + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for p in srcs:
        shutil.copyfile(p, os.path.join(tmp, os.path.basename(p)))
    meta = {"key": key, "row": row.id, "cut": cut,
            "receipt": os.path.basename(receipt_path(row.id, cut)),
            "built_utc": f"{utcnow():%Y-%m-%dT%H:%M:%SZ}", "manifest": manifest,
            "files": {os.path.basename(p): file_sha256(os.path.join(tmp, os.path.basename(p)))
                      for p in srcs}}
    with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    shutil.rmtree(final, ignore_errors=True)
    os.replace(tmp, final)
    return True


def cache_restore(meta, out_dir):
    """Copy a cache entry's files into the lane; True when every copy hashes as recorded."""
    os.makedirs(out_dir, exist_ok=True)
    for n, h in meta["files"].items():
        dst = os.path.join(out_dir, n)
        shutil.copyfile(os.path.join(meta["dir"], n), dst)
        if file_sha256(dst) != h:
            return False
    return True


def cached_receipt(row, cut, lane, key, meta):
    note = (f"cache: {meta['dir']}\n"
            f"outputs: {' '.join(f'{n}={h}' for n, h in sorted(meta['files'].items()))}\n"
            f"key manifest: {json.dumps(meta.get('manifest'), sort_keys=True)}\n"
            + inputs_note(hash_inputs(row_inputs(row, lane))))
    return receipts.run_receipt_text(
        row=row.id, item=row.item.replace(" ", "_"), cut=cut, lane=lane, command=row.command(),
        cwd=row.cwd, env=row.env, attempts=[],
        verdict=f"CACHED key={key} from {meta['receipt']} @{meta['cut']}", note=note)


def predicted_checkpoint_inputs(row, cut, lane):
    """The checkpoint row's input hashes as they WILL be once its title's three builds are
    restored from the cache, or None when any of them misses (then it is rebuilt live and
    its states' hashes cannot be known in advance)."""
    if is_companion_row(row.id):
        return None
    title = row.id[len("checkpoint_"):]
    out = {f"rom:{title}": file_sha256(os.path.join(lane, STAGED[title])) or "MISSING"}
    for kind in CHECKPOINT_BUILD_KINDS.get(title, ("town", "battle", "trainer", "tutorials")):
        rid = checkpoint_build_row_id(title, kind)
        key, _m = build_key(Row(rid, "§1", [], lane, 0), cut, lane)
        meta = cache_lookup(key) if key else None
        if not meta:
            return None
        for n, h in meta["files"].items():
            out[f"state:{BUILD_DIRS[kind]}/{n}"] = h
    return out


# ---------------------------------------------------------------------------
# --shard i/n: two runner instances split the RUN rows across two lanes
# ---------------------------------------------------------------------------

def parse_shard(text):
    i, n = (int(x) for x in text.split("/"))
    if not 1 <= i <= n:
        raise SystemExit(f"--shard {text}: need 1 <= i <= n")
    return i, n


def chain_of(row_id):
    """Rows that must share a shard, in plan order: a title's state/tutorial builds feed its
    checkpoint probe (SLINK_STATE_DIR, the bw hashes), and zip_build feeds zip_check/zip_boot.
    The zip test is by shape (endswith zip_build/zip_check, or the zip_boot_ prefix), not a fixed
    prefix list, so a new title's zip_rows() prefix (card E4b-CKPT review F2: emerald_zip_build/
    emerald_zip_check were missed by the old startswith(("zip_", "rr_zip_")) check and each fell
    back to ITS OWN id, i.e. a singleton chain, scattering them from zip_boot_emerald) is covered
    without another per-title branch here."""
    m = re.match(r"(?:states|tutorials|checkpoint)_(firered|leafgreen|emerald)", row_id)
    if m:
        return f"probe_{m[1]}"
    return "zip" if row_id.endswith(("zip_build", "zip_check")) or \
        row_id.startswith(("zip_boot_", "frlgc_zip_boot_", RAND_ROW_PREFIX + "zip_boot_")) else row_id


def shard_rows(rows, n, est):
    """Deterministic longest-first split of `rows` into n lists, with each prerequisite chain
    (chain_of) an atomic unit; plan order inside each shard, so a chain keeps its order. Every
    row lands in exactly one shard. `est` maps row id -> seconds."""
    order = {r.id: k for k, r in enumerate(rows)}
    units = {}
    for r in rows:
        units.setdefault(chain_of(r.id), []).append(r)
    loads, out = [0.0] * n, [[] for _ in range(n)]
    for unit in sorted(units.values(),
                       key=lambda u: (-sum(est[r.id] for r in u), order[u[0].id])):
        k = min(range(n), key=lambda j: (loads[j], j))
        out[k] += unit
        loads[k] += sum(est[r.id] for r in unit)
    return [sorted(o, key=lambda r: order[r.id]) for o in out]


def status_of(verdict):
    if verdict.startswith("CARRIED"):
        return "CARRIED"
    if verdict.startswith("CACHED"):
        return "CACHED"
    return "RUN" if verdict.startswith(("PASS", "SKIP-ALLOWED")) else "FAIL"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def plan_decisions(rows, cut, carry, lane):
    """({row id: Decision}, {row id: est seconds or None}). A §1 build row is CACHED on a cache
    hit, else RUN (built live, then cached); without --carry every other row RUNs."""
    ev = collect_evidence()
    est = {r.id: estimate_seconds(ev.get(estimate_twin(r.id), []), cut) or r.est for r in rows}   # a companion row: its clean twin's history; else the plan's own estimate
    master = primary_revision() if carry and any(r.id == "item6_route_diff" for r in rows) else None
    out = {}
    for r in rows:
        if build_kind(r.id):
            key, manifest = build_key(r, cut, lane)
            meta = cache_lookup(key) if key else None
            out[r.id] = (Decision("CACHED", f"cache hit key={key[:12]} (built by {meta['receipt']})",
                                  cache=(key, manifest, meta)) if meta else
                         Decision("RUN", f"cache miss ({f'key={key[:12]}' if key else manifest}): "
                                         f"build live, then cache", cache=(key, manifest, None)))
        elif not carry:
            out[r.id] = Decision("RUN", "no --carry")
        elif r.id.startswith("checkpoint_"):
            inputs = predicted_checkpoint_inputs(r, cut, lane)
            out[r.id] = (Decision("RUN", "its states are rebuilt live this pass (a build missed "
                                         "the cache), so their hashes are not known yet")
                         if inputs is None else
                         carry_decision(r, cut, ev.get(r.id, []), git_diff_names, master,
                                        git_is_ancestor, inputs))
        else:
            out[r.id] = carry_decision(r, cut, ev.get(r.id, []), git_diff_names, master,
                                       git_is_ancestor,
                                       hash_inputs(row_inputs(r, lane)) if r.deps else {})
    return out, est


def _mins(s):
    return f"{s / 60:.0f}m"


def rand_rom_inputs_problem(row, receipt_text):
    """Why a frlgcr_ receipt's recorded `rand:<file>` hashes are not the randomized ROMs present NOW (absent, or repointed to other
    bytes), or None. Lane-local inputs (overlays, the zip) are not compared: a merge may run in another lane than the row did."""
    now = {k: file_sha256(p) or "MISSING" for k, p in rand_row_inputs(row.id, "", row).items() if k.startswith("rand:")}
    then = parse_inputs(receipt_text)
    differ = sorted(k for k in now if now[k] != then.get(k))
    if not differ:
        return None
    return f"randomized ROM input absent or changed since the receipt ({', '.join(differ[:3])})"


def merge_summary(cut, rows, suffix=""):
    """--merge-summary: one fc_SUMMARY_<cut8>.txt from every row's receipt at `cut`, whichever
    shard (or lane) wrote it; a row with no receipt is NOT RUN, i.e. a failure."""
    results = []
    for row in rows:
        path = receipt_path(row.id, cut)
        name = os.path.basename(path)
        if not os.path.isfile(path):
            results.append((row, "NOT RUN (no receipt at this cut)", 0, "-"))
            continue
        text = _read(path)
        hdr, ok, why = fc_check(name, text, PROBES)
        if not ok or hdr["row"] != row.id or hdr["cut"] != cut:
            results.append((row, f"FAIL invalid receipt ({why or 'wrong row/cut'})", 0, name))
        elif is_rand_row(row.id) and hdr["verdict"].startswith(("PASS", "SKIP-ALLOWED")) and \
                (stale := rand_rom_inputs_problem(row, text)):
            results.append((row, f"FAIL {stale}", 0, name))
        else:
            results.append((row, hdr["verdict"], text.count("\n--- attempt "), name))
    path, ok = write_summary(cut, results, suffix, new_run_stamp(cut, suffix))
    print(_read(path))
    return 0 if ok else 1


# Patch-first (owner 2026-10-02): these plans stage CLEAN FireRed/LeafGreen/Emerald dumps as working clients, which
# Entry.admit_routed refuses at launch (a refusal that otherwise surfaces as a harness TimeoutError, retried for hours as
# "contention"). Their receipts are history; the live plan is --title frlgc. rr boots its companion and exp is exempt
# (gen3_exp has no companion_required), so neither is listed.
CLEAN_PLAN_TITLES = {"frlg": "FireRed/LeafGreen", "emerald": "Emerald"}


def clean_plan_refusal(title):
    """Why `--title <title>` cannot run live (a message naming the rule and the live plan), or None."""
    if title not in CLEAN_PLAN_TITLES:
        return None
    return (f"--title {title} stages CLEAN {CLEAN_PLAN_TITLES[title]} dumps, but a clean cartridge is refused at launch "
            f"(this cartridge needs the SLink companion patch; patch-first, owner 2026-10-02); its clean receipts are "
            f"history. Run the companion re-cut instead: --title frlgc (--dry-run/--list/--merge-summary still work).")


def run_pass(args):
    refusal = clean_plan_refusal(args.title)
    if refusal and not (args.dry_run or args.list or args.merge_summary):
        print(f"[final_cut] ABORT: {refusal}", file=sys.stderr)
        return 2
    root = main_checkout()
    lane = os.path.abspath(args.lane).replace("\\", "/")
    master = os.path.abspath(args.master).replace("\\", "/")
    try:
        # resolved HERE, not in the main checkout: `--cut HEAD` means this branch's head
        cut = _git(REPO, "rev-parse", f"{args.cut}^{{commit}}").stdout.strip()
    except LaneError:
        if not args.dry_run:
            raise
        cut = args.cut
    plan_fn = {"rr": build_plan_rr, "emerald": build_plan_emerald, "exp": build_plan_exp,
               "frlgc": build_plan_frlgc, "frlgc-rand": build_plan_frlgc_rand}.get(args.title, build_plan)
    rows = select_rows(plan_fn(cut, lane, master), args.rows)
    if args.list:
        for r in rows:
            print(f"{r.id:<48} {r.item}")
        return 0
    # fc_SUMMARY_<cut8>_rr.txt / _emerald.txt: FR's stays put
    title_sfx = {"rr": "_rr", "emerald": "_emerald", "exp": "_exp", "frlgc": "_frlgc", "frlgc-rand": "_frlgc_rand"}.get(args.title, "")
    # frlgc also needs the clean Emerald dump (companion-check recomposes from it) beside the three pinned companion ROMs
    emerald_pins = {"emerald": EMERALD_PINNED_INPUTS, "exp": EXPANSION_PINNED_INPUTS,
                    "frlgc": {**EMERALD_PINNED_INPUTS, **COMPANION_PINNED_INPUTS},
                    "frlgc-rand": {**EMERALD_PINNED_INPUTS, **COMPANION_PINNED_INPUTS}}.get(args.title)
    emerald_unpinned = {"emerald": EMERALD_UNPINNED_INPUTS, "exp": EXPANSION_UNPINNED_INPUTS,
                        "frlgc": EMERALD_UNPINNED_INPUTS, "frlgc-rand": EMERALD_UNPINNED_INPUTS}.get(args.title)
    blockers = rand_blockers(rows)
    if blockers and not (args.dry_run or args.merge_summary):
        for rid, why in blockers.items():
            print(f"[final_cut] ABORT: {rid}: {why}", file=sys.stderr)
        return 2
    if args.merge_summary:
        return merge_summary(cut, rows, title_sfx)
    decisions, est = plan_decisions(rows, cut, args.carry, lane)
    suffix, plan, mine = title_sfx, None, {r.id for r in rows}
    if args.shard:
        # cut over ALL selected rows (budget where no history), so two instances agree even when
        # one sees a warmer cache or newer receipts; each shard writes its own rows' receipts
        i, n = parse_shard(args.shard)
        plan = shard_rows(rows, n, {r.id: est[r.id] or r.budget for r in rows})
        mine = {r.id for r in plan[i - 1]}
        suffix = f"{title_sfx}_shard{i}of{n}"
    rows_here = [r for r in rows if r.id in mine]
    run_rows = [r for r in rows_here if decisions[r.id].kind == "RUN"]
    carry_rows = [r for r in rows_here if decisions[r.id].kind == "CARRY"]
    cached_rows = [r for r in rows_here if decisions[r.id].kind == "CACHED"]
    if args.dry_run:
        print(f"# G4 final cut {cut}  lane={lane}  master={master}  rows={len(rows)}"
              f"{f'  shard={args.shard}' if args.shard else ''}  carry={bool(args.carry)}")
        for step in provision_plan(lane, cut, emerald_pins, emerald_unpinned,
                                   base_inputs=args.title != "exp"):
            print(f"# provision: {step}")
        if args.title == "exp":
            print("# provision: link the locked, clean expansion source; copy the offline probe.o "
                  "(SLINK_EXPANSION_PROBE_OBJECT) plus compile.json when present; verify object hash vs facts")
            print("# Expansion production qualification; unmodified launcher admission required")
        if any(is_gate_row(r) for r in run_rows) and args.title != "exp":
            print(f"# provision: gate inputs (release_gate_quick's unit lane): {STAGED['exp']} (pin exp) + .cache/expansion-output/, "
                  f".cache/expansion-src junction (locked commit, tracked-clean), {VENDORED_DECOMP} junction")
        if any(r.id == "item6_route_diff" for r in run_rows):
            print(f"# provision: the same for {master} at primary branch {primary_revision()}")
        for k, r in enumerate(rows, 1):
            d, env = decisions[r.id], " ".join(f"{a}={b}" for a, b in r.env.items())
            where = "" if r.id in mine else "  [other shard]"
            e = est[r.id]
            print(f"[{k:02d}] {r.id}  {d.kind}{where}  ({r.item}, "
                  f"{'est ' + _mins(e) if e else 'no history, budget ' + _mins(r.budget)}, "
                  f"cwd={r.cwd})\n     $ {(env + ' ') if env else ''}{r.command()}\n"
                  f"     {d.reason}")
        for rid, why in blockers.items():
            print(f"# {rid}: {why}")
        if args.title == "frlgc-rand":
            for line in rand_estimate_lines(rows, est):
                print(line)
        known = [est[r.id] for r in run_rows if est[r.id]]
        unknown = [r for r in run_rows if not est[r.id]]
        print(f"# {len(rows)} rows: RUN {len(run_rows)} / CACHED {len(cached_rows)} / "
              f"CARRY {len(carry_rows)} (this shard)"
              f" -- lane time: {_mins(sum(known))} from {len(known)} rows' receipts + "
              f"{len(unknown)} rows with no history (budget ceiling "
              f"{_mins(sum(r.budget for r in unknown))})")
        for k, rs in enumerate(plan or [], 1):
            live = [r for r in rs if decisions[r.id].kind == "RUN"]
            secs = sum(est[r.id] or r.budget for r in live)
            print(f"# shard {k}/{len(plan)} ({len(rs)} rows, {len(live)} live, ~{_mins(secs)} "
                  f"with budget for rows without history): "
                  f"{' '.join(r.id + ('' if decisions[r.id].kind == 'RUN' else '=' + decisions[r.id].kind) for r in rs)}")
        print(f"# receipts docs/gen3/probes/fc_<row>_{cut[:8]}.txt, summary "
              f"fc_SUMMARY_{cut[:8]}{suffix}.txt")
        return 0
    deadline = parse_utc(args.stop_at).timestamp() if args.stop_at else None
    try:
        if run_rows or cached_rows:
            provision(lane, cut, root)
            if args.title == "exp":
                copy_expansion_inputs(lane, root)
            else:
                copy_inputs(lane, root, extra_pinned=emerald_pins, extra_unpinned=emerald_unpinned,
                            pins=rom_pins(lane, require_companions=True) if args.title in ("frlgc", "frlgc-rand") else None)
                if any(is_gate_row(r) for r in run_rows):
                    provision_gate_inputs(lane, root)
        if any(r.id == "item6_route_diff" for r in run_rows):
            provision(master, primary_revision(), root)
            copy_inputs(master, root, only=ITEM6_INPUTS)
    except LaneError as exc:
        print(f"[final_cut] ABORT: {exc}", file=sys.stderr)
        return 2
    results = []
    run_stamp = new_run_stamp(cut, suffix)      # this invocation's own summary copy, never overwritten by a later pass
    for row in rows_here:
        prior = prior_verdict(row.id, cut, lane)
        rec = os.path.basename(receipt_path(row.id, cut))
        if prior and prior.startswith("FAIL"):
            # never re-run an unchanged failed row automatically, and never paper over it
            results.append((row, f"{prior} (prior receipt at this cut; not re-run)", 0, rec))
        elif prior and (args.resume or decisions[row.id].kind in ("CARRY", "CACHED")) and \
                prior.startswith(("PASS", "SKIP-ALLOWED", "CARRIED", "CACHED")) and \
                not resume_inputs_problem(row, _read(receipt_path(row.id, cut)), lane):
            results.append((row, f"{prior} (resumed)", 0, rec))
        elif decisions[row.id].kind == "CARRY":
            with open(receipt_path(row.id, cut), "w", encoding="utf-8") as f:
                f.write(carried_receipt(row, cut, lane, decisions[row.id]))
            results.append((row, decisions[row.id].reason, 0, rec))
        elif decisions[row.id].kind == "CACHED" and \
                cache_restore(decisions[row.id].cache[2], row.outputs_dir):
            key, _manifest, meta = decisions[row.id].cache
            with open(receipt_path(row.id, cut), "w", encoding="utf-8") as f:
                f.write(cached_receipt(row, cut, lane, key, meta))
            results.append((row, f"CACHED key={key} from {meta['receipt']} @{meta['cut']}", 0, rec))
        elif deadline and time.time() >= deadline:
            results.append((row, "NOT RUN (--stop-at)", 0, "-"))
        else:
            t0 = time.time()
            verdict, n, clean_after = run_row(row, cut, lane, deadline)
            results.append((row, verdict, n, rec))
            cache = decisions[row.id].cache
            if cache and cache[0] and verdict == "PASS" and not cache_store(
                    cache[0], cache[1], row, cut, row.outputs_dir, t0 - 2):
                print(f"[final_cut] {row.id}: PASS but its outputs are incomplete; not cached",
                      file=sys.stderr)
            if not clean_after:
                write_summary(cut, results, suffix, run_stamp)
                print("[final_cut] ABORT: the lane went tracked-dirty", file=sys.stderr)
                return 2
        write_summary(cut, results, suffix, run_stamp)
    path, ok = write_summary(cut, results, suffix, run_stamp)
    print(f"\n[final_cut] summary: {path}")
    print(_read(path))
    return 0 if ok else 1


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    # Owner space rule: lanes live under SLINK_WORK_ROOT/lanes/gen3 (short names: BizHawk 240-char path budget);
    # without it the old Drive default stays, so nothing changes for a shell that has not set it.
    wt = (os.path.join(os.environ["SLINK_WORK_ROOT"], "lanes", "gen3") if os.environ.get("SLINK_WORK_ROOT")
          else os.path.join(main_checkout(), ".claude", "worktrees"))
    if argv[:1] == ["zip-boot"]:
        ap = argparse.ArgumentParser(prog="gen3_final_cut.py zip-boot")
        ap.add_argument("--zip", required=True)
        ap.add_argument("--lane", required=True)
        ap.add_argument("--timeout", type=int, default=300)
        ap.add_argument("--title", default="firered", choices=sorted(ZIP_BOOT))
        a = ap.parse_args(argv[1:])
        return zip_boot(a.zip, a.lane, a.timeout, a.title)
    if argv[:1] == ["stage-companions"]:
        ap = argparse.ArgumentParser(prog="gen3_final_cut.py stage-companions")
        ap.add_argument("--tree", default=main_checkout(),
                        help="the tree holding the clean dumps; the pinned companion ROMs are written under its patch/build")
        ap.add_argument("--title", action="append", choices=GEN3_COMPANION_TITLES)
        a = ap.parse_args(argv[1:])
        return stage_companions(a.tree, tuple(a.title or GEN3_COMPANION_TITLES))
    if argv[:1] == ["companion-check"]:
        ap = argparse.ArgumentParser(prog="gen3_final_cut.py companion-check")
        ap.add_argument("--lane", required=True)
        ap.add_argument("--title", required=True, choices=GEN3_COMPANION_TITLES)
        a = ap.parse_args(argv[1:])
        return companion_check(a.lane, a.title)
    if argv[:1] == ["item6"]:
        ap = argparse.ArgumentParser(prog="gen3_final_cut.py item6")
        ap.add_argument("--branch", required=True)
        ap.add_argument("--master", required=True)
        a = ap.parse_args(argv[1:])
        return item6(a.branch, a.master)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", required=True, help="the frozen cut (any rev; resolved to a sha)")
    ap.add_argument("--lane", default=os.path.join(wt, "gen3-lane-clean"))
    ap.add_argument("--master", default=os.path.join(wt, "gen3-lane-master"),
                    help="item 6's baseline tree, provisioned at the resolved primary branch")
    ap.add_argument("--title", default="frlg", choices=("frlg", "rr", "emerald", "exp", "frlgc", "frlgc-rand"),
                    help="frlg (default): the G4 FR/LG plan, unchanged. rr: the G5 Radical Red "
                         "plan (card G5-RUNNER-RR) -- the RR duo rows, the RR opcode gates, and the "
                         "zip build/check/boot on RR -- in place of it. emerald: the E4b plan "
                         "(docs/gen3_emerald/PLAN.md) -- SOURCE-lane generator checks, the "
                         "Emerald states/duo/bootcheck rows, the Emerald slice of probe-gates, "
                         "the shadow-negatives manifest check, and the zip build/check/boot on "
                         "Emerald. exp: build-specific source/unit checks, every applicable "
                         "expansion duo, ZIP build/check and test-only boot; production refused. frlgc: the PATCHED "
                         "(companion) FR/LG/Emerald re-cut -- staged-companion source check, companion cold-boot admission, "
                         "every FR/LG/Emerald duo row plus the clause / ball-gate / shiny rows run with --gen3-companion, "
                         "and the zip booted on each companion title; every row RUNs (65 rows, fc_SUMMARY_<cut8>_frlgc.txt). "
                         "frlgc-rand: the small opt-in randomized-COMPANION plan (13 rows: admission / link / nearby-trainer "
                         "panel on FR+LG and Emerald, the clean zip-boot refusal proofs; needs SLINK_GEN3_RAND_ROMS, "
                         "fc_SUMMARY_<cut8>_frlgc_rand.txt)")
    ap.add_argument("--rows", default=None, help="comma list of row-id globs or item tags")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; launch nothing")
    ap.add_argument("--list", action="store_true", help="print the selected row ids")
    ap.add_argument("--resume", action="store_true",
                    help="skip rows whose receipt at this cut already says PASS")
    ap.add_argument("--stop-at", default=None,
                    help="UTC time (ISO 8601): no row starts after it, a running row is killed")
    ap.add_argument("--carry", action="store_true",
                    help="CARRY a row whose PASS receipt at an earlier cut has no dependency in "
                         "the diff to --cut (owner-approved synthetic evidence)")
    ap.add_argument("--shard", default=None,
                    help="i/n: run only this instance's share of the RUN rows (use a distinct "
                         "--lane per shard); shard 1 also writes the CARRIED receipts")
    ap.add_argument("--merge-summary", action="store_true",
                    help="write fc_SUMMARY_<cut8>.txt from every row's receipt at --cut, then exit")
    try:
        return run_pass(ap.parse_args(argv))
    except LaneError as exc:
        print(f"[final_cut] ABORT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
