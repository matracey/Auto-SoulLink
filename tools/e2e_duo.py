#!/usr/bin/env python3
"""e2e_duo.py — TWO-INSTANCE headless E2E harness for the SLink companion patch.

Runs a throwaway SLink server + two concurrent EmuHawk instances (players a/b), both
booting a battery save seeded from the SAME source (instance B mutates its party OTIDs
pre-hello so mon keys don't collide in the server's flat key index), then orchestrates a
scenario via the server's debug HTTP API and waits for both instances' result files.

    python tools/e2e_duo.py --scenario faint
    python tools/e2e_duo.py --scenario all --keep-alive

Per instance: a generated stub (patch/build/duo_{a,b}.lua) bakes SLINK_HOST/PORT/PLAYER
plus the SLINK_DUO table and dofiles the row's Lua driver (for example,
lua/tests/duo/duo_gen3_main.lua for Gen 3), which runs the REAL production client and the
scenario coroutine (lua/tests/duo/scenario_<name>.lua).
Result protocol: patch/build/e2e_<scenario>_{a,b}_result.txt — incremental log lines,
"MYKEY <slot> <key>" markers, final "RESULT: PASS|FAIL".

Launch rules (hard-won): CWD = repo root with RELATIVE EmuHawk arg paths (absolute paths
containing the "Google Drive" space break BizHawk's CLI parser); absolute paths are fine
INSIDE Lua. Per-instance --config copies avoid the shared config.ini write race.
"""
import argparse
import contextlib
import glob
import hashlib
import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

if __package__:
    from . import gen3_expansion_faint_oracle as exp_faint_oracle
    from .duo_oracle_pipeline import EvidenceContract, run_pipeline, validate_pipeline
    from .gen2_trade_lane import SCENARIOS as GEN2_TRADE_SCENARIOS
    from .slink_space import work_root
else:
    import gen3_expansion_faint_oracle as exp_faint_oracle
    from duo_oracle_pipeline import EvidenceContract, run_pipeline, validate_pipeline
    from gen2_trade_lane import SCENARIOS as GEN2_TRADE_SCENARIOS
    from slink_space import work_root

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMUHAWK = "E:/Howard/Bizhawk/EmuHawk.exe"
BIZHAWK_CONFIG = "E:/Howard/Bizhawk/config.ini"
ROM_REL = "patch/build/slink_RR.gba"
BUILD = os.path.join(REPO, "patch", "build")
# Where --wire-log parks a run's golden transcripts (tests/fixtures/gen3/wire/README.md).
WIRE_FIXTURES = os.path.join(REPO, "tests", "fixtures", "gen3", "wire")
WT_FWD = REPO.replace("\\", "/")

# Per-scenario knobs: extra server flags, per-side timeout (seconds), fillers (default True;
# or {"a":…,"b":…} — explode keeps B at ONE mon so the Explosion self-faint whites out
# instead of opening the switch menu), and `games`.
#
# `games` is which titles a scenario applies to. ABSENT MEANS EVERY TITLE — read it through
# scenarios_for(), never inline.
#
# Matching is exact: an entry names the titles it runs. (The `("gen1",)` family that used to
# cover `gen1_yellow` went with those two titles in deletion step 3.)
#
# This key was declared and documented here for a long time while nothing but
# tests/e2e/test_duo.py read it, so `--scenario all` expanded to the whole table regardless of
# --game: Gen 3-only scenarios were run against a Game Boy, where they died on the savestate
# they declare and no GB fixture has.
SCENARIOS = {
    **{f"exp_static_{case}_gen3": {
        "flags": [], "timeout": 1200, "games": ("gen3_exp",),
        "target": f"static_{case}_synth", "frames": 2000000, "explicit_only": True,
        "scenario_module": "static_wild", "static_wild_case": case,
        "oracle": "assert_static_wild_gen3_saved",
    } for case in ("static", "static_run", "grass", "surf", "rock", "fish", "altering0", "altering1")},
    "gift_gen3": {"flags": ["--species-clause", "--gender-clause", "--type-clause"],
        "timeout": 1200, "games": ("gen3_frlg", "gen3_emerald", "gen3_rr", "gen3_exp"),
        "target": "gift_synth", "frames": 2000000, "explicit_only": True,
        "scenario_module": "gift_egg", "acquisition_kind": "gift", "oracle": "assert_gift_egg_gen3_saved"},
    "egg_hatch_gen3": {"flags": [], "timeout": 1800,
        "games": ("gen3_frlg", "gen3_emerald", "gen3_rr", "gen3_exp"), "target": "hatch_synth",
        "frames": 2500000, "explicit_only": True, "scenario_module": "gift_egg",
        "acquisition_kind": "hatch", "oracle": "assert_gift_egg_gen3_saved"},
    "egg_receive_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_exp",),
        "target": "egg_receive_synth", "frames": 2000000, "explicit_only": True,
        "scenario_module": "gift_egg", "acquisition_kind": "egg_receive", "oracle": "assert_gift_egg_gen3_saved"},
    "choice_gift_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_exp",),
        "target": "choice_gift_synth", "frames": 2000000, "explicit_only": True,
        "scenario_module": "gift_egg", "acquisition_kind": "gift", "acquisition_case": "choice_gift",
        "oracle": "assert_gift_egg_gen3_saved"},
    "gift_box_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_exp",),
        "target": "gift_box_synth", "frames": 2000000, "explicit_only": True,
        "scenario_module": "gift_egg", "acquisition_kind": "gift_box", "oracle": "assert_gift_egg_gen3_saved"},
    # FRLG-R4: clean-derived saves are disclosed SYNTH. Dependency preflight
    # refuses these rows before any server/emulator launch until CR-R1/CR-R2 and
    # server ingest are committed. Exactly one attempt; no borrowed vanilla PASS.
    "admit_randomized_frlg": {"flags": [], "timeout": 600, "games": ("gen3_frlg",),
        "target": "town", "frames": 1200000, "no_save": ("a", "b"), "gen3_rand": True, "explicit_only": True,
        "scenario_module": "rand_admit", "oracle": "assert_admit_randomized_frlg_saved"},
    "admit_randomized_emerald": {"flags": [], "timeout": 600, "games": ("gen3_emerald",),
        "target": "town", "frames": 1200000, "no_save": ("a", "b"), "gen3_rand": True, "explicit_only": True,
        "scenario_module": "rand_admit", "oracle": "assert_admit_randomized_frlg_saved"},
    # E-RAND-CATCH: E<->E reuses Emerald's own pre-existing 20-ball `catch` fixture (the
    # non-randomized rows' emerald_catch{,_b}.sav) rather than a new SYNTH file -- only FR/LG
    # needs the R4-LINK SYNTH catch_synth edit (2/4 Poke Balls in the battle save).
    "link_gen3_rand": {"flags": [], "timeout": 900, "games": ("gen3_frlg", "gen3_emerald"),
        "target": "catch_synth", "target_by_game": {"gen3_emerald": "catch"},
        "frames": 2000000, "gen3_rand": True, "explicit_only": True, "ball_hunt": True,
        # rng_attempts 6 (this scenario id only, so the Emerald twin has the same budget): the randomized FR/LG Route 1 tables make the catch a
        # coin flip -- measured ~40-44% failure per attempt (per-throw catch odds 6-33%; fc_frlgcr_link_gen3_rand_frlg_d5a26da9 failed 3/3 attempts,
        # the Emerald twin whited out once and passed on attempt 2); the predicted failure over 6 attempts is ~0.5%. The failure is "hunt ended
        # whiteout" before the catch, so more balls would not help; retryable_gen1_rng admits the attempts past 2 for that reason only.
        "rng_attempts": 6,
        "scenario_module": "rand_link", "oracle": "assert_link_gen3_rand_saved"},
    "trainer_panel_gen3_rand": {"flags": [], "timeout": 600, "games": ("gen3_frlg", "gen3_emerald"),
        "target": "trainer", "frames": 1200000, "gen3_rand": True, "explicit_only": True,
        "scenario_module": "rand_trainer_panel", "oracle": "assert_trainer_panel_gen3_rand_saved"},
    # LIVE PROBE (not a gate, in no plan): a companion pair where B's cartridge has ONE byte flipped inside a protected span, off every
    # anchor (gen3_probe_flip_choice). It records what the client and the server actually do with it -- admitted or refused, and why --
    # and passes on any recorded observation; it fails only when the harness itself breaks. Opt-in: --scenario probe_protected_span_flip_gen3.
    "probe_protected_span_flip_gen3": {"flags": [], "timeout": 300, "games": ("gen3_frlg", "gen3_lgfr", "gen3_emerald"),
        "target": "town", "frames": 600000, "no_save": ("a", "b"), "gen3_probe_flip": True, "explicit_only": True,
        "scenario_module": "probe_flip", "oracle": "assert_probe_protected_span_flip_gen3_saved"},
    # A boots {firered,leafgreen}_party_trainer.sav (6e85ddfc): CACHED-NATIVE at (41,45) on map
    # 1.0, one step west of Rick 102's sight line, Lv13 lead -- gen3_routes skips the T2 walk.
    "trainer_bench_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_frlg",),
        "target": {"a": "trainer", "b": "town"}, "frames": 2500000, "no_save": ("b",),
        "scenario_module": "battle_window", "battle_window_case": "trainer_bench", "battle_window_slot": 1,
        "oracle": "assert_trainer_bench_gen3_saved"},
    # A2 under mechanism P+H (owner rulings 15-18): the command-only single-subject row. A parks
    # with its lead active, gets ONE keyed force_faint, and the engine's own Perish KO faints it in
    # battle with no input; A sends out slot 1, RUNs and saves the engine-written HP 0. B idles.
    "active_end_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_frlg",),
        "target": {"a": "battle", "b": "town"}, "frames": 2000000, "no_save": ("b",),
        "scenario_module": "linked_faint_active", "active_faint_case": "command",
        "oracle": "assert_active_end_gen3_saved"},
    # Driver FOR CODEX 4: reset_commit's proposer outlives the server watchdog (~4000 events) before
    # party evidence settles it; every other trade leg keeps well over the driver's 150000 default.
    **{name: {"flags": [], "timeout": 3600 if name == "gen2_trade_reset_commit" else 2400, "games": ("gen2_new",),
              "no_setup": True, "artifact_kind": "overlay",
              "frames": {"a": 600000, "b": 432000} if name == "gen2_trade_reset_commit" else 432000,
              "oracle": "assert_gen2_trade_saved", "oracle_kwargs": {}}
       for name in sorted(GEN2_TRADE_SCENARIOS)},
    "link": {"flags": [], "timeout": 1200, "games": ("gen2_new",),
             "no_setup": True, "frames": 216000, "target": {"a": "battle", "b": "battle_ot2"},
             "oracle": "assert_gen2_link_saved", "oracle_kwargs": {}},
    "gen2_faint": {"flags": [], "timeout": 2400, "games": ("gen2_new",),
                   "no_setup": True, "frames": 432000,
                   "target": {"a": "battle", "b": "battle_ot2"},
                   "oracle": "assert_gen2_faint_saved", "oracle_kwargs": {}},
    # DUO-WAVE-C (drivers fe4feb73, oracles c1adc8e8): the faint body plus its own verdict facts
    **{name: {"flags": [], "timeout": 7200 if name == "gen2_poison" else 3600, "games": ("gen2_new",),
              "no_setup": True, "frames": 900000 if name == "gen2_poison" else 432000,
              "target": {"a": "battle", "b": "battle_ot2"},
              "oracle": f"assert_{name}_saved", "oracle_kwargs": {}}
       for name in ("gen2_whiteout", "gen2_pc_ops", "gen2_changebox", "gen2_poison", "gen2_whiteout_rebuild")},
    # DUO-WAVE-D (D-2): zero-Ball town fixtures, the errand played in the duo for the aide's natural Balls
    # (docs/gen2/reviews/DUO_WAVE_D_FACTS_2026-09-24.md section 1); C-C and G-S only (GEN2_BALL_GATE_FIXTURES).
    # DUO-WAVE-D (O-33): the four duos that start from a synthetic setup (GEN2_SYNTH_SCENARIOS), C-C and G-S only
    **{name: {"flags": [], "timeout": 1800, "games": ("gen2_new",), "no_setup": True, "frames": 216000,
              "oracle": "assert_gen2_synth_saved", "oracle_kwargs": {}}
       for name in ("gen2_boxed_capture", "gen2_gift", "gen2_egg_hatch", "gen2_npc_trade", "gen2_evolution")},
    "gen2_ball_gate": {"flags": [], "timeout": 7200, "games": ("gen2_new",),
                       "no_setup": True, "frames": 600000,
                       "target": {"a": "town", "b": "town_ot2"},
                       "oracle": "assert_gen2_ball_gate_saved", "oracle_kwargs": {}},
    "gen2_faint_active": {"flags": [], "timeout": 3000, "games": ("gen2_new",),
                          "no_setup": True, "frames": 432000,
                          "target": {"a": "battle", "b": "battle_ot2"},
                          "oracle": "assert_gen2_faint_active_saved", "oracle_kwargs": {}},
    # O-30 review MINOR-5: B's linked mon dies as the active battler of a Route 30 TRAINER battle (the trainer
    # next-mon path); errand seeds (GEN2_TRAINER_FIXTURES), C-C and G-S cover all three titles
    "gen2_faint_active_trainer": {"flags": [], "timeout": 3600, "games": ("gen2_new",),
                                  "no_setup": True, "frames": 432000,
                                  "target": {"a": "battle_errand", "b": "battle_ot2_errand"},
                                  "oracle": "assert_gen2_faint_active_trainer_saved", "oracle_kwargs": {}},
    "gen2_admit_wrong_rom": {"flags": [], "timeout": 1200, "games": ("gen2_new",),
                             "no_setup": True, "frames": 216000,
                             "oracle": "assert_gen2_admit_wrong_rom", "oracle_kwargs": {}},
    "gen2_reconnect": {"flags": [], "timeout": 2400, "games": ("gen2_new",),
                       "no_setup": True, "frames": 432000,
                       "oracle": "assert_gen2_reconnect_saved", "oracle_kwargs": {}},
    "gen2_type_clause": {"flags": ["--type-clause"], "timeout": 2400, "games": ("gen2_new",),
                         "no_setup": True, "frames": 432000, "oracle": "assert_gen2_clause_saved", "oracle_kwargs": {"kind": "type"}},
    "gen2_gender_clause": {"flags": ["--gender-clause"], "timeout": 2400, "games": ("gen2_new",),
                           "no_setup": True, "frames": 432000, "oracle": "assert_gen2_clause_saved", "oracle_kwargs": {"kind": "gender"}},
    "gen2_species_clause": {"flags": ["--species-clause"], "timeout": 2400, "games": ("gen2_new",),
                            "no_setup": True, "frames": 432000, "oracle": "assert_gen2_clause_saved", "oracle_kwargs": {"kind": "species"}},
    "gen2_soft_reset": {"flags": [], "timeout": 1200, "games": ("gen2_new",),
                        "no_setup": True, "frames": 216000,
                        "oracle": "assert_gen2_soft_reset_saved", "oracle_kwargs": {}},
    # C-5 (docs/gen2/C5_RUNBOOK.md): a Manager-randomized cart refused by the SERVER at hello -- G-c, the partner's
    # contract names another cartridge; G-i, the run has no rom_contract.json (server.py _decide_admission). Run only
    # through tools/c5_runner.py, which stages the randomized carts and the contract; explicit_only keeps them out of
    # every --scenario all / scenarios_for selection (the release matrix and the final sweep).
    **{name: {"flags": [], "timeout": 1200, "games": ("gen2_new",), "no_setup": True, "frames": 216000,
              "target": {"a": "battle", "b": "battle_ot2"}, "explicit_only": True,
              "oracle": "assert_gen2_c5_refused_saved", "oracle_kwargs": {}}
       for name in ("gen2_c5_wrong_rom", "gen2_c5_no_contract")},
    "faint":   {"flags": [], "timeout": 420},
    "boxsync": {"flags": [], "timeout": 420},
    # The old `gen1`/`gen1_yellow` client and its scenario drivers were deleted (deletion plan
    # step 3): lua/tests/duo/scenario_gen1_*.lua and gen1_hunt.lua are gone, so every entry that
    # named `("gen1",)` went with them.
    # NEW Gen 1 client (lua/gen1/*, game "gen1_new"): docs/gen1_requirements.md D-1 and D-3
    # from real play through lua/tests/duo/duo_gen1_main.lua. Both battle fixtures carry
    # exactly ONE Poke Ball unless a row declares the disclosed 20-ball SYNTH recipe.
    # The hunt fights one Tackle first
    # when the foe is at full HP (lua/tests/gen1_rb_hunt_inputs.lua).
    # `frames` is only a runaway guard: the main runs at 16x, so 150000 frames (~156 s) expired
    # inside a wall-clock wait; the real bound is `timeout`, enforced by this runner's cleanup.
    "link_new": {"flags": [], "timeout": 900, "games": ("gen1_new",),
                 "target": "battle", "no_setup": True, "frames": 2000000,
                 "oracle": "assert_link_new_saved"},
    "deadzone_new": {"flags": [], "timeout": 900, "games": ("gen1_new",),
                     "target": "battle", "no_setup": True, "frames": 2000000,
                     "oracle": "assert_dead_zone_new_saved"},
    "linked_faint_bench_new": {"flags": [], "timeout": 1500, "games": ("gen1_new",),
                               "target": "battle", "no_setup": True, "frames": 2500000,
                               "oracle": "assert_linked_faint_saved",
                               "oracle_kwargs": {"active": False}},
    # Control rows retain the ordinary original attempt plus two RNG retries.
    "linked_faint_active_new": {"flags": [], "timeout": 1500, "games": ("gen1_new",),
                                "target": "battle", "gen1_synth": "explode", "rng_attempts": 3, "no_setup": True, "frames": 2500000,
                                "oracle": "assert_linked_faint_saved",
                                "oracle_kwargs": {"active": True}},
    # The same linked faint landing while B's linked mon sits on the BENCH of a wild battle
    # (commit 6a8958fb): the write must land at the next MainInBattleLoop head, inside the battle,
    # not at the overworld checkpoint after it. The explode twin runs --explode-mode: the server
    # sends force_explode, which off the field degrades to a plain faint.
    "linked_faint_bench_battle_new": {"flags": [], "timeout": 1500, "games": ("gen1_new",),
                                      "target": "battle", "no_setup": True, "frames": 2500000,
                                      "oracle": "assert_linked_faint_saved",
                                      "oracle_kwargs": {"active": False, "bench_battle": True}},
    "explode_bench_battle_new": {"flags": ["--explode-mode"], "timeout": 1500,
                                 "games": ("gen1_new",), "target": "battle", "gen1_synth": "explode",
                                 "rng_attempts": 3, "no_setup": True,
                                 "frames": 2500000, "oracle": "assert_linked_faint_saved",
                                 "oracle_kwargs": {"active": False, "bench_battle": True,
                                                   "explode": True}},
    "reconnect_new": {"flags": [], "timeout": 900, "games": ("gen1_new",),
                      "target": "battle", "no_setup": True, "frames": 2000000,
                      "oracle": "assert_reconnect_saved"},
    # D-2: the starters form a gift pair; the pre-ball rival faints do not kill that pair.
    # Both cartridges start cold and play lab/parcel/save with sequential rival turns.
    "ball_gate_new": {"flags": [], "timeout": 1800, "games": ("gen1_new",),
                      "cold_boot": True, "no_setup": True, "frames": 400000,
                      "oracle": "assert_ball_gate_saved"},
    # T-3/T-4: the companion trade bank (every Gen 1 row's cartridge carries it).
    "trade_new": {"flags": [], "timeout": 1500, "games": ("gen1_new",),
                  "target": "battle", "no_setup": True, "frames": 2500000,
                  "oracle": "assert_trade_new"},
    # W-6/R-4: A holds A+B+Select+Start for 16 polls, the WRAM clear lands, the client withholds
    # its hello and pauses writes, CONTINUE reloads the SAME save, and the re-hello carries the
    # same trainer ID. Marker ranges and the reset timing come from the body's header comment
    # (duo_gen1_main.lua:1240-1265): the clear lands ~48 frames into a 24-frame chord.
    "soft_reset_new": {"flags": [], "timeout": 900, "games": ("gen1_new",),
                       "target": "battle", "no_setup": True, "frames": 2000000,
                       "oracle": "assert_soft_reset_saved"},
    # T-3/T-4's NO path: the same receptionist route, but the partner answers NO at the native
    # confirm, so no blob is ever staged and no party moves.
    "trade_decline_new": {"flags": [], "timeout": 1500, "games": ("gen1_new",),
                          "target": "battle", "no_setup": True, "frames": 2500000,
                          "oracle": "assert_trade_decline_saved"},
    # W-3 / D-11: linked_faint_active_new's A half against a server started with --explode-mode
    # (server/server.py:4755 spells it exactly that; the help text still says "RR only" although
    # the Gen 1 client consumes force_explode too). B's in-battle VBlank probe reads the companion
    # patch's mailbox counter -- every Gen 1 row runs its companion cartridge (GAMES
    # `patched_saves`), so no scenario names a ROM of its own any more.
    "explode_new": {"flags": ["--explode-mode"], "timeout": 1800, "games": ("gen1_new",),
                    # The wild foe can KO the linked mon before EXPLOSION (25-49%);
                    # four attempts cover crit/tie variation in that additional battle.
                    # O-33: private qualified battle-save copies with 20 Poke Balls.
                    # Catch/link/explode still run natively; each PYDEC receipt discloses SYNTH.
                    "target": "battle", "gen1_synth": "explode", "rng_attempts": 4, "no_setup": True, "frames": 2500000,
                    "oracle": "assert_explode_saved"},
    # S-6 / W-5 (Bill's PC listing): the link_new body, then A drives DEPOSIT -> WITHDRAW ->
    # DEPOSIT -> RELEASE through the native PC menus. The release sends release{key} and the
    # server kills the pair with cause "release" (owner ruling O-35, client 149b38e2).
    "pc_ops_new": {"flags": [], "timeout": 1800, "games": ("gen1_new",),
                   "target": "battle", "no_setup": True, "frames": 2500000,
                   "oracle": "assert_pc_ops_new_saved"},
    # W-5's box-change half: the deadzone body, then B CHANGEs BOX to 12 (the memorial is
    # listed) and back to 1, with the saved current-box index and the flag read afterwards.
    "changebox_new": {"flags": [], "timeout": 1800, "games": ("gen1_new",),
                      "target": "battle", "no_setup": True, "frames": 2500000,
                      "oracle": "assert_changebox_new_saved"},
    # S-4's blackout half + W-3 (the auto-rebuild): both halves deposit their linked catch,
    # A loses its starter to a wild foe and blacks out, and the server rebuilds the pair out
    # of the two PCs. The runner gates the blackout on BOTH_BOXED (server field, not the
    # client's word) because the rebuild only picks pairs whose halves are absent from
    # `state.party_keys` (server/state.py:2359-2360).
    "whiteout_new": {"flags": [], "timeout": 1800, "games": ("gen1_new",),
                     "target": "battle", "no_setup": True, "frames": 2500000,
                     "oracle": "assert_whiteout_new_saved"},
    # ── A4 (ledger D-5 / D-4) and A7 (S-4): the clause scenarios and the poison blackout ────
    # D-5 type clause: link_new's Route 1 body against `--type-clause`. Route 1's whole table is
    # Pidgey and Rattata (Normal/Flying and Normal, pret data/wild/maps/Route1.asm), so both
    # catches always share Normal and the LATER catcher is rejected at link formation; which
    # half that is falls out of the encounter RNG, so both halves run one body and read their
    # verdict off the wire.
    "type_clause_new": {"flags": ["--type-clause"], "timeout": 1800, "games": ("gen1_new",),
                        "target": "battle", "no_setup": True, "frames": 2500000,
                        "oracle": "assert_type_clause_new_saved"},
    # D-4 species clause, ORDERED: A catches first, the runner releases B only once /api/status
    # shows A's pending capture on route_1 (A_PENDING species=<n>), and B's first encounter then
    # meets check 2 of the dupes clause (server/state.py:1788-1800). The reroll branch is a coin
    # flip on Route 1's table, so a PASS that never observed it re-runs the whole scenario --
    # see run_scenario_with_rng_retry / scenario_attempt_limit.
    "species_clause_new": {"flags": ["--species-clause"], "timeout": 1800,
                           "games": ("gen1_new",), "target": "battle", "no_setup": True,
                           "frames": 2500000, "oracle": "assert_species_clause_new_saved"},
    # S-4's poison half: B walks Route 1 into Viridian Forest, poisons its lone starter and
    # blacks out to Pallet Town with NO link formed; A idles. The per-instance `target` is what
    # keeps A encounter-free -- B needs the battle fixture (post parcel, on Route 1) and A the
    # town one, so neither can meet a wild mon it was not driven to.
    "poison_new": {"flags": [], "timeout": 2400, "games": ("gen1_new",),
                   "target": {"a": "town", "b": "battle"}, "no_setup": True,
                   "frames": 2500000, "oracle": "assert_poison_new_saved"},
    # W-4 / D-11 (swap half): the Route 22 rival fight with --rival-team-swap on, so the server
    # replaces the rival's team with B's party. A MUST boot the battle fixture: only the
    # `lab,parcel,route1` chain arms the Route 22 rival events (tools/gen1_fixtures.py:40), and a
    # town-fixture A would walk the whole route and meet nobody. B boots battle too, so its party
    # carries a catch for the swap to mirror.
    "rival_swap_new": {"flags": ["--rival-team-swap"], "timeout": 1800, "games": ("gen1_new",),
                       "target": {"a": "battle", "b": "battle"}, "no_setup": True,
                       "frames": 2500000, "oracle": "assert_rival_swap_new_saved"},
    # F-4: one randomized Red hello admitted, the un-randomized companion Blue rejected against
    # its randomized Blue contract. The second UPR output is required by prepare_pair but is not
    # launched.
    "admit_randomized_new": {"flags": [], "timeout": 1800, "games": ("gen1_new",),
                             "target": "town", "no_setup": True, "frames": 100000,
                             "oracle": "assert_admit_randomized_saved"},
    # ── NEW Gen 3 client on vanilla FRLG (lua/gen3/*, game "gen3_frlg"; docs/gen3/PLAN.md §5.5,
    # §6 P4). Keys are `<name>_gen3` so the new Gen 3 rows stay distinct from the shared
    # `faint`/`boxsync` entries. Every entry names its saved-state oracle, and the row's
    # check_save_witness_gen3 runs before it (_run_oracle).
    # The drivers are lua/tests/duo/scenario_gen3_<name>.lua under duo_gen3_main.lua; the
    # runner half of each is DuoRun.orchestrate_<key>. `no_save` names a half whose final
    # receipt legitimately saves nothing (reconnect's A ends on the wrong-save relaunch).
    #   faint_cmd           server-command/persistence only: an injected faint propagates, B's
    #                       force_faint lands at the overworld checkpoint, both memorials save.
    #   linked_faint_active natural engine faint of A's ACTIVE linked mon; B's partner sits
    #                       active mid-battle and takes mechanism P+H (owner rulings 15-18): the
    #                       Perish commit + controller hand-off, then the engine's own KO in
    #                       battle with no input (active_faint_chain).
    # P5 (card C5-5): every `games` tuple below now names "gen3_rr" too -- these seven apply
    # to RR unchanged (PLAN §14 P5, owner ruling: RR under the standard). The five whose `target`
    # includes "battle" are blocked on the missing rr_battle{,_b}.sav fixture (see GAMES
    # ["gen3_rr"]'s comment); faint_cmd (server-command only) and reconnect (target "town")
    # have no such dependency. All seven also inherit an UNVERIFIED risk in common: their walking
    # (Route 1 grass) and PC-menu navigation come from lua/tests/gen3_scripted_play.lua's
    # FR-pret-derived PATHS/symbols, reused for RR (duo_gen3_main.lua); the retired savestate
    # driver is archived, and there is no pinned RR walking flow in this repo to port instead.
    # Only the specific functions docs/gen3/research/rr_pc_menu.md and rr_save_layout.md
    # actually cross-checked against the RR binary (Task_DepositMenu/Task_WithdrawMon, a few
    # battle-flag addresses) are confirmed identical to FR's; the rest (battle action/move
    # cursors, map names/tile coordinates) are assumed, not verified.
    "faint_cmd_gen3": {"flags": [], "timeout": 900, "games": ("gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp"),
                       "target_by_game": {"gen3_rr": "battle2", "gen3_emerald": "pc", "gen3_exp": "pc"}, "target": "town",
                       "frames": 2000000, "oracle": "assert_faint_cmd_gen3_saved"},
    # G5-RR-BATTERY: on gen3_rr every row that links or trades the slot-1 mon, or throws a Poke
    # Ball, boots rr_battle2{,_b}.sav (two mons, nine balls; 80913bdf) via target_by_game --
    # rr_town / rr_battle hold ONE mon and no balls (live 3fa789da: KeyError 1 on the slot-1 key,
    # "the fixture starts with no Poke Balls", no no_catch without balls).
    # A1 and R1 (RR companion): mechanism P+H on the wild battle (active_faint_case "wild").
    # `target_by_game` (scenario_target): RR's rr_battle.sav holds ONE mon and no balls, so R1's
    # send-out needs rr_battle2 (parcel -> 10 balls, then a Route 1 catch; driver 8103ddec).
    # XG3: the expansion's compiler-probed Volatiles head and ROM-bound controller handoff
    # are exercised by this row; production routing remains separately gated.
    "linked_faint_active_gen3": {"flags": [], "timeout": 1800,
                                 "games": ("gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp"),
                                 "target": "battle",
                                 "target_by_game": {"gen3_rr": "battle2", "gen3_emerald": "pc", "gen3_exp": "pc"},
                                 "frames": 2500000,
                                 "oracle": "assert_linked_faint_active_gen3_saved"},
    # A1 (i) and R4: B's linked mon is its only mon -- a hand deposit of the slot-1 mon on FR/LG,
    # already so on RR's one-mon rr_battle.sav -- so the Perish KO whites out (ruling 18: P is not
    # held on the last mon; ruling 21: the game_over that follows is intended).
    "linked_faint_active_whiteout_gen3": {"flags": [], "timeout": 2400,
                                          "games": ("gen3_frlg", "gen3_rr"),
                                          "target": "battle", "frames": 3000000,
                                          "scenario_module": "linked_faint_active",
                                          "active_faint_case": "whiteout",
                                          "oracle": "assert_linked_faint_active_whiteout_gen3_saved"},
    # A1 (ii): B takes T2's route to Bug Catcher Rick 102 (cffe0a25); the forced party screen
    # follows the KO. B boots the town fixture (the route starts there), A the battle one.
    "linked_faint_active_trainer_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_frlg",),
                                         "target": {"a": "battle", "b": "trainer"}, "frames": 2500000,
                                         "scenario_module": "linked_faint_active",
                                         "active_faint_case": "trainer",
                                         "oracle": "assert_linked_faint_active_trainer_gen3_saved"},
    "boxsync_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp"),
                     "target_by_game": {"gen3_rr": "battle2", "gen3_emerald": "pc", "gen3_exp": "pc"}, "target": {"a": "battle", "b": "town"}, "frames": 2500000,
                     "oracle": "assert_boxsync_gen3_saved"},
    # E4c: on gen3_emerald the receipt is Emerald's own landing, not FR's Center: DoWhiteOut heals
    # in C and warps straight to gSaveBlock1Ptr->lastHealLocation, OUTDOORS (pret pokeemerald
    # src/overworld.c:357-366,665-668; heal_locations.json:82-85 Oldale Town 0.10 (6,17)).
    # Both sides boot the two-mon pc fixture (slot 1 is the linked mon; A deposits it at the Oldale
    # PC and walks out to Route 103 with its lone starter).
    "whiteout_gen3": {"flags": [], "timeout": 2400, "games": ("gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp"),
                      "target_by_game": {"gen3_rr": "battle2", "gen3_emerald": "pc", "gen3_exp": "whiteout_synth"}, "target": {"a": "battle", "b": "town"}, "frames": 3000000,
                      "oracle": "assert_whiteout_gen3_saved"},
    # G4 item 2a (4): the Center 2F negative controls (the nurse rides whiteout_gen3). A walks
    # from the Route 1 grass to the 2F; its one in-game save is the Cable Club's own
    # (EventScript_AskSaveGame), so B -- which only idles -- never saves.
    "center_controls_gen3": {"flags": [], "timeout": 2400, "games": ("gen3_frlg",),
                             "target": {"a": "battle", "b": "town"}, "frames": 3000000,
                             "no_save": ("b",), "oracle": "assert_center_controls_gen3_saved"},
    # C4-6r's PRODUCT FINDING as a live regression: after an in-game save the stale sSaveDialogCB
    # must not hold SLink writes (fix: C4-SAVE). FAILS on a pack that still has save_dialog_cb.
    "save_then_write_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_frlg",),
                             "target": "town", "frames": 2000000, "no_save": ("b",),
                             "oracle": "assert_save_then_write_gen3_saved"},
    # NAT-LEGS: the three FR/LG natural legs (docs/gen3_requirements.md S-8, S-9, S-11). A boots
    # a DISCLOSED O-33 SYNTH edit of a party fixture (tools/gen3_fixtures.py make-frlg-synth,
    # {firered,leafgreen}_party_<kind>_synth.sav); only the behaviour under test runs natively,
    # on ordinary buttons. explicit_only: never part of `--scenario all`. `games` names the
    # gen3_frlg family only (scenario_applies resolves `gen3_lgfr` through its family, same as
    # every other shared FR/LG row, e.g. center_controls_gen3): `--game gen3_lgfr` runs these
    # too (card NAT-LEGS-2, LG-as-A), each game's own `sides` mapping formatting `target` against
    # the right title's fixture stem. The three scenario_gen3_*.lua modules are title-agnostic
    # (ctx.player, not ROM version). LeafGreen's Route 2 trade (INGAME_TRADE_MR_MIME) has no
    # FIRERED/LEAFGREEN #if branch in pret pokefirered (src/data/ingame_trades.h, pin c75f3523):
    # ABRA -> MR. MIME is the same on both titles, so the LG-as-A trade leg reuses MR_MIME/ABRA
    # unchanged.
    #   evolve       a Route 1 wild win levels the Lv15 Squirtle to 16 and evolves it; the tick
    #                publishes WARTORTLE to the linked half (S-8 evolve_species_store)
    #   npc_trade    Reyley's Route 2 house trade (ABRA -> MR. MIME); the client emits key_change
    #                reason npc_trade (S-9 trade_done); the migration is judged (NAT-LEGS-3)
    #   poison_faint a field poison step faints the 1-HP lead; B's linked mon is force-fainted
    #                (S-11 poison_faint + the Soul Link rule)
    # card NAT-LEGS-4 (Emerald parity): evolve_gen3 also runs E<->E on gen3_emerald, target_by_game
    # swapping the FR/LG "_synth" stem for Emerald's own committed emerald_evolve.sav (already built,
    # card E2-FIX-VARIANTS round 3: SYNTH Mudkip Lv15, EXP one short of Lv16, on Route 102 grass --
    # same tile as emerald_battle.sav, so no city/forest walk); B idles on emerald_town_b.sav
    # (already committed). EVOLVE_FACTS (below the oracle) carries the post-evolution species per
    # game so the oracle isn't a Wartortle-only constant.
    # card RR-SYNTH: gen3_rr joins too. rr_evolve_synth.sav (tools/gen3_fixtures.py
    # make-rr-synth --kind evolve over rr_battle2.sav) is a SYNTH Lv15 TREECKO one EXP short of
    # its RR Lv16 EVO_LEVEL evolution (RR's own relocated evolution/base-stat/exp tables, not
    # vanilla FR/LG -- see the build_rr_synth module comment in tools/gen3_fixtures.py); RR's
    # own level-up learnset has no level-16 entry for TREECKO, so no move-learn prompt. B idles
    # on the DEFAULT target's "town" ("b": "town" -> rr_town_b.sav, already committed): evolve
    # never force-faints B, so the single-mon fixture is fine here (unlike poison_faint_gen3
    # below). No target_by_game override needed: the default {"a": "evolve_synth", "b": "town"}
    # already resolves to rr_evolve_synth.sav / rr_town_b.sav via gen3_rr's own "rr_{target}"
    # sides template.
    "evolve_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_frlg", "gen3_emerald", "gen3_rr"),
                    "explicit_only": True,
                    "target": {"a": "evolve_synth", "b": "town"},
                    "target_by_game": {"gen3_emerald": {"a": "evolve", "b": "town"}},
                    "frames": 2000000, "no_save": ("b",),
                    "oracle": "assert_evolve_gen3_saved"},
    # NAT-LEGS-3: gen3_emerald joins as its own E<->E leg (RustboroCity_House1's INGAME_TRADE_SEEDOT,
    # emerald_trade.sav; target_by_game overrides the FR/LG "_synth" fixture stem with Emerald's own
    # naming, tests/fixtures/gen3/README.md). The oracle's NPC_TRADE_FACTS carries the per-game
    # new_key/species; poison_faint_gen3's oracle is already game-agnostic (no species assertion).
    # orchestrate_npc_trade_gen3 links party slot 1 on BOTH halves (_gen3_prelude(link_slot=1)):
    # FR/LG's "town" fixture already carries 2 party mons (unlike Emerald's, which is a single
    # Mudkip for that kind), so B's Emerald side boots "pc" instead (Mudkip + Poochyena, slot 1
    # present) -- the same B-idle fixture boxsync_gen3/whiteout_gen3 already use for gen3_emerald.
    # card RR-NPCTRADE: gen3_rr joins too, now that RR's own trade table + trader position are
    # pinned (docs/gen3/research/rr_ingame_trades.md, tools/rr_ingame_trades.py). card
    # RR-NPCTRADE-2: Route2_House/"Reyley" (MR_MIME slot) turned out DEAD on the real RR ROM (its
    # CFRU-detoured species check reads a runtime table whose index-0 entry is garbage -- doc
    # section "RR-NPCTRADE-2"), so the fixture targets JYNX/"Dontae" (CeruleanCity_House3)
    # instead: no badge/checkflag gate either, same map/script relationship to vanilla.
    # rr_trade_synth.sav (tools/gen3_fixtures.py make-rr-synth --kind trade over rr_battle2.sav,
    # which build_rr_synth's module comment and the card doc source) makes party[1] a
    # player-owned Lv10 SNOM (RR species 1164) and warps CONTINUE into CeruleanCity_House3 (7,2)
    # (2,1), one tile above Dontae (2,2) facing up. B needs 2 party mons for the same link_slot=1
    # reason as the FR/LG and Emerald legs
    # above (rr_town.sav/rr_town_b.sav only carry one mon); rr_battle2_b.sav (already committed,
    # 2 mons) is reused as-is -- the same B-idle substitution boxsync_gen3/whiteout_gen3 already
    # make for gen3_rr via target_by_game "battle2".
    "npc_trade_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_frlg", "gen3_emerald", "gen3_rr"),
                       "explicit_only": True,
                       "target": {"a": "trade_synth", "b": "town"},
                       "target_by_game": {"gen3_emerald": {"a": "trade", "b": "pc"},
                                          "gen3_rr": {"a": "trade_synth", "b": "battle2"}},
                       "frames": 2000000, "no_save": ("b",),
                       "oracle": "assert_npc_trade_gen3_saved"},
    # B's Emerald side boots "pc" (Mudkip + Poochyena), not "town" (single Mudkip): B's own linked
    # lead is force_faint'ed by the server (Soul Link), and a single-mon party would white out
    # (RESULT: FAIL "no memorialize_done" -- the game overs before the memorial can send), exactly
    # the reason linked_faint_active_gen3 already substitutes "pc" for gen3_emerald.
    # card RR-SYNTH: gen3_rr does NOT join this row -- checked and refused, not attempted blind.
    # RR's DoPoisonFieldEffect (vanilla pokefirered.sym 080a0618) detours to 0x090B20D4, whose
    # bytes are `00 20 70 47` = `MOVS r0,#0; BX LR`: an unconditional `return FLDPSN_NONE` stub,
    # no party loop, no HP mutation at all (confirmed directly against patch/build/slink_RR.gba;
    # independently already pinned by docs/gen3_engine_sites.md's own source note citing CFRU
    # src/overworld.c's NO_POISON_IN_OW build option). Field poison cannot faint OR EVEN DAMAGE a
    # party mon outside battle on the companion-patched RR ROM these duo rows boot, so there is
    # no natively-reachable S-11 site to build a SYNTH seed around on gen3_rr.
    "poison_faint_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_frlg", "gen3_emerald"),
                          "explicit_only": True,
                          "target": {"a": "poison_synth", "b": "town"},
                          "target_by_game": {"gen3_emerald": {"a": "poison", "b": "pc"}},
                          "frames": 2000000,
                          "oracle": "assert_poison_faint_gen3_saved"},
    # `ball_hunt`: a half throws Poke Balls, so "hunt ended out-of-balls" (the game's catch RNG
    # on a fixture's few balls) earns the Gen 1 standard's whole-run retry (RNG_RETRY_FAMILIES).
    # gen3_emerald hunts "catch" (20 balls), not "battle" (5): deadzone_gen3 burned all three RNG
    # retries on the 5-ball fixture (B ran out of balls twice) before the catch fixture existed.
    # FR/LG link_gen3 hunts the SYNTH party_catch_synth saves (20 balls) for the same reason: the
    # LeafGreen battle save holds 2 balls and gen3_lgfr failed out-of-balls 3/3.
    "link_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp"),
                  "ball_hunt": True,
                  "target_by_game": {"gen3_rr": "battle2", "gen3_emerald": "catch", "gen3_exp": "catch",
                                     "gen3_frlg": "catch_synth", "gen3_lgfr": "catch_synth"},
                  "target": "battle", "frames": 2500000, "oracle": "assert_link_gen3_saved"},
    "deadzone_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_frlg", "gen3_rr", "gen3_emerald"),
                      "ball_hunt": True,
                      "target_by_game": {"gen3_rr": "battle2", "gen3_emerald": "catch"},
                      "target": "battle", "frames": 2500000,
                      "oracle": "assert_deadzone_gen3_saved"},
    "reconnect_gen3": {"flags": [], "timeout": 1200, "games": ("gen3_frlg", "gen3_rr", "gen3_emerald"),
                       "target_by_game": {"gen3_rr": "battle2", "gen3_emerald": "pc"}, "target": "town",
                       "frames": 2000000, "no_save": ("a",),
                       "oracle": "assert_reconnect_gen3_saved"},
    # ── RR-only (P5, card C5-5): docs/gen3/PLAN.md §14 P5's nine minus the retired old-client
    # trade/ghost/infopanel rows and trade_abort (a later card; not built here, see the card's
    # final report). All three below are BLOCKED the same way as the five "battle"-target
    # scenarios above (missing rr_battle{,_b}.sav); see each entry.
    #   explode      NON-QUALIFYING CONTROL (`control`): A loses its linked lead naturally,
    #                B must receive the KEYED force_explode, and B's receipt shows the engine
    #                STARTING the Explosion action (lastUsedMovePlayer). That is upstream of
    #                attackcanceler/tryexplosion, so it cannot prove execution (Codex C4-6c
    #                finding 4); blocked until an RR-verified downstream witness exists.
    #   rival_swap   BLOCKED NEGATIVE CONTROL (`control`): a dummy, identity-less team is
    #                refused with stale_battle_id -- the C5-10 battle-identity gate (2dc1b750;
    #                docs/gen3/research/rival_swap_refresh_window.md §3.3: missing session /
    #                battle_id -> refuse, nothing written) answers before any refresh window.
    #   clean_rr_refused REFUSAL PROOF (patch-first, owner 2026-10-02: the companion patch is
    #                REQUIRED, a clean RR is refused at launch): B boots the CLEAN RR dump
    #                (rom_kind clean/companion split) and `expect_refused` makes the driver PASS
    #                B only on lua/gen3/run.lua's own "needs the SLink companion patch" refusal --
    #                no client, no hello, no write, no save. A (companion) boots and connects
    #                alone and must see no partner, no link and no write.
    # Owner ruling 19: RR force_explode's commit ends in the P+H hand-off, so it runs on the P+H
    # carrier (active_faint_case "explode"): no press after the commit, the same engine oracles,
    # and the attacker's own faint site after the 153 stamp. A QUALIFICATION row (G5-RR-ORACLES):
    # the old control label asked for a witness downstream of attackcanceler/tryexplosion, and the
    # attacker's own faint site (counter +1, HP 0, no SLink HP write) after the stamp is one -- a
    # Damp/sleep/flinch cancel produces no self-KO.
    "explode_gen3": {"flags": ["--explode-mode"], "timeout": 900,
                     "games": ("gen3_frlg", "gen3_rr", "gen3_emerald"),
                     "target": "battle", "frames": 1500000,
                     "scenario_module": "linked_faint_active", "active_faint_case": "explode",
                     "oracle": "assert_explode_gen3_saved"},
    # `control`: a NEGATIVE/BLOCKED characterization, never a qualification PASS (Codex
    # C4-6b finding 6); summary_lines and the oracle's PYDEC line both say so.
    "rival_swap_gen3": {"flags": [], "timeout": 600, "games": ("gen3_rr",),
                        "control": "BLOCKED negative control: a dummy, identity-less team is "
                                   "refused with stale_battle_id by the C5-10 battle-identity "
                                   "gate; no valid swap is proven here",
                        "target": "battle", "frames": 900000, "no_save": ("a", "b"),
                        "oracle": "assert_rival_swap_gen3_saved"},
    # G5-RR-RIVAL (owner ruling 25): the QUALIFYING rival row. A boots rr_rival.sav (CACHED-NATIVE,
    # the fixture driver's `rival` leg: Route 22 (34,6), early rival armed) and steps onto the
    # coord event; the server's auto swap (--rival-team-swap) stages B's rr_battle2_b party.
    "rival_swap_real_gen3": {"flags": ["--rival-team-swap"], "timeout": 900, "games": ("gen3_rr",),
                             "target": {"a": "rival", "b": "battle2"}, "frames": 900000,
                             "no_save": ("a", "b"), "oracle": "assert_rival_swap_real_gen3_saved"},
    "clean_rr_refused_gen3": {"flags": [], "timeout": 300, "games": ("gen3_rr",),
                           "target_by_game": {"gen3_rr": "battle2"}, "target": "town", "frames": 300000,
                           "rom_kind": {"a": "companion", "b": "clean"}, "expect_refused": ("b",),
                           "no_save": ("a", "b"), "oracle": "assert_clean_rr_refused_gen3_saved"},
    # G5 two-player evidence for the retired old-client rows, rebuilt on the new client.
    # trade / trade_decline: the companion's Pokemon-Center trade NPC; both boot rr_battle2{,_b}
    # (slot 1 is the linked/traded mon); the oracle reads both flashes (PYDEC) + links.json.
    # infopanel: the SOULLINK START-row panel; the oracle decodes the drawn bytes with the Python
    # charmap. Both RR menu shapes: rr_town{,_b} (pre-Pokedex, 6 stock rows) and, as infopanel_dex,
    # rr_battle2{,_b} (Pokedex owned, 7 stock rows; the shape the old count==6 splice skipped).
    "native_trade_firered": {"flags": [], "timeout": 1800, "games": ("gen3_fr_trade",),
        "target": "town", "frames": 2500000, "gen3_native_trade": True,
        "scenario_module": "native_trade", "oracle": "assert_native_trade_firered"},
    "native_trade_decline_firered": {"flags": [], "timeout": 1800, "games": ("gen3_fr_trade",),
        "target": "town", "frames": 2500000, "gen3_native_trade": True,
        "native_decline": True, "scenario_module": "native_trade", "oracle": "assert_native_trade_firered"},
    "trade_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_rr",), "target": "battle2",
                   "frames": 2500000, "oracle": "assert_trade_gen3_saved"},
    "trade_decline_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_rr",), "target": "battle2",
                           "frames": 2500000, "scenario_module": "trade",
                           "oracle": "assert_trade_decline_gen3_saved"},
    "trade_lock_probe_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_rr",),
                               "target": "battle2", "frames": 2500000, "explicit_only": True,
                               "journal_lock_probe": True, "scenario_module": "trade_lock_probe",
                               "oracle": "assert_trade_lock_probe_gen3_saved"},
    # RR physical durability: native pre-save/commit interruption and native-success
    # cold reload. The FINAL phase has no save; the initial phase's native save(s) are
    # checked separately against immutable battery/journal copies by the reset oracle.
    "trade_reset_commit_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_rr",),
                                 "target": "battle2", "frames": 2500000, "explicit_only": True,
                                 "rr_reset_trade": True, "reset_case": "commit",
                                 "scenario_module": "trade_reset", "no_save": ("a", "b"),
                                 "oracle": "assert_rr_trade_reset_saved"},
    "trade_reset_success_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_rr",),
                                  "target": "battle2", "frames": 2500000, "explicit_only": True,
                                  "rr_reset_trade": True, "reset_case": "success",
                                  "scenario_module": "trade_reset", "no_save": ("a", "b"),
                                  "oracle": "assert_rr_trade_reset_saved"},
    "infopanel_gen3": {"flags": [], "timeout": 600, "games": ("gen3_rr",), "target": "town",
                       "frames": 900000, "no_save": ("a", "b"),
                       "oracle": "assert_infopanel_gen3_saved"},
    "infopanel_dex_gen3": {"flags": [], "timeout": 600, "games": ("gen3_rr",), "target": "battle2",
                           "frames": 900000, "no_save": ("a", "b"), "scenario_module": "infopanel",
                           "oracle": "assert_infopanel_dex_gen3_saved"},
    # RR rows R2/R3/R5 of rr_active_faint_parity_scope §5.5 (R1 = linked_faint_active_gen3 and
    # R4 = linked_faint_active_whiteout_gen3 on gen3_rr). R1/R2/R3 boot rr_battle2{,_b}.sav
    # (a second mon for the send-out, balls for R3's L-throw) and SKIP by name until it is built
    # (skip_reason); R4 runs on the one-mon rr_battle{,_b}.sav.
    #   R2 clean   RETIRED 2026-10-03 (patch-first): a clean RR is refused at launch, so there is
    #              no clean-side link row; clean_rr_refused_gen3 is the single refusal proof
    #              (docs/gen3/RR_CLEAN_ROWS_CONVERSION_2026-10-03.md).
    #   R3 lhammer B pulses L every frame from the commit to the KO; no ball may be lost.
    #   R5 mega    SIGNED G5 LIMIT (owner ruling 20): an allowed SKIP, never launched.
    "linked_faint_active_lhammer_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_rr",),
                                         "target": "battle2", "frames": 2500000,
                                         "scenario_module": "linked_faint_active",
                                         "active_faint_case": "lhammer",
                                         "oracle": "assert_linked_faint_active_lhammer_gen3_saved"},
    "linked_faint_active_mega_gen3": {"flags": [], "timeout": 1800, "games": ("gen3_rr",),
                                      "target": "battle", "frames": 2500000,
                                      "scenario_module": "linked_faint_active",
                                      "active_faint_case": "mega",
                                      "signed_limit": "owner ruling 20 (docs/gen3/G4_request_draft.md "
                                                      "§6 item 20): no Mega Ring or stone holder is "
                                                      "reachable by normal inputs early in RR, and the "
                                                      "Perish path reads no mega state",
                                      "oracle": "assert_linked_faint_active_mega_gen3_saved"},
}


for _rule in ("species", "gender", "type"):
    SCENARIOS[f"{_rule}_clause_gen3"] = {
        "flags": [f"--{_rule}-clause"], "timeout": 2400, "frames": 3000000,
        "games": ("gen3_frlg", "gen3_rr", "gen3_emerald"), "ball_hunt": True,
        "target": "catch_synth", "target_by_game": {"gen3_emerald": "catch", "gen3_rr": "battle2"},
        "scenario_module": "clause", "rule_kind": _rule, "oracle": f"assert_{_rule}_clause_gen3_saved",
    }
# RR nature change (Sol's client + pack + census sites). explicit_only: the row needs the
# qualified nature_change_begin/end pair, so it never joins an automatic --scenario all
# sweep; tools/gen3_final_cut.py's RR plan selects explicit_only rows explicitly. The
# target is battle2, the RR fixture whose party has the LINKED mon in slot 1.
SCENARIOS["nature_change_gen3"] = {
    "flags": [], "timeout": 2400, "frames": 2000000,
    "games": ("gen3_rr",), "target": "battle2",
    "explicit_only": True, "nature_change": True,
    "scenario_module": "nature", "oracle": "assert_nature_change_gen3_saved",
}

# Native School loan lifecycle: menu-time command hold/cancel and real battle restore.
# Both use the existing battle2 battery and ordinary inputs; the idle peer does not save.
for _borrow_case in ("menu", "opponent", "battle"):
    SCENARIOS[f"borrowed_party_{_borrow_case}_gen3"] = {
        "flags": [], "timeout": 2400, "frames": 3000000,
        "games": ("gen3_rr",), "target": "battle2", "explicit_only": True,
        "scenario_module": "borrowed", "no_save": ("b",),
        "oracle": "assert_borrowed_party_gen3_saved",
    }

SCENARIOS["release_gen3"] = {
    "flags": [], "timeout": 2400, "frames": 3000000,
    "games": ("gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp"),
    "target": {"a": "battle", "b": "town"},
    "target_by_game": {"gen3_emerald": "pc", "gen3_rr": "battle2", "gen3_exp": "pc"},
    "oracle": "assert_release_gen3_saved",
}
SCENARIOS["ball_gate_gen3"] = {
    "post_flip_stock": True,
    "flags": [], "timeout": 2400, "frames": 3000000,
    "games": ("gen3_frlg", "gen3_rr", "gen3_emerald"),
    "ball_hunt": True, "rng_attempts": 8,
    "target": "ball_gate_synth", "target_by_game": {"gen3_emerald": "ball_gate"},
    "hunt_area_by_game": {"gen3_frlg": "viridian_forest", "gen3_lgfr": "viridian_forest",
                          "gen3_emerald": "rusturf_tunnel", "gen3_rr": "route_1"},
    "oracle": "assert_ball_gate_gen3_saved",
}
SCENARIOS["species_family_gen3"] = {
    "flags": ["--species-clause"], "timeout": 1800, "frames": 2500000,
    "games": ("gen3_frlg", "gen3_rr", "gen3_emerald"), "explicit_only": True,
    "rule_kind": "family", "target": {"a": "family_synth", "b": "catch_synth"},
    "target_by_game": {"gen3_emerald": {"a": "family", "b": "catch"},
                       "gen3_rr": {"a": "family_synth", "b": "family_galar_synth"}},
    "oracle": "assert_species_family_gen3_saved",
}
SCENARIOS["shiny_bonus_gen3"] = {
    "flags": [], "timeout": 2400, "frames": 3000000, "ball_hunt": True, "rng_attempts": 8,
    "games": ("gen3_frlg", "gen3_rr", "gen3_emerald"), "explicit_only": True,
    "target": "catch_synth", "target_by_game": {"gen3_emerald": "catch", "gen3_rr": "battle2"},
    "oracle": "assert_shiny_bonus_gen3_saved",
}


# No families any more: the `gen1`/`gen1_yellow` pair was the only one, and both titles (and
# their scenario drivers) were deleted in the same step. `games` matching is exact.
# Titles that never inherit a scenario implicitly. An entry with no `games` key means "every
# title", which is right for savestate-less shared scenarios like faint/boxsync — but not for
# `gen1_new`, whose driver runs only the scenarios that name it, so opt-in is the whole rule.
OPT_IN_GAMES = ("gen1_new", "gen2_new", "gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_fr_trade", "gen3_exp")


def is_pure_pairing(game) -> bool:
    """A pairing row whose cartridges are pureRGB builds (its fixtures are pure titles)."""
    row = GAMES.get(game, {}) if isinstance(GAMES, dict) else {}
    fixtures = row.get("fixture") or {}
    return any(str(t).startswith("pure") for t in fixtures.values())


def scenario_family(game):
    """The scenario family a pairing row runs (GAMES[row]["game"]); gen1_pure -> gen1_new."""
    return GAMES.get(game, {}).get("game", game) if isinstance(GAMES, dict) else game


def scenario_applies(name, game):
    """Does `name` apply to `game`? Absent `games` means every title but the opt-in ones."""
    allowed = SCENARIOS[name].get("games")
    # A pairing row can belong to a scenario family (GAMES[...]["game"], e.g. gen1_pure runs
    # the gen1_new scenarios on the pure builds); scenarios declare the family, not the row.
    family = GAMES.get(game, {}).get("game", game) if isinstance(GAMES, dict) else game
    # A pairing row may exclude scenarios its artifacts cannot run yet (gen1_pure: the native
    # trade/explode scenarios need the M3 overlay, admit_randomized the M5 UPR fork).
    if isinstance(GAMES, dict) and name in GAMES.get(game, {}).get("not_yet", ()):
        return False
    # Native trade runs on the pairings with errand trade seeds: C-C, G-S and C-G (O-34 supersedes Q10).
    if name in GEN2_TRADE_SCENARIOS and game not in GEN2_TRADE_FIXTURES:
        return False
    if name == "gen2_ball_gate" and game not in GEN2_BALL_GATE_FIXTURES:
        return False
    if name == "gen2_faint_active_trainer" and game not in GEN2_TRAINER_FIXTURES:
        return False
    if name in GEN2_SYNTH_SCENARIOS and game not in GEN2_SYNTH_PAIRS:
        return False
    if allowed is None:
        return game not in OPT_IN_GAMES and family not in OPT_IN_GAMES
    return game in allowed or family in allowed


def scenarios_for(game):
    """The scenarios that apply to `game`, in table order.

    The single source of truth for that question — tests/e2e/test_duo.py used to hand-roll
    its own copy with a different default, which is how the two answers drifted apart.
    `explicit_only` rows (the randomized FR/LG rows) never join `--scenario all`; naming one
    with `--scenario <name>` still runs it (scenario_applies is unchanged).
    """
    return [n for n in SCENARIOS if scenario_applies(n, game) and not SCENARIOS[n].get("explicit_only")]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(http_port, method, path, body=None, timeout=10):
    url = f"http://127.0.0.1:{http_port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ── lane identity (docs/purergb/research/p3/harness_literals_bbcd037.md table 3) ────────────────
# Two DuoRuns in ONE process must share no generated stub, no BizHawk config copy and no window
# position. The stub is the worst of the three: it bakes SLINK_PORT, so a collision makes the
# loser's emulator talk to the winner's server, and the config copy carries that instance's SaveRAM
# directory. The lane id defaults to the TCP port (already free per run) and can be pinned with
# --lane; the window offset comes from the lane's first-seen ordinal in this process, so lanes
# created in one process never overlap even though the ordinals restart with the process.
_LANE_ORDINAL: dict[str, int] = {}
_LANE_WINDOW_STEP = 160


def lane_ordinal(lane: str) -> int:
    return _LANE_ORDINAL.setdefault(lane, len(_LANE_ORDINAL))


def read_result(scenario, inst):
    path = os.path.join(BUILD, f"e2e_{scenario}_{inst}_result.txt")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    # complete lines only: the Lua receipt writers end every line with a newline, so a line
    # without one is mid-write (a torn MYKEY/BALL_* JSON/RESULT line at the 0.2 s poll)
    return text[:text.rfind("\n") + 1]


RNG_OUT_OF_BALLS = "RESULT: FAIL (hunt ended out-of-balls)"
# The scenario families the ball-RNG retry covers: the Gen 1 standard (gen1_new) and the Gen 3
# rows built on it (card C4-6g). ONE mechanism for all of them -- the cause table, the pair rule
# (retryable_gen1_rng), the per-attempt idle jitter the driver echoes (jitter_problems) and the
# attempt budget (scenario_attempt_limit). A Gen 3 scenario opts in with `ball_hunt`: only a
# half that throws Poke Balls can end on the out-of-balls cause.
RNG_RETRY_FAMILIES = ("gen1_new", "gen3_frlg", "gen3_rr", "gen3_emerald", "gen3_exp")
# A partner half's own "I only failed because the other half did" line (the gen3 drivers phrase
# it "CONSEQUENCE: <what>"). It never retries by itself: the pair still needs a CAUSE_RNG.
CONSEQUENCE_PREFIX = "CONSEQUENCE: "


def rng_retry_family(game) -> bool:
    return scenario_family(game) in RNG_RETRY_FAMILIES
# Classification table for RESULT reasons in duo_gen1_main.lua:286-758:
#   CAUSE_RNG   bare/nested `hunt ended out-of-balls` or opt-in first catch battle loss
#   CONSEQUENCE exact linked-capture-not-returned phrases when the other side has CAUSE_RNG
#   FINAL       every other return-false template: no-go/boot/save, other hunt phase,
#               trade menu/prompt/apply, faint window/force_faint/battle/text/Box 12,
#               and admission hello/party/map errors. A nested link prerequisite with
#               other causes than the named RNG prerequisites stay FINAL.
GEN1_RNG_REASON_CLASS = {
    "hunt ended out-of-balls": "CAUSE_RNG",  # link_new/deadzone_new direct
    "link_new prerequisite failed: hunt ended out-of-balls": "CAUSE_RNG",  # nested trade/faint
    "hunt ended first-catch-battle-lost": "CAUSE_RNG",  # opt-in Route 1 prerequisite
    "link_new prerequisite failed: hunt ended first-catch-battle-lost": "CAUSE_RNG",
    # The Route 1 hunter can also lose its wild battle outright (a Rattata crit chain on the L5
    # starter): the walk-back whiteout is the game's RNG, same shape as poison's starter KO,
    # so a whole-run retry is the right response (seen in the duo-pairs lane, linked_faint_bench).
    "hunt ended whiteout": "CAUSE_RNG",
    "hunt ended lead fainted while catching": "CAUSE_RNG",
    "link_new prerequisite failed: hunt ended lead fainted while catching": "CAUSE_RNG",
    "link_new prerequisite failed: hunt ended whiteout": "CAUSE_RNG",
    # poison_new's two forest legs (duo_gen1_main.lua:2352,2385): the poisoning is a race
    # between the wild table and the starter's HP, so both outcomes are the game's RNG and a
    # whole-run retry is the right response. Any other poison leg failure is FINAL.
    # `the forest hunt ended unexpected-battle (...)` (the route module's new terminal) matches
    # no key here ON PURPOSE: an unexpected battle is a driver fault, not the game's RNG, so it
    # classifies FINAL and the run is not retried.
    "RNG: the forest hunt spent its encounter budget without a poisoning": "CAUSE_RNG",
    "RNG: a wild foe knocked the starter out before the poisoning": "CAUSE_RNG",
    "linked capture was not returned": "CONSEQUENCE",  # linked_faint_* without pair
    "linked capture was not returned to party": "CONSEQUENCE",  # trade_new without pair
    # species_clause_new: A's hunt spent the fixture's only ball, so the runner never wrote the
    # A_PENDING mark and B reported this (duo_gen1_main.lua:776). CONSEQUENCE, not CAUSE_RNG:
    # it only earns a retry when the pair also carries A's out-of-balls phrase.
    "runner never released B (A_PENDING)": "CONSEQUENCE",
    # explode_new's partner half: A waits 180 s for B's READY_ACTIVE mark and reports this when
    # B died before parking (duo_gen1_main.lua:1608-1612). CONSEQUENCE, not FINAL, or B's
    # explode-KO phrase could never earn the pair a retry.
    "B did not park in the required faint window": "CONSEQUENCE",
    # type_clause_new: the verdict wait saw the partner finish first -- its only ball missed
    # (duo_gen1_main.lua type-clause body). Only a retry when the partner's miss is CAUSE_RNG.
    "no type-clause verdict (partner-gone)": "CONSEQUENCE",
    "no gender-clause verdict (partner-gone)": "CONSEQUENCE",
    "no species-clause verdict (partner-gone)": "CONSEQUENCE",
    # species_clause_new's own budget phrase: B's battle cap (8) exhausted by duplicates. The
    # reroll observation and the hunt's RNG budget are the same attempts, so this one is
    # retryable on ANY attempt (see retryable_gen1_rng), unlike the ball miss.
    "RNG: the species hunt met only duplicates within its battle budget": "CAUSE_RNG",
    # RR type clause: the first natural B foe may share no type with A. Leave the battle
    # unfinished and retry fresh; RUN would send no_catch and dead-zone A's pending area.
    "RNG: type first encounter has no overlap": "CAUSE_RNG",
    # explode_new: battle HP hit 0 before the coerced EXPLOSION could be committed. Same shape:
    # the game's RNG, not a driver fault, so a whole-run retry is the right response.
    "RNG: the wild foe knocked the linked mon out before EXPLOSION": "CAUSE_RNG",
    "B could not hold its linked mon active: out-of-balls": "FINAL",  # switch-turn death, not catch RNG
}



def _flat_mon_stats(stats):
    """links.json `mon_stats` as one {key: stats} map. The server keeps it per player
    ({"a": {...}, "b": {...}}, KEY-SCOPE-2); an older document is already flat."""
    stats = stats or {}
    if set(stats) <= {"a", "b"} and all(isinstance(v, dict) for v in stats.values()):
        return {**(stats.get("a") or {}), **(stats.get("b") or {})}   # ponytail: an equal key merges
    return stats

class GameRngMiss(Exception):
    """The only early orchestration exit eligible for a whole-run Gen 1 retry."""


def classify_gen1_result(text):
    """PASS, CAUSE_RNG, CONSEQUENCE, FINAL, or None for missing/ambiguous RESULT."""
    lines = [line.strip() for line in (text or "").splitlines() if line.startswith("RESULT:")]
    if len(lines) != 1:
        return None
    line = lines[0]
    if line.startswith("RESULT: PASS"):
        return "PASS"
    if not line.startswith("RESULT: FAIL (") or not line.endswith(")"):
        return "FINAL"
    reason = line[len("RESULT: FAIL ("):-1]
    if reason.startswith(CONSEQUENCE_PREFIX):
        return "CONSEQUENCE"
    return GEN1_RNG_REASON_CLASS.get(reason, "FINAL")


def _has_exact_rng_miss(text):
    return classify_gen1_result(text) == "CAUSE_RNG"


SPECIES_BUDGET_MISS = "RNG: the species hunt met only duplicates within its battle budget"
# Explode Mode's own RNG outcome: the linked mon was KO'd before the coerced turn could fire
# (the Lua card lands the string; the phrase is pinned here and cross-checked against the body
# once it exists).
EXPLODE_KO_MISS = "RNG: the wild foe knocked the linked mon out before EXPLOSION"
# Only explode_new may retry a third miss; the two SYNTH controls retain three attempts.
EXPLODE_BALL_MISS = "hunt ended out-of-balls"
LATE_ATTEMPT_RNG = (
    (SPECIES_BUDGET_MISS, frozenset()),  # preserve the existing species-hunt policy
    (EXPLODE_KO_MISS, frozenset({"explode_new"})),
    (EXPLODE_BALL_MISS, frozenset({"explode_new"})),
)


def retryable_gen1_rng(game, results, attempt, limit=2, *, scenario=None):
    """May these receipts restart one whole gen1_new run?

    Attempt 1 is the original rule: a CAUSE_RNG on one side and nothing worse than CONSEQUENCE
    on the other. Attempts 1 and 2 may retry an RNG cause. Later attempts require a
    phrase whose tuple scope admits the named scenario (an empty set is unscoped).
    Species-budget misses retain their existing policy; explode_new admits foe-KO
    and out-of-balls misses into its fourth attempt. The SYNTH controls stop at three.
    Omitting the scenario never admits a scoped late phrase.

    A half with NO RESULT is NOT "worse than CONSEQUENCE" — it made no claim at all. The RNG
    half's FAIL is what ended the wait (`DuoRun.wait_for` -> `ClientFinishedEarly`) and the
    runner tore the other cartridge down mid-play, so its silence is an EFFECT of the retryable
    failure, not a second one. Reading it as disqualifying is what cost species_clause_new its
    whole 8-attempt budget on one ball miss (H-7: A `RESULT: FAIL (hunt ended out-of-balls)`,
    B still hunting, run ended `FAIL (attempt 1 of 8)` with no retry) while the same phrase on
    linked_faint_active_new DID retry — there both halves had written a RESULT first. This is
    the seam BOTH paths share: `run()`'s GameRngMiss branch calls it after `wait_results`, where
    both halves are present by construction, so only the early-finish path changes.
    """
    if not rng_retry_family(game) or attempt >= limit:
        return False
    classes = [classify_gen1_result(text) for text in results.values()]
    if "CAUSE_RNG" not in classes:
        return False
    if not all(c in ("CAUSE_RNG", "CONSEQUENCE", "PASS", None) for c in classes):
        return False
    # CLAUSE-FIX: preserve the zero-ball -> native pickup proof and its one-ball
    # catch honestly. Only this row gets the disclosed eight-attempt RNG budget;
    # no runtime bag write, changed activation, or retry for a harness failure.
    if scenario == "ball_gate_gen3" and SCENARIOS[scenario].get("rng_attempts") == limit:
        return True
    # link_gen3_rand (randomized Route 1 catch odds, see its registry row): attempts past 2 retry a "hunt ended whiteout" CAUSE_RNG, only that.
    if scenario == "link_gen3_rand" and attempt > 2 and SCENARIOS[scenario].get("rng_attempts") == limit:
        causes = [text for text in results.values() if classify_gen1_result(text) == "CAUSE_RNG"]
        if causes and all("hunt ended whiteout" in (text or "") for text in causes):
            return True
    if (scenario == "type_clause_gen3" and scenario_family(game) == "gen3_rr" and limit == 8
            and "RESULT: FAIL (RNG: type first encounter has no overlap)" in
            (results.get("b") or "").splitlines()):
        return True
    # Owner 2026-09-18: a ball miss (and any other CAUSE_RNG) earns TWO whole-run retries, not
    # one -- four full runner passes each lost a different scenario to a second consecutive
    # roll (species double miss, poison double KO) with no defect behind it.
    if attempt <= 2:
        return True
    causes = [text for text in results.values()
              if classify_gen1_result(text) == "CAUSE_RNG"]
    if scenario_family(game) == "gen1_new":
        # A first-battle KO dead-zones the duo hunt. Give it the same late-attempt
        # scope as a spent ball (including explode_new), without changing other gens.
        causes = [text.replace("hunt ended first-catch-battle-lost", EXPLODE_BALL_MISS)
                  for text in causes]
    return bool(causes) and all(
        any(phrase in (text or "") and (not scenarios or scenario in scenarios)
            for phrase, scenarios in LATE_ATTEMPT_RNG) for text in causes)


def scenario_attempt_limit(name, game):
    """How many whole-run attempts a scenario may take.

    One for everything that is not a `gen1_new` scenario; two for the ball-RNG retry
    (`retryable_gen1_rng`); three for species_clause_new, whose PASS may still be a
    coin-flip outcome (see `run_scenario_with_rng_retry`).
    """
    if scenario_family(game) == "gen2_new" and name in GEN2_CLAUSE_SCENARIOS:
        return 3
    if scenario_family(game) == "gen2_new" and name == "gen2_ball_gate":
        return 2   # one retry, only when a side ran out of the aide's five natural Balls (GEN2_OUT_OF_BALLS)
    if scenario_family(game) == "gen2_new" and (name in GEN2_TRADE_SCENARIOS or name in GEN2_ROUTE_LOSS_SCENARIOS):
        return 2   # one retry, only when the route's own wild battle was lost (GEN2_ROUTE_BATTLE_LOST)
    if name in GEN2_C5_REFUSED:
        return 1   # refused at hello: nothing to retry, on any pairing (tools/c5_runner.py owns a cell's one retry)
    entry = SCENARIOS.get(name, {})
    if entry.get("rule_kind") == "family" and scenario_family(game) == "gen3_rr":
        return 16
    if entry.get("rule_kind") == "gender" and scenario_family(game) == "gen3_rr":
        return 8
    if entry.get("rule_kind") == "gender" and scenario_family(game) in ("gen3_frlg", "gen3_emerald"):
        # Same-gender observation is roughly a coin flip: six fresh jitter offsets cut a wholly
        # unobserved row from ~12.5% at three attempts to ~1.6% (freeze #3 FR+LG ClauseUnobserved 3/3).
        return 6
    if entry.get("rule_kind") == "type" and scenario_family(game) == "gen3_rr":
        return 8
    if rng_retry_family(game) and entry.get("rng_attempts"):
        return entry["rng_attempts"]
    if entry.get("rule_kind") in ("species", "family"):
        return 8
    if entry.get("battle_window_case"):
        return 2 if entry["battle_window_case"] == "trainer_bench" else 1
    # a cold boot replays one fixed NEW GAME: nothing to retry (ball_gate_new)
    if not rng_retry_family(game) or entry.get("cold_boot"):
        return 1
    if scenario_family(game) != "gen1_new":
        # Gen 3: two retries (the Gen 1 default) for a half that throws Poke Balls, else one
        # attempt -- every other cause is FINAL, so there is nothing a retry could change.
        return 3 if entry.get("ball_hunt") else 1
    if name == "species_clause_new":
        return 8
    if name == "poison_new":
        # The forest hunt races the wild table against the starter's HP; run 4 of the full
        # runner lost both attempts to 'a wild foe knocked the starter out' (owner: raise it).
        return 4
    return 3


def battle_window_failure(name, receipts, attempt):
    """Early client FAIL classification; never borrowed from the generic ball-RNG policy."""
    case = SCENARIOS.get(name, {}).get("battle_window_case")
    if not case:
        return None
    from gen3_battle_window_oracle import classify_failure
    texts = {side: receipts.get(side) or "" for side in ("a", "b")}
    ready = re.search(r"(?m)^READY_BATTLE_WINDOW " + re.escape(case) + r" ([0-9A-F]{8}:[0-9A-F]{8})\b",
                      texts["a"])
    if not ready:
        return None
    return classify_failure(case=case, key=ready.group(1), receipts=texts, attempt=attempt,
                            helpers=sys.modules[__name__])


def species_reroll_state(receipts):
    """Did B's half of species_clause_new see the dupes reroll? 'observed' | 'unobserved' | 'unknown'.

    B's own `PATH` line (duo_gen1_main.lua:882) is the source: `reroll_observed` means at
    least one Route 1 encounter was A's family and the reroll prompt arrived, `unobserved`
    means B caught the other species first and the branch never ran — a PASS either way.
    """
    text = receipts.get("b") or ""
    if "PATH reroll_observed" in text:
        return "observed"
    if "PATH reroll_unobserved" in text:
        return "unobserved"
    return "unknown"


JITTER_MARKER_RE = re.compile(r"JITTER requested=(\d+) applied=(\d+) attempt=(\d+)")
JITTER_PER_ATTEMPT = 37


def jitter_for_attempt(base, attempt):
    """The idle-frame count for one attempt: the harness's own value, moved per attempt.

    Two attempts of a scenario differ only through network-timing jitter, so a retry that asks
    for the same idle count is the same run twice — the harness print, the value written into
    the stubs and this file's own check must all use this one expression.
    """
    return base + (attempt - 1) * JITTER_PER_ATTEMPT


def jitter_problems(text, expected_requested, *, validated_launch_refusal=False):
    """The driver's echo of the harness's own `--idle-jitter` value, from its result file.

    The harness writes `idle_jitter` into each stub; the driver logs what it requested, what it
    applied and which attempt it was. A missing marker means the check could not run — a harness
    finding, not a pass — and a count the harness did not ask for means this attempt did not
    vary the timing the retry rule claims to have varied.

    A scenario-declared launch refusal finishes before gameplay jitter. The caller may
    set validated_launch_refusal only AFTER the existing oracle validates that side's
    refusal evidence; the result must still carry PASS and REFUSED_AT_LAUNCH.
    """
    if (validated_launch_refusal
            and re.search(r"(?m)^RESULT: PASS(?: \([^\r\n]*\))?\r?$", text or "")
            and re.search(r"(?m)^REFUSED_AT_LAUNCH \[SLink-gen3\] refused: ", text or "")):
        return []
    matches = JITTER_MARKER_RE.findall(text or "")
    if not matches:
        return ["JITTER marker missing"]
    requested, applied, _attempt = (int(value) for value in matches[0])
    if applied != requested or requested != expected_requested:
        return ["JITTER applied != requested"]
    return []


# Scenarios whose verdict needs a LIVE leg to have finished, not just two client RESULT lines:
# the flag is set only inside the live assert (assert_reconnect_new / assert_admit_randomized_new),
# and the post-result oracle refuses to describe a save that leg never produced.
GEN2_CLAUSE_SCENARIOS = ("gen2_type_clause", "gen2_gender_clause", "gen2_species_clause")
LIVE_LEG_SCENARIOS = ("reconnect_new", "admit_randomized_new", "gen2_reconnect", "gen2_soft_reset",
                      "reconnect_gen3", "trade_reset_commit_gen3", "trade_reset_success_gen3")

RECONNECT_GAMEPLAY_EVENTS = ("capture", "linked", "no_catch", "dead_zone")


def _event_counts(rows):
    return {name: sum(row.get("type") == name for row in rows) for name in RECONNECT_GAMEPLAY_EVENTS}


def _new_events(problems, events_before, events_after):
    """The rows `events_after` gained over `events_before`, in the file's own order.

    events.json is NEWEST-FIRST: the server appends with `appendleft` (server/server.py:1488)
    and persists `list(self._recent_events)` (:1481), so a reconnect's new rows are the FRONT of
    the list and a tail slice is the OLDEST history instead. The ring is capped at 200 rows
    (:79), so at the cap every new row also drops the oldest one and the surviving history is a
    PREFIX of the previous list; it is matched here against the tail of the new one. Anything
    else — a row spliced into the middle of the history, a shorter or reordered list — is a
    rewrite, and reading the new hellos out of a rewritten log would be guessing.
    """
    if not events_before:
        return events_after
    kept = min(len(events_after), len(events_before))
    while kept > 0 and events_after[len(events_after) - kept:] != events_before[:kept]:
        kept -= 1
    if kept == 0:
        problems.append("events log shrank or was rewritten")
        return []
    return events_after[:len(events_after) - kept]


# Identity-bearing fields of the (200-char truncated) `TX hello - {...}` receipt lines: a refresh hello may change the AREA (the
# client re-announces it when the map settles after CONTINUE) but nothing here. ot_id and the party keys are past the truncation, so
# the server's own view (events.json "Connected (" rows of one text, the locked OT, identity_error) carries that half of the check.
SAME_SAVE_HELLO_IDENTITY_FIELDS = ("artifact_kind", "foundation", "companion_abi", "badges", "ball_count", "has_pokeballs")
_RECONNECT_HELLO_LINE_RE = re.compile(r"(?m)^RECONNECT_HELLO\b.*$")
_TX_HELLO_LINE_RE = re.compile(r"(?m)^TX hello - (\{.*)$")


def same_save_hello_problems(result_text):
    """Violations in a same-save phase receipt (empty = none). The Gen 3 reconnect driver logs ONE `RECONNECT_HELLO <phase> count=N`
    line (N = hellos sent when the phase starts; a companion cartridge sends capability-refresh hellos too, so N = 1..3 is normal),
    and every `TX hello` of the phase must carry the same identity-bearing fields as the first. Any hello line for another phase
    (a different save), any line this check cannot parse, or identity drift between the hellos is a violation."""
    problems, parsed = [], []
    for line in _RECONNECT_HELLO_LINE_RE.findall(result_text or ""):
        m = re.fullmatch(r"RECONNECT_HELLO (\S+) count=(\d+)\s*", line)
        if m:
            parsed.append((m[1], int(m[2])))
        else:
            problems.append(f"unknown reconnect hello line {line.strip()!r}")
    other = sorted({phase for phase, _count in parsed if phase != "same_save"})
    if other:
        problems.append(f"hello for another save in the same-save window: {', '.join(other)}")
    hellos = [{k: v for k, v in re.findall(r'"(' + "|".join(SAME_SAVE_HELLO_IDENTITY_FIELDS) + r')":("[^"]*"|-?\d+|true|false)', body)}
              for body in _TX_HELLO_LINE_RE.findall(result_text or "")]
    for k, hello in enumerate(hellos[1:], 2):
        drift = sorted(f for f in SAME_SAVE_HELLO_IDENTITY_FIELDS if f in hello and f in hellos[0] and hello[f] != hellos[0][f])
        if drift:
            problems.append(f"hello #{k} differs from hello #1 in {', '.join(drift)} (not a capability refresh of one save)")
    return problems


def same_save_hello_seen(result_text):
    """The wait predicate: True once a `RECONNECT_HELLO same_save count=N` line with N >= 1 is there. It RAISES on a violation
    (same_save_hello_problems) instead of waiting out the timeout, so a mixed same/different-save window fails at once."""
    problems = same_save_hello_problems(result_text)
    if problems:
        raise RuntimeError("same-save reconnect window is not one save: " + "; ".join(problems))
    return re.search(r"(?m)^RECONNECT_HELLO same_save count=[1-9]\d*\s*$", result_text or "") is not None


_CONNECTED_ROW_RE = re.compile(r"Connected \((.+), (\d+) mons\)")
_HELLO_FACTS_RE = re.compile(r"(?m)^HELLO_FACTS party=(absent|\d+) party_hidden=(true|false)\s*$")


def hello_facts_from(result_text):
    """The client-side facts of each hello a phase sent, in order (lua/tests/duo/duo_gen3_main.lua `HELLO_FACTS`): the party it carried
    (`None` when the field is absent, else its length) and whether it declared the party WITHHELD."""
    return [{"party": None if p == "absent" else int(p), "hidden": h == "true"} for p, h in _HELLO_FACTS_RE.findall(result_text or "")]


def reconnect_hello_party_problems(new_hellos, events_before, hello_facts=None):
    """Identity and party-size problems among A's NEW accepted hello rows (newest first, as events.json stores them).

    `hello_facts=None` is the strict rule: every row is the same text (one rom, one party size). With `hello_facts` (hello_facts_from) one
    relaxation exists: a row that reports 0 mons while the baseline reported more is accepted ONLY when the client-side fact for that very
    hello says its party was WITHHELD (party_hidden=true). The server keeps the stored party intact for a withheld hello (server/state.py:
    1958-1959, 2065 `if not hidden`), but REPLACES it for an empty party that is not withheld (:2067, :2087-2089 party_keys emptied), so a
    present-and-empty party stays a failure, as does any other count; the rom must be one throughout and at least one hello must carry the
    baseline party."""
    rows = [_CONNECTED_ROW_RE.fullmatch(row.get("text", "")) for row in new_hellos]
    if hello_facts is None:
        return [] if len({row.get("text") for row in new_hellos}) == 1 else [
            "A's accepted reconnect hellos name more than one cartridge/party (another identity)"]
    if any(m is None for m in rows):
        return ["A's accepted reconnect hello text is not `Connected (<rom>, <n> mons)`"]
    problems = []
    if len({m[1] for m in rows}) != 1:
        problems.append("A's accepted reconnect hellos name more than one cartridge (another identity)")
    baseline = next((int(m[2]) for m in (_CONNECTED_ROW_RE.fullmatch(r.get("text", "")) for r in events_before
                                         if r.get("type") == "hello" and r.get("player") == "a") if m), None)
    counts = [int(m[2]) for m in reversed(rows)]                     # chronological, to line up with the client's own hello log
    if baseline is None:
        return problems + (["A's accepted reconnect hellos differ in party size and the baseline has no A hello to judge them by"]
                           if len(set(counts)) != 1 else [])
    odd = [i for i, n in enumerate(counts) if n != baseline]
    if odd and len(hello_facts) != len(counts):
        problems.append(f"{len(counts)} new A hello rows but {len(hello_facts)} client HELLO_FACTS lines: a hello whose party differs "
                        f"from the baseline ({baseline}) cannot be matched to its client-side facts")
    else:
        for i in odd:
            if counts[i] != 0:
                problems.append(f"hello #{i + 1} reports {counts[i]} mons, the baseline party is {baseline}")
            elif hello_facts[i] != {"party": 0, "hidden": True}:
                problems.append(f"hello #{i + 1} reports 0 mons but its party was not withheld "
                                f"(client: party={hello_facts[i]['party']}, party_hidden={hello_facts[i]['hidden']}): the server "
                                f"treats that as an EMPTY party")
    if len(odd) == len(counts):
        problems.append(f"no A hello carried the baseline party ({baseline} mons)")
    return problems


def accepted_reconnect_hellos(events_before, events_after, hello_facts=None):
    """True when A added at least one hello row since the baseline, every one ACCEPTED ("Connected (") and one identity
    (reconnect_hello_party_problems), with no event-log rewrite."""
    problems = []
    new_hellos = _new_a_hellos(problems, events_before, events_after)
    return (not problems and bool(new_hellos)
            and all(row.get("text", "").startswith("Connected (") for row in new_hellos)
            and not reconnect_hello_party_problems(new_hellos, events_before, hello_facts))


def _new_a_hellos(problems, events_before, events_after):
    """A's hellos among the rows this reconnect actually added, not among all of history."""
    return [row for row in _new_events(problems, events_before, events_after)
            if row.get("type") == "hello" and row.get("player") == "a"]


def reconnect_same_problems(before, after, events_before, events_after, linked_key, ot_id,
                            allow_accepted_refreshes=False, hello_facts=None):
    """Public/persisted C-2 facts; empty means a safe same-save reconnect."""
    problems = []
    a = (after.get("status", {}).get("players") or {}).get("a") or {}
    b = (after.get("status", {}).get("players") or {}).get("b") or {}
    if not a.get("connected") or a.get("identity_error"):
        problems.append("A did not reconnect with accepted identity")
    if not b.get("connected"):
        problems.append("B stopped being connected during A relaunch")
    if linked_key not in (a.get("party_keys") or []):
        problems.append("A's resumed party lacks the linked key")
    if before.get("links") != after.get("links"):
        problems.append("the link changed across the same-save relaunch")
    if str((after.get("player_identity", {}).get("a") or {}).get("ot_id")) != str(ot_id):
        problems.append("A's locked OT ID changed")
    if _event_counts(events_before) != _event_counts(events_after):
        problems.append("a " + "/".join(RECONNECT_GAMEPLAY_EVENTS) + " count changed")
    new_hellos = _new_a_hellos(problems, events_before, events_after)
    if allow_accepted_refreshes:
        if not new_hellos or any(not row.get("text", "").startswith("Connected (")
                                 for row in new_hellos):
            problems.append("A did not add only accepted reconnect hellos")
        else:
            problems += reconnect_hello_party_problems(new_hellos, events_before, hello_facts)
    elif len(new_hellos) != 1 or not new_hellos[0].get("text", "").startswith("Connected ("):
        problems.append("A did not add exactly one accepted reconnect hello")
    return problems


def reconnect_wrong_problems(before_bytes, after_bytes, status, events_before, events_after,
                             allow_rejected_refreshes=False):
    """C-1: only the rejected hello event is allowed; the link file is byte-identical."""
    problems = []
    a = (status.get("players") or {}).get("a") or {}
    if "Identity mismatch for slot A" not in (a.get("identity_error") or ""):
        problems.append("A's different-OT save was not rejected")
    if not ((status.get("players") or {}).get("b") or {}).get("connected"):
        problems.append("B disconnected during A's wrong-save rejection")
    if before_bytes != after_bytes:
        problems.append("links.json bytes changed after the rejected hello")
    if _event_counts(events_before) != _event_counts(events_after):
        problems.append("a rejected save changed a " + "/".join(RECONNECT_GAMEPLAY_EVENTS)
                        + " count")
    new_hellos = _new_a_hellos(problems, events_before, events_after)
    if allow_rejected_refreshes:
        if not new_hellos or any(row.get("text") != "REJECTED — wrong save/slot"
                                 for row in new_hellos):
            problems.append("A did not add only WRONG SAVE rejection hellos")
    elif len(new_hellos) != 1 or new_hellos[0].get("text") != "REJECTED — wrong save/slot":
        problems.append("A did not add exactly one WRONG SAVE rejection hello")
    return problems


BALL_GATE_FORBIDDEN_EVENTS = {"no_catch", "dead_zone", "force_faint", "force_explode",
                              "memorialize"}


def ball_gate_fact(receipt, label):
    """Read one named, structured cartridge milestone; duplicate/missing facts fail closed."""
    matches = [line[len(label) + 1:] for line in (receipt or "").splitlines()
               if line.startswith(label + " ")]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {label} receipt, got {len(matches)}")
    return json.loads(matches[0])


def ball_gate_pre_problems(status, links, events, hellos):
    """D-2: independent New Game identities, with both server gates still closed."""
    problems = []
    ids = []
    for inst in ("a", "b"):
        hello = hellos[inst]
        ot_id = hello.get("ot_id")
        ids.append(ot_id)
        if not isinstance(ot_id, int) or ot_id <= 0 or ot_id > 0xFFFF:
            problems.append(f"{inst} cold hello has no real trainer ID")
        if (hello.get("map") != 0x26 or hello.get("party_count") != 0
                or hello.get("has_pokeballs") is not False or hello.get("ball_count") != 0):
            problems.append(f"{inst} first hello was not the empty-party, zero-ball bedroom")
        player = (status.get("players") or {}).get(inst) or {}
        if (not player.get("connected") or player.get("nuzlocke_active") is not False
                or player.get("ball_count") != 0):
            problems.append(f"{inst} server ball gate opened before the first ball")
    if ids[0] == ids[1]:
        problems.append("cold New Game trainer IDs are not distinct")
    if links:
        problems.append("a pair linked before the first Poké Ball")
    if any(row.get("type") in BALL_GATE_FORBIDDEN_EVENTS | {"linked"} for row in events):
        problems.append("server emitted a link/death event before either starter arrived")
    return problems


def ball_gate_starters_problems(status, links, events, pre_rival):
    """Gifts link before balls, while both independent nuzlocke gates stay closed."""
    problems = []
    if len(links) != 1 or links[0].get("area_id") != "gift_map_40" or links[0].get("status") != "alive":
        problems.append("starter gifts did not form one alive gift_map_40 pair")
    else:
        link = links[0]
        for inst in ("a", "b"):
            if (link.get(inst) or {}).get("key") != pre_rival[inst].get("key"):
                problems.append(f"{inst} starter key differs from the gift link")
    for inst in ("a", "b"):
        fact = pre_rival[inst]
        player = (status.get("players") or {}).get(inst) or {}
        if (not fact.get("key") or fact.get("capture_count", 0) < 1
                or fact.get("gift") is not True or fact.get("ball_count") != 0):
            problems.append(f"{inst} starter was not captured as a zero-ball gift")
        if player.get("nuzlocke_active") is not False or player.get("ball_count") != 0:
            problems.append(f"{inst} server ball gate opened on the starter gift")
        if not any(row.get("type") == "linked" and row.get("player") == inst for row in events):
            problems.append(f"{inst} gift-link event missing from events.json")
    if any(row.get("type") in BALL_GATE_FORBIDDEN_EVENTS for row in events):
        problems.append("starter gifts resolved a wild encounter or caused a death")
    return problems


def ball_gate_lab_problems(status, links, expected_link, events, labs, pre_rival, released, server_log):
    """Both real Rival1 losses must emit faint but leave the partner untouched."""
    problems = []
    for inst in ("a", "b"):
        lab = labs[inst]
        if (lab.get("result") != 1 or lab.get("hp", 0) <= 0 or lab.get("faint_count", 0) < 1
                or lab.get("ball_count") != 0 or lab.get("has_pokeballs") is not False):
            problems.append(f"{inst} lab loss lacked the real pre-ball faint/heal receipt")
        if lab.get("force_faint") != 0 or lab.get("memorialize") != 0:
            problems.append(f"{inst} received a partner death command before balls")
        player = (status.get("players") or {}).get(inst) or {}
        if player.get("nuzlocke_active") is not False or player.get("ball_count") != 0:
            problems.append(f"{inst} server gate was open during the lab loss")
        if f"[{inst}] faint key=" not in server_log:
            problems.append(f"{inst} lab faint was absent from slink.log")
    if pre_rival["b"].get("hp", 0) <= 0 or pre_rival["b"].get("hp") != released["b"].get("hp"):
        problems.append("B's parked starter HP changed when A fainted")
    if released["b"].get("force_faint") != 0 or released["b"].get("memorialize") != 0:
        problems.append("B received a death command while parked outside battle")
    if links != [expected_link] or any(row.get("type") in BALL_GATE_FORBIDDEN_EVENTS for row in events):
        problems.append("starter pair changed or a wild encounter/death resolved before balls")
    return problems


def ball_gate_flip_problems(status, flips):
    """Both activations must follow the filtered bag_received engine site and the bag read."""
    problems = []
    for inst in ("a", "b"):
        fact = flips[inst]
        player = (status.get("players") or {}).get(inst) or {}
        if (fact.get("signal_count", 0) < 1 or fact.get("ball_count", 0) < 1
                or fact.get("has_pokeballs") is not True):
            problems.append(f"{inst} client did not activate from bag_received")
        if player.get("nuzlocke_active") is not True or player.get("ball_count", 0) < 1:
            problems.append(f"{inst} server did not activate after the bag tick")
    return problems


def ball_gate_after_labs_problems(labs, after):
    """A and B each remain healthy while the other side's rival faint is delivered."""
    problems = []
    for inst in ("a", "b"):
        if after[inst].get("hp") != labs[inst].get("hp"):
            problems.append(f"{inst} starter HP changed after the partner's lab faint")
        if after[inst].get("force_faint") != 0 or after[inst].get("memorialize") != 0:
            problems.append(f"{inst} received a late pre-ball death command")
    return problems


def seen_counters(text, label):
    """The driver's `SEEN name=<n> ...` line as a dict, or a failure that names it."""
    match = re.search(r"^SEEN (.*)$", text or "", re.M)
    if not match:
        raise RuntimeError(f"{label}: no SEEN line in the receipt")
    return {name: int(value) for name, value in re.findall(r"(\w+)=(\d+)", match.group(1))}


# S-7's save witness. The duo body dumps CartRAM[0x498:0x8000] from inside the
# SaveMenu.save hook — data/games/gen1_rby/engine_signals.json:129-135, capture_offset 3, i.e.
# the instruction after `call SaveGameData` returns (pret engine/menus/save.asm:165-166, with
# SaveGameData at :290-295) — into patch/build/e2e_<scenario>_<inst>_<attempt>_witness.bin,
# overwriting on every save, and logs one SAVE_WITNESS_DUMP line per dump
# (lua/tests/duo/duo_gen1_main.lua:77-129). The slice starts past the three sprite buffers
# (3 * 0x188) and is the one docs/gen1_requirements.md pins for S-7.
SAVE_WITNESS_START = 0x498
SAVE_WITNESS_END = 0x8000
SAVE_WITNESS_BYTES = SAVE_WITNESS_END - SAVE_WITNESS_START  # 0x7B68 = 31592
SAVE_WITNESS_DUMP_RE = re.compile(
    r"SAVE_WITNESS_DUMP path=(\S+) bytes=(\d+) saves=(\d+) frame=(\d+)")
# A failed or skipped dump is a trailing marker: the file on disk is then an EARLIER save's,
# which is why the check reads the dump outcomes in order (see check_save_witness).
SAVE_WITNESS_TROUBLE_RE = re.compile(r"SAVE_WITNESS_DUMP(?:_FAIL|_SKIPPED)\b")


def save_witness_diff(site, flushed, limit=4):
    """The SRAM addresses where the hook-time dump and the flushed file disagree.

    A mismatch message that said only "different" would send the next reader hunting for a
    defect; the addresses are in SRAM terms, which is what a reader can look up.
    """
    out = []
    for index, (a, b) in enumerate(zip(site, flushed, strict=True)):
        if a != b:
            out.append(SAVE_WITNESS_START + index)
            if len(out) >= limit:
                break
    return out


def rel_to_repo(path):
    """`os.path.relpath(path, REPO)`, or the path itself when they are on different drives.

    The harness's data dirs are temp dirs on C: and the repo need not be on C: — a bare
    `os.path.relpath` raises there, turning a diagnostic message into the crash it was
    reporting.
    """
    try:
        return os.path.relpath(path, REPO)
    except ValueError:
        return str(path)


class ClientFinishedEarly(Exception):
    """A wait whose predicate is still false while a cartridge has already ended.

    The whiteout_new hang (2026-09-17): both receipts carried
    `RESULT: FAIL (the deposit did not send party_to_box for the linked key)` and the runner
    sat in the BOTH_BOXED gate's 1800 s budget anyway — no orchestrate-time wait looked at the
    receipts at all. `DuoRun.wait_for` raises this instead, `run()` records it and returns False,
    and `run_scenario_with_rng_retry` still classifies the receipts (a mid-wait ball miss has to
    keep its retry).
    """

    def __init__(self, finished: dict, awaited: str, exited=()):
        self.finished = dict(finished)
        self.awaited = awaited
        self.exited = sorted(exited)
        detail = "; ".join(f"{inst}: {reason or 'no RESULT'}"
                           for inst, reason in sorted(self.finished.items()))
        if self.exited:
            detail += f"; process gone with no RESULT: {', '.join(self.exited)}"
        super().__init__(f"a client finished before {awaited!r} ({detail})")


# links.json's per-player maps are dicts (insertion-ordered) and three of them are set-derived
# lists (`list(keys)`/`list(areas)`, server/state.py:3077-3082) — Python's set iteration order
# is not canonical, so two saves of the SAME state can differ in order alone. That is why the
# soft-reset comparison is canonical, and why exactly those three paths are sorted before
# comparing: a value difference still fails, an order difference does not. Nothing is excluded
# silently — the note names every path whose order differed.
_LINKS_SET_PATHS = ("retry_areas", "bonus_keys", "pending_memorials")


def canonical_json(document) -> str:
    """`document` with key order and set-derived list order normalized."""
    normalized = json.loads(json.dumps(document))
    for name in _LINKS_SET_PATHS:
        node = normalized.get(name)
        if isinstance(node, dict):
            for player, values in node.items():
                if isinstance(values, list):
                    node[player] = sorted(values)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def first_json_difference(left, right, path="$"):
    """The first differing path and both values, or None when the documents are equal."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                return (f"{path}.{key}", left.get(key, "<missing>"), right.get(key, "<missing>"))
            found = first_json_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path} (length)", len(left), len(right)
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            found = first_json_difference(a, b, f"{path}[{index}]")
            if found:
                return found
        return None
    if left != right:
        return path, left, right
    return None


def reordered_json_keys(left, right):
    """Top-level keys whose insertion order differs between two canonically equal documents."""
    out = []
    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        if isinstance(a, dict) and isinstance(b, dict) and list(a) != list(b):
            out.append(f"{key} ({'|'.join(list(a))} -> {'|'.join(list(b))})")
    return out


def is_formed_link(entry) -> bool:
    """True when a links.json row is a FORMED pair, not a dead-zone record.

    Dead zones live in the same table with both halves null — `a: null, b: null`,
    `status: "dead"`, `cause: "dead_zone"` — a no-catch area lock, not a link (the poison lane
    produced two: an incidental Route 1 encounter and the wrong-species first forest encounter).
    Anything carrying a key on either side, or any other status/cause pair, counts as formed.
    """
    if entry.get("status") != "dead" or entry.get("cause") != "dead_zone":
        return True
    for field in ("a", "b", "encounter_a", "encounter_b"):
        if (entry.get(field) or {}).get("key"):
            return True
    return False


def terminal_result(text):
    """The last `RESULT:` line of a receipt, or '' — the client's own terminal marker."""
    lines = [line.strip() for line in (text or "").splitlines() if line.startswith("RESULT:")]
    return lines[-1] if lines else ""


def wait_for(desc, pred, timeout, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for {desc}")


def marker(text, pattern, label):
    """The named driver marker's regex match, or a failure that names it.

    A marker is the driver's own log line; one that is absent is absent evidence, so an oracle
    refuses rather than reading a default. The oracle re-derives the ranges from the matched
    values instead of trusting the driver's pass/fail, so a driver that logged a bad number
    without failing still fails here.
    """
    match = re.search(pattern, text or "")
    if not match:
        raise RuntimeError(f"{label}: marker /{pattern}/ not found in the receipt")
    return match


def extract_keys(text):
    """MYKEY <slot> <key> lines -> {slot: key}."""
    keys = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "MYKEY":
            keys[int(parts[1])] = parts[2]
    return keys


def extract_marks(text, tag):
    """'<tag> <value>' lines -> [values]."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == tag:
            out.append(parts[1])
    return out


# The saved money sits in the main-data block at sMainData + (wPlayerMoney - wMainDataStart),
# the same derivation gen1_codec._BAG_COUNT uses for the bag. wMainDataStart/wPlayerMoney are
# $D2F7/$D347 in R/B (pokered.sym:19158,19167) and $D2F6/$D346 in Yellow
# (pokeyellow.sym:22386,22395) — the delta is +$50 either way, so the SAVED offset is the same
# in all three titles: $25A3 + $50 = $25F3. ram/wram.asm:1749,1761 ('ds 3 ; BCD').
# lua/tests/gen1_rb_point_fields.lua:11-19 is the WRAM twin of this decoder.
SAVED_MONEY_DELTA = 0x50
SAVED_MONEY_SIZE = 3


def saved_money(sram):
    """The player's money from a 32 KiB SRAM image, decoded from its three BCD bytes.

    No helper existed for this: server/adapters/gen1_codec.py decodes the party, the boxes,
    the bag and the checksums, but not money (grep 'money' over it returns nothing).
    """
    if REPO not in sys.path:
        sys.path.insert(0, REPO)  # python tools/e2e_duo.py otherwise has tools/ at sys.path[0]
    from server.adapters import gen1_codec as codec

    base = codec.SRAM_LAYOUT["sMainData"] + SAVED_MONEY_DELTA
    value = 0
    for byte in sram[base:base + SAVED_MONEY_SIZE]:
        value = value * 100 + (byte >> 4) * 10 + (byte & 0x0F)
    return value


# ── Gen 3 (FRLG) new client: titles, the save witness and saved-state readers ─────────────
# `rom` is the dump's file name, looked up at the repo root and then each parent (a worktree's
# dumps live in the main checkout, docs/gen3_requirements.md Pins); `saveram` is the name
# BizHawk's gamedb files a clean dump's battery under (tests/fixtures/gen3/README.md
# "boot-check") -- never the ROM-derived gen3_fixtures.saveram_name().
# P5 (card C5-5): Radical Red. `staged` names the already-built ROM directly (ROM_REL, the
# companion patch, ships from patch/build/) -- unlike firered/leafgreen it is not a raw dump the
# repo-root/parents search in _gen3_rom applies to, so `staged` short-circuits that search.
# `saveram` is pinned from tools/mkstates.py:117 / the RR boot-check note in
# tools/gen3_fixtures.py:saveram_name (BizHawk has no gamedb hash for this ROM, so it derives
# the battery name from the launched filename, underscores->spaces -- "slink_RR.gba" is what
# actually gets launched here, unstaged, so this is that rule applied to that exact name).
GEN3_EXP_TITLE = "emerald_expansion_28877d73"


def gen3_emerald_engine(title) -> bool:
    """X3: the titles on the pokeemerald engine (vanilla Emerald + the expansion reference build
    share the maps, the whiteout landing and the START-menu flows); data stays per title."""
    return title in ("emerald", GEN3_EXP_TITLE)


GEN3_TITLES = {
    "firered": {"rom": "Pokemon - FireRed Version (USA).gba",
                "saveram": "Pokemon - FireRed Version (USA).SaveRAM"},
    "leafgreen": {"rom": "Pokemon - LeafGreen Version (USA).gba",
                  "saveram": "Pokemon - LeafGreen Version (USA).SaveRAM"},
    "radical_red": {"staged": ROM_REL, "saveram": "slink RR.SaveRAM"},
    # E4: BizHawk's gamedb knows a clean BPEE dump (tools/gen3_fixtures.py EMERALD_SAVERAM).
    "emerald": {"rom": "Pokemon - Emerald Version (USA, Europe).gba",
                "saveram": "Pokemon - Emerald Version (USA, Europe).SaveRAM"},
    # X3: the pokeemerald-expansion reference build -- never committed, built by
    # tools/build_expansion.py into .cache/expansion-output/reference and staged as
    # patch/build/gen3_pokeemerald.gba; BizHawk's gamedb does not know it, so the battery is
    # named after the staged file (gen3_fixtures.saveram_name).
    GEN3_EXP_TITLE: {"artifact": "pokeemerald.gba", "saveram": "gen3 pokeemerald.SaveRAM"},
}
# The raw, UNPATCHED Radical Red dump (patch/tools/build.py:91 DEFAULT_RR, patch/README.md:18),
# for clean_rr_refused_gen3's refusal-proof side only (`rom_kind`: "clean"). It resolves through the
# ORDINARY repo-root/parents dump search (no `staged` entry), same as firered/leafgreen, because
# unlike the companion build it genuinely is a raw dump to stage; its battery name is computed
# from the staged path at resolve time (_gen3_battery_path), not pinned here, since stage_rom's
# naming is derived from the ORIGINAL filename and hand-transcribing it risks a typo.
GEN3_CLEAN_RR_ROM = "Pokemon - Radical Red.gba"
GEN3_FIXTURES = os.path.join(REPO, "tests", "fixtures", "gen3")
# Each title's pack directory under data/games (the same split duo_gen3_main.lua and
# gen3_scripted_play.lua PROFILE_PACK_BY_TITLE make).
GEN3_PACKS = {"firered": "gen3_frlg", "leafgreen": "gen3_frlg", "radical_red": "gen3_rr",
              "emerald": "gen3_emerald", GEN3_EXP_TITLE: "gen3_exp/28877d73"}
GEN3_RR_PROFILE = os.path.join(REPO, "data", "games", "gen3_rr", "profile.json")
# Receipt lines the Gen 3 driver writes (lua/tests/duo/duo_gen3_main.lua): one per non-tick
# event it SENDS and one per command it RECEIVES.
GEN3_TX_RE = r"(?m)^TX {event} {key}(?=\s|$)"   # key "-" for an event that carries none
GEN3_RX_RE = r"(?m)^RX {cmd} key={key}(?=\s|$)"
# lua/gen3/entry.lua Entry.admit_routed's refusal of a clean companion-required cartridge, as run.lua
# logs it (console.log "[SLink-gen3] refused: <why>", teed with the "[client] " prefix by the driver).
def gen3_clean_refusal_re(title="radical_red"):
    """The refusal line for a CLEAN `title` cartridge, as the driver tees it (`[client] ` prefix)."""
    return (rf"(?m)^\[client\] \[SLink-gen3\] refused: this {re.escape(title)} cartridge needs the SLink "
            r"companion patch; prepare it through the Manager or /patcher$")


GEN3_CLEAN_REFUSAL_RE = gen3_clean_refusal_re()


def gen3_refused_side_problems(label, text, title="radical_red"):
    """A side that was REFUSED at launch (`expect_refused`): the companion-patch refusal line, the driver's
    REFUSED_AT_LAUNCH and WRITES 0, and nothing a working client leaves behind."""
    return gen3_receipt_problems(
        label, text,
        required=[gen3_clean_refusal_re(title), r"(?m)^REFUSED_AT_LAUNCH \[SLink-gen3\] refused: ", r"(?m)^WRITES 0$"],
        forbidden=[r"(?m)^MYKEY ", r"(?m)^TX ", r"(?m)^RX ", r"(?m)^\[client\] \[SLink-gen3\] write ",
                   r"(?m)^\[client\] \[SLink-gen3\] \S+/\S+ \(\w+ by \w+\) player ", r"(?m)^SAVE_WITNESS_DUMP"])


def gen3_refused_server_problems(status, side, links=()):
    """The server never saw the refused side: it is not connected and nothing is linked."""
    problems = []
    if status is None:
        problems.append(f"server: /api/status unavailable, cannot show {side} never connected")
    elif (status.get("players") or {}).get(side, {}).get("connected"):
        problems.append(f"server: {side} connected -- the refused cartridge reached the server")
    if links:
        problems.append(f"server: a persisted link exists: {list(links)}")
    return problems


def gen3_expect_refused_problems(cfg):
    """`expect_refused` must be a collection of sides (a bare string would match by substring), and each
    refused side must boot a cartridge the launcher refuses: rom_kind clean or rand."""
    refused = cfg.get("expect_refused", ())
    if not refused:
        return []
    if not isinstance(refused, (tuple, list, set, frozenset)):
        return [f"expect_refused must be a tuple/set of sides, not {type(refused).__name__} {refused!r}"]
    kinds, problems = cfg.get("rom_kind", "companion"), []
    for side in sorted(refused, key=str):
        if side not in ("a", "b"):
            problems.append(f"expect_refused names {side!r}, not a side")
            continue
        kind = kinds.get(side, "companion") if isinstance(kinds, dict) else kinds
        if kind not in ("clean", "rand"):
            problems.append(f"expect_refused side {side} has rom_kind {kind!r}: only a clean/rand cartridge is "
                            f"refused at launch")
    return problems
# duo_gen3_main.lua dump_witness: one per extension RAM copy, right after its save's DUMP line.
GEN3_EXT_RE = re.compile(r"SAVE_WITNESS_EXT path=(\S+) bytes=(\d+) saves=(\d+)\s*$")


def gen3_codec():
    if REPO not in sys.path:
        sys.path.insert(0, REPO)  # python tools/e2e_duo.py otherwise has tools/ at sys.path[0]
    from server.adapters import gen3_codec as codec
    return codec


def gen3_key(mon) -> str:
    """The wire key (lua/gen3/reads.lua r.key): PERSONALITY:OTID, eight upper-case hex digits."""
    return f"{mon['personality']:08X}:{mon['ot_id']:08X}"


def gen3_decode(image, rr=False, title="frlg"):
    """(party, boxes) of a flash image; `boxes` maps (box, slot) -> mon, occupied only.

    `rr` selects RR's 25-scattered-box/fixed-order-party layout (gen3_codec party_from_save/
    boxes_from_save(rr=True), pinned commit 62887460) instead of vanilla FRLG's 14 boxes.
    `title` is a gen3_codec title/alias (frlg, firered, leafgreen, emerald): Emerald's
    SaveBlock sizes and party offsets differ (gen3_codec TITLE_EMERALD).
    """
    codec = gen3_codec()
    body = codec.split_rtc(image)[0]
    layout = None
    if title == codec.TITLE_EXPANSION:          # X2: records are masked bitfields there
        from server.adapters.gen3_expansion import EXPANSION_PARTY_LAYOUT as layout
    boxes = {}
    for box, row in enumerate(codec.boxes_from_save(body, rr=rr, title=title, layout=layout)):
        for slot, mon in enumerate(row):
            if mon["has_species"] and mon["species"]:
                boxes[(box, slot)] = mon
    return codec.party_from_save(body, rr=rr, title=title, layout=layout), boxes


def gen3_codec_title(title) -> str:
    """The gen3_codec layout title for a GEN3_TITLES title: radical_red decodes through rr=True
    on the FR skeleton, every vanilla title names its own layout."""
    return "frlg" if title == "radical_red" else title


def gen3_profile_path(title) -> str:
    """The pack profile.json this title's derived offsets live in (GEN3_PACKS)."""
    return os.path.join(REPO, "data", "games", GEN3_PACKS[title], "profile.json")


def gen3_ball_count(image, title="firered") -> int:
    """The saved POKe BALLS pocket total, the same sum lua/gen3/reads.lua read_balls() makes.

    Both layouts come from the title's pack profile, exactly as read_balls branches on them:
      * vanilla FRLG: SaveBlock1 + derived SB1_BALL_POCKET_OFFSET, SB1_BALL_POCKET_COUNT slots,
        the quantity XORed with the low u16 of SaveBlock2's encryption key (pret src/item.c:20-29);
      * Radical Red (derived BAG_IN_EWRAM, BALL_POCKET_ENC false): the pocket lives in EWRAM at
        ram.BALL_POCKET_ADDR, plaintext, and reaches the flash through RR's saved regions
        (gen3_codec._rr_regions: the three save objects plus the sectors 30-31 extension).
    """
    codec = gen3_codec()
    with open(gen3_profile_path(title), encoding="utf-8") as handle:
        profile = json.load(handle)["titles"][title]
    derived, ram = profile["derived"], profile["ram"]
    body = codec.split_rtc(image)[0]
    count = derived["SB1_BALL_POCKET_COUNT"]
    if derived.get("BAG_IN_EWRAM"):
        pocket = codec._rr_read(codec._rr_regions(body), ram["BALL_POCKET_ADDR"], count * 4)
        key = 0
    else:
        parsed = codec.parse_flash(body, title=gen3_codec_title(title))
        at = derived["SB1_BALL_POCKET_OFFSET"]
        pocket = parsed["sb1"][at:at + count * 4]
        key_at = derived["SB2_ENC_KEY_OFFSET"]
        key = int.from_bytes(parsed["sb2"][key_at:key_at + 2], "little")
    if derived.get("BALL_POCKET_ENC") is False:
        key = 0
    total = 0
    for i in range(count):
        if int.from_bytes(pocket[i * 4:i * 4 + 2], "little"):
            total += int.from_bytes(pocket[i * 4 + 2:i * 4 + 4], "little") ^ key
    return total


def _gen3_sectors(body, indexes):
    codec = gen3_codec()
    return b"".join(body[i * codec.SECTOR_SIZE:(i + 1) * codec.SECTOR_SIZE] for i in indexes)


def check_gen3_witness(witness, flushed, fixture, *, saves, rr=False, ext_ram=None, title="frlg"):
    """PLAN §5.5's check_save_witness_gen3, on bytes: returns the facts or raises, naming the rule.

    `witness` is the SRAM (flash) domain dumped inside the successful TrySavingData return,
    `flushed` the battery file BizHawk wrote, `fixture` the save the instance was seeded from,
    `saves` how many validated save dumps the receipt carries. Hash equality alone would pass an
    unchanged old save, so every rule below is required:
      * the witness is a whole, strictly qualifying image (the complete sector set);
      * the save counter advanced over the fixture's, by exactly `saves` (one per in-game save;
        a missed dump or an unwitnessed extra save both break it);
      * untouched regions are byte-equal to the fixture: sectors 28-31 (Hall of Fame / Trainer
        Tower, never written by SAVE_NORMAL, pret src/save.c HandleSavingData), and after a
        single save also the slot that save did not write;
      * the decoded party and boxes equal the flushed battery's;
      * the flushed body, its optional 16-byte RTC suffix normalized by gen3_codec.split_rtc,
        is byte-identical to the hook-time image.

    `rr=True` (P5, card C5-5) qualifies/decodes through the RR layout instead (`cfru=True`,
    `gen3_decode(..., rr=True)`) and narrows the blind "untouched" range to sectors 28-29:
    RR's own physical sectors 30-31 are a single-copy, unrotated EXTENSION that carries boxes
    20-22 and the bag (gen3_codec.RR_EXT_SECTORS/RR_EXT_ADDR) with no checksum or generation
    counter (docs/gen3/research/flash_save.md:136) -- a normal RR save CAN rewrite them, so
    byte-equality against the fixture there would be wrong, and inequality proves nothing either.
    The hook dump and the flushed file read the SAME flash, so their agreement says nothing
    about the extension's freshness (Codex C4-6b finding 4). Freshness is therefore proven, or
    left OPEN, by `ext_ram`: the live EWRAM range [RR_EXT_ADDR, +RR_EXT_SIZE) the driver copies
    inside the same save hook. The extension writer copies exactly that range verbatim, 0xFF0
    bytes per sector (gen3_codec.verify_rr_save_layout_rom anchors 0x10B8DFC), so each mapped
    payload must equal its RAM half. Without `ext_ram` the facts say extension=OPEN; they
    never claim it.
    """
    import hashlib

    codec = gen3_codec()
    if len(witness) != codec.FLASH_SIZE:
        raise RuntimeError(f"the save witness is {len(witness)} bytes, expected "
                           f"{codec.FLASH_SIZE} (0x{codec.FLASH_SIZE:X})")
    ok, why = codec.qualify_flash(witness, cfru=rr, title=title)
    if not ok:
        raise RuntimeError(f"the save witness is not a complete save (sector set): {why}")
    try:
        body, rtc = codec.split_rtc(flushed)
        fixture_body = codec.split_rtc(fixture)[0]
    except ValueError as exc:
        raise RuntimeError(f"a battery image has an unsupported length: {exc}") from exc
    before = codec.parse_flash(fixture_body, cfru=rr, title=title)["counter"]
    after = codec.parse_flash(witness, cfru=rr, title=title)["counter"]
    if after <= before:
        raise RuntimeError(f"the save counter did not advance over the fixture's "
                           f"({before} -> {after}): the witness is not a save this run made")
    if after - before != saves:
        raise RuntimeError(f"the save counter moved {before} -> {after} but the receipt "
                           f"witnessed {saves} save(s); a save went unwitnessed or a dump is "
                           f"missing")
    per_slot = codec.NUM_SECTORS_PER_SLOT
    special_end = (2 * per_slot + 2) if rr else codec.SECTORS_COUNT
    untouched = {f"sectors 28-{special_end - 1}": range(2 * per_slot, special_end)}
    if saves == 1:
        other = 1 - after % codec.NUM_SAVE_SLOTS
        untouched[f"the unwritten slot {other}"] = range(other * per_slot, (other + 1) * per_slot)
    for name, indexes in untouched.items():
        if _gen3_sectors(witness, indexes) != _gen3_sectors(fixture_body, indexes):
            raise RuntimeError(f"{name} differ from the fixture; a normal save never writes them")
    w_party, w_boxes = gen3_decode(witness, rr=rr, title=title)
    f_party, f_boxes = gen3_decode(body, rr=rr, title=title)
    if [gen3_key(m) for m in w_party] != [gen3_key(m) for m in f_party] or w_party != f_party:
        raise RuntimeError(f"the decoded party differs: witness {[gen3_key(m) for m in w_party]} "
                           f"vs flushed {[gen3_key(m) for m in f_party]}")
    if w_boxes != f_boxes:
        raise RuntimeError(f"the decoded boxes differ: witness {sorted(w_boxes)} vs flushed "
                           f"{sorted(f_boxes)}")
    site, file = hashlib.sha256(witness).hexdigest(), hashlib.sha256(body).hexdigest()
    if site != file:
        first = next(i for i, (a, b) in enumerate(zip(witness, body, strict=True)) if a != b)
        raise RuntimeError(f"the save witness does not match the flushed battery: site {site} "
                           f"vs file {file}; first differing flash offset 0x{first:05X}")
    facts = {"site": site, "file": file, "counter": (before, after), "rtc": bool(rtc),
             "extension": None}
    if rr:
        facts["extension"] = "OPEN"
        if ext_ram is not None:
            chunk = codec.CHUNK_SIZE_CFRU
            if len(ext_ram) != codec.RR_EXT_SIZE:
                raise RuntimeError(f"the live extension RAM copy is {len(ext_ram)} bytes, "
                                   f"expected {codec.RR_EXT_SIZE}")
            for n, sector in enumerate(codec.RR_EXT_SECTORS):
                saved = witness[sector * codec.SECTOR_SIZE:sector * codec.SECTOR_SIZE + chunk]
                if saved != ext_ram[n * chunk:(n + 1) * chunk]:
                    raise RuntimeError(f"extension sector {sector} is not the live EWRAM "
                                       f"0x{codec.RR_EXT_ADDR + n * chunk:08X}+0x{chunk:X} at "
                                       f"the save boundary: the save did not write this "
                                       f"state (stale or torn extension)")
            facts["extension"] = "LIVE_RAM_MATCH"
    return facts


# The title's record bounds (Codex C4-6c findings 1 and 5). Vanilla FRLG: species 1..411 --
# pret include/constants/species.h:421-423 (SPECIES_EGG 412 == NUM_SPECIES, eggs keep their real
# species in the record). Radical Red: the species ids CFRU actually defines,
# data/games/gen3_frlge/rr_species.json (the table tools/gen_gen3_profile.py already cites for
# RR's SHEDINJA_SPECIES_ID). MAX_LEVEL is each pack's derived.MAX_LEVEL (RR 250, ROM-pinned in
# data/games/gen3_rr/profile.json _src). A move's max PP is pret's CalculatePPWithBonus
# (src/pokemon.c:3898-3902) over the title's own ROM move table (rom.BATTLE_MOVES_ADDR,
# derived.BATTLE_MOVE_ENTRY_SIZE/BATTLE_MOVE_PP_OFFSET), the same read lua/gen3/boxes.lua makes.
# NUM_SPECIES = SPECIES_EGG = 412 in both vanilla trees: pokefirered include/constants/species.h:421-423,
# pokeemerald include/constants/species.h:418-420.
GEN3_VANILLA_NUM_SPECIES = 412
GEN3_RR_SPECIES = os.path.join(REPO, "data", "games", "gen3_frlge", "rr_species.json")
MAIL_NONE = 0xFF                                  # pret include/constants/items.h:451
GEN3_MAIL_ITEMS = range(121, 133)                 # ITEM_ORANGE_MAIL..ITEM_RETRO_MAIL, items.h:125-136
GEN3_MAIL_COUNT = 16                              # MAIL_COUNT = PARTY_SIZE + 10, global.h:43


def gen3_limits(title, rom=None):
    """The record bounds for `title`: {"max_level", "species" (a predicate), "max_pp"
    (move, pp_bonuses, index) -> int, or None without a ROM}. Nothing here is a literal the
    title's pack or pret does not already pin."""
    with open(gen3_profile_path(title), encoding="utf-8") as handle:
        profile = json.load(handle)["titles"][title]
    derived = profile["derived"]
    if title == "radical_red":
        with open(GEN3_RR_SPECIES, encoding="utf-8") as handle:
            known = {int(k) for k in json.load(handle)}

        def species(value):
            return value in known
    elif title == GEN3_EXP_TITLE:
        facts = os.path.join(REPO, "data", "games", GEN3_PACKS[title], "facts.json")
        with open(facts, encoding="utf-8") as handle:
            num_species = json.load(handle)["constants"]["NUM_SPECIES"]

        def species(value):
            return 1 <= value < num_species
    else:
        def species(value):
            return 1 <= value < GEN3_VANILLA_NUM_SPECIES
    max_pp = None
    if rom is not None:
        base = profile["rom"]["BATTLE_MOVES_ADDR"] - 0x08000000
        stride, off = derived["BATTLE_MOVE_ENTRY_SIZE"], derived["BATTLE_MOVE_PP_OFFSET"]

        def max_pp(move, pp_bonuses, index):
            base_pp = rom[base + move * stride + off]
            ups = (pp_bonuses >> (2 * index)) & 3          # gPPUpGetMask[index] = 3 << 2*index
            return base_pp + (base_pp * 20 * ups) // 100
    return {"max_level": derived["MAX_LEVEL"], "species": species, "max_pp": max_pp}


def gen3_status_ok(status) -> bool:
    """A valid status1 encoding (pret include/constants/battle.h:90-101): none, sleep turns 1-7,
    exactly one of poison/burn/freeze/paralysis, or toxic with its turn counter (bits 8-11)."""
    return (status == 0 or 1 <= status <= 7 or status in (0x08, 0x10, 0x20, 0x40)
            or (status & ~0xF00) == 0x80)


def gen3_record_problems(label, mon, rr=False, limits=None):
    """The cartridge's own record validity, every mutable field bounded.

    Checksum: vanilla must hold (pret GetBoxMonData's bad-egg rule); CFRU never stores one
    (gen3_codec leaves checksum_ok None). Header: has_species, not a bad egg, a species the
    title defines. Party tail: level 1..MAX_LEVEL, 0 < max_hp, hp <= max_hp, a valid status,
    mail MAIL_NONE unless a mail item is held. PP: none on an empty move slot (vanilla; CFRU's
    expansion computes all four from the table, so an empty slot must stay within move 0's), and
    never above CalculatePPWithBonus for the move in that slot. Without `limits` (no ROM) the
    PP bound cannot be checked and that is a problem, not a pass."""
    problems = []
    if rr:
        if mon.get("checksum_ok") is not None:
            problems.append(f"{label}: an RR record decoded with a vanilla checksum verdict")
    elif mon.get("checksum_ok") is not True:
        problems.append(f"{label}: the secure checksum fails")
    species_ok = limits["species"] if limits else (lambda v: bool(v))
    if mon.get("has_species") != 1 or mon.get("is_bad_egg") or not species_ok(mon.get("species", 0)):
        problems.append(f"{label}: not a valid record (has_species={mon.get('has_species')}, "
                        f"bad_egg={mon.get('is_bad_egg')}, species={mon.get('species')})")
    max_level = limits["max_level"] if limits else 100
    if "level" in mon and not 1 <= mon["level"] <= max_level:
        problems.append(f"{label}: level {mon['level']} is outside 1..{max_level}")
    if "hp" in mon:
        if mon["max_hp"] <= 0 or mon["hp"] > mon["max_hp"]:
            problems.append(f"{label}: hp {mon['hp']}/{mon['max_hp']} is not a valid HP")
        if not gen3_status_ok(mon["status"]):
            problems.append(f"{label}: status 0x{mon['status']:08X} is not a valid status1")
        mail_ok = (mon["mail"] == MAIL_NONE
                   or (mon.get("held_item") in GEN3_MAIL_ITEMS and mon["mail"] < GEN3_MAIL_COUNT))
        if not mail_ok:
            problems.append(f"{label}: mail 0x{mon['mail']:02X} without a held mail item")
    max_pp = limits and limits.get("max_pp")
    if not max_pp:
        problems.append(f"{label}: no move table to bound PP against")
    else:
        for i, (move, pp) in enumerate(zip(mon["moves"], mon["pp"], strict=True)):
            cap = 0 if move == 0 and not rr else max_pp(move, mon["pp_bonuses"], i)
            if pp > cap:
                problems.append(f"{label}: move slot {i} (move {move}) holds {pp} PP, max {cap}")
    return problems


# What moving one record may change, and NOTHING else: party-only state the game resets or
# recomputes (hp/status/mail: BoxMonToMon + CalculateMonStats; pp: BoxMonRestorePP on deposit,
# battle use) and the checksum covering those secure fields. Friendship is added ONLY when the
# scenario walked the mon in the party or fought with it (the walking/faint friendship events).
# Everything else -- PID, OT id/name, nickname, language, species, item, exp, moves, PP-ups, EVs,
# IVs, ability bit, met data, pokerus, ribbons, markings, egg flags, level and stats -- is
# invariant. The mutable fields are not free: gen3_record_problems bounds each of them, and a
# round trip additionally pins them to the withdrawal's own postcondition.
GEN3_RECORD_MUTABLE = frozenset({"hp", "status", "mail", "pp", "checksum"})
GEN3_ACTIVITY_MUTABLE = frozenset({"friendship"})
# CFRU's 58-byte CompressedPokemon drops these on every trip through a box (gen3_codec
# expand_compressed_box_mon): no contest bytes, a zero `unknown`, and the expansion sets ribbons
# bit 31 (RR ROM 0x090B696A). Ribbons are compared with that bit masked, not skipped.
GEN3_RR_BOX_LOSSY = frozenset({"contest", "unknown"})
RR_RIBBON_EXPANDED = 0x80000000


# The berries a holder EATS in battle: pret include/constants/items.h ITEM_CHERI_BERRY (133) ..
# ITEM_IAPAPA_BERRY (147) and ITEM_LIECHI_BERRY (168) .. ITEM_STARF_BERRY (174) -- exactly the
# berries whose src/data/items.json holdEffect is not HOLD_EFFECT_NONE (status cures, Leppa,
# Oran/Sitrus, the confusion berries, the pinch berries). RR's rr_battle starter holds 139 (Oran).
GEN3_BATTLE_BERRIES = frozenset(range(133, 148)) | frozenset(range(168, 175))


def gen3_consumed_ok(changed, battled):
    """Drop ONLY a battle berry eaten in battle: held_item from a GEN3_BATTLE_BERRIES id to NONE,
    and only when the mon `battled` (G5-RR-ORACLES-2, OMP review of 410d9578). Any other
    held-item change -- another item cleared, a berry swapped, a berry gone without a battle --
    stays a diff, so an SLink write that clears an item still fails."""
    return [c for c in changed
            if not (battled and c[0] == "held_item" and c[1] in GEN3_BATTLE_BERRIES and c[2] == 0)]


def gen3_record_diff(was, now, rr=False, mutable=GEN3_RECORD_MUTABLE):
    """(field, before, after) for every invariant field the two records share that differs.
    Fields only one side carries (a box record has no party tail) are not compared."""
    skip = set(mutable) | (GEN3_RR_BOX_LOSSY if rr else set())
    out = []
    for field in sorted(set(was) & set(now)):
        if field in skip:
            continue
        a, b = was[field], now[field]
        if rr and field == "ribbons":
            a, b = a & ~RR_RIBBON_EXPANDED, b & ~RR_RIBBON_EXPANDED
        if a != b:
            out.append((field, was[field], now[field]))
    return out


def gen3_withdrawn_problems(label, mon, limits, rr=False):
    """The postcondition of a mon that just came OUT of a box, on either cartridge: healthy
    (hp == max_hp, BoxMonToMon + CalculateMonStats), cured (status 0), mail cleared, and every
    move slot at its full PP. Vanilla restores PP on deposit (BoxMonRestorePP), where an empty
    slot has none; CFRU's expansion calls CalculatePPWithBonus for ALL four slots, move 0
    included (lua/gen3/boxes.lua RR withdraw; RR ROM 0x090B69C6..0x090B69E2), so on RR an empty
    slot carries the table's move-0 PP -- the same split gen3_record_problems makes."""
    problems = []
    if mon["hp"] != mon["max_hp"]:
        problems.append(f"{label}: withdrawn at {mon['hp']}/{mon['max_hp']} HP, not healed")
    if mon["status"] != 0:
        problems.append(f"{label}: withdrawn with status 0x{mon['status']:08X}, not cured")
    if mon["mail"] != MAIL_NONE and mon.get("held_item") not in GEN3_MAIL_ITEMS:
        problems.append(f"{label}: withdrawn with mail 0x{mon['mail']:02X}")
    max_pp = limits and limits.get("max_pp")
    if max_pp:
        full = [max_pp(m, mon["pp_bonuses"], i) if (m or rr) else 0
                for i, m in enumerate(mon["moves"])]
        if list(mon["pp"]) != full:
            problems.append(f"{label}: withdrawn with PP {list(mon['pp'])}, not restored {full}")
    return problems


# A mon trained up the T2 route (linked_faint_active_trainer_gen3): the growth fields move.
GEN3_TRAINED_MUTABLE = frozenset({"level", "experience", "evs", "max_hp", "attack", "defense",
                                  "speed", "sp_attack", "sp_defense", "moves"})


def active_faint_chain(key, case):
    """(required, ordered, forbidden) receipt markers of the P+H subject (scenario_gen3_linked_
    faint_active.lua), in chain order: READY_ACTIVE, the keyed RX, the client's own hand-off
    commit line, ACTIVE_COMMIT (5 writes), HANDOFF (successor within 2 frames, exec bit 0 clear),
    the driver's FORCED_HP0 IN battle, ACTIVE_KO with measured inputs=0 keys=0x0 hp_writes=0 (R3
    lhammer presses L on purpose: any count), ACTIVE_FAINT_SITE (counter +1), then the case's aftermath: SENT_OUT (wild,
    trainer, command, lhammer) or TX whiteout (whiteout), ACTIVE_OUTCOME and a save. Forbidden:
    the old hold's markers and a `faint` echo for the key."""
    k = re.escape(key)
    # the carrier prints its MEASURED press count and OR-ed heldKeysRaw (R1 L3); R3 presses L
    # on purpose, so there only the HP-write count is pinned
    inputs = r"\d+ keys=0x[0-9A-F]+" if case == "lhammer" else "0 keys=0x0"
    explode = case == "explode"
    tag = rf" case={re.escape(case)}$"      # the carrier closes both lines with its case
    if explode:   # owner ruling 19: the Explosion menu skip ends in the same hand-off
        commit = [gen3_rx("force_explode", key),
                  r"(?m)^\[client\] \[SLink-gen3\] force_explode: menu skip committed slot=0 battler=0 handoff=1$",
                  rf"(?m)^ACTIVE_COMMIT {k} frame=\d+ writes=\d+ attempted=\d+ handoff=1 .*{tag}"]
    else:
        commit = [gen3_rx("force_faint", key),
                  rf"(?m)^\[client\] \[SLink-gen3\] force_faint: Perish commit battler=0 handoff=1 {k}\b",
                  rf"(?m)^ACTIVE_COMMIT {k} frame=\d+ writes=5 attempted=\d+ handoff=1 .*{tag}"]
    chain = [rf"(?m)^READY_ACTIVE {k} case={re.escape(case)}$", *commit,
             rf"(?m)^HANDOFF {k} from=0x[0-9A-F]{{8}} to=0x[0-9A-F]{{8}} frames=[12] exec_bit0=0$",
             rf"(?m)^ACTIVE_KO {k} frame=\d+ in_battle=1 battle_hp=0 .*"
             rf"{' last_move=153' if explode else ''} inputs={inputs} hp_writes=0 attempted=\d+{tag}",
             rf"(?m)^ACTIVE_FAINT_SITE {k} frame=\d+ active=0 battler0_slot=0 battle_hp=0 party_hp=0 "
             rf"counter=(\d+)->(?!\1\b)\d+$"]
    if case == "lhammer":
        chain.append(rf"(?m)^LHAMMER_BALLS {k} balls=\d+ ball_id=\d+ unchanged$")
    if case == "whiteout":
        chain += [gen3_tx("whiteout", "-"), rf"(?m)^ACTIVE_OUTCOME {k} outcome=2 "]
    elif explode:   # the foe usually falls too: a win, a send-out, a whiteout or a draw follows
        chain.append(rf"(?m)^ACTIVE_OUTCOME {k} outcome=\d+ ")
    else:
        outcome = "4" if case == "command" else r"\d+"
        chain += [r"(?m)^SENT_OUT slot=1 battler_slot=1$", rf"(?m)^ACTIVE_OUTCOME {k} outcome={outcome} sent_out=1 "]
    chain.append(r"(?m)^SAVE_WITNESS_DUMP ")
    # the driver's own HP-0 watcher (party HP, in battle, battler 0): after the commit and before
    # the aftermath, but not ordered against ACTIVE_KO/the site -- the party word follows
    # gBattleMons by the controller's SetMonData, and all three can land in one frame
    hp0 = rf"(?m)^FORCED_HP0 {k} frame=\d+ in_battle=1 battler=1$"
    ordered = list(zip(chain, chain[1:], strict=False)) + [(chain[3], hp0), (hp0, chain[8 if case == "lhammer" else 7])]
    forbidden = [rf"(?m)^ACTIVE_HOLD {k}\b", rf"(?m)^SWITCHED_OUT {k}\b", rf"(?m)^BENCH_HP0_IN_BATTLE {k}\b",
                 gen3_tx("faint", key), rf"(?m)^FORCED_HP0 {k} .*in_battle=0",
                 r"(?m)^\[client\] .*active faint committed \(press A\)"]
    required = chain + [hp0]
    if explode:
        pp = rf"(?m)^EXPLOSION_PP {k} committed=5/5/5/5 ko=(?!5/5/5/5$)[0-5]/[0-5]/[0-5]/[0-5]$"
        required.append(pp)
        ordered += [(chain[4], pp), (pp, chain[5])]
    return required, ordered, forbidden


def gen3_last_mon_problems(label, saved, fixture, key, deposited, memorial_box, rr=False, limits=None,
                           battled=False):
    """linked_faint_active_whiteout_gen3's B: after the Perish KO whited it out, `key` is the
    saved party's ONLY mon (the Center heal restored it; its memorialize was dropped after
    game_over), boxed nowhere, valid, with the fixture record's invariants; each hand-`deposited`
    key sits once in a non-memorial box, and no other box changed."""
    party, boxes = saved
    f_party, f_boxes = fixture
    keys = [gen3_key(m) for m in party]
    problems = [] if keys == [key] else [f"{label}: saved party {keys}, expected [{key}] alone"]
    if not deposited and len(f_party) != 1:
        problems.append(f"{label}: no hand deposit built the one-mon party")
    if any(gen3_key(m) == key for m in boxes.values()):
        problems.append(f"{label}: {key} also has a boxed copy")
    now = next((m for m in party if gen3_key(m) == key), None)
    was = next((m for m in f_party if gen3_key(m) == key), None)
    if now and was:
        problems += gen3_record_problems(f"{label}: the kept {key}", now, rr, limits)
        # the walk to the PC and back crosses Route 1 grass, where playlib FIGHTS incidental
        # battles: the lone lead may level on the way
        changed = gen3_consumed_ok(gen3_record_diff(was, now, rr, GEN3_RECORD_MUTABLE | GEN3_ACTIVITY_MUTABLE
                                                    | GEN3_TRAINED_MUTABLE), battled)
        if changed:
            problems.append(f"{label}: the kept {key} differs from the fixture in {changed}")
    moved = set(deposited)
    others = {pos: gen3_key(m) for pos, m in boxes.items() if gen3_key(m) not in moved}
    if others != {pos: gen3_key(m) for pos, m in f_boxes.items()}:
        problems.append(f"{label}: boxes other than the hand deposit changed")
    for mon in sorted(moved):
        at = [pos for pos, m in boxes.items() if gen3_key(m) == mon]
        if len(at) != 1 or at[0][0] == memorial_box or not any(gen3_key(m) == mon for m in f_party):
            problems.append(f"{label}: the hand-deposited {mon} is saved at {at}, not once in a "
                            f"non-memorial box")
    return problems


def gen3_memorial_problems(label, saved, fixture, key, memorial_box, rr=False, limits=None,
                           battled=False, trained=False, captured=None,
                           expansion_memorial_source=None, expansion_require_zero=False):
    """`key` left the party for exactly one slot of the memorial box; nothing else moved. The
    memorial record must be valid for the cartridge and, when the fixture carried the mon, keep
    every invariant field of the record it was cut from (friendship only if it `battled`, the
    growth fields only if it was `trained`)."""
    party, boxes = saved
    f_party, f_boxes = fixture
    keys = [gen3_key(m) for m in party]
    problems = []
    if key in keys:
        problems.append(f"{label}: {key} is still in the saved party {keys}")
    where = sorted(pos for pos, m in boxes.items() if gen3_key(m) == key)
    if len(where) != 1 or where[0][0] != memorial_box:
        problems.append(f"{label}: {key} is saved at {where}, not exactly once in the memorial "
                        f"box {memorial_box}")
    else:
        problems += gen3_record_problems(f"{label}: the memorial record for {key}",
                                         boxes[where[0]], rr, limits)
        was = next((m for m in f_party if gen3_key(m) == key), None)
        mutable = (GEN3_RECORD_MUTABLE | (GEN3_ACTIVITY_MUTABLE if battled else set())
                   | (GEN3_TRAINED_MUTABLE if trained else set()))
        now = boxes[where[0]]
        if was and expansion_memorial_source:
            # Expansion BoxPokemon embeds hpLost in `unknown`'s low 14 bits. The engine's
            # MON_DATA_HP write on the proved B Perish KO makes it maxHP-0; keep both high
            # bits (including shinyModifier) invariant. This is narrower than ignoring unknown.
            hp_problems = exp_faint_oracle.memorial_hp_lost_problems(
                was, now, source_hp=expansion_memorial_source[0],
                source_max_hp=expansion_memorial_source[1], require_zero=expansion_require_zero)
            problems += [f"{label}: {problem}" for problem in hp_problems]
            if not hp_problems:
                mutable = mutable | {"unknown"}
        changed = gen3_consumed_ok(gen3_record_diff(was, now, rr, mutable), battled) if was else []
        if was is None:
            # a key the fixture never carried (a catch): compare against its own capture event,
            # never skip (G5-RR-ORACLES-3 F3) -- species and held item as the client reported them
            if captured is None:
                problems.append(f"{label}: {key} is not in the fixture and no capture event was "
                                f"given to compare its memorial record against")
            else:
                for field, sent in (("species", "species_id"), ("held_item", "held_item_id")):
                    if now.get(field) != captured.get(sent):
                        changed.append((field, captured.get(sent), now.get(field)))
        if changed:
            problems.append(f"{label}: the memorial {key} differs from the fixture record in "
                            f"{changed}")
    expected = [gen3_key(m) for m in f_party if gen3_key(m) != key]
    if keys != expected:
        problems.append(f"{label}: saved party {keys}, expected the fixture's minus {key}: "
                        f"{expected}")
    others = {pos: gen3_key(m) for pos, m in boxes.items() if gen3_key(m) != key}
    if others != {pos: gen3_key(m) for pos, m in f_boxes.items()}:
        problems.append(f"{label}: boxes other than the memorial slot changed")
    return problems


def gen3_round_trip_problems(label, saved, fixture, key, rr=False, limits=None, walked=False):
    """`key` is back in the party exactly once, boxed nowhere, valid for the cartridge, in the
    withdrawal's postcondition (healed, cured, PP restored), and every invariant field equals
    the fixture's record -- friendship may move only when the scenario `walked` it."""
    party, boxes = saved
    f_party, f_boxes = fixture
    keys = [gen3_key(m) for m in party]
    problems = []
    if keys.count(key) != 1:
        problems.append(f"{label}: {key} appears {keys.count(key)}x in the saved party {keys}")
    if any(gen3_key(m) == key for m in boxes.values()):
        problems.append(f"{label}: {key} is still (or also) in a saved box")
    if sorted(keys) != sorted(gen3_key(m) for m in f_party):
        problems.append(f"{label}: saved party {keys} is not the fixture's membership")
    if {pos: gen3_key(m) for pos, m in boxes.items()} != {
            pos: gen3_key(m) for pos, m in f_boxes.items()}:
        problems.append(f"{label}: the saved boxes are not the fixture's")
    now = next((m for m in party if gen3_key(m) == key), None)
    was = next((m for m in f_party if gen3_key(m) == key), None)
    if now and was:
        problems += gen3_record_problems(f"{label}: the returned {key}", now, rr, limits)
        problems += gen3_withdrawn_problems(f"{label}: the returned {key}", now, limits, rr)
        mutable = GEN3_RECORD_MUTABLE | (GEN3_ACTIVITY_MUTABLE if walked else set())
        changed = gen3_record_diff(was, now, rr, mutable)
        if changed:
            problems.append(f"{label}: the returned {key} differs from the fixture in {changed}")
    return problems


def gen3_capture_problems(label, saved, fixture, key, sent=None, rr=False, limits=None):
    """A caught `key` joined the party once (appended), is boxed nowhere, nothing else moved, the
    record is valid for the cartridge, and it carries what the client's capture event said
    (species, level, held item, nickname) -- `sent` is that event's decoded fields."""
    party, boxes = saved
    f_party, f_boxes = fixture
    keys = [gen3_key(m) for m in party]
    expected = [gen3_key(m) for m in f_party] + [key]
    problems = []
    if keys != expected:
        problems.append(f"{label}: saved party {keys}, expected {expected}")
    if any(gen3_key(m) == key for m in boxes.values()):
        problems.append(f"{label}: the capture {key} is also saved in a box (duplicate)")
    if {pos: gen3_key(m) for pos, m in boxes.items()} != {
            pos: gen3_key(m) for pos, m in f_boxes.items()}:
        problems.append(f"{label}: the saved boxes are not the fixture's")
    mon = next((m for m in party if gen3_key(m) == key), None)
    if mon:
        problems += gen3_record_problems(f"{label}: the capture {key}", mon, rr, limits)
        for wire, field in (("species_id", "species"), ("level", "level"),
                            ("held_item_id", "held_item"), ("nickname", "nickname")):
            if sent and sent.get(wire) is not None and mon[field] != sent[wire]:
                problems.append(f"{label}: {key} saved {field}={mon[field]!r}, the capture event "
                                f"said {sent[wire]!r}")
    return problems


# Paths a run itself rewrites (the wire goldens `--wire-log` refreshes): never "dirty source".
GEN3_IDENTITY_EXCLUDE = ("tests/fixtures/gen3/wire/",)

def gen3_dirty_paths(porcelain_z):
    """The dirty paths of `git status --porcelain=v1 -z` output, NUL-separated and unquoted. A
    rename/copy record ("R" or "C" in XY) is `XY new\0old\0`: BOTH paths are reported, and the
    record is excluded only when BOTH lie under GEN3_IDENTITY_EXCLUDE (Codex, 86245d1e: a
    wire-fixture -> server/ rename used to read as one excluded path)."""
    out, fields, i = [], porcelain_z.split("\0"), 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if len(record) < 4:
            continue
        status, paths = record[:2], [record[3:]]
        if "R" in status or "C" in status:
            paths.append(fields[i])
            i += 1
        if not all(path.startswith(GEN3_IDENTITY_EXCLUDE) for path in paths):
            out.extend(paths)
    return out


# While a Gen 3 oracle runs, DuoRun._run_oracle points this at a list: gen3_receipt_problems
# appends (label, line) for every receipt line a required/ordered pattern matched, and the runner
# copies them into the PYDEC receipt (Codex receipt audit 2026-09-23: the receipt must carry
# the raw markers the verdict consumed, not only its summary line).
_CONSUMED_MARKERS = None


def _receipt_line(text, match):
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    return text[start:end if end >= 0 else len(text)]


# rows 1/9 (C4-SAVE-ROWS): the START menu refusals name the menu's own clauses, never a
# save-dialog pointer (sSaveDialogCB is a witness, not a clause, since C4-SAVE)
_MENU_CLAUSE = r"clause=(?:task|field_controls_locked) "


def save_then_write_chain(ka):
    """save_then_write_gen3's A receipt, in order (scenario_gen3_save_then_write.lua emits it):
    the C4-6s regression, each save's SAVE_DISMISSAL (A press vs the 60-call timeout, read from
    sSaveDialogDelay), then the menu leg -- row 9's witness false off SAVE with a refused probe,
    row 1's overwrite prompt and redrawn menu each refusing the same probe, and its landing after
    the release, with its write-frame witness (field free, no START task, read inside the write).
    SAVE_CANCEL_FIELD_FREE is NOT in this line: see save_then_write_order."""
    k = re.escape(ka)
    dismissal = r"by=(?:a_press delay=(?:[1-9]|[1-5]\d)|timeout delay=0)$"
    return [r"(?m)^SAVE_WITNESS_DUMP .*saves=1 ", rf"(?m)^SAVE_DISMISSAL save_then_write_1 {dismissal}",
            r"(?m)^STALE_SAVE_DIALOG save_dialog_cb=0x[0-9A-F]{8}:SaveDialogCB_ReturnSuccess",
            rf"(?m)^WRITE_PROBE_READY {k} ", gen3_rx("box_mon", ka), gen3_tx("stats_cache", ka),
            gen3_boxed(ka), rf"(?m)^WRITE_LANDED box_mon {k} ", r"(?m)^SAVE_WITNESS_DUMP .*saves=2 ",
            rf"(?m)^SAVE_DISMISSAL save_then_write_2 {dismissal}",
            r"(?m)^DIALOG_WITNESS_FALSE cursor=\d+ action=\d+ save_row=\d+ menu=open stale=0x(?!00000000)[0-9A-F]{8}$",
            rf"(?m)^CONTROL_LIVE dialog_witness {k} ", gen3_rx("party_mon", ka),
            rf"(?m)^CONTROL_REFUSED dialog_witness party_mon {k} {_MENU_CLAUSE}",
            rf"(?m)^SAVE_CANCEL_PROMPT {k} row=save prompt=(?:overwrite|different_file)$",
            rf"(?m)^CONTROL_LIVE save_prompt {k} ",
            rf"(?m)^CONTROL_REFUSED save_prompt party_mon {k} {_MENU_CLAUSE}",
            rf"(?m)^SAVE_CANCEL_MENU_REDRAWN {k} ", rf"(?m)^CONTROL_LIVE save_cancel_menu {k} ",
            rf"(?m)^CONTROL_REFUSED save_cancel_menu party_mon {k} {_MENU_CLAUSE}",
            rf"(?m)^CONTROL_RELEASED save_cancel party_mon {k}$",
            gen3_tx("sync_retrieve_done", ka), gen3_returned(ka),
            rf"(?m)^SAVE_CANCEL_WRITE_FRAME {k} frame=\d+ field_free=true start_menu_task=false$",
            rf"(?m)^CONTROL_SETTLED save_cancel party_mon {k}$",
            r"(?m)^SAVE_COUNTER_UNCHANGED before=(\d+) after=\1$"]


def save_then_write_order(ka):
    """(required, ordered) for the A receipt: the chain in order, plus SAVE_CANCEL_FIELD_FREE
    between the release and CONTROL_SETTLED only. Live 03ab26e7 (FR and LG): the client admits
    and writes in the FIRST free frame, inside that frame's onframeend pump, so its write and TX
    log before the scenario's per-frame check can log FIELD_FREE -- product-correct, and not
    evidence of anything. What proves the landing waited for the field is the write-frame
    witness in the chain, read in the write itself; the held refusals before the release prove
    nothing landed while the field was locked."""
    chain = save_then_write_chain(ka)
    released = rf"(?m)^CONTROL_RELEASED save_cancel party_mon {re.escape(ka)}$"
    free = rf"(?m)^SAVE_CANCEL_FIELD_FREE {re.escape(ka)}$"
    settled = rf"(?m)^CONTROL_SETTLED save_cancel party_mon {re.escape(ka)}$"
    return [*chain, free], [*zip(chain, chain[1:], strict=False), (released, free), (free, settled)]


def save_then_write_forbidden(ka):
    """No failed write, no third save (the cancelled dialog wrote nothing), and no save-dialog
    pointer named in row 9's refusal (the old pack's clause, G4 draft row 9)."""
    return [gen3_tx("box_mon_failed", ka), gen3_tx("sync_retrieve_failed", ka),
            r"(?m)^SAVE_WITNESS_DUMP .*saves=3 ", r"(?m)^CONTROL_REFUSED dialog_witness .*save_dialog_cb"]


# ctx.hold_probe's 600 held frames plus its frame-0 sample: every held frame calls live() once
CABLE_HOLD_SAMPLES = 601
_CABLE_NULL_RE = re.compile(
    r"(?m)^CABLE_CALLBACK_NULL (?:(?P<witnessed>sLinkOpen=1 gLinkCallback=0)|limit=no-cable-partner) "
    r"held_frames=(?P<held>\d+) open_frames=(?P<open>\d+) null_frames=(?P<null>\d+)"
    r"(?: callback=0x(?P<cb>[0-9A-F]{8}):(?P<name>\w+))?$")


def gen3_sym(title, name):
    """`name`'s address in pret's .sym for `title` (radical_red reads FR's, as the driver does)."""
    sym = "firered" if title == "radical_red" else title
    with open(os.path.join(REPO, "data", "gen3", "pret", f"poke{sym}.sym"), encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) == 4 and parts[3] == name:
                return int(parts[0], 16)
    raise RuntimeError(f"no {name} in poke{sym}.sym")


def cable_callback_problems(label, text, link_cb):
    """Row 6 (G4 draft §3.2): the CABLE_CALLBACK_NULL line's fields. Witnessed: 1 <= null <= open
    <= held. The no-cable-partner limit: the link open on EVERY held frame (held >= the hold's
    CABLE_HOLD_SAMPLES), never null, and the callback LinkCB_RequestPlayerDataExchange at its
    .sym address (`link_cb`, Thumb bit set)."""
    m = _CABLE_NULL_RE.search(text or "")
    if not m:
        return [f"{label}: CABLE_CALLBACK_NULL missing or malformed"]
    held, opened, null = int(m["held"]), int(m["open"]), int(m["null"])
    problems = []
    if held < CABLE_HOLD_SAMPLES:
        problems.append(f"{label}: CABLE_CALLBACK_NULL held_frames={held} < {CABLE_HOLD_SAMPLES}")
    if m["witnessed"]:
        if m["cb"] is not None or not 1 <= null <= opened <= held:
            problems.append(f"{label}: CABLE_CALLBACK_NULL witnessed form needs 1 <= null <= open <= held "
                            f"and no callback ({m.group(0)})")
    elif null != 0 or opened != held or m["cb"] is None or m["name"] != "LinkCB_RequestPlayerDataExchange" \
            or int(m["cb"], 16) != (link_cb | 1):
        problems.append(f"{label}: CABLE_CALLBACK_NULL limit needs open_frames == held_frames, null_frames=0 "
                        f"and callback=0x{link_cb | 1:08X}:LinkCB_RequestPlayerDataExchange ({m.group(0)})")
    return problems


def center_controls_chain(ka):
    """center_controls_gen3's A receipt, in order (scenario_gen3_center_controls.lua emits it):
    each control's source-pinned WITNESS before its CONTROL_LIVE, the keyed probe after it, the
    refusal, then CONTROL_RELEASED at the release input BEFORE the probe's ACK and read-back,
    and CONTROL_SETTLED after them (Codex REV-center-receipt-2). C4-SAVE-ROWS: cable_save (row 3)
    refuses the same probe before the save's DUMP, and CABLE_CALLBACK_NULL (row 6) follows the
    link wait's refusal."""
    k = re.escape(ka)
    return [r"(?m)^WITNESS cable_welcome_message script=CableClub_EventScript_WelcomeToCableClub at=\S+ "
            r"var_result=0 adapter_connected=false",
            rf"(?m)^CONTROL_LIVE cable_welcome_message {k} ", gen3_rx("box_mon", ka),
            rf"(?m)^CONTROL_REFUSED cable_welcome_message box_mon {k} clause=\S+ ",
            # row 3: the Cable Club save's own refusal, BEFORE the save lands
            rf"(?m)^CONTROL_LIVE cable_save {k} ", rf"(?m)^CONTROL_REFUSED cable_save box_mon {k} clause=\S+ ",
            r"(?m)^SAVE_WITNESS_DUMP ", rf"(?m)^CONTROL_LIVE cable_link {k} ",
            rf"(?m)^CONTROL_REFUSED cable_link box_mon {k} clause=\S+ ",
            # row 6: located here, judged field by field by cable_callback_problems
            r"(?m)^CABLE_CALLBACK_NULL ",
            rf"(?m)^CONTROL_RELEASED cable_link box_mon {k}$", gen3_tx("stats_cache", ka),
            gen3_boxed(ka), rf"(?m)^CONTROL_SETTLED cable_link box_mon {k}$",
            r"(?m)^WITNESS union_room_attendant script=CableClub_EventScript_UnionRoomAdapterNotConnected "
            r"at=\S+ var_result=0 adapter_connected=false",
            rf"(?m)^CONTROL_LIVE union_room_attendant {k} ", gen3_rx("party_mon", ka),
            rf"(?m)^CONTROL_REFUSED union_room_attendant party_mon {k} clause=\S+ ",
            rf"(?m)^CONTROL_RELEASED union_room_attendant party_mon {k}$",
            gen3_tx("sync_retrieve_done", ka), gen3_returned(ka),
            rf"(?m)^CONTROL_SETTLED union_room_attendant party_mon {k}$"]


def gen3_receipt_problems(label, text, required=(), forbidden=(), ordered=()):
    """Receipt markers: every `required` regex present, no `forbidden` one, each `ordered`
    (before, after) pair found in that order."""
    if _CONSUMED_MARKERS is not None:
        for pattern in [*required, *(p for pair in ordered for p in pair)]:
            match = re.search(pattern, text or "")
            if match:
                _CONSUMED_MARKERS.append((label, _receipt_line(text, match)))
    problems = [f"{label}: missing /{p}/" for p in required if not re.search(p, text or "")]
    problems += [f"{label}: forbidden /{p}/ present" for p in forbidden
                 if re.search(p, text or "")]
    for first, second in ordered:
        a, b = re.search(first, text or ""), re.search(second, text or "")
        if not a or not b or b.start() <= a.start():
            problems.append(f"{label}: /{first}/ must precede /{second}/")
    return problems


def rival_swap_real_problems(text, source_party, rival_ids, native_lo, native_hi):
    """rival_swap_real_gen3's receipt oracle (card G5-RR-RIVAL) over A's receipt
    (lua/tests/duo/scenario_gen3_rival_swap_real.lua). `source_party` is B's decoded party (the
    swap source), `rival_ids` the RR adapter's rival set, [native_lo, native_hi) the companion
    mailbox arena. Returns (problems, fact)."""
    codec = gen3_codec()
    text = text or ""
    problems = []

    def one(pattern, label):
        found = re.findall(pattern, text, re.M)
        if len(found) != 1:
            problems.append(f"a: {len(found)} {label} line(s), expected exactly one")
            return None
        return found[0]

    def mon(hexed):
        try:
            return codec.decode_party_mon(bytes.fromhex(hexed), rr=True)
        except ValueError:
            return None

    def ident(m):
        return (m["species"], m["level"], gen3_key(m)) if m else None

    if not re.search(r"(?m)^RIVAL_PRE map=3\.41 at=\(34,6\) var4054=1$", text):
        problems.append("a: RIVAL_PRE is not Route 22 (34,6) with VAR_MAP_SCENE_ROUTE22 == 1")
    tbs = one(r"^TX trainer_battle_start - (\{.*\})$", "TX trainer_battle_start")
    tbs = json.loads(tbs) if tbs else {}
    trainer = tbs.get("trainer_id")
    if trainer not in rival_ids:
        problems.append(f"a: trainer_battle_start named {trainer!r}, not an RR rival id")
    cmd = one(r"^RIVAL_CMD trainer_id=(\S+) n=(\S+) session=(\S+) battle_id=(\S+) source=(\S+)",
              "RIVAL_CMD")
    if cmd:
        if cmd[0] != str(trainer) or cmd[4] != "auto":
            problems.append(f"a: the command is trainer {cmd[0]} source {cmd[4]}, expected the "
                            f"server's auto swap for trainer {trainer}")
        if (cmd[2], cmd[3]) != (str(tbs.get("session")), str(tbs.get("battle_id"))):
            problems.append("a: the command's session/battle_id is not the announcement's")
    # the server hex-encodes lower case (state.py blobs_hex), the driver dumps upper case
    blobs = dict(re.findall(r"(?m)^RIVAL_BLOB (\d+) ([0-9A-Fa-f]{200})$", text))
    staged = [ident(mon(blobs[str(i)])) for i in range(len(blobs)) if str(i) in blobs]
    if not staged or staged != [ident(m) for m in source_party]:
        problems.append(f"a: the staged blobs {staged} are not B's party "
                        f"{[ident(m) for m in source_party]}")
    if cmd and cmd[1] != str(len(blobs)):
        problems.append(f"a: RIVAL_CMD n={cmd[1]} but {len(blobs)} RIVAL_BLOB line(s)")
    if not re.search(r"(?m)^RX replace_rival_team\b", text):
        problems.append("a: missing RX replace_rival_team")
    reply = one(r"^TX rival_team_replaced - (\{.*\})$", "TX rival_team_replaced")
    reply = json.loads(reply) if reply else {}
    if reply.get("error"):
        problems.append(f"a: rival_team_replaced error={reply.get('error')} "
                        f"reason={reply.get('reason')}")
    if reply.get("trainer_id") != trainer or reply.get("species_ids") != [s[0] for s in staged if s]:
        problems.append(f"a: the ack {reply} is not trainer {trainer} with the staged species")
    count = one(r"^ENEMY_COUNT (\d+)$", "ENEMY_COUNT")
    if count != str(len(staged)):
        problems.append(f"a: ENEMY_COUNT {count}, the staged team has {len(staged)}")
    enemy = dict(re.findall(r"(?m)^ENEMY_SLOT (\d+) ([0-9A-Fa-f]{200})$", text))
    for i, want in enumerate(staged):
        got = ident(mon(enemy.get(str(i), "")))
        if got != want:
            problems.append(f"a: ENEMY_SLOT {i} reads {got}, staged {want}")
    lead = mon(enemy.get("0", ""))
    mon1 = re.search(r"(?m)^BATTLE_MON1 species=(\d+) pid=([0-9A-F]{8})$", text)
    if not (lead and mon1 and int(mon1.group(1)) == lead["species"]
            and int(mon1.group(2), 16) == lead["personality"]):
        problems.append(f"a: BATTLE_MON1 {mon1.group(0) if mon1 else None} is not the swapped lead")
    if one(r"^TRAINER_OPPONENT_A (\d+)$", "TRAINER_OPPONENT_A") != str(trainer):
        problems.append(f"a: gTrainerBattleOpponent_A is not {trainer}")
    writes = re.findall(r"(?m)^\[client\] \[SLink-gen3\] write (\S+) 0x([0-9A-Fa-f]+) \+(\d+)", text)
    for reason, addr, size in writes:
        lo = int(addr, 16)
        if reason != "native" or lo < native_lo or lo + int(size) > native_hi:
            problems.append(f"a: client write {reason} 0x{lo:08X} +{size} outside the native mailbox")
    fact = (f"rival_swap_real: rival {trainer} fought B's {len(staged)} mon(s) {staged}; "
            f"{len(writes)} client write(s), all native mailbox")
    return problems, fact


# The companion's reserved EWRAM arena (patch/src/trade_targets/abi.h SLINK_ARENA_SIZE; its base is the
# title pack profile's native.BASE, firered.h SLINK_TARGET_ARENA_BASE). Every write the client's native
# driver makes (lua/gen3/native.lua arms reason "native" with an allow predicate inside the mailbox/control
# blocks): the config (CONTROL epoch/enable, TN_ENABLE) and link_panel (INFO) classes among them.
GEN3_NATIVE_ARENA_SIZE = 0x1000
# apply_prepare/apply_trade (native trade) and show_menu/show_choices/choose_mon (native field script / party
# chooser, lua/gen3/native.lua show_menu..choose_mon) are listed too: their writes are reason "native" inside the
# arena, but they start game script state, so the arena alone does not make them harmless on an idle peer.
# config and link_panel are the intended idle exceptions.
GEN3_STATE_COMMAND_RE = (r"(?m)^RX (?:force_\w+|box_mon|party_mon|memorialize|replace_rival_team"
                         r"|apply_prepare|apply_trade|show_menu|show_choices|choose_mon)\b")


def gen3_idle_peer_write_problems(label, text, native_lo, native_hi):
    """An idle peer made no Soul Link GAME-STATE write. The companion legitimately writes its own mailbox
    (config after hello, link_panel after RX link_panel) on every run, so a raw `WRITES 0` is the wrong
    contract there and its count depends on timing. The contract is by scope: the driver's `WRITES n` line
    must exist and equal the write lines it logged, every logged write must be reason `native` inside
    [native_lo, native_hi), and no state-changing command (force_*/box_mon/party_mon/memorialize) arrived."""
    text = text or ""
    problems = []
    total = re.search(r"(?m)^WRITES (\d+)$", text)
    writes = re.findall(r"(?m)^\[client\] \[SLink-gen3\] write (\S+) 0x([0-9A-Fa-f]+) \+(\d+)", text)
    if not total:
        problems.append(f"{label}: missing /(?m)^WRITES \\d+$/")
    elif int(total.group(1)) != len(writes):
        problems.append(f"{label}: WRITES {total.group(1)} but {len(writes)} parsed write line(s)")
    for reason, addr, size in writes:
        lo = int(addr, 16)
        if reason != "native" or lo < native_lo or lo + int(size) > native_hi:
            problems.append(f"{label}: idle peer wrote {reason} 0x{lo:08X} +{size} outside the native arena "
                            f"0x{native_lo:08X}..0x{native_hi:08X}")
    if re.search(GEN3_STATE_COMMAND_RE, text):
        problems.append(f"{label}: forbidden /{GEN3_STATE_COMMAND_RE}/ present")
    return problems


def gen3_tx(event, key):
    return GEN3_TX_RE.format(event=re.escape(event), key=re.escape(key))


def gen3_rx(cmd, key):
    return GEN3_RX_RE.format(cmd=re.escape(cmd), key=re.escape(key))


def gen3_boxed(key):
    """The driver's cartridge read-back: `key` absent from the party AND present in a box."""
    return rf"(?m)^BOXED_OBSERVED {re.escape(key)} box=\d+:\d+"


def gen3_returned(key):
    """The driver's cartridge read-back: `key` back in the party AND in no box."""
    return rf"(?m)^RETURNED_OBSERVED {re.escape(key)} slot=\d+"


# What a trade must never change about the mon itself (pret struct BoxPokemon: identity, OT,
# genome, name); level/experience/friendship may move on A's walk, so they are not compared.
GEN3_TRADE_INVARIANT = ("personality", "ot_id", "ot_name", "species", "ivs", "nickname", "moves")


def gen3_trade_problems(ka, kb, link_rows, saved, fixture, traded, preimages=None):
    """trade_gen3 / trade_decline_gen3's saved state, from the flash images alone (PYDEC) and the
    server's persisted links.json: `saved`/`fixture` map "a"/"b" -> (party, boxes) as gen3_decode
    returns them. traded: each side's party is its fixture's with its linked key replaced, in
    place, by the partner's -- the received record's invariant fields are the partner fixture's
    -- and the one link row is re-keyed (a holds kb, b holds ka). Not traded: every party is its
    fixture's and the row is unchanged. Either way each linked key exists exactly once across
    both cartridges (parties and boxes), so nothing was duplicated or lost."""
    problems = []
    want = {ka, kb}
    rows = [e for e in link_rows
            if {(e.get("a") or {}).get("key"), (e.get("b") or {}).get("key")} == want]
    if len(rows) != 1:
        problems.append(f"links.json: expected one row over {sorted(want)}, got {len(rows)}")
    else:
        row = rows[0]
        held = ((row.get("a") or {}).get("key"), (row.get("b") or {}).get("key"))
        expect = (kb, ka) if traded else (ka, kb)
        if held != expect or row.get("status") != "alive":
            problems.append(f"links.json: row a/b = {held} {row.get('status')}, expected "
                            f"{expect} alive")
    counts = {ka: [], kb: []}
    for inst, gives, gets, peer in (("a", ka, kb, "b"), ("b", kb, ka, "a")):
        party, boxes = saved[inst]
        keys = [gen3_key(m) for m in party]
        f_keys = [gen3_key(m) for m in fixture[inst][0]]
        expect = [gets if k == gives else k for k in f_keys] if traded else f_keys
        if keys != expect:
            problems.append(f"{inst}: saved party {keys}, expected {expect}")
        for key in (ka, kb):
            counts[key] += [f"{inst}:party"] * keys.count(key)
            counts[key] += [f"{inst}:box{b}" for b, m in boxes.items() if gen3_key(m) == key]
        if traded and gets in keys:
            got = party[keys.index(gets)]
            source_party = preimages[peer] if preimages is not None else fixture[peer][0]
            src = next((m for m in source_party if gen3_key(m) == gets), None)
            if src is None:
                problems.append(f"{inst}: {gets} is not in {peer}'s fixture party")
            else:
                diff = [f for f in GEN3_TRADE_INVARIANT if got.get(f) != src.get(f)]
                if diff:
                    problems.append(f"{inst}: received {gets} differs from {peer}'s record in {diff}")
        if not traded and preimages is not None and gives in keys:
            own = party[keys.index(gives)]
            source = next((m for m in preimages[inst] if gen3_key(m) == gives), None)
            diff = [f for f in GEN3_TRADE_INVARIANT if source is None or own.get(f) != source.get(f)]
            if diff:
                problems.append(f"{inst}: declined trade changed its captured donor in {diff}")
    for key, where in counts.items():
        if len(where) != 1:
            problems.append(f"{key} exists {len(where)}x across both saves: {where}")
    return problems


def gen3_trade_preimages(results, keys, fixture, rom_sha1):
    """Decode each donor's one raw RR party record captured before native preparation."""
    from server.adapters import gen3_codec as codec

    records, problems, tokens = {}, [], []
    for side in "ab":
        text = results[side]
        rows = re.findall(r"(?m)^TRADE_PREIMAGE (\{.*\})$", text)
        try:
            if len(rows) != 1:
                raise ValueError(f"expected one preimage, got {len(rows)}")
            row = json.loads(rows[0])
            if row.get("player") != side or row.get("key") != keys[side]:
                raise ValueError("player/key differs")
            slot = row.get("slot")
            if type(slot) is not int or not 0 <= slot < len(fixture[side][0]):
                raise ValueError("slot outside fixture party")
            if gen3_key(fixture[side][0][slot]) != keys[side]:
                raise ValueError("slot no longer names offered fixture key")
            if type(row.get("frame")) is not int or row["frame"] < 0:
                raise ValueError("frame missing")
            if type(row.get("counter")) is not int or row["counter"] < 0:
                raise ValueError("native flash counter missing")
            if row.get("rom_sha1") != rom_sha1[side]:
                raise ValueError("ROM hash differs")
            token = row.get("token")
            if not isinstance(token, str) or not token:
                raise ValueError("offer token missing")
            tokens.append(token)
            raw_hex = row.get("raw_hex")
            if not isinstance(raw_hex, str) or len(raw_hex) != 200 or not re.fullmatch(r"[0-9A-F]{200}", raw_hex):
                raise ValueError("raw party record is not 100 bytes of hex")
            mon = codec.decode_party_mon(bytes.fromhex(raw_hex), rr=True)
            # CFRU does not maintain a verifiable BoxPokemon checksum: the field is zero and
            # the codec reports checksum_ok=None. Refuse a changed format rather than bless it.
            if mon["checksum"] != 0 or mon["checksum_ok"] is not None or mon["has_species"] != 1:
                raise ValueError("RR raw record format/checksum differs")
            if gen3_key(mon) != keys[side]:
                raise ValueError("decoded raw key differs")
            marker = "TX mon_chosen " if side == "a" else "TX menu_result "
            first_send = text.find(marker)
            prepare = text.find("RX apply_prepare")
            if (first_send < 0 or text.find("TRADE_PREIMAGE ") >= first_send
                    or (prepare >= 0 and prepare < first_send)):
                raise ValueError("preimage was not before native transaction")
            wire = re.search(r"(?m)^" + marker + r"- (\{.*\})$", text[first_send:])
            if not wire or json.loads(wire.group(1)).get("token") != token:
                raise ValueError("preimage token differs from native menu send")
            records[side] = [mon]
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f"{side}: trade preimage invalid: {exc}")
    if len(tokens) == 2 and tokens[0] != tokens[1]:
        problems.append("A/B trade preimage tokens differ")
    return records, problems


def rr_reset_initial_receipt_problems(receipts, case, keys):
    """Check named partial/native-success exits before the later cold-reload oracle."""
    problems = []
    if case not in ("commit", "success"):
        return ["unknown RR reset case"]
    if set(receipts) != {"a", "b"} or set(keys) != {"a", "b"}:
        return ["RR reset receipts/keys require both players"]

    def marker(inst, label):
        rows = re.findall(rf"^{re.escape(label)} (\{{.*\}})$", receipts[inst], re.M)
        if len(rows) != 1:
            problems.append(f"{inst}: expected exactly one {label}, got {len(rows)}")
            return None
        try:
            row = json.loads(rows[0])
        except ValueError:
            problems.append(f"{inst}: malformed {label} JSON")
            return None
        if not isinstance(row, dict):
            problems.append(f"{inst}: {label} is not an object")
            return None
        return row

    tokens = []
    for inst in ("a", "b"):
        body = receipts[inst]
        interrupted = case == "commit" and inst == "a"
        expected_dumps = 1 if interrupted else 2
        ordinals = [int(row[2]) for row in SAVE_WITNESS_DUMP_RE.findall(body)]
        if ordinals != list(range(1, expected_dumps + 1)):
            problems.append(f"{inst}: native save ordinals {ordinals}, expected 1..{expected_dumps}")
        if re.search(r"^SAVE_WITNESS\s", body, re.M):
            problems.append(f"{inst}: runner manual SAVE occurred")
        start = body.find("RX apply_prepare")
        ready = re.search(r'^TX apply_ready - .*"ok":true', body, re.M)
        apply = body.find("RX apply_trade")
        if start < 0 or not ready or apply < 0 or not start < ready.start() < apply:
            problems.append(f"{inst}: native prepare/ready/apply order missing")
        if ready and body.find("SAVE_WITNESS_DUMP ") > ready.start():
            problems.append(f"{inst}: pre-save witness landed after apply_ready")
        if interrupted:
            observed = marker(inst, "RESET_COMMIT_ENTERED")
            exit_row = marker(inst, "RESET_PARTIAL_COMMIT_EXIT")
            if observed and exit_row and observed != exit_row:
                problems.append(f"{inst}: partial exit did not bind the commit witness")
            if observed:
                if (observed.get("bits") != 3 or observed.get("side") != inst
                        or observed.get("old_key") != keys[inst]
                        or observed.get("counter_after") != observed.get("counter_before", -10) + 1
                        or not all(isinstance(observed.get(k), int) and observed[k] > 0
                                   for k in ("epoch", "visit", "revision", "pre_seq", "scene_seq"))
                        or observed["revision"] % 2
                        or not str(observed.get("witness_path", "")).endswith("_native_witness.bin")):
                    problems.append(f"{inst}: commit window marker is unbound or post-saved")
                tokens.append(observed.get("token"))
            if "TX trade_done " in body or re.search(r"^TRADED\b", body, re.M):
                problems.append(f"{inst}: interrupted side claimed trade completion")
            if "RESULT: FAIL (EXPECTED_RESET_PARTIAL_COMMIT)" not in body:
                problems.append(f"{inst}: no named clean interruption result")
        else:
            saved = marker(inst, "RESET_NATIVE_SUCCESS_NO_MANUAL_SAVE")
            exit_row = marker(inst, "RESET_NATIVE_SUCCESS_EXIT")
            if saved and exit_row and saved != exit_row:
                problems.append(f"{inst}: native success exit changed its witness")
            if saved:
                partner = keys["b" if inst == "a" else "a"]
                if (saved.get("bits") != 31 or saved.get("side") != inst
                        or saved.get("old_key") != keys[inst] or saved.get("new_key") != partner
                        or saved.get("counter_after") != saved.get("counter_before", -10) + 2
                        or not str(saved.get("witness_path", "")).endswith("_native_witness.bin")):
                    problems.append(f"{inst}: native success lacks received/post-save witness")
                tokens.append(saved.get("token"))
            if (re.search(r'^TX trade_done - .*"uncertain":true', body, re.M)
                    or not re.search(r'^TX trade_done - .*"new_key":', body, re.M)):
                problems.append(f"{inst}: no concrete native trade_done")
            partner = keys["b" if inst == "a" else "a"]
            if not re.search(rf"^TRADED gave={re.escape(keys[inst])} got={re.escape(partner)}\b",
                             body, re.M):
                problems.append(f"{inst}: received mon was not read back before exit")
            if "RESULT: FAIL (EXPECTED_NATIVE_SUCCESS_NO_MANUAL_SAVE)" not in body:
                problems.append(f"{inst}: no named native-success clean exit")
    if len(tokens) == 2 and (not tokens[0] or tokens[0] != tokens[1]):
        problems.append("RR reset sides disagree on server trade token")
    return problems


def rr_reset_partial_a_problems(receipt, old_key):
    """Only the named, internally bound A interruption may remain terminal while B runs."""
    if terminal_result(receipt) != "RESULT: FAIL (EXPECTED_RESET_PARTIAL_COMMIT)":
        return ["A did not make the exact expected partial exit"]
    rows = []
    for tag in ("RESET_COMMIT_ENTERED", "RESET_PARTIAL_COMMIT_EXIT"):
        found = re.findall(rf"^{tag} (\{{.*\}})$", receipt, re.M)
        if len(found) != 1:
            return [f"A needs one {tag} witness"]
        try:
            rows.append(json.loads(found[0]))
        except ValueError:
            return [f"A {tag} witness is malformed"]
    first, last = rows
    if not isinstance(first, dict) or first != last or first.get("old_key") != old_key:
        return ["A partial witnesses disagree or name another key"]
    if (first.get("bits") != 3 or not first.get("token")
            or type(first.get("counter_before")) is not int
            or type(first.get("counter_after")) is not int
            or first["counter_after"] != first["counter_before"] + 1):
        return ["A partial witness lacks the native pre-save/commit window"]
    if "TX trade_done " in receipt or re.search(r"^TRADED\b", receipt, re.M):
        return ["A partial exit falsely claimed trade completion"]
    return []


def rr_reset_link_problems(case, keys, staged, initial, final):
    """The staged pair is the independent pre-trade server baseline."""
    old = lambda row: (row.get("a") or {}).get("key") == keys["a"] and (row.get("b") or {}).get("key") == keys["b"]  # noqa: E731
    new = lambda row: (row.get("a") or {}).get("key") == keys["b"] and (row.get("b") or {}).get("key") == keys["a"]  # noqa: E731
    problems = []
    baseline = [row for row in staged if old(row)]
    if (len(baseline) != 1 or not baseline[0].get("area_id")
            or sum(row.get("area_id") == baseline[0]["area_id"] for row in staged) != 1
            or baseline[0].get("status") != "alive"):
        problems.append("RR reset lacks exactly one staged pre-trade server link")
    if case == "commit":
        if len(baseline) == 1:
            original = baseline[0]
            for label, rows in (("interruption", initial), ("reload", final)):
                same_area = [row for row in rows if row.get("area_id") == original.get("area_id")]
                if (len(same_area) != 1 or same_area[0] != original
                        or sum(old(row) for row in rows) != 1 or any(new(row) for row in rows)):
                    problems.append(f"commit {label} changed the staged linked pair")
    elif case == "success":
        if sum(new(row) for row in final) != 1:
            problems.append("native-success reset lacks persisted server re-key")
    else:
        problems.append("unknown RR reset link case")
    return problems


def rr_reset_pending_problems(case, token, document):
    """Require token-bound server recovery or its durable visible-hello successor."""
    pending = (document or {}).get("pending_trade") or {}
    if case == "success":
        return ["native-success reset left a pending server trade"] if pending else []
    phase = pending.get("phase")
    verdict = pending.get("verdict") or {}
    if (pending.get("token") != token
            or (pending.get("hello_only") or {}).get("a") is not True
            or not ((phase == "uncertain" and verdict.get("a") == "await")
                    or (phase == "conflict" and verdict.get("a") == "none"
                        and verdict.get("b") == "traded" and pending.get("problem")))):
        return ["server lacks A's token-bound uncertainty or its durable split-trade successor"]
    return []


def rr_reset_wire_problems(rows, token):
    """A server response paired to A's after-reset report proves its consumption."""
    matches = [row for row in rows if row.get("dir") == "c2s"
               and (row.get("msg") or {}).get("event") == "trade_done"
               and (row.get("msg") or {}).get("token") == token
               and (row.get("msg") or {}).get("uncertain") is True
               and (row.get("msg") or {}).get("after_reset") is True]
    if len(matches) != 1:
        return [f"server wire has {len(matches)} token-bound A after-reset report(s), expected one"]
    request = matches[0]
    if (type(request.get("t")) is not int or request["t"] <= 0
            or type(request.get("conn")) is not int or request["conn"] <= 0):
        return ["server wire after-reset report lacks positive request/connection identity"]
    if not any(row.get("dir") == "s2c" and row.get("req") == request.get("t")
               and row.get("conn") == request.get("conn") for row in rows):
        return ["server wire has no response paired to A's after-reset report"]
    return []


def rr_reset_b_consumed_problems(rows, token, partner_key, document):
    """B's real native report reached the server, even if done.b was not re-saved."""
    matches = [row for row in rows if row.get("dir") == "c2s"
               and (row.get("msg") or {}).get("event") == "trade_done"
               and (row.get("msg") or {}).get("token") == token
               and (row.get("msg") or {}).get("new_key") == partner_key
               and (row.get("msg") or {}).get("uncertain") is not True]
    if len(matches) != 1:
        return ["server wire lacks exactly one B native trade_done for the token/partner"]
    request = matches[0]
    if (type(request.get("t")) is not int or request["t"] <= 0
            or type(request.get("conn")) is not int or request["conn"] <= 0
            or not any(row.get("dir") == "s2c" and row.get("req") == request["t"]
                       and row.get("conn") == request["conn"] for row in rows)):
        return ["server wire lacks B's paired native trade_done response"]
    pending = (document or {}).get("pending_trade") or {}
    received = (pending.get("new") or {}).get("b")
    if (pending.get("token") != token or pending.get("phase") not in ("applying", "uncertain")
            or (pending.get("verdict") or {}).get("b") != "traded"
            or not isinstance(received, (list, tuple)) or not received
            or received[0] != partner_key):
        return ["server has no persisted B native-trade outcome for the token"]
    return []


def gen3_trade_chain(inst, ka, kb, decline):
    """(required, ordered, forbidden) receipt regexes for one side of the NPC trade (RR-DURABLE):
    the server's native menus, then on YES the durable round on BOTH sides -- apply_prepare ->
    apply_ready ok (the native pre-save), apply_trade -> trade_done with the partner's key (the
    native scene + post-save) -> the server's commit notice. The named refusal an old-UPS client
    gets is never a pass, and no side may report an uncertain or refused apply."""
    tx = lambda ev, body="": rf"(?m)^TX {ev} - .*{body}"          # noqa: E731
    rx = lambda cmd: rf"(?m)^RX {cmd}(?=\s|$)"                    # noqa: E731
    # TALKED is a required witness but not an ordering anchor: the patch sends trade_request in the
    # frame A talks, and the scenario logs TALKED only once it sees pi_count move (receipt
    # rr_trade_gen3_gen3_rr_as_a_539e0aea_RED.txt), so the send line comes first.
    talked = [r"(?m)^TALKED "] if inst == "a" else []
    if inst == "a":
        chain = [tx("trade_request"), rx("show_choices"), tx("menu_result", '"choice":0'),
                 rx("choose_mon"), tx("mon_chosen", '"slot":1')]
        if decline:
            chain.append(r"(?m)^RX msgbox text=Your partner declined")
    else:
        chain = [rx("show_menu"), tx("menu_result", f'"choice":{0 if decline else 1}')]
    never = [r"(?m)^REFUSED_UNAVAILABLE ", r"apply_trade refused"]
    if decline:
        return talked + chain, list(zip(chain, chain[1:], strict=False)), never + [
            rx("apply_prepare"), rx("apply_trade"), tx("trade_done"), r"(?m)^TRADED "]
    gets = kb if inst == "a" else ka
    gives = ka if inst == "a" else kb
    done = tx("trade_done", f'"new_key":"{re.escape(gets)}"')
    chain += [rx("apply_prepare"), tx("apply_ready", '"ok":true'), rx("apply_trade"), done,
              rf"(?m)^TRADED gave={re.escape(gives)} got={re.escape(gets)} "]
    # the server's commit notice follows BOTH trade_done reports, so it may land after this
    # side's own TRADED read-back (live receipt, RR-DURABLE trade_gen3): ordered after trade_done only
    notice = r"(?m)^RX msgbox text=Traded "
    return (talked + chain + [notice], list(zip(chain, chain[1:], strict=False)) + [(done, notice)],
            never + [tx("apply_ready", '"ok":false'), tx("trade_done", '"uncertain":true')])


def gen3_panel_text(raw: bytes) -> str:
    """One SlinkInfo line (FR-encoded, 0xFE = newline, 0xFF-terminated) through the Python
    codec's own charmap (gen3_codec._FR_DECODE via decode_name), independent of the Lua encoder."""
    codec = gen3_codec()
    body = raw.split(b"\xff", 1)[0]
    return "\n".join(codec.decode_name(part + b"\xff") for part in body.split(b"\xfe"))


def gen3_panel_problems(text, alive_pairs):
    """infopanel_gen3: the server's rows (PANEL_ROWS, the link_panel it sent) are what the
    cartridge staged and drew: page 1 = rows[0:6] ("|" -> newline), the header slot reads
    PAGE 1/<pages>, the START row opened it (opened +1, drawn caught up, the field locked, VRAM
    changed), and A and B each closed it (the field released). `alive_pairs` comes from
    links.json, so the rows' "Pairs alive" claim is checked against the server's own record."""
    problems = []
    first_open = re.search(r"(?m)^PANEL_OPEN ", text or "")
    sent = re.findall(r"(?m)^PANEL_ROWS (.+)$", (text or "")[:first_open.start() if first_open else None])
    if not sent:
        return ["no PANEL_ROWS before the panel opened (the server never sent link_panel)"]
    rows = json.loads(sent[-1])          # the payload the client held when the player opened it
    per_page = 6
    pages = max(1, -(-len(rows) // per_page))
    if f"Pairs alive|{alive_pairs}/{alive_pairs}" not in rows:
        problems.append(f"rows {rows} do not say Pairs alive|{alive_pairs}/{alive_pairs}")
    opens = re.findall(r"(?m)^PANEL_OPEN (\d+) opened=(\d+)->(\d+) drawn=(\d+) sc2=(\d+) lines=(\d+) "
                       r"page=(\d+) pages=(\d+) vram=(\w+)->(\w+)$", text or "")
    if len(opens) != 2:
        problems.append(f"expected 2 PANEL_OPEN lines, got {len(opens)}")
    for n, o0, o1, drawn, sc2, lines, page, npages, v0, v1 in opens:
        if int(o1) != (int(o0) + 1) % 256 or drawn != o1:
            problems.append(f"open {n}: opened {o0}->{o1} drawn {drawn} (one START-row bump, drawn)")
        if sc2 == "0" or v0 == v1:
            problems.append(f"open {n}: field not locked (sc2={sc2}) or nothing drawn (vram {v0}->{v1})")
        if int(lines) != min(per_page, len(rows)) or page != "0" or int(npages) != pages:
            problems.append(f"open {n}: lines={lines} page={page} pages={npages}, expected "
                            f"{min(per_page, len(rows))}/0/{pages}")
        staged = dict(re.findall(rf"(?m)^PANEL_LINE {n} (\d+) ([0-9A-F]+)$", text or ""))
        for i, row in enumerate(rows[:per_page]):
            got = gen3_panel_text(bytes.fromhex(staged.get(str(i), "")))
            if got != row.replace("|", "\n"):
                problems.append(f"open {n} line {i}: drawn {got!r}, server sent {row!r}")
        slot = re.search(rf"(?m)^PANEL_SLOT7 {n} ([0-9A-F]+)$", text or "")
        if not slot or gen3_panel_text(bytes.fromhex(slot.group(1))) != f"PAGE 1/{pages}":
            problems.append(f"open {n}: the header does not read PAGE 1/{pages}")
    closes = re.findall(r"(?m)^PANEL_CLOSED (\d+) button=(\w) sc2=(\d+)$", text or "")
    if [(b, s) for _, b, s in closes] != [("A", "0"), ("B", "0")]:
        problems.append(f"closes {closes}, expected A then B, each releasing the field")
    return problems


# ── Games ────────────────────────────────────────────────────────────────────
# The duo harness was written for Radical Red and hardcoded to it. Gen 1 differs in three
# ways that matter, so the per-game bits live here rather than being threaded through:
#
#   * NO SAVESTATE. Gen 1 boots from a battery save (tests/fixtures/gen1/*.SaveRAM), which
#     is not BizHawk-version-locked the way a .State is — nothing to rebuild after an
#     emulator upgrade.
#   * DIFFERENT CARTRIDGES per instance on the new Gen 1 client: A is Red, B is Blue. The two
#     cannot collide over BizHawk's SaveRAM because it names saves from its own gamedb entry.
#   * Generation-specific drivers compose each client's production graph.
#
# "legacy" is the family of a DuoRun built without a `game` (unit stubs): no witness, and a client
# RESULT is the whole verdict unless the scenario names an oracle. (Master kept gen3_rr for this; the
# merged gen3_rr is the new battery row, whose saved-state oracle is required.)
FAMILY_EVIDENCE = {
    "legacy": EvidenceContract(),
    "gen2_new": EvidenceContract("check_gen2_save_witness", require_oracle=True),
    "gen1_new": EvidenceContract("check_save_witness", require_oracle=True),
    # The new Gen 3 battery rows (gen3_lgfr is the gen3_frlg family): the Gen 1 rule, a scenario
    # with no saved-state oracle FAILS, with check_save_witness_gen3 run first (PLAN §5.5).
    "gen3_frlg": EvidenceContract("check_save_witness_gen3", require_oracle=True),
    "gen3_rr": EvidenceContract("check_save_witness_gen3", require_oracle=True),
    "gen3_emerald": EvidenceContract("check_save_witness_gen3", require_oracle=True),
    "gen3_fr_trade": EvidenceContract("check_native_trade_witness", require_oracle=True),
    "gen3_exp": EvidenceContract("check_save_witness_gen3", require_oracle=True),
}


def evidence_contract(game):
    family = scenario_family(game)
    if family not in FAMILY_EVIDENCE:
        raise RuntimeError(f"{family}: no family evidence contract")
    return FAMILY_EVIDENCE[family]


GEN2_TRADE_FIXTURES = {
    "gen2_new": {"a": "crystal_battle_errand", "b": "crystal_battle_ot2_errand"},
    "gen2_gold_silver": {"a": "gold_battle_errand", "b": "silver_battle_errand"},
    "gen2_crystal_gold": {"a": "crystal_battle_errand", "b": "gold_battle_errand"},   # O-34
}
# gen2_trade_evolve: A boots an O-33 seed, its errand base with Master Balls (tools/gen2_synth_fixtures.TRADE_RECIPES;
# post-RC card TRADE-EVOLVE-CATCH: the planted HAUNTER outlasted the Poke Balls in 4 of 6 RC link catches).
GEN2_TRADE_EVOLVE_FIXTURES = {game: {**fixtures, "a": fixtures["a"].split("_", 1)[0] + "_synth_trade_evolve"}
                              for game, fixtures in GEN2_TRADE_FIXTURES.items()}
# gen2_faint_active_trainer: B needs an errand seed (on Crystal the pre-errand Route 30 battle demo blocks the aisle,
# C maps/Route30.asm:424-430, ElmsLab.asm:344-345); the trade seeds are the qualified errand pairs.
# B boots an O-33 seed: its errand base with the starter at L10 (tools/gen2_synth_fixtures.TRAINER_RECIPES), so the
# replacement wins the trainer fight after the forced pick (final sweep: the native L5 starter whited out).
GEN2_TRAINER_FIXTURES = {"gen2_new": {"a": "crystal_battle_errand", "b": "crystal_synth_trainer_ot2"},
                         "gen2_gold_silver": {"a": "gold_battle_errand", "b": "silver_synth_trainer"}}


# BizHawk writes a save whose path nears Windows MAX_PATH (260) SILENTLY not at all: a 255-char SaveRAM path left
# the seed file on disk while the run went on (DUO-WAVE-C cc changebox, 2026-09-24; a 249-char one saved). The
# limit keeps a margin for BizHawk's own temp/backup suffixes.
BIZHAWK_PATH_LIMIT = 240


def bizhawk_path_problem(paths, limit=BIZHAWK_PATH_LIMIT):
    """The first path (absolute, as Windows sees it) at or over `limit` characters, else None."""
    for path in paths:
        full = os.path.abspath(str(path))
        if len(full) >= limit:
            return full
    return None


# DUO-CLOCK: every Gen 2 duo boot's RTC is pinned to this in-game hour (_seed_instance_save), matching
# U1_CLOCK's hour (tests/live/test_gen2_frame_align.py) -- inside Route 30's day/morn window
# (data/wild/johto_grass.asm ROUTE_30: 10:00-17:59 is DAY) so a duo's route stays host-clock independent.
DUO_CLOCK_HOUR = 11
# gen2_ball_gate regression (fsw-sweep3/-rr1): a retry (scenario_attempt_limit's "one retry, only when a side
# ran out of the aide's five natural Balls") assumed two attempts differ through real host-clock variance
# feeding the RTC trailer -- true before DUO_CLOCK_HOUR pinned every boot to hour:00:00, which made a retry's
# FixTime catch-up (and so its frame-based RNG) reproduce attempt 1 bit-for-bit (both attempts landed on the
# exact same frame numbers, gen2_ball_gate/gen2_poison logs). Attempt 2+ (of any Gen 2 duo scenario that
# retries: gen2_ball_gate, the clause scenarios) is instead pinned to hour:MINUTE:00, a different but still
# fully deterministic and disclosed target -- never a hunt for a lucky seed, never host-clock-dependent -- so
# a retry is a genuine second roll again. Attempt 1 always gets minute 0: bit-for-bit as before this fix.
DUO_CLOCK_RETRY_MINUTE = 23


def _duo_clock_minute(attempt):
    return ((attempt - 1) * DUO_CLOCK_RETRY_MINUTE) % 60
# Gold has no day POISON_STING foe south of the Route 30 battle demo: its poison A plays the
# post-errand save (U1 ruling, tests/live/test_gen2_frame_align.py U1_FIXTURE); cc/cg keep the pairing.
GEN2_POISON_FIXTURES = {"gen2_gold_silver": {"a": "gold_battle_errand"}}
# gen2_ball_gate boots the zero-Ball town fixtures (Elm's lab after the starter) and plays the errand itself.
GEN2_BALL_GATE_FIXTURES = {"gen2_new": {"a": "crystal_town", "b": "crystal_town_ot2"},
                           "gen2_gold_silver": {"a": "gold_town", "b": "silver_town"}}
# F.driver's refusal when the Ball pocket is empty (lua/tests/gen2_frame_align.lua): five natural Balls at ~33% a
# throw miss about 13% of full-HP catches, so the lane may retry once on exactly this reason.
GEN2_OUT_OF_BALLS = "no Poke Ball left in the pocket"
# Trade and link_prelude scenarios throw balls at their opening wild battle. On the pinned DUO-CLOCK the whole route
# is bit-for-bit deterministic, so a starter that loses that battle loses it on every re-run of the same attempt
# (gen2_new/gen2_trade_refuse_item, sweep gen2-fsw-1003-0029 and -rerun1: ENGINE_WHITEOUT at the same frame twice).
# The one retry runs on the next pinned clock minute (_duo_clock_minute), a disclosed second roll like gen2_ball_gate's.
GEN2_ROUTE_BATTLE_LOST = "link route failed: the battle ended without a catch"
GEN2_ROUTE_LOSS_SCENARIOS = frozenset({
    "gen2_faint", "gen2_whiteout", "gen2_pc_ops", "gen2_changebox", "gen2_poison", "gen2_whiteout_rebuild",
})
# DUO-WAVE-D O-33 setups (tools/gen2_synth_fixtures.py): scenario -> recipe kind. Each side boots
# <title>_synth_<kind> (C<->C B: crystal_synth_<kind>_ot2) through its BASE fixture's qualified CONTINUE (case.synth).
# C-5 refused-at-hello scenarios: each side's expected server admission state (status players[<side>].admission).
GEN2_C5_REFUSED = {"gen2_c5_wrong_rom": {"a": "admitted", "b": "rejected"},
                   "gen2_c5_no_contract": {"a": "rejected", "b": "rejected"}}
GEN2_SYNTH_SCENARIOS = {"gen2_boxed_capture": "full", "gen2_gift": "bill", "gen2_egg_hatch": "hatch",
                        "gen2_npc_trade": "trade", "gen2_evolution": "evolve"}
GEN2_SYNTH_PAIRS = {"gen2_new": {"a": ("crystal", ""), "b": ("crystal", "_ot2")},
                    "gen2_gold_silver": {"a": ("gold", ""), "b": ("silver", "")}}


def gen2_synth_name(scenario, game, inst):
    title, suffix = GEN2_SYNTH_PAIRS[game][inst]
    return f"{title}_synth_{GEN2_SYNTH_SCENARIOS[scenario]}{suffix}"
GEN2_WAVE_C = {"gen2_whiteout": ("whiteout_oracle", "repair"), "gen2_pc_ops": ("pc_ops_oracle", "release"),
               "gen2_changebox": ("changebox_oracle", "box_change"), "gen2_poison": ("poison_oracle", "death"),
               "gen2_whiteout_rebuild": ("whiteout_rebuild_oracle", "rebuild")}

def _gen2_gs_harden_facts(title, *, root=REPO):
    """SYNTH setup only for the two G/S driver gaps; engine/capture/death stay native."""
    from server.adapters import gen2_codec
    from tools.gen2_trade_facts import trade_facts

    if title not in ("gold", "silver"):
        raise ValueError("G/S hardening requires Gold or Silver")
    facts = trade_facts(title, root=Path(root))
    index = json.loads((Path(root) / f"data/games/gen2_{title}/species_index.json").read_text(encoding="utf-8"))
    level = 20
    species = {key: {"base_stats": row["base_stats"],
                     "exp": gen2_codec.exp_for_level(level, row["growth_rate"])}
               for key, row in index["species"].items() if row["classification"] == "ordinary"}
    return {"schema": "gen2-gs-harden-v1", "title": title, "overlay_sha1": facts["overlay_sha1"],
            "level": level, "species": species, "site": facts["code"]["StartBattle"], "ram": facts["ram"]}



GEN2_DEFAULT_SPEED = 300
GEN2_FRAME_CAP = 1_000_000   # lua/scripted_inputs.lua LIMIT: the largest play bound a driver may pass


def gen2_frame_scale(speed):
    """How much faster than the receipted 300% a Gen 2 duo runs (unthrottled counted as 20x)."""
    return 20 if speed == 0 else max(1, -(-speed // 300))


def gen2_selected_artifact(args, scenario):
    """One explicit artifact decision for every Gen 2 scenario, including relaunches."""
    kind = getattr(args, "gen2_artifact", None) or os.getenv("SLINK_GEN2_ARTIFACT")
    kind = kind or "overlay"   # patch-first (owner 2026-10-02): the launcher refuses a clean Gen 2 cartridge
    if kind not in ("clean", "overlay"):
        raise ValueError("Gen 2 artifact must be clean or overlay")
    if scenario in GEN2_TRADE_SCENARIOS and kind != "overlay":
        raise ValueError("Gen 2 native trade requires overlay")
    return kind


def gen2_preflight(*, repo=None, game="gen2_new", scenario="link", artifact_kind="clean"):
    """Bind each side's fixture to its full qualification report and its title's pinned ROM."""
    root = Path(repo or REPO).resolve()
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from tests.live.test_gen2_new_gates import qualified_identity
    from tools.gen2_fixtures import BY_NAME
    from tools.gen2_source_data import load_context, load_overlay_context

    pairing = GAMES[game]
    if pairing.get("launch_profile") != "gen2":
        raise ValueError(f"not a Gen 2 duo pairing: {game}")
    result = {}
    for inst in ("a", "b"):
        refused = scenario == "gen2_admit_wrong_rom" and inst == "b"
        name = "crystal_battle_ot2" if refused else pairing["fixture"][inst]
        if scenario in GEN2_TRADE_SCENARIOS:
            name = GEN2_TRADE_FIXTURES[game][inst]
        if scenario == "gen2_trade_evolve":
            name = GEN2_TRADE_EVOLVE_FIXTURES[game][inst]
        if scenario == "gen2_poison":
            name = GEN2_POISON_FIXTURES.get(game, {}).get(inst, name)
        if scenario == "gen2_ball_gate":
            name = GEN2_BALL_GATE_FIXTURES[game][inst]
        if scenario == "gen2_faint_active_trainer":
            name = GEN2_TRAINER_FIXTURES[game][inst]
        synth = None
        if scenario in GEN2_SYNTH_SCENARIOS:   # O-33: the base fixture's qualification, the synthetic bytes staged
            from tools import gen2_synth_fixtures

            synth = gen2_synth_name(scenario, game, inst)
            title, suffix = GEN2_SYNTH_PAIRS[game][inst]
            kind = GEN2_SYNTH_SCENARIOS[scenario]
            target = {**gen2_synth_fixtures.SYNTH_RECIPES, **gen2_synth_fixtures.DUO_RECIPES}[kind][0]
            name = f"{title}_{target}{suffix}"
        elif "_synth_" in name:   # an O-33 seed named directly (trainer B, trade_evolve A): boot its PLAYED base
            from tools import gen2_synth_fixtures

            synth = name
            name = gen2_synth_fixtures.build_named(synth, root=root)[1]["base_fixture"]
        if name not in BY_NAME:
            raise FileNotFoundError(f"Gen 2 lane missing played/qualified fixture declaration: {name}")
        title = BY_NAME[name].title
        if artifact_kind not in ("clean", "overlay"):
            raise ValueError("Gen 2 artifact must be clean or overlay")
        ctx = load_context(title, root=root) if artifact_kind == "clean" else load_overlay_context(title, root=root)
        source = ctx.source_record()
        base_rom = ctx.source_dir / ctx.lock["outputs"][ctx.artifact]["filename"]
        if hashlib.sha1(base_rom.read_bytes()).hexdigest() != source["rom_sha1"]:
            raise RuntimeError(f"{inst}: Gen 2 {title} ROM differs from the pinned source")
        rom = base_rom if artifact_kind == "clean" else root / f"patch/build/gen2_{title}_overlay.gbc"
        executed_sha1 = hashlib.sha1(ctx.rom).hexdigest()
        fixture = root / "tests/fixtures/gen2" / f"{name}.SaveRAM"
        raw = fixture.read_bytes()
        ot_id = qualified_identity(name, raw, repo=root, kind=artifact_kind)
        if synth is not None:
            fixture = root / "tests/fixtures/gen2" / f"{synth}.SaveRAM"
            built, _disclosure = gen2_synth_fixtures.build_named(synth, root=root)
            raw = fixture.read_bytes()
            if built != raw:
                raise RuntimeError(f"{inst}: {synth} is not the builder's output of {name}")
        receipt = root / ("tests/fixtures/gen2/receipts/overlay" if artifact_kind == "overlay"
                          else "tests/fixtures/gen2/receipts") / f"{name}.qualification.json"
        report = json.loads(receipt.read_text(encoding="utf-8"))
        result[inst] = {"name": name, "fixture": fixture, "sha256": hashlib.sha256(raw).hexdigest(),
                        "ot_id": ot_id, "qualification": receipt,
                        "qualification_attempt_id": report["attempt_id"],
                         "rom": rom, "rom_sha1": executed_sha1, "title": title, "artifact_kind": artifact_kind,
                         "source_rom_sha1": source["rom_sha1"],
                         "binding_sha256": ctx.execution_record().get("binding_sha256") if artifact_kind == "overlay" else None}
        if artifact_kind == "overlay":
            binding = root / f"data/games/gen2_{title}/overlay/binding.json"
            result[inst]["binding_sha256"] = hashlib.sha256(binding.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if synth is not None:
            result[inst]["synth"] = synth
        if refused:
            pin = ctx.lock["outputs"]["pokecrystal11"]
            wrong_rom = ctx.source_dir / pin["filename"]
            if hashlib.sha1(wrong_rom.read_bytes()).hexdigest() != pin["sha1"]:
                raise RuntimeError("b: refused Crystal 1.1 ROM differs from the pinned source")
            result[inst].update(rom=wrong_rom, rom_sha1=pin["sha1"], expect_admission="refused")
    if result["a"]["ot_id"] == result["b"]["ot_id"] or result["a"]["sha256"] == result["b"]["sha256"]:
        raise RuntimeError("Gen 2 duo requires distinct qualified OTs and fixture bytes")
    if scenario == "gen2_reconnect":
        name = f"{result['a']['title']}_battle_ot2"
        wrong = root / "tests/fixtures/gen2" / f"{name}.SaveRAM"
        if name not in BY_NAME or not wrong.is_file():
            raise FileNotFoundError(f"Gen 2 reconnect missing qualified wrong-save fixture: {name}")
        raw = wrong.read_bytes()
        if BY_NAME[name].title != result["a"]["title"] or qualified_identity(name, raw, repo=root, kind=artifact_kind) == result["a"]["ot_id"]:
            raise RuntimeError("Gen 2 reconnect wrong-save must match title and differ in OT")
        result["a"].update(wrong_fixture=wrong, wrong_sha256=hashlib.sha256(raw).hexdigest())
    return result


GAMES = {
    "gen2_new": {
        "main": "lua/tests/duo/duo_gen2_main.lua", "game": "gen2_new",
        "launch_profile": "gen2", "uses_savestate": False,
        "fixture": {"a": "crystal_battle", "b": "crystal_battle_ot2"},
        "scenario_prefix": "gen2_",
    },
    "gen2_gold_silver": {
        "main": "lua/tests/duo/duo_gen2_main.lua", "game": "gen2_new",
        "launch_profile": "gen2", "uses_savestate": False,
        "fixture": {"a": "gold_battle", "b": "silver_battle"},
        "scenario_prefix": "gen2_",
    },
    "gen2_crystal_gold": {
        "main": "lua/tests/duo/duo_gen2_main.lua", "game": "gen2_new",
        "launch_profile": "gen2", "uses_savestate": False,
        "fixture": {"a": "crystal_battle", "b": "gold_battle"},
        "scenario_prefix": "gen2_",
    },
    # The NEW Gen 1 client (lua/gen1/entry.lua composition root), Red as A and Blue as B, on
    # the battle fixtures rebuilt from scripted play (tools/gen1_fixtures.py).
    #
    # `patched_saves` is the cartridge each instance LAUNCHES (a run_gb_gate.PATCHED key): the
    # SLink companion is REQUIRED for Red/Blue/pureRGB (owner 2026-10-02), so every scenario runs
    # on red_patched/blue_patched or the pureRGB overlays, seeded from the clean title's fixture
    # (`fixture`; the companion adds ROM code and moves no SRAM, A4) under the companion build's
    # filename-derived SaveRAM name. `rom` stays the CLEAN base: it is what the randomized-admission
    # leg hands the randomizer, never what an emulator boots. The scenarios it
    # runs are the ones that NAME it: `gen1_new` is opt-in (OPT_IN_GAMES), so the
    # savestate-less shared ones do not leak in, and every scenario here
    # carries an `oracle` -- a Gen 1 verdict always reads the saved state (A0-H2). duo_gen1_main
    # refuses any name it does not implement, so `--scenario all --game gen1_new` must select
    # exactly this set (pinned in tests/unit/test_e2e_duo_scenario_selection.py).
    "gen1_new": {
        "main": "lua/tests/duo/duo_gen1_main.lua",
        "game": "gen1_new",
        "play": "gen1_playthrough",
        "rom": {"a": "patch/build/gen1_red.gb", "b": "patch/build/gen1_blue.gb"},
        "patched_saves": {"a": "red_patched", "b": "blue_patched"},
        "uses_savestate": False,
        "fixture": {"a": "red", "b": "blue"},
        "scenario_prefix": "gen1_",
    },
    # The pureRGB pairing (P3b-e): the SAME client, the same scenarios and the same driver main as
    # gen1_new -- only the cartridges and their fixtures differ. A=purered, B=pureblue (the vanilla
    # pair's shape). `game` stays "gen1_new" because the scenario registry names that game (a
    # scenario's `games` tuple is what selects it), while `self.game` (the row key) carries the
    # foundation: `is_gen1` and the battery-boot branch both read it. Every scenario runs on the
    # companion OVERLAY (clean build + patch/dist/SLink-Pure*.ups, PLAN M3/P4), booting the clean
    # pure fixture (A4); admit_randomized runs on the M5 fork jar.
    "gen1_pure": {
        "main": "lua/tests/duo/duo_gen1_main.lua",
        "game": "gen1_new",
        "play": "gen1_playthrough",
        "rom": {"a": "patch/build/gen1_purered.gbc", "b": "patch/build/gen1_pureblue.gbc"},
        "patched_saves": {"a": "purered_overlay", "b": "pureblue_overlay"},
        "uses_savestate": False,
        "fixture": {"a": "purered", "b": "pureblue"},
        "scenario_prefix": "gen1_",
    },
    # The third pure title on the B side (PureGreen's fixtures lead with Charmander like Blue's),
    # so every pure cartridge has a duo pairing; on the overlays, like gen1_pure.
    "gen1_pure_green": {
        "main": "lua/tests/duo/duo_gen1_main.lua",
        "game": "gen1_new",
        "play": "gen1_playthrough",
        "rom": {"a": "patch/build/gen1_purered.gbc", "b": "patch/build/gen1_puregreen.gbc"},
        "patched_saves": {"a": "purered_overlay", "b": "puregreen_overlay"},
        "uses_savestate": False,
        "fixture": {"a": "purered", "b": "puregreen"},
        "scenario_prefix": "gen1_",
    },
    # The NEW Gen 3 client on vanilla FRLG (lua/gen3/run.lua's build, docs/gen3/PLAN.md §5.5).
    # Battery boot like gen1_new, but through tools/gen3_fixtures.py's GBA config and flash
    # fixtures, dispatched by `play` (see DuoRun.is_gen3_battery). `sides` is (title, fixture
    # stem) per instance, the stem formatted with the scenario's per-instance target; the fixture
    # worker's saves are firered_party_{town,battle}{,_b}.sav. B is LeafGreen (the G4 FR<->LG pairing). `oracle_required`: a
    # scenario with no saved-state oracle FAILS here, the Gen 1 rule; `save_witness` is the
    # method _run_oracle runs first.
    "gen3_fr_trade": {
        "main": "lua/tests/duo/duo_gen3_main.lua", "game": "gen3_fr_trade", "play": "gen3_fixtures",
        "sides": {"a": ("firered", "firered_party_{target}"), "b": ("leafgreen", "leafgreen_party_{target}_b")},
        "uses_savestate": False, "scenario_prefix": "gen3_", "oracle_required": True,
        "save_witness": "check_native_trade_witness",
    },
    "gen3_lg_trade": {
        "main": "lua/tests/duo/duo_gen3_main.lua", "game": "gen3_fr_trade", "play": "gen3_fixtures",
        "sides": {"a": ("leafgreen", "leafgreen_party_{target}"), "b": ("firered", "firered_party_{target}_b")},
        "uses_savestate": False, "scenario_prefix": "gen3_", "oracle_required": True,
        "save_witness": "check_native_trade_witness",
    },
    "gen3_frlg": {
        "main": "lua/tests/duo/duo_gen3_main.lua",
        "game": "gen3_frlg",
        "play": "gen3_fixtures",
        "sides": {"a": ("firered", "firered_party_{target}"),
                  "b": ("leafgreen", "leafgreen_party_{target}")},   # FR<->LG, the G4 pairing (fixtures 0978a5be)
        "uses_savestate": False,
        "scenario_prefix": "gen3_",
        "oracle_required": True,
        "save_witness": "check_save_witness_gen3",
    },
    # LeafGreen as A (Codex review of d199da32, G4 item 2a): the gen3_frlg family with the sides
    # swapped, so every A-side receipt (the whiteout's Center write, the 2F controls) runs on LG.
    # `game` is the family: duo_gen3_main.lua, the scenario set and the retry rule are gen3_frlg's.
    "gen3_lgfr": {
        "main": "lua/tests/duo/duo_gen3_main.lua",
        "game": "gen3_frlg",
        "play": "gen3_fixtures",
        "sides": {"a": ("leafgreen", "leafgreen_party_{target}"),
                  "b": ("firered", "firered_party_{target}")},
        "uses_savestate": False,
        "scenario_prefix": "gen3_",
        "oracle_required": True,
        "save_witness": "check_save_witness_gen3",
    },
    # P5 (card C5-5): the NEW Gen 3 client on Radical Red, battery-boot like gen3_frlg (the same
    # `play`/duo_gen3_main.lua/witness method). `sides` stems "rr_{target}" match
    # tests/fixtures/gen3/README.md's naming (only "town" exists today: rr_town.sav / rr_town_b.sav,
    # rr_battle{,_b}.sav do not exist, so every scenario below whose `target` is "battle" FAILS
    # at fixture lookup until the fixture worker builds them; this is a real, named blocker, not
    # a silent gap). `game` is its own family (not aliased to "gen3_frlg") so duo_gen3_main.lua
    # and the SYS/pack selection can tell RR apart; `rr=True` is read by check_save_witness_gen3.
    "gen3_rr": {
        "main": "lua/tests/duo/duo_gen3_main.lua",
        "game": "gen3_rr",
        "play": "gen3_fixtures",
        "sides": {"a": ("radical_red", "rr_{target}"), "b": ("radical_red", "rr_{target}_b")},
        "uses_savestate": False,
        "scenario_prefix": "gen3_",
        "oracle_required": True,
        "save_witness": "check_save_witness_gen3",
        "rr": True,
    },
    # E4: the NEW Gen 3 client on vanilla Emerald, E<->E (no second vanilla Hoenn title to pair
    # with). Fixtures emerald_{town,battle,trainer}{,_b}.sav (tests/fixtures/gen3/README.md, the
    # make-emerald SYNTH saves; `derive-b --title emerald` makes the _b side). `game` is its own
    # family so duo_gen3_main.lua selects the gen3_emerald pack and pokeemerald.sym by title.
    "gen3_emerald": {
        "main": "lua/tests/duo/duo_gen3_main.lua",
        "game": "gen3_emerald",
        "play": "gen3_fixtures",
        "sides": {"a": ("emerald", "emerald_{target}"), "b": ("emerald", "emerald_{target}_b")},
        # the grass the drivers hunt, by fixture target (gen3_scripted_play.lua EMH.hunt): the
        # battle/catch fixtures stand in Route 102's loop (21..22,16..17); the two-mon pc fixture
        # starts in Oldale and walks to Route 103's loop (12..13,14..15). The server area id every
        # route_1-bound oracle reads through DuoRun._hunt_area. "catch" (20 balls) is the same
        # tile as "battle" (5 balls) -- link_gen3/deadzone_gen3 hunt there so a ball_hunt scenario
        # never runs the fixture's Poke Balls out for real.
        "hunt_area": {"battle": "route_102", "pc": "route_103", "catch": "route_102", "evolve": "route_102"},
        "uses_savestate": False,
        "scenario_prefix": "gen3_",
        "oracle_required": True,
        "save_witness": "check_save_witness_gen3",
    },
    # X3: the pokeemerald-expansion reference build (ROM 28877d73), E<->E on its own make-exp
    # fixtures (tests/fixtures/gen3/exp_*.sav); the ordinary production route is verified
    # from accepted server HELLOs and each client's unwrapped admission result.
    "gen3_exp": {
        "main": "lua/tests/duo/duo_gen3_main.lua",
        "game": "gen3_exp",
        "play": "gen3_fixtures",
        "sides": {"a": (GEN3_EXP_TITLE, "exp_{target}"), "b": (GEN3_EXP_TITLE, "exp_{target}_b")},
        "hunt_area": {"battle": "route_102", "pc": "route_103", "catch": "route_102",
                      "whiteout_synth": "route_103"},
        "uses_savestate": False,
        "scenario_prefix": "gen3_",
        "oracle_required": True,
        "save_witness": "check_save_witness_gen3",
    },
}


# FRLG-R4 helpers: independent ROM-file and saved-flash oracles. These are
# exclusive to the three randomized rows; no trade or legacy scenario changes.
GEN3_RAND_ADMISSION = {"admit_randomized_frlg", "admit_randomized_emerald"}
GEN3_RAND_SCENARIOS = GEN3_RAND_ADMISSION | {"link_gen3_rand", "trainer_panel_gen3_rand"}
GEN3_RAND_PHASES = {
    "pair": {"a": ("a", "rand"), "b": ("b", "rand")},
    "wrong_rom": {"a": ("b", "rand")},
    # patch-first (owner 2026-10-02): a clean or randomized-clean cartridge never reaches a server verdict, it
    # is refused at launch. Every randomized cartridge here carries the companion (kind `rand` on the wire,
    # plus companion_abi), and the partner of the mixed-kind / clean-equivalent legs is the plain COMPANION
    # (hello kind `companion`, which pairs as clean). The clean cartridge itself is the `clean_refused` leg.
    "mixed_kind": {"b": ("companion_b", "companion")},
    "rules_changed": {"a": ("forbidden_a", "rand")},
    "equivalent_pair": {"a": ("equivalent_a", "rand"), "b": ("companion_b", "companion")},
}
# The randomized ROMs are never committed: point SLINK_GEN3_RAND_ROMS at a directory holding
# {FireRed,LeafGreen}_allowed.gba, or Emerald_allowed.gba and Emerald_allowed_b.gba.
# Admission also needs the A title's *_widest.gba. Unset = BLOCK by name.
GEN3_RAND_SCRATCH = Path(os.environ["SLINK_GEN3_RAND_ROMS"]) if os.environ.get("SLINK_GEN3_RAND_ROMS") else None


def gen3_rand_retail(title):
    path = ("data/games/gen3_emerald/emerald_trainers.json" if title == "emerald"
            else "data/games/gen3_frlge/frlg_trainers.json")
    return json.loads(Path(REPO, path).read_text())["trainers"]


def gen3_rand_site_problems(raw, title, root=REPO):
    """Test-cartridge preflight only; this does not enable Emerald in Manager."""
    if title != "emerald":
        from server.upr_pipeline import gen3_site_mismatches
        return gen3_site_mismatches(raw, title)
    pack = Path(root, "data/games/gen3_emerald")
    sites = json.loads((pack / "engine_signals.json").read_text())["titles"][title]["artifacts"]["clean"]["sites"]
    records = []
    for kind, site in sites.items():
        records.append((kind, site["rom_offset"], site["expected_hex"]))
        if site.get("context"):
            context = site["context"]
            records.append((kind + " context", context["rom_offset"], context["expected_hex"]))
    anchors = json.loads((pack / "write_checkpoint.json").read_text())[title]["anchors"]
    records += [("checkpoint " + name, a["rom_offset"], a["expected_hex"]["clean"])
                for name, a in anchors.items()]
    return [f"{label} @0x{offset:06X}" for label, offset, expected in records
            if not expected or raw[offset:offset + len(bytes.fromhex(expected))] != bytes.fromhex(expected)]


def gen3_rand_dependencies(root=REPO):
    """Named BLOCKED reasons from COMMITTED dependencies, including dirty leases."""
    root = Path(root)
    paths = {"CR-R1": "lua/gen3/entry.lua", "CR-R2": "lua/gen3/client.lua",
             "server ingest": "server/adapters/gen3_frlge.py"}
    sources, problems = {}, []
    for label, path in paths.items():
        result = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{path}"],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            problems.append(f"{label}: {path} is not on the branch")
            sources[label] = ""
            continue
        sources[label] = result.stdout
        # filter-aware (core.autocrlf=true lanes check out CRLF): git decides "modified", not a
        # raw text compare against the LF blob, which reported a clean tree as dirty forever
        dirty = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", path]).returncode
        if not (root / path).is_file() or dirty:
            problems.append(f"{label}: {path} has uncommitted changes")
    entry = sources.get("CR-R1", "")
    if not re.search(r"['\"]rand['\"]|\brand\s*=", entry) or "randomizable" not in entry:
        problems.append("CR-R1: committed entry.lua lacks randomized anchor admission")
    if "rom_content" not in sources.get("CR-R2", ""):
        problems.append("CR-R2: committed client.lua lacks hello rom_content")
    if "def ingest_rom_content(" not in sources.get("server ingest", ""):
        problems.append("server ingest: committed gen3_frlge.py lacks ingest_rom_content")
    return problems


def gen3_rand_rom_facts(raw, title, root=REPO):
    """File-derived table closure and display names; never call the ingest adapter."""
    import struct

    from server.adapters.gen3_codec import decode_name
    from server.adapters.gen3_rom_tables import decode_rom_tables, gen3_content_fingerprint

    names = {"gTrainers", "gWildMonHeaders", "gEvolutionTable", "gSpeciesInfo",
             "gTrainerClassNames", "gSpeciesNames"}
    symbols = {}
    for line in (Path(root) / f"data/gen3/pret/poke{title}.sym").read_text().splitlines():
        words = line.split()
        if len(words) == 4 and words[-1] in names:
            symbols[words[-1]] = (int(words[0], 16), int(words[2], 16))
    if set(symbols) != names:
        raise ValueError("randomized oracle: missing pret table symbols")
    spans = []

    def read(address, size):
        offset = address - 0x08000000
        if size <= 0 or offset < 0 or offset + size > len(raw):
            raise ValueError(f"randomized oracle: out-of-range pointer {address:#x} + {size}")
        spans.append((address, address + size))
        return raw[offset:offset + size]

    for name, (address, size) in symbols.items():
        if name != "gSpeciesNames":
            read(address, size)
    address, size = symbols["gTrainers"]
    trainers = read(address, size)
    trainer_parties = {}
    for offset in range(0, size, 40):
        flags, count = trainers[offset], trainers[offset + 32]
        if flags > 3 or count > 6:
            raise ValueError("randomized oracle: invalid trainer flags/count")
        pointer = struct.unpack_from("<I", trainers, offset + 36)[0]
        trainer_parties[offset // 40] = []
        if count:
            stride = 16 if flags & 1 else 8
            party = read(pointer, count * stride)
            # Independent of decode_rom_tables/ingest: pret battle.h's lvl u8 at +2 and
            # species u16 at +4. A shared-decoder level bug must not teach the oracle its answer.
            trainer_parties[offset // 40] = [
                {"level": party[i + 2], "species": struct.unpack_from("<H", party, i + 4)[0]}
                for i in range(0, len(party), stride)]
        elif pointer:
            read(pointer, 1)
    address, size = symbols["gWildMonHeaders"]
    wild = read(address, size)
    wild_slots = {}
    for offset in range(0, size, 20):
        if wild[offset] == 255:
            break
        for habitat, count in enumerate((12, 5, 5, 10), 1):
            pointer = struct.unpack_from("<I", wild, offset + 4 * habitat)[0]
            if pointer:
                info = read(pointer, 8)
                slots = read(struct.unpack_from("<I", info, 4)[0], count * 4)
                # Independent u8 min/max + u16 species reads, retaining the first set on
                # multi-set maps. Do not obtain the expected slots from the ingest decoder.
                wild_slots.setdefault((wild[offset], wild[offset + 1], habitat),
                                      list(struct.iter_unpack("<BBH", slots)))
    else:
        raise ValueError("randomized oracle: wild sentinel missing")
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    blobs = [raw[start - 0x08000000:end - 0x08000000] for start, end in merged]
    payload = {"tables": [{"addr": pair[0], "hex": blob.hex()}
                          for pair, blob in zip(merged, blobs, strict=True)],
               "fingerprint": hashlib.sha1(b"".join(blobs)).hexdigest()}
    address, size = symbols["gSpeciesNames"]
    name_bytes = raw[address - 0x08000000:address - 0x08000000 + size]
    if len(name_bytes) != size or size % 11:
        raise ValueError("randomized oracle: species names truncated")
    tables = decode_rom_tables(raw, title)
    return {"title": title, "sha1": hashlib.sha1(raw).hexdigest(), "payload": payload,
            "tables": tables, "trainer_parties": trainer_parties, "wild_slots": wild_slots,
            "content_fingerprint": gen3_content_fingerprint(tables),
            "species_names": {i // 11: decode_name(name_bytes[i:i + 11])
                              for i in range(0, size, 11)}}


def gen3_probe_flip_choice(patched, row, anchors):
    """The ONE byte the protected-span probe flips, chosen by a fixed rule so the same build always gives the same byte.

    Rule: the LAST byte of the payload's version slot (`row["payload_version_slot"]`, inside the first protected span). The slot is a
    GBA text string, 0xFF-terminated and zero padded; its last byte is padding after the terminator, so the game cannot read it, the
    flip does not break boot, and it still changes the ROM hash. If that byte is not a 0x00 behind a 0xFF terminator, the fallback is the
    first byte of the first protected span outside the version slot. The byte must lie in a protected span and outside every anchor
    (`anchors`: [(rom_offset, length)]), else ValueError. Returns {offset, before, after, span, rule}."""
    spans = row["protected_spans"]
    slot = row["payload_version_slot"]
    base = spans[0]["offset"] + slot["offset"]
    slot_bytes = patched[base:base + slot["length"]]
    if slot_bytes[-1:] == b"\x00" and b"\xff" in slot_bytes[:-1]:
        offset, rule = base + slot["length"] - 1, "version-slot padding byte"
    else:
        offset, rule = next((spans[0]["offset"] + i for i in range(spans[0]["size"])
                             if not base <= spans[0]["offset"] + i < base + slot["length"]), None), "first non-slot payload byte"
    span = next((k for k, s in enumerate(spans) if offset is not None and s["offset"] <= offset < s["offset"] + s["size"]), None)
    if span is None:
        raise ValueError("probe byte is not inside a protected span")
    if any(start <= offset < start + length for start, length in anchors):
        raise ValueError(f"probe byte {offset:#x} is on an anchor")
    return {"offset": offset, "before": patched[offset], "after": patched[offset] ^ 0x01, "span": span, "rule": rule,
            "span_offset": spans[span]["offset"], "span_size": spans[span]["size"]}


def probe_flip_facts(flip):
    """The flipped cartridge, named: which title (B's cart: gen3_frlg flips LeafGreen, gen3_lgfr FireRed, gen3_emerald Emerald), its byte,
    the protected span that byte is inside, and that it is off every anchor."""
    return (f"flipped_title={flip['title']} cart=b offset={flip['offset']:#x} inside_protected_span={flip['span_offset']:#x}+{flip['span_size']:#x} "
            f"(span {flip['span']}) on_anchor=false (checked {flip['anchors_checked']} anchors)")


def gen3_rand_equivalent_rom(patched, protected_spans):
    """SYNTH 32(b) on the COMPANION cartridge: an unknown hash whose tables and engine/companion sites are
    byte-identical to the published companion -- the last 0xFF padding byte outside every protected span is
    changed to 0xFE."""
    spans = [(s["offset"], s["offset"] + s["size"]) for s in protected_spans]
    for at in range(len(patched) - 1, -1, -1):
        if patched[at] == 0xFF and not any(lo <= at < hi for lo, hi in spans):
            return patched[:at] + b"\xFE" + patched[at + 1:]
    raise ValueError("no unused 0xFF padding byte outside the companion's protected spans")


def gen3_rand_hello(text):
    rows = re.findall(r"(?m)^RAND_HELLO (.+)$", text or "")
    if len(rows) != 1:
        raise ValueError(f"expected one complete RAND_HELLO, got {len(rows)}")
    return json.loads(rows[0])


def gen3_rand_contract(facts):
    """Manager wire contract: the semantic SHA-256, not the transport SHA-1."""
    return {"players": {side: {"rom_sha1": facts[side]["sha1"],
                              "fingerprint": facts[side]["content_fingerprint"]} for side in ("a", "b")}}


def gen3_rand_hello_problems(hello, facts, kind="rand"):
    problems = []
    if hello.get("artifact_kind") != kind:
        problems.append(f"hello kind {hello.get('artifact_kind')!r}, expected {kind}")
    if hello.get("rom_sha1", "").lower() != facts["sha1"]:
        problems.append("hello SHA-1 differs from launched ROM file")
    if hello.get("rom_type") != facts["title"]:
        problems.append("hello title differs from launched ROM file")
    if kind == "rand" and hello.get("rom_content") != facts["payload"]:
        problems.append("hello table bytes/fingerprint differ from independent ROM closure")
    return problems


# The clean-pairing class: the server commits the artifact_kind of the FIRST hello of a run (server/server.py:2271-2284). In the
# equivalent_pair leg the companion partner may land first (committing "companion") or the clean-equivalent randomized cart may
# (committing "clean"); both are the same pairing class (gen3_frlge.pairing_kind maps companion -> clean), so either is correct.
GEN3_EQUIVALENT_PAIRING_KINDS = ("clean", "companion")


def gen3_rand_admission_problems(phase, status, hellos, facts):
    """No connected/default-admitted shortcut: require actual contract verdicts."""
    problems = []
    for side, (source, kind) in GEN3_RAND_PHASES[phase].items():
        player = (status.get("players") or {}).get(side, {})
        problems += [f"{side}: {p}" for p in gen3_rand_hello_problems(
            hellos.get(side, {}), facts[source], kind)]
        reason = player.get("admission_reason", "")
        if phase in ("pair", "equivalent_pair"):
            expected_reason = ("cartridge matches the contract" if phase == "pair"
                               else "no randomized-ROM contract for this run")
            if (not player.get("connected") or player.get("admission") != "admitted"
                    or reason != expected_reason):
                problems.append(f"{side}: no explicit accepted contract verdict")
        else:
            named = ("not the cartridge built for player a" if phase == "wrong_rom"
                     else "randomized evolutions" if phase == "rules_changed" else "Mixed artifact kinds:")
            if player.get("admission") != "rejected" or named not in reason:
                problems.append(f"{side}: missing named {phase} rejection")
            if phase == "wrong_rom" and any(facts[p]["content_fingerprint"][:12] not in reason
                                             for p in ("a", "b")):
                problems.append("wrong-ROM rejection lacks expected/reported fingerprints")
            if phase == "mixed_kind" and not all(k in reason for k in ("'rand'", "'companion'")):
                problems.append("mixed-kind rejection does not name both kinds")
            if player.get("party_keys") or player.get("trainer_name") or player.get("current_area_id"):
                problems.append(f"{side}: rejected cartridge adopted party/identity/area")
            if status.get("links"):
                problems.append(f"{side}: admission-only negative leg created links")
    if phase in ("pair", "equivalent_pair"):
        effective = GEN3_EQUIVALENT_PAIRING_KINDS if phase == "equivalent_pair" else ("rand",)
        if status.get("gen3_rand_effective_kind") not in effective:
            problems.append(f"{phase}: effective kind {status.get('gen3_rand_effective_kind')!r} is not "
                            f"{' or '.join(effective)}")
        if status.get("gen3_rand_identity_errors") != {}:
            problems.append(f"{phase}: identity/pairing error proof missing or nonempty")
    if phase == "equivalent_pair" and (
            facts["equivalent_a"]["sha1"] == facts["clean_a"]["sha1"]
            or facts["equivalent_a"]["content_fingerprint"] != facts["clean_a"]["content_fingerprint"]):
        problems.append("32(b): control is not an unknown hash with clean-equivalent tables")
    if phase == "pair":
        problems += gen3_rand_encounter_problems(status.get("gen3_rand_probe") or {}, facts)
    return problems


def gen3_rand_encounter_problems(probes, facts):
    """Emerald Route 102 land projection versus independent cartridge slot reads.

    pret pokeemerald src/wild_encounter.c: ChooseWildMonIndex_Land uses these
    twelve slot weights. The map header is group 0, number 17, not FR/LG Route 1.
    """
    problems = []
    for side in ("a", "b"):
        if facts[side]["title"] != "emerald":
            continue
        probe = probes.get(side) or {}
        if probe.get("encounter_area") != "route_102":
            problems.append(f"{side}: Emerald encounter probe is not Route 102")
        slots = facts[side]["wild_slots"].get((0, 17, 1), [])
        if len(slots) != 12:
            problems.append(f"{side}: independent Route 102 land slots missing")
            continue
        expected = {}
        for (low, high, species), rate in zip(slots, (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1), strict=True):
            row = expected.setdefault(species, [0, low, high])
            row[0] += rate
            row[1], row[2] = min(row[1], low), max(row[2], high)
        land = (probe.get("encounters") or {}).get("Grass") or []
        actual = [(e.get("species_id"), [e.get("rate"), e.get("min_level"), e.get("max_level")]) for e in land]
        if len(actual) != len(expected) or dict(actual) != expected:
            problems.append(f"{side}: Route 102 encounters differ from its OWN ROM")
    return problems


def _gen3_rand_species(name):
    import unicodedata

    text = str(name).replace("♀", "F").replace("♂", "M")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def gen3_rand_panel_problems(probes, facts, retail, *, expected_area):
    """Actual per-player trainer_brief + calc fallback versus independent ROM IDs."""
    if not expected_area:
        raise ValueError("gen3_rand_panel_problems requires the fixture's expected_area")
    problems, selected = [], {}
    for side in ("a", "b"):
        probe = probes.get(side) or {}
        if probe.get("kind") != "rand" or not probe.get("area"):
            problems.append(f"{side}: randomized per-player adapter/area missing")
        if probe.get("area") != expected_area:
            problems.append(f"{side}: nearby area is not the trainer fixture's {expected_area}")
        briefs = probe.get("briefs") or {}
        requested = {str(tid) for tid, row in retail.items() if row.get("area") == expected_area and row.get("party")}
        if set(briefs) != requested:
            problems.append(f"{side}: nearby trainer brief IDs differ from the independent source roster")
        if not briefs:
            problems.append(f"{side}: no nearby trainer briefs")
        changed = False
        for tid, brief in briefs.items():
            party = facts[side]["trainer_parties"].get(int(tid))
            base = retail.get(str(tid), {})
            if not party or base.get("area") != expected_area:
                problems.append(f"{side}: trainer {tid} has no ROM party/nearby source area")
                continue
            expected = [(_gen3_rand_species(facts[side]["species_names"][m["species"]]), m["level"])
                        for m in party]
            actual = [(_gen3_rand_species(m.get("species")), m.get("level"))
                      for m in (brief.get("party") or [])]
            if actual != expected:
                problems.append(f"{side}: trainer {tid} party differs from its OWN ROM")
            if brief.get("area") != expected_area:
                problems.append(f"{side}: trainer {tid} is not nearby")
            if brief.get("calc_label") or (probe.get("calc_labels") or {}).get(str(tid)) != "":
                problems.append(f"{side}: trainer {tid} did not use species/level calc fallback")
            old = [(_gen3_rand_species(m["species"]), m["level"]) for m in base.get("party", [])]
            changed |= expected != old
            selected.setdefault(str(tid), {})[side] = expected
        if not changed:
            problems.append(f"{side}: nearby parties do not distinguish ROM from retail")
    if not any(set(pair) == {"a", "b"} and pair["a"] != pair["b"] for pair in selected.values()):
        problems.append("nearby trainer control does not distinguish the two cartridges")
    return problems


def gen3_rand_saved_problems(saved, fixture, *, unchanged_bytes=False):
    try:
        if gen3_decode(saved) != gen3_decode(fixture):
            return ["passive cartridge's saved party/boxes changed"]
        if unchanged_bytes and gen3_codec().split_rtc(saved)[0] != gen3_codec().split_rtc(fixture)[0]:
            return ["admission-only cartridge's flash bytes changed"]
    except (ValueError, KeyError) as exc:
        return [f"saved-state PYDEC refused: {exc}"]
    return []


# pret map_groups: FR/LG Route 1 is (3, 19); Emerald Route 102 is (0, 17) (the same key
# gen3_rand_encounter_problems already reads from the independent wild_slots probe, and
# data/games/gen3_emerald/area_map.json "0:17": "route_102").
GEN3_RAND_HUNT_TABLE = {"firered": ((3, 19), "Route 1"), "leafgreen": ((3, 19), "Route 1"),
                        "emerald": ((0, 17), "Route 102")}


def gen3_rand_capture_problems(saved, key, facts):
    party, boxes = saved
    found = [m for m in party if gen3_key(m) == key]
    problems = []
    if len(found) != 1 or any(gen3_key(m) == key for m in boxes.values()):
        return ["randomized catch not saved exactly once in party"]
    map_key, area_label = GEN3_RAND_HUNT_TABLE[facts["title"]]
    land = facts["tables"]["wild_encounters"].get(map_key, [])
    slots = [m for variant in land for m in (variant.get("land") or {}).get("mons", [])]
    if not any(m["species"] == found[0]["species"]
               and min(m["min_level"], m["max_level"]) <= found[0]["level"]
               <= max(m["min_level"], m["max_level"]) for m in slots):
        problems.append(f"saved catch is absent from this cartridge's {area_label} land table")
    return problems


def gen3_rand_catch_note(side, title, path, fixture):
    """R4-LINK's 20-ball fixture disclosure. FR/LG's catch_synth is a SYNTH ball-count edit
    (tests/fixtures/gen3/README.md "R4-LINK catch stock"); Emerald's catch fixture predates
    R4-LINK and needs no edit (same README, E2-FIX-VARIANTS round 3): already 20 balls."""
    balls = gen3_ball_count(fixture, title)
    if balls != 20:
        raise RuntimeError(f"{side}: R4-LINK fixture must have 20 Poke Balls, got {balls}")
    if title == "emerald":
        return (f"RAND_CATCH_FIXTURE {side} file={path} sha256={hashlib.sha256(fixture).hexdigest()} "
                f"PokeBalls={balls} SYNTH=false source=pre-existing_emerald_catch_fixture")
    return (f"RAND_SYNTH_FIXTURE {side} file={path} sha256={hashlib.sha256(fixture).hexdigest()} "
            f"PokeBalls={balls} change=quantity_and_section_checksum_only")


def gen3_rand_status_probe(server, status, retail=None):
    """Read-only harness instrumentation of the LIVE server's existing adapters.

    No substitute ingest/brief implementation or expected answers are installed.
    Forest's non-key trainers have briefs even though its key-trainer widget is
    empty; the R4 row qualifies those nearby briefs, not UI navigation to Pewter.
    """
    from server.server import _calc_trainer_label

    probes = {}
    for side in ("a", "b"):
        adapter = server.adapter_for(side)
        title = getattr(adapter, "_rom_type", "firered")
        roster = retail if retail is not None else gen3_rand_retail(title)
        area = server.player_area_id.get(side, "")
        # Select IDs from independent pret area metadata, never the adopted table being tested.
        briefs = {str(tid): adapter.trainer_brief(int(tid))
                  for tid, row in roster.items() if row.get("area") == area and row.get("party")}
        briefs = {tid: brief for tid, brief in briefs.items() if brief}
        probes[side] = {"kind": getattr(adapter, "_artifact_kind", None), "area": area,
                        "briefs": briefs, "calc_labels": {
                            tid: _calc_trainer_label(brief, [{"species_name": m["species"]}
                                                           for m in brief["party"]])
                            for tid, brief in briefs.items()}}
        if title == "emerald":
            probes[side].update(encounter_area="route_102", encounters=adapter.encounter_table("route_102"))
    return {**status, "gen3_rand_probe": probes, "gen3_rand_effective_kind": server.state.artifact_kind,
            "gen3_rand_identity_errors": dict(server.state.identity_error)}


def gen3_rand_server_main():
    """Only randomized duo rows launch this read-only status-instrumented server."""
    import asyncio

    from server import server as module

    original = module.SLinkServer._build_status_dict
    # per-title retail roster is chosen inside the probe (gen3_rand_retail)
    module.SLinkServer._build_status_dict = lambda self: gen3_rand_status_probe(self, original(self))
    asyncio.run(module.main(host="127.0.0.1", port=int(sys.argv[1]), http_port=int(sys.argv[2]),
                            reset=True, data_dir=sys.argv[3]))


def gen3_production_route_lines(server_text, client_text, side, pack, title, rom_hash):
    """One accepted server route and the client's actual admit_routed identity, or None.

    Shared by duo and extracted-ZIP evidence. A requested route, old override receipt,
    wrong player/title/hash, or TCP connection alone cannot qualify production routing.
    """
    if side not in ("a", "b") or any("TEST-ONLY" in text for text in (server_text, client_text)):
        return None
    server_pattern = re.compile(rf"^.*\[{side}\] route {re.escape(title)} -> {re.escape(pack)} \(production\)$")
    client_pattern = re.compile(
        rf"^.*\[SLink-gen3\] {re.escape(pack)}/{re.escape(title)} \(clean by hash\) "
        rf"player {side} -> \S+:\d+ \(rom ([0-9a-fA-F]{{8}})\)$")
    server_line = next((line for line in server_text.splitlines() if server_pattern.fullmatch(line)), None)
    client_line = next((line for line in client_text.splitlines()
                        if (match := client_pattern.fullmatch(line))
                        and match[1].lower() == rom_hash[:8].lower()), None)
    return (server_line, client_line) if server_line and client_line else None


GEN3_COMPANION_GAMES = ("gen3_frlg", "gen3_lgfr", "gen3_emerald")


def gen3_companion_game_problem(game):
    """Why --gen3-companion cannot apply to `game`, or None. The flag swaps the CLEAN FR/LG/Emerald dumps for their patched
    companions (patch/build/slink_*.gba); RR already boots its companion and the expansion has no companion."""
    if game in GEN3_COMPANION_GAMES:
        return None
    return (f"--gen3-companion applies only to {', '.join(GEN3_COMPANION_GAMES)} (FR/LG/Emerald pairings), not {game}")


def gen3_companion_admission_problems(results, sides, pins):
    """(problems, proof lines): each side's OWN admission line -- lua/gen3/run.lua "[SLink-gen3] pack/title (kind by how)
    player X -> host:port (rom HASH8)" -- must say `companion by hash` for that side's title and carry the first 8 hex of
    the title's pinned companion sha1. `sides` {inst: title}, `pins` rom_pins() with the `<title>_companion` keys.
    Every admission line of the side must qualify (a relaunch in the same receipt cannot hide a clean one); a clean-labelled
    line, an anchors/header admission, another title, another player's line or another hash is a problem."""
    problems, proofs = [], []
    for inst, title in sides.items():
        pin = pins.get(f"{title}_companion")
        pack = GEN3_PACKS[title]
        if not pin:
            problems.append(f"{inst}: no {title}_companion pin to compare the admission hash with")
            continue
        text = results.get(inst) or ""
        want = (rf"\[client\] \[SLink-gen3\] {re.escape(pack)}/{re.escape(title)} \(companion by hash\) "
                rf"player {inst} -> \S+:\d+ \(rom {pin[:8]}\)")
        lines = re.findall(r"(?m)^\[client\] \[SLink-gen3\] \S+/\S+ \(\w+ by \w+\) player " + inst + r" .*$", text)
        good = [ln for ln in lines if re.fullmatch(want, ln)]
        bad = [ln for ln in lines if ln not in good]
        if not good:
            problems.append(f"{inst}: no `{title} (companion by hash)` admission line with rom {pin[:8]} (the pin)")
        problems += [f"{inst}: admission line is not the {title} companion by hash: {ln}" for ln in bad]
        proofs += good[:1]
    return problems, proofs


class DuoRun:
    def __init__(self, scenario, args, attempt=1):
        self.scenario = scenario
        self.attempt = attempt
        self.cfg = SCENARIOS[scenario]
        self.args = args
        self.game = getattr(args, "game", "gen3_rr")
        self.gcfg = GAMES[self.game]
        # --gen3-companion: boot the PATCHED FR/LG/Emerald cartridges (explicit per run; the clean rows never change)
        self.gen3_companion = bool(getattr(args, "gen3_companion", False))
        # What the launch path actually branches on is "does this game boot from a battery
        # save", not which generation it is — Gen 2 needs the identical treatment. Kept as
        # `is_gen1` only where a SCENARIO is genuinely Gen 1-specific.
        self.battery_boot = not self.gcfg["uses_savestate"]
        self.is_gen1 = self.game.startswith("gen1")
        self.tcp_port = free_port()
        self.http_port = free_port()
        if self.cfg.get("gen3_rand") or self.cfg.get("gen3_native_trade"):
            os.makedirs(BUILD, exist_ok=True)
        data_parent = (BUILD if self.cfg.get("gen3_rand") or self.cfg.get("gen3_native_trade")
                       else work_root("tmp"))
        if self.cfg.get("gen3_rand") and os.environ.get("SLINK_STATE_DIR"):
            data_parent = Path(os.environ["SLINK_STATE_DIR"]).resolve()
            if not data_parent.is_relative_to(Path(REPO).resolve()):
                raise RuntimeError("randomized lane SLINK_STATE_DIR must be private to this worktree")
            data_parent.mkdir(parents=True, exist_ok=True)
        # Keep the leaf short: native SaveRAM/evidence paths have a 240-character ceiling.
        self.data_dir = tempfile.mkdtemp(prefix="duo_", dir=data_parent)
        # This run's lane: the stub, the config copy, the SaveRAM directory and the window
        # position are keyed by it (the `lane` property below). --lane names a lane for a wrapper
        # that wants stable names ("pure-a", "lane3"); the port is the default.
        self._lane = getattr(args, "lane", None)
        self._pydec_path = (os.path.join(BUILD, f"e2e_{self.artifact_name}_pydec_result.txt")
                            if evidence_contract(self.game).require_oracle else None)
        # CODE-DIGEST (5b1274c9): the production code this run exercised, one line per pydec receipt
        self._code_stamp = (importlib.import_module("gen2_code_digest").run_stamp()
                            if scenario_family(getattr(self, "game", "")) == "gen2_new" else None)
        self.server = None
        self.emus = []
        self.emu_by_inst = {}
        self.go_files = {inst: os.path.join(BUILD, f"duo_go_{self.artifact_name}_{inst}.txt")
                         for inst in ("a", "b")}
        # When this attempt started, and when each instance was launched: artifact freshness is
        # measured against these, because a leftover file from an earlier run would otherwise
        # read as this run's evidence.
        self._started = time.time()
        self._launch_times = {}
        # Live legs that finished, by scenario name. Set ONLY by the live assert, never by the
        # post-result oracle: a client RESULT must not stand in for a leg that never ran.
        self._live_complete = {}
        self._same_save_artifact = None
        # Which phase each instance is currently writing a receipt for (reconnect relaunches),
        # the exits the harness itself asked for, and the run's own wall-clock bound.
        self._phase = {"a": "initial", "b": "initial"}
        self._expected_exit: set[str] = set()
        # What the wrapper would kill the subprocess at anyway (scenario_attempt_limit x timeout
        # + the teardown allowance), enforced here so an overrunning run ends with a FAIL
        # summary instead of an external kill.
        self._run_deadline = (time.time()
                              + scenario_attempt_limit(scenario, self.game) * self.cfg["timeout"]
                              + 300)

    def _gen3_duo_journal_path(self, inst):
        """One PRIVATE durable journal per instance, kept across that instance's relaunch phases within this data identity.

        Production has one journal per player, so A and B never share a file or its OS guard (sharing made one client's read answer
        "busy", hiding the other's captures: docs/gen3/RR_JOURNAL_ISOLATION_2026-09-29.md, per-instance addendum). A new private data
        directory (a new attempt, or the randomized-admission control's new identity) means new files for both.

        T5's native candidate owns its manifest-pinned path. The lock probe intentionally reads
        and locks the install-root path, so neither row uses this ordinary duo override.
        """
        if self.cfg.get("gen3_native_trade") or self.cfg.get("journal_lock_probe"):
            return None
        return Path(self.data_dir, f"slink_gen3_trade_{inst}")

    def _archive_gen3_duo_journals(self, archive):
        """Copy both instances' durable journal (.log and .guard) into `archive` under their own names; every source must exist."""
        sources = []
        for inst in "ab":
            base = self._gen3_duo_journal_path(inst)
            if base is None:
                raise RuntimeError("RR reset has no private durable journal path")
            for suffix in ("log", "guard"):
                source = Path(str(base) + f".{suffix}")
                if not source.is_file():
                    raise RuntimeError(f"RR reset missing durable trade journal {source}")
                sources.append(source)
        for source in sources:
            shutil.copy2(source, Path(archive) / source.name)

    # ── lane identity ────────────────────────────────────────────────────────
    def stub_path(self, inst: str) -> str:
        """The generated Lua stub for this lane and instance (one place that names it)."""
        return os.path.join(BUILD, f"duo_{self.lane}_{inst}.lua")

    def cfg_path(self, inst: str) -> str:
        """The per-lane BizHawk config copy for this instance."""
        return os.path.join(BUILD, f"duo_cfg_{self.lane}_{inst}.ini")

    @property
    def lane(self) -> str:
        """This run's lane id (--lane, else the run's TCP port).

        A property with a fallback because the unit tests build a DuoRun with `__new__` and fill
        in only the fields a case needs; a bare instance laning by its port (or "0") is right for
        the single run those cases describe.
        """
        named = getattr(self, "_lane", None)
        if named:
            return str(named)
        return str(getattr(self, "tcp_port", "0"))

    @property
    def lane_index(self) -> int:
        return lane_ordinal(self.lane)

    @property
    def is_gen3_battery(self) -> bool:
        """A row that boots GBA flash fixtures through tools/gen3_fixtures.py (gen3_frlg; P5's
        battery-boot gen3_rr row joins by naming the same `play`). A property for the same
        reason as `lane`: the unit tests build DuoRuns with `__new__`."""
        return (getattr(self, "gcfg", None) or {}).get("play") == "gen3_fixtures"

    # ── Gen 3 per-instance facts (title, fixture, ROM, battery file) ────────────────────
    def _gen3_title(self, inst) -> str:
        if getattr(self, "_rand_current", None):
            return self._rand_current[inst]["title"]
        return self.gcfg["sides"][inst][0]

    def _gen3_fixture_path(self, inst) -> str:
        if getattr(self, "cfg", {}).get("gen3_native_trade"):
            return str(Path(REPO) / self._native_candidate["fixtures"][inst]["path"])
        if inst in getattr(self, "_gen3_phase_fixtures", {}):
            return self._gen3_phase_fixtures[inst]
        stem = self.gcfg["sides"][inst][1].format(target=self._target_for(inst))
        return os.path.join(GEN3_FIXTURES, stem + ".sav")

    def _gen3_rom_kind(self, inst) -> str:
        """clean_rr_refused_gen3's per-instance ROM selection (a scenario field, like `target`):
        "companion" (default, the ROM every other RR scenario boots) or "clean" (the raw,
        unpatched dump, GEN3_CLEAN_RR_ROM). Titles other than radical_red ignore this."""
        kind = self.cfg.get("rom_kind", "companion")
        return kind[inst] if isinstance(kind, dict) else kind

    def _gen3_expect_refused(self, inst) -> bool:
        """True when this side is a REFUSAL PROOF (scenario field `expect_refused`, like `rom_kind`):
        the driver passes it only on lua/gen3/run.lua's companion-patch refusal, never as a client."""
        problems = gen3_expect_refused_problems(self.cfg)
        if problems:
            raise RuntimeError("; ".join(problems))
        leg = (getattr(self, "_rand_current", None) or {}).get(inst) or {}     # a randomized row's clean leg
        return inst in set(self.cfg.get("expect_refused", ())) or bool(leg.get("expect_refused"))

    def _gen3_launch_refusal_problems(self):
        """Patch-first preflight: a side that would boot a CLEAN companion-required cartridge as a working
        client dies at launch (Entry.admit_routed), so refuse the run up front and name why. The clean
        FR/LG/Emerald plans land here; --gen3-companion, a companion rom_kind and `expect_refused` clear it."""
        if (not getattr(self, "is_gen3_battery", False) or self.cfg.get("gen3_rand") or self.cfg.get("gen3_native_trade")
                or self.cfg.get("gen3_probe_flip")):      # (the probe stages its own companion carts; --gen3-companion is optional there)
            return []
        problems = []
        for inst in ("a", "b"):
            title = self._gen3_title(inst)
            clean = ((title in ("firered", "leafgreen", "emerald") and not getattr(self, "gen3_companion", False))
                     or (title == "radical_red" and self._gen3_rom_kind(inst) == "clean"))
            if clean and not self._gen3_expect_refused(inst):
                problems.append(f"{inst}: this row would boot a clean {title} as a working client, but a clean {title} is "
                                f"refused at launch (patch-first, owner 2026-10-02); run it with --gen3-companion, or "
                                f"mark the side expect_refused")
        return problems

    def _gen3_rom(self, inst) -> str:
        """The instance's ROM, staged to a space-free repo-relative path (gen3_fixtures.stage_rom,
        the launch rule shared with run_gate). An already-staged copy is used when the dump
        itself is not reachable from this checkout.

        radical_red's entry carries `staged` (ROM_REL, the already-built companion patch) for
        the ordinary "companion" kind, bypassing the dump search below entirely; clean_rr_refused_
        gen3's "clean" kind instead searches for the raw dump (GEN3_CLEAN_RR_ROM), same as
        firered/leafgreen.
        """
        from pathlib import Path

        if self.cfg.get("gen3_native_trade"):
            from tools.gen3_trade_duo import player_manifest
            return player_manifest(self._native_candidate, inst)["rom"]
        import gen3_fixtures
        if self.cfg.get("gen3_rand") or self.cfg.get("gen3_probe_flip"):
            if not getattr(self, "_rand_current", None):
                raise RuntimeError("randomized ROM preflight has not run")
            return self._rand_current[inst]["rom"]
        title = self._gen3_title(inst)
        row = GEN3_TITLES[title]
        if "artifact" in row:
            artifacts = Path(os.environ.get("SLINK_EXPANSION_ARTIFACTS")
                             or Path(REPO) / ".cache/expansion-output/reference")
            rom = artifacts / row["artifact"]
            if not rom.is_file():
                raise FileNotFoundError(f"{rom} not found (tools/build_expansion.py --host hgbox)")
            return gen3_fixtures.stage_rom(str(rom))
        if getattr(self, "gen3_companion", False) and title in ("firered", "leafgreen", "emerald"):
            return self._gen3_companion_rom(title)
        staged = row.get("staged")
        if staged and self._gen3_rom_kind(inst) == "companion":
            if not os.path.isfile(os.path.join(REPO, staged)):
                raise FileNotFoundError(f"{staged} not found (build the companion patch first)")
            return staged
        name = GEN3_CLEAN_RR_ROM if title == "radical_red" else row["rom"]
        for base in (Path(REPO), *Path(REPO).parents):
            if (base / name).is_file():
                return gen3_fixtures.stage_rom(str(base / name))
        staged = f"patch/build/gen3_{Path(name).stem.replace(' ', '_')}{Path(name).suffix}"
        if os.path.isfile(os.path.join(REPO, staged)):
            return staged
        raise FileNotFoundError(f"{name} not found at the repo root or any parent, and no "
                                f"staged {staged}")

    def _gen3_companion_rom(self, title) -> str:
        """The staged companion cartridge of a --gen3-companion run, byte-pinned to patch/dist/gen3_companions.json; a missing
        or different file is a loud failure, never a fall back to the clean dump."""
        from tools.gen3_final_cut import COMPANION_ROMS, rom_pins

        rel = COMPANION_ROMS[title]
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{rel} not found (stage the {title} companion cartridge: patch/dist/SLink-*.ups on the clean dump)")
        pin = rom_pins(REPO, require_companions=True)[f"{title}_companion"]
        got = hashlib.sha1(Path(path).read_bytes()).hexdigest()
        if got != pin:
            raise RuntimeError(f"{rel} sha1 {got} does not match the {title} companion pin {pin} (patch/dist/gen3_companions.json)")
        return rel

    def _gen3_require_companion_admission(self, results):
        """A --gen3-companion run proves, from each client's own admission line, that it ran the companion by hash."""
        if not getattr(self, "gen3_companion", False):
            return
        from tools.gen3_final_cut import rom_pins

        # the protected-span probe's B is an UNKNOWN-hash cart on purpose (one byte flipped): only A is proven by hash
        sides = {inst: self._gen3_title(inst) for inst in (("a",) if self.cfg.get("gen3_probe_flip") else ("a", "b"))}
        problems, proofs = gen3_companion_admission_problems(results, sides, rom_pins(REPO, require_companions=True))
        if problems:
            raise RuntimeError("companion provenance: " + "; ".join(problems))
        for inst, line in zip(sides, proofs, strict=True):
            self._pydec_note(f"COMPANION_ADMISSION {inst}: {line}")

    def _gen3_battery_path(self, inst) -> str:
        title = self._gen3_title(inst)
        row = GEN3_TITLES[title]
        if self.cfg.get("gen3_native_trade"):
            from tools import gen3_fixtures
            return os.path.join(self._saveram_dir(inst), gen3_fixtures.saveram_name(self._gen3_rom(inst)))
        if self.cfg.get("gen3_rand") or self.cfg.get("gen3_probe_flip"):
            import gen3_fixtures

            current = self._rand_current[inst]
            saveram = (row["saveram"] if current.get("clean")
                       else gen3_fixtures.saveram_name(current["rom"]))
            return os.path.join(self._saveram_dir(inst), saveram)
        if title == "radical_red" and self._gen3_rom_kind(inst) == "clean":
            # No gamedb hash for this dump either: the battery name follows whatever filename
            # _gen3_rom actually staged it under (gen3_fixtures.saveram_name), computed here
            # rather than hand-transcribed to avoid a transposition error going unnoticed.
            import gen3_fixtures

            saveram = gen3_fixtures.saveram_name(self._gen3_rom(inst))
        elif self._gen3_companion_cart(inst, title):
            # A patched FR/LG/Emerald cartridge is an unknown hash to BizHawk's gamedb: its battery follows the FILENAME
            # (same rule as the RR branch above), so seeding the clean gamedb name would leave an untouched file for the
            # oracles to read back (_flushed_saveram returns the seeded name whenever it exists).
            import gen3_fixtures

            saveram = gen3_fixtures.saveram_name(self._gen3_rom(inst))
        else:
            saveram = row["saveram"]
        return os.path.join(self._saveram_dir(inst), saveram)

    def _gen3_companion_cart(self, inst, title) -> bool:
        """True when `title` is a gamedb-known one (FR/LG/Emerald) and the resolved ROM is NOT its pinned clean dump."""
        if title not in ("firered", "leafgreen", "emerald"):
            return False
        from tools.gen3_final_cut import rom_pins

        return hashlib.sha1(Path(REPO, self._gen3_rom(inst)).read_bytes()).hexdigest() != rom_pins(REPO)[title]

    def _gen3_flushed(self, inst) -> bytes:
        """The battery the instance's EmuHawk left, by the gamedb name it was seeded under (or the
        one other *.SaveRAM in its directory, which gen3_fixtures names the same way)."""
        from pathlib import Path

        import gen3_fixtures

        seeded = Path(self._gen3_battery_path(inst))
        if self._gen3_companion_cart(inst, self._gen3_title(inst)):
            # The seed is written before the launch: only a file the emulator wrote after it is an oracle input, under the
            # derived name alone (no 'any other *.SaveRAM' fallback: a stale file of another name must not be adopted).
            launched = self._launch_times.get(inst)
            # A NO-WRITE half (`no_save`: the cartridge is expected to leave its battery as the harness seeded it) may be
            # an untouched seed: BizHawk need not flush an SRAM nothing wrote. What proves "nothing wrote" differs by row:
            # active_end_gen3's B and reconnect_gen3's A are READ by their oracles and compared byte-for-byte; the
            # center_controls_gen3 / save_then_write_gen3 B halves are never read at all, and their proof is the
            # declared-no_save gate in check_save_witness_gen3 (a no_save half whose receipt dumped a save FAILS).
            # Every other half still needs a battery the emulator wrote after the launch.
            no_write = inst in self.cfg.get("no_save", ())
            if not seeded.is_file() or (not no_write and (not launched or seeded.stat().st_mtime < launched)):
                raise RuntimeError(f"{inst}: companion cartridge has no fresh flushed battery at {rel_to_repo(seeded)} "
                                   f"(only the seed, or nothing, is there)")
            return seeded.read_bytes()
        found = gen3_fixtures._flushed_saveram(seeded.parent, seeded.name)
        if found is None:
            raise RuntimeError(f"{inst}: EmuHawk left no *.SaveRAM in {rel_to_repo(seeded.parent)}")
        return found.read_bytes()

    def _gen3_fixture_bytes(self, inst) -> bytes:
        with open(self._gen3_fixture_path(inst), "rb") as handle:
            body = handle.read()
        if getattr(self, "cfg", {}).get("gen3_native_trade") and hashlib.sha256(body).hexdigest() != self._native_candidate["fixtures"][inst]["sha256"]:
            raise RuntimeError(f"{inst}: T5 fixture changed before seeding/oracle")
        return body

    @property
    def _gen3_rr(self) -> bool:
        """The row decodes Radical Red's layout (GAMES[...]["rr"]); every saved-state read,
        record-validity rule and bag read below threads it (Codex C4-6b finding 1)."""
        return bool((getattr(self, "gcfg", None) or {}).get("rr"))

    @property
    def _hunt_area(self) -> str:
        """The server area id of the row's hunting grass (GAMES[...]["hunt_area"], a string or a
        {target: area} map read with A's fixture target -- gen3_emerald: battle -> route_102, pc ->
        route_103). Only the Kanto families (and a bare unit-stub run) default to Route 1: any
        other row that reaches a hunt-area oracle without naming its grass fails loud."""
        override = (getattr(self, "cfg", None) or {}).get("hunt_area_by_game", {})
        if override:
            return override[self.game]
        row = getattr(self, "gcfg", None) or {}
        if "hunt_area" in row:
            area = row["hunt_area"]
            if isinstance(area, dict):
                target = self._target_for("a")
                if target not in area:
                    raise RuntimeError(f"GAMES row names no hunt_area for target {target!r}")
                area = area[target]
            return area
        if row and row.get("game") not in ("gen1_new", "gen3_frlg", "gen3_rr"):
            raise RuntimeError(f"GAMES row family {row.get('game')!r} names no hunt_area; only the "
                               f"Kanto rows default to route_1")
        return "route_1"

    def _gen3_limits(self, inst):
        """gen3_limits for this instance's title over the ROM it booted (cached per ROM path):
        the species/level bounds and the move table every record's PP is checked against."""
        cache = self.__dict__.setdefault("_gen3_limits_cache", {})
        rom = self._gen3_rom(inst)
        if rom not in cache:
            with open(os.path.join(REPO, rom), "rb") as handle:
                cache[rom] = gen3_limits(self._gen3_title(inst), handle.read())
        return cache[rom]

    def _gen3_saved(self, inst):
        return gen3_decode(self._gen3_flushed(inst), rr=self._gen3_rr,
                           title=gen3_codec_title(self._gen3_title(inst)))

    def _gen3_fixture_saved(self, inst):
        return gen3_decode(self._gen3_fixture_bytes(inst), rr=self._gen3_rr,
                           title=gen3_codec_title(self._gen3_title(inst)))

    # ── lifecycle ────────────────────────────────────────────────────────────
    def wait_for(self, desc, pred, timeout, interval=0.2):  # ponytail: 0.2 s cut ~2 s/row (W23); raise if polling ever costs CPU
        """`wait_for`, with the cartridges' terminal RESULT lines as a second exit.

        Every orchestrate-time wait goes through this (the module-level `wait_for` stays for
        the places whose whole job is to collect a RESULT, and for callers outside a run). The
        rule is deliberately NOT "any RESULT ends the wait": one half finishing first is normal
        — A saves and exits while the runner is still waiting on B's markers (deadzone_new,
        pc_ops_new, whiteout_new) — so a wait ends early only when nothing can satisfy it any
        more:

          * both receipts carry a RESULT (nothing is left running), or
          * a receipt carries a FAIL, i.e. the run is already lost and the remaining wait can
            only burn its budget.

        The predicate is checked FIRST, and — unless a receipt FAILed — once more at the moment
        the abort would fire, so waits whose predicate IS the RESULT (wait_results, the
        reconnect B-done wait) still return normally however late the client writes it.
        """
        deadline = min(time.time() + timeout, getattr(self, "_run_deadline", float("inf")))
        while time.time() < deadline:
            value = pred()
            if value:
                return value
            finished = {inst: terminal_result(self._read_receipt(inst)) for inst in ("a", "b")}
            expected = getattr(self, "_expected_exit", set())
            # A process that exited AFTER writing its RESULT is FINISHED, not dead: its RESULT
            # counts toward the "both RESULT lines" predicate and the wait goes on for the
            # other half (the lane's misfire: B PASSED and exited while A was still playing,
            # and the abort named B as "gone with no RESULT" while quoting B's RESULT in the
            # same line). Only a process gone with NO RESULT of its own ends the wait.
            dead = sorted(inst for inst in ("a", "b")
                          if inst not in expected and self._process_exited(inst)
                          and not finished[inst])
            failed = any(reason.startswith("RESULT: FAIL") for reason in finished.values())
            if failed or dead or all(finished.values()):
                # LAST WORD TO THE PREDICATE. A wait reached after the clients have legitimately
                # finished is not an early finish: reconnect_new's "B stayed online through
                # reconnect legs" is a post-hoc check of a durable receipt, and at unthrottled
                # speed B wrote its PASS in the microseconds between this iteration's `pred()`
                # and the `finished` read above — the run aborted quoting the very PASS it was
                # waiting for (H-6). Everything these predicates read (receipts, server state)
                # is final once the clients are done, so one more call decides it. A FAIL keeps
                # its immediate abort: the run is lost and the budget is better spent elsewhere.
                if not failed:
                    value = pred()
                    if value:
                        return value
                raise ClientFinishedEarly(finished, desc, exited=dead)
            time.sleep(interval)
        if time.time() >= getattr(self, "_run_deadline", float("inf")):
            raise TimeoutError(f"the run's wall-clock budget expired while waiting for {desc}")
        raise TimeoutError(f"timed out after {timeout}s waiting for {desc}")

    def _read_receipt(self, inst):
        """The receipt an instance is CURRENTLY writing: its phase file once relaunched.

        reconnect_new's A writes `same_save` / `wrong_save` receipts after its relaunches, and a
        FAIL there has to end a wait the same way an initial receipt's would.
        """
        phase = getattr(self, "_phase", {}).get(inst, "initial")
        if phase == "initial":
            return read_result(self.artifact_name, inst) or ""
        path = self._phase_result_path(inst, phase)
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            return ""
        return text[:text.rfind("\n") + 1]  # complete lines only, as read_result

    def _process_exited(self, inst) -> bool:
        """True when this instance's emulator is gone — a dead client that never wrote a RESULT."""
        process = getattr(self, "emu_by_inst", {}).get(inst)
        return bool(process) and process.poll() is not None

    def _wire_dir(self):
        """This run's server-side wire-log directory, or None when --wire-log is absent."""
        if not getattr(self.args, "wire_log", False) and not self.cfg.get("rr_reset_trade"):
            return None
        return os.path.join(self.data_dir, "wire")

    def server_cmd(self):
        """Manager-equivalent run identity, stable across this run's reconnects."""
        if any(flag == "--test-only-route" or flag.startswith("--test-only-route=")
               for flag in self.args.server_flags):
            raise ValueError("test-only route flags are not supported")
        if not getattr(self, "_server_run_id", None):
            self._server_run_id = "duo-" + uuid.uuid4().hex
        cmd = [sys.executable, "-m", "server.server",
               "--host", "127.0.0.1",
               "--port", str(self.tcp_port),
               "--http-port", str(self.http_port),
               "--data-dir", self.data_dir, "--run-id",
               ("t5-" + self._native_candidate["nonce"]) if self.cfg.get("gen3_native_trade")
               else self._server_run_id] + self.cfg["flags"] + self.args.server_flags
        wire = self._wire_dir()
        if wire:
            cmd += ["--wire-log", wire]
        return cmd

    def collect_wire_logs(self):
        """Promote this run's wire log to the golden-transcript path, and name what landed.

        `tests/fixtures/gen3/wire/<scenario>_<player>_gen3_new.jsonl`, per that directory's
        README. Only a --wire-log run has anything to promote; the source is the server's own
        capture under the data dir, so this must run before the data dir is removed.

        `wire_rejected.jsonl` (the tap's bounded sink for lines it could not attribute to a
        real player) is server-side debug evidence, not a per-player transcript -- it is never
        promoted here.

        A player this run did NOT log is left with no transcript at all: its stale golden is
        removed and this run's data-dir source is named in the print instead. Copying what
        exists and saying nothing about what does not is what let a FAILING run be read off a
        PASSING run's transcript (T5, 2026-09-27: native_trade_firered_b_gen3_new.jsonl held a
        completed trade_done while the failing run's own wire/wire_b.jsonl held none).
        """
        if getattr(self, "cfg", {}).get("rr_reset_trade"):
            # Reset receipts keep their private server wire beside the immutable
            # battery/journal archive; never promote this partial row to a golden.
            return []
        wire = self._wire_dir()
        if not wire:
            return []
        sources = {}
        if os.path.isdir(wire):
            for name in sorted(os.listdir(wire)):
                if not (name.startswith("wire_") and name.endswith(".jsonl")):
                    continue
                player = name[len("wire_"):-len(".jsonl")]
                if player != "rejected":
                    sources[player] = os.path.join(wire, name)
        if sources:
            os.makedirs(WIRE_FIXTURES, exist_ok=True)
        # All active Gen 3 rows use the new battery client; non-Gen3 rows keep their game key
        # (getattr: a unit-test double need not carry a row key).
        label = "gen3_new" if self.is_gen3_battery else getattr(self, "game", "")
        # "a" and "b" are the tap's whole per-player set (server/server.py: VALID_PLAYERS), so a
        # player with no file in this run's dir is one whose golden is now older than the run.
        landed = []
        for player in sorted(set(sources) | {"a", "b"}):
            dest = os.path.join(WIRE_FIXTURES, f"{self.scenario}_{player}_{label}.jsonl")
            source = sources.get(player) or os.path.join(wire, f"wire_{player}.jsonl")
            if player in sources:
                shutil.copyfile(source, dest)
                print(f"[duo] wire log: {dest}")
                landed.append(dest)
            elif os.path.exists(dest):
                os.remove(dest)
                print(f"[duo] no wire log for {player} this run: {source} is absent; removed the "
                      f"stale {dest} (an earlier run's transcript)")
        return landed

    def start_server(self):
        cmd = self.server_cmd()
        if self.cfg.get("gen3_rand"):
            cmd = [sys.executable, "-c", "from tools.e2e_duo import gen3_rand_server_main; gen3_rand_server_main()",
                   str(self.tcp_port), str(self.http_port), self.data_dir]
        if self.scenario in GEN2_TRADE_SCENARIOS:
            cmd = [sys.executable, "-m", "tools.gen2_trade_lane", "--manifest",
                   str(self._gen2_trade_manifest_path), "--", *cmd[3:]]
        self.server = subprocess.Popen(
            cmd, cwd=REPO,
            # The handle is the server subprocess's stdout and must outlive this call —
            # a `with` would close it out from under the still-running server.
            stdout=open(os.path.join(self.data_dir, "server.log"), "w"),  # noqa: SIM115
            stderr=subprocess.STDOUT)
        self.wait_for("server HTTP up", lambda: self._status() is not None, 30)
        print(f"[duo] server up: tcp={self.tcp_port} http={self.http_port} data={self.data_dir}")

    def _require_production_route_receipt(self, timeout: float = 15):
        """Expansion-only receipt: accepted HELLOs and native admission after both connect.

        Other game rows deliberately keep their existing evidence contracts.
        """
        if getattr(self, "game", None) != "gen3_exp":
            return []
        title = GEN3_EXP_TITLE
        profile = json.loads(Path(gen3_profile_path(title)).read_text(encoding="utf-8"))
        rom_hash = profile["titles"][title]["rom_sha1"]
        deadline = time.time() + timeout

        def complete_lines(path):
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
            return text[:text.rfind("\n") + 1]

        while True:
            server = complete_lines(Path(self.data_dir) / "server.log")
            proofs = [gen3_production_route_lines(server, complete_lines(self._result_path(side)),
                                                  side, "gen3_exp", title, rom_hash)
                      for side in ("a", "b")]
            if all(proofs):
                break
            if time.time() >= deadline:
                missing = ", ".join(side for side, proof in zip("ab", proofs, strict=True) if not proof)
                raise RuntimeError(f"gen3_exp: missing production route/client admission for {missing}")
            time.sleep(0.2)
        for side, (server_line, client_line) in zip("ab", proofs, strict=True):
            self._pydec_note(f"PRODUCTION_ROUTE side={side} server={server_line}")
            self._pydec_note(f"PRODUCTION_ADMISSION side={side} client={client_line}")
        return proofs

    def _saveram_dir(self, inst: str) -> str:
        """A SaveRAM directory unique to this SCENARIO and this instance.

        Per-instance is the load-bearing half: two instances of one cartridge (Gen 2 runs
        Crystal on both sides) resolve to the same gamedb SaveRAM filename and would otherwise
        share one file and stamp on each other.

        Except for cold-boot and native-trade rows, the path is reused across invocations and nothing cleans
        it. That is safe only because `seed_saveram` overwrites the file before every launch,
        which is what actually prevents a crashed run's save leaking into the next one. Do not
        weaken that copy on the assumption this directory is fresh; it isn't.
        """
        if self.cfg.get("cold_boot") or self.cfg.get("gen3_native_trade") or self.cfg.get("rr_reset_trade"):
            # Preserve earlier cold-boot/native-trade batteries, including uncertain saves.
            return os.path.join(self.data_dir, f"saveram_{inst}")
        # Per scenario, instance AND LANE: two lanes on one scenario would otherwise seed and boot
        # from one directory. Seeding still overwrites the file before every launch (that is what
        # stops a crashed run's save leaking in), so this is belt-and-braces on the same rule.
        return os.path.join(BUILD, f"saveram_{self.scenario}_{self.lane}_{inst}")

    def _pydec_note(self, fact):
        """Keep Python oracle facts beside both Lua receipts for the release ledger."""
        line = str(fact).replace("\n", " ")
        print(f"[duo] {line}")
        path = getattr(self, "_pydec_path", None)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _status(self):
        try:
            return api(self.http_port, "GET", "/api/status", timeout=3)
        except Exception:
            return None

    def _raw_state(self):
        """The server's own state dump (`/api/debug/raw_state`).

        `_live.party_keys` here is `SoulLinkState.party_keys` — the field
        `_handle_party_to_box` discards from (server/state.py:2086) — unlike the `party_keys`
        `/api/status` publishes, which `_get_party_ordered` rebuilds from the cartridges'
        party snapshots every tick (server/server.py:2209, :4335-4343). The BOTH_BOXED gate
        reads this one.
        """
        try:
            return api(self.http_port, "GET", "/api/debug/raw_state", timeout=3)
        except Exception:
            return None

    def prepare_admit_randomized_new(self):
        """Build the real two-seed contract before the server or either cartridge starts."""
        import hashlib

        if REPO not in sys.path:
            sys.path.insert(0, REPO)  # python tools/e2e_duo.py otherwise has tools/ at sys.path[0]
        from server import cartridges
        from server.adapters.gen1_rom_scan import fingerprint_rom
        from server.upr_pipeline import find_upr_jar
        from server.upr_settings import build_categories

        jar = find_upr_jar()  # upr_pipeline.py:101-123 searches the main checkout's .cache/upr
        if not jar:
            raise RuntimeError("PokeRandoZX.jar missing; randomized admission cannot be skipped")
        sources = {p: os.path.join(REPO, self.gcfg["rom"][p]) for p in ("a", "b")}
        for player, source in sources.items():
            if not os.path.isfile(source):
                raise FileNotFoundError(f"clean {player} ROM missing: {source}")
        settings = os.path.join(self.data_dir, "settings.rnqs")
        # The pure family refuses every tweak (fastest text is a code write; pureRGB has
        # INSTANT text natively), so the pairing's family decides the flag.
        fastest = not is_pure_pairing(getattr(self, "game", ""))
        with open(settings, "wb") as handle:
            handle.write(build_categories({"wild"}, fastest_text=fastest))  # upr_settings.py:483-491
        # Companion required: the cartridges are what the Manager hands out for a randomized
        # companion run -- server.cartridges.provision, the production composition (vanilla
        # randomizes the clean bytes, THEN injects the companion; pureRGB randomizes the overlay
        # after its UPS). A randomized CLEAN cartridge is refused by the harness like any clean
        # one. provision runs prepare_pair once (both players, distinct seeds, rule-bearing data)
        # and writes the Manager-shaped contract to <run_dir>/rom_contract.json, which is the path
        # server.py reads from --data-dir.
        result = cartridges.provision(self.data_dir, sources, companion=True,
                                      randomize={"settings_path": settings}, jar=jar)
        with open(os.path.join(self.data_dir, "rom_contract.json"), encoding="utf-8") as handle:
            contract = json.load(handle)
        # BizHawk opens a .gbc as a Color title and misses the DMG battery save
        # (tools/make_randomized_patched.py:30-34). Stage the final bytes unchanged under a known
        # space-free .gb basename.
        stage_dir = os.path.join(BUILD, "e2e_admit_randomized_new")
        os.makedirs(stage_dir, exist_ok=True)
        stage = os.path.join(stage_dir, "slink_red_randomized.gb")
        shutil.copyfile(result["players"]["a"]["output"], stage)
        with open(stage, "rb") as handle:
            staged = handle.read()
        if hashlib.sha1(staged).hexdigest() != contract["players"]["a"]["rom_sha1"]:
            raise RuntimeError("staged randomized Red SHA-1 differs from the contract")
        if fingerprint_rom(staged) != contract["players"]["a"]["fingerprint"]:
            raise RuntimeError("staged randomized Red fingerprint differs from the contract")
        # Only A stages its own cartridge. B boots the row's companion build (`_rom_for`): a clean
        # cartridge is refused outright now (owner 2026-10-02), before any contract check, so the
        # negative control is the un-randomized COMPANION B, and its fingerprint is read off the
        # bytes that actually boot.
        self._admit_roms = {"a": os.path.relpath(stage, REPO).replace("\\", "/")}
        with open(os.path.join(REPO, self._rom_for("b")), "rb") as handle:
            b_fingerprint = fingerprint_rom(handle.read())
        if b_fingerprint == contract["players"]["b"]["fingerprint"]:
            raise RuntimeError("un-randomized B equals randomized Blue fingerprint; negative control is invalid")
        # A's save is seeded under the filename-derived name of the staged cartridge alone
        # (`_gen1_save_name`, which `_seed_instance_save` and the oracles share).
        self._admit_fingerprints = {"expected_b": contract["players"]["b"]["fingerprint"],
                                    "reported_b": b_fingerprint}
        print(f"[duo] admission contract staged: A={contract['players']['a']['fingerprint'][:12]} "
              f"B expected={contract['players']['b']['fingerprint'][:12]} "
              f"B companion={b_fingerprint[:12]}")
        return contract

    def _target_for(self, inst):
        """The fixture target for ONE instance.

        A scenario declares `target` as a string (both halves) or per instance as
        {"a": ..., "b": ...}. poison_new is the reason for the second form: B has to boot the
        battle fixture (post parcel, standing on Route 1) while A idles on the town one, or the
        idle half would meet wild mons of its own and muddle the receipt. `target_by_game`
        overrides it for one row (scenario_target).
        """
        target = scenario_target(self.cfg, getattr(self, "game", ""))
        if isinstance(target, dict):
            return target[inst]
        return target

    def _lane_window(self) -> str | None:
        """This lane's emulator window position, or None to leave the config's own position.

        The base is the process default (SLINK_EMU_WINDOW, which write_run_config already applied),
        moved one slot right per lane ordinal; "primary" keeps its meaning (leave the position).
        """
        base = os.environ.get("SLINK_EMU_WINDOW", "1200,-1300")
        if base.lower() == "primary" or self.lane_index == 0:
            return None
        try:
            x, y = (int(v) for v in base.split(","))
        except ValueError:
            return None
        return f"{x + _LANE_WINDOW_STEP * self.lane_index}, {y}"

    def _apply_lane_window(self, cfg_ini: str) -> None:
        """Move this lane's copied config off the shared window position (no-op for lane 0)."""
        pos = self._lane_window()
        if not pos:
            return
        try:
            with open(cfg_ini, encoding="utf-8-sig") as handle:
                cfg = json.load(handle)
        except (OSError, ValueError):
            return
        cfg["MainWindowPosition"] = pos
        cfg["MainWindowMaximized"] = False
        cfg["SaveWindowPosition"] = False
        with open(cfg_ini, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2)

    def _patch_key(self, inst: str) -> str | None:
        """The `run_gb_gate.PATCHED` row (companion cartridge) this instance launches, or None
        for a row without one.

        The GAME row decides, never the scenario: the companion is REQUIRED for Red/Blue/pureRGB
        (owner 2026-10-02), so every Gen 1 row except Yellow names its companion keys
        (red_patched/blue_patched, or the *_overlay keys on the pure rows) and every scenario on
        it runs that cartridge. This is the one place the key is read; `_rom_for` (what boots),
        `_seed_instance_save` (which file is seeded) and `_saved_gen1_party` (which file and ROM
        the oracle reads) all go through it, so the three can never disagree.
        """
        return (self.gcfg.get("patched_saves") or {}).get(inst)

    def _rom_for(self, inst: str) -> str:
        """The ROM path this instance launches.

        A scenario that stages its own cartridge for an instance (the randomized-admission leg's
        A) wins. Otherwise the instance's companion key does: a vanilla companion build is the
        literal `patch/gen1/build/slink_*.gb` path, and an overlay key stages ITS OWN cartridge
        (g1.staged_rom applies the UPS and sha1-verifies the result). Every Gen 1 row must
        declare a companion key, even when the scenario stages a randomized companion.
        Other battery-boot games resolve through their own play module's staged_rom().
        """
        if self.is_gen3_battery:
            return self._gen3_rom(inst)
        if self.gcfg.get("launch_profile") == "gen2":
            return Path(self._gen2_plans[inst]["rom"]).relative_to(Path(REPO).resolve()).as_posix()
        patch_key = self._patch_key(inst)
        if self.gcfg.get("game") == "gen1_new" and not patch_key:
            raise RuntimeError(f"{inst}: Gen 1 row has no companion key")
        admitted = getattr(self, "_admit_roms", None) or {}
        if inst in admitted:
            return admitted[inst]
        if patch_key:
            from run_gb_gate import PATCHED
            rom_rel = PATCHED[patch_key][1]
            if rom_rel is None:
                import gen1_playthrough as g1
                return g1.staged_rom(patch_key)
            return rom_rel
        staged = self.cfg.get("rom")
        if staged:
            return staged[inst]
        if self.battery_boot and self.gcfg.get("play") and self.gcfg.get("fixture"):
            play = importlib.import_module(self.gcfg["play"])
            return play.staged_rom(self.gcfg["fixture"][inst])
        return self.gcfg["rom"][inst]

    def _seed_instance_save(self, inst):
        if self.is_gen3_battery:
            # The fixture's flash body (any RTC suffix dropped: BizHawk writes its own), under the
            # gamedb battery name, in this instance's own directory -- overwritten every launch.
            codec = gen3_codec()
            seeded = self._gen3_battery_path(inst)
            os.makedirs(os.path.dirname(seeded), exist_ok=True)
            with open(seeded, "wb") as handle:
                handle.write(codec.split_rtc(self._gen3_fixture_bytes(inst))[0])
            return seeded
        if self.gcfg.get("launch_profile") == "gen2":
            fixture, plan = self._gen2_inputs[inst], self._gen2_plans[inst]
            raw = Path(fixture["fixture"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != fixture["sha256"]:
                raise RuntimeError(f"{inst}: qualified fixture changed after preflight")
            # DUO-CLOCK: every Gen 2 duo boot is pinned to a fixed DAY hour (matching the gate's
            # U1_CLOCK, tests/live/test_gen2_frame_align.py) with the same day_clock mechanism and
            # disclosure pattern -- the committed fixture on disk (source_rom_sha1 check above) is
            # untouched; only this runtime copy carries the pinned RTC trailer, so every oracle that
            # reads the boot fixture (fixture_sha256, boot_saveram, O-33 synth disclosures) still
            # binds against the qualified bytes. Without a pin, "the fixture RTC runs on with the
            # host clock" (same file, same comment as U1_CLOCK): fsw-gen3merge 2026-09-25/26, a
            # ~00:30-01:30 host-clock sweep, put gen2_new/gen2_poison's Route 30 hunt at night, where
            # Crystal's Weedle (data/wild/johto_grass.asm ROUTE_30) never appears (day/morn only).
            # A non-gameplay filler boot (e.g. a unit test's placeholder bytes, never a real save --
            # the sha256 check above only binds it to ITS OWN declared hash, not to a real save's
            # checksums) cannot be clock-pinned at all; that is disclosed, never silently skipped.
            from tools import gen2_synth_fixtures

            try:
                raw, clock = gen2_synth_fixtures.day_clock(raw, hour=DUO_CLOCK_HOUR, now=int(time.time()),
                                                           title=fixture["title"],
                                                           minute=_duo_clock_minute(getattr(self, "attempt", 1)))
                self._pydec_note(f"{inst}: clock_setup {clock}")
            except Exception as exc:  # noqa: BLE001 -- disclosed, not raised: see comment above
                self._pydec_note(f"{inst}: clock_setup skipped (not a played save): {exc!r}")
            destination = Path(plan["directory"]) / plan["saveram_name"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            return str(destination)
        if self.cfg.get("gen1_synth") == "explode":
            fixture = Path(self._prepare_gen1_explode_fixture(inst))
            booted = Path(self._saveram_dir(inst)) / self._gen1_save_name(inst)
            booted.parent.mkdir(parents=True, exist_ok=True)
            booted.write_bytes(fixture.read_bytes())
            return str(booted)
        from run_gb_gate import seed_saveram

        seeded = seed_saveram(self.gcfg["fixture"][inst], self._target_for(inst),
                              dest_dir=self._saveram_dir(inst))
        if self._patch_key(inst) or inst in (getattr(self, "_admit_roms", None) or {}):
            # MOVED, not copied, to the one name the booted cartridge (companion, or a staged
            # randomized one) reads and writes: any other name left beside it is a stale file an
            # oracle could read and pass on (the fixture's bytes, never touched by the run).
            booted = os.path.join(self._saveram_dir(inst), self._gen1_save_name(inst))
            os.replace(seeded, booted)
            seeded = booted
        return seeded

    @property
    def artifact_name(self):
        if scenario_family(getattr(self, "game", "")) == "gen2_new":
            # Escape the separator/glob characters in custom lanes: cc must not
            # share cc_more's cleanup prefix. Normal matrix lane names are unchanged.
            lane = urllib.parse.quote(self.lane, safe="-").replace("_", "%5F")
            return f"{self.scenario}_{lane}"
        return self.scenario

    def _phase_result_path(self, inst, phase="initial"):
        if phase == "initial":
            return self._result_path(inst)
        return os.path.join(BUILD, f"e2e_{self.artifact_name}_{inst}_{phase}_result.txt")

    def expected_idle_jitter(self) -> int:
        """The idle-frame count both stubs are told to apply, and the driver must echo back.

        Each attempt varies it so that a retried scenario is not the same run twice under a
        different label (see `jitter_for_attempt`).
        """
        return jitter_for_attempt(self.args.idle_jitter, self.attempt)

    def launch_instance(self, inst, *, phase="initial", seed=True, expected_key=""):
        """Launch one cartridge; reconnect phases keep the existing per-instance SaveRAM."""
        cfg_ini = self.cfg_path(inst)
        if self.is_gen3_battery:
            import gen3_fixtures

            # The GBA Save RAM path entry, not the Game Boy one gen1's write_run_config edits.
            gen3_fixtures.write_gba_run_config(BIZHAWK_CONFIG, cfg_ini, self._saveram_dir(inst))
        elif self.gcfg.get("launch_profile") == "gen2":
            from run_gb_gate import GENS
            GENS["gen2"]["config"](self._gen2_plans[inst], Path(cfg_ini))
        else:
            import gen1_playthrough as g1

            # purergb pins the config to GBC + not-SGB (PLAN A15); the fixture key names the
            # foundation, so the pure row needs nothing else to get it.
            g1.write_run_config(BIZHAWK_CONFIG, cfg_ini, saveram_dir=self._saveram_dir(inst),
                                purergb=g1.is_purergb(self.gcfg["fixture"][inst]))
        self._apply_lane_window(cfg_ini)
        self._phase = getattr(self, "_phase", {})
        self._phase[inst] = phase
        result = self._phase_result_path(inst, phase)
        if os.path.exists(result):
            os.remove(result)  # stale phase receipts cannot satisfy a new relaunch
        stub = self.stub_path(inst)
        fillers = self.cfg.get("fillers", True)
        duo = {
            "wt": WT_FWD, "player": inst, "scenario": self.scenario,
            "phase": phase, "expected_key": expected_key, "attempt": self.attempt,
            "idle_jitter": self.expected_idle_jitter(),
            "cold_boot": bool(self.cfg.get("cold_boot")),
            "max_attempts": 1 if self.cfg.get("cold_boot") else 2,
            "game": self.gcfg.get("game", ""),
            "fillers": fillers[inst] if isinstance(fillers, dict) else fillers,
            "mutate_otid": inst == "b", "result": result.replace("\\", "/"),
            "partner_result": self._phase_result_path("b" if inst == "a" else "a",
                self._phase.get("b" if inst == "a" else "a", "initial")).replace("\\", "/"),
            "go_file": self.go_files[inst].replace("\\", "/"),
            "timeout_frames": (lambda f: f[inst] if isinstance(f, dict) else f)(
                self.cfg.get("frames", self.cfg["timeout"] * 60)),
            # the driver's frame budgets were sized at 300%: above it, the scenario's frame cap grows by
            # the same factor (duo_gen2_main.lua h.wait also waits out the 300%-equivalent wall time)
            # The scenario's own wall budget, for the bodies that wait on a partner with a
            # bounded loop (poison_new's A half: `D.timeout_secs or 2400`).
            "timeout_secs": self.cfg["timeout"],
        }
        env = None
        if self.gcfg.get("launch_profile") == "gen2":
            duo["mutate_otid"] = False
            speed = self.gen2_speed()
            duo["speed_percent"] = speed
            # lua/scripted_inputs.lua refuses a play bound above 1,000,000 frames (its LIMIT)
            duo["timeout_frames"] = min(duo["timeout_frames"] * gen2_frame_scale(speed),
                                        max(duo["timeout_frames"], GEN2_FRAME_CAP))
            if self.game == "gen2_gold_silver" and self.scenario in ("gen2_poison", "gen2_species_clause"):
                duo["gs_harden"] = True
                duo["gs_setup"] = _gen2_gs_harden_facts(self._gen2_inputs[inst]["title"], root=REPO)
                self._pydec_note(f"SYNTH G/S setup inst={inst}: starter level/EXP/HP/stats at level 20; "
                                 "species/DVs/OT/moves/status/names retained. Poison also conditions the linked "
                                 "catch; species clause plants duplicate then unlike-family wild species at "
                                 "StartBattle before native generation. All writes disclosed in SYNTH_SETUP; "
                                 "catch, poison faint, reroll commands, memorial and saves remain native.")
            if self.scenario in GEN2_TRADE_SCENARIOS:
                duo["trade_manifest"] = str(self._gen2_trade_manifest_path).replace("\\", "/")
                duo["trade_manifest_sha256"] = self._gen2_trade_manifest_sha256
                duo["trade_case"] = self.scenario
                duo["trade_evidence_dir"] = str(Path(self.data_dir, "trade_evidence")).replace("\\", "/")
                duo["variant"] = self._gen2_trade_expected_case()["variant"]
            if self._gen2_inputs[inst].get("expect_admission") == "refused":
                duo["expect_admission"] = "refused"
            env = dict(os.environ, SLINK_ROOT=WT_FWD, **self._gen2_plans[inst]["env"],
                       **self._gen2_env[inst])
        if self.is_gen3_battery:
            # duo_gen3_main.lua resolves lua/tests/duo/scenario_<prefix><name>.lua and reads the
            # title's pack files and pret symbols by `title`.
            duo.update({"title": self._gen3_title(inst),
                        "scenario_prefix": self.gcfg["scenario_prefix"]})
            journal_path = self._gen3_duo_journal_path(inst)
            if journal_path is not None:
                duo["journal_path"] = journal_path.as_posix()
                print(f"[duo] JOURNAL attempt={self.attempt} inst={inst} phase={phase} "
                      f"scope=private data={self.data_dir} path={journal_path}")
            else:
                scope = "native-candidate" if self.cfg.get("gen3_native_trade") else "install-root-probe"
                print(f"[duo] JOURNAL attempt={self.attempt} inst={inst} phase={phase} "
                      f"scope={scope} install_root={REPO}")
            duo["ball_stock_phase"] = bool(self.cfg.get("post_flip_stock") and not self._gen3_rr)
            duo["expect_refused"] = self._gen3_expect_refused(inst)
            duo["probe_admission"] = bool(self.cfg.get("gen3_probe_flip")) and inst == "b"      # B holds the flipped cartridge; A is ordinary
            for field in ("scenario_module", "battle_window_case", "active_faint_case"):
                if field in self.cfg:
                    duo[field] = self.cfg[field]
            if self.cfg.get("rr_reset_trade"):
                expected = getattr(self, "_rr_reset_expected", {}).get(inst, {})
                duo.update(reset_case=self.cfg["reset_case"],
                           expected_token=expected.get("token", ""),
                           expected_counter=expected.get("counter"))
            if self.cfg.get("rule_kind"):
                from gen3_clause_rows import own_facts
                duo.update(rule_kind=self.cfg["rule_kind"], clause_facts=own_facts(self, inst))
            if self.cfg.get("acquisition_kind"):
                from gen3_gift_egg_rows import own_facts
                duo.update(acquisition_kind=self.cfg["acquisition_kind"], acquisition_facts=own_facts(self, inst))
            if self.cfg.get("static_wild_case"):
                from gen3_static_wild_rows import own_facts
                duo.update(static_wild_case=self.cfg["static_wild_case"], static_wild_facts=own_facts(self, inst))
            if self._gen3_rr:
                # the live EWRAM range RR's extension writer copies to sectors 30-31
                codec = gen3_codec()
                duo.update({"ext_addr": codec.RR_EXT_ADDR, "ext_size": codec.RR_EXT_SIZE})
            if self.cfg.get("gen3_native_trade"):
                from tools.gen3_trade_duo import ENV, player_manifest, validate_prepared
                candidate = player_manifest(self._native_candidate, inst)
                validate_prepared(Path(REPO), candidate)
                if hashlib.sha1((Path(REPO) / candidate["rom"]).read_bytes()).hexdigest() != candidate["rom_sha1"]:
                    raise RuntimeError("T5 candidate changed before emulator launch")
                duo.update(native_candidate_manifest=candidate["path"],
                           native_manifest_sha1=candidate["manifest_sha1"],
                           native_battery=self._gen3_battery_path(inst).replace("\\", "/"),
                           native_decline=self.cfg.get("native_decline", False))
                env = dict(os.environ, **{ENV: self._native_candidate["nonce"]})
                print(f"[duo] HARNESS_ONLY {ENV}={self._native_candidate['nonce']} side={inst} phase={phase}")
        if seed and not self.cfg.get("cold_boot"):
            self._seed_instance_save(inst)
        elif self.cfg.get("cold_boot"):
            os.makedirs(self._saveram_dir(inst), exist_ok=True)
        with open(stub, "w") as f:
            f.write('SLINK_HOST = "127.0.0.1"\n')
            f.write(f"SLINK_PORT = {self.tcp_port}\n")
            f.write(f'SLINK_PLAYER = "{inst}"\n')
            f.write("SLINK_DUO = {\n")
            for k, v in duo.items():
                f.write(f"  {k} = {lua_literal(v)},\n")
            f.write("}\n")
            f.write(f'dofile("{WT_FWD}/{self.gcfg["main"]}")\n')
        if self.gcfg.get("launch_profile") == "gen2" and self.scenario in GEN2_TRADE_SCENARIOS:
            plan = self._gen2_plans[inst]
            if hashlib.sha1(Path(plan["rom"]).read_bytes()).hexdigest() != plan["launch_sha1"]:
                raise RuntimeError(f"{inst}: staged trade overlay changed before launch")
        # the emulator's own output (a .NET exception text, a crash) goes to a per-run file: a modal
        # exception dialog blocks the frame-counted Lua waits, so this is the only witness it leaves
        # (the child inherits its own handle, so the parent's copy closes after the launch)
        os.makedirs(os.path.join(REPO, "patch", "build"), exist_ok=True)
        with open(os.path.join(REPO, "patch", "build", f"duo_{self.lane}_{inst}.out"), "wb") as emu_out:
            p = subprocess.Popen(
                [EMUHAWK, f"--config=patch/build/duo_cfg_{self.lane}_{inst}.ini",
                 f"--lua=patch/build/duo_{self.lane}_{inst}.lua",
                 self._rom_for(inst)],
                cwd=REPO, stdout=emu_out, stderr=subprocess.STDOUT,
                **({"env": env} if env is not None else {}))
        self.emus.append(p)
        self.emu_by_inst[inst] = p
        self._launch_times[inst] = time.time()
        print(f"[duo] launched {inst} phase={phase} seed={seed}")
        if self.cfg.get("gen3_rand"):
            self._pydec_note(f"RAND_PROCESS side={inst} phase={phase} pid={p.pid}")
        if self.cfg.get("gen3_native_trade"):
            self._pydec_note(f"T5_PROCESS side={inst} phase={phase} pid={p.pid}")
        return p

    def terminate_instance(self, inst):
        """Harness-only crash; keep the server and the other emulator running.

        Recorded as an EXPECTED exit: the waits that follow it are waiting for the relaunch, and
        a dead process with no RESULT must not end them.
        """
        self._expected_exit = getattr(self, "_expected_exit", set())
        self._expected_exit.add(inst)
        p = self.emu_by_inst[inst]
        if p.poll() is None:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            p.wait(timeout=15)
        print(f"[duo] terminated {inst} pid={p.pid}; server retained")

    def gen2_speed(self):
        """O-36: duo speed percent (default 300, the receipted speed; 0 = unthrottled). Qualification runs
        (run_gb_gate) stay at 100% and never read this."""
        speed = getattr(getattr(self, "args", None), "speed_percent", None)
        return GEN2_DEFAULT_SPEED if speed is None else speed

    @contextlib.contextmanager
    def _timed(self, phase):
        """EMU-SPEED item 1: one wall-clock line per run phase ('[duo] timing <phase> <s>s')."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            times = self.__dict__.setdefault("_phase_times", {})
            times[phase] = times.get(phase, 0.0) + elapsed
            print(f"[duo] timing {phase} {elapsed:.1f}s", flush=True)

    def start_instances(self):
        if self.scenario in GEN3_RAND_ADMISSION:
            return self._start_gen3_rand_admission()
        if self.is_gen3_battery:
            for inst in ("a", "b"):
                self._gen3_rom(inst)  # staged (and found) before either emulator starts
        elif self.battery_boot and self.gcfg.get("launch_profile") != "gen2":
            play = importlib.import_module(self.gcfg["play"])
            # Clean Gen 1 dumps are inputs only to randomization or a pureRGB UPS base;
            # ordinary vanilla scenarios boot already-built companions without those dumps.
            if (self.gcfg.get("game") != "gen1_new"
                    or self.scenario == "admit_randomized_new"
                    or is_pure_pairing(getattr(self, "game", ""))):
                for key in self.gcfg["fixture"].values():
                    play.staged_rom(key)  # space-free relative input paths for BizHawk
            for inst in ("a", "b"):
                # the cartridge each instance boots (its companion build, or the overlay staged
                # and sha1-verified here) must exist BEFORE either emulator starts: BizHawk handed
                # a missing path opens nothing and the lane would wait out its whole timeout
                rom = self._rom_for(inst)
                if not os.path.isfile(os.path.join(REPO, rom)):
                    raise FileNotFoundError(f"{inst}: {rom} missing -- build the companion with "
                                            "`python patch/gen1/tools/build.py`")
        if self.cfg.get("gen1_synth") == "explode":
            for inst in ("a", "b"):
                self._prepare_gen1_explode_fixture(inst)  # qualify/disclose both BEFORE either launch
        self._clear_attempt_artifacts()
        # F-4 tests B's CONTRACT verdict. A's randomized hello commits the run's artifact kind,
        # after which an un-randomized B is refused earlier by the mixed-kinds gate, which records
        # no admission verdict (server.py _mixed_games_error), so B must hello alone first.
        b_first = self.scenario == "admit_randomized_new"
        for inst in (("b", "a") if b_first else ("a", "b")):
            with self._timed(f"launch_{inst}"):
                self.launch_instance(inst, seed=not self.cfg.get("cold_boot"))
            if b_first and inst == "b":
                self.wait_for("un-randomized companion B's contract verdict before A launches", lambda: (
                    ((self._status() or {}).get("players") or {}).get("b", {})
                    .get("admission") == "rejected"), 120)
        print("[duo] two EmuHawk instances launched")

    def _check_bizhawk_paths(self):
        """Refuse, before any launch, a lane whose SaveRAM or result/save-copy paths BizHawk would fail to write."""
        paths = []
        for inst, plan in self._gen2_plans.items():
            result = self._result_path(inst)
            stem = result[:-len("_result.txt")] if result.endswith("_result.txt") else result
            paths += [result, stem + "_link_save.SaveRAM", stem + "_witness.SaveRAM"]
            if plan.get("directory") and plan.get("saveram_name"):   # a real run_gb_gate plan (stubs carry neither)
                paths.append(Path(plan["directory"]) / plan["saveram_name"])
        long = bizhawk_path_problem(paths)
        if long:
            raise RuntimeError(f"path too long for BizHawk ({len(long)} >= {BIZHAWK_PATH_LIMIT} chars; its saves "
                               f"fail SILENTLY there): {long} -- use a shorter checkout/lane path or lane name")

    def _prepare_gen2_lane(self):
        if not Path(EMUHAWK).is_file():
            raise FileNotFoundError(f"EmuHawk missing for Gen 2 duo: {EMUHAWK}")
        with self._timed("gen2_preflight"):
            self._gen2_artifact = gen2_selected_artifact(self.args, self.scenario)
            self._gen2_inputs = gen2_preflight(game=self.game, scenario=self.scenario, artifact_kind=self._gen2_artifact)
        from run_gb_gate import GENS

        from tests.live.test_gen2_frame_align import u1_facts
        from tests.live.test_gen2_new_gates import inspect_env
        from tools import gen2_fixtures

        suffix = "_overlay" if self._gen2_artifact == "overlay" else ""
        with self._timed("plans"):
            self._gen2_plans = {
                inst: GENS["gen2"]["plan"](row["title"] + ("" if row.get("expect_admission") == "refused" else suffix),
                                           self._saveram_dir(inst), row["fixture"],
                                           self.gen2_speed())
                for inst, row in self._gen2_inputs.items()}
        # The negative side deliberately boots clean Crystal 1.1. Resolve its
        # complete launch identity BEFORE path checks/staging can replace it.
        for inst, row in self._gen2_inputs.items():
            if row.get("expect_admission") != "refused":
                continue
            plan = self._gen2_plans[inst]
            database = Path(EMUHAWK).resolve().parent / "gamedb/gamedb_gbc.txt"
            names = [line.split("\t") for line in database.read_text(encoding="utf-8-sig").splitlines()
                     if line.split("\t", 1)[0].lower() == row["rom_sha1"]]
            if len(names) != 1 or len(names[0]) < 4 or names[0][1] != "G" or names[0][3] != "GBC":
                raise RuntimeError("refused Crystal 1.1 gamedb binding missing or contradictory")
            if hashlib.sha1(Path(row["rom"]).read_bytes()).hexdigest() != row["rom_sha1"]:
                raise RuntimeError("refused Crystal 1.1 ROM changed after preflight")
            plan.update(rom=row["rom"], rom_sha1=row["rom_sha1"], base_sha1=row["rom_sha1"],
                        launch_sha1=row["rom_sha1"], artifact="pokecrystal11", kind="clean", overlay=False,
                        stage=None, saveram_name=names[0][2] + ".SaveRAM")
            plan["env"].update(SLINK_GEN2_ROM_SHA1=row["rom_sha1"], SLINK_GEN2_BASE_SHA1=row["rom_sha1"],
                               SLINK_GEN2_EXEC_SHA1=row["rom_sha1"], SLINK_GEN2_ARTIFACT_KIND="clean",
                               SLINK_GEN2_SAVERAM_NAME=plan["saveram_name"])
            for key in ("SLINK_GEN2_OVERLAY_SHA1", "SLINK_GEN2_BINDING_SHA256"):  # defence: the clean plan has none
                plan["env"].pop(key, None)
            row.update(artifact_kind="clean", binding_sha256=None)  # the row, too, names what B boots
        self._check_bizhawk_paths()
        self._stage_gen2_roms()
        if self.scenario in GEN2_TRADE_SCENARIOS:
            with self._timed("trade_manifest"):
                self._prepare_gen2_trade_manifest()
        self._gen2_env = {}
        for inst, row in self._gen2_inputs.items():
            if row.get("expect_admission") == "refused":
                self._gen2_env[inst] = {}
                continue
            timer = self._timed(f"env_{inst}")
            timer.__enter__()
            ctx = gen2_fixtures.exec_context(row["title"], self._gen2_artifact, Path(REPO))
            # errand fixtures play on their own spec's facts (the Gold errand maps), not the title's
            facts = gen2_fixtures.spec_route_facts(gen2_fixtures.BY_NAME[row["name"]], Path(REPO), kind=self._gen2_artifact)
            env = inspect_env(gen2_fixtures.BY_NAME[row["name"]], row["fixture"].read_bytes(),
                              repo=Path(REPO), kind=self._gen2_artifact)
            if self.scenario == "gen2_ball_gate":
                # the town fixture plays the errand: errand route facts + ledges, qualify facts over the same
                # fingerprint, and the case flag the shared gate admits a town case with errand facts on
                facts = gen2_fixtures.route_facts(row["title"], Path(REPO), errand=True, kind=self._gen2_artifact)
                qualify = json.loads(env["SLINK_GEN2_QUALIFY"])
                qualify["facts"] = gen2_fixtures.qualify_facts(row["title"], Path(REPO), errand=True, kind=self._gen2_artifact)
                env.update(SLINK_GEN2_ROUTE_FACTS=json.dumps(facts), SLINK_GEN2_QUALIFY=json.dumps(qualify),
                           SLINK_GEN2_ROUTE_LEDGES=json.dumps(gen2_fixtures.route_ledges(row["title"], facts,
                                                                                        Path(REPO))))
            case = json.loads(env["SLINK_GEN2_FIXTURE_CASE"])
            case["attempt_id"] = f"duo-{self.scenario}-{inst}-{self.attempt}-{uuid.uuid4().hex}"
            if self.scenario == "gen2_ball_gate":
                case["ball_gate"] = True
            if row.get("synth"):
                case["synth"] = row["synth"]   # the shared gate names the case by its base fixture
            env["SLINK_GEN2_FIXTURE_CASE"] = json.dumps(case)
            env["SLINK_GEN2_U1_FACTS"] = json.dumps(u1_facts(ctx, facts, row["qualification_attempt_id"]))
            kind = GEN2_SYNTH_SCENARIOS.get(self.scenario)
            if kind in ("bill", "trade", "evolve"):   # the U1G route driver (lua/tests/gen2_u1g_inputs.lua), its facts
                from tests.live.test_gen2_u1g import run_facts

                u1g = run_facts(ctx, gen2_fixtures.BY_NAME[row["name"]],
                                {"trade": "kyle", "evolve": "grass"}.get(kind, kind),
                                row["synth"], row["qualification_attempt_id"])
                if kind == "trade":
                    u1g["u1g"]["give_slot"] = 1   # the hatched Bellsprout is slot 2 (DUO_RECIPES trade)
                env["SLINK_GEN2_U1_FACTS"] = json.dumps(u1g)
            if self.scenario in GEN2_TRADE_SCENARIOS:
                from tools.gen2_trade_facts import trade_facts

                env["SLINK_GEN2_TRADE_MANIFEST"] = str(self._gen2_trade_manifest_path)
                env["SLINK_GEN2_TRADE_MANIFEST_SHA256"] = self._gen2_trade_manifest_sha256
                env["SLINK_GEN2_TRADE_FACTS"] = json.dumps(trade_facts(row["title"], root=Path(REPO)))
            self._gen2_env[inst] = env
            timer.__exit__(None, None, None)
        scenario_name = self.scenario.removeprefix("gen2_")
        driver_files = [self.gcfg["main"], f"lua/tests/duo/scenario_gen2_{scenario_name}.lua",
                        "lua/tests/duo/gen2_route29_inputs.lua"]
        if self.game == "gen2_gold_silver" and self.scenario in ("gen2_poison", "gen2_species_clause"):
            driver_files.append("lua/tests/duo/gen2_gs_setup.lua")
        if self.scenario in GEN2_TRADE_SCENARIOS:
            driver_files += ["lua/tests/duo/gen2_trade.lua", "tools/gen2_trade_facts.py"]
        if self.scenario in GEN2_WAVE_C:
            driver_files.append("lua/tests/duo/scenario_gen2_faint.lua")
        if self.scenario in ("gen2_faint", "gen2_faint_active", "gen2_whiteout", "gen2_changebox", "gen2_poison",
                             "gen2_whiteout_rebuild", "gen2_faint_active_trainer"):
            driver_files.append("lua/tests/duo/gen2_faint_inputs.lua")
        if self.scenario == "gen2_faint_active_trainer":
            driver_files += ["lua/tests/duo/scenario_gen2_faint_active.lua", "lua/tests/gen2_poison_inputs.lua",
                             "lua/tests/gen2_walk.lua"]
        if self.scenario in ("gen2_pc_ops", "gen2_changebox", "gen2_whiteout_rebuild"):
            driver_files.append("lua/tests/gen2_pc_inputs.lua")
        if self.scenario in ("gen2_pc_ops", "gen2_changebox", "gen2_poison", "gen2_whiteout_rebuild"):
            driver_files += ["lua/tests/gen2_poison_inputs.lua", "lua/tests/gen2_walk.lua"]
        if self.scenario in GEN2_CLAUSE_SCENARIOS:
            driver_files.append("lua/tests/duo/gen2_clause.lua")
        if self.scenario in GEN2_SYNTH_SCENARIOS:
            driver_files += ["lua/tests/duo/gen2_synth_duo.lua", "lua/tests/gen2_u1g_inputs.lua",
                             "lua/tests/gen2_poison_inputs.lua", "lua/tests/gen2_walk.lua",
                             "lua/tests/duo/gen2_faint_inputs.lua"]
        if self.scenario == "gen2_ball_gate":
            driver_files += ["lua/tests/duo/gen2_ball_gate_inputs.lua", "lua/tests/gen2_scripted_play.lua",
                             "lua/tests/gen2_walk.lua"]
        for path in driver_files:
            if not (Path(REPO) / path).is_file():
                raise FileNotFoundError(f"Gen 2 duo driver missing: {path}")
        oracle = importlib.import_module("gen2_duo_oracles")
        oracle_name = {"link": "link_oracle", "gen2_faint": "faint_oracle", "gen2_faint_active": "faint_active_oracle",
                       "gen2_faint_active_trainer": "faint_active_oracle", "gen2_reconnect": "reconnect_oracle",
                       "gen2_admit_wrong_rom": "admit_wrong_rom_oracle", "gen2_soft_reset": "soft_reset_oracle",
                       "gen2_ball_gate": "ball_gate_oracle",
                       **dict.fromkeys(GEN2_C5_REFUSED, "c5_refused_oracle"),
                       **dict.fromkeys(GEN2_SYNTH_SCENARIOS, "synth_duo_oracle"),
                       **{name: row[0] for name, row in GEN2_WAVE_C.items()},
                       **dict.fromkeys(GEN2_CLAUSE_SCENARIOS, "clause_oracle"),
                       **dict.fromkeys(GEN2_TRADE_SCENARIOS, "trade_oracle")}[self.scenario]
        witness = "check_admit_wrong_rom_witness" if self.scenario == "gen2_admit_wrong_rom" else "check_save_witness"
        if self.scenario == "gen2_reconnect":
            witness = "check_reconnect_witness"
        if self.scenario in GEN2_TRADE_SCENARIOS:
            oracle = importlib.import_module("gen2_trade_oracles")
            witness = "check_trade_witness"
        if not all(callable(getattr(oracle, name, None)) for name in (witness, oracle_name)):
            raise RuntimeError("Gen 2 duo witness/oracle implementation missing")

    def _stage_gen2_roms(self):
        """Materialize every overlay before launch, not only native trade's cartridges."""
        for side, plan in self._gen2_plans.items():
            if self._gen2_inputs[side].get("expect_admission") == "refused":
                continue
            stage = plan.get("stage")
            if stage is None:
                continue
            if hashlib.sha1(stage).hexdigest() != plan["launch_sha1"]:
                raise RuntimeError("staged Gen 2 overlay hash differs before launch")
            target = Path(plan["rom"]).resolve()
            if not target.is_relative_to(Path(BUILD).resolve()):
                raise RuntimeError("Gen 2 staged ROM escapes build directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or hashlib.sha1(target.read_bytes()).hexdigest() != plan["launch_sha1"]:
                with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(stage)
                try:
                    os.replace(temporary, target)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            self._gen2_inputs[side].update(rom=target, rom_sha1=plan["launch_sha1"])

    def _prepare_gen2_trade_manifest(self):
        from tools.gen2_trade_lane import validate_manifest

        root = Path(REPO).resolve()
        raw = (root / "data/gen2/overlay_provenance.json").read_bytes()
        provenance = json.loads(raw)
        manifest = {"schema": "gen2-trade-lane-v1", "run_id": "g2trade_" + uuid.uuid4().hex,
                    "scenario": self.scenario, "evidence_class": "PHYSICAL_RECEIPTED",
                    "provenance_sha256": hashlib.sha256(raw).hexdigest(), "players": {}}
        for inst, row in self._gen2_inputs.items():
            title, plan = row["title"], self._gen2_plans[inst]
            artifact = provenance["outputs"]["poke" + title]
            stage = plan.get("stage")
            if (not isinstance(stage, bytes) or hashlib.sha1(stage).hexdigest() != artifact["sha1"]
                    or plan["launch_sha1"] != artifact["sha1"] or row["source_rom_sha1"] != artifact["base_sha1"]):
                raise RuntimeError(f"{inst}: trade overlay stage/base binding differs")
            manifest["players"][inst] = {"title": title, "rom_type": title, "foundation": "gen2_gsc",
                "artifact_kind": "overlay", "rom_sha1": artifact["sha1"], "base_sha1": artifact["base_sha1"],
                "ups_sha256": artifact["ups"]["sha256"], "sym_sha256": provenance["symbols"][title + "_slink.sym"]}
        validate_manifest(manifest, root=root)
        # gen2_trade_lane refuses a server --run-id other than the manifest's: the trade server IS this
        # run, so server_cmd must name the same id (it used to mint its own duo-<uuid>; every trade cell failed)
        self._server_run_id = manifest["run_id"]
        for inst, plan in self._gen2_plans.items():
            target = Path(plan["rom"]).resolve()
            if not target.is_relative_to(Path(BUILD).resolve()):
                raise RuntimeError("trade staged ROM escapes the build directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or hashlib.sha1(target.read_bytes()).hexdigest() != plan["launch_sha1"]:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(plan["stage"])
                    os.replace(temporary, target)
                finally:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()
            self._gen2_inputs[inst].update(source_rom_sha1=self._gen2_inputs[inst]["source_rom_sha1"],
                                          rom_sha1=plan["launch_sha1"], rom=target)
        path = Path(self.data_dir) / "gen2_trade_manifest.json"
        encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        with path.open("xb") as handle:
            handle.write(encoded)
        self._gen2_trade_manifest_path = path
        self._gen2_trade_manifest_sha256 = hashlib.sha256(encoded).hexdigest()
        self._gen2_trade_manifest = manifest
        Path(self.data_dir, "trade_evidence").mkdir(exist_ok=False)

    def _gen2_trade_overlay_reference(self):
        return {"manifest_path": str(self._gen2_trade_manifest_path),
                "manifest_sha256": self._gen2_trade_manifest_sha256,
                "players": {side: {"rom_path": str(self._gen2_plans[side]["rom"]),
                                   "sym_path": str(Path(REPO, "data/gen2", row["title"] + "_slink.sym").resolve())}
                            for side, row in self._gen2_inputs.items()}}

    def _gen2_trade_expected_case(self):
        case = {"scenario": self.scenario,
                "variant": {"gen2_new": "cc", "gen2_gold_silver": "gs", "gen2_crystal_gold": "cg"}[self.game],
                "fixture_sha256": {side: row["sha256"] for side, row in self._gen2_inputs.items()},
                "required_phases": ["wait", "trade_animation", "native_save"]
                    if self.scenario in ("gen2_trade_new", "gen2_trade_evolve") else ["wait"]}
        synth = {side: {"name": row["synth"], "base": row["name"]}
                 for side, row in self._gen2_inputs.items() if row.get("synth")}
        if synth:   # O-33: the oracle binds the seed's disclosure (gen2_trade_oracles.SEEDED)
            case["synth"] = synth
        return case

    def _release_gen2_trade(self):
        oracle = importlib.import_module("gen2_trade_oracles")

        def ready():
            found = {}
            for side in ("a", "b"):
                text = read_result(self.artifact_name, side) or ""
                if not text.endswith("\n"):
                    text = text.rsplit("\n", 1)[0] if "\n" in text else ""
                if not any(line.startswith("TRADE_READY ") for line in text.splitlines()):
                    return None
                mark, baseline = oracle._one(text, "TRADE_READY"), oracle._one(text, "TRADE_BASELINE")
                raw = oracle._image(baseline, side + " runner baseline")
                if mark.get("snapshot_sha256") != baseline["snapshot_sha256"]:
                    raise RuntimeError("trade READY does not bind its baseline")
                found[side] = raw
            return found

        with self._timed("trade_ready"):   # launch -> both TRADE_READY (catch, walk, baseline)
            images = self.wait_for("both immutable trade baselines", ready, self.cfg["timeout"])
        self._gen2_trade_baseline_saves = {}
        for side, raw in images.items():
            path = Path(self.data_dir, "trade_evidence", side + "_runner_baseline.SaveRAM")
            with path.open("xb") as handle:
                handle.write(raw)
            self._gen2_trade_baseline_saves[side] = path
        raw = Path(self.data_dir, "links.json").read_bytes()
        path = Path(self.data_dir, "trade_evidence", "baseline_links.json")
        with path.open("xb") as handle:
            handle.write(raw)
        self._gen2_trade_baseline_links = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
        self.go({side: ["TRADE_GO"] for side in ("a", "b")})

    def assert_gen2_trade_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_trade_oracles")
        source = Path(self.data_dir, "trade_lane_events.jsonl")
        raw = source.read_bytes()
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        tokens = set()
        for event in events:
            message, outcome = event.get("message", {}), event.get("outcome", {})
            for candidate in (message.get("token"), (outcome.get("pending_trade") or {}).get("token")):
                if isinstance(candidate, str) and re.fullmatch(r"t[0-9]+", candidate):
                    tokens.add(candidate)
        if len(tokens) > 1:
            raise RuntimeError("trade lane observed multiple server transactions")
        path = Path(self.data_dir, "trade_evidence", "server_events.jsonl")
        with path.open("xb") as handle:
            handle.write(raw)
        transaction = {"schema": "gen2-duo-trade-transaction-v1",
                       "tokens": {side: oracle._one(results[side], "RECEIPT").get("token") for side in ("a", "b")},
                       "server_token": next(iter(tokens), None), "baseline_links": self._gen2_trade_baseline_links,
                       "events": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}}
        if self.scenario == "gen2_trade_reset_commit":
            status = self._status()
            if not isinstance(status, dict):
                raise RuntimeError("trade reconciliation final status unavailable")
            evidence = {"status": (json.dumps(status, sort_keys=True) + "\n").encode(),
                        "events": Path(self.data_dir, "events.json").read_bytes()}
            transaction["reconciliation"] = {}
            for label, content in evidence.items():
                snapshot = Path(self.data_dir, "trade_evidence", label + ".json")
                with snapshot.open("xb") as handle:
                    handle.write(content)
                transaction["reconciliation"][label] = {"path": str(snapshot), "sha256": hashlib.sha256(content).hexdigest()}

        def verified(facts):
            if facts.get("scenario") != self.scenario or facts.get("admission_scope") != "PHYSICAL_RECEIPTED":
                raise RuntimeError("trade oracle omitted its production scope")
            self._record_gen2_facts({"a": facts["players"]["a"]["key"], "b": facts["players"]["b"]["key"],
                "area": facts["area_id"], "titles": "/".join(facts["players"][side]["title"] for side in ("a", "b")),
                "status": facts["status"], "scenario": self.scenario, "admission_scope": facts["admission_scope"]})

        return oracle.trade_oracle(results, data_dir=self.data_dir,
            baseline_saves=self._gen2_trade_baseline_saves, transaction_evidence=transaction,
            overlay_provenance=self._gen2_trade_overlay_reference(), expected_case=self._gen2_trade_expected_case(),
            on_verified=verified, **kwargs)

    def check_gen2_save_witness(self, results):
        if hasattr(self, "_gen2_artifact"):
            for side, text in results.items():
                if side not in ("a", "b") or self._gen2_inputs[side].get("expect_admission") == "refused":
                    continue
                if self.scenario == "gen2_reconnect" and side == "a":
                    text = self._gen2_initial_a
                lines = [line.split(" ", 1)[1] for line in text.splitlines() if line.startswith("CLIENT ")]
                if len(lines) != 1:
                    raise RuntimeError("Gen 2 production artifact CLIENT marker missing or ambiguous")
                client = json.loads(lines[0])
                if (client.get("artifact_kind", "clean") != self._gen2_artifact
                        or client.get("rom_sha1") != self._gen2_inputs[side]["rom_sha1"]):
                    raise RuntimeError("Gen 2 receipt belongs to another executed artifact")
        if self.scenario in GEN2_TRADE_SCENARIOS:
            return importlib.import_module("gen2_trade_oracles").check_trade_witness(results,
                expected_case=self._gen2_trade_expected_case(), overlay_provenance=self._gen2_trade_overlay_reference())
        oracle = importlib.import_module("gen2_duo_oracles")
        if self.scenario == "gen2_admit_wrong_rom":
            return oracle.check_admit_wrong_rom_witness(results, **self._gen2_admit_paths())
        if self.scenario == "gen2_reconnect":
            return oracle.check_reconnect_witness(results,
                initial_results={"a": self._gen2_initial_a, "b": results["b"]},
                relaunch_results=self._gen2_relaunch_results, staged_saves=self._gen2_staged_saves)
        return oracle.check_save_witness(results)

    def _gen2_admit_paths(self):
        plan = self._gen2_plans["b"]
        return {"boot_saveram": {inst: row["fixture"] for inst, row in self._gen2_inputs.items()},
                "refused_saveram": Path(plan["directory"]) / plan["saveram_name"]}

    def _gen2_admit_snapshot(self):
        raw = Path(self.data_dir, "links.json").read_bytes()
        return {"status": self._status(), "raw": self._raw_state(),
                "links": json.loads(raw), "links_bytes": raw,
                "events": self._reconnect_events()}

    def assert_gen2_admit_wrong_rom(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.admit_wrong_rom_oracle(results, data_dir=self.data_dir,
            before=self._gen2_admit_before, after=self._gen2_admit_snapshot(),
            on_verified=self._record_gen2_facts, **self._gen2_admit_paths(), **kwargs)

    def assert_gen2_c5_refused_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.c5_refused_oracle(results, data_dir=self.data_dir, status=self._status(),
            events=self._reconnect_events(), expect=dict(GEN2_C5_REFUSED[self.scenario]),
            on_verified=self._record_gen2_facts, on_reason=self._pydec_note, **kwargs)

    def assert_gen2_reconnect_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.reconnect_oracle(results, data_dir=self.data_dir,
            initial_results={"a": self._gen2_initial_a, "b": results["b"]},
            relaunch_results=self._gen2_relaunch_results, staged_saves=self._gen2_staged_saves,
            relaunch_saves=self._gen2_relaunch_saves, snapshots=self._gen2_reconnect_snapshots,
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()},
            on_verified=self._record_gen2_facts, **kwargs)

    def assert_gen2_clause_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.clause_oracle(results, data_dir=self.data_dir,
            pending_snapshot=getattr(self, "_gen2_clause_pending", None),
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()},
            on_verified=self._record_gen2_facts, **kwargs)

    def _release_gen2_species(self):
        def pending_marker():
            rows = [json.loads(line.partition(" ")[2]) for line in self._read_receipt("a").splitlines()
                    if line.startswith("PENDING_CAPTURE ")]
            if len(rows) > 1:
                raise RuntimeError("multiple A PENDING_CAPTURE markers")
            return rows[0] if rows else None
        marker = self.wait_for("Gen 2 A pending marker", pending_marker, self.cfg["timeout"])
        if marker.get("area_id") != "route_29" or not marker.get("key") or type(marker.get("species_id")) is not int:
            raise RuntimeError("invalid Gen 2 pending key/area/species")
        def server_pending():
            entry = ((self._status() or {}).get("pending_captures", {}).get("route_29", {}).get("a"))
            return entry if entry and entry.get("key") == marker["key"] else None
        entry = self.wait_for("Gen 2 server A pending capture", server_pending, self.cfg["timeout"])
        if entry.get("species") != marker["species_id"]:
            raise RuntimeError("Gen 2 server pending species differs from A")
        self._gen2_clause_pending = self._gen2_admit_snapshot()
        self._go_one("b", [f"A_PENDING species={marker['species_id']}"])

    def assert_gen2_soft_reset_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.soft_reset_oracle(results, data_dir=self.data_dir,
            before=self._gen2_soft_reset_before, after=self._gen2_soft_reset_after,
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()},
            on_verified=self._record_gen2_facts, **kwargs)

    def _orchestrate_gen2_soft_reset(self):
        self.wait_for("Gen 2 A ready for reset chord", lambda: any(line.startswith("HELLO_AT_CHECKPOINT ")
                      for line in self._read_receipt("a").splitlines()), 60)
        self._gen2_soft_reset_before = self._gen2_admit_snapshot()
        Path(self.go_files["a"] + ".chord").write_text("baseline recorded\n", encoding="utf-8")
        def rehello():
            if not any(line.startswith("REHELLO ") for line in self._read_receipt("a").splitlines()):
                return False
            return sum(row.get("type") == "hello" and row.get("player") == "a"
                       and row.get("text", "").startswith("Connected (")
                       for row in self._reconnect_events()) == 2
        self.wait_for("Gen 2 same-OT rehello accepted after reset", rehello, 300)
        self._gen2_soft_reset_after = self._gen2_admit_snapshot()
        self._live_complete["gen2_soft_reset"] = True

    def _stage_gen2_reconnect(self, phase, source, key):
        from run_gb_gate import GENS

        if phase not in ("same_save", "wrong_save"):
            raise RuntimeError("invalid Gen 2 reconnect phase")
        raw = Path(source).read_bytes()
        if phase == "wrong_save" and hashlib.sha256(raw).hexdigest() != self._gen2_inputs["a"]["wrong_sha256"]:
            raise RuntimeError("qualified wrong-save fixture changed after preflight")
        seed = Path(BUILD, f"e2e_{self.artifact_name}_a_{phase}_seed.SaveRAM")
        seed.write_bytes(raw)
        directory = Path(self._saveram_dir("a") + "_" + phase)
        plan = GENS["gen2"]["plan"](self._gen2_inputs["a"]["title"] + ("_overlay" if self._gen2_artifact == "overlay" else ""), directory, seed, self.gen2_speed())
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / plan["saveram_name"]
        target.write_bytes(raw)
        self._gen2_plans["a"] = plan
        env = dict(self._gen2_env["a"])
        case = json.loads(env["SLINK_GEN2_FIXTURE_CASE"])
        case["attempt_id"] = f"duo-reconnect-a-{phase}-{uuid.uuid4().hex}"
        qualify = json.loads(env["SLINK_GEN2_QUALIFY"])
        qualify.update(stage="boot", stage_fingerprint=hashlib.sha256(raw).hexdigest())
        env.update(SLINK_GEN2_FIXTURE_CASE=json.dumps(case), SLINK_GEN2_QUALIFY=json.dumps(qualify))
        self._gen2_env["a"] = env
        self._gen2_staged_saves[phase], self._gen2_relaunch_saves[phase] = seed, target
        Path(self.go_files["a"]).write_text("", encoding="utf-8")
        self.launch_instance("a", phase=phase, seed=False, expected_key=key)
        self._expected_exit.discard("a")

    def _orchestrate_gen2_reconnect(self):
        def marker(side, tag):
            text = self._read_receipt(side)
            rows = [json.loads(line[len(tag) + 1:]) for line in text.splitlines() if line.startswith(tag + " ")]
            return rows[0] if len(rows) == 1 else None

        ready = {inst: self.wait_for(f"{inst} reconnect link save", lambda i=inst: marker(i, "RECONNECT_READY"), 600)
                 for inst in ("a", "b")}
        self.wait_for("Gen 2 persisted live pair before crash", lambda: any(
            row.get("status") == "alive" and row.get("area_id") == "route_29"
            and all((row.get(inst) or {}).get("key") == ready[inst]["key"] for inst in ("a", "b"))
            for row in self._links_json()), 60)
        self._gen2_initial_a = self._read_receipt("a")
        Path(BUILD, f"e2e_{self.artifact_name}_a_initial_result.txt").write_text(self._gen2_initial_a, encoding="utf-8")
        initial_save = Path(self._gen2_plans["a"]["directory"]) / self._gen2_plans["a"]["saveram_name"]
        self._gen2_reconnect_snapshots = {"initial": self._gen2_admit_snapshot()}
        self._gen2_staged_saves, self._gen2_relaunch_saves, self._gen2_relaunch_results = {}, {}, {}
        self.terminate_instance("a")
        def disconnected():
            players = (self._status() or {}).get("players", {})
            return not players.get("a", {}).get("connected") and players.get("b", {}).get("connected")
        self.wait_for("Gen 2 A disconnected, B online", disconnected, 45)
        self._gen2_reconnect_snapshots["disconnected"] = self._gen2_admit_snapshot()
        for phase, source in (("same_save", initial_save), ("wrong_save", self._gen2_inputs["a"]["wrong_fixture"])):
            if phase == "wrong_save":
                self._gen2_reconnect_snapshots["before_wrong"] = self._gen2_admit_snapshot()
            self._stage_gen2_reconnect(phase, source, ready["a"]["key"])
            self.wait_for(f"Gen 2 {phase} hello", lambda: marker("a", "RECONNECT_HELLO"), 300)
            def reconciled(phase=phase):
                players = (self._status() or {}).get("players", {})
                a = players.get("a", {})
                if not a.get("connected") or not players.get("b", {}).get("connected"):
                    return False
                if phase == "wrong_save":
                    return a.get("identity_error") and marker("a", "WRONG_SAVE_HUD")
                return not a.get("identity_error") and ready["a"]["key"] in a.get("party_keys", [])
            self.wait_for(f"Gen 2 {phase} server reconciliation", reconciled, 60)
            self._gen2_reconnect_snapshots[phase] = self._gen2_admit_snapshot()
            self._append_reconnect_marker("a", "A_DONE_SAME" if phase == "same_save" else "A_DONE_WRONG")
            self.wait_for(f"Gen 2 {phase} PASS", lambda: (terminal_result(self._read_receipt("a")) or "").startswith("RESULT: PASS"), 60)
            self._gen2_relaunch_results[phase] = self._read_receipt("a")
            self.emu_by_inst["a"].wait(timeout=30)
            self.terminate_instance("a")
            self.wait_for(f"Gen 2 {phase} socket closed", disconnected, 45)
        Path(self._result_path("a")).write_text(self._gen2_relaunch_results["wrong_save"], encoding="utf-8")
        self._append_reconnect_marker("b", "B_DONE")
        self.wait_for("Gen 2 B stayed online", lambda: (terminal_result(self._read_receipt("b")) or "").startswith("RESULT: PASS"), 120)
        self._live_complete["gen2_reconnect"] = True

    def assert_gen2_link_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.link_oracle(results, data_dir=self.data_dir,
            on_verified=self._record_gen2_facts,
            ot_ids={inst: row["ot_id"] for inst, row in self._gen2_inputs.items()},
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()}, **kwargs)

    def assert_gen2_faint_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.faint_oracle(results, data_dir=self.data_dir,
            on_verified=self._record_gen2_facts,
            ot_ids={inst: row["ot_id"] for inst, row in self._gen2_inputs.items()},
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()}, **kwargs)

    def assert_gen2_synth_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.synth_duo_oracle(results, scenario=self.scenario, data_dir=self.data_dir,
            on_verified=self._record_gen2_facts,
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()}, **kwargs)

    def assert_gen2_ball_gate_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.ball_gate_oracle(results, data_dir=self.data_dir, on_verified=self._record_gen2_facts,
            ot_ids={inst: row["ot_id"] for inst, row in self._gen2_inputs.items()},
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()}, **kwargs)

    def _assert_gen2_wave_c(self, results, **kwargs):
        oracle = getattr(importlib.import_module("gen2_duo_oracles"), GEN2_WAVE_C[self.scenario][0])
        return oracle(results, data_dir=self.data_dir, on_verified=self._record_gen2_facts,
            ot_ids={inst: row["ot_id"] for inst, row in self._gen2_inputs.items()},
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()}, **kwargs)

    assert_gen2_whiteout_saved = assert_gen2_pc_ops_saved = _assert_gen2_wave_c
    assert_gen2_changebox_saved = assert_gen2_poison_saved = assert_gen2_whiteout_rebuild_saved = _assert_gen2_wave_c

    def assert_gen2_faint_active_saved(self, results, **kwargs):
        oracle = importlib.import_module("gen2_duo_oracles")
        return oracle.faint_active_oracle(results, data_dir=self.data_dir,
            on_verified=self._record_gen2_facts,
            ot_ids={inst: row["ot_id"] for inst, row in self._gen2_inputs.items()},
            boot_saveram={inst: row["fixture"] for inst, row in self._gen2_inputs.items()}, **kwargs)

    def assert_gen2_faint_active_trainer_saved(self, results, **kwargs):
        return self.assert_gen2_faint_active_saved(results, trainer=True, **kwargs)

    def _release_gen2_active_faint(self):
        def active():
            text = read_result(self.artifact_name, "b") or ""
            rows = [line[len("LINKED_ACTIVE "):] for line in text.splitlines()
                    if line.startswith("LINKED_ACTIVE ")]
            if len(rows) > 1:
                raise RuntimeError("B emitted more than one LINKED_ACTIVE")
            if not rows:
                return None
            try:
                row = json.loads(rows[0])
            except json.JSONDecodeError as exc:
                if not text.endswith("\n"):
                    return None
                raise RuntimeError("B LINKED_ACTIVE is malformed") from exc
            if (not isinstance(row, dict) or type(row.get("slot")) is not int or not 0 <= row["slot"] < 6
                    or row.get("cur_battle_mon") != row["slot"]
                    or row.get("battle_mode") != (2 if self.scenario == "gen2_faint_active_trainer" else 1)
                    or row.get("link_mode") != 0 or not isinstance(row.get("key"), str) or not row["key"]):
                raise RuntimeError("B LINKED_ACTIVE does not describe an active linked battler")
            return row
        self._gen2_active_release = self.wait_for("B linked catch active in battle", active, self.cfg["timeout"])
        self._go_one("a", ["B_ACTIVE"])

    def _record_gen2_facts(self, facts):
        fields = ("a", "b", "area", "titles", "status")
        if not isinstance(facts, dict) or any(not isinstance(facts.get(key), str)
                                             or not facts[key] for key in fields):
            raise RuntimeError("Gen 2 oracle verified facts missing")
        allowed = ("refused",) if self.scenario == "gen2_admit_wrong_rom" else ("alive", "dead", "memorial")
        if self.scenario == "gen2_soft_reset":
            allowed = ("unchanged",)
        if self.scenario in ("gen2_type_clause", "gen2_gender_clause"):
            allowed = ("clause_observed",)
        if self.scenario == "gen2_species_clause":
            allowed = ("alive",)
        if self.scenario in GEN2_TRADE_SCENARIOS:
            allowed = ("committed", "unchanged")
        if self.scenario in GEN2_C5_REFUSED:
            allowed = ("refused",)
        if facts["status"] not in allowed:
            raise RuntimeError("Gen 2 oracle verified status invalid")
        self._gen2_verified_facts = dict(facts)

    def _mon_stats_keys(self):
        """The persisted `mon_stats` keys, or [] when the document is absent/unreadable."""
        try:
            with open(os.path.join(self.data_dir, "links.json"), encoding="utf-8") as handle:
                return sorted(_flat_mon_stats(json.load(handle).get("mon_stats")).keys())
        except (OSError, ValueError):
            return []

    def _clear_attempt_artifacts(self):
        """Drop every receipt, go-file and witness this scenario could read as its own.

        Freshness is identity here: a rerun (or a crashed attempt re-entered) leaves results,
        phase receipts, attempt archives, witnesses and PYDEC copies from OLDER runs in the same
        build directory. Gen 1/2/3 attempt 1 clears those; later retries retain numbered archives
        from this invocation, while clearing all live paths. Gen 1's immutable copies live outside
        this prefix. The current PYDEC file is opened before launch and retained.
        """
        keep = os.path.basename(getattr(self, "_pydec_path", "") or "")
        removed = []
        for path in sorted(glob.glob(os.path.join(BUILD, f"e2e_{self.artifact_name}_*"))):
            if keep and os.path.basename(path) == keep:
                continue
            archive = re.search(r"_attempt(\d+)_(?:result\.txt|witness\.SaveRAM|exit\.SaveRAM|manifest\.json)$", os.path.basename(path))
            if (scenario_family(getattr(self, "game", "legacy")) in ("gen1_new", "gen2_new", "gen3_frlg", "gen3_emerald", "gen3_rr")
                    and archive and 0 < int(archive[1]) < getattr(self, "attempt", 1)):
                continue  # Earlier attempts of THIS invocation remain replayable; attempt 1 clears all.
            os.remove(path)
            removed.append(os.path.basename(path))
        for inst in ("a", "b"):
            for path in (self.go_files[inst], self.go_files[inst] + ".chord"):
                if os.path.exists(path):
                    os.remove(path)
                    removed.append(os.path.basename(path))
        if removed:
            print(f"[duo] cleared {len(removed)} stale artifact(s) for {self.scenario}: "
                  f"{', '.join(sorted(removed))}")

    def wait_keys(self):
        """Both wrappers log MYKEY lines right after savestate+mutation."""
        def both():
            ka = extract_keys(self._read_receipt("a"))
            kb = extract_keys(self._read_receipt("b"))
            return (ka, kb) if ka and kb else None
        ka, kb = self.wait_for("MYKEY lines from both instances", both, 120)
        if set(ka.values()) & set(kb.values()):
            raise RuntimeError(f"key collision between instances: {ka} vs {kb}")
        print(f"[duo] keys: a={ka.get(0)} b={kb.get(0)}")
        return ka, kb

    def wait_connected(self):
        last_status = None
        def both():
            nonlocal last_status
            st = self._status()
            last_status = st
            if not st:
                return None
            players = st.get("players", {})
            return (players.get("a", {}).get("connected")
                    and players.get("b", {}).get("connected")) or None
        try:
            self.wait_for("both players hello'd", both, 120)
        except TimeoutError:
            players = (last_status or {}).get("players", {})
            diagnostic = {"players": {inst: {
                "connected": players.get(inst, {}).get("connected", "unavailable"),
                "last_event": players.get(inst, {}).get("last_event", "unavailable"),
            } for inst in ("a", "b")}}
            print("[duo] CONNECT_TIMEOUT " + json.dumps(diagnostic, sort_keys=True))
            raise
        print("[duo] both players connected")
        self._require_production_route_receipt()

    def inject_link(self, a_key, b_key, area_id="duo"):
        def linked():
            try:
                r = api(self.http_port, "POST", "/api/inject_link",
                        {"a_key": a_key, "b_key": b_key, "area_id": area_id, "force": True})
                return r if r.get("ok") else None
            except Exception:
                return None
        self.wait_for("inject_link", linked, 60)
        print(f"[duo] linked {a_key} <-> {b_key}")

    def assert_real_link_formed(self):
        """The whole point of the playthrough scenario.

        Both instances catch a wild mon through actual play; the server must pair those two
        captures BY AREA on its own. Nothing here injects anything — if encounter linking is
        broken, no link appears and this raises. This is the only assertion in the suite that
        covers the rule SLink exists for.
        """
        def caught(inst):
            txt = read_result(self.scenario, inst) or ""
            for line in txt.splitlines():
                if "CAUGHT " in line:
                    return line.split("CAUGHT ", 1)[1].split()[0]
            return None

        def both_caught():
            a, b = caught("a"), caught("b")
            return (a, b) if a and b else None

        a_key, b_key = self.wait_for("both instances to catch a wild mon", both_caught,
                                self.cfg["timeout"])
        print(f"[duo] real captures: a={a_key} b={b_key}")

        def linked():
            st = self._status() or {}
            for link in (st.get("links") or []):
                keys = {link.get("a_key"), link.get("b_key")}
                if keys == {a_key, b_key}:
                    return link
            return None

        link = self.wait_for("the SERVER to pair the two real captures", linked, 180)
        area = link.get("area_id")
        print(f"[duo] ENCOUNTER LINK FORMED FROM REAL PLAY: "
              f"{a_key} <-> {b_key} in area={area}")
        if area in (None, "", "duo"):
            raise RuntimeError(f"link formed but area_id is {area!r} — expected a real "
                               f"encounter area resolved from the map, not a harness value")

    def go(self, lines_by_inst=None):
        """Write the per-instance go-files; lines_by_inst = {"a": [...], "b": [...]} or None."""
        for inst in ("a", "b"):
            self._go_one(inst, (lines_by_inst or {}).get(inst, []))

    def _go_one(self, inst, lines=()):
        """Release ONE instance. Scenarios where B must provably act after A do this rather
        than starting both and hoping the ordering holds."""
        with open(self.go_files[inst], "w") as f:
            for line in lines:
                f.write(line + "\n")
            f.write("GO\n")
        print(f"[duo] go-file written for {inst}")

    # ── result-file readers ──────────────────────────────────────────────────
    def _marks(self, inst, tag):
        return extract_marks(read_result(self.scenario, inst) or "", tag)

    def _caught(self, inst):
        """The key of a mon this instance caught, if it managed to name one.

        TWO SOURCES, BECAUSE THE FIRST IS A RACE THE SCENARIO ALREADY GAVE UP ON.
        `CAUGHT` is printed by the hunt only if it can still read the mon in the party --
        and inside a dead zone the server force-faints and memorialises the capture so
        quickly that it very often cannot. The (now deleted) old-client deadzone driver said so
        words ("The return value is a bonus, not a requirement") and asserts on the ball
        count and the memorial instead. This poller was left demanding the line the Lua
        had stopped promising, so a run where the server won the race timed out after
        1500s with both halves of the scenario reporting PASS.

        `REFUSED <key> (<how>)` is the line the scenario does promise, and it carries the
        full key whenever one was recovered at all.
        """
        text = self._read_receipt(inst)
        for line in text.splitlines():
            if "CAUGHT " in line:
                return line.split("CAUGHT ", 1)[1].split()[0]
        for line in text.splitlines():
            if "REFUSED " in line:
                key = line.split("REFUSED ", 1)[1].split()[0]
                # "<retired before we could read it>" means no key was ever recovered.
                if key.startswith("<"):
                    return None
                return key
        return None

    def _area(self, inst):
        marks = self._marks(inst, "AREA")
        return marks[0] if marks else None

    def _dead_zone_area(self):
        for area, st in ((self._status() or {}).get("area_states") or {}).items():
            if st == "dead_zone":
                return area
        return None

    def _shared_area(self):
        """The one encounter area both cartridges are standing on."""
        a, b = self._area("a"), self._area("b")
        if not (a and b):
            return None
        if a != b:
            raise RuntimeError(
                f"A is on {a} and B is on {b}. Soul Link pairs and locks BY AREA, so these "
                f"scenarios need both fixtures on one encounter map. All six committed "
                f"battery saves stand on Route 1 at (10,35) — Red, Blue AND Yellow, decoded "
                f"in tests/unit/test_gen1_fixtures.py — so if these disagree the fixtures "
                f"have been rebuilt somewhere else, not inherited from a title difference.")
        return a

    # ── assertions ───────────────────────────────────────────────────────────
    def assert_dead_zone_refusal(self):
        """(a) A real failed encounter locks the area, and the PARTNER is refused there.

        Nothing is injected: A's no_catch comes from the client noticing its own wild battle
        ended with no new party member, and the area lock is read back off the live server
        before B is allowed to move. Withholding B's go-file until then is what makes "B
        caught INSIDE a dead zone" a fact instead of a race.
        """
        self._go_one("a")
        area = self.wait_for("A's failed encounter to lock an area",
                        self._dead_zone_area, self.cfg["timeout"])
        print(f"[duo] DEAD ZONE FROM REAL PLAY: {area}")
        a_area = self._area("a")
        if a_area and a_area != area:
            raise RuntimeError(f"A played on {a_area} but {area} is what got locked")

        self._go_one("b")
        self.wait_for("B to report its area", lambda: self._area("b"), 900)
        self._shared_area()      # raises with a readable message if the fixtures disagree

        # Wait on the RETIREMENT, not on B naming the mon. A ball leaving the bag plus the
        # memorial growing is the rule under test; whether B could still read the mon it
        # threw at is a race it does not need to win (see _caught).
        self.wait_for("B to throw a ball inside the dead zone",
                 lambda: "THREW " in (read_result(self.scenario, "b") or ""),
                 self.cfg["timeout"])
        self.wait_for("B's client to retire the refused capture",
                 lambda: "REFUSED " in (read_result(self.scenario, "b") or ""), 900)
        b_key = self._caught("b")
        print(f"[duo] B threw in the dead area and the client retired "
              f"{b_key or 'a capture it could not read back'}")

        st = self._status() or {}
        got = (st.get("area_states") or {}).get(area)
        if got != "dead_zone":
            raise RuntimeError(f"{area} is {got!r} after B's capture, not dead_zone — the "
                               f"lock did not survive a capture attempt")
        # Key-specific check, only when B managed to name the mon. Every assertion around
        # it is key-independent on purpose, so losing the race weakens the test by exactly
        # one check instead of failing it.
        if b_key:
            for link in st.get("links") or []:
                if b_key in (link.get("a_key"), link.get("b_key"))                         and link.get("status") == "alive":
                    raise RuntimeError(f"B's dead-zone catch {b_key} formed a LIVE link")
        if ((st.get("pending_captures") or {}).get(area) or {}).get("b"):
            raise RuntimeError(f"B's dead-zone catch is pending in {area} — it was accepted")
        if not [lnk for lnk in (st.get("links") or [])
                if lnk.get("area_id") == area and lnk.get("status") == "dead"]:
            raise RuntimeError(f"no DEAD link entry recorded for {area}")

    def assert_species_clause_rejection(self):
        """(b) The species clause rejects a partner catch from the same evolution family.

        Both cartridges are pointed at one species through wGrassMons, so both meet it for
        real; the server rejects whichever capture arrives second. Neither side knows in
        advance which it will be, so the verdicts are cross-checked here.
        """
        self.go()
        self.wait_for("both instances to report their area",
                 lambda: self._area("a") and self._area("b"), 900)
        area = self._shared_area()
        keys = self.wait_for("both instances to catch the forced species",
                        lambda: (self._caught("a"), self._caught("b"))
                        if self._caught("a") and self._caught("b") else None,
                        self.cfg["timeout"])
        print(f"[duo] both caught in {area}: a={keys[0]} b={keys[1]}")

        def verdicts():
            out = {}
            for inst in ("a", "b"):
                text = read_result(self.scenario, inst) or ""
                for tag in ("REJECTED", "KEPT"):
                    if any(ln.startswith(tag + " ") for ln in text.splitlines()):
                        out[inst] = tag
            return out if len(out) == 2 else None

        v = self.wait_for("both instances to report a verdict", verdicts, 900)
        if sorted(v.values()) != ["KEPT", "REJECTED"]:
            raise RuntimeError(f"expected exactly one rejection, got {v} — with both sides "
                               f"holding the same species the clause must fire exactly once")

        st = self._status() or {}
        if [lnk for lnk in (st.get("links") or []) if lnk.get("area_id") == area]:
            raise RuntimeError(f"a link was recorded in {area} — the clause did not reject")
        pend = ((st.get("pending_captures") or {}).get(area) or {})
        if len(pend) != 1:
            raise RuntimeError(f"expected one surviving pending capture in {area}, got {pend}")
        rejected = next(i for i, tag in v.items() if tag == "REJECTED")
        if rejected in pend:
            raise RuntimeError(f"{rejected} reported REJECTED but its capture is still pending")
        got = (st.get("area_states") or {}).get(area)
        if got not in ("pending_a", "pending_b"):
            raise RuntimeError(f"{area} is {got!r} — a clause rejection must reopen the area "
                               f"for a retry, not lock or link it")
        print(f"[duo] SPECIES CLAUSE: {rejected}'s catch rejected, {area} reopened ({got})")

    # ── NEW Gen 1 client (game gen1_new) ─────────────────────────────────────
    def _links_json(self):
        """The server's persisted link table (carries `cause`, which /api/status does not)."""
        path = os.path.join(self.data_dir, "links.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("links") or []

    def _reconnect_document(self):
        with open(os.path.join(self.data_dir, "links.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def _links_bytes(self):
        """The persisted link document's raw bytes, or None before the server writes one.

        Byte identity is the strongest server-side claim a scenario like the soft reset can
        make: it is checked against a baseline taken before either client acted.
        """
        path = os.path.join(self.data_dir, "links.json")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()

    def _reconnect_events(self):
        path = os.path.join(self.data_dir, "events.json")
        if not os.path.exists(path):
            return []
        # the server rewrites events.json in place (truncate + dump), so a read can land
        # mid-write; retry briefly, then let the last attempt raise
        for _ in range(20):
            try:
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            except (ValueError, OSError):
                time.sleep(0.05)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _wait_ball_fact(self, inst, label, timeout=300):
        def fact():
            receipt = read_result(self.scenario, inst) or ""
            if label + " " not in receipt:
                return None
            return ball_gate_fact(receipt, label)

        return self.wait_for(f"{inst} {label}", fact, timeout)

    def assert_ball_gate_new(self):
        """Stage the lab rivals separately; take every verdict from the server and receipts."""
        hellos = {inst: self._wait_ball_fact(inst, "BALL_HELLO", 180) for inst in ("a", "b")}
        self.wait_connected()
        self._ball_hellos = hellos
        def check_pre():
            return ball_gate_pre_problems(self._status() or {}, self._links_json(),
                                          self._reconnect_events(), hellos)

        problems = check_pre()
        if problems:
            raise RuntimeError("; ".join(problems))
        self._pydec_note("cold bedroom: two distinct OTs, zero balls, both gates closed")
        self.go()
        pre = {inst: self._wait_ball_fact(inst, "BALL_PRE_RIVAL", 300) for inst in ("a", "b")}
        def starter_link():
            links = self._links_json()
            return links if len(links) == 1 else None

        links = self.wait_for("starter gift pair", starter_link, 60)
        problems = ball_gate_starters_problems(self._status() or {}, links,
                                               self._reconnect_events(), pre)
        if problems:
            raise RuntimeError("; ".join(problems))
        self._ball_starter_link = links[0]
        self._pydec_note("starter gift_map_40 pair linked with both zero-ball gates closed")
        self._append_reconnect_marker("a", "ALLOW_A_RIVAL")
        a_lab = self._wait_ball_fact("a", "BALL_LAB", 300)
        self._append_reconnect_marker("b", "ALLOW_B_RIVAL")
        b_release = self._wait_ball_fact("b", "BALL_RELEASE_RIVAL", 30)
        b_lab = self._wait_ball_fact("b", "BALL_LAB", 300)
        log_path = os.path.join(self.data_dir, "slink.log")
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            server_log = handle.read()
        problems = ball_gate_lab_problems(self._status() or {}, self._links_json(),
                                          self._ball_starter_link,
                                          self._reconnect_events(), {"a": a_lab, "b": b_lab},
                                          pre, {"b": b_release}, server_log)
        if problems:
            raise RuntimeError("; ".join(problems))
        self._pydec_note("both real Rival1 faints suppressed; gift pair alive; parked B HP unchanged")
        for inst in ("a", "b"):
            self._append_reconnect_marker(inst, "ALLOW_PARCEL")
        after = {inst: self._wait_ball_fact(inst, "BALL_AFTER_LABS", 30) for inst in ("a", "b")}
        problems = ball_gate_after_labs_problems({"a": a_lab, "b": b_lab}, after)
        if problems:
            raise RuntimeError("; ".join(problems))
        flips = {inst: self._wait_ball_fact(inst, "BALL_FLIP", 360) for inst in ("a", "b")}
        self.wait_for("both server ball gates active", lambda: all(
            ((self._status() or {}).get("players") or {}).get(inst, {}).get("nuzlocke_active")
            for inst in ("a", "b")), 60)
        problems = ball_gate_flip_problems(self._status() or {}, flips)
        if problems:
            raise RuntimeError("; ".join(problems))
        self._pydec_note("both filtered bag_received signals followed by client and server activation")
        for inst in ("a", "b"):
            self._append_reconnect_marker(inst, "ALLOW_SAVE")

    def assert_ball_gate_saved(self, results):
        """Post-result oracle; `results` is the dispatcher contract, consumed by the live leg."""
        if self._links_json() != [self._ball_starter_link]:
            raise RuntimeError("starter gift pair changed before the first wild encounter")
        for process in self.emus:
            process.wait(timeout=30)  # BizHawk flushes the normal SAVE on client.exit.
        for inst, species in (("a", 0x99), ("b", 0xB0)):
            receipt = read_result(self.scenario, inst) or ""
            if "SAVE_WITNESS ball_gate_new" not in receipt or "BALL_SAVED " not in receipt:
                raise RuntimeError(f"{inst} has no ordinary-button save witness")
            _sram, party, _box, _codec = self._saved_gen1_party(inst)
            if (len(party) != 1 or party[0]["species"] != species or party[0]["hp"] <= 0
                    or party[0]["ot_id"] != self._ball_hellos[inst]["ot_id"]):
                raise RuntimeError(f"{inst} qualified save lacks its living original starter")
        self._pydec_note("both flushed SaveRAMs qualify; one living original starter per cartridge")

    def _append_reconnect_marker(self, inst, marker):
        with open(self.go_files[inst], "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")

    def _wrong_red_save_ot(self, path, expected_ot):
        """Require a real, independently played Red save with a different saved trainer ID."""
        from pathlib import Path

        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        from gen1_fixtures import qualify

        from server.adapters import gen1_codec as codec

        source = Path(path)
        name = source.name.lower()
        title_a = self.gcfg["fixture"]["a"]  # red, or purered on the pure pairing
        if (not source.is_file() or source.suffix != ".SaveRAM"
                or not (name.startswith(title_a) or f"{title_a} version" in name)):
            raise RuntimeError(f"--wrong-save must name an existing second-OT {title_a} SaveRAM")
        sram = source.read_bytes()
        # the cartridge A boots (its companion build), never the clean dump the run refuses
        rom = (Path(REPO) / self._rom_for("a")).read_bytes()
        problems = qualify(sram, rom)  # gen1_fixtures.py:81-148, game's checksum/stat oracle
        if problems:
            raise RuntimeError(f"--wrong-save is not a game-loadable Red save: {problems}")
        pack = "gen1_purergb" if title_a.startswith("pure") else "gen1_rby"
        profile = json.loads((Path(REPO) / "data/games" / pack / "profile.json").read_text(
            encoding="utf-8"))["titles"][title_a]["ram"]
        # save.asm:208-220 copies wMainDataStart..End to sMainData; never subtract
        # wPlayerName here (gen1_codec.py:74-77 uses the same compacted offset rule).
        offset = codec.SRAM_LAYOUT["sMainData"] + profile["wPlayerID"] - profile["wMainDataStart"]
        other_ot = int.from_bytes(sram[offset:offset + 2], "big")
        if str(other_ot) == str(expected_ot):
            raise RuntimeError(f"--wrong-save has the original OT {other_ot}; need a second-OT Red save")
        return other_ot

    def _wrong_save_missing(self):
        self._pydec_note("WRONG_SAVE_LEG NOT RUN — supply --wrong-save <second-OT red*.SaveRAM>")
        self._live_complete["reconnect_new"] = False

    def assert_reconnect_new(self):
        """C-2 crash/reload while B stays online, then optional fail-closed C-1 wrong save."""
        from pathlib import Path

        self.go()
        self.assert_link_new()
        for inst in ("a", "b"):
            self.wait_for(f"{inst} link_new SAVE and reconnect hold",
                     lambda i=inst: "RECONNECT_READY " + i in (read_result(self.scenario, i) or ""), 300)
        _sram, party, _current, codec = self._saved_gen1_party("a")
        if [codec.key(mon) for mon in party] != [self._boot_keys["a"], self._link_keys["a"]]:
            raise RuntimeError("A's flushed Red save does not hold the linked pair before the crash")
        correct_save = Path(self._saveram_dir("a")) / self._gen1_save_name("a")
        shutil.copyfile(correct_save, os.path.join(self.data_dir, "a_original_before_reconnect.SaveRAM"))
        baseline = {"links": self._reconnect_document(), "events": self._reconnect_events()}
        shutil.copyfile(self._result_path("a"), os.path.join(self.data_dir, "a_initial_result.txt"))

        self.terminate_instance("a")
        self.wait_for("A disconnected while B stays online", lambda: (
            (s := self._status()) and not s["players"]["a"]["connected"]
            and s["players"]["b"]["connected"]), 45)
        if self._reconnect_document().get("links") != baseline["links"].get("links"):
            raise RuntimeError("the live link changed when A's EmuHawk was killed")
        self._pydec_note("C-2 A EmuHawk terminated; server/B live, link unchanged while A disconnected")
        self.launch_instance("a", phase="same_save", seed=False, expected_key=self._link_keys["a"])
        same_path = self._phase_result_path("a", "same_save")
        self.wait_for("same-save A hello", lambda: "RECONNECT_HELLO same_save count=1" in (
            Path(same_path).read_text(encoding="utf-8") if os.path.exists(same_path) else ""), 180)
        self.wait_for("server accepts A's same-save party", lambda: (
            (s := self._status()) and (a := s["players"]["a"]).get("connected")
            and not a.get("identity_error") and self._link_keys["a"] in (a.get("party_keys") or [])), 60)
        old_a_hellos = sum(row.get("type") == "hello" and row.get("player") == "a"
                           for row in baseline["events"])
        self.wait_for("durable accepted reconnect hello", lambda: sum(
            row.get("type") == "hello" and row.get("player") == "a"
            for row in self._reconnect_events()) == old_a_hellos + 1, 30)
        same_after = {**self._reconnect_document(), "events": self._reconnect_events(),
                      "status": self._status() or {}}
        problems = reconnect_same_problems(baseline["links"], same_after, baseline["events"],
                                           same_after["events"], self._link_keys["a"],
                                           baseline["links"]["player_identity"]["a"]["ot_id"])
        same_receipt = Path(same_path).read_text(encoding="utf-8")
        if "RX force_faint" in same_receipt or "RX box_mon" in same_receipt:
            problems.append("A relaunch received force_faint/box_mon")
        if problems:
            raise RuntimeError("C-2 same-save reconnect failed: " + "; ".join(problems))
        self._pydec_note(f"C-2 same OT accepted; alive link {self._link_keys['a']} / "
                         f"{self._link_keys['b']}; party re-synced, zero duplicate gameplay events")
        self._append_reconnect_marker("a", "A_DONE_SAME")
        self.wait_for("same-save A phase PASS", lambda: "RESULT: PASS" in (
            Path(same_path).read_text(encoding="utf-8") if os.path.exists(same_path) else ""), 60)
        final_same_receipt = Path(same_path).read_text(encoding="utf-8")
        if "RX force_faint" in final_same_receipt or "RX box_mon" in final_same_receipt:
            raise RuntimeError("A received a late force_faint/box_mon after reconnect validation")
        self.emu_by_inst["a"].wait(timeout=30)
        # The wrong-OT relaunch overwrites this file IN PLACE (shutil.copyfile(wrong,
        # correct_save), further down), so the same-save leg's flush is copied out here, while
        # it is still the same-save leg's bytes, and read back by assert_reconnect_saved.
        self._same_save_artifact = os.path.join(BUILD, "e2e_reconnect_new_a_same.SaveRAM")
        shutil.copyfile(correct_save, self._same_save_artifact)
        self.terminate_instance("a")
        self.wait_for("same-save A socket closed before the wrong-save relaunch", lambda: (
            (s := self._status()) and not s["players"]["a"]["connected"]), 45)

        wrong = getattr(self.args, "wrong_save", None)
        if not wrong:
            self._wrong_save_missing()
            final_a = same_path
        else:
            other_ot = self._wrong_red_save_ot(wrong, baseline["links"]["player_identity"]["a"]["ot_id"])
            before_wrong_bytes = Path(self.data_dir, "links.json").read_bytes()
            before_wrong_events = self._reconnect_events()
            shutil.copyfile(wrong, correct_save)
            self.launch_instance("a", phase="wrong_save", seed=False)
            wrong_path = self._phase_result_path("a", "wrong_save")
            self.wait_for("A wrong-save HUD", lambda: "WRONG_SAVE_HUD [x] WRONG SAVE: slot A" in (
                Path(wrong_path).read_text(encoding="utf-8") if os.path.exists(wrong_path) else ""), 180)
            after_wrong = self._status() or {}
            problems = reconnect_wrong_problems(before_wrong_bytes, Path(self.data_dir, "links.json").read_bytes(),
                                                after_wrong, before_wrong_events, self._reconnect_events())
            if problems:
                raise RuntimeError("C-1 wrong-save refusal failed: " + "; ".join(problems))
            self._pydec_note(f"C-1 wrong OT {other_ot} rejected; links.json byte-identical, no gameplay events")
            self._append_reconnect_marker("a", "A_DONE_WRONG")
            self.wait_for("wrong-save A phase PASS", lambda: "RESULT: PASS" in (
                Path(wrong_path).read_text(encoding="utf-8") if os.path.exists(wrong_path) else ""), 60)
            self.emu_by_inst["a"].wait(timeout=30)
            self._live_complete["reconnect_new"] = True
            final_a = wrong_path
        shutil.copyfile(final_a, self._result_path("a"))
        self._append_reconnect_marker("b", "B_DONE")
        self.wait_for("B stayed online through reconnect legs", lambda: "RESULT: PASS" in (
            read_result(self.scenario, "b") or ""), 120)
        return self._live_complete.get("reconnect_new", False)

    def assert_reconnect_saved(self, results):
        """C-2/C-1 saved states: each leg's flushed SaveRAM, read as the cartridge that wrote it.

        The wrong-OT relaunch overwrites A's SaveRAM in place, so the same-save leg has to be
        read from the artifact the live assert copied out before that (`_same_save_artifact`);
        the instance's own file then describes the wrong-save phase. Both reads name their file
        and phase in the PYDEC receipt: the OT expectation is what tells the two apart, so a
        wrong-path or stale read cannot pass as the other leg.
        """
        if not self._live_complete.get("reconnect_new"):
            raise RuntimeError("reconnect_new's live legs did not complete — the final A save "
                               "belongs to the wrong-save phase and the same-save flush was "
                               "never copied out")
        from gen1_fixtures import saved_ot

        for process in self.emus:
            process.wait(timeout=30)  # client.exit flushes CartRAM
        # The clean OT is the pairing's own A fixture (red 0x4190, purered 0xC131), not a literal.
        title_a = self.gcfg["fixture"]["a"]
        with open(self._fixture_save_path("a"), "rb") as handle:
            DEFAULT_OT = saved_ot(handle.read(), title_a)
        same = self._artifact(self._same_save_artifact, "C-2 same-save SaveRAM")
        same_sram, same_party, _same_box, codec = self._saved_gen1_party("a", save_name=str(same))
        same_keys = [codec.key(mon) for mon in same_party]
        same_ot = saved_ot(same_sram, title_a)
        if self._link_keys["a"] not in same_keys:
            raise RuntimeError(f"same-save artifact lacks A's linked key {self._link_keys['a']}: "
                               f"{same_keys}")
        if same_ot != DEFAULT_OT:
            raise RuntimeError(f"same-save artifact carries OT 0x{same_ot:04X}, not the clean "
                               f"Red OT 0x{DEFAULT_OT:04X} — it is not the same-save flush")
        self._pydec_note(f"phase same_save: {os.path.relpath(same, REPO)} OT 0x{same_ot:04X}, "
                         f"linked key present, party {same_keys}")

        final_sram, final_party, _final_box, _codec = self._saved_gen1_party("a")
        final_keys = [codec.key(mon) for mon in final_party]
        final_ot = saved_ot(final_sram, self.gcfg["fixture"]["a"])
        if final_ot == DEFAULT_OT:
            raise RuntimeError(f"final A save still carries the clean Red OT 0x{final_ot:04X}; "
                               f"the wrong-OT relaunch did not replace the same-save file")
        if self._link_keys["a"] in final_keys:
            raise RuntimeError(f"the wrong-OT save still holds A's linked key "
                               f"{self._link_keys['a']}: {final_keys}")
        self._pydec_note(f"phase wrong_save: OT 0x{final_ot:04X} != 0x{DEFAULT_OT:04X}, "
                         f"linked key absent, party {final_keys}")

    def assert_soft_reset_saved(self, results):
        """W-6/R-4: A's same-save soft reset, from the driver's markers and the server's refusal
        to see anything but a reconnect.

        The reset zero-fills $C000-$DFFF while the client is mid-flight, so the load-bearing
        claims are about what did NOT happen: no WRAM and no cart write landed on the cleared
        window, the durable pair is byte-identical to the pre-reset baseline, and the re-hello
        carried the SAME trainer ID. Every marker read here is named in the PYDEC receipt.
        """
        from pathlib import Path

        from gen1_fixtures import saved_ot

        from server.adapters import gen1_codec as codec

        for process in self.emus:
            process.wait(timeout=30)  # client.exit flushes CartRAM
        a_text, b_text = results["a"], results["b"]

        ot = marker(a_text, r"HELLO_AT_CHECKPOINT ot=([0-9A-Fa-f]{4}) hellos=1",
                    "A checkpoint hello")
        reset = marker(a_text, r"RESET_SEEN frame=(\d+)", "A soft reset")
        if not 40 <= int(reset.group(1)) <= 60:
            raise RuntimeError(f"A's WRAM clear landed {reset.group(1)} frames after the chord "
                               f"started, outside 40..60 (16 polls + 32 DelayFrames)")
        cleared = marker(a_text, r"HELLO_CLEARED frame=\d+ delta=(\d+)", "A hello withhold")
        if int(cleared.group(1)) > 120:
            raise RuntimeError(f"A's hello_sent survived {cleared.group(1)} frames of cleared "
                               f"WRAM (one validation is 60)")
        paused = marker(a_text, r"WRITES_PAUSED frame=\d+ delta=(\d+)", "A writes pause")
        if not 240 <= int(paused.group(1)) <= 360:
            raise RuntimeError(f"A paused writes {paused.group(1)} frames after the reset, "
                               f"outside 240..360 (5 x 60-frame validations)")
        started, resumed = a_text.find("WRITES_PAUSED"), a_text.find("WRITES_RESUMED")
        if resumed < 0 or resumed < started:
            raise RuntimeError("A never logged WRITES_RESUMED after WRITES_PAUSED")
        marker(a_text, r"CONTINUED frame=", "A CONTINUE")
        rehello = marker(a_text, r"REHELLO ot=([0-9A-Fa-f]{4}) hellos=2", "A re-hello")
        if rehello.group(1).upper() != ot.group(1).upper():
            raise RuntimeError(f"A re-helloed as OT {rehello.group(1)}, not the pre-reset "
                               f"OT {ot.group(1)}")
        marker(a_text, r"NO_WRITES_IN_WINDOW writes=0 cart_writes=0", "A cleared-window witness")
        marker(a_text, r"SAVE_WITNESS soft_reset_new_a", "A save witness")
        marker(a_text, r"\[SLink-gen1\] writes PAUSED", "A client pause line")
        marker(a_text, r"writes re-enabled after a live validation", "A client resume line")
        for absent in ("BOX_WRITE ", "RX force_faint", "RX box_mon"):
            if absent in a_text:
                raise RuntimeError(f"A's reset receipt carries {absent!r}; nothing may write to "
                                   f"the cleared window")
        marker(b_text, r"IDLE_PARTNER hellos=1", "B idle partner")
        marker(b_text, r"SAVE_WITNESS soft_reset_new_b", "B save witness")
        self._pydec_note(f"W-6 markers: RESET_SEEN={reset.group(1)} HELLO_CLEARED="
                         f"{cleared.group(1)} WRITES_PAUSED={paused.group(1)} "
                         f"OT={ot.group(1)} re-hello={rehello.group(1)}; "
                         f"NO_WRITES_IN_WINDOW 0/0; no BOX_WRITE/force_faint/box_mon")

        baseline = self._reset_baseline
        rows = self._reconnect_events()
        a_hellos = [row for row in rows if row.get("type") == "hello" and row.get("player") == "a"]
        b_hellos = [row for row in rows if row.get("type") == "hello" and row.get("player") == "b"]
        # The hello baseline is taken after `wait_connected` and BEFORE the go-file, and a
        # client's ticks are gated on its hello (lua/gen1/client.lua:908-912,920), so both
        # players had exactly one hello row when it was taken. Taking it after the release
        # instead is what broke tonight: A's chord follows its checkpoint hello by ~55 frames
        # and the re-hello lands ~1.8 s later, so the snapshot caught 2 A hellos and this check
        # fired (H-6). It stays a hard failure rather than "one more than whatever the baseline
        # happened to be" — a baseline that is not pre-reset makes the whole comparison vacuous.
        if baseline["a_hellos"] != 1:
            raise RuntimeError(f"the pre-reset baseline saw {baseline['a_hellos']} A hellos, not "
                               f"the one the checkpoint hello produces")
        if (len(a_hellos) != baseline["a_hellos"] + 1
                or not all(row.get("text", "").startswith("Connected (") for row in a_hellos)):
            raise RuntimeError(f"A's durable hellos are not baseline+1 accepted rows: {a_hellos}")
        if len(b_hellos) != 1 or not b_hellos[0].get("text", "").startswith("Connected ("):
            raise RuntimeError(f"B's durable hello is not one accepted row: {b_hellos}")
        if any("REJECTED" in row.get("text", "") for row in rows):
            raise RuntimeError("a REJECTED hello appeared during a same-save reset")
        if _event_counts(baseline["events"]) != _event_counts(rows):
            raise RuntimeError("gameplay event counts changed across the soft reset")
        current_bytes = self._links_bytes()
        # Byte identity was the old claim, and it is too strong: a re-hello re-inserts
        # per-player entries (dict order) and the set-derived lists are `list(set)`, so the
        # same state can be written in a different order (see _LINKS_SET_PATHS). Canonical
        # equality is the state claim; anything else fails with the first differing path.
        original = json.loads((baseline["links_bytes"] or b"{}").decode("utf-8"))
        current = json.loads((current_bytes or b"{}").decode("utf-8"))
        # mon_stats is a DEFERRED FLUSH, not state. server/server.py:1685-1687 backfills it from
        # each hello's own party snapshot (_cache_mon_info :1438-1446) and never saves; the hello
        # handler's only `_save()` is the `_dirty` rom/trainer commit ~30 lines earlier (:1652)
        # and ticks never save (server/state.py:395-412), so hello N persists hello N-1's stats
        # and the last half's sit in RAM until the next save of any kind — here A's re-hello
        # (server/state.py:1013-1015). The pre-reset baseline therefore holds the FIRST half to
        # hello and the end state holds both: a write that was already owed before the chord, not
        # a change the reset caused. So the node is reconciled rather than diffed — values may not
        # move and no key outside the two boot mons may appear — and then dropped from the compare.
        base_stats = _flat_mon_stats(original.pop("mon_stats", None))
        now_stats = _flat_mon_stats(current.pop("mon_stats", None))
        moved = {key: (was, now_stats.get(key, "<missing>"))
                 for key, was in base_stats.items() if now_stats.get(key) != was}
        if moved:
            raise RuntimeError(f"mon_stats changed across the soft reset: {moved}")
        strays = sorted(set(now_stats) - set(self._boot_keys.values()))
        if strays:
            raise RuntimeError(f"mon_stats gained {strays} across a reset in which nothing was "
                               f"caught; only the two boot mons may ever appear")
        flushed = sorted(set(now_stats) - set(base_stats))
        if current_bytes != baseline["links_bytes"]:
            if canonical_json(original) != canonical_json(current):
                path, was, now = first_json_difference(original, current)
                raise RuntimeError(f"links.json changed across the soft reset at {path}: "
                                   f"{was!r} -> {now!r}")
            reordered = "; ".join(reordered_json_keys(original, current)) or "nested maps"
            self._pydec_note(f"links.json differs from the baseline only in ORDER ({reordered})"
                             + (f" and the deferred mon_stats flush for {flushed}" if flushed else "")
                             + f"; the state is identical, baseline kept at "
                               f"{rel_to_repo(baseline['links_baseline_path'])}")
        self._pydec_note(f"server: A hellos {len(a_hellos)} = baseline {baseline['a_hellos']}+1, "
                         f"B {len(b_hellos)}, 0 REJECTED, gameplay counts unchanged, "
                         f"links.json canonically identical to the baseline")

        for inst, title in (("a", self.gcfg["fixture"]["a"]), ("b", self.gcfg["fixture"]["b"])):
            fixture = Path(self._fixture_save_path(inst)).read_bytes()
            start = codec.SRAM_LAYOUT["sPartyData"]
            want = [codec.key(mon) for mon in codec.decode_party(
                fixture[start:start + codec.PARTY_LAYOUT["size"]])]
            sram, party, _box, _codec = self._saved_gen1_party(inst)
            got = [codec.key(mon) for mon in party]
            if got != want:
                raise RuntimeError(f"{inst}'s saved party {got} is not the fixture's {want}: the "
                                   f"reset did not reload the same save")
            if saved_ot(sram, title) != saved_ot(fixture, title):
                raise RuntimeError(f"{inst}'s saved trainer ID changed across the reset")
            self._pydec_note(f"{inst} saved party {got} == fixture "
                             f"{self.gcfg['fixture'][inst]}_{self.cfg.get('target', 'town')}; "
                             f"OT 0x{saved_ot(sram, title):04X} unchanged")

    def assert_link_new(self):
        """D-1: ONE alive link on route_1 whose halves are the two keys the cartridges caught."""
        def both_caught():
            a, b = self._caught("a"), self._caught("b")
            if a and b:
                return a, b
            if any(_has_exact_rng_miss(self._read_receipt(inst)) for inst in ("a", "b")):
                raise GameRngMiss("a cartridge missed its sole ball before the pair formed")
            return None
        a_key, b_key = self.wait_for("both instances to catch a wild mon", both_caught,
                                self.cfg["timeout"])
        print(f"[duo] real captures: a={a_key} b={b_key}")

        def linked():
            for link in (self._status() or {}).get("links") or []:
                if {link.get("a_key"), link.get("b_key")} == {a_key, b_key}:
                    return link
            return None
        link = self.wait_for("the SERVER to pair the two real captures", linked, 180)
        st = self._status() or {}
        links = st.get("links") or []
        area = self._hunt_area
        area_state = (st.get("area_states") or {}).get(area)
        problems = []
        if len(links) != 1:
            problems.append(f"expected exactly one link, got {len(links)}: {links}")
        if link.get("status") != "alive":
            problems.append(f"link status is {link.get('status')!r}, not alive")
        if link.get("area_id") != area:
            problems.append(f"link area is {link.get('area_id')!r}, not {area}")
        if area_state != "linked":
            problems.append(f"area_states.{area} is {area_state!r}, not linked")
        if problems:
            raise RuntimeError("; ".join(problems))
        print(f"[duo] ENCOUNTER LINK FROM REAL PLAY (new client): {a_key} <-> {b_key} "
              f"on {area}, alive")
        self._link_keys = {"a": a_key, "b": b_key}
        return a_key, b_key

    def _artifact(self, path, label):
        """A file this run wrote, with its provenance checked.

        Existence alone is not provenance: a leftover from an earlier run would read as this
        run's evidence, so the mtime has to be later than the attempt's start.
        """
        from pathlib import Path

        if not path:
            raise RuntimeError(f"{label} artifact was never produced by this run")
        file = Path(path)
        if not file.is_file():
            raise RuntimeError(f"{label} artifact missing: {file}")
        age = self._started - file.stat().st_mtime
        if age > 0:
            raise RuntimeError(f"{label} artifact is stale: {file} predates this run's start "
                               f"by {age:.0f}s")
        return file

    def _prepare_gen1_explode_fixture(self, inst):
        """An immutable private SYNTH baseline, distinct from the cartridge's writable save."""
        import gen1_playthrough as play

        cached = self.__dict__.setdefault("_gen1_synth_fixtures", {})
        if inst in cached:
            path, digest, _disclosure = cached[inst]
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"{inst}: SYNTH setup fixture changed after qualification")
            return path
        module = importlib.import_module(
            "tools.gen1_synth_fixtures" if __package__ else "gen1_synth_fixtures")
        title = self.gcfg["fixture"][inst]
        source = Path(play.fixture_path(title, self._target_for(inst)))
        rom = Path(REPO) / self._rom_for(inst)
        raw, disclosure = module.build_explode_synth(title, source.read_bytes(), rom.read_bytes())
        path = Path(self.data_dir) / f"explode_{inst}.SaveRAM"
        path.write_bytes(raw)
        disclosure.update(inst=inst, scenario=self.scenario, attempt=self.attempt,
                          source=str(source), fixture=str(path))
        self._pydec_note("GEN1_SYNTH_SETUP " + json.dumps(disclosure, sort_keys=True))
        cached[inst] = (str(path), disclosure["fixture_sha256"], dict(disclosure))
        return str(path)

    def assert_gen1_synth_setup(self, results, *, complete=True):
        """Bind each first catch encounter to this attempt's qualified SYNTH disclosure.

        Later hunts legitimately spend balls. On early failure an unfinished partner may
        not have reached grass yet; a PASS or RNG-cause half must always carry its first read.
        """
        if getattr(self, "cfg", {}).get("gen1_synth") != "explode":
            return
        from server.adapters import gen1_codec

        def fail(why):
            raise RuntimeError("GEN1 SYNTH setup: " + why)

        try:
            text = Path(self._pydec_path).read_text(encoding="utf-8")
            records = [json.loads(line.removeprefix("GEN1_SYNTH_SETUP "))
                       for line in text.splitlines() if line.startswith("GEN1_SYNTH_SETUP ")]
        except (OSError, TypeError, ValueError, AttributeError) as exc:
            fail("disclosure unavailable or malformed: " + str(exc))
        if (len(records) != 2 or any(not isinstance(row, dict) for row in records)
                or sorted(str(row.get("inst")) for row in records) != ["a", "b"]):
            fail("one unambiguous disclosure per instance is required")
        by_inst = {row["inst"]: row for row in records}
        cached = getattr(self, "_gen1_synth_fixtures", {})
        for inst in ("a", "b"):
            if inst not in cached:
                fail(f"{inst}: no qualified setup for this attempt")
            path, digest, expected = cached[inst]
            row = by_inst[inst]
            if json.dumps(row, sort_keys=True) != json.dumps(expected, sort_keys=True):
                fail(f"{inst}: disclosure differs from this attempt's qualified setup")
            try:
                raw = Path(path).read_bytes()
                rom_hash = hashlib.sha1((Path(REPO) / self._rom_for(inst)).read_bytes()).hexdigest()
            except OSError as exc:
                fail(f"{inst}: setup/cartridge bytes unavailable: {exc}")
            if hashlib.sha256(raw).hexdigest() != digest:
                fail(f"{inst}: setup bytes changed after qualification")
            if rom_hash != row["rom_sha1"]:
                fail(f"{inst}: cartridge differs from the setup qualification")
            foundation = "gen1_purergb" if row["title"].startswith("pure") else "gen1_rby"
            layout = gen1_codec.for_foundation(foundation)
            quantity = sum(qty for item, qty in layout.decode_bag(raw) if item in layout.ball_items)
            if type(row["balls_after"]) is not int or row["balls_after"] != quantity:
                fail(f"{inst}: disclosed balls_after disagrees with the setup bytes")
            receipt = (results or {}).get(inst) or ""
            encounters = [line for line in receipt.splitlines() if line.startswith("[hunt] encounter ")]
            if not encounters:
                if complete or classify_gen1_result(receipt) in ("PASS", "CAUSE_RNG"):
                    fail(f"{inst}: first catch encounter ball count missing")
                continue
            observed = re.fullmatch(
                r"\[hunt\] encounter 1 at \(-?\d+,-?\d+\) mode=catch balls=(\d+)", encounters[0])
            if not observed or int(observed[1]) != quantity:
                fail(f"{inst}: first catch encounter differs from disclosed balls_after={quantity}: {encounters[0]}")
            seen = self.__dict__.setdefault("_gen1_synth_observed", set())
            proof = (inst, digest, quantity)
            if proof not in seen:
                self._pydec_note(f"GEN1_SYNTH_OBSERVED inst={inst} balls={observed[1]} "
                                 f"disclosed={quantity} fixture_sha256={digest}")
                seen.add(proof)

    def _fixture_save_path(self, inst):
        """The committed or disclosed private fixture this instance was seeded from.

        `run_gb_gate.seed_saveram` copies `<rom>_<target>.SaveRAM` from this directory into the
        instance's SaveRAM dir, so reading it here compares the flushed bag with the bytes the
        cartridge actually booted from.
        """
        import gen1_playthrough as play

        if self.cfg.get("gen1_synth") == "explode":
            if inst not in getattr(self, "_gen1_synth_fixtures", {}):
                raise RuntimeError(f"{inst}: SYNTH setup was not prepared before the oracle")
            return self._prepare_gen1_explode_fixture(inst)
        return os.path.join(play.FIXTURES,
                            f"{self.gcfg['fixture'][inst]}_{self._target_for(inst)}.SaveRAM")

    def _gen1_save_name(self, inst):
        """The companion's SaveRAM filename; a missing game-row companion key is an error."""
        from run_gb_gate import GENS

        patch_key = self._patch_key(inst)
        if not patch_key:
            raise RuntimeError(f"{inst}: Gen 1 row has no companion key")
        staged = (getattr(self, "_admit_roms", None) or {}).get(inst)
        if staged:
            # a scenario-staged cartridge (admit_randomized_new's A) is unknown to the gamedb
            import gen1_playthrough as g1
            return g1.save_name_for(staged)
        return GENS["gen1"]["patched"][patch_key][2]

    def _saved_gen1_party(self, inst, rom=None, save_name=None):
        """PYDEC + the fixture qualifier on the cartridge's flushed 32 KiB SaveRAM.

        The defaults are the cartridge the instance LAUNCHED: `_rom_for` for the ROM and the save
        name of its companion key (`_patch_key`), which is the filename-derived name BizHawk gives
        a hash it does not know. Defaulting to the clean title's gamedb name read the fixture
        bytes seeded under it -- a file the companion cartridge never touches -- and passed on
        them. `rom` (repo-relative) and `save_name` override the defaults: reconnect_new's
        same-save leg is overwritten in place by the wrong-OT copy before the run ends, and
        admit_randomized_new's A saves under the randomized build's own name. An absolute
        `save_name` is read as given; a relative one resolves inside the instance's own SaveRAM
        directory.
        """
        from pathlib import Path

        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        from gen1_fixtures import qualify

        from server.adapters import gen1_codec as codec
        from server.adapters.gen1_rom_scan import RomScanError

        name = save_name or self._gen1_save_name(inst)
        path = Path(name) if os.path.isabs(str(name)) else Path(self._saveram_dir(inst)) / name
        sram = path.read_bytes()
        rom_bytes = (Path(REPO) / (rom or self._rom_for(inst))).read_bytes()
        # tools/gen1_fixtures.py:81-148 uses codec.verify_bank1 (the game's CalcCheckSum),
        # decode_party, level_from_exp and recompute_stats against this exact ROM.
        # `notes` carries the stored stats that lag their stat exp: legal (the engine only
        # rebuilds stats where it calls CalcStats -- see the band in gen1_fixtures.qualify),
        # but named in the PYDEC line so a tolerated value is never silent.
        notes: list[str] = []
        try:
            problems = qualify(sram, rom_bytes, notes)
        except RomScanError as exc:
            problems = [f"qualification: {exc}"]
        if problems:
            raise RuntimeError(f"{inst} saved game would not qualify: {problems}")
        start = codec.SRAM_LAYOUT["sPartyData"]  # gen1_codec.py:66-72,587-595
        party = codec.decode_party(sram[start:start + codec.PARTY_LAYOUT["size"]])
        current = codec.SRAM_LAYOUT["sCurBoxData"]  # the WRAM mirror is copied here on SAVE
        current_box = codec.decode_box(sram[current:current + codec.BOX_SIZE])
        self._pydec_note(f"{inst} main checksum/exp/recomputed stats valid; saved party/current "
                         f"box decode valid" + ("; " + "; ".join(notes) if notes else ""))
        return sram, party, current_box, codec

    def assert_link_new_saved(self, results):
        """The caught halves are saved in slot 1, with PYDEC-rebuilt stored stats, and each
        half's bag is exactly one Poke Ball lighter than the fixture it booted from."""
        from pathlib import Path

        for process in self.emus:
            process.wait(timeout=30)  # BizHawk flushes CartRAM when client.exit completes
        for inst in ("a", "b"):
            sram, party, current_box, codec = self._saved_gen1_party(inst)
            keys = [codec.key(mon) for mon in party]  # gen1_codec.py:602-610
            expected = [self._boot_keys[inst], self._link_keys[inst]]
            if keys != expected:
                raise RuntimeError(f"{inst} saved link party {keys}, expected starter/withdrawn {expected}")
            if any(codec.key(mon) == self._link_keys[inst] for mon in current_box):
                raise RuntimeError(f"{inst} saved current box still holds its withdrawn linked mon")
            # qualify() above independently invokes codec.recompute_stats on both mons
            # (tools/gen1_fixtures.py:118-147; gen1_codec.py:738-751).
            fixture = Path(self._fixture_save_path(inst)).read_bytes()
            # The bag's SRAM offset is a foundation fact (pureRGB moved wNumBagItems, PLAN §4
            # row 6): a pure pairing reads it through the pack layout, vanilla keeps the literal.
            layout = codec.for_foundation("gen1_purergb") if is_pure_pairing(getattr(self, "game", "")) else None
            bag_quantity = layout.bag_quantity if layout else codec.bag_quantity
            baseline = bag_quantity(fixture, codec.POKE_BALL)  # gen1_codec.py:642-645
            final = bag_quantity(sram, codec.POKE_BALL)
            self._pydec_note(f"BAG_BALLS baseline={baseline} final={final} inst={inst}")
            if final != baseline - 1:
                raise RuntimeError(
                    f"{inst} saved bag holds {final} Poke Balls; the fixture it booted from "
                    f"carried {baseline}, and this route throws exactly one")
            self._pydec_note(f"{inst} saved slot 0 starter {keys[0]}")
            self._pydec_note(f"{inst} saved slot 1 linked {keys[1]}, no current-box duplicate")

    def assert_dead_zone_new_saved(self, results):
        """B's retired key is absent from party and durably present, fainted, in Box 12."""
        for process in self.emus:
            process.wait(timeout=30)
        _a_sram, a_party, _a_current, codec = self._saved_gen1_party("a")
        b_sram, b_party, b_current, _codec = self._saved_gen1_party("b")
        if [codec.key(mon) for mon in a_party] != [self._boot_keys["a"]]:
            raise RuntimeError("A's failed encounter changed its saved starter party")
        b_keys = [codec.key(mon) for mon in b_party]
        if b_keys != [self._boot_keys["b"]] or self._deadzone_b_key in b_keys:
            raise RuntimeError(f"B's retired key remained in saved party: {b_keys}")
        if any(codec.key(mon) == self._deadzone_b_key for mon in b_current):
            raise RuntimeError("B's saved current box still holds the Box 12 memorial")
        # gen1_codec.py:636-668: raw individual and bank checksums, box count, saved
        # initialized flag and offsets. decode_box validates every count/FF terminator.
        verdict = codec.verify_boxes(b_sram)
        if (not verdict["initialized"] or not all(v["valid"] for v in verdict["banks"].values())
                or not all(v["valid"] for v in verdict["boxes"].values())):
            raise RuntimeError(f"B's saved box banks/checksums/initialized flag invalid: {verdict}")
        boxes = {}
        for number, info in verdict["boxes"].items():
            start = info["offset"]
            boxes[number] = codec.decode_box(b_sram[start:start + codec.BOX_SIZE])
        memorial = boxes[12]  # 1-based report key = Box 12; command index is 11
        if len(memorial) != 1 or codec.key(memorial[0]) != self._deadzone_b_key or memorial[0]["hp"] != 0:
            raise RuntimeError(f"B Box 12 did not save the zero-HP retired key {self._deadzone_b_key}: "
                               f"{[(codec.key(mon), mon['hp']) for mon in memorial]}")
        self._pydec_note(f"B saved party/current box excludes {self._deadzone_b_key}")
        self._pydec_note("B initialized box banks and all 12 counts/terminators/checksums valid")
        self._pydec_note(f"B Box 12 holds {self._deadzone_b_key} at HP 0")

    def assert_linked_faint_saved(self, results, *, active, explode=False, bench_battle=False):
        """D-6/W-1/W-2: server cause + both game-loadable memorials and engine receipts.

        `bench_battle` is the bench-in-battle lane (commit 6a8958fb): B's linked mon sits on the
        bench of a wild battle when the command arrives, and every B marker has to show the write
        landing INSIDE that battle -- the pre-fix client deferred it to the overworld checkpoint.
        """
        self.assert_gen1_synth_setup(results)
        for process in self.emus:
            process.wait(timeout=30)
        matches = [entry for entry in self._links_json() if entry.get("area_id") == "route_1"]
        if len(matches) != 1:
            raise RuntimeError(f"expected one durable Route 1 link, got {matches}")
        link = matches[0]
        # state.py:2661-2704 sets DEAD/cause=battle; :2745-2768 advances to MEMORIAL
        # after BOTH physical memorialize_done events.
        if link.get("status") != "memorial" or link.get("cause") != "battle":
            raise RuntimeError(f"linked faint did not become a battle-caused memorial: {link}")
        with open(os.path.join(self.data_dir, "slink.log"), encoding="utf-8") as handle:
            log_text = handle.read()
        # The server logs the command it actually chose: force_explode under Explode Mode,
        # force_faint otherwise (server/state.py:2677-2685). Selecting it once keeps the
        # server-log and client checks from disagreeing about the same run.
        expected_cmd = "force_explode" if explode else "force_faint"
        dead_transition = log_text.find(
            f"[a] faint → {expected_cmd} b:{self._link_keys['b']}")
        memorial_transition = log_text.find("pair in route_1 fully memorialized")
        if dead_transition < 0 or memorial_transition <= dead_transition:
            raise RuntimeError("server lacked ordered DEAD propagation then full memorial receipt")
        self._pydec_note("server DEAD propagation precedes final MEMORIAL, cause=battle")
        for inst in ("a", "b"):
            key = self._link_keys[inst]
            if link[inst]["key"] != key:
                raise RuntimeError(f"{inst} persisted link key differs from captured {key}")
            sram, party, current_box, codec = self._saved_gen1_party(inst)
            if getattr(self, "cfg", {}).get("gen1_synth") == "explode":
                from server.adapters import gen1_codec
                layout = gen1_codec.for_foundation("gen1_purergb" if is_pure_pairing(self.game) else "gen1_rby")
                stock = self._gen1_synth_fixtures[inst][2]["balls_after"]
                throw_lines = [line for line in results[inst].splitlines()
                               if line.startswith("[hunt] threw ball")]
                throws = []
                for line in throw_lines:
                    throw = re.fullmatch(r"\[hunt\] threw ball index \d+ -> (missed|caught|timeout) "
                                         r"balls_before=(\d+) balls_after=(\d+)", line)
                    if not throw:
                        raise RuntimeError(f"{inst}: SYNTH catch has malformed throw receipt: {line}")
                    throws.append((throw[1], int(throw[2]), int(throw[3])))
                saved_balls = sum(qty for item, qty in layout.decode_bag(sram) if item in layout.ball_items)
                remaining = stock
                for outcome, before, after in throws:
                    # A timeout is an unfinished observation, not a failed throw. The
                    # input-free wait can expire before the capture animation consumes
                    # the ball. Only settled observations carry reliable live counts;
                    # the final save must independently account for EVERY attempt.
                    if outcome != "timeout" and (before != remaining or after != remaining - 1):
                        raise RuntimeError(f"{inst}: SYNTH catch throw counts are not continuous: {throws}")
                    remaining -= 1
                caught = sum(outcome == "caught" for outcome, _, _ in throws)
                final_caught = bool(throws) and throws[-1][0] == "caught" and caught == 1
                if throws and throws[-1][0] == "timeout" and caught == 0:
                    tail = results[inst].rsplit(throw_lines[-1], 1)[1]
                    captures = []
                    capture_prefix = ""
                    for line in tail.splitlines():
                        if line.startswith("TX "):
                            try:
                                event = json.loads(line[3:])
                            except ValueError:
                                continue
                            if isinstance(event, dict) and event.get("event") == "capture":
                                captures.append(event)
                                capture_prefix = tail.split(line, 1)[0]
                    # Later party routing can remove/reinsert a captured mon; count
                    # growth only up to the capture TX, not across the whole scenario.
                    growth = re.findall(r"(?m)^PARTY_COUNT (\d+) -> (\d+) @\d+$", capture_prefix)
                    final_caught = (len(captures) == 1
                                    and captures[0].get("key") == key
                                    and captures[0].get("player") == inst
                                    and captures[0].get("area_id") == "route_1"
                                    and captures[0].get("gift") is False
                                    and sum(int(b) - int(a) for a, b in growth if int(b) > int(a)) == 1)
                decrement = stock - saved_balls
                if (not throws or decrement != len(throws) or decrement < 1
                        or not final_caught or saved_balls != remaining):
                    raise RuntimeError(f"{inst}: SYNTH catch spent balls must match recorded throws: "
                                       f"stock={stock} throws={throws} saved={saved_balls}")
                self._pydec_note(f"GEN1_SYNTH_CATCH inst={inst} balls_before={stock} "
                                 f"balls_after={saved_balls} throws={len(throws)} "
                                 f"decrement={decrement} saved=true")
            if len(party) != 1 or codec.key(party[0]) != self._boot_keys[inst] or party[0]["hp"] == 0:
                raise RuntimeError(f"{inst} saved party is not its living starter: "
                                   f"{[(codec.key(m), m['hp']) for m in party]}")
            if any(codec.key(mon) == key for mon in current_box):
                raise RuntimeError(f"{inst} saved current box still holds the linked corpse")
            verdict = codec.verify_boxes(sram)  # gen1_codec.py:636-668
            if (not verdict["initialized"] or not all(v["valid"] for v in verdict["banks"].values())
                    or not all(v["valid"] for v in verdict["boxes"].values())):
                raise RuntimeError(f"{inst} memorial banks/checksums invalid: {verdict}")
            for number, info in verdict["boxes"].items():
                offset = info["offset"]
                boxed = codec.decode_box(sram[offset:offset + codec.BOX_SIZE])  # validates count/FF
                if number == 12:
                    memorial = boxed
            # Bit 7 of the boxed status byte is an ENGINE artifact, not an ailment, so it is
            # masked off here: RemoveFaintedPlayerMon (pokered 405b624
            # engine/battle/core.asm:1003-1023) does `ld [wBattleMonStatus], a` and then
            # ReadPlayerMonCurHPAndStatus (:1800-1810) copies HP/party-pos/status into the party
            # slot the memorial is cut from. The comment there ("a is 0") only holds when the
            # low-health alarm was off; when the HP bar was RED (home/palettes.asm:44-56 ->
            # DrawPlayerHUDAndHPBar core.asm:1857-1873 sets bit 7 of wLowHealthAlarm) the routine
            # first stores DISABLE_LOW_HEALTH_ALARM ($ff) and calls WaitForSoundToFinish, which
            # returns with a == $80 from its own `and $80` (home/delay.asm:15-18) -- so the corpse
            # is saved with status $80. $80 is not a status bit (SLP_MASK %111, PSN/BRN/FRZ/PAR =
            # bits 3-6, constants/status_constants.asm), and whether it lands depends only on the
            # mon's HP fraction at the faint (red below 10/48 bar pixels), i.e. on hunt RNG.
            if len(memorial) != 1 or codec.key(memorial[0]) != key or (
                    memorial[0]["hp"], memorial[0]["status"] & 0x7F) != (0, 0):
                raise RuntimeError(f"{inst} Box 12 lacks the HP0000/status00 linked key {key}: "
                                   f"{[(codec.key(m), m['hp'], m['status']) for m in memorial]}")
            self._pydec_note(f"{inst} saved living starter; linked key {key} absent from party/current box")
            self._pydec_note(f"{inst} Box 12 {key} HP0000/status00; initialized checksums/terminators valid")
        a_text, b_text = results["a"], results["b"]
        for inst, text in (("a", a_text), ("b", b_text)):
            key = self._link_keys[inst]
            if f"RX memorialize key={key}" not in text or '"event":"memorialize_done"' not in text:
                raise RuntimeError(f"{inst} lacked memorialize command/ack for {key}")
        if f"BATTLE_FAINT_SITE {self._link_keys['a']}" not in a_text:
            raise RuntimeError("A's linked faint lacked the engine battle_faint site witness")
        if f'"event":"faint","key":"{self._link_keys["a"]}"' not in a_text:
            raise RuntimeError("A's engine battle_faint did not emit its linked key")
        # `explode=True` is Explode Mode's half: the server sends force_explode instead of
        # force_faint (server/state.py:2678-2680 picks the name, and :2677-2685 logs it) and
        # the client's coercion marker is LOOP_HEAD_EXPLODE, not LOOP_HEAD_WRITE
        # (duo_gen1_main.lua:1520 vs :1524). Without the swap this delegate demands the two
        # markers the scenario asserts ABSENT, so explode_new could never pass on a live
        # receipt. `expected_cmd` is also what the server LOG is checked for above.
        if f"RX {expected_cmd} key={self._link_keys['b']}" not in b_text:
            raise RuntimeError(f"B never received the server {expected_cmd} for its linked key")
        if "GAME_OVER RX game_over" not in b_text:
            raise RuntimeError("B never received the last-link game_over command")
        if active:
            # LOOP_HEAD_WRITE's line now carries a trailing ` hp_before=<n>`; the substring find
            # is deliberately unanchored so that field can grow.
            head = ("LOOP_HEAD_EXPLODE " if explode
                    else "LOOP_HEAD_WRITE key=" + self._link_keys["b"])
            first = b_text.find(head)
            second = b_text.find("BATTLE_FAINT_SITE " + self._link_keys["b"])
            tile_witness = ("TILEMAP_FAINTED offset=" in b_text or
                            "TILEMAP_FAINTED unavailable: native faint text advanced before probe" in b_text)
            if (first < 0 or second <= first or not tile_witness
                    or f'"event":"faint","key":"{self._link_keys["b"]}"' not in b_text
                    or "BATTLE_RESULT b " not in b_text):
                raise RuntimeError("B active write was not followed by engine battle_faint/text")
        elif bench_battle:
            self._assert_bench_battle_markers(b_text, expected_cmd)
        elif ("READY_BENCH map=12 x=8 y=31" not in b_text or "BENCH_HP_STATUS 0000 00" not in b_text
              or ("TILEMAP_FNT row=2" not in b_text and
                  "TILEMAP_FNT unavailable: memorialised within " not in b_text)):
            raise RuntimeError("B bench write lacked HP/status and party-menu FNT tile evidence")
        self._pydec_note(f"D-6/W-{2 if active else 1} server battle cause and ordered engine receipts valid")

    def _assert_bench_battle_markers(self, b_text, expected_cmd):
        """B's in-battle ordering for the bench lanes: held battle -> command received in battle
        -> bench at 0000/00 within a few frames of RX with NO loop head run yet (the write lands
        on receipt) -> the next loop head finds the queued backstop already dead and moves no
        byte -> in-battle readback with the active battler unchanged -> battle end. Each marker
        is read off the receipt duo_gen1_main.lua's bench_battle_half writes; a loop-head-only
        client never logs BENCH_ZERO_ON_RX (the driver fails first) and its loop head would be
        the one that moved the bytes (hp_before != 0, moved=true)."""
        key = self._link_keys["b"]
        other = "force_faint" if expected_cmd == "force_explode" else "force_explode"
        ready = marker(b_text, r"READY_BENCH_BATTLE linked_slot=1 active_slot=0 in_battle=([1-9]\d*) "
                               r"bench_hp=([0-9A-F]{4})", "B held battle with the linked mon benched")
        if ready.group(2) == "0000":
            raise RuntimeError("B's bench slot was already at 0 HP when the battle was held")
        rx = marker(b_text, rf"RX {expected_cmd} key={re.escape(key)} in_battle=(\d+)",
                    f"B {expected_cmd} receipt")
        if rx.group(1) == "0":
            raise RuntimeError(f"B received {expected_cmd} outside a battle")
        zero = marker(b_text, rf"BENCH_ZERO_ON_RX key={re.escape(key)} cmd={expected_cmd} "
                              r"in_battle=([1-9]\d*) bench_hp=0000 status=00 frames=([0-5]) loop_heads=0 "
                              r"active_slot=0", "B bench zero on receipt")
        write = marker(b_text, rf"LOOP_HEAD_BENCH_SETTLED key={re.escape(key)} cmd={expected_cmd} "
                               r"in_battle=([1-9]\d*) bench_hp=0000 status=00 hp_before=0 landed=true "
                               r"moved=false active_slot=0 ", "B loop-head bench backstop")
        readback = marker(b_text, r"BENCH_HP_STATUS_IN_BATTLE 0000 00 in_battle=[1-9]\d* active_slot=0 "
                                  r"active_hp=([0-9A-F]{4})->([0-9A-F]{4}) "
                                  r"starter_hp=([0-9A-F]{4})->([0-9A-F]{4}) "
                                  r"moves=([0-9A-F]{8})->([0-9A-F]{8})", "B in-battle bench readback")
        end = marker(b_text, r"BATTLE_RESULT b \d+", "B battle end")
        if not (ready.start() < rx.start() < zero.start() < write.start() < readback.start() < end.start()):
            raise RuntimeError("B bench markers out of order: want READY_BENCH_BATTLE < RX < "
                               "BENCH_ZERO_ON_RX < LOOP_HEAD_BENCH_SETTLED < BENCH_HP_STATUS_IN_BATTLE "
                               "< BATTLE_RESULT")
        if readback.group(1) != readback.group(2) or readback.group(3) != readback.group(4):
            raise RuntimeError(f"the bench write touched the active battler: {readback.group(0)}")
        moves = readback.group(6)
        if readback.group(5) != moves or "99" in (moves[i:i + 2] for i in range(0, 8, 2)):
            raise RuntimeError(f"the active battler's moves changed or carry EXPLOSION: {readback.group(0)}")
        for absent in (f"RX {other}", "READY_BENCH map=", "LOOP_HEAD_WRITE", "LOOP_HEAD_EXPLODE"):
            if absent in b_text:
                raise RuntimeError(f"B's bench-in-battle receipt carries {absent!r}")
        if expected_cmd == "force_explode":
            cmds = marker(b_text, r"EXPLODE_CMDS force_explode=(\d+) force_faint=(\d+)", "B command split")
            if cmds.groups() != ("1", "0"):
                raise RuntimeError(f"Explode Mode sent force_explode={cmds.group(1)} "
                                   f"force_faint={cmds.group(2)}, expected 1 / 0")
        self._pydec_note(f"bench-in-battle: {expected_cmd} RX in battle, bench 0000/00 "
                         f"{zero.group(2)} frame(s) after RX with no loop head run, loop-head backstop "
                         f"in_battle={write.group(1)} moved nothing, before BATTLE_RESULT, active "
                         f"battler {readback.group(1)} moves {moves} untouched")

    def assert_explode_saved(self, results):
        """W-3/D-11: the shared faint half plus the markers only a companion-patched cartridge
        with `--explode-mode` can produce.

        The saved-state half is `assert_linked_faint_saved(active=True)` on the companion
        cartridges' own saves (`_saved_gen1_party` reads what each instance launched): the pair
        must be dead with cause battle and both Box 12s must hold the linked key at HP 0. B's
        markers then prove the path was Explode Mode's: the in-battle VBlank counter advanced
        (the patch's hook still runs), the server sent only `force_explode`, the move menu
        showed the catch's own moves BEFORE the write and four EXPLOSIONs after it, and the
        commit was the coerced turn rather than a queued one.
        """
        self.assert_gen1_synth_setup(results)
        self.assert_linked_faint_saved(results, active=True, explode=True)
        a_text, b_text = results["a"], results["b"]

        marker(a_text, r"A_ENGINE_FAINT", "A engine faint")
        marker(a_text, r"BATTLE_FAINT_SITE ", "A battle faint site")
        marker(a_text, r"SAVE_WITNESS explode_new", "A save witness")
        marker(b_text, r"READY_ACTIVE linked_slot=0", "B active hold")
        counters = marker(b_text, r"PANEL_COUNTER_IN_BATTLE a=(\d+) b=(\d+)", "B VBlank probe")
        if counters.group(1) == counters.group(2):
            raise RuntimeError(f"B's in-battle VBlank counter did not advance across a frame "
                               f"({counters.group(1)} == {counters.group(2)}); the patch's hook "
                               f"is not running inside the battle")
        marker(b_text, r"RX force_explode key=", "B explode command")
        cmds = marker(b_text, r"EXPLODE_CMDS force_explode=(\d+) force_faint=(\d+)",
                      "B command split")
        if cmds.group(1) != "1" or cmds.group(2) != "0":
            raise RuntimeError(f"Explode Mode sent force_explode={cmds.group(1)} "
                               f"force_faint={cmds.group(2)}, expected 1 / 0")
        # THE RECEIPT'S OWN FORMAT, not a tidied one: duo_gen1_main.lua:1609 logs
        # `MOVE_MENU_<tag> @<framecount> <row> | <row> | <row> | <row>` with every row padded to
        # 12 columns by Center.row (:1607), so the real line reads
        # `MOVE_MENU_AFTER @16759 EXPLOSION    | EXPLOSION    | ...`. The old patterns wanted a
        # tagless marker with single spaces and could never match a cartridge receipt (H-6).
        before = marker(b_text, r"MOVE_MENU_BEFORE @\d+ (.*)", "B pre-write move menu")
        if "EXPLOSION" in before.group(1):
            raise RuntimeError(f"the move menu already showed EXPLOSION before the write landed: "
                               f"{before.group(1)!r}")
        loop = marker(b_text, r"LOOP_HEAD_EXPLODE moves=(\S+) pp=(\S+)", "B loop-head write")
        if loop.group(1) != "99999999" or loop.group(2) != "01010101":
            raise RuntimeError(f"B's battle struct after the write reads moves={loop.group(1)} "
                               f"pp={loop.group(2)}, expected 99999999 / 01010101")
        marker(b_text, r"MOVE_MENU_AFTER @\d+ EXPLOSION\s+\| EXPLOSION\s+\| EXPLOSION\s+\| "
                       r"EXPLOSION", "B post-write move menu")
        marker(b_text, r"MOVE_MENU_EXPLOSION @\d+ rows=4", "B four EXPLOSION rows")
        marker(b_text, r"B_ACTIVE_COMMIT player_move", "B commit")
        marker(b_text, r"BATTLE_FAINT_SITE .* battle_hp=0", "B battle faint site")
        marker(b_text, r"BATTLE_RESULT b", "B battle result")
        marker(b_text, r"SAVE_WITNESS explode_new", "B save witness")
        for absent in ("RX force_faint", "LOOP_HEAD_WRITE", "PANEL_COUNTER_IN_BATTLE absent"):
            if absent in b_text:
                raise RuntimeError(f"B's explode receipt carries {absent!r}")
        self._pydec_note(f"W-3/D-11 markers: VBlank counter {counters.group(1)}->"
                         f"{counters.group(2)}, force_explode={cmds.group(1)} "
                         f"force_faint={cmds.group(2)}, menu before={before.group(1).strip()!r}, "
                         f"loop head moves={loop.group(1)} pp={loop.group(2)}, rows=4, "
                         f"player_move commit; absent RX force_faint/LOOP_HEAD_WRITE/absent-probe")

        # Routing only: this row says the SERVER asked for an explode, not that the game ran one
        # (state.py:2678-2680 picks the command name; server.py:1918-1922 logs the verb). The
        # engine markers above are the execution proof, so the row is recorded, never asserted.
        exploded = [row.get("text", "") for row in self._reconnect_events()
                    if row.get("type") == "force_explode"]
        self._pydec_note(f"events.json force_explode rows: {exploded} (routing only)")

    def _rival1_id(self) -> str:
        """OPP_RIVAL1 for this pairing: the pack's trainers.json rival_ids[0] (vanilla 225,
        pureRGB 221 — OPP_ID_OFFSET 197 + RIVAL1 $18), never a literal."""
        pack = "gen1_purergb" if is_pure_pairing(getattr(self, "game", "")) else "gen1_rby"
        path = os.path.join(REPO, "data", "games", pack, "trainers.json")
        try:
            with open(path, encoding="utf-8") as f:
                ids = json.load(f).get("rival_ids")
            if ids:
                return str(ids[0])
        except (OSError, ValueError):
            pass
        return "225"

    def assert_rival_swap_new_saved(self, results):
        """W-4 / D-11 (swap half): the Route 22 rival fights the PARTNER's party.

        A's receipt carries the whole exchange: the armed-events precondition, the rival battle
        begin (opponent 225 = OPP_RIVAL1, the Gen 1 adapter's rival set at
        server/adapters/gen1_rby.py:318-320), the trainer_battle_start on the wire, the
        `replace_rival_team` command, the client's ack `within` the RIVAL_SWAP_FRAMES=120 window,
        the byte-for-byte enemy-party comparison against the command's own blobs, the send-out
        species and the battle's outcome. B idles and saves.

        The server's half is its LOG, not events.json: `_handle_rival_team_replaced`
        (server/state.py:2992-3015) logs the ack to the run's log and writes no ring-buffer row,
        so `rival_team_replaced` never appears in events.json — the card's expectation. The ack's
        species list is the readback, and the command's blob count has to equal B's party size as
        the SERVER sees it (`/api/status`), which is the claim "the blobs were B's current party"
        in the only form a post-hoc reader can check.

        The link branch is keyed on the battle's outcome, because losing to the rival is an
        ordinary trainer loss with a blackout: the whited-out half's linked pair is retired with
        cause `whiteout` (server/state.py:1982-2062). A win or a draw leaves the pair alive and
        both saved parties are starter + own catch.
        """
        for process in self.emus:
            process.wait(timeout=30)  # BizHawk flushes CartRAM when client.exit completes
        a_text, b_text = results["a"], results["b"]
        marker(a_text, r"RIVAL_EVENTS byte164=[0-9A-Fa-f]{2} first=1 wants=1",
               "A rival-event precondition")
        marker(b_text, r"RIVAL_IDLE b", "B idle receipt")
        rival1 = self._rival1_id()
        begin = marker(a_text, r"RIVAL_BATTLE_BEGIN frame=\d+ opponent=(\d+)", "A battle begin")
        if begin.group(1) != rival1:
            raise RuntimeError(f"the battle_begin that opened the window named opponent "
                               f"{begin.group(1)}, not Rival1's {rival1}")
        tx = re.findall(r'^TX .*"event":"trainer_battle_start".*"trainer_id":(\d+)', a_text, re.M)
        if tx != [rival1]:
            raise RuntimeError(f"A sent {len(tx)} trainer_battle_start line(s) for "
                               f"{tx}; exactly one naming {rival1} is expected")
        rx = marker(a_text, r"RX replace_rival_team n=(\d+)", "A swap command")
        blobs = int(rx.group(1))
        replaced = marker(a_text, r"RIVAL_TEAM_REPLACED frame=\d+ within=(-?\d+)", "A swap ack")
        within = int(replaced.group(1))
        # The window constants are read from the client BY NAME (the way the poison phrases are
        # read from the body): A13-r3 replaced RIVAL_SWAP_FRAMES=120 with RIVAL_INIT_FRAMES
        # (battle_begin -> $FF staging) and RIVAL_STAGED_FRAMES (staging -> the write). Nothing
        # here derives a bound from 120 any more.
        with open(os.path.join(REPO, "lua", "gen1", "client.lua"), encoding="utf-8") as handle:
            client_lua = handle.read()
        init_frames = int(re.search(r"RIVAL_INIT_FRAMES = (\d+)", client_lua).group(1))
        staged_frames = int(re.search(r"RIVAL_STAGED_FRAMES = (\d+)", client_lua).group(1))
        if within < 0 or within > init_frames:
            raise RuntimeError(f"the swap landed {within} frames after battle_begin; the "
                               f"client's own init window is RIVAL_INIT_FRAMES={init_frames} "
                               f"(RIVAL_STAGED_FRAMES={staged_frames} bounds the write after "
                               f"staging), so this is a late reply, not a swap")
        if "already_applied" in a_text:
            raise RuntimeError("the client acked already_applied: the swap was delivered twice, "
                               "and a live run has to apply exactly once")
        window = re.findall(r"RIVAL_WINDOW init_frames=(\d+)(?: staged_frames=(\d+))?", a_text)
        if not window:
            raise RuntimeError("A logged no RIVAL_WINDOW line; the window diagnostics are how "
                               "the lane measures the real staging age")
        # The mismatch line is the more informative failure, so it is checked first: a receipt
        # with a mismatch carries no MATCH line at all.
        if "ENEMY_MONS_MISMATCH" in a_text:
            raise RuntimeError("A logged ENEMY_MONS_MISMATCH; the enemy party did not match the "
                               "blobs the command carried")
        match = marker(a_text, r"ENEMY_MONS_MATCH slots=(\d+)", "A enemy-party compare")
        if int(match.group(1)) != blobs:
            raise RuntimeError(f"the compare covered {match.group(1)} slot(s) but the command "
                               f"carried {blobs}")
        sendout = marker(a_text, r"ENEMY_SENDOUT species=(\d+) expected=(\d+)", "A send-out")
        if sendout.group(1) != sendout.group(2):
            raise RuntimeError(f"the enemy sent out species {sendout.group(1)}, not the "
                               f"partner's slot-1 {sendout.group(2)}")
        outcome = marker(a_text, r"RIVAL_RESULT (win|loss|draw)", "A rival result")
        marker(a_text, r"RIVAL_DONE", "A rival done")
        marker(a_text, r"SAVE_WITNESS rival_swap_new_a", "A save witness")
        marker(b_text, r"SAVE_WITNESS rival_swap_new_b", "B save witness")

        with open(os.path.join(self.data_dir, "slink.log"), encoding="utf-8") as handle:
            log_text = handle.read()
        if f"[a] trainer_battle_start trainer_id={rival1} is_rival=True" not in log_text:
            raise RuntimeError(f"the server never saw A's {rival1} as a rival; the swap cannot have "
                               "been triggered by the id gate")
        ack = re.search(r"\[a\] rival_team_replaced ack trainer_id=" + rival1 + r" species=\[([0-9,\s]*)\]",
                        log_text)
        if not ack:
            raise RuntimeError("the server logged no rival_team_replaced ack for A")
        species_readback = [value for value in ack.group(1).replace(" ", "").split(",") if value]
        if len(species_readback) != blobs:
            raise RuntimeError(f"the ack read back {len(species_readback)} species for {blobs} "
                               f"blob(s): {ack.group(1)}")
        status = self._status() or {}
        b_keys = ((status.get("players") or {}).get("b") or {}).get("party_keys") or []
        # The blobs are B's party AT COMMAND TIME (state.py:2938-2948 copies `partner_blobs` as
        # the command is queued), while /api/status is read after the run ends. RIVAL-2's
        # replacement path sends A's LINKED mon into the rival fight, so a linked faint (D-6)
        # can retire B's copy in between and shrink B's party — a consequence to assert, not a
        # mismatch. Reconstruct the command-time size from the queue row's own timestamp plus
        # the retirements that follow it; both stamps are ISO, so string order is time order.
        queued = re.search(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ .*queued replace_rival_team",
                           log_text, re.M)
        if not queued:
            raise RuntimeError("the server logged no queued replace_rival_team row; its "
                               "timestamp is what dates B's party against the command")
        command_ts = f"{queued.group(1)}T{queued.group(2)}"
        links = self._links_json()
        retired = []
        # ponytail: events.json stamps are second-granular, so a retirement inside the queue's
        # own second counts as "after" — it is still a real retirement, and the link check below
        # is what keeps that lenient edge from swallowing an unrelated shortfall.
        for row in self._reconnect_events():
            if row.get("player") != "b" or row.get("type") not in ("force_faint", "faint"):
                continue
            key = row.get("key")
            if not key or key in b_keys or key in retired or str(row.get("ts")) < command_ts:
                continue
            if not any((entry.get("b") or {}).get("key") == key
                       and entry.get("status") != "alive" for entry in links):
                raise RuntimeError(f"B's party lost {key} after the swap command but no retired "
                                   f"link names it; only a linked faint (D-6) may shrink the "
                                   f"partner's party once the blobs were taken")
            retired.append(key)
        at_command = len(b_keys) + len(retired)
        if at_command != blobs:
            raise RuntimeError(f"the command carried {blobs} blob(s) but the server's view of "
                               f"B's party holds {at_command}; the swap mirrors the partner's "
                               f"current party")
        if retired:
            self._pydec_note(f"D-6 after the swap: B's party reads {len(b_keys)} at the end "
                             f"because {', '.join(retired)} was retired with its link after the "
                             f"command at {command_ts}; command-time party {at_command} == "
                             f"{blobs} blob(s)")

        durable = [entry for entry in self._links_json() if entry.get("area_id") == "route_1"]
        if len(durable) != 1:
            raise RuntimeError(f"expected one durable Route 1 link, got {durable}")
        link = durable[0]
        if outcome.group(1) == "loss":
            # A's own site marker says whether the LINKED mon fought and fainted INSIDE the
            # rival battle (RIVAL-2's replacement path is what sends it in). If it did, D-6
            # retires the pair right there with cause `battle` and the blackout that follows
            # finds nothing left to retire; only an untouched linked mon leaves the whiteout
            # itself as the cause.
            a_key = (link.get("a") or {}).get("key") or ""
            fainted_in_battle = bool(a_key) and bool(
                re.search(rf"BATTLE_FAINT_SITE {re.escape(a_key)}\b", a_text))
            cause = "battle" if fainted_in_battle else "whiteout"
            if link.get("status") not in ("dead", "memorial") or link.get("cause") != cause:
                raise RuntimeError(f"A lost to the rival, so the retired pair has to be dead or "
                                   f"memorial with cause {cause}: {link}")
            self._pydec_note(f"W-4 swap: {blobs} blob(s), within={within} frames "
                             f"(RIVAL_INIT_FRAMES={init_frames}), compare "
                             f"slots={match.group(1)}, send-out {sendout.group(1)}, "
                             f"result=loss, pair {link.get('status')}/{link.get('cause')}; "
                             f"RIVAL_WINDOW init_frames={window[-1][0]} "
                             f"staged_frames={window[-1][1] or '-'} — diagnostics only, neither "
                             f"number may widen the window")
        else:
            if link.get("status") != "alive":
                raise RuntimeError(f"A {outcome.group(1)}s the rival battle, so the pair has to "
                                   f"stay alive: {link}")
            for inst, key in (("a", link["a"]["key"]), ("b", link["b"]["key"])):
                _sram, party, current_box, codec = self._saved_gen1_party(inst)
                keys = [codec.key(mon) for mon in party]
                if keys != [self._boot_keys[inst], key]:
                    raise RuntimeError(f"{inst}'s saved party is {keys}, expected starter + {key}")
                if any(codec.key(mon) == key for mon in current_box):
                    raise RuntimeError(f"{inst}'s current box still holds {key}")
            self._pydec_note(f"W-4 swap: {blobs} blob(s), within={within} frames "
                             f"(RIVAL_INIT_FRAMES={init_frames}), compare "
                             f"slots={match.group(1)}, send-out {sendout.group(1)}, "
                             f"result={outcome.group(1)}, pair alive, both flushes starter+catch; "
                             f"RIVAL_WINDOW init_frames={window[-1][0]} "
                             f"staged_frames={window[-1][1] or '-'} — diagnostics only, neither "
                             f"number may widen the window")

    def assert_pc_ops_new_saved(self, results):
        """S-6 (Bill's PC by play) and the O-35 release kill.

        A deposits its linked half, withdraws it, deposits it again and RELEASES it from the box.
        All four are on the wire: the release logs `RELEASE_SEEN key=… box=…` and sends
        release{key} (lua/gen1/client.lua, 149b38e2), and the server kills the pair with cause
        "release" (owner ruling O-35, state.py _handle_release). B saved and exited before the
        release by design (the third box_mon must not land on a live B), so B's force_faint +
        memorialize stay queued and B's saved party still holds its partner; the physical
        memorial is the Gen 2 pc_ops receipt's claim, not this one's.

        The saved box claim is the ACTIVE box (sCurBoxData), not the numbered banks: pc_ops_new
        never changes boxes, so the numbered banks were never written by this route and their
        checksums say nothing about it. An ordinary SAVE copies the active box into sCurBoxData
        under the MAIN checksum (pret engine/menus/save.asm:246-260, :365-387; gen1_codec.py
        :669-678), which `_saved_gen1_party` already validated through `qualify()`. The claim
        here is therefore the decoded active box: it parses (count and terminator) and no longer
        holds the released key.
        """
        for process in self.emus:
            process.wait(timeout=30)  # client.exit flushes CartRAM
        a_text, b_text = results["a"], results["b"]
        key, partner_key = self._link_keys["a"], self._link_keys["b"]

        before = marker(a_text, r"PC_BOX_BEFORE box=(\d+) count=(\d+)", "A pre-deposit box")
        if before.group(1) != "1" or before.group(2) != "0":
            raise RuntimeError(f"A's Box {before.group(1)} held {before.group(2)} mon(s) before "
                               f"the deposit; the scenario needs Box 1 empty")
        counts = {name: len(re.findall(pattern, a_text)) for name, pattern in (
            ("deposit start", r"PC_OP deposit start"), ("deposit done", r"PC_OP deposit done"),
            ("withdraw done", r"PC_OP withdraw done"),
            ("release_box done", r"PC_OP release_box done"))}
        if (counts["deposit start"], counts["deposit done"]) != (2, 2):
            raise RuntimeError(f"A ran {counts['deposit start']}/{counts['deposit done']} deposit "
                               f"start/done ops, expected 2/2")
        if counts["withdraw done"] != 1 or counts["release_box done"] != 1:
            raise RuntimeError(f"A ran {counts['withdraw done']} withdraw and "
                               f"{counts['release_box done']} release ops, expected 1 each")

        tx_box = re.findall(r'^TX .*"event":"party_to_box".*' + re.escape(key), a_text, re.M)
        tx_party = re.findall(r'^TX .*"event":"box_to_party".*' + re.escape(key), a_text, re.M)
        if len(tx_box) != 2 or len(tx_party) != 1:
            raise RuntimeError(f"A sent {len(tx_box)} party_to_box and {len(tx_party)} "
                               f"box_to_party for {key}, expected 2 and 1")
        marker(a_text, re.escape("PC_DEPOSIT_KEY " + key), "A deposit key")
        marker(a_text, re.escape("PC_WITHDRAW_KEY " + key), "A withdraw key")
        marker(a_text, re.escape("PC_RELEASE_SEEN " + key), "A release receipt")
        marker(a_text, r"PC_MID party=2 box=1 count=0", "A mid state")

        releases = re.findall(r"RELEASE_SEEN key=(\S+) box=(\d+)", a_text)
        if len(releases) != 1:
            raise RuntimeError(f"A logged {len(releases)} RELEASE_SEEN line(s), expected exactly one")
        if releases[0][0] != key or releases[0][1] != "0":
            raise RuntimeError(f"RELEASE_SEEN named {releases[0][0]} box={releases[0][1]}, "
                               f"expected {key} box=0")
        offsets = [m.start() for m in re.finditer(
            r'^TX .*"event":"(?:party_to_box|box_to_party)"', a_text, re.M)]
        release_at = a_text.find("RELEASE_SEEN key=")
        # The Lua stamps each RELEASE_SEEN with the number of storage sends before it and
        # refuses anything but "after all three" (duo_gen1_main.lua:1566-1571): deposit,
        # withdraw, second deposit. The release itself sends nothing.
        if len(offsets) != 3 or release_at < offsets[-1]:
            raise RuntimeError(f"RELEASE_SEEN is not after the third storage send "
                               f"({len(offsets)} sends, release at {release_at}, last at "
                               f"{offsets[-1] if offsets else -1})")

        # The route never runs ChangeBox, so the box-initialised flag stays CLEAR: the marker
        # says init=false and the saved flag is checked through the codec rather than trusted.
        marker(a_text, r"PC_FINAL party=1 box=1 count=0 init=false", "A final state")
        marker(a_text, r"SAVE_WITNESS pc_ops_new_a", "A save witness")
        rx1 = marker(b_text, r"PC_PARTNER_RX 1 box_mon (\S+)", "B partner box_mon")
        rx2 = marker(b_text, r"PC_PARTNER_RX 2 party_mon (\S+)", "B partner party_mon")
        if rx1.group(1) != partner_key or rx2.group(1) != partner_key:
            raise RuntimeError(f"B's partner sync named {rx1.group(1)} / {rx2.group(1)}, expected "
                               f"{partner_key} for both")
        if b_text.find("PC_PARTNER_RX 1") > b_text.find("PC_PARTNER_RX 2"):
            raise RuntimeError("B's partner sync arrived party_mon before box_mon")
        if b_text.find("PC_PARTNER_RX 3") >= 0:
            raise RuntimeError("B received a third storage command; the second deposit's sync "
                               "was supposed to land before B finished")
        marker(b_text, r"SAVE_WITNESS pc_ops_new_b", "B save witness")
        tx_release = [m.start() for m in re.finditer(
            r'^TX .*"event":"release".*' + re.escape(key), a_text, re.M)]
        if len(tx_release) != 1 or tx_release[0] < release_at:
            raise RuntimeError(f"A sent {len(tx_release)} release event(s) for {key} after its "
                               f"RELEASE_SEEN, expected exactly one (O-35)")
        self._pydec_note("S-6 markers: Box 1 empty->deposit->withdraw->deposit->release; "
                         "2/1 storage sends, one RELEASE_SEEN after the third send, then "
                         "release{key} on the wire")

        a_sram, a_party, a_box, codec = self._saved_gen1_party("a")
        a_keys = [codec.key(mon) for mon in a_party]
        if a_keys != [self._boot_keys["a"]]:
            raise RuntimeError(f"A's saved party is {a_keys}, expected the starter alone after "
                               f"the release")
        if a_sram[codec._CURRENT_BOX] & codec._BOX_INITIALIZED:
            raise RuntimeError("A's saved box-initialised flag is set; pc_ops_new never runs "
                               "ChangeBox, so the flag has to stay clear")
        boxed = [codec.key(mon) for mon in a_box]  # decoded sCurBoxData, main-checksum covered
        if key in boxed:
            raise RuntimeError(f"A's saved active box still holds the released key {key}")
        if boxed:
            raise RuntimeError(f"A's saved active box holds {boxed}; the release left it empty")
        b_sram, b_party, _b_box, _codec = self._saved_gen1_party("b")
        b_keys = [codec.key(mon) for mon in b_party]
        if b_keys != [self._boot_keys["b"], partner_key]:
            raise RuntimeError(f"B's saved party is {b_keys}, expected starter + {partner_key}")
        self._pydec_note(f"{key} released: A's saved party is the starter alone and its active "
                         f"box (sCurBoxData) decodes empty; B still holds {partner_key}")

        # O-35: A's release{key} killed the pair; A initiated it and nothing killed it in battle.
        durable = [entry for entry in self._links_json() if entry.get("area_id") == "route_1"]
        pair = durable[0] if len(durable) == 1 else {}
        if ((pair.get("status"), pair.get("cause"), pair.get("killer"),
             pair.get("initiating_player")) != ("dead", "release", None, "a")
                or pair.get("a", {}).get("key") != key
                or pair.get("b", {}).get("key") != partner_key):
            raise RuntimeError(f"the durable pair {key} <-> {partner_key} did not die by the "
                               f"release (O-35): {durable}")
        self._pydec_note(f"O-35: route_1 pair a={key} b={partner_key} is dead, cause=release, "
                         f"initiated by a; B's partner death commands stay queued (B exited first)")

    def assert_changebox_new_saved(self, results):
        """W-5's box-change half: the deadzone body, then B CHANGEs BOX to 12 and back to 1.

        The shared half is `assert_dead_zone_new_saved` (A untouched, B's dead-zone catch is in
        Box 12 at HP 0 with every box bank initialised). B's own receipt then proves the change:
        Box 12 listed the memorial, the current box went back to 1, and the run sent no storage
        event of its own — a box change is a local PC action, not a party/box transfer.
        """
        self.assert_dead_zone_new_saved(results)
        b_text = results["b"]
        at12 = marker(b_text, r"CHANGEBOX_TO 12 initialised=true count=(\d+)", "B change to Box 12")
        if int(at12.group(1)) < 1:
            raise RuntimeError(f"Box 12 listed {at12.group(1)} mon(s) after the change; the "
                               f"memorial has to still be there")
        marker(b_text, r"CHANGEBOX_BACK 1", "B change back to Box 1")
        marker(b_text, r"SAVE_WITNESS changebox_new_b", "B save witness")
        for pattern, label in ((r'"event":"party_to_box"', "party_to_box"),
                               (r'"event":"box_to_party"', "box_to_party"),
                               (r"RELEASE_SEEN", "RELEASE_SEEN")):
            if re.search(pattern, b_text):
                raise RuntimeError(f"B's changebox receipt carries {label}; a box change is not "
                                   f"a storage transfer")

        b_sram, _b_party, _b_box, codec = self._saved_gen1_party("b")
        flag = b_sram[codec._CURRENT_BOX]
        index = flag & ~codec._BOX_INITIALIZED
        if not flag & codec._BOX_INITIALIZED:
            raise RuntimeError("B's saved has-changed-boxes bit is clear after CHANGE BOX")
        if index != 0:
            raise RuntimeError(f"B's saved current box is {index}, expected Box 1 (index 0)")
        verdict = codec.verify_boxes(b_sram)
        box12 = verdict["boxes"][12]
        memorial = codec.decode_box(b_sram[box12["offset"]:box12["offset"] + codec.BOX_SIZE])
        if (len(memorial) != 1 or codec.key(memorial[0]) != self._deadzone_b_key
                or memorial[0]["hp"] != 0):
            raise RuntimeError(f"Box 12 no longer holds {self._deadzone_b_key} at HP 0: "
                               f"{[(codec.key(mon), mon['hp']) for mon in memorial]}")
        self._pydec_note(f"W-5 box change: Box 12 listed {at12.group(1)} memorial(s), back to "
                         f"Box 1; saved flag set, index {index}, {self._deadzone_b_key} still "
                         f"HP 0 in Box 12; no storage event on the wire")

    def assert_whiteout_both_boxed(self):
        """The runner's half of the BOTH_BOXED handshake, before either cartridge may leave
        the PC.

        The Lua polls its go-file for the literal BOTH_BOXED (duo_gen1_main.lua:1849-1853,
        900 s budget) instead of trusting the mirrored box_mon, and this is the process that
        has to write it. Both halves deposit by hand, and a deposit is mirrored to the partner
        the moment the server sees `party_to_box` -- so the partner's key leaves `party_keys`
        there, before that cartridge has moved anything (server/state.py:2066-2113). A
        mirrored box_mon arriving at an already-deposited mon is a no-op, not a failure
        (lua/gen1/boxes.lua:296-318). Bookkeeping is therefore not evidence; this gate is the
        runner reading the server for itself:

          1. both driver receipts carry DEPOSITED_FOR_REBUILD <their own linked key> (the
             physical read: party = starter only, the catch listed in the active box);
          2. the SERVER's `state.party_keys` no longer lists either key -- the field
             `_handle_party_to_box` discards from (server/state.py:2086 own half, :2108
             mirrored half), and exactly the co-location predicate `_alive_pc_mons` uses to
             pick rebuild candidates (server/state.py:2359-2360). If this passes, the blackout
             that follows has a pair to rebuild from. The `party_keys` `/api/status` publishes
             is NOT this field: `_get_party_ordered` rebuilds it from the cartridges' own
             party snapshots (server/server.py:2209, :4335-4343);
          3. only then BOTH_BOXED into both go-files, through the same writer `go()` uses.
        """
        keys = self._link_keys
        missing = {}

        def deposited():
            missing.clear()
            for inst in ("a", "b"):
                text = read_result(self.scenario, inst) or ""
                if not re.search(re.escape("DEPOSITED_FOR_REBUILD " + keys[inst]), text):
                    missing[inst] = f"no DEPOSITED_FOR_REBUILD {keys[inst]} yet"
            return True if not missing else None

        try:
            self.wait_for("both halves to deposit their linked catch for the rebuild", deposited,
                     self.cfg["timeout"])
        except TimeoutError as exc:
            raise RuntimeError("the deposit never landed — "
                               + "; ".join(f"{inst}: {why}" for inst, why in missing.items())) from exc

        problems = {}

        def server_agrees():
            live = (self._raw_state() or {}).get("_live") or {}
            party_keys = live.get("party_keys") or {}
            problems.clear()
            for inst in ("a", "b"):
                field = party_keys.get(inst)
                if field is None:
                    problems[inst] = "party_keys: the server published no such field"
                elif keys[inst] in field:
                    problems[inst] = f"party_keys still lists {keys[inst]}: {field}"
            return True if not problems else None

        try:
            # The sub-budget stays 300 s, but never longer than the scenario's own: the pins
            # shrink the scenario timeout, and the gate must shrink with it.
            self.wait_for("the SERVER to see both linked keys boxed", server_agrees,
                          min(300, self.cfg["timeout"]))
        except TimeoutError as exc:
            raise RuntimeError(
                "the server never saw both halves boxed — "
                + "; ".join(f"{inst}: {why}" for inst, why in problems.items())) from exc
        for inst in ("a", "b"):
            self._go_one(inst, ["BOTH_BOXED"])
        print(f"[duo] BOTH_BOXED appended for both halves; the server's party_keys dropped "
              f"{keys['a']} and {keys['b']}")

    def assert_whiteout_new_saved(self, results):
        """S-4 (blackout) + W-3 (auto-rebuild): one whiteout, one rebuild, no deaths.

        Both halves deposit their linked catch, A loses its starter to a wild foe and blacks
        out to Pallet Town. `_handle_whiteout` retires only links whose half is still in the
        WHITED-OUT player's `party_keys` (server/state.py:2024-2026) — both halves are boxed —
        and plans the rebuild from alive pairs with both halves boxed (`_alive_pc_mons`
        :2359-2360, `_plan_rebuild` :2365-2392). One `party_mon` is queued to each half and
        `rebuild_start` to A alone (`_queue_rebuild_commands` :2418-2443); `rebuild_done`
        follows once A's own `sync_retrieve_done` lands (:2448-2462). Both are COMMANDS, so
        they are evidenced by the RX lines the driver tees, never by events.json, which records
        selected inbound events and derived effects (`_log_event`, server/server.py:1896-1923);
        outbound rebuild commands are not recorded.
        `game_over` is a command too; the "no game over" claim is asserted as its durable
        cause: the pair is still ALIVE, `run_over` is false, and neither client was told.

        The saved state is read through the same PYDEC path as `assert_pc_ops_new_saved`
        (`_saved_gen1_party`, whose `qualify()` checks the game's own checksum and recomputed
        stats), on the save of the companion cartridge the instance launched.
        """
        for process in self.emus:
            process.wait(timeout=30)  # BizHawk flushes CartRAM when client.exit completes
        self._artifact(self._result_path("a"), "A result receipt")
        self._artifact(self._result_path("b"), "B result receipt")
        a_text, b_text = results["a"], results["b"]
        key_a, key_b = self._link_keys["a"], self._link_keys["b"]

        # The blackout, re-derived from the driver's numbers rather than its MONEY_HALVED
        # verdict: the game halves the BCD total, so after == before // 2 or the run is wrong.
        before = marker(a_text, r"MONEY_BEFORE (\d+)", "A money before the blackout")
        after = marker(a_text, r"MONEY_AFTER (\d+)", "A money after the blackout")
        expected = int(before.group(1)) // 2
        if int(after.group(1)) != expected:
            raise RuntimeError(f"A's money went {before.group(1)} -> {after.group(1)}; the "
                               f"blackout halves it to {expected}")
        marker(a_text, r"MONEY_HALVED before=\d+ after=\d+", "A money-halving receipt")
        site = marker(a_text, r"BLACKOUT_SITE map=(\d+) x=(\d+) y=(\d+)", "A blackout site")
        # The lane visited the Viridian Center's PC first: pureRGB sets the blackout map on Center
        # ENTRY (Viridian City 1,23,26), vanilla only when the nurse heals (Pallet Town 0,5,6).
        want = ("1", "23", "26") if is_pure_pairing(getattr(self, "game", "")) else ("0", "5", "6")
        if site.groups() != want:
            where = "Viridian City (1, 23, 26)" if want[0] == "1" else "Pallet Town (0, 5, 6)"
            raise RuntimeError(f"A blacked out to map={site.group(1)} "
                               f"({site.group(2)},{site.group(3)}), not {where}")
        ko = marker(a_text, r"STARTER_KO frame=\d+ key=(\S+)", "A starter KO")
        if ko.group(1) != self._boot_keys["a"]:
            raise RuntimeError(f"A's KO'd key is {ko.group(1)}, not the boot starter "
                               f"{self._boot_keys['a']}; the linked half was supposed to be "
                               f"boxed and out of the battle")
        tx = re.findall(r'^TX .*"event":"whiteout"', a_text, re.M)
        if len(tx) != 1:
            raise RuntimeError(f"A sent {len(tx)} whiteout event(s), expected exactly one")
        if re.search(r'"event":"whiteout"', b_text):
            raise RuntimeError("B sent a whiteout; only the blacked-out half may")

        for inst, key in (("a", key_a), ("b", key_b)):
            text = results[inst]
            marker(text, re.escape("DEPOSITED_FOR_REBUILD " + key), f"{inst} deposit-for-rebuild")
            marker(text, re.escape("BOTH_BOXED status=true"), f"{inst} BOTH_BOXED ack")
            marker(text, re.escape("REBUILD_PARTY_MON " + key), f"{inst} rebuild party_mon")
            marker(text, re.escape("SYNC_RETRIEVE_DONE " + key), f"{inst} rebuild withdraw")
            marker(text, r"REBUILT party=2 box_count=0", f"{inst} rebuilt party")
            marker(text, re.escape("SAVE_WITNESS whiteout_new_" + inst), f"{inst} save witness")
            if re.search(r"GAME_OVER RX game_over", text):
                raise RuntimeError(f"{inst} was told the run is over; a boxed pair was there "
                                   f"to rebuild")

        starts = len(re.findall(r"^RX rebuild_start\b", a_text, re.M))
        dones = len(re.findall(r"^RX rebuild_done\b", a_text, re.M))
        if starts != 1 or dones != 1:
            raise RuntimeError(f"A was told rebuild_start x{starts} and rebuild_done x{dones}, "
                               f"expected 1 each")
        if a_text.find("RX rebuild_start") > a_text.find("RX rebuild_done"):
            raise RuntimeError("A's rebuild_done arrived before its rebuild_start")
        if re.search(r"^RX rebuild_(?:start|done)\b", b_text, re.M):
            raise RuntimeError("B received a rebuild command; rebuild_start/rebuild_done "
                               "belong to the whited-out half alone")

        events = self._reconnect_events()  # newest-first: server.py:1508 appendleft
        whiteouts = [row for row in events if row.get("type") == "whiteout"]
        if len(whiteouts) != 1 or whiteouts[0].get("player") != "a":
            raise RuntimeError(f"events.json carries {len(whiteouts)} whiteout row(s) "
                               f"{[(row.get('player'), row.get('text')) for row in whiteouts]}, "
                               f"expected exactly one from a")
        document = self._reconnect_document()  # the whole links.json, for run_over
        if document.get("run_over"):
            raise RuntimeError("links.json says the run is over; the whiteout had a boxed pair "
                               "to rebuild")
        live = [entry for entry in (document.get("links") or [])
                if entry.get("area_id") == "route_1"]
        if (len(live) != 1 or live[0].get("status") != "alive"
                or live[0].get("a", {}).get("key") != key_a
                or live[0].get("b", {}).get("key") != key_b):
            raise RuntimeError(f"the route_1 pair did not survive the whiteout: {live}")
        self._pydec_note(f"S-4/W-3 one whiteout from a, run_over false, route_1 pair still "
                         f"ALIVE {key_a} <-> {key_b}")

        for inst, key in (("a", key_a), ("b", key_b)):
            sram, party, current_box, codec = self._saved_gen1_party(inst)
            if not re.fullmatch(r"[0-9A-F]{4}:[0-9A-F]{4}:[0-9A-F]{2}", key):
                raise RuntimeError(f"{inst}'s linked key {key!r} is not in DDDD:OOOO:SS form")
            keys = [codec.key(mon) for mon in party]
            if keys != [self._boot_keys[inst], key]:
                raise RuntimeError(f"{inst}'s saved party is {keys}, expected the starter plus "
                                   f"the rebuilt {key}")
            if current_box:
                raise RuntimeError(f"{inst}'s saved current box still holds "
                                   f"{[codec.key(mon) for mon in current_box]}; the rebuild "
                                   f"withdrew it and box_count must be 0")
            self._pydec_note(f"{inst} saved party {len(party)} mon(s) = starter + rebuilt {key}, "
                             f"active box empty, decode valid")

    def assert_species_clause_release(self):
        """D-4's ordered release: the SERVER sees A's pending capture, then A_PENDING in B's file.

        B blocks on its go-file (duo_gen1_main.lua:706-711) and then compares the species byte
        it finds there against its own `wEnemyMonSpecies`, so the number has to be A's own WRAM
        numbering. A logs it as `PENDING_CAPTURE <key> species=<n> level=<n>`; the server's
        `pending_captures` entry is the independent confirmation that there is a capture for B
        to duplicate (server/state.py:1556-1558). Both are waited for before the file is
        written, the number written is A's, and what was written is recorded so the oracle can
        hold B's own echo to it.
        """
        def a_pending():
            match = re.search(r"PENDING_CAPTURE (\S+) species=(\d+) level=(\d+)",
                              read_result(self.scenario, "a") or "")
            if match:
                return match
            # A's hunt can end before the marker exists: `hunt("catch")` returns
            # "hunt ended out-of-balls" (duo_gen1_main.lua:727-733), the runner then never
            # writes A_PENDING, and B fails with "runner never released B (A_PENDING)" (:776).
            # Raising TimeoutError here would escape DuoRun.run() — it is not GameRngMiss, so
            # the bounded retry never classifies the receipts and the whole scenario aborts.
            # GameRngMiss is the harness's own RNG channel: run() catches it and hands the two
            # receipts to retryable_gen1_rng, which needs A's CAUSE_RNG plus this half's
            # CONSEQUENCE.
            text = read_result(self.scenario, "a") or ""
            if "RESULT:" in text and _has_exact_rng_miss(text):
                raise GameRngMiss("A's hunt spent the fixture's only ball before the release")
            return None

        match = self.wait_for("A's PENDING_CAPTURE marker", a_pending, self.cfg["timeout"])
        key, species = match.group(1), int(match.group(2))

        def server_pending():
            pending = (self._status() or {}).get("pending_captures") or {}
            entry = (pending.get("route_1") or {}).get("a")
            return entry if entry and entry.get("key") == key else None

        entry = self.wait_for("the SERVER to hold A's pending capture on route_1", server_pending,
                         self.cfg["timeout"])
        if int(entry.get("species") or 0) != species:
            raise RuntimeError(f"the server holds species {entry.get('species')} for {key}, "
                               f"but A's receipt says {species} — the numbering B compares "
                               f"against would be wrong")
        self._go_one("b", [f"A_PENDING species={species}"])
        self._species_release = {"key": key, "species": species}
        print(f"[duo] species_clause_new: released B with A_PENDING species={species} "
              f"(A's pending {key} confirmed on the server)")

    def assert_type_clause_new_saved(self, results):
        """D-5: one Route 1 catch each; the later one is rejected for the shared Normal type.

        The rejected half's receipt: force_faint + memorialize + play_sound 26 + gui_prompt
        "[x] Type clause: shared ..." + unresolve_area (server/state.py:1588-1618), then Box 12
        at hp 0. The accepted half gets play_sound 22 and NOTHING else (:1593) — no
        unresolve_area, which is why its JSON's unresolve_area is empty — and keeps its capture
        quarantined in the CURRENT BOX: an unlinked capture with a non-empty party is boxed by
        the capture-time box_mon (:1554-1557) and no party_mon follows while the area is
        pending (duo_gen1_main.lua:657-659 says the same).

        The card expected the rejected half's JSON to carry `force_faint`; the body's rejected
        form carries prompt/memorialize/sound26/unresolve_area (duo_gen1_main.lua:678-682) and
        force_faint is a COMMAND, so it is asserted from the half's own SEEN counter and from
        the Box 12 read instead.

        The area side: `pending_<rejected>`, `retry_areas[<rejected>]` gaining route_1
        (:1596-1598, :1618). The `reason=` string the card quotes lives only in a DEBUG log
        line (:913-925) and is published nowhere, so what is asserted is the durable pair.
        """
        for process in self.emus:
            process.wait(timeout=30)  # BizHawk flushes CartRAM when client.exit completes
        self._artifact(self._result_path("a"), "A result receipt")
        self._artifact(self._result_path("b"), "B result receipt")
        verdicts = {}
        for inst in ("a", "b"):
            rows = re.findall(r"^TYPE_CLAUSE (\{.*\})$", results[inst], re.M)
            if len(rows) != 1:
                raise RuntimeError(f"{inst} logged {len(rows)} TYPE_CLAUSE line(s), expected "
                                   f"exactly one")
            try:
                verdicts[inst] = json.loads(rows[0])
            except ValueError as exc:
                raise RuntimeError(f"{inst}'s TYPE_CLAUSE line is not JSON: {rows[0]!r}") from exc
        by_verdict = {row.get("verdict"): inst for inst, row in verdicts.items()}
        if set(by_verdict) != {"rejected", "accepted"}:
            raise RuntimeError(f"the halves' verdicts were "
                               f"{sorted(row.get('verdict') for row in verdicts.values())}, "
                               f"expected exactly one rejected and one accepted")
        rejected, accepted = by_verdict["rejected"], by_verdict["accepted"]
        rj, ac = verdicts[rejected], verdicts[accepted]

        prompt = rj.get("prompt") or ""
        if not prompt.startswith("[x] Type clause: shared"):
            raise RuntimeError(f"{rejected}'s prompt is {prompt!r}, not an [x] Type clause: "
                               f"shared one")
        if "Normal" not in prompt:
            raise RuntimeError(f"the type-clause prompt did not name Normal: {prompt!r}")
        if rj.get("memorialize") is not True or rj.get("sound26") is not True:
            raise RuntimeError(f"{rejected}'s verdict lacks memorialize/sound26: {rj}")
        if rj.get("unresolve_area") != "route_1":
            raise RuntimeError(f"{rejected}'s unresolve_area is "
                               f"{rj.get('unresolve_area')!r}, not 'route_1' — the area the "
                               f"capture was rejected in, and the one the client re-resolves")
        marker(results[rejected],
               re.escape(f"MEMORIAL {rj['key']} box12=true hp=0"), "rejected Box 12 receipt")

        rj_seen = seen_counters(results[rejected], f"{rejected} SEEN line")
        ac_seen = seen_counters(results[accepted], f"{accepted} SEEN line")
        if rj_seen.get("force_faint", 0) < 1 or rj_seen.get("memorialize", 0) < 1:
            raise RuntimeError(f"{rejected}'s SEEN counters are {rj_seen}; the rejected capture "
                               f"had to be force-fainted and memorialized")
        if ac_seen.get("force_faint", 0) or ac_seen.get("memorialize", 0):
            raise RuntimeError(f"{accepted}'s SEEN counters are {ac_seen}; the accepted half is "
                               f"told SE_BOO and nothing else")
        if ac.get("force_faint") is not False:
            raise RuntimeError(f"{accepted}'s verdict reports force_faint={ac.get('force_faint')!r}")
        if ac.get("type_prompt"):
            raise RuntimeError(f"{accepted} got the rejection prompt too: {ac['type_prompt']!r}")
        if ac.get("unresolve_area"):
            raise RuntimeError(f"{accepted} was sent unresolve_area ({ac['unresolve_area']!r}); "
                               f"only the rejected half is told to retry")

        status = self._status() or {}
        keyed = [entry for entry in (status.get("links") or [])
                 if {entry.get("a_key"), entry.get("b_key")} & {rj.get("key"), ac.get("key")}]
        if keyed:
            raise RuntimeError(f"a clause-rejected pair still formed a link: {keyed}")
        pending = (status.get("pending_captures") or {}).get("route_1") or {}
        if set(pending) != {accepted}:
            raise RuntimeError(f"route_1's pending captures are {sorted(pending)}, expected "
                               f"only the accepted half {accepted}")
        if pending[accepted].get("key") != ac.get("key"):
            raise RuntimeError(f"route_1 holds {pending[accepted].get('key')} for {accepted}, "
                               f"expected {ac.get('key')}")
        area_state = (status.get("area_states") or {}).get("route_1")
        if area_state != f"pending_{rejected}":
            raise RuntimeError(f"area_states.route_1 is {area_state!r}, expected "
                               f"pending_{rejected}")
        document = self._reconnect_document()
        retry = (document.get("retry_areas") or {}).get(rejected) or []
        if "route_1" not in retry:
            raise RuntimeError(f"retry_areas[{rejected}] is {retry}; the rejection has to leave "
                               f"route_1 retryable")
        self._pydec_note(f"D-5 {rejected} rejected for {prompt!r} and left route_1 pending_"
                         f"{rejected} (retry_areas); {accepted} holds {ac.get('key')} pending")

        for inst, key, boxed in ((rejected, rj["key"], "memorial"), (accepted, ac["key"], "current")):
            sram, party, current_box, codec = self._saved_gen1_party(inst)
            in_party = [codec.key(mon) for mon in party]
            in_box = [codec.key(mon) for mon in current_box]
            if key in in_party:
                raise RuntimeError(f"{inst}'s saved party still holds {key}: {in_party}")
            if boxed == "memorial":
                if key in in_box:
                    raise RuntimeError(f"{inst}'s current box still holds the rejected {key}: "
                                       f"{in_box}")
                verdict = codec.verify_boxes(sram)
                box12 = verdict["boxes"][12]
                if not box12["valid"]:
                    raise RuntimeError(f"{inst}'s Box 12 checksum does not match (stored "
                                       f"{box12['stored']:02X}, calculated "
                                       f"{box12['calculated']:02X})")
                memorial = codec.decode_box(
                    sram[box12["offset"]:box12["offset"] + codec.BOX_SIZE])
                if [(codec.key(mon), mon["hp"]) for mon in memorial] != [(key, 0)]:
                    raise RuntimeError(f"{inst}'s Box 12 holds "
                                       f"{[(codec.key(mon), mon['hp']) for mon in memorial]}, "
                                       f"expected {key} at hp 0")
                self._pydec_note(f"{inst} saved Box 12 holds the rejected {key} at HP 0")
            else:
                if not any(codec.key(mon) == key for mon in current_box):
                    raise RuntimeError(f"{inst}'s current box is {in_box}, expected the "
                                       f"quarantined {key}")
                self._pydec_note(f"{inst} saved current box holds the quarantined pending {key}")
        for inst in ("a", "b"):
            marker(results[inst], r"SAVE_WITNESS type_clause_new", f"{inst} save witness")

    def assert_species_clause_new_saved(self, results):
        """D-4: A's pending capture, B's reroll (or its absence), and the link that follows.

        Shared half: A's verdict has capture=1 and no force_faint; B echoes the species number
        the runner wrote (A's own numbering, `_species_release`) and logs one ENCOUNTER line per
        battle; exactly one PATH line decides the branch; the pair is ALIVE on route_1 with both
        keys; both halves saved starter + own catch with no copy left in the current box.

        Observed half (`PATH reroll_observed`): every REROLL_SEEN names A's species, its prompt
        is the emoji-free "Dupes clause: <NAME> -- reroll!" the server queues
        (server/state.py:1726-1738), and events.json carries the matching `reroll` row whose
        text carries the 🔁 prefix (server/server.py:1833-1834). `dead_zone` must not appear at
        all: B's RUN from a notified duplicate is exempted by `dupe_notified_areas`
        (state.py:1866-1872) and A simply caught.

        Unobserved half: the same checks with the reroll asserted ABSENT, so an unobserved PASS
        is still a complete account of what happened. The runner re-runs the whole scenario in
        that case (`run_scenario_with_rng_retry`) and says so in its own line.
        """
        for process in self.emus:
            process.wait(timeout=30)
        a_text, b_text = results["a"], results["b"]
        release = getattr(self, "_species_release", None)
        if not release:
            raise RuntimeError("the live release leg never ran; B's receipt cannot be read "
                               "against a species the runner never confirmed")

        def clause_line(inst, player):
            rows = re.findall(r"^SPECIES_CLAUSE (\{.*\})$", results[inst], re.M)
            if len(rows) != 1:
                raise RuntimeError(f"{inst} logged {len(rows)} SPECIES_CLAUSE line(s), "
                                   f"expected exactly one")
            try:
                row = json.loads(rows[0])
            except ValueError as exc:
                raise RuntimeError(f"{inst}'s SPECIES_CLAUSE line is not JSON: {rows[0]!r}") from exc
            if row.get("player") != player:
                raise RuntimeError(f"{inst}'s SPECIES_CLAUSE names player {row.get('player')!r}")
            return row

        aj = clause_line("a", "a")
        bj = clause_line("b", "b")
        pending = marker(a_text, r"PENDING_CAPTURE (\S+) species=(\d+) level=\d+",
                         "A pending capture")
        if pending.group(1) != release["key"] or int(pending.group(2)) != release["species"]:
            raise RuntimeError(f"A's PENDING_CAPTURE is {pending.group(1)} species="
                               f"{pending.group(2)}, but the runner released B with "
                               f"{release}")
        if aj.get("capture") != 1 or aj.get("force_faint"):
            raise RuntimeError(f"A's verdict is capture={aj.get('capture')} "
                               f"force_faint={aj.get('force_faint')}; the first catcher keeps "
                               f"its capture and is never force-fainted")
        marker(b_text, re.escape(f"A_PENDING species={release['species']}"), "B's echo of the "
               "runner's mark")
        if bj.get("dupe_species") != release["species"]:
            raise RuntimeError(f"B's verdict says dupe_species={bj.get('dupe_species')}, the "
                               f"runner wrote {release['species']}")

        encounters = [(int(i), int(s), d == "true") for i, s, d
                      in re.findall(r"^ENCOUNTER (\d+) species=(\d+) dupe_of_a=(\w+)$",
                                    b_text, re.M)]
        if not encounters:
            raise RuntimeError("B logged no ENCOUNTER line; the reroll decision has no receipt")
        paths = re.findall(r"^PATH (reroll_\w+)$", b_text, re.M)
        if len(paths) != 1:
            raise RuntimeError(f"B logged {len(paths)} PATH line(s), expected exactly one")
        path = paths[0]
        if path not in ("reroll_observed", "reroll_unobserved"):
            raise RuntimeError(f"B's PATH is {path!r}")
        reroll_seen = re.findall(r"^REROLL_SEEN species=(\d+) prompt=(.*)$", b_text, re.M)
        events = self._reconnect_events()  # newest-first: server.py:1508 appendleft
        reroll_rows = [row for row in events if row.get("type") == "reroll"]
        if [row for row in events if row.get("type") == "dead_zone"]:
            raise RuntimeError("events.json carries a dead_zone row; the notified duplicate's "
                               "RUN is exempt (state.py:1866-1872) and A caught")
        if path == "reroll_observed":
            if not reroll_seen:
                raise RuntimeError("PATH says reroll_observed but no REROLL_SEEN line exists")
            for species_text, prompt in reroll_seen:
                if int(species_text) != release["species"]:
                    raise RuntimeError(f"the reroll ran on species {species_text}, not A's "
                                       f"{release['species']}")
                if not re.fullmatch(r"Dupes clause: .+ -- reroll!", prompt):
                    raise RuntimeError(f"the reroll prompt is {prompt!r}, not the server's "
                                       f"emoji-free 'Dupes clause: <NAME> -- reroll!'")
            if not reroll_rows:
                raise RuntimeError("PATH says reroll_observed but events.json has no reroll row")
            if not any("🔁" in (row.get("text") or "") for row in reroll_rows):
                raise RuntimeError(f"the reroll row carries no 🔁 prefix: {reroll_rows}")
            # ORDER: every dupe encounter (and every reroll) comes before the non-dupe catch.
            # The count is the same thing seen from two sides — B's own reroll counter and the
            # prompts it received — so a mismatch means one side of the story is wrong.
            dupe_at = [i for i, (_n, _s, dupe) in enumerate(encounters) if dupe]
            plain_at = [i for i, (_n, _s, dupe) in enumerate(encounters) if not dupe]
            if not dupe_at or not plain_at or max(dupe_at) > min(plain_at):
                raise RuntimeError(f"the encounter order is {[d for _n, _s, d in encounters]}, "
                                   f"not the dupe escape(s) followed by the catch")
            if bj.get("rerolls") != len(reroll_seen):
                raise RuntimeError(f"B's verdict says {bj.get('rerolls')} reroll(s) but its "
                                   f"receipt carries {len(reroll_seen)} REROLL_SEEN line(s)")
            linked_at = [index for index, row in enumerate(events) if row.get("type") == "linked"]
            reroll_at = [index for index, row in enumerate(events) if row.get("type") == "reroll"]
            if linked_at and min(reroll_at) < max(linked_at):
                raise RuntimeError(f"events.json puts a reroll row NEWER than the link row "
                                   f"(newest-first: reroll at {min(reroll_at)}, linked at "
                                   f"{max(linked_at)}); the catch has to follow the rerolls")
            self._pydec_note(f"D-4 reroll observed: {len(reroll_seen)} reroll(s) on species "
                             f"{release['species']}, events.json row present")
        else:
            if reroll_seen or reroll_rows:
                raise RuntimeError(f"PATH says {path} but the receipt has {len(reroll_seen)} "
                                   f"REROLL_SEEN line(s) and events.json {len(reroll_rows)} "
                                   f"reroll row(s)")
            self._pydec_note(f"D-4 reroll not observed: B met the other Route 1 species on "
                             f"{len(encounters)} battle(s); D-4's branch stays partial")

        status = self._status() or {}
        live = [entry for entry in (status.get("links") or []) if entry.get("area_id") == "route_1"]
        if (len(live) != 1 or live[0].get("status") != "alive"
                or live[0].get("a_key") != aj.get("key") or live[0].get("b_key") != bj.get("key")):
            raise RuntimeError(f"route_1's link is {live}, expected an alive pair "
                               f"{aj.get('key')} <-> {bj.get('key')}")
        durable = [entry for entry in self._links_json() if entry.get("area_id") == "route_1"]
        if (len(durable) != 1 or durable[0].get("status") != "alive"
                or durable[0].get("a", {}).get("key") != aj.get("key")
                or durable[0].get("b", {}).get("key") != bj.get("key")):
            raise RuntimeError(f"the durable route_1 pair is {durable}")
        for inst in ("a", "b"):
            marker(results[inst], re.escape("SAVE_WITNESS species_clause_new_" + inst),
                   f"{inst} save witness")
        for inst, key in (("a", aj.get("key")), ("b", bj.get("key"))):
            _sram, party, current_box, codec = self._saved_gen1_party(inst)
            keys = [codec.key(mon) for mon in party]
            if keys != [self._boot_keys[inst], key]:
                raise RuntimeError(f"{inst}'s saved party is {keys}, expected starter + {key}")
            if any(codec.key(mon) == key for mon in current_box):
                raise RuntimeError(f"{inst}'s current box still holds {key}; the un-quarantine "
                                   f"party_mon should have retrieved it")
            self._pydec_note(f"{inst} saved starter + linked {key}, current box clear")

    def assert_poison_new_saved(self, results):
        """S-4's poison half: the one blackout that never goes through a battle.

        B's receipt is the game-side story: the forest hunt's PSN status byte (bit 3 of
        wPartyMon1Status, constants/battle_constants.asm:64), the poison_faint signal site
        (wWhichPokemon = slot 0), one faint and one whiteout on the wire, the game's own
        blackout flag ($FF in wOutOfBattleBlackout, poison.asm:107-114), the BCD-halved money
        and the HealParty that ends ResetStatusAndHalveMoneyOnBlackout. A idles on the town
        fixture and only saves.

        WHAT THE SERVER MUST NOT DO. This scenario forms no link at all, so `_handle_whiteout`
        (server/state.py:1982-2062) walks an EMPTY link table: nothing is retired, so no
        memorial is written (`_queue_memorialize` is called only inside that loop), no
        `game_over` is queued (that needs `retired and not player_picks`, :2044-2057) and no
        rebuild is planned (:2359-2360 over no links). events.json therefore carries B's
        `faint` and `whiteout` rows, the whiteout newer, and no `memorialize` row; neither
        receipt carries `GAME_OVER RX game_over`. `no_catch` rows are deliberately NOT asserted
        absent: the forest walk RUNs from incidental Route 1 battles, which is a real dead zone
        for a route this scenario does not care about.
        """
        for process in self.emus:
            process.wait(timeout=30)
        a_text, b_text = results["a"], results["b"]
        marker(a_text, r"(?m)^POISON_IDLE a$", "A idle receipt")
        marker(a_text, r"SAVE_WITNESS poison_new_a", "A save witness")
        baseline = marker(b_text, r"POISON_BASELINE key=(\S+) faint=(\d+) whiteout=(\d+) "
                                  r"no_catch=(\d+) signals=(\d+)", "B baseline")
        starter = baseline.group(1)
        psn = marker(b_text, r"POISON_PSN encounters=(\d+) steps=(\d+) status=([0-9A-F]{2})",
                     "B PSN receipt")
        if int(psn.group(1)) < 1:
            raise RuntimeError(f"POISON_PSN reports {psn.group(1)} encounter(s); a wild foe "
                               f"inflicted the poison, so the hunt met at least one")
        if int(psn.group(3), 16) & 0x08 == 0:
            raise RuntimeError(f"wPartyMon1Status read ${psn.group(3)}, which carries no PSN "
                               f"bit ($08)")
        money_before = marker(b_text, r"(?m)^MONEY_BEFORE (\d+)$", "B money before")
        marker(b_text, r"POISON_FAINT_SITE frame=\d+ slot=0", "B poison faint site")
        marker(b_text, re.escape("TX faint " + starter), "B faint TX")
        marker(b_text, r"(?m)^TX whiteout x1$", "B whiteout TX")
        marker(b_text, r"BLACKOUT_FLAG frame=\d+ value=FF", "B blackout flag")
        site = marker(b_text, r"BLACKOUT_SITE map=(\d+) x=(\d+) y=(\d+)", "B blackout site")
        if site.groups() != ("0", "5", "6"):
            raise RuntimeError(f"B blacked out to map={site.group(1)} "
                               f"({site.group(2)},{site.group(3)}), not Pallet Town (0, 5, 6)")
        money_after = marker(b_text, r"(?m)^MONEY_AFTER (\d+)$", "B money after")
        expected = int(money_before.group(1)) // 2
        if int(money_after.group(1)) != expected:
            raise RuntimeError(f"money went {money_before.group(1)} -> {money_after.group(1)}; "
                               f"the blackout halves it to {expected}")
        marker(b_text, r"MONEY_HALVED before=\d+ after=\d+", "B money-halved receipt")
        healed = marker(b_text, r"PARTY_HEALED key=(\S+) hp=(\d+)", "B healed party")
        if healed.group(1) != starter or int(healed.group(2)) <= 0:
            raise RuntimeError(f"PARTY_HEALED named {healed.group(1)} at hp={healed.group(2)}; "
                               f"the blackout ends in HealParty on {starter}")
        marker(b_text, r"SIGNAL_ORDER poison_faint->blackout ok", "B signal order")
        marker(b_text, r"SAVE_WITNESS poison_new_b", "B save witness")

        rows = self._reconnect_events()  # newest-first: server.py:1508 appendleft
        whiteout_rows = [i for i, row in enumerate(rows) if row.get("type") == "whiteout"]
        faint_rows = [i for i, row in enumerate(rows) if row.get("type") == "faint"]
        if len(whiteout_rows) != 1 or len(faint_rows) != 1:
            raise RuntimeError(f"events.json carries {len(faint_rows)} faint and "
                               f"{len(whiteout_rows)} whiteout row(s), expected one of each")
        if rows[whiteout_rows[0]].get("player") != "b" or rows[faint_rows[0]].get("player") != "b":
            raise RuntimeError(f"the faint/whiteout rows are not B's: "
                               f"{rows[faint_rows[0]].get('player')} / "
                               f"{rows[whiteout_rows[0]].get('player')}")
        if whiteout_rows[0] > faint_rows[0]:
            raise RuntimeError("the whiteout row is older than the faint row; the whiteout has "
                               "to follow the poison faint")
        memorial_rows = [row for row in rows if row.get("type") == "memorialize"]
        if memorial_rows:
            raise RuntimeError(f"events.json carries {len(memorial_rows)} memorialize row(s): "
                               f"{memorial_rows}; a whiteout over an empty link table retires "
                               f"nothing")
        if re.search(r"GAME_OVER RX game_over", a_text + b_text):
            raise RuntimeError("a client was told the run is over; with no link there was "
                               "nothing to rebuild and nothing to end")
        # The ROW above cannot see an orphan command: server.py:1945-1954 logs `memorialize`
        # only when a link's status transitions to memorial, so a command aimed at a mon the
        # server never linked leaves no row at all. The receipts and the link table are where
        # that shows.
        for inst, text in (("a", a_text), ("b", b_text)):
            for command in ("memorialize", "rebuild_start", "rebuild_done", "party_mon"):
                if re.search(rf"(?m)^RX {command}\b", text):
                    raise RuntimeError(f"{inst} received a {command} command; no pair was ever "
                                       f"linked, so the server had nothing to bury, rebuild or "
                                       f"retrieve")
        rows = self._links_json()
        formed = [entry for entry in rows if is_formed_link(entry)]
        if formed:
            raise RuntimeError(f"links.json carries {len(formed)} formed link(s) "
                               f"{[entry.get('area_id') for entry in formed]}; this scenario "
                               f"forms none")
        dead_zones = len(rows) - len(formed)
        if dead_zones:
            self._pydec_note(f"links.json carries {dead_zones} dead-zone record(s) "
                             f"(no_catch areas) — area locks, not links")
        self._pydec_note(f"S-4 poison: PSN ${psn.group(3)} after {psn.group(1)} encounters, one "
                         f"faint then whiteout on the wire, no memorial; the empty link table "
                         f"left the server idle")

        sram, party, _box, codec = self._saved_gen1_party("b")
        if len(party) != 1:
            raise RuntimeError(f"B's saved party holds {len(party)} mon(s), expected the lone "
                               f"starter")
        mon = party[0]
        if codec.key(mon) != starter:
            raise RuntimeError(f"B's saved party holds {codec.key(mon)}, not the poisoned "
                               f"starter {starter}")
        if mon["hp"] != mon["max_hp"] or mon["hp"] <= 0:
            raise RuntimeError(f"B's saved starter is at {mon['hp']}/{mon['max_hp']}; the "
                               f"blackout's HealParty restores it")
        if mon["status"] != 0:
            raise RuntimeError(f"B's saved starter still carries status ${mon['status']:02X}; "
                               f"the PSN bit had to be cleared by the same heal")
        money_saved = saved_money(sram)
        if money_saved != int(money_after.group(1)):
            raise RuntimeError(f"B's saved money is {money_saved}; the blackout left "
                               f"{money_after.group(1)} in RAM")
        self._pydec_note(f"B saved the starter at {mon['hp']}/{mon['max_hp']} status 0 and "
                         f"money {money_saved} (the receipt's MONEY_AFTER)")
        self._saved_gen1_party("a")  # A's save has to qualify too

    def assert_trade_new(self, results):
        """T-3/T-4: durable swapped halves plus each cartridge's actual saved party."""
        from pathlib import Path

        if REPO not in sys.path:
            sys.path.insert(0, REPO)  # python tools/e2e_duo.py otherwise has tools/ at sys.path[0]

        from server.adapters import gen1_codec as codec
        from tests.unit import protocol_schema

        for process in self.emus:
            process.wait(timeout=30)  # client.exit flushes CartRAM to its per-instance SaveRAM
        with open(os.path.join(self.data_dir, "links.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        matches = [entry for entry in document.get("links", [])
                   if entry.get("area_id") == "route_1" and entry.get("status") == "alive"]
        if len(matches) != 1:
            raise RuntimeError(f"expected one durable alive route_1 link, got {matches}")
        before_a, before_b = self._trade_before
        link = matches[0]
        if link["a"]["key"] != before_b or link["b"]["key"] != before_a:
            raise RuntimeError(f"links.json halves were not swapped: {link['a']['key']} / {link['b']['key']}")

        for inst, incoming, partner in (("a", before_b, "b"), ("b", before_a, "a")):
            save = Path(self._saveram_dir(inst)) / self._gen1_save_name(inst)
            sram = save.read_bytes()
            if len(sram) != codec.SRAM_SIZE:
                raise RuntimeError(f"{inst} SaveRAM is {len(sram)} bytes, expected {codec.SRAM_SIZE}")
            start = codec.SRAM_LAYOUT["sPartyData"]
            party = codec.decode_party(sram[start:start + codec.PARTY_LAYOUT["size"]])
            if len(party) != 2 or codec.key(party[-1]) != incoming:
                raise RuntimeError(f"{inst} saved party lacks partner's mon in LAST slot: "
                                   f"{[codec.key(mon) for mon in party]}")
            dv, ot, species = incoming.split(":")
            received = party[-1]
            if (received["dvs"]["raw"] != int(dv, 16)
                    or received["ot_id"] != int(ot, 16)
                    or received["species"] != int(species, 16)):
                raise RuntimeError(f"{inst} received mon identity fields differ from {incoming}")
            # Hello locks this name in player_identity; trainer_names is an older field
            # that Gen 1 never populates (server/state.py:953-989,3071-3073).
            original_ot = document.get("player_identity", {}).get(partner, {}).get("trainer_name")
            if not original_ot or received["ot_name"] != original_ot:
                raise RuntimeError(f"{inst} received OT name {received['ot_name']!r}, "
                                   f"expected partner's original {original_ot!r}")
            trade_lines = [json.loads(line[3:]) for line in results[inst].splitlines()
                           if line.startswith("TX {") and '"event":"trade_done"' in line]
            if len(trade_lines) != 1:
                raise RuntimeError(f"{inst} sent {len(trade_lines)} trade_done lines, expected one")
            report = trade_lines[0]
            errors = protocol_schema.validate_event(report)
            if errors or report.get("new_key") != incoming or report.get("slot") != len(party) - 1:
                raise RuntimeError(f"{inst} trade_done disagrees with saved party: {report}, {errors}")
            self._pydec_note(f"{inst} saved LAST slot {incoming}, OT {original_ot}, trade_done valid")
        self._pydec_note(f"T-3/T-4 durable swapped halves: a={before_b}, b={before_a}")

    def assert_trade_decline_saved(self, results):
        """T-3/T-4's NO path: the partner declined, so no blob was staged and no party moved.

        The server exposes no pending-trade field on /api/status (recorded finding), so the
        decline is evidenced by what is ABSENT on the wire — no RX apply_trade, no TRADE_DONE,
        no PARTNER_ACCEPTED — by the durable route_1 pair keeping both pre-trade keys, and by
        both saved parties still holding [starter, linked] with the linked half on the side
        that caught it.
        """
        for process in self.emus:
            process.wait(timeout=30)  # client.exit flushes CartRAM to its per-instance SaveRAM
        for inst, text in results.items():
            marker(text, r"DECLINE_OVERWORLD", f"{inst} decline return")
            marker(text, r"SAVE_WITNESS trade_decline_new", f"{inst} save witness")
            for absent in ("RX apply_trade", "TRADE_DONE ", "PARTNER_ACCEPTED"):
                if absent in text:
                    raise RuntimeError(f"{inst}'s declined trade receipt carries {absent!r}")
        marker(results["b"], r"TRADE_DECLINED", "B decline answer")
        self._pydec_note("both receipts: DECLINE_OVERWORLD + SAVE_WITNESS trade_decline_new; "
                         "absent: RX apply_trade / TRADE_DONE / PARTNER_ACCEPTED")

        before_a, before_b = self._trade_before
        matches = [entry for entry in self._links_json()
                   if entry.get("area_id") == "route_1" and entry.get("status") == "alive"]
        if len(matches) != 1:
            raise RuntimeError(f"expected one durable alive route_1 link, got {matches}")
        link = matches[0]
        if link["a"]["key"] != before_a or link["b"]["key"] != before_b:
            raise RuntimeError(f"the declined trade moved the durable pair: {link['a']['key']} / "
                               f"{link['b']['key']} != {before_a} / {before_b}")
        self._pydec_note(f"durable route_1 pair unchanged: a={before_a} b={before_b}")

        for inst in ("a", "b"):
            _sram, party, _box, codec = self._saved_gen1_party(inst)
            keys = [codec.key(mon) for mon in party]
            want = [self._boot_keys[inst], self._link_keys[inst]]
            if keys != want:
                raise RuntimeError(f"{inst}'s saved party {keys} is not the pre-trade pair "
                                   f"{want}: the declined trade staged a blob")
            own = before_a if inst == "a" else before_b
            self._pydec_note(f"{inst} saved party {keys}; linked half {own} on {inst}, no swap")

    def assert_admit_randomized_new(self):
        """The server verdict and durable hello events, not the TCP connected bit, decide F-4."""
        def both_verdicts():
            status = self._status() or {}
            players = status.get("players") or {}
            a, b = players.get("a") or {}, players.get("b") or {}
            # server.py:2300-2303 reports "admitted" with an EMPTY reason for a player that has
            # no verdict yet, so A must carry a real one: B's rejection can land before A's hello
            # (a pure PureRed↔PureBlue lane did exactly that) and a no-verdict A is not admitted.
            return (status if a.get("admission") == "admitted" and a.get("admission_reason")
                    and b.get("admission") == "rejected" else None)

        status = self.wait_for("randomized A admitted and un-randomized companion B rejected",
                               both_verdicts, 180)
        a, b = status["players"]["a"], status["players"]["b"]
        if a.get("admission_reason") != "cartridge matches the contract":
            raise RuntimeError(f"A admission reason differs: {a.get('admission_reason')!r}")
        got, want = self._admit_fingerprints["reported_b"], self._admit_fingerprints["expected_b"]
        reason = b.get("admission_reason", "")
        if ("not the cartridge built for player b" not in reason
                or got[:12] not in reason or want[:12] not in reason):
            raise RuntimeError(f"B wrong-cartridge reason lacks the fingerprint prefixes: {reason!r}")
        # server.py:1574-1591 returns only noop on a rejected hello. Status is the public
        # evidence that no B party snapshot or trainer identity was adopted.
        if b.get("party_keys") or b.get("trainer_name") or b.get("current_area_id"):
            raise RuntimeError(f"rejected B adopted party/identity/area: {b}")

        def receipts():
            a_text = read_result(self.scenario, "a") or ""
            b_text = read_result(self.scenario, "b") or ""
            if all(tag in text for text in (a_text, b_text)
                   for tag in ("ADMIT_MAP 40 5 6", "ADMIT_PARTY ", "HELLO_RECEIPT ")):
                return a_text, b_text
            return None

        a_text, b_text = self.wait_for("live town save and hello receipts", receipts, 120)
        for player, text in (("a", a_text), ("b", b_text)):
            party_line = next((line for line in text.splitlines() if line.startswith("ADMIT_PARTY ")), "")
            if not party_line or int(party_line.split()[1]) < 1:
                raise RuntimeError(f"{player} booted without a live party: {party_line!r}")

        def durable_events():
            path = os.path.join(self.data_dir, "events.json")
            if not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8") as handle:
                rows = json.load(handle)
            hellos = [row for row in rows if row.get("type") == "hello"]
            return hellos if len(hellos) >= 2 else None

        hellos = self.wait_for("durable admitted/rejected hello events", durable_events, 30)
        a_events = [row for row in hellos if row.get("player") == "a"]
        b_events = [row for row in hellos if row.get("player") == "b"]
        if (len(a_events) != 1 or not a_events[0].get("text", "").startswith("Connected (")
                or len(b_events) != 1 or not b_events[0].get("text", "").startswith("REJECTED — ")):
            raise RuntimeError(f"unexpected durable hello events: {hellos}")
        with open(os.path.join(self.data_dir, "slink.log"), encoding="utf-8") as handle:
            log_text = handle.read()
        if ("[a] admission: admitted — cartridge matches the contract" not in log_text
                or "[b] admission: rejected — " + reason not in log_text):
            raise RuntimeError("slink.log omitted an admission transition")
        self._pydec_note(f"F-4 public verdicts: A admitted, un-randomized companion B rejected; "
                         f"expected={want[:12]} reported={got[:12]} (admission-only, no save mutation)")
        self._live_complete["admit_randomized_new"] = True

    def assert_admit_randomized_saved(self, results):
        """F-4's saved half, with explicit provenance for both cartridges.

        A runs the STAGED randomized companion Red, whose SaveRAM carries the filename-derived
        name BizHawk resolves for a hash it does not know (`_gen1_save_name`, the one name it was
        seeded under), qualified against that staged ROM. B -- the un-randomized companion build --
        shows the rejected cartridge's save was not touched by the server: party and current box
        unchanged from the seed it booted with, its rejection the only durable record, and none
        of its keys anywhere in links.json.
        """
        if not self._live_complete.get("admit_randomized_new"):
            raise RuntimeError("admit_randomized_new's live verdict did not complete")
        from pathlib import Path

        for process in self.emus:
            process.wait(timeout=30)  # client.exit flushes CartRAM

        a_rom = self._admit_roms["a"]
        a_save = self._gen1_save_name("a")
        a_path = Path(self._saveram_dir("a")) / a_save
        launched = self._launch_times.get("a")
        if not launched or a_path.stat().st_mtime < launched:
            raise RuntimeError(f"A's randomized SaveRAM was not written after its launch: {a_path}")
        a_sram, a_party, _a_box, codec = self._saved_gen1_party("a", rom=a_rom, save_name=a_save)
        if not a_party:
            raise RuntimeError("A's admitted save holds no party")
        self._pydec_note(f"phase initial: {os.path.relpath(a_path, REPO)} written after launch, "
                         f"qualifies against {a_rom}, party {[codec.key(m) for m in a_party]}")

        b_sram, b_party, b_box, _codec = self._saved_gen1_party("b")
        seed = Path(self._fixture_save_path("b")).read_bytes()
        party_start = codec.SRAM_LAYOUT["sPartyData"]
        box_start = codec.SRAM_LAYOUT["sCurBoxData"]
        seed_party = codec.decode_party(seed[party_start:party_start + codec.PARTY_LAYOUT["size"]])
        seed_box = codec.decode_box(seed[box_start:box_start + codec.BOX_SIZE])
        for what, got, want in (("party", b_party, seed_party), ("current box", b_box, seed_box)):
            if [codec.key(mon) for mon in got] != [codec.key(mon) for mon in want]:
                raise RuntimeError(
                    f"rejected B's saved {what} changed vs the seed it booted with: "
                    f"{[codec.key(m) for m in got]} != {[codec.key(m) for m in want]}")

        hellos = [row for row in self._reconnect_events()
                  if row.get("type") == "hello" and row.get("player") == "b"]
        if len(hellos) != 1 or not hellos[0].get("text", "").startswith("REJECTED — "):
            raise RuntimeError(f"B's rejection is not the durable record: {hellos}")
        document = Path(self.data_dir, "links.json").read_text(encoding="utf-8")
        leaked = sorted(key for key in {codec.key(mon) for mon in b_party + b_box}
                        if key in document)
        if leaked:
            raise RuntimeError(f"links.json carries rejected B's keys: {leaked}")
        self._pydec_note("B's saved party/current box identical to the seed; one durable "
                         "REJECTED hello; no B key in links.json")

    def assert_dead_zone_new(self):
        """D-3: A's RUN sends no_catch and locks route_1; B's later catch there is retired.

        B is released only once the SERVER reports the lock, so "B caught inside a dead zone"
        is a fact. Retirement is read two ways: the server's links.json carries the DEAD entry
        with cause dead_zone, and B's result file shows the caught key at HP 0 in the party
        (FAINTED, written by the client's force_faint) before memorialize moves it out.
        """
        self._go_one("a")
        area = self.wait_for("A's failed encounter to lock an area", self._dead_zone_area,
                        self.cfg["timeout"])
        if area != self._hunt_area:
            raise RuntimeError(f"A locked {area!r}, expected {self._hunt_area}")
        self.wait_for("A to report its no_catch",
                 lambda: "NO_CATCH" in (read_result(self.scenario, "a") or ""), 60)
        print(f"[duo] DEAD ZONE FROM REAL PLAY (new client): {area}")

        self._go_one("b")
        def b_caught():
            key = self._caught("b")
            if not key and _has_exact_rng_miss(read_result(self.scenario, "b")):
                raise GameRngMiss("B missed its sole ball inside the dead zone")
            return key

        b_key = self.wait_for("B to catch inside the dead zone", b_caught, self.cfg["timeout"])
        self._deadzone_b_key = b_key
        self.wait_for("B's client to force-faint the refused capture",
                 lambda: "FAINTED " in (read_result(self.scenario, "b") or ""), 300)
        retired = "RETIRED " in (read_result(self.scenario, "b") or "")
        print(f"[duo] B caught {b_key} in the dead zone; force-fainted"
              f"{', memorialized' if retired else ' (memorialize not observed)'}")

        st = self._status() or {}
        area_state = (st.get("area_states") or {}).get(area)
        problems = []
        if area_state != "dead_zone":
            problems.append(f"{area} is {area_state!r} after B's catch, not dead_zone")
        for link in st.get("links") or []:
            if b_key in (link.get("a_key"), link.get("b_key")) and link.get("status") == "alive":
                problems.append(f"B's dead-zone catch {b_key} formed a LIVE link")
        if ((st.get("pending_captures") or {}).get(area) or {}).get("b"):
            problems.append(f"B's dead-zone catch is pending in {area}")
        dead = [lnk for lnk in self._links_json()
                if lnk.get("area_id") == area and lnk.get("status") == "dead"
                and lnk.get("cause") == "dead_zone"]
        if not dead:
            problems.append(f"links.json has no DEAD/dead_zone entry for {area}: "
                            f"{self._links_json()}")
        if problems:
            raise RuntimeError("; ".join(problems))
        print(f"[duo] links.json: {len(dead)} dead_zone entry for {area}; B's {b_key} retired")

    def set_pokeballs(self):
        """Faints are suppressed server-side until the nuzlocke is active (pokéballs obtained)."""
        for p in ("a", "b"):
            r = api(self.http_port, "POST", "/api/debug/set_pokeballs",
                    {"player": p, "value": True})
            if not r.get("ok"):
                raise RuntimeError(f"set_pokeballs failed: {r}")
        print("[duo] pokeballs_obtained set for both players")

    def queue_command(self, player, cmd):
        body = dict(cmd)
        body["player"] = player
        r = api(self.http_port, "POST", "/api/debug/queue_command", body)
        if not r.get("ok"):
            raise RuntimeError(f"queue_command failed: {r}")
        print(f"[duo] queued for {player}: {cmd}")

    def wait_results(self):
        def both():
            ra = self._read_receipt("a")
            rb = self._read_receipt("b")
            if ra and "RESULT:" in ra and rb and "RESULT:" in rb:
                return ra, rb
            return None
        return self.wait_for("both RESULT lines", both, self.cfg["timeout"])

    def _wait_gen2_exit_flush(self):
        """RESULT precedes asynchronous client.exit; verify saves only after exit flushes."""
        processes = getattr(self, "emu_by_inst", {})
        if any(not callable(getattr(processes.get(side), "wait", None)) for side in ("a", "b")):
            raise RuntimeError("Gen 2 exit validation requires both emulator processes")
        deadline = time.monotonic() + 30
        for side in ("a", "b"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Gen 2 emulator exit flush deadline exceeded")
            try:
                code = processes[side].wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Gen 2 {side} emulator exit flush timed out") from exc
            if code != 0:
                raise RuntimeError(f"Gen 2 {side} emulator exited abnormally: {code}")

    def _result_path(self, inst):
        return os.path.join(BUILD, f"e2e_{self.artifact_name}_{inst}_result.txt")

    def cleanup(self, passed):
        if self.cfg.get("rr_reset_trade"):
            live = [p.pid for p in self.emus if p.poll() is None]
            if live:
                # A native reset proof permits only a cartridge's own clean client.exit.
                # Preserve the live PIDs, server and data dir for inspection; no taskkill.
                raise RuntimeError(f"RR reset clean exit missing for EmuHawk PID(s) {live}; "
                                   "refusing taskkill")
        for p in self.emus:
            if p.poll() is None:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                               capture_output=True)
        if self.server and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
        for gf in self.go_files.values():
            for path in (gf, gf + ".chord"):
                if os.path.exists(path):
                    os.remove(path)
        self.collect_wire_logs()  # the source lives under the data dir; copy before it goes
        if self.cfg.get("gen3_native_trade"):
            self._archive_native_receipts()
        if (passed and not self.args.keep_data and self.scenario not in GEN2_TRADE_SCENARIOS
                and not self.cfg.get("gen3_rand") and not self.cfg.get("gen3_native_trade")
                and not self.cfg.get("rr_reset_trade") and not self.cfg.get("journal_lock_probe")):
            shutil.rmtree(self.data_dir, ignore_errors=True)
        else:
            print(f"[duo] data dir kept: {self.data_dir}")

    # ── per-scenario orchestration ───────────────────────────────────────────
    def orchestrate(self):
        if getattr(self, "gcfg", {}).get("launch_profile") == "gen2":
            admitted = ("a",) if self.scenario == "gen2_admit_wrong_rom" else ("a", "b")
            if self.scenario in GEN2_C5_REFUSED:
                admitted = tuple(inst for inst, state in GEN2_C5_REFUSED[self.scenario].items() if state == "admitted")
            def both_hellos():
                players = (self._status() or {}).get("players", {})
                return all(players.get(inst, {}).get("connected")
                           and players[inst].get("admission") == "admitted"
                           and any(line.startswith("HELLO ") for line in
                                   (read_result(self.artifact_name, inst) or "").splitlines())
                            for inst in admitted)
            self.wait_for("both admitted Gen 2 hellos", both_hellos, 300)
            if self.scenario in GEN2_C5_REFUSED:
                refused = [inst for inst, state in GEN2_C5_REFUSED[self.scenario].items() if state == "rejected"]

                def refusals():
                    players = (self._status() or {}).get("players", {})
                    return all(players.get(inst, {}).get("admission") == "rejected"
                               and any(line.startswith("HELLO ") for line in
                                       (read_result(self.artifact_name, inst) or "").splitlines())
                               for inst in refused)
                self.wait_for("the server's refusal of each randomized hello", refusals, 300)
            if self.scenario == "gen2_admit_wrong_rom":
                self._gen2_admit_before = self._gen2_admit_snapshot()
            self.go()
            if self.scenario in GEN2_TRADE_SCENARIOS:
                self._release_gen2_trade()
            elif self.scenario in ("gen2_faint_active", "gen2_faint_active_trainer"):
                self._release_gen2_active_faint()
            elif self.scenario == "gen2_reconnect":
                self._orchestrate_gen2_reconnect()
            elif self.scenario == "gen2_soft_reset":
                self._orchestrate_gen2_soft_reset()
            elif self.scenario == "gen2_species_clause":
                self._release_gen2_species()
            return
        # A scenario with its own runner half (the gen3_frlg set) says so by name; nothing in the
        # older rows defines one, so their branches below are untouched.
        own = getattr(self, f"orchestrate_{self.scenario}", None)
        if own:
            return own()
        if self.scenario == "admit_randomized_new":
            self.assert_admit_randomized_new()
            self.go()  # passive clients hold for ~600 frames before RESULT: PASS
            return
        if self.scenario == "ball_gate_new":
            self.assert_ball_gate_new()
            return
        ka, kb = self.wait_keys()
        self._boot_keys = {"a": ka[0], "b": kb[0]}
        self.wait_connected()
        if self.scenario == "reconnect_new":
            self.assert_reconnect_new()
            return
        if self.cfg.get("no_setup"):
            # Deliberately does NOT call set_pokeballs() or inject_link(): these scenarios
            # exist to prove the paths those shortcuts bypass. The fixture carries real
            # Poke Balls, so the gate flips from the client's own bag read.
            if self.scenario == "deadzone":
                self.assert_dead_zone_refusal()
            elif self.scenario == "dupes":
                self.assert_species_clause_rejection()
            elif self.scenario in ("link_new", "linked_faint_bench_new", "linked_faint_active_new",
                                   "linked_faint_bench_battle_new", "explode_bench_battle_new",
                                   "explode_new", "pc_ops_new", "rival_swap_new"):
                self.go()
                self.assert_link_new()
            elif self.scenario in ("type_clause_new", "poison_new"):
                # Both halves hunt (or idle) on their own; every verdict is read off the wire
                # and the saved states, so the runner's only job is to release them together.
                self.go()
            elif self.scenario == "species_clause_new":
                self.go()
                self.assert_species_clause_release()
            elif self.scenario == "whiteout_new":
                # The extra gate is this scenario's own: both halves must be BOXED as far as
                # the server is concerned before either may leave the PC, because the blackout
                # that follows rebuilds only from pairs whose halves are absent from
                # `party_keys` (_alive_pc_mons, server/state.py:2336-2363).
                self.go()
                self.assert_link_new()
                self.assert_whiteout_both_boxed()
            elif self.scenario in ("trade_new", "trade_decline_new"):
                self.go()
                self.assert_link_new()
                before = [entry for entry in self._links_json()
                          if entry.get("area_id") == "route_1" and entry.get("status") == "alive"]
                if len(before) != 1:
                    raise RuntimeError(f"{self.scenario} has no durable post-link_new pair")
                self._trade_before = (before[0]["a"]["key"], before[0]["b"]["key"])
            elif self.scenario == "soft_reset_new":
                # The reset's evidence is that NOTHING changed, so the baseline is taken before
                # either client acts: the end state is compared against it, which is what stops
                # a duplicated or rewritten log from passing as "the same save reconnected".
                # ONE SNAPSHOT, TAKEN BEFORE THE RELEASE. Both halves are quiescent there and
                # nowhere later: `wait_connected` above is gated on both hellos being on the
                # server, and nothing else may happen until the go-file lands. AFTER the release
                # they are NOT — A's body helloed at the checkpoint before the go-file and takes
                # the chord ~55 frames after it, and at unthrottled speed its re-hello is only
                # ~1.8 s behind the first one, so any post-release snapshot races it (H-6).
                # The old code waited post-release for both boot keys' mon_stats, which DEADLOCKED
                # once the chord gate stopped A from resetting (DIAG-SR2): mon_stats is not
                # a client message at all. server/server.py:1685-1687 backfills it from the
                # hello's OWN party snapshot via _cache_mon_info (:1438-1446), which mutates
                # state.mon_stats WITHOUT saving, and the hello handler's only `_save()` is the
                # `_dirty` rom/trainer commit ~30 lines earlier (:1652); ticks never save
                # (server/state.py:395-412). So each hello persists the PREVIOUS hello's stats
                # and the last one's sit in RAM until the next save of any kind — in the passing
                # 2c17161 run that was A's post-reset re-hello (server/state.py:1013-1015,
                # server.log 18:44:28.160, baseline status GET 18:44:28.210). The "pre-reset"
                # baseline was therefore taken AFTER the reset, i.e. vacuous; with the gate in
                # place it can never be taken at all. The flush is reconciled in the oracle.
                # Waited for, not read blind: `connected` flips on a player's first message
                # (server/server.py:1146-1149) and the hello's event row is persisted a few
                # lines later in the handler (:1604, _log_event -> _save_events :1516), so
                # wait_connected returning is not yet proof the rows are on disk.
                def helloed():
                    rows = self._reconnect_events()
                    counts = {inst: sum(row.get("type") == "hello" and row.get("player") == inst
                                        for row in rows) for inst in ("a", "b")}
                    return rows if counts == {"a": 1, "b": 1} else None
                events = self.wait_for("one durable hello row per player", helloed, 30)
                self._pydec_note(f"baseline mon_stats keys: {self._mon_stats_keys()}")
                baseline_bytes = self._links_bytes()
                baseline_path = os.path.join(self.data_dir, "links_baseline.json")
                if baseline_bytes is not None:
                    with open(baseline_path, "wb") as handle:
                        handle.write(baseline_bytes)
                self._reset_baseline = {
                    "status": self._status() or {},
                    "links": self._links_json(),
                    "links_bytes": baseline_bytes,
                    "links_baseline_path": baseline_path,
                    "events": events,
                    "a_hellos": sum(row.get("type") == "hello" and row.get("player") == "a"
                                    for row in events),
                }
                # Release only now: the baseline is already on disk, so BOTH files are ordered
                # after it and the ~55-frame margin between them stops mattering (H-6 defect 2).
                # The .chord file is what lua/tests/duo/duo_gen1_main.lua's body waits on before
                # the reset chord; it is written unconditionally and immediately, because there
                # is nothing left to wait for — a frame-counted Lua deadline (3600) can never be
                # made to cover a wall-clock-bounded Python wait anyway.
                self.go()
                chord_path = self.go_files["a"] + ".chord"
                with open(chord_path, "w") as handle:
                    handle.write("GO\n")
                print("[duo] chord go-file written for a")
            elif self.scenario in ("deadzone_new", "changebox_new"):
                self.assert_dead_zone_new()
            else:
                self.go()
                self.assert_real_link_formed()
            return
        self.set_pokeballs()
        if self.scenario == "infopanel":
            # THREE pairs, not one: 3 pairs (6 rows) + 3 summary rows = 9 rows = 2 pages, which is
            # what makes the pagination half of the scenario meaningful. One pair fits on a single
            # page and would never exercise it.
            for i in range(min(3, len(ka), len(kb))):
                self.inject_link(ka[i], kb[i], area_id=f"duo{i}")
            self.go()
        elif self.scenario in ("faint", "explode_g1"):
            self.inject_link(ka[0], kb[0])
            self.go()
        elif self.scenario == "whiteout":
            # TWO pairs, and the second one is not padding. Measured against the real
            # server: on a cartridge every `faint` beats the `whiteout` to the server, so
            # _handle_whiteout finds nothing left to retire and its only observable effect
            # is the auto-rebuild — which needs an alive, fully-boxed pair to pull back. A
            # boxes its slot-1 half during the scenario; link it here or there is no pair.
            self.inject_link(ka[0], kb[0], area_id="duo0")
            self.inject_link(ka[1], kb[1], area_id="duo1")
            self.go()
        elif self.scenario == "rivalswap":
            # No link needed: the swap is gated on the rival id and the partner having
            # cached blobs, not on a formed pair. Go straight away and let A drive.
            self.go()
        elif self.scenario == "explode":
            self.inject_link(ka[0], kb[0])
            # B must be inside a LIVE battle before A's faint fires the force_explode (a
            # frozen battle savestate can't execute the coerced turn — foe never commits).
            self.wait_for("B inside a live battle",
                     lambda: "IN_BATTLE" in (read_result(self.scenario, "b") or ""), 240)
            self.go()
        elif self.scenario == "boxsync" and self.battery_boot:
            # The GB gens exercise the RULE, not the storage opcodes: link the pair, then let A
            # deposit its own half. The server's _handle_party_to_box is what must send
            # box_mon to B — nothing is injected here, so a broken rule cannot be masked by
            # the harness doing the work itself.
            self.inject_link(ka[0], kb[0])
            self.go()
        elif self.scenario == "boxsync":
            # Symmetric: BOTH sides deposit their slot-1 filler, then withdraw it statless
            # (native OP_DEPOSIT_MON / OP_WITHDRAW_MON; the Lua asserts live in the scenario).
            self.go()
            self.queue_command("a", {"cmd": "box_mon", "key": ka[1]})
            self.queue_command("b", {"cmd": "box_mon", "key": kb[1]})
            for inst, key in (("a", ka[1]), ("b", kb[1])):
                self.wait_for(f"{inst} deposit done",
                         lambda i=inst: "DEPOSIT_DONE" in (read_result(self.scenario, i) or ""),
                         180)
                self.queue_command(inst, {"cmd": "party_mon", "key": key})
        elif self.scenario == "trade":
            # MVP scripted trade: no inject_link (the server menu flow is pytest-covered, and a
            # link whose halves swap owners behind the server's back would just feed the
            # reconciler). Cross-inject apply_trade with each side's slot-0 blob.
            def blobs():
                ba = extract_marks(read_result(self.scenario, "a"), "MYBLOB")
                bb = extract_marks(read_result(self.scenario, "b"), "MYBLOB")
                return (ba[0], bb[0]) if ba and bb else None
            blob_a, blob_b = self.wait_for("MYBLOB from both", blobs, 120)
            self.queue_command("a", {"cmd": "apply_trade", "slot": 0, "blob_hex": blob_b,
                                     "token": "duo"})
            self.queue_command("b", {"cmd": "apply_trade", "slot": 0, "blob_hex": blob_a,
                                     "token": "duo"})
            # The partner's pre-trade key rides in each go-file (blob bytes 0-3 PID, 4-7 OTID).
            def key_of(blob):
                pid = int.from_bytes(bytes.fromhex(blob[:8]), "little")
                otid = int.from_bytes(bytes.fromhex(blob[8:16]), "little")
                return f"{pid:08X}:{otid:08X}"
            self.go({"a": [f"PARTNER {key_of(blob_b)}"],
                     "b": [f"PARTNER {key_of(blob_a)}"]})
        elif self.scenario == "ghost":
            self.go()
        else:
            raise ValueError(self.scenario)

    def _witness_path(self, inst):
        """This attempt's dump, from the name the body writes (duo_gen1_main.lua:81-84)."""
        return os.path.join(BUILD, f"e2e_{self.scenario}_{inst}_{self.attempt}_witness.bin")

    def _witness_flush(self, inst):
        """The flushed SaveRAM the scenario's OWN oracle reads.

        The scenario's resolver, not a hardcoded name: a staged randomized cartridge
        (admit_randomized_new) and a companion cartridge each save under their own
        filename-derived name (`_gen1_save_name`, `_saved_gen1_party`'s default). Comparing a
        witness against a fixture would fail for a reason that is not the cartridge's.
        """
        return self._saved_gen1_party(inst)[0]

    def check_save_witness(self, results, *, archived_paths=None):
        """S-7: the cartridge's save bytes hashed where the hook fired and where the file landed.

        Each instance first requires a client-built ROM hash prefix matching its resolved
        cartridge and records that cartridge's full SHA-1 in GEN1_ROM_SHA1. This applies
        even when the instance never saved. The save witness then records:

            SAVE_WITNESS_SHA256 inst=<a|b> site=<hex> file=<hex> match=<true|false> saves=<n>

        `site` is the sha256 of the hook-time dump, `file` the sha256 of the same
        [0x498:0x8000] slice of the flushed SaveRAM, and `saves` the number of
        SAVE_WITNESS_DUMP lines the receipt carries. A missing, short or mismatching witness
        FAILS the scenario, naming the instance and which of the three it was; the check is
        never weakened for a mismatch.

        The skip is for a half that never saves: a receipt with no save marker at all gets
        `saves=0 skipped` — reconnect_new's relaunch phases save nothing, and inventing a dump
        for them would be a guess. A half that saved but whose dump failed is NOT skipped: the
        body logs SAVE_WITNESS_DUMP_FAIL, the receipt still carries the plain SAVE_WITNESS
        marker, and a missing file then fails with that error quoted.

        WHERE IT RUNS. From `_run_oracle`, before the scenario's own oracle: the one point every
        gen1_new scenario passes through on a double PASS, and the emulators are exited here
        first — the same flush boundary each oracle's own prologue waits on.
        """
        import hashlib

        # `emus` is set by __init__ for every real run; the getattr is for the hand-built stubs
        # the pins use, which model a receipt without modelling the emulator processes.
        for process in getattr(self, "emus", []):
            process.wait(timeout=30)  # the flush boundary; see _saved_gen1_party
        for inst in ("a", "b"):
            receipt = (results or {}).get(inst) or ""
            # Bind every receipt, including a no-save half, to the cartridge the launcher
            # resolved. Repeated builds must all agree; one good line cannot mask a bad one.
            built = [line for line in receipt.splitlines() if line.startswith("client built:")]
            pattern = (rf"client built: title=\S+ pack=(?P<pack>\S+) kind=(?P<kind>\S+) player={inst} "
                       r"rom=(?P<rom>[0-9a-fA-F]{8}) -> \S+")
            matches = [re.fullmatch(pattern, line) for line in built]
            if not matches or any(match is None for match in matches):
                raise RuntimeError(f"{inst}: client built ROM receipt missing or malformed")
            expected_pack = "gen1_purergb" if is_pure_pairing(self.game) else "gen1_rby"
            allowed = {"gen1_rby": {"named"}, "gen1_purergb": {"overlay", "rand_overlay"}}
            if any(match["pack"] != expected_pack or match["kind"] not in allowed[expected_pack]
                   for match in matches):
                raise RuntimeError(f"{inst}: client built ROM kind is not an admitted companion/overlay")
            rom = self._rom_for(inst)
            digest = hashlib.sha1((Path(REPO) / rom).read_bytes()).hexdigest()
            prefixes = {match["rom"].lower() for match in matches}
            if prefixes != {digest[:8]}:
                raise RuntimeError(f"{inst}: client built ROM prefixes {sorted(prefixes)} "
                                   f"differ from booted cartridge {rom} sha1={digest}")
            self._pydec_note(f"GEN1_ROM_SHA1 inst={inst} rom={rom} sha1={digest} "
                             f"receipt={matches[0]['rom']} computed={digest[:8]} match=true")
            dumps = SAVE_WITNESS_DUMP_RE.findall(receipt)
            if not re.search(r"(?m)^SAVE_WITNESS[_ ]", receipt):
                self._pydec_note(f"SAVE_WITNESS_SHA256 inst={inst} site=- file=- match=- "
                                 f"saves=0 skipped")
                continue
            # The body's dump is gated on the signal having VALIDATED (duo_gen1_main.lua:123-147):
            # a fire that was rejected logs SAVE_WITNESS_DUMP_SKIPPED and writes no file. A
            # scenario that saved but whose dump gate said no is a defect, not a skip — the
            # Lua's own reason is quoted so the failure names what the client saw.
            skipped = re.findall(r"SAVE_WITNESS_DUMP_SKIPPED why=(\S+)", receipt)
            if skipped and not dumps:
                raise RuntimeError(f"{inst}: the cartridge saved but the dump gate rejected the "
                                   f"fire (why={skipped[-1]}); no witness was written")
            # A failed or skipped dump leaves the PREVIOUS save's file on disk, and a repeated
            # save can make that file byte-identical to what this attempt's last save would have
            # written — so the outcomes are read IN ORDER: the last attempted dump has to be the
            # successful one, and the successful dumps' own ordinals have to run 1..n unbroken.
            lines = (receipt or "").splitlines()
            dump_at = [i for i, line in enumerate(lines) if line.startswith("SAVE_WITNESS_DUMP ")]
            trouble_at = [i for i, line in enumerate(lines) if line.startswith(
                ("SAVE_WITNESS_DUMP_FAIL", "SAVE_WITNESS_DUMP_SKIPPED"))]
            if dump_at and trouble_at and trouble_at[-1] > dump_at[-1]:
                raise RuntimeError(f"{inst}: the last dump attempt was {lines[trouble_at[-1]]!r}, "
                                   f"after the last successful dump — the file on disk is an "
                                   f"earlier save, not this attempt's final one")
            ordinals = [int(row[2]) for row in dumps]
            if ordinals != list(range(1, len(ordinals) + 1)):
                raise RuntimeError(f"{inst}: the dump ordinals are {ordinals}, not 1.."
                                   f"{len(ordinals)}; a dump line is missing or reordered")
            path = self._witness_path(inst)
            if dumps:
                logged = os.path.normpath(os.path.join(REPO, dumps[-1][0]))
                if logged != os.path.normpath(path):
                    raise RuntimeError(
                        f"{inst}: the save witness landed at {dumps[-1][0]!r} (the receipt's "
                        f"last dump), not {rel_to_repo(path)!r} — the body and this "
                        f"check disagree about the name")
            # Offline rejudgment preserves the receipt's original logical path above,
            # then reads an explicitly retained copy. Size/hash/flush checks still apply.
            if archived_paths is not None:
                path = str(archived_paths[inst])
                self._pydec_note(f"SAVE_WITNESS_ARCHIVE inst={inst} path={path}")
            if not os.path.exists(path):
                failed = re.findall(r"SAVE_WITNESS_DUMP_FAIL (.*)", receipt)
                detail = (f" (the body logged SAVE_WITNESS_DUMP_FAIL: {failed[-1]})"
                          if failed else "")
                raise RuntimeError(f"{inst}: the save witness is missing at "
                                   f"{rel_to_repo(path)}{detail}")
            with open(path, "rb") as handle:
                blob = handle.read()
            if len(blob) != SAVE_WITNESS_BYTES:
                raise RuntimeError(f"{inst}: the save witness is short — {len(blob)} bytes, "
                                   f"expected {SAVE_WITNESS_BYTES} (0x7B68)")
            flushed = self._witness_flush(inst)[SAVE_WITNESS_START:SAVE_WITNESS_END]
            site_hash = hashlib.sha256(blob).hexdigest()
            file_hash = hashlib.sha256(flushed).hexdigest()
            match = site_hash == file_hash
            saves = len(dumps)
            self._pydec_note(f"SAVE_WITNESS_SHA256 inst={inst} site={site_hash} "
                             f"file={file_hash} match={'true' if match else 'false'} "
                             f"saves={saves}")
            if not match:
                diff = save_witness_diff(blob, flushed)
                raise RuntimeError(
                    f"{inst}: the save witness does not match the flushed SaveRAM — site "
                    f"{site_hash} vs file {file_hash}; first differing SRAM address(es) "
                    f"{[hex(a) for a in diff]}")

    # ── Gen 3 (gen3_frlg): the save witness ────────────────────────────────────────────────
    def check_save_witness_gen3(self, results):
        """PLAN §5.5's witness for every gen3_frlg scenario, run by _run_oracle before its oracle.

        The driver dumps the whole SRAM (flash) domain inside the pinned `save` site -- the
        successful TrySavingData(SAVE_NORMAL) return, gated on R0 == 1 and R5 == 0 and on the
        signal having validated (lua/tests/duo/duo_gen3_main.lua) -- into this attempt's
        witness file, overwritten per save, and logs one SAVE_WITNESS_DUMP line per dump. Here:
        the dump outcomes are read in order (the last attempt must be a successful dump, the
        ordinals 1..n unbroken), the file must be this attempt's (named as logged, newer than
        the attempt start), and check_gen3_witness applies the byte rules. One PYDEC line per
        instance:

            SAVE_WITNESS_SHA256 inst=<a|b> site=<hex> file=<hex> match=true saves=<n> counter=<a>-><b>

        A half named in the scenario's `no_save` must carry no dump at all and is recorded as
        skipped; every other half that did not save FAILS.
        """
        game = getattr(self, "game", "")
        for process in getattr(self, "emus", []):
            process.wait(timeout=30)  # the flush boundary: client.exit writes the battery
        started = getattr(self, "_started", 0)
        for inst in ("a", "b"):
            receipt = (results or {}).get(inst) or ""
            dumps = SAVE_WITNESS_DUMP_RE.findall(receipt)
            if inst in self.cfg.get("no_save", ()):
                if dumps:
                    raise RuntimeError(f"{inst}: declared no_save, but its final receipt dumped "
                                       f"{len(dumps)} save(s)")
                self._pydec_note(f"SAVE_WITNESS_SHA256 inst={inst} site=- file=- match=- "
                                 f"saves=0 skipped (no_save)")
                continue
            skipped = re.findall(r"SAVE_WITNESS_DUMP_SKIPPED why=(\S+)", receipt)
            if not dumps:
                gate = f" (the dump gate said why={skipped[-1]})" if skipped else ""
                raise RuntimeError(f"{inst}: the receipt carries no SAVE_WITNESS_DUMP{gate}; every "
                                   f"{game} scenario half saves unless it is declared no_save")
            lines = receipt.splitlines()
            dump_at = [i for i, line in enumerate(lines) if line.startswith("SAVE_WITNESS_DUMP ")]
            trouble_at = [i for i, line in enumerate(lines) if line.startswith(
                ("SAVE_WITNESS_DUMP_FAIL", "SAVE_WITNESS_DUMP_SKIPPED"))]
            if trouble_at and trouble_at[-1] > dump_at[-1]:
                raise RuntimeError(f"{inst}: the last dump attempt was {lines[trouble_at[-1]]!r}, "
                                   f"after the last successful dump — the file on disk is an "
                                   f"earlier save, not this attempt's final one")
            ordinals = [int(row[2]) for row in dumps]
            if ordinals != list(range(1, len(ordinals) + 1)):
                raise RuntimeError(f"{inst}: the dump ordinals are {ordinals}, not 1.."
                                   f"{len(ordinals)}; a dump line is missing or reordered")
            path = self._witness_path(inst)
            logged = os.path.normpath(os.path.join(REPO, dumps[-1][0]))
            if logged != os.path.normpath(path):
                raise RuntimeError(f"{inst}: the save witness landed at {dumps[-1][0]!r}, not "
                                   f"{rel_to_repo(path)!r}")
            if not os.path.exists(path):
                raise RuntimeError(f"{inst}: the save witness is missing at {rel_to_repo(path)}")
            if os.path.getmtime(path) < started:
                raise RuntimeError(f"{inst}: the save witness {rel_to_repo(path)} predates this "
                                   f"attempt's start — a leftover, not this run's save")
            with open(path, "rb") as handle:
                witness = handle.read()
            ext_ram, ext_note = (self._gen3_final_ext(inst, receipt, len(dumps), started)
                                 if self._gen3_rr else (None, ""))
            facts = check_gen3_witness(witness, self._gen3_flushed(inst),
                                       self._gen3_fixture_bytes(inst), saves=len(dumps),
                                       rr=self._gen3_rr, ext_ram=ext_ram,
                                       title=gen3_codec_title(self._gen3_title(inst)))
            # RR sectors 30-31: LIVE_RAM_MATCH only when the save-boundary RAM copy matched;
            # otherwise OPEN, stated, never claimed (Codex C4-6b finding 4).
            self._pydec_note(f"SAVE_WITNESS_SHA256 inst={inst} site={facts['site']} "
                             f"file={facts['file']} match=true saves={len(dumps)} "
                             f"counter={facts['counter'][0]}->{facts['counter'][1]}"
                             + (" rtc=normalized" if facts["rtc"] else "")
                             + (f" extension_30_31={facts['extension']}" if facts["extension"] else "")
                             + (f" ({ext_note})" if ext_note else ""))

    def _gen3_final_ext(self, inst, receipt, saves, started):
        """The live extension RAM copy bound to the FINAL save by its own receipt, or (None, why)
        -> the witness reports extension=OPEN (Codex C4-6c finding 3). The file name and mtime
        prove nothing about WHICH save wrote it: the driver logs one SAVE_WITNESS_EXT line per
        copy, right after that save's SAVE_WITNESS_DUMP, carrying its ordinal. Only a copy whose
        receipt is the last EXT line, carries the final dump's ordinal, follows the final dump,
        and names this attempt's path at the documented length is compared. A receipt that names
        the wrong path or length is a FAIL, not an OPEN."""
        codec = gen3_codec()
        lines = receipt.splitlines()
        exts = [(i, m) for i, line in enumerate(lines)
                if (m := GEN3_EXT_RE.match(line))]
        last_dump = max(i for i, line in enumerate(lines) if line.startswith("SAVE_WITNESS_DUMP "))
        if not exts:
            return None, "no SAVE_WITNESS_EXT receipt"
        at, match = exts[-1]
        rel, size, ordinal = match.group(1), int(match.group(2)), int(match.group(3))
        if ordinal != saves or at < last_dump:
            return None, (f"the last extension copy is save {ordinal}'s, not the final save "
                          f"{saves}'s")
        path = self._witness_path(inst)[:-len(".bin")] + "_ext.bin"
        if os.path.normpath(os.path.join(REPO, rel)) != os.path.normpath(path):
            raise RuntimeError(f"{inst}: the extension copy landed at {rel!r}, not "
                               f"{rel_to_repo(path)!r}")
        if size != codec.RR_EXT_SIZE:
            raise RuntimeError(f"{inst}: the extension receipt says {size} bytes, the extension "
                               f"is {codec.RR_EXT_SIZE}")
        if not os.path.exists(path) or os.path.getmtime(path) < started:
            raise RuntimeError(f"{inst}: the extension copy {rel_to_repo(path)} is missing or "
                               f"predates this attempt")
        with open(path, "rb") as handle:
            blob = handle.read()
        if len(blob) != size:
            raise RuntimeError(f"{inst}: the extension copy is {len(blob)} bytes, its receipt "
                               f"says {size}")
        return blob, ""

    # ── Gen 3 (gen3_frlg): runner halves ───────────────────────────────────────────────────
    def _gen3_prelude(self, link_slot=None):
        """MYKEY from both, both hellos accepted, and -- for the scenarios whose stimulus is not
        link FORMATION -- one server pair over the boot keys at `link_slot`. The pair is server
        staging (the debug API), never a cartridge poke; set_pokeballs is the server's nuzlocke
        gate, which the link scenarios leave to the clients' own bag reads."""
        ka, kb = self.wait_keys()
        self._boot_keys = {"a": ka, "b": kb}
        self.wait_connected()
        if link_slot is not None:
            self.set_pokeballs()
            self.inject_link(ka[link_slot], kb[link_slot])
            self._link_keys = {"a": ka[link_slot], "b": kb[link_slot]}
        return ka, kb

    def _gen3_linked_lines(self):
        return {inst: [f"LINKED {self._link_keys[inst]}"] for inst in ("a", "b")}

    def _gen3_mark(self, inst, pattern, what, timeout=None):
        """Wait for a driver marker (a regex over the receipt, multiline)."""
        return self.wait_for(f"{inst}: {what}", lambda: re.search(
            pattern, self._read_receipt(inst), re.M), timeout or self.cfg["timeout"])

    # Per-game-family areas for _gen3_area_control: gen3_frlg's evolve leg walks Viridian's Route 1
    # grass (a defensive superset around it); gen3_emerald's evolve_gen3 leg stands directly on
    # Route 102 grass (tests/fixtures/gen3/README.md emerald_evolve.sav, "same map/tile as
    # `battle`" -- no city/forest walk to cover).
    GEN3_AREA_CONTROL_AREAS = {
        "gen3_frlg": ("viridian_city", "route_1", "route_2", "viridian_forest"),
        "gen3_emerald": ("route_102",),
        "gen3_rr": ("route_1",),   # rr_battle2 / rr_evolve_synth stand in Route 1 grass (map 3.19)
    }

    def _gen3_area_control(self):
        """Write-window controls, NOT encounter-rule qualification. A normal RUN or training KO
        otherwise dead-zones its area and queues play_sound to BOTH clients
        (state._handle_no_catch), violating an isolated write and an idle peer's contract.
        Resolve only SERVER test state before GO; no RAM/save edit. `self.game` defaults to
        gen3_frlg (unit test doubles for the FR/LG-only callers never set it)."""
        family = scenario_family(getattr(self, "game", "gen3_frlg") or "gen3_frlg")
        areas = self.GEN3_AREA_CONTROL_AREAS.get(family)
        if not areas:
            raise RuntimeError(f"_gen3_area_control: no area list for game family {family!r}")
        for area in areas:
            reply = api(self.http_port, "POST", "/api/debug/set_area_state", {"area_id": area, "state": "linked"})
            if not reply.get("ok"):
                raise RuntimeError(f"battle-window area control refused: {area}: {reply}")
        self._pydec_note(f"BATTLE_WINDOW_AREA_CONTROL server_only=linked areas={','.join(areas)} "
                         "encounter_rules=not_qualified")

    def orchestrate_trainer_bench_gen3(self):
        ka, kb = self._gen3_prelude()  # command-only carrier: no death/memorialize event or injected link
        self._gen3_area_control()
        slot = self.cfg["battle_window_slot"]
        self._link_keys = {"a": ka[slot], "b": kb[slot]}
        self.go(self._gen3_linked_lines())
        case, key = self.cfg["battle_window_case"], self._link_keys["a"]
        self._gen3_mark("a", rf"^READY_BATTLE_WINDOW {case} {re.escape(key)} slot={slot}\b",
                        "battle-window READY")
        self.queue_command("a", {"cmd": "force_faint", "key": key})

    def orchestrate_active_end_gen3(self):
        """A2 under P+H: no pair and no death -- one keyed force_faint to A, queued only once A
        parks with its lead as battler 0 (READY_ACTIVE). B idles and never saves."""
        ka, kb = self._gen3_prelude()
        self._gen3_area_control()
        self._link_keys = {"a": ka[0], "b": kb[0]}
        self.go(self._gen3_linked_lines())
        key = self._link_keys["a"]
        self._gen3_mark("a", rf"^READY_ACTIVE {re.escape(key)} case=command\b", "A parked, lead active")
        self.queue_command("a", {"cmd": "force_faint", "key": key})

    def assert_battle_window_gen3_saved(self, results):
        # _run_oracle already called check_save_witness_gen3: path/mtime/ordinal/flush are bound.
        from pathlib import Path

        from gen3_battle_window_oracle import verify

        facts = verify(case=self.cfg["battle_window_case"], key=self._link_keys["a"], receipts=results,
                       fixture=self._gen3_fixture_bytes("a"), witness=Path(self._witness_path("a")).read_bytes(),
                       flushed=self._gen3_flushed("a"), peer_fixture=self._gen3_fixture_bytes("b"),
                       peer_flushed=self._gen3_flushed("b"), helpers=sys.modules[__name__], attempt=self.attempt)
        self._pydec_note(f"BATTLE_WINDOW_PYDEC case={facts['case']} key={facts['key']} "
                         f"slot={facts['slot']} hp=0 samples={facts['observed_samples']}")

    assert_trainer_bench_gen3_saved = assert_battle_window_gen3_saved

    def orchestrate_faint_cmd_gen3(self):
        """Server-command/persistence-only (PLAN §5.5): link the two slot-1 mons, then inject A's
        faint through the debug API. The server marks the pair DEAD, queues force_faint to B and
        memorialize to both; nothing touches either cartridge but the clients."""
        self._gen3_prelude(link_slot=1)
        self.go(self._gen3_linked_lines())
        for inst in ("a", "b"):
            self._gen3_mark(inst, r"^READY\b", "READY after the go-file", 300)
        reply = api(self.http_port, "POST", "/api/debug/inject_event",
                    {"player": "a", "event": "faint", "key": self._link_keys["a"],
                     "area_id": "duo"})
        if not reply.get("ok"):
            raise RuntimeError(f"inject_event faint was refused: {reply}")
        # handle_event drains A's queue into the HTTP reply (server/state.py:423-424), so A's own
        # memorialize/game_over would never reach A's socket: hand them back to A's queue.
        for cmd in reply.get("commands_returned") or []:
            self.queue_command("a", cmd)
        print(f"[duo] injected A faint {self._link_keys['a']} -> force_faint B "
              f"{self._link_keys['b']}; re-queued to A: "
              f"{[c.get('cmd') for c in reply.get('commands_returned') or []]}")

    # ── NAT-LEGS (S-8, S-9, S-11): link the leg's subject (server staging), GO; nothing else is
    # injected -- the stimulus is A's own native play (scenario_gen3_{evolve,npc_trade,poison_faint}).
    def orchestrate_evolve_gen3(self):
        self._gen3_prelude(link_slot=0)
        self._gen3_area_control()     # the wild KO's no_catch must not dead-zone route_1 mid-leg
        self.go(self._gen3_linked_lines())

    def orchestrate_npc_trade_gen3(self):
        self._gen3_prelude(link_slot=1)
        self.go(self._gen3_linked_lines())

    def orchestrate_poison_faint_gen3(self):
        self._gen3_prelude(link_slot=0)
        self.go(self._gen3_linked_lines())

    def orchestrate_linked_faint_active_gen3(self):
        """The in-battle path, mechanism P+H (owner rulings 15-18). Link the two ACTIVE leads; B
        parks on its action menu with the linked mon out (READY_ACTIVE: a wild battle, or Rick
        after the T2 route); only then is A released to lose its own active linked mon. B's
        force_faint becomes the Perish commit + hand-off and the engine faints the mon in battle
        with no press -- the receipts and the oracle read the engine."""
        self._gen3_prelude(link_slot=0)
        if self.cfg.get("active_faint_case") == "trainer":
            self._gen3_area_control()
        lines = self._gen3_linked_lines()
        self._go_one("b", lines["b"])
        self._gen3_mark("b", r"^READY_ACTIVE ", "READY_ACTIVE (linked mon active at the action menu)")
        self._go_one("a", lines["a"])

    orchestrate_linked_faint_active_whiteout_gen3 = orchestrate_linked_faint_active_gen3
    orchestrate_linked_faint_active_trainer_gen3 = orchestrate_linked_faint_active_gen3
    orchestrate_linked_faint_active_lhammer_gen3 = orchestrate_linked_faint_active_gen3
    orchestrate_linked_faint_active_mega_gen3 = orchestrate_linked_faint_active_gen3

    def orchestrate_boxsync_gen3(self):
        """A deposits its linked half at the Viridian PC by hand; the server mirrors box_mon to
        B, whose client deposits at its checkpoint. Only then may A withdraw it, which mirrors
        party_mon. Both save after the round trip."""
        self._gen3_prelude(link_slot=1)
        self.go(self._gen3_linked_lines())
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        self._gen3_mark("a", rf"^DEPOSITED {re.escape(ka)}\b", "hand deposit at the PC")
        self._gen3_mark("b", rf"^MIRROR_DEPOSITED {re.escape(kb)}\b", "mirrored box_mon deposit")
        self._append_reconnect_marker("a", "ALLOW_WITHDRAW")
        self._gen3_mark("a", rf"^WITHDRAWN {re.escape(ka)}\b", "hand withdraw at the PC")
        self._gen3_mark("b", rf"^MIRROR_WITHDRAWN {re.escape(kb)}\b", "mirrored party_mon withdraw")
        for inst in ("a", "b"):
            self._append_reconnect_marker(inst, "SAVE")

    def _archive_native_receipts(self):
        """Keep nonce-bound raw receipts when another orientation reuses top-level filenames."""
        candidate = getattr(self, "_native_candidate", None)
        if not candidate:
            return
        directory = Path(self.data_dir) / "receipts"
        saved = {}
        for side in "ab":
            for phase in ("initial", "native_trade_reload"):
                source = Path(self._phase_result_path(side, phase))
                if not source.exists():
                    continue
                body = source.read_bytes()
                bound = False
                for line in body.decode("utf-8", errors="replace").splitlines():
                    if not line.startswith("T5 "):
                        continue
                    try:
                        row = json.loads(line[3:])
                    except ValueError:
                        continue  # a killed process can leave its last line incomplete
                    if (isinstance(row, dict) and row.get("kind") == "override" and row.get("value") == candidate["nonce"]
                            and row.get("side") == side and row.get("phase") == phase):
                        bound = True
                        break
                if bound:
                    directory.mkdir(parents=True, exist_ok=True)
                    name = f"{side}_{phase}.txt"
                    (directory / name).write_bytes(body)
                    saved[name] = hashlib.sha256(body).hexdigest()
        if saved:
            (directory / "sha256.json").write_text(json.dumps(saved, indent=2)+"\n", encoding="utf-8")

    def _prepare_native_trade(self):
        from tools import gen3_trade_duo as t5
        self._native_candidate = t5.prepare_pair(Path(REPO), Path(self.data_dir) / "candidate",
                                                 {side: self._gen3_title(side) for side in "ab"})
        self._native_initial = {}
        self._pydec_note(f"CANDIDATE_ONLY {t5.DISCLOSURE}")
        for side, candidate in self._native_candidate["players"].items():
            self._pydec_note(f"T5_CANDIDATE side={side} title={candidate['title']} rom_sha1={candidate['rom_sha1']} production=false READY=0")
            self._pydec_note(f"T5_PACK side={side} source={candidate['source_commit']} manifest_sha1={candidate['manifest_sha1']} "
                             f"run_lua_sha1={candidate['source_sha1']['lua/gen3/run.lua']} sites_sha1={candidate['pack_sha1']['sites']}")
            self._pydec_note(candidate["fixture_disclosure"])

    def orchestrate_native_trade_firered(self):
        self._gen3_prelude(link_slot=1)
        for inst in "ab":
            self._gen3_mark(inst, r"^FR_TRADE_CAPABLE$", "acknowledged T5 capability")
        self.go(self._gen3_linked_lines())
        for inst in "ab":
            self._gen3_mark(inst, r"^FR_TRADE_READY_FOR_RELOAD$", "native trade/decline finished")
        for inst in "ab":
            self._append_reconnect_marker(inst, "RELOAD")
        for inst in "ab":
            self.wait_for(f"{inst} initial T5 terminal receipt", lambda i=inst: terminal_result(self._read_receipt(i)), 120)
            self._native_initial[inst] = self._read_receipt(inst)
            if "RESULT: PASS" not in self._native_initial[inst]:
                raise RuntimeError(f"{inst}: T5 initial leg failed")
            self.emu_by_inst[inst].wait(timeout=30)
        # Preserve the saved relationship before restart, then cold boot both
        # instances from their own flushed battery without copying a fixture.
        self._native_document = self._reconnect_document()
        for inst in "ab":
            peer = "b" if inst == "a" else "a"
            expected = self._link_keys[inst if self.cfg.get("native_decline") else peer]
            self.launch_instance(inst, phase="native_trade_reload", seed=False, expected_key=expected)
        for inst in "ab":
            self.wait_for(f"{inst} T5 cold reload completed", lambda i=inst: terminal_result(self._read_receipt(i)), 180)
            if "RESULT: PASS" not in self._read_receipt(inst):
                raise RuntimeError(f"{inst}: T5 cold reload failed")
        self._live_complete[self.scenario] = True

    orchestrate_native_trade_decline_firered = orchestrate_native_trade_firered

    def _rr_reset_wait_commit_initial(self, description, predicate, timeout):
        """Wait past A's validated partial FAIL, but never past another terminal failure."""
        def guarded():
            a, b = (self._read_receipt(inst) for inst in "ab")
            a_result, b_result = terminal_result(a), terminal_result(b)
            if a_result:
                problems = rr_reset_partial_a_problems(a, self._link_keys["a"])
                if problems:
                    raise RuntimeError("; ".join(problems))
            if b_result and b_result.startswith("RESULT: FAIL"):
                raise RuntimeError(f"B failed before {description}: {b_result}")
            for inst, result in (("a", a_result), ("b", b_result)):
                if self._process_exited(inst) and not result:
                    raise RuntimeError(f"{inst} exited without a result before {description}")
            return predicate()

        remaining = max(0, getattr(self, "_run_deadline", float("inf")) - time.time())
        return wait_for(description, guarded, min(timeout, remaining))

    def _rr_reset_wire_rows(self, inst="a"):
        assert inst in ("a", "b")
        path = Path(self._wire_dir(), f"wire_{inst}.jsonl")
        if not path.is_file():
            return []
        try:
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"RR reset server wire unreadable: {path}: {exc}") from exc

    def orchestrate_trade_reset_commit_gen3(self):
        """Cold boot the same native battery after a clean commit-window exit."""
        self._gen3_prelude(link_slot=1)
        keys = dict(self._link_keys)
        self._rr_reset_links_staged = self._links_json()
        Path(self.data_dir, "rr_reset_staged_links.json").write_text(
            json.dumps(self._rr_reset_links_staged, sort_keys=True), encoding="utf-8")
        lines = self._gen3_linked_lines()
        for inst in "ab":
            lines[inst].append(f"PARTNER {keys['b' if inst == 'a' else 'a']}")
        self.go(lines)
        if self.cfg["reset_case"] == "success":
            for inst in "ab":
                self._gen3_mark(inst, r"^RESET_NATIVE_SUCCESS_NO_MANUAL_SAVE ",
                                "native post-save before clean exit")
            # The Lua client queues trade_done asynchronously. Keep both games
            # pumping until the server has consumed both real reports and
            # durably re-keyed; only then release their ordinary client.exit.
            def rekeyed():
                doc = self._reconnect_document()
                return (not doc.get("pending_trade") and any(
                    (row.get("a") or {}).get("key") == keys["b"]
                    and (row.get("b") or {}).get("key") == keys["a"]
                    for row in doc.get("links") or []))
            self.wait_for("server settled both native trade_done reports", rekeyed, 120)
            for inst in "ab":
                self._append_reconnect_marker(inst, "RESET_EXIT")
        else:
            self._gen3_mark("a", r"^RESET_PARTIAL_COMMIT_EXIT ",
                            "A clean commit-window interruption")
            marker = self._rr_reset_wait_commit_initial("B native post-save before clean exit", lambda: re.search(
                r"^RESET_NATIVE_SUCCESS_NO_MANUAL_SAVE ({.*})$",
                self._read_receipt("b"), re.M), self.cfg["timeout"])
            token = json.loads(marker[1])["token"]
            self._rr_reset_wait_commit_initial("server consumed B's native trade_done", lambda:
                not rr_reset_b_consumed_problems(self._rr_reset_wire_rows("b"), token,
                                                  keys["a"], self._reconnect_document()), 120)
            self._append_reconnect_marker("b", "RESET_EXIT")
        initial = {}
        for inst in "ab":
            initial[inst] = wait_for(
                f"{inst} named RR reset partial exit",
                lambda i=inst: self._read_receipt(i) if terminal_result(self._read_receipt(i)) else None,
                self.cfg["timeout"])
        problems = rr_reset_initial_receipt_problems(initial, self.cfg["reset_case"], keys)
        self._gen3_raise(problems, "RR reset initial native phase markers checked")
        # client.exit is the only flush boundary. Never relaunch on an unexited emulator;
        # cleanup also refuses to taskkill an RR reset cartridge.
        for inst in "ab":
            code = self.emu_by_inst[inst].wait(timeout=30)
            if code != 0:
                raise RuntimeError(f"{inst}: RR reset clean exit failed ({code})")
        self._rr_reset_initial = initial
        self._rr_reset_before = {inst: self._gen3_flushed(inst) for inst in "ab"}
        self._rr_reset_links_before = self._links_json()
        archive = Path(self.data_dir, "rr_reset_initial")
        archive.mkdir(exist_ok=True)
        for inst in "ab":
            (archive / f"{inst}_receipt.txt").write_text(initial[inst], encoding="utf-8")
            (archive / f"{inst}_battery.bin").write_bytes(self._rr_reset_before[inst])
            witness = Path(self._witness_path(inst))
            for source in (witness, witness.with_name(witness.stem + "_ext.bin")):
                if not source.is_file():
                    raise RuntimeError(f"{inst}: native save artifact missing at clean exit: {source}")
                shutil.copy2(source, archive / f"{inst}_{source.name}")
        (archive / "links.json").write_bytes(self._links_bytes() or b"")
        self._archive_gen3_duo_journals(archive)
        self._pydec_note(f"RR_RESET_INITIAL_ARCHIVE {rel_to_repo(archive)} "
                         + " ".join(f"{i}_sha256={hashlib.sha256(self._rr_reset_before[i]).hexdigest()}"
                                    for i in "ab"))
        for inst in "ab":
            row = re.search(r"^RESET_(?:COMMIT_ENTERED|NATIVE_SUCCESS_NO_MANUAL_SAVE) (\{.*\})$",
                            initial[inst], re.M)
            if not row:
                raise RuntimeError(f"{inst}: missing initial native witness")
            marker = json.loads(row[1])
            native = Path(marker["witness_path"])
            if not native.is_file() or native.stat().st_size != 0x50:
                raise RuntimeError(f"{inst}: native trade witness missing/short: {native}")
            shutil.copy2(native, archive / f"{inst}_native_witness.bin")
            expected = keys[inst] if self.cfg["reset_case"] == "commit" and inst == "a" else keys["b" if inst == "a" else "a"]
            self._rr_reset_expected = getattr(self, "_rr_reset_expected", {})
            self._rr_reset_expected[inst] = {"token": marker["token"], "counter": marker["counter_after"]}
            self.launch_instance(inst, phase="rr_reset_reload", seed=False, expected_key=expected)
        if self.cfg["reset_case"] == "commit":
            self._gen3_mark("a", r"^RESET_AFTER_RESET ",
                            "A qualified production after-reset report")
            token = self._rr_reset_expected["a"]["token"]
            wait_for("server response to RR after-reset report",
                     lambda: not rr_reset_wire_problems(self._rr_reset_wire_rows(), token), 30)
            wait_for("persisted RR after-reset uncertainty",
                     lambda: not rr_reset_pending_problems("commit", token,
                                                           self._reconnect_document()), 30)
            self._append_reconnect_marker("a", "RESET_RELOAD_EXIT")
        for inst in "ab":
            self.wait_for(f"{inst} RR native battery reload", lambda i=inst:
                          terminal_result(self._read_receipt(i)), 240)
            if "RESULT: PASS" not in self._read_receipt(inst):
                raise RuntimeError(f"{inst}: RR cold reload failed")
        self._live_complete[self.scenario] = True

    orchestrate_trade_reset_success_gen3 = orchestrate_trade_reset_commit_gen3

    def assert_rr_trade_reset_saved(self, results):
        """Independent native flash, reload and server outcome at the clean exit boundary."""
        codec = gen3_codec()

        self._gen3_flush_boundary()
        problems = []
        if not self._live_complete.get(self.scenario):
            problems.append("RR reset cold reload did not complete")
        keys = self._link_keys
        case = self.cfg["reset_case"]
        initial = self._rr_reset_initial
        problems += rr_reset_initial_receipt_problems(initial, case, keys)
        problems += rr_reset_link_problems(case, keys, self._rr_reset_links_staged,
                                           self._rr_reset_links_before, self._links_json())
        staged_path = Path(self.data_dir, "rr_reset_staged_links.json")
        if (not staged_path.is_file()
                or json.loads(staged_path.read_text(encoding="utf-8")) != self._rr_reset_links_staged):
            problems.append("staged pre-GO server link archive missing or changed")
        problems += rr_reset_pending_problems(case, self._rr_reset_expected["a"]["token"],
                                              self._reconnect_document())
        if case == "commit":
            problems += rr_reset_wire_problems(self._rr_reset_wire_rows(),
                                                self._rr_reset_expected["a"]["token"])
        archive = Path(self.data_dir, "rr_reset_initial")
        for inst in "ab":
            before = self._rr_reset_before[inst]
            flushed = self._gen3_flushed(inst)
            if codec.split_rtc(before)[0] != codec.split_rtc(flushed)[0]:
                problems.append(f"{inst}: cold reload changed the native flash body")
            if (archive / f"{inst}_battery.bin").read_bytes() != before:
                problems.append(f"{inst}: immutable initial battery archive changed")
            if (archive / f"{inst}_receipt.txt").read_text(encoding="utf-8") != initial[inst]:
                problems.append(f"{inst}: immutable initial receipt archive changed")
            saves = 1 if case == "commit" and inst == "a" else 2
            path = self._witness_path(inst)
            if not os.path.isfile(path):
                problems.append(f"{inst}: initial native save witness missing: {path}")
                continue
            try:
                ext, _ = self._gen3_final_ext(inst, initial[inst], saves, self._started)
                facts = check_gen3_witness(Path(path).read_bytes(), before,
                                           self._gen3_fixture_bytes(inst), saves=saves,
                                           rr=True, ext_ram=ext, title="frlg")
                if facts["extension"] != "LIVE_RAM_MATCH":
                    problems.append(f"{inst}: RR extension save not tied to live RAM")
                self._pydec_note(f"RR_RESET_NATIVE_SAVE side={inst} site={facts['site']} "
                                 f"battery={facts['file']} counter={facts['counter'][0]}->"
                                 f"{facts['counter'][1]} saves={saves} extension={facts['extension']}")
            except (OSError, RuntimeError, ValueError) as exc:
                problems.append(f"{inst}: native save witness invalid: {exc}")
            partner = keys["b" if inst == "a" else "a"]
            expected = keys[inst] if case == "commit" and inst == "a" else partner
            try:
                party, _ = gen3_decode(flushed, rr=True, title="frlg")
                if sum(gen3_key(mon) == expected for mon in party) != 1:
                    problems.append(f"{inst}: reloaded native party lacks {expected} exactly once")
            except (ValueError, RuntimeError) as exc:
                problems.append(f"{inst}: native party decode failed: {exc}")
            if "SAVE_WITNESS_DUMP " in results[inst]:
                problems.append(f"{inst}: a game SAVE occurred after cold reload")
            if not re.search(rf'^RESET_RELOADED .*"key":"{re.escape(expected)}"',
                             results[inst], re.M):
                # Marker field order is not a protocol; the Lua side already checks
                # party/key/counter. Require the marker and inspect its JSON here.
                rows = re.findall(r"^RESET_RELOADED (\{.*\})$", results[inst], re.M)
                if len(rows) != 1 or json.loads(rows[0]).get("key") != expected:
                    problems.append(f"{inst}: cold-reload key witness missing or wrong")
        if case == "commit":
            token = self._rr_reset_expected["a"]["token"]
            if not re.search(r"^RESET_AFTER_RESET (\{.*\})$", results["a"], re.M):
                problems.append("a: production after_reset marker absent")
            sent = re.findall(r'^TX trade_done - (\{.*\})$', results["a"], re.M)
            if not any((item := json.loads(row)).get("token") == token
                       and item.get("uncertain") is True and item.get("after_reset") is True
                       for row in sent):
                problems.append("a: missing production uncertain after_reset trade_done")
        self._gen3_raise(problems, f"RR {case} native battery/journal archived; cold reload and "
                                   "independent save/party/server outcome verified")

    def _native_trade_problems(self, results):
        from tools import gen3_trade_duo as t5
        self._gen3_flush_boundary()
        if not self._live_complete.get(self.scenario):
            return ["T5 cold reload leg was not launched"]
        return t5.physical_problems(Path(REPO), self._native_candidate, self._native_initial, results,
                                   self._native_document, {i: self._gen3_fixture_saved(i) for i in "ab"},
                                   {i: self._gen3_saved(i) for i in "ab"}, decline=self.cfg.get("native_decline", False))

    def check_native_trade_witness(self, results):
        self._gen3_raise(self._native_trade_problems(results),
                        "T5 native/flush/save/reload witness chain verified from cartridge bytes")

    def assert_native_trade_firered(self, results):
        self._gen3_raise(self._native_trade_problems(results),
                        "T5 native NPC/selection/offer, durable outcome, server settlement and cold reload verified")

    def orchestrate_species_clause_gen3(self):
        from gen3_clause_rows import orchestrate_clause
        return orchestrate_clause(self, helpers=sys.modules[__name__])

    def orchestrate_gift_gen3(self):
        from gen3_gift_egg_rows import orchestrate
        return orchestrate(self)

    orchestrate_egg_hatch_gen3 = orchestrate_gift_gen3
    orchestrate_egg_receive_gen3 = orchestrate_gift_gen3
    orchestrate_choice_gift_gen3 = orchestrate_gift_gen3
    orchestrate_gift_box_gen3 = orchestrate_gift_gen3

    def assert_gift_egg_gen3_saved(self, results):
        from gen3_gift_egg_rows import saved_oracle
        return saved_oracle(self, results)

    def orchestrate_static_wild_gen3(self):
        from gen3_static_wild_rows import orchestrate
        return orchestrate(self)

    orchestrate_exp_static_static_gen3 = orchestrate_static_wild_gen3
    orchestrate_exp_static_static_run_gen3 = orchestrate_static_wild_gen3
    orchestrate_exp_static_grass_gen3 = orchestrate_static_wild_gen3
    orchestrate_exp_static_surf_gen3 = orchestrate_static_wild_gen3
    orchestrate_exp_static_rock_gen3 = orchestrate_static_wild_gen3
    orchestrate_exp_static_fish_gen3 = orchestrate_static_wild_gen3
    orchestrate_exp_static_altering0_gen3 = orchestrate_static_wild_gen3
    orchestrate_exp_static_altering1_gen3 = orchestrate_static_wild_gen3

    def assert_static_wild_gen3_saved(self, results):
        from gen3_static_wild_rows import saved_oracle
        return saved_oracle(self, results)

    orchestrate_gender_clause_gen3 = orchestrate_species_clause_gen3
    orchestrate_type_clause_gen3 = orchestrate_species_clause_gen3

    def assert_clause_gen3_saved(self, results):
        from gen3_clause_rows import clause_oracle
        return clause_oracle(self, results)

    assert_species_clause_gen3_saved = assert_clause_gen3_saved
    assert_gender_clause_gen3_saved = assert_clause_gen3_saved
    assert_type_clause_gen3_saved = assert_clause_gen3_saved

    def orchestrate_ball_gate_gen3(self):
        from gen3_clause_rows import orchestrate_ball_gate
        return orchestrate_ball_gate(self)

    def assert_ball_gate_gen3_saved(self, results):
        from gen3_clause_rows import ball_gate_oracle
        return ball_gate_oracle(self, results)

    def orchestrate_nature_change_gen3(self):
        from gen3_nature_rows import orchestrate
        return orchestrate(self)

    def assert_nature_change_gen3_saved(self, results):
        from gen3_nature_rows import nature_oracle
        return nature_oracle(self, results)

    def orchestrate_borrowed_party_menu_gen3(self):
        from gen3_borrowed_rows import orchestrate
        return orchestrate(self)

    def orchestrate_borrowed_party_battle_gen3(self):
        from gen3_borrowed_rows import orchestrate
        return orchestrate(self)

    def orchestrate_borrowed_party_opponent_gen3(self):
        from gen3_borrowed_rows import orchestrate
        return orchestrate(self)

    def assert_borrowed_party_gen3_saved(self, results):
        from gen3_borrowed_rows import saved_oracle
        return saved_oracle(self, results)

    def orchestrate_release_gen3(self):
        from gen3_clause_rows import orchestrate_release
        return orchestrate_release(self)

    def orchestrate_species_family_gen3(self):
        from gen3_clause_rows import family_members
        self._gen3_prelude(link_slot=0)
        lines = self._gen3_linked_lines()
        lines["a"].append("FAMILY_LINKS " + json.dumps(family_members(self), sort_keys=True))
        self.go(lines)
        self._gen3_mark("a", r"^FAMILY_READY \S+$", "native family encounter and RUN")
        for inst in ("a", "b"):
            self._append_reconnect_marker(inst, "SAVE")

    def assert_species_family_gen3_saved(self, results):
        from gen3_clause_rows import family_oracle
        return family_oracle(self, results)

    def orchestrate_shiny_bonus_gen3(self):
        from gen3_shiny_rows import orchestrate
        return orchestrate(self)

    def assert_shiny_bonus_gen3_saved(self, results):
        from gen3_shiny_rows import saved_oracle
        return saved_oracle(self, results)

    def assert_release_gen3_saved(self, results):
        from gen3_clause_rows import release_oracle
        return release_oracle(self, results)

    def orchestrate_trade_gen3(self):
        """The RR PC trade NPC, driven only by the two cartridges: link the slot-1 mons (server
        staging), name each side's partner key, GO. A talks to the NPC and answers the server's
        native menus, B answers the offer, and on YES both run the durable native trade
        (RR-DURABLE: pre-save, scene, post-save); nothing is injected after GO. Both save only
        once the server has settled (re-keyed links.json, or a declined offer on both receipts)."""
        self._gen3_prelude(link_slot=self.cfg.get("trade_slot", 1))
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        lines = self._gen3_linked_lines()
        lines["a"].append(f"PARTNER {kb}")
        lines["b"].append(f"PARTNER {ka}")
        self.go(lines)
        if self.scenario == "trade_decline_gen3":
            self._gen3_mark("b", r"^DECLINED ", "B declined the offer")
            for inst, key in (("a", ka), ("b", kb)):
                self._gen3_mark(inst, rf"^KEPT {re.escape(key)} ", "kept its linked mon")
        else:
            self._gen3_mark("a", rf"^TRADED gave={re.escape(ka)} got={re.escape(kb)} ", "A traded")
            self._gen3_mark("b", rf"^TRADED gave={re.escape(kb)} got={re.escape(ka)} ", "B traded")

            def rekeyed():
                return any((e.get("a") or {}).get("key") == kb and (e.get("b") or {}).get("key") == ka
                           for e in self._links_json())
            self.wait_for("links.json re-keyed by the server's commit", rekeyed, 120)
        for inst in ("a", "b"):
            self._append_reconnect_marker(inst, "SAVE")

    orchestrate_trade_decline_gen3 = orchestrate_trade_gen3

    def orchestrate_trade_lock_probe_gen3(self):
        return importlib.import_module("gen3_journal_probe").orchestrate(self)

    def orchestrate_infopanel_gen3(self):
        """Every slot pair both fixtures hold (up to two), linked only after A's receipt shows the
        RX tee is in place, so the link_panel the server sends next is the one A records and draws."""
        ka, kb = self._gen3_prelude()
        self._gen3_mark("a", r"^PANEL_TEE\b", "A's link_panel tee")
        pairs = min(2, len(ka), len(kb))
        for slot in range(pairs):
            self.inject_link(ka[slot], kb[slot], area_id=f"duo{slot}")
        self._link_keys = {"a": ka[pairs - 1], "b": kb[pairs - 1]}
        self.go()

    orchestrate_infopanel_dex_gen3 = orchestrate_infopanel_gen3

    def _gen3_trade_facts(self, results, traded):
        self._gen3_flush_boundary()
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        fixture = {i: self._gen3_fixture_saved(i) for i in "ab"}
        rom_sha1 = {i: hashlib.sha1(Path(REPO, self._gen3_rom(i)).read_bytes()).hexdigest() for i in "ab"}
        preimages, pre_problems = gen3_trade_preimages(results, {"a": ka, "b": kb}, fixture, rom_sha1)
        if pre_problems:
            return ka, kb, pre_problems  # missing/bad evidence cannot fall back to an old fixture
        problems = gen3_trade_problems(
            ka, kb, self._links_json(), {i: self._gen3_saved(i) for i in "ab"},
            fixture, traded, preimages=preimages)
        for inst in ("a", "b"):
            required, ordered, forbidden = gen3_trade_chain(inst, ka, kb, not traded)
            problems += gen3_receipt_problems(inst, results[inst], required=required,
                                              ordered=ordered, forbidden=forbidden)
            if inst == "a" and len(re.findall(r"(?m)^TX trade_request ", results[inst])) != 1:
                problems.append("a: expected exactly one trade_request")
        if re.search(r"(?m)^TX trade_request ", results["b"]):
            problems.append("b: sent a trade_request (it never talked to the NPC)")
        if traded:
            for inst, gets in (("a", kb), ("b", ka)):
                got = next((m for m in self._gen3_saved(inst)[0] if gen3_key(m) == gets), None)
                if got is not None:
                    problems += gen3_record_problems(f"{inst}: the received {gets}", got,
                                                     self._gen3_rr, self._gen3_limits(inst))
        return ka, kb, problems

    def assert_trade_gen3_saved(self, results):
        ka, kb, problems = self._gen3_trade_facts(results, traded=True)
        self._gen3_raise(problems, f"trade_gen3: NPC talk -> Trade -> slot 1 -> partner YES -> "
                                   f"durable native pre-save/scene/post-save both sides; saved a holds "
                                   f"{kb}, b holds {ka} (partner records intact), links.json re-keyed, "
                                   f"each key once")

    def assert_trade_lock_probe_gen3_saved(self, results):
        return importlib.import_module("gen3_journal_probe").saved_oracle(self, results)

    def assert_trade_decline_gen3_saved(self, results):
        ka, kb, problems = self._gen3_trade_facts(results, traded=False)
        self._gen3_raise(problems, f"trade_decline_gen3: offer {ka} for {kb} declined with B; no "
                                   f"apply_prepare/apply_trade, both saves hold their fixture party, "
                                   f"link unchanged")

    def assert_infopanel_gen3_saved(self, results):
        alive = sum(1 for e in self._links_json() if e.get("status") == "alive")
        problems = [f"a: {p}" for p in gen3_panel_problems(results["a"], alive)]
        problems += gen3_receipt_problems("b", results["b"], required=[r"(?m)^WRITES "])
        self._gen3_raise(problems, f"infopanel_gen3: START -> SOULLINK drew the server's "
                                   f"link_panel rows ({alive} pair(s)), A and B each close it")

    assert_infopanel_dex_gen3_saved = assert_infopanel_gen3_saved

    def _gen3_identity(self) -> str:
        """G4 item 2a (1) + Codex REV-center-receipt-2: what this receipt ran on -- each side's ROM
        and fixture bytes, the pack files (full SHA-256), and the source cut: HEAD, +dirty when
        ANY tracked or untracked path differs from it (the whole executed closure: server/, lua/,
        tools/, data/, ...), minus GEN3_IDENTITY_EXCLUDE's run outputs. A git error fails the
        run closed; `_gen3_source_dirty` lets _run_oracle REJECT a dirty receipt."""
        import hashlib

        def sha(path):
            with open(path if os.path.isabs(path) else os.path.join(REPO, path), "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()

        def git(*args):
            proc = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"IDENTITY: git {' '.join(args)} failed ({proc.returncode}): "
                                   f"{proc.stderr.strip()}")
            return proc.stdout

        pack = os.path.join(REPO, "data", "games", GEN3_PACKS[self._gen3_title("a")])
        sides = " ".join(f"{inst}={self._gen3_title(inst)}:rom={sha(self._gen3_rom(inst))}"
                         f":fixture={sha(self._gen3_fixture_path(inst))}" for inst in ("a", "b"))
        packs = " ".join(f"{name}={sha(os.path.join(pack, name))}"
                         for name in ("write_checkpoint.json", "profile.json"))
        head = git("rev-parse", "HEAD").strip()
        dirty = gen3_dirty_paths(git("status", "--porcelain=v1", "-z", "--untracked-files=all",
                                     "--ignore-submodules=none"))
        self._gen3_source_dirty = dirty
        shown = f" dirty=[{','.join(dirty[:20])}{',...' if len(dirty) > 20 else ''}]" if dirty else ""
        return f"IDENTITY {sides} {packs} source={head}{'+dirty' if dirty else ''}{shown}"

    def orchestrate_center_controls_gen3(self):
        """G4 item 2a (4) on the Center 2F: each time A parks in a refusing state it logs
        CONTROL_LIVE <name>, and only then is its keyed probe queued -- box_mon for the Cable Club
        menu (still held through the link wait), party_mon (with the stats A's own stats_cache
        reported when the box_mon landed) for the Union Room attendant."""
        self._gen3_prelude(link_slot=1)
        self.go(self._gen3_linked_lines())
        ka = self._link_keys["a"]
        self._gen3_mark("a", rf"^CONTROL_LIVE cable_welcome_message {re.escape(ka)} ", "A parked at the Cable Club welcome message")
        self.queue_command("a", {"cmd": "box_mon", "key": ka})
        self._gen3_mark("a", rf"^CONTROL_LIVE union_room_attendant {re.escape(ka)} ",
                        "A parked at the Union Room attendant")
        cached = self._gen3_sent_event(read_result(self.scenario, "a"), "stats_cache", ka) or {}
        if not cached.get("stats"):
            raise RuntimeError(f"A never reported stats_cache for {ka}; no party_mon probe to send")
        self.queue_command("a", {"cmd": "party_mon", "key": ka, "stats": cached["stats"]})

    def orchestrate_save_then_write_gen3(self):
        """A saves, reports the stale sSaveDialogCB on an idle field, and only then is its keyed
        box_mon queued: the write must land there (C4-6r finding, C4-SAVE fix)."""
        self._gen3_prelude(link_slot=1)
        self.go(self._gen3_linked_lines())
        ka = self._link_keys["a"]
        self._gen3_mark("a", rf"^WRITE_PROBE_READY {re.escape(ka)} ", "A idle after its save, sSaveDialogCB stale")
        self.queue_command("a", {"cmd": "box_mon", "key": ka})
        # rows 9/1 (C4-SAVE-ROWS): one party_mon, queued once A rests the START cursor off SAVE; A
        # holds it there, at the overwrite prompt and the redrawn menu, and it lands after B
        self._gen3_mark("a", rf"^CONTROL_LIVE dialog_witness {re.escape(ka)} ",
                        "A in the START menu, cursor off SAVE")
        cached = self._gen3_sent_event(read_result(self.scenario, "a"), "stats_cache", ka) or {}
        if not cached.get("stats"):
            raise RuntimeError(f"A never reported stats_cache for {ka}; no party_mon probe to send")
        self.queue_command("a", {"cmd": "party_mon", "key": ka, "stats": cached["stats"]})

    def orchestrate_whiteout_gen3(self):
        """Both halves of the slot-1 pair boxed (A by hand, B by the mirrored box_mon), the
        server's own party_keys agreeing (assert_whiteout_both_boxed, shared with Gen 1), then A
        whites out with its lone starter and the server rebuilds the pair out of both PCs."""
        self._gen3_prelude(link_slot=1)
        self.go(self._gen3_linked_lines())
        self.assert_whiteout_both_boxed()
        # G4 item 2a's negative control: after its save A parks in the Center nurse's script, and
        # a queued SLink write must stay HELD there (scenario_gen3_whiteout.lua nurse_control).
        # G5-RR-NURSE-2: RR's nurse never refuses, so the control there is the START menu instead,
        # named start_menu (assert_whiteout_gen3_saved's control_name, same rule).
        ka = self._link_keys["a"]
        control_name = self._gen3_whiteout_control()
        self._gen3_mark("a", rf"^CONTROL_LIVE {control_name} {re.escape(ka)} ",
                        f"A parked in the {control_name} negative control")
        self.queue_command("a", {"cmd": "box_mon", "key": ka})

    def orchestrate_link_gen3(self):
        """D-1 on FRLG: both catch on Route 1 and the SERVER pairs them by area (shared check)."""
        self._gen3_prelude()
        self.go()
        self.assert_link_new()

    def _prepare_gen3_probe_flip(self):
        """A boots the byte-pinned companion; B boots the published companion with ONE byte flipped (gen3_probe_flip_choice). The edit
        is staged under patch/build/probe_flip_<lane>/ and recorded in the receipt before anything launches."""
        gen3_codec()
        from tools.gen3_companions import published
        from tools.gen3_final_cut import STAGED, rom_pins

        pins = rom_pins(REPO, require_companions=True)
        title_a, title_b = (self.gcfg["sides"][side][0] for side in ("a", "b"))
        clean = next((base / STAGED[title_b] for base in (Path(REPO), *Path(REPO).parents) if (base / STAGED[title_b]).is_file()), None)
        if clean is None:
            raise RuntimeError(f"BLOCKED: pinned clean {title_b} ROM absent for the probe build")
        clean_raw = clean.read_bytes()
        if hashlib.sha1(clean_raw).hexdigest() != pins[title_b]:
            raise RuntimeError(f"present-but-wrong clean {title_b} SHA-1")
        published_row = published(title_b, clean_raw)
        if published_row is None:
            raise RuntimeError(f"BLOCKED: no published companion for {title_b} (patch/dist/gen3_companions.json)")
        patched, row = published_row
        pack = Path(REPO) / "data" / "games" / GEN3_PACKS[title_b].split("/")[0]
        checkpoint = json.loads((pack / "write_checkpoint.json").read_text(encoding="utf-8"))[title_b]
        anchors = [(a["rom_offset"], len(a["expected_hex"]["companion"]) // 2) for a in checkpoint["anchors"].values()]
        choice = gen3_probe_flip_choice(patched, row, anchors)
        flipped = patched[:choice["offset"]] + bytes([choice["after"]]) + patched[choice["offset"] + 1:]
        stage = Path(BUILD) / f"probe_flip_{self.lane}"
        stage.mkdir(parents=True, exist_ok=True)
        dest = stage / f"b_{title_b}.gba"
        dest.write_bytes(flipped)
        self._probe_flip = {**choice, "title": title_b, "anchors_checked": len(anchors), "sha1": hashlib.sha1(flipped).hexdigest(),
                            "published_sha1": hashlib.sha1(patched).hexdigest()}
        self._rand_current = {"a": {"title": title_a, "rom": self._gen3_companion_rom(title_a)},
                              "b": {"title": title_b, "rom": dest.relative_to(REPO).as_posix()}}
        self._rand_inputs = dict(self._rand_current)
        self._pydec_note(
            f"PROBE_FLIP_INPUT {probe_flip_facts(self._probe_flip)} before={choice['before']:#04x} after={choice['after']:#04x} "
            f"span={choice['span']} rule='{choice['rule']}' sha1={self._probe_flip['sha1']} published_sha1={self._probe_flip['published_sha1']}")

    def orchestrate_probe_protected_span_flip_gen3(self):
        """Record what the flipped cartridge (B) gets: refused at launch by the client's own admission, or a hello the server then
        admits or rejects. Never asserts either outcome; an observation of either kind is the result."""
        def b_outcome():
            text = self._read_receipt("b")
            refused = re.search(r"(?m)^PROBE_ADMISSION client=refused_at_launch reason=(.*)$", text)
            if refused:
                return {"client": "refused_at_launch", "reason": refused[1].strip()}
            if hello_facts_from(text):
                return {"client": "admitted", "reason": ""}
            return None
        seen = self.wait_for("b: the flipped cartridge's launch outcome", b_outcome, 180)
        server = {"server": "n/a (the client never sent a hello)", "server_reason": ""}
        if seen["client"] == "admitted":
            def verdict():
                player = ((self._status() or {}).get("players") or {}).get("b") or {}
                if player.get("admission") in ("admitted", "rejected") or player.get("identity_error"):
                    return player
                return None
            player = self.wait_for("b: the server's verdict on the flipped cartridge", verdict, 90)
            server = {"server": player.get("admission") or "identity_error", "server_reason": player.get("admission_reason") or
                      player.get("identity_error") or ""}
        self._probe_observed = {**seen, **server}
        flip = self._probe_flip
        self._pydec_note(
            f"PROBE_FLIP_OBSERVED {probe_flip_facts(flip)} client={seen['client']} server={server['server']} "
            f"reason='{(seen['reason'] or server['server_reason']).replace(chr(10), ' ')[:200]}'")
        self.go()

    def assert_probe_protected_span_flip_gen3_saved(self, results):
        """A probe, not a gate: it passes when an observation was recorded (either outcome); it fails only when the harness broke."""
        observed = getattr(self, "_probe_observed", None)
        problems = []
        if not observed:
            problems.append("no admission observation was recorded for the flipped cartridge")
        elif observed["client"] == "refused_at_launch" and not re.search(r"(?m)^PROBE_ADMISSION client=refused_at_launch ", results["b"]):
            problems.append("b: the refusal observation is not in its own receipt")
        elif observed["client"] == "admitted" and not re.search(r"(?m)^PROBE_CLIENT admitted ", results["b"]):
            problems.append("b: the admitted observation is not in its own receipt")
        if observed and observed["client"] == "admitted" and not hello_facts_from(results["b"]):
            problems.append("b: no hello was logged although the probe records it as admitted")
        outcome = (observed or {}).get("client", "nothing")
        self._gen3_raise(problems, f"OBSERVED {outcome} (an observation, not a verdict): probe_protected_span_flip "
                          f"{probe_flip_facts(self._probe_flip) if getattr(self, '_probe_flip', None) else 'no flip recorded'} {observed}")

    def _prepare_gen3_rand(self):
        problems = gen3_rand_dependencies()
        if problems:
            raise RuntimeError("BLOCKED: " + "; ".join(problems))
        gen3_codec()  # establish the repository import path for direct script invocation
        from tools.gen3_final_cut import STAGED, rom_pins

        if GEN3_RAND_SCRATCH is None:
            raise RuntimeError("BLOCKED: SLINK_GEN3_RAND_ROMS is not set (the randomized ROMs are never committed)")
        source = GEN3_RAND_SCRATCH
        stage = Path(BUILD) / f"rand_{self.lane}"
        stage.mkdir(parents=True, exist_ok=True)
        self._rand_facts, self._rand_inputs, self._rand_evidence = {}, {}, {}
        self._rand_negative_saves, self._rand_refusals = {}, {}
        pins = rom_pins(REPO)
        from tools.gen3_companions import overlay_randomized, published

        pristine = {}

        def pinned_clean(title):
            # the pinned clean dump: the base every companion overlay is built on and the pret-table reference
            if title not in pristine:
                clean = next((base / STAGED[title] for base in (Path(REPO), *Path(REPO).parents)
                              if (base / STAGED[title]).is_file()), None)
                if clean is None:
                    raise RuntimeError(f"BLOCKED: pinned clean {title} ROM absent for the companion overlays")
                pristine[title] = clean.read_bytes()
                if hashlib.sha1(pristine[title]).hexdigest() != pins[title]:
                    raise RuntimeError(f"present-but-wrong clean {title} SHA-1")
            return pristine[title]

        def companion_of(title, randomized):
            # patch-first: the launched cartridge is the randomized ROM WITH the companion overlay
            try:
                return overlay_randomized(title, pinned_clean(title), randomized)
            except ValueError as exc:
                raise RuntimeError(f"BLOCKED: companion overlay for {title}: {exc}") from exc

        for side in ("a", "b"):
            title = self.gcfg["sides"][side][0]
            if self.scenario == "link_gen3_rand":
                fixture = self._gen3_fixture_bytes(side)
                self._pydec_note(gen3_rand_catch_note(side, title, self._gen3_fixture_path(side), fixture))
            label = {"firered": "FireRed", "leafgreen": "LeafGreen", "emerald": "Emerald"}[title]
            suffix = "_b" if title == "emerald" and side == "b" else ""
            path = source / f"{label}_allowed{suffix}.gba"
            if not path.is_file():
                raise RuntimeError(f"BLOCKED: randomized ROM absent: {path}")
            raw = path.read_bytes()
            code = {"firered": b"BPRE", "leafgreen": b"BPGE", "emerald": b"BPEE"}[title]
            if raw[0xAC:0xB0] != code or hashlib.sha1(raw).hexdigest() == pins[title]:
                raise RuntimeError(f"randomized input is wrong-title or clean: {path}")
            if gen3_rand_site_problems(raw, title):
                raise RuntimeError(f"randomized input changes engine sites: {path}")
            raw = companion_of(title, raw)
            facts = gen3_rand_rom_facts(raw, title)
            dest = stage / f"{side}_{title}.gba"
            dest.write_bytes(raw)
            self._rand_inputs[side] = {"title": title, "rom": dest.relative_to(REPO).as_posix()}
            self._rand_facts[side] = facts
            self._pydec_note(f"RAND_INPUT {side} SYNTH=clean-derived-save title={title} companion=overlay "
                             f"companion_pin={pins.get(title + '_companion', '')[:12]} "
                             f"sha1={facts['sha1']} transport_sha1={facts['payload']['fingerprint']} "
                             f"content_fingerprint={facts['content_fingerprint']}")
        if self._rand_facts["a"]["payload"]["fingerprint"] == self._rand_facts["b"]["payload"]["fingerprint"]:
            raise RuntimeError("randomized pair has identical fingerprints; swap control invalid")
        for side in ("a", "b"):
            title = self._rand_inputs[side]["title"]
            raw = pinned_clean(title)
            # the CLEAN cartridge: never launched as a client (it is refused at launch -- the clean_refused leg
            # on B); its facts are the pret-table reference the clean-equivalent control is compared with
            clean_dest = stage / f"clean_{title}.gba"
            clean_dest.write_bytes(raw)
            self._rand_inputs[f"clean_{side}"] = {"title": title, "clean": True, "expect_refused": True,
                                                 "rom": clean_dest.relative_to(REPO).as_posix()}
            self._rand_facts[f"clean_{side}"] = gen3_rand_rom_facts(raw, title)
            if side == "b":
                # the plain COMPANION partner of the mixed-kind and clean-equivalent legs: the byte-pinned build
                companion = self._gen3_companion_rom(title)
                self._rand_inputs["companion_b"] = {"title": title, "rom": companion}
                self._rand_facts["companion_b"] = gen3_rand_rom_facts(Path(REPO, companion).read_bytes(), title)
            if side == "a" and self.scenario in GEN3_RAND_ADMISSION:
                # SYNTH 32(b): one unused trailing padding byte of the COMPANION build changes the ROM hash,
                # while every shipped table and every engine/companion site stays byte-identical.
                companion_row = published(title, raw)
                if companion_row is None:
                    raise RuntimeError(f"BLOCKED: no published companion for {title} (patch/dist/gen3_companions.json)")
                patched, row = companion_row
                equivalent = gen3_rand_equivalent_rom(patched, row["protected_spans"])
                dest = stage / f"equivalent_{title}.gba"
                dest.write_bytes(equivalent)
                self._rand_inputs["equivalent_a"] = {"title": title, "rom": dest.relative_to(REPO).as_posix()}
                self._rand_facts["equivalent_a"] = gen3_rand_rom_facts(equivalent, title)
                if self._rand_facts["equivalent_a"]["payload"] != self._rand_facts["clean_a"]["payload"]:
                    raise RuntimeError("32(b) control changed shipped table bytes")
                label = {"firered": "FireRed", "leafgreen": "LeafGreen", "emerald": "Emerald"}[title]
                widest = source / f"{label}_widest.gba"
                if not widest.is_file():
                    raise RuntimeError(f"BLOCKED: rule-changed control absent: {widest}")
                forbidden = widest.read_bytes()
                if gen3_rand_site_problems(forbidden, title):
                    raise RuntimeError("control changes engine sites, so cannot isolate table admission")
                forbidden = companion_of(title, forbidden)
                dest = stage / f"forbidden_{title}.gba"
                dest.write_bytes(forbidden)
                self._rand_inputs["forbidden_a"] = {"title": title, "rom": dest.relative_to(REPO).as_posix()}
                self._rand_facts["forbidden_a"] = gen3_rand_rom_facts(forbidden, title)
                if self._rand_facts["forbidden_a"]["tables"]["evolutions"] == self._rand_facts["clean_a"]["tables"]["evolutions"]:
                    raise RuntimeError("rule-changed control has no changed evolution table")
                self._pydec_note(f"RAND_32B_INPUT equivalent_sha1={hashlib.sha1(equivalent).hexdigest()} "
                                 f"clean_sha1={pins[title]} SYNTH=one_unused_tail_byte tables_identical=true "
                                 f"forbidden_sha1={hashlib.sha1(forbidden).hexdigest()}")
        self._rand_current = {side: self._rand_inputs[side] for side in ("a", "b")}
        Path(self.data_dir, "rom_contract.json").write_text(json.dumps(gen3_rand_contract(self._rand_facts)), encoding="utf-8")

    def _observe_gen3_rand_admission(self, phase):
        sides = GEN3_RAND_PHASES[phase]

        def ready():
            status = self._status() or {}
            players = status.get("players") or {}
            if not all(players.get(side, {}).get("admission") == (
                    "admitted" if phase in ("pair", "equivalent_pair") else "rejected") for side in sides):
                return None
            if phase == "pair" and not all(players[side].get("admission_reason") ==
                                             "cartridge matches the contract" for side in sides):
                return None
            try:
                hellos = {side: gen3_rand_hello(self._read_receipt(side)) for side in sides}
            except (ValueError, json.JSONDecodeError):
                return None
            return {"status": status, "hellos": hellos}

        evidence = self.wait_for(f"randomized {phase} hello/verdict", ready, 180)
        problems = gen3_rand_admission_problems(phase, **evidence, facts=self._rand_facts)
        if problems:
            raise RuntimeError("; ".join(problems))
        self._rand_evidence[phase] = evidence
        path = Path(self.data_dir, f"rand_{phase}.json")
        path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
        self._pydec_note(f"RAND_VERDICT phase={phase} evidence={path} sha256="
                         + hashlib.sha256(path.read_bytes()).hexdigest())
        return evidence

    def _finish_gen3_rand_negative(self, side, phase):
        self._go_one(side)
        self.emu_by_inst[side].wait(timeout=45)
        text = self._read_receipt(side)
        if "RESULT: PASS" not in text or "RAND_PASSIVE writes=0" not in text:
            raise RuntimeError(f"{phase}: negative client did not finish passively")
        saved = self._gen3_rand_fresh_flushed(side)
        problems = gen3_rand_saved_problems(saved, self._gen3_fixture_bytes(side), unchanged_bytes=True)
        if problems:
            raise RuntimeError(f"{phase}: {'; '.join(problems)}")
        path = Path(self.data_dir, f"rand_{phase}.sav")
        path.write_bytes(saved)
        self._rand_negative_saves[phase] = (side, path)
        self._expected_exit.add(side)
        if os.path.exists(self.go_files[side]):
            os.remove(self.go_files[side])

    def _observe_gen3_rand_refused(self, side, phase):
        """A CLEAN cartridge in a randomized run: the launcher refuses it before any hello, so the proof is the
        companion-patch refusal on its own receipt (nothing sent, nothing written, no save) and a server that
        never saw it -- not a server-side verdict."""
        title = self._rand_inputs[f"clean_{side}"]["title"]
        self.wait_for(f"{side}: clean {title} refused at launch",
                      lambda: re.search(gen3_clean_refusal_re(title), self._read_receipt(side)), 120)
        self.emu_by_inst[side].wait(timeout=45)
        status = self._status()
        problems = gen3_refused_side_problems(side, self._read_receipt(side), title)
        problems += gen3_refused_server_problems(status, side, (status or {}).get("links"))
        if problems:
            raise RuntimeError(f"{phase}: " + "; ".join(problems))
        self._rand_refusals[phase] = side
        self._expected_exit.add(side)
        self._pydec_note(f"RAND_REFUSED phase={phase} side={side} title={title} at=launch")

    def _gen3_rand_fresh_flushed(self, side):
        battery = Path(self._gen3_battery_path(side))
        launched = self._launch_times.get(side)
        if not launched or not battery.is_file() or battery.stat().st_mtime < launched:
            raise RuntimeError(f"{side}: randomized leg has no fresh flushed battery")
        # Read the exact selected filename, not an untouched seed under another
        # ROM's name. Unknown-hash cartridges have filename-derived SaveRAM.
        return battery.read_bytes()

    def _start_gen3_rand_admission(self):
        # Rejected slots have never been accepted, so stale adopted state cannot
        # hide a rejection. Both controls use real cartridge launches, no forged hello.
        self._clear_attempt_artifacts()
        self._start_gen3_rand_rule_controls()
        self._rand_current["a"] = self._rand_inputs["b"]
        self.launch_instance("a", phase="wrong_rom")
        self._observe_gen3_rand_admission("wrong_rom")
        self._finish_gen3_rand_negative("a", "wrong_rom")
        self._rand_current["a"] = self._rand_inputs["a"]
        self._expected_exit.discard("a")
        self.launch_instance("a")
        self.wait_for("randomized A commits rand before clean B", lambda:
                      ((self._status() or {}).get("players") or {}).get("a", {}).get(
                          "admission_reason") == "cartridge matches the contract", 180)
        # a plain COMPANION partner in a randomized run: the server's mixed-kind refusal (a clean one cannot
        # get that far, it is refused at launch -- the next leg)
        self._rand_current["b"] = self._rand_inputs["companion_b"]
        self.launch_instance("b", phase="mixed_kind")
        self._observe_gen3_rand_admission("mixed_kind")
        self._finish_gen3_rand_negative("b", "mixed_kind")
        self._rand_current["b"] = self._rand_inputs["clean_b"]
        self._expected_exit.discard("b")
        self.launch_instance("b", phase="clean_refused")
        self._observe_gen3_rand_refused("b", "clean_refused")
        self._rand_current["b"] = self._rand_inputs["b"]
        self._expected_exit.discard("b")
        self.launch_instance("b")

    def _start_gen3_rand_rule_controls(self):
        """Physical rule refusal and 32(b), on a run with no accepted identity yet."""
        self._rand_current["a"] = self._rand_inputs["forbidden_a"]
        self.launch_instance("a", phase="rules_changed")
        self._observe_gen3_rand_admission("rules_changed")
        self._finish_gen3_rand_negative("a", "rules_changed")
        Path(self.data_dir, "rom_contract.json").unlink()
        for side, source, phase in (("a", "equivalent_a", "equivalent"), ("b", "companion_b", "companion_partner")):
            self._rand_current[side] = self._rand_inputs[source]
            self._expected_exit.discard(side)
            self.launch_instance(side, phase=phase)
        self._observe_gen3_rand_admission("equivalent_pair")
        self._finish_gen3_rand_negative("a", "equivalent_a")
        self._finish_gen3_rand_negative("b", "equivalent_b")
        # A clean committed run cannot then accept the genuinely randomized pair. Keep its
        # evidence and start a new private server, rather than changing a run's identity.
        self.server.terminate()
        self.server.wait(timeout=15)
        self.data_dir = tempfile.mkdtemp(prefix="randomized_pair_", dir=self.data_dir)
        Path(self.data_dir, "rom_contract.json").write_text(json.dumps(gen3_rand_contract(self._rand_facts)), encoding="utf-8")
        self._expected_exit.clear()
        self._rand_current = {side: self._rand_inputs[side] for side in ("a", "b")}
        self.start_server()

    def orchestrate_admit_randomized_frlg(self):
        self._gen3_prelude()
        self._observe_gen3_rand_admission("pair")
        self._assert_gen3_rand_roms_unchanged()
        self._live_complete[self.scenario] = True
        self.go()

    def orchestrate_admit_randomized_emerald(self):
        self.orchestrate_admit_randomized_frlg()

    def orchestrate_link_gen3_rand(self):
        self._gen3_prelude()
        self._observe_gen3_rand_admission("pair")
        self.go()
        self.assert_link_new()
        self._assert_gen3_rand_roms_unchanged()
        self._live_complete[self.scenario] = True

    def orchestrate_trainer_panel_gen3_rand(self):
        self._gen3_prelude()
        evidence = self._observe_gen3_rand_admission("pair")
        retail = gen3_rand_retail(self._gen3_title("a"))
        area = "route_102" if self.game == "gen3_emerald" else "viridian_forest"
        probes = evidence["status"].get("gen3_rand_probe") or {}
        problems = gen3_rand_panel_problems(probes, self._rand_facts, retail, expected_area=area)
        if problems:
            raise RuntimeError("; ".join(problems))
        self._assert_gen3_rand_roms_unchanged()
        self._live_complete[self.scenario] = True
        self.go()

    def _assert_gen3_rand_roms_unchanged(self):
        """Every ROM file the attempt prepared (all six randomized rows share one stage dir) still has the sha1 its facts were
        taken from. Called by all three randomized orchestrations and by the shared verdict check."""
        for side, row in self._rand_inputs.items():
            if hashlib.sha1(Path(REPO, row["rom"]).read_bytes()).hexdigest() != self._rand_facts[side]["sha1"]:
                raise RuntimeError(f"{side}: ROM file changed during the attempt")

    def _check_gen3_rand_pair(self):
        if not self._live_complete.get(self.scenario):
            raise RuntimeError("randomized live legs incomplete")
        self._assert_gen3_rand_roms_unchanged()
        evidence = self._rand_evidence["pair"]
        problems = gen3_rand_admission_problems("pair", **evidence, facts=self._rand_facts)
        if problems:
            raise RuntimeError("; ".join(problems))

    def assert_admit_randomized_frlg_saved(self, results):
        self._check_gen3_rand_pair()
        if self._rand_refusals.get("clean_refused") != "b":
            raise RuntimeError("randomized clean_refused live leg missing (the clean cartridge must be refused at launch)")
        problems = []
        for phase in ("wrong_rom", "mixed_kind"):
            if phase not in self._rand_evidence or phase not in self._rand_negative_saves:
                raise RuntimeError(f"randomized {phase} live leg missing")
            problems += gen3_rand_admission_problems(phase, **self._rand_evidence[phase], facts=self._rand_facts)
            side, path = self._rand_negative_saves[phase]
            problems += gen3_rand_saved_problems(path.read_bytes(), self._gen3_fixture_bytes(side), unchanged_bytes=True)
        for phase in ("rules_changed", "equivalent_pair"):
            if phase not in self._rand_evidence:
                raise RuntimeError(f"ruling 32 live phase absent: {phase}")
            problems += gen3_rand_admission_problems(phase, **self._rand_evidence[phase], facts=self._rand_facts)
        for phase in ("rules_changed", "equivalent_a", "equivalent_b"):
            if phase not in self._rand_negative_saves:
                raise RuntimeError(f"ruling 32 passive flash proof absent: {phase}")
            side, path = self._rand_negative_saves[phase]
            problems += gen3_rand_saved_problems(path.read_bytes(), self._gen3_fixture_bytes(side), unchanged_bytes=True)
        self._gen3_flush_boundary()
        for side in ("a", "b"):
            if "RAND_PASSIVE writes=0" not in results[side]:
                problems.append(f"{side}: final admission client did not remain passive")
            problems += gen3_rand_saved_problems(self._gen3_rand_fresh_flushed(side), self._gen3_fixture_bytes(side),
                                                 unchanged_bytes=True)
        self._gen3_raise(problems, "rand pair admitted; other-player/mixed-kind(companion)/rule-changed ROMs refused; "
                                  "a clean cartridge refused at launch; 32(b) declared-rand unknown-hash companion admitted by anchors + "
                                  "mailbox, effective pairing class clean (committed kind clean or companion, by hello "
                                  "order); flash unchanged")

    def assert_link_gen3_rand_saved(self, results):
        self._check_gen3_rand_pair()
        self.assert_link_gen3_saved(results)  # independent flash, key, bag and server-link facts
        problems = [f"{side}: {p}" for side in ("a", "b") for p in gen3_rand_capture_problems(
            self._gen3_saved(side), self._link_keys[side], self._rand_facts[side])]
        self._gen3_raise(problems, "randomized catches saved and present in each OWN ROM encounter table")

    def assert_trainer_panel_gen3_rand_saved(self, results):
        self._check_gen3_rand_pair()
        retail = gen3_rand_retail(self._gen3_title("a"))
        area = "route_102" if self.game == "gen3_emerald" else "viridian_forest"
        probes = self._rand_evidence["pair"]["status"].get("gen3_rand_probe") or {}
        problems = gen3_rand_panel_problems(probes, self._rand_facts, retail, expected_area=area)
        self._gen3_flush_boundary()
        for side in ("a", "b"):
            problems += gen3_rand_saved_problems(self._gen3_flushed(side), self._gen3_fixture_bytes(side))
        self._gen3_raise(problems, "nearby trainer_brief uses each OWN ROM; calc labels empty; saved party/boxes unchanged")

    def orchestrate_deadzone_gen3(self):
        """D-3 on FRLG: A runs, route_1 dead-zones, B's later catch there is retired (shared)."""
        self._gen3_prelude()
        self.assert_dead_zone_new()

    # ── RR-only runner halves (P5, card C5-5) ───────────────────────────────────────────────
    def orchestrate_explode_gen3(self):
        """explode_gen3 (--explode-mode): link the two party LEADS, release B first so it
        parks in a wild battle with its linked mon active (READY_ACTIVE), then A, which loses its
        own linked lead naturally. Nothing pokes either cartridge; the server's force_explode is
        the only command, and B's receipt must show the engine executing it."""
        self._gen3_prelude(link_slot=0)
        lines = self._gen3_linked_lines()
        self._go_one("b", lines["b"])
        self._gen3_mark("b", r"^READY_ACTIVE ", "READY_ACTIVE (linked mon active at the action menu)")
        self._go_one("a", lines["a"])

    def orchestrate_rival_swap_gen3(self):
        """PLAN P5's native rival-swap NEGATIVE control (RR only): B fights, A idles. Once B logs
        READY_IN_BATTLE the runner queues replace_rival_team with a dummy blob and NO session /
        battle_id; the client's C5-10 identity gate refuses it stale_battle_id before anything is
        staged (lua/gen3/client.lua C.replace_rival_team). The real swap is
        rival_swap_real_gen3."""
        self._gen3_prelude()
        self.go()
        self._gen3_mark("b", r"^READY_IN_BATTLE\b", "B at the battle action menu")
        self.queue_command("b", {"cmd": "replace_rival_team", "trainer_id": 0,
                                 "blobs_hex": ["00" * 100]})

    def orchestrate_rival_swap_real_gen3(self):
        """G5-RR-RIVAL: nothing staged by the runner -- the server's own trainer_battle_start
        handler queues the swap (--rival-team-swap)."""
        self._gen3_prelude()
        self.go()

    def assert_rival_swap_real_gen3_saved(self, results):
        """G5-RR-RIVAL, a QUALIFICATION row: a real RR rival battle fought B's party.
        rival_swap_real_problems holds the checks; B's party is its fixture's (B only idles)."""
        from server.adapters.gen3_frlge import _RR_RIVAL_TRAINER_IDS

        codec = gen3_codec()
        with open(GEN3_RR_PROFILE, encoding="utf-8") as handle:
            base = json.load(handle)["native"]["BASE"]
        source = codec.rr_party_from_save(codec.split_rtc(self._gen3_fixture_bytes("b"))[0])
        problems, fact = rival_swap_real_problems(results["a"], source, _RR_RIVAL_TRAINER_IDS,
                                                  base, base + 0x800)
        self._gen3_raise(problems, fact)

    def _gen3_party_record_hex(self, inst, slot):
        """The raw 100-byte party record at `slot` of this instance's fixture, as hex -- a real,
        well-formed trade blob (the protocol's apply_trade blob_hex, docs/protocol.md §6.2)."""
        codec = gen3_codec()
        body = codec.split_rtc(self._gen3_fixture_bytes(inst))[0]
        layout = gen3_codec_title(self._gen3_title(inst))
        sb1 = codec.parse_flash(body, cfru=self._gen3_rr, title=layout)["sb1"]
        # the title's own SaveBlock1 party offset (Emerald's moved: gen3_codec TITLE_EMERALD)
        at = codec._TITLE_PARTY_OFFSETS[codec._title(layout)][1] + slot * codec.PARTY_MON_SIZE
        return sb1[at:at + codec.PARTY_MON_SIZE].hex().upper()

    def orchestrate_clean_rr_refused_gen3(self):
        """Clean-RR REFUSAL PROOF (patch-first, owner 2026-10-02). B boots the raw dump and the
        driver (`expect_refused`) must see lua/gen3/run.lua refuse it: no MYKEY, no hello. So only A
        is waited on for keys and a hello; B is waited on for its refusal line; then A is released
        to sit quietly (it must see no partner and no write)."""
        self.wait_for("a: MYKEY line", lambda: extract_keys(self._read_receipt("a")) or None, 120)
        self.wait_for("a: hello accepted", lambda: ((self._status() or {}).get("players", {})
                                                    .get("a", {}).get("connected")) or None, 120)
        self.wait_for("b: refused at launch", lambda: re.search(GEN3_CLEAN_REFUSAL_RE,
                                                                self._read_receipt("b")), 120)
        self.go()

    def _gen3_wrong_save(self, path):
        """A real save, qualifying under the row's own layout (vanilla or CFRU), whose trainer id
        differs from A's fixture. The trainer-id offset is the title's pack profile's."""
        codec = gen3_codec()
        title, rr = self._gen3_title("a"), self._gen3_rr
        layout = gen3_codec_title(title)
        with open(gen3_profile_path(title), encoding="utf-8") as handle:
            at = json.load(handle)["titles"][title]["derived"]["SB2_OT_ID_OFFSET"]

        def trainer(image):
            return int.from_bytes(codec.parse_flash(image, cfru=rr, title=layout)["sb2"][at:at + 4],
                                  "little")

        with open(path, "rb") as handle:
            body = codec.split_rtc(handle.read())[0]
        ok, why = codec.qualify_flash(body, cfru=rr, title=layout)
        if not ok:
            raise RuntimeError(f"--wrong-save {path} is not a qualifying save: {why}")
        own = trainer(codec.split_rtc(self._gen3_fixture_bytes("a"))[0])
        if trainer(body) == own:
            raise RuntimeError(f"--wrong-save {path} carries A's own trainer id {own}")
        return body, trainer(body)

    def orchestrate_reconnect_gen3(self):
        """C-2 then C-1 on FRLG, with B online throughout: A's EmuHawk is killed and relaunched on
        the SAME save (accepted, nothing duplicated or re-sent), then on a save with another OT
        (refused; links.json byte-identical; the cartridge writes nothing and its battery is the
        seeded bytes afterwards). The wrong save defaults to B's own fixture for A's target."""
        from pathlib import Path

        self._gen3_prelude(link_slot=1)
        self.go(self._gen3_linked_lines())
        for inst in ("a", "b"):
            self._gen3_mark(inst, rf"^RECONNECT_READY {inst}\b", "hello and link hold", 300)
        battery = Path(self._gen3_battery_path("a"))
        self._gen3_before_kill = self._gen3_flushed("a")
        baseline = {"links": self._reconnect_document(), "events": self._reconnect_events()}
        shutil.copyfile(self._result_path("a"), os.path.join(self.data_dir, "a_initial_result.txt"))

        def text(path):
            return Path(path).read_text(encoding="utf-8") if os.path.exists(path) else ""

        self.terminate_instance("a")
        self.wait_for("A disconnected while B stays online", lambda: (
            (s := self._status()) and not s["players"]["a"]["connected"]
            and s["players"]["b"]["connected"]), 45)
        if self._reconnect_document().get("links") != baseline["links"].get("links"):
            raise RuntimeError("the live link changed when A's EmuHawk was killed")
        self.launch_instance("a", phase="same_save", seed=False)
        same_path = self._phase_result_path("a", "same_save")
        self.wait_for("same-save A hello", lambda: same_save_hello_seen(text(same_path)), 300)
        self.wait_for("server accepts A's same-save party", lambda: (
            (s := self._status()) and (a := s["players"]["a"]).get("connected")
            and not a.get("identity_error") and self._link_keys["a"] in (a.get("party_keys") or [])),
            60)
        self.wait_for("durable accepted reconnect hello",
                      lambda: accepted_reconnect_hellos(baseline["events"], self._reconnect_events(),
                                                        hello_facts_from(text(same_path))), 30)
        same_after = {**self._reconnect_document(), "events": self._reconnect_events(),
                      "status": self._status() or {}}
        problems = reconnect_same_problems(baseline["links"], same_after, baseline["events"],
                                           same_after["events"], self._link_keys["a"],
                                           baseline["links"]["player_identity"]["a"]["ot_id"],
                                           allow_accepted_refreshes=True, hello_facts=hello_facts_from(text(same_path)))
        problems += same_save_hello_problems(text(same_path))
        problems += gen3_receipt_problems("a same_save", text(same_path), forbidden=(
            r"(?m)^RX force_faint ", r"(?m)^RX box_mon ", r"(?m)^RX memorialize "))
        if problems:
            raise RuntimeError("C-2 same-save reconnect failed: " + "; ".join(problems))
        self._pydec_note(f"C-2 same OT accepted; alive link {self._link_keys['a']} / "
                         f"{self._link_keys['b']}; zero duplicate gameplay events")
        self._append_reconnect_marker("a", "A_DONE_SAME")
        self.wait_for("same-save A phase PASS", lambda: "RESULT: PASS" in text(same_path), 60)
        self.emu_by_inst["a"].wait(timeout=30)
        late_problems = gen3_receipt_problems("a same_save after PASS", text(same_path), forbidden=(
            r"(?m)^RX force_faint ", r"(?m)^RX box_mon ", r"(?m)^RX memorialize "))
        if late_problems:
            raise RuntimeError("C-2 same-save reconnect late command: " + "; ".join(late_problems))
        self._same_save_artifact = os.path.join(BUILD, f"e2e_{self.scenario}_a_same.SaveRAM")
        with open(self._same_save_artifact, "wb") as handle:
            handle.write(self._gen3_flushed("a"))
        self.terminate_instance("a")
        self.wait_for("same-save A socket closed before the wrong-save relaunch", lambda: (
            (s := self._status()) and not s["players"]["a"]["connected"]), 45)

        wrong = getattr(self.args, "wrong_save", None) or self._gen3_fixture_path("b")
        body, other_ot = self._gen3_wrong_save(wrong)
        before_wrong_bytes = Path(self.data_dir, "links.json").read_bytes()
        before_wrong_events = self._reconnect_events()
        battery.write_bytes(body)
        self._gen3_wrong_body = body
        self.launch_instance("a", phase="wrong_save", seed=False)
        wrong_path = self._phase_result_path("a", "wrong_save")
        self.wait_for("A wrong-save HUD", lambda: "WRONG_SAVE_HUD " in text(wrong_path), 300)
        problems = reconnect_wrong_problems(before_wrong_bytes,
                                            Path(self.data_dir, "links.json").read_bytes(),
                                            self._status() or {}, before_wrong_events,
                                            self._reconnect_events(),
                                            allow_rejected_refreshes=True)
        if problems:
            raise RuntimeError("C-1 wrong-save refusal failed: " + "; ".join(problems))
        self._pydec_note(f"C-1 wrong OT {other_ot} rejected; links.json byte-identical, no "
                         f"gameplay events")
        self._append_reconnect_marker("a", "A_DONE_WRONG")
        self.wait_for("wrong-save A phase PASS", lambda: "RESULT: PASS" in text(wrong_path), 60)
        self.emu_by_inst["a"].wait(timeout=30)
        self._live_complete[self.scenario] = True
        shutil.copyfile(wrong_path, self._result_path("a"))
        self._append_reconnect_marker("b", "B_DONE")
        self.wait_for("B stayed online through reconnect legs", lambda: "RESULT: PASS" in (
            read_result(self.scenario, "b") or ""), 300)

    # ── Gen 3 (gen3_frlg): saved-state oracles ─────────────────────────────────────────────
    def _gen3_memorial_box(self) -> int:
        """MEMORIAL = the last box (lua/gen3/boxes.lua memorial_box = BOXES_PER_STORE - 1).

        RR (25 boxes) reads its own pack profile, not gen3_frlg's (14 boxes) -- `title` is
        constant across both instances of a row, so "a" stands in for the pair.
        """
        title = self._gen3_title("a")
        with open(gen3_profile_path(title), encoding="utf-8") as handle:
            derived = json.load(handle)["titles"][title]["derived"]
        return derived["BOXES_PER_STORE"] - 1

    def _gen3_one_link(self, status, cause=None):
        """The single persisted links.json row whose halves are this run's linked keys."""
        want = {self._link_keys["a"], self._link_keys["b"]}
        rows = [e for e in self._links_json()
                if {(e.get("a") or {}).get("key"), (e.get("b") or {}).get("key")} == want]
        if len(rows) != 1:
            raise RuntimeError(f"expected one persisted link {sorted(want)}, got {rows}")
        row = rows[0]
        if row.get("status") != status or (cause and row.get("cause") != cause):
            raise RuntimeError(f"link {sorted(want)} is {row.get('status')}/{row.get('cause')}, "
                               f"expected {status}" + (f"/{cause}" if cause else ""))
        return row

    def _gen3_raise(self, problems, fact):
        if problems:
            raise RuntimeError("; ".join(problems))
        self._pydec_note(fact)

    def _gen3_flush_boundary(self):
        for process in getattr(self, "emus", []):
            process.wait(timeout=30)

    def assert_faint_cmd_gen3_saved(self, results):
        """Both memorials saved: the linked key left each party for the memorial box and nothing
        else moved; B's key went to HP 0 through an armed OVERWORLD write (the checkpoint), after
        the force_faint and before its memorialize_done; A got no force_faint."""
        self._gen3_flush_boundary()
        self._gen3_one_link("memorial")
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        box = self._gen3_memorial_box()
        problems = []
        for inst, key in (("a", ka), ("b", kb)):
            problems += gen3_memorial_problems(inst, self._gen3_saved(inst),
                                               self._gen3_fixture_saved(inst), key, box, rr=self._gen3_rr,
                                               limits=self._gen3_limits(inst))
        problems += gen3_receipt_problems(
            "a", results["a"], required=[gen3_rx("memorialize", ka), gen3_tx("memorialize_done", ka)],
            forbidden=[r"(?m)^RX force_faint "])
        forced = rf"(?m)^FORCED_HP0 {re.escape(kb)} .*in_battle=0"
        problems += gen3_receipt_problems(
            "b", results["b"],
            required=[gen3_rx("force_faint", kb), forced, gen3_rx("memorialize", kb),
                      gen3_tx("memorialize_done", kb)],
            ordered=[(gen3_rx("force_faint", kb), r"(?m)^\[client\] \[SLink-gen3\] write overworld "),
                     (r"(?m)^\[client\] \[SLink-gen3\] write overworld ", forced),
                     (forced, gen3_tx("memorialize_done", kb))])
        self._gen3_raise(problems, f"faint_cmd: {ka} and {kb} saved once each in box {box + 1}; "
                                   f"B's HP 0 came from an overworld-armed write")

    # ── NAT-LEGS oracles: the engine signal (the driver's SIGNAL tee), what the client sent, what
    # the SERVER persisted, and A's saved battery against its SYNTH fixture.
    def _gen3_nat_saved_party(self, inst):
        party, boxes = self._gen3_saved(inst)
        f_party, f_boxes = self._gen3_fixture_saved(inst)
        problems = [] if boxes == f_boxes else [f"{inst}: the saved boxes differ from the fixture's"]
        return party, f_party, problems

    # NAT-LEGS-4 (Emerald parity): the post-evolution species is a game fact, not a Wartortle
    # constant. FR/LG: SQUIRTLE (7) -> WARTORTLE (8) at level 16 (pret pokefirered
    # src/data/pokemon/evolution.h, EVO_LEVEL 16). Emerald: MUDKIP (283) -> MARSHTOMP (284) at
    # level 16 (pret pokeemerald c65e93f2 src/data/pokemon/evolution.h:129). RR is never sourced
    # from vanilla FR/LG/Emerald data (RR data is RR-only): card RR-SYNTH read RR's own
    # relocated evolution table (tools/gen3_fixtures.py RR_EVO_TABLE = 0x097CD9B0 in
    # patch/build/slink_RR.gba) directly -- TREECKO (RR species id 277, rr_species.json) ->
    # GROVYLE (278) at level 16, method EVO_LEVEL.
    EVOLVE_FACTS = {
        "gen3_emerald": {"species": 284, "level": 16},
        "gen3_rr": {"species": 278, "level": 16},
    }
    EVOLVE_DEFAULT = {"species": 8, "level": 16}

    def assert_evolve_gen3_saved(self, results):
        """S-8: the evolution scene's species store fired (SIGNAL evolve_species_store), no key
        changed, the server's linked half now reads the game's post-evolution species from A's
        tick, and A saved the same key at the post-evolution species/level with the rest of the
        party untouched."""
        self._gen3_flush_boundary()
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        facts = self.EVOLVE_FACTS.get(self.game, self.EVOLVE_DEFAULT)
        species, level = facts["species"], facts["level"]
        row = self._gen3_one_link("alive")
        half = {h.get("key"): h for h in (row.get("a") or {}, row.get("b") or {})}
        problems = [] if (half.get(ka) or {}).get("species") == species else [
            f"server: A's linked half {half.get(ka)} is not species {species}"]
        problems += gen3_receipt_problems(
            "a", results["a"], required=[r"(?m)^SIGNAL evolve_species_store ",
                                         rf"(?m)^EVOLVED {re.escape(ka)} species={species} level={level}"],
            forbidden=[r"(?m)^TX key_change "],
            # the evolution runs INSIDE the battle's end (battle_main.c TryEvolvePokemon), so the
            # pinned battle_end completion site follows it (live: 4310 < 4589)
            ordered=[(r"(?m)^SIGNAL battle_begin ", r"(?m)^SIGNAL evolve_species_store "),
                     (r"(?m)^SIGNAL evolve_species_store ", r"(?m)^EVOLVED ")])
        party, f_party, more = self._gen3_nat_saved_party("a")
        problems += more
        keys = [gen3_key(m) for m in party]
        if keys != [gen3_key(m) for m in f_party]:
            problems.append(f"a: saved party keys {keys} are not the fixture's")
        elif (party[0]["species"], party[0]["level"]) != (species, level):
            problems.append(f"a: saved slot 0 is species {party[0]['species']} Lv{party[0]['level']}, "
                            f"not species {species} Lv{level}")
        self._gen3_raise(problems, f"evolve: {ka} evolved to species {species} Lv{level} under the "
                                   f"same key; links.json half species={species} (partner {kb} unchanged)")

    # card NAT-LEGS-3 (Emerald parity): each game's npc_trade_gen3 target trades away a fixed mon
    # for a fixed one, so the resulting key/species are game facts, not FR/LG constants. FR/LG:
    # Reyley's Route 2 house, ABRA -> MR. MIME (pret pokefirered ingame_trades.h: personality
    # 0x9CAE, OT id 1985 -> key 00009CAE:000007C1, species 122). Emerald: RustboroCity_House1's
    # trader, a player Ralts -> his SEEDOT (pret pokeemerald c65e93f2 src/data/trade.h
    # INGAME_TRADE_SEEDOT: personality 0x84, otId 38726 -> key 00000084:00009746, species 298).
    # card RR-NPCTRADE-2: Route2_House/Reyley (MR_MIME) is DEAD on the real RR ROM -- its
    # CFRU-detoured species check reads a runtime table (ROM 0x09147C74) whose index-0 entry is
    # garbage (species=1375, requested=162/Furret), so the trade's own species comparison can
    # never pass (docs/gen3/research/rr_ingame_trades.md "RR-NPCTRADE-2"). The fixture now targets
    # JYNX/"Dontae" (CeruleanCity_House3, runtime table idx1, which decodes cleanly). new_key is
    # NOT vanilla's static sInGameTrades content -- CreateInGameTradePokemonInternal is ALSO
    # detoured to read the same runtime table, and its personality/otId fields (offset 0x1C/0x10)
    # were decoded directly from ROM: personality 0x498A2E1D, otId 36728 (0x00008F78), species
    # Carnivine (RR id 508, offset+4).
    NPC_TRADE_FACTS = {
        "gen3_emerald": {"new_key": "00000084:00009746", "species": 298},
        # Live 2026-09-27: the received mon's personality was 35DC164E, not the runtime table's
        # 0x498A2E1D -- RR's detoured CreateInGameTradePokemonInternal generates it at trade time,
        # so only the OT half of the key is fixed. The oracle takes the key from A's key_change
        # and holds the saved party and links.json to that same key.
        "gen3_rr": {"new_key": None, "new_otid": "00008F78", "species": 508},
    }
    NPC_TRADE_DEFAULT = {"new_key": "00009CAE:000007C1", "species": 122}

    def assert_npc_trade_gen3_saved(self, results):
        """S-9: TradeMons fired (SIGNAL trade_begin -> trade_done) and THEN the client emitted
        key_change reason npc_trade for the game's fixed NPC trade (NPC_TRADE_FACTS /
        NPC_TRADE_DEFAULT); A saved the received mon in slot 1. Card NAT-LEGS-3: this cut carries
        both the box-census stamp (lua/gen3/client.lua pc_boxes_generation) and the key_change
        retry (lua/core/session.lua RETRYABLE_REJECTIONS), so the migration now LANDS -- either
        accepted on the first try, or refused once for a RETRYABLE reason and resent after a fresh
        census. Both paths are asserted explicitly (not just recorded), and so is the final
        links.json: A's half moves to the NEW key, still alive, B unchanged."""
        self._gen3_flush_boundary()
        ka = self._link_keys["a"]
        facts = self.NPC_TRADE_FACTS.get(self.game, self.NPC_TRADE_DEFAULT)
        new, species = facts["new_key"], facts["species"]
        if new is None:
            hit = re.search(rf'(?m)^TX key_change - .*"new_key":"([0-9A-F]{{8}}:{facts["new_otid"]})"'
                            rf'.*"old_key":"{re.escape(ka)}".*"reason":"npc_trade"', results["a"] or "")
            new = hit.group(1) if hit else f"<no npc_trade key_change with OT {facts['new_otid']}>"
        kc = rf'(?m)^TX key_change - .*"new_key":"{new}".*"old_key":"{re.escape(ka)}".*"reason":"npc_trade"'
        problems = gen3_receipt_problems(
            "a", results["a"], required=[r"(?m)^SIGNAL trade_begin ", r"(?m)^SIGNAL trade_done ", kc],
            ordered=[(r"(?m)^SIGNAL trade_begin ", r"(?m)^SIGNAL trade_done "),
                     (r"(?m)^SIGNAL trade_done ", kc)])
        party, f_party, more = self._gen3_nat_saved_party("a")
        problems += more
        keys = [gen3_key(m) for m in party]
        if keys != [gen3_key(f_party[0]), new] or party[1]["species"] != species:
            problems.append(f"a: saved party {keys}, expected [{gen3_key(f_party[0])}, {new}] with species {species}")
        # the reply sequence after the FIRST TX key_change: either an immediate ack (the generic
        # RX logger in duo_gen3_main.lua prints "RX key_change_ack" with no key= field), or exactly
        # one RETRYABLE refusal ("RX key_change_rejected") followed by exactly one resend of the
        # SAME key_change and then the ack. Anything else is a problem, not a recorded curiosity.
        tail = (results["a"] or "").split("TX key_change", 1)[-1]
        seq = re.findall(r"(?m)^(?:RX key_change_(?:ack|rejected)|TX key_change - .*)$", tail)
        settle = next((i for i, ln in enumerate(seq) if ln.startswith("RX key_change_ack")), None)
        if settle is None:
            problems.append(f"a: key_change never acked; observed reply sequence {seq}")
        else:
            pre = seq[:settle]
            if pre and (len(pre) != 2 or not pre[0].startswith("RX key_change_rejected")
                        or new not in pre[1] or ka not in pre[1]):
                problems.append(f"a: expected an immediate ack, or ONE refusal + ONE resend of the "
                                f"same key_change before the ack; observed {seq[:settle + 1]}")
        reply = seq[:settle + 1] if settle is not None else seq
        rows = [(e.get("status"), (e.get("a") or {}).get("key"), (e.get("b") or {}).get("key"))
                for e in self._links_json()]
        kb = self._link_keys["b"]
        expected_rows = [("alive", new, kb)]
        if rows != expected_rows:
            problems.append(f"a: server links.json is {rows}, expected {expected_rows} "
                            f"(the migrated npc_trade key_change should leave the pair alive under "
                            f"A's NEW key, B unchanged)")
        self._gen3_raise(problems, f"npc_trade: {ka} -> {new} key_change npc_trade emitted after "
                                   f"trade_done; reply sequence {reply}; links.json {rows}")

    def assert_poison_faint_gen3_saved(self, results):
        """S-11 + the Soul Link rule: the field poison step's site fired (SIGNAL poison_faint), THEN
        A sent faint for its linked lead; the server killed the pair; B's partner went to HP 0
        through an OVERWORLD-armed write after its force_faint; both memorials saved. No
        whiteout (the Pidgey stayed up), and A got no force_faint."""
        self._gen3_flush_boundary()
        self._gen3_one_link("memorial")
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        box = self._gen3_memorial_box()
        problems = []
        for inst, key in (("a", ka), ("b", kb)):
            problems += gen3_memorial_problems(inst, self._gen3_saved(inst), self._gen3_fixture_saved(inst),
                                               key, box, rr=self._gen3_rr, limits=self._gen3_limits(inst),
                                               battled=(inst == "a"))
        problems += gen3_receipt_problems(
            "a", results["a"],
            required=[r"(?m)^SIGNAL poison_faint ", gen3_tx("faint", ka), gen3_rx("memorialize", ka),
                      gen3_tx("memorialize_done", ka)],
            forbidden=[r"(?m)^RX force_faint ", r"(?m)^TX whiteout ", r"(?m)^SIGNAL whiteout "],
            ordered=[(r"(?m)^SIGNAL poison_faint ", gen3_tx("faint", ka))])
        forced = rf"(?m)^FORCED_HP0 {re.escape(kb)} .*in_battle=0"
        problems += gen3_receipt_problems(
            "b", results["b"],
            required=[gen3_rx("force_faint", kb), forced, gen3_tx("memorialize_done", kb)],
            ordered=[(gen3_rx("force_faint", kb), r"(?m)^\[client\] \[SLink-gen3\] write overworld "),
                     (r"(?m)^\[client\] \[SLink-gen3\] write overworld ", forced),
                     (forced, gen3_tx("memorialize_done", kb))])
        self._gen3_raise(problems, f"poison_faint: {ka} fainted from field poison, {kb} force-fainted "
                                   f"at the overworld checkpoint; both saved in box {box + 1}")

    def assert_linked_faint_active_gen3_saved(self, results):
        """W-2 on FRLG/RR, the in-battle path under mechanism P+H. A: the engine's faint site
        fired, THEN the client sent faint for the linked key. Server: DEAD by battle, force_faint
        to B, then MEMORIAL. B: active_faint_chain (the commit, the hand-off, the KO with no
        input, the faint site, the case's aftermath) and no `faint` echo. Both memorials saved."""
        self._gen3_flush_boundary()
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        case = self.cfg.get("active_faint_case", "wild")
        cmd = "force_explode" if case == "explode" else "force_faint"
        # a linked mon that is its party's LAST mon (a one-mon fixture, or the whiteout case) keeps
        # its slot: the only pair died, game_over is latched, and lua/core/deferred.lua drops the
        # last-mon memorialize (ruling 21) -- LAST_MON_KEPT in that side's receipt. The link then
        # stays DEAD unless both halves were memorialized.
        lone = {inst: re.search(rf"(?m)^LAST_MON_KEPT {re.escape(key)}$", results[inst] or "") is not None
                for inst, key in (("a", ka), ("b", kb))}
        with open(os.path.join(self.data_dir, "slink.log"), encoding="utf-8") as handle:
            log_text = handle.read()
        link = self._gen3_one_link("dead" if any(lone.values()) else "memorial")
        if link.get("cause") != "battle":
            if (case == "whiteout" and link.get("cause") == "whiteout"
                    and "[a] faint refused: party hidden; waiting for a trustworthy snapshot" in log_text):
                raise RuntimeError("A faint refused: party hidden; A's whiteout retired the link "
                                   "instead of the required battle faint propagation")
            raise RuntimeError(f"link {sorted((ka, kb))} is {link.get('status')}/{link.get('cause')}, "
                               f"expected {link.get('status')}/battle")
        dead = log_text.find(f"[a] faint → {cmd} b:{kb}")
        memorial = log_text.find("fully memorialized")
        problems = []
        if dead < 0 or (not any(lone.values()) and memorial <= dead):
            problems.append(f"server log lacks DEAD propagation ({cmd} b) before MEMORIAL")
        box = self._gen3_memorial_box()
        marks = {}   # inst -> (required, ordered, forbidden) for its memorial ending
        for inst, key in (("a", ka), ("b", kb)):
            saved, fixture = self._gen3_saved(inst), self._gen3_fixture_saved(inst)
            if lone[inst]:
                deposited = re.findall(rf"(?m)^ONE_MON_PARTY {re.escape(key)} deposited=(\S+)$", results[inst] or "")
                # both sides' linked mon fought: A's lost its lead, B's took the engine KO
                problems += gen3_last_mon_problems(inst, saved, fixture, key, [d for d in deposited if d != "-"],
                                                   box, rr=self._gen3_rr, limits=self._gen3_limits(inst),
                                                   battled=True)
                kept = rf"(?m)^LAST_MON_KEPT {re.escape(key)}$"
                dropped = rf"(?m)^\[client\] .*memorialize dropped: last mon after game over {re.escape(key)}\b"
                marks[inst] = ([gen3_rx("memorialize", key), r"(?m)^RX game_over\b", dropped, kept],
                              [(r"(?m)^RX game_over\b", dropped), (dropped, kept),
                               (kept, r"(?m)^SAVE_WITNESS_DUMP ")],
                              [gen3_tx("memorialize_done", key)])
            else:
                # A is the side whose engine faint site fires: it fights NATURALLY in every case
                # (wild/trainer/lhammer/...) and can legitimately gain EXP before it goes down.
                # B's KO is engine-forced with no input (active_faint_chain), so its growth only
                # moves when the case gives it real turns first (the "trainer" case, PREP_LEVEL).
                # PYDEC FAIL on the RR final cut (d9a928d7): the memorial for A's key differed in
                # experience because this mask never covered A's own natural growth.
                source = None
                if self.game == "gen3_exp":
                    source, source_problems = exp_faint_oracle.memorial_source(results[inst], key)
                    problems += [f"{inst}: {problem}" for problem in source_problems]
                problems += gen3_memorial_problems(inst, saved, fixture, key, box, rr=self._gen3_rr,
                                                   limits=self._gen3_limits(inst), battled=True,
                                                   trained=inst == "a" or case == "trainer",
                                                   expansion_memorial_source=source,
                                                   expansion_require_zero=self.game == "gen3_exp" and inst == "b")
                done = gen3_tx("memorialize_done", key)
                marks[inst] = ([done], [(done, r"(?m)^SAVE_WITNESS_DUMP ")], [])
        req_a, ord_a, forb_a = marks["a"]
        if self.game == "gen3_exp":
            # Expansion natural A faint uses pinned SetValuesOnFaint plus the raw frame-end
            # party HP0 watcher. B's P+H path also requires that engine site, counter
            # increment and independent save readback.
            problems += exp_faint_oracle.natural_faint_receipt_problems(results["a"], ka)
            problems += gen3_receipt_problems("a", results["a"],
                                             required=[gen3_tx("faint", ka), *req_a],
                                             ordered=ord_a, forbidden=[r"(?m)^RX force_", *forb_a])
        else:
            site = r"(?m)^ENGINE_FAINT_SITE "
            problems += gen3_receipt_problems(
                "a", results["a"], required=[site, gen3_tx("faint", ka), *req_a],
                ordered=[(site, gen3_tx("faint", ka)), *ord_a], forbidden=[r"(?m)^RX force_", *forb_a])
        required, ordered, forbidden = active_faint_chain(kb, case)
        req_b, ord_b, forb_b = marks["b"]
        problems += gen3_receipt_problems("b", results["b"], required=required + req_b,
                                          ordered=ordered + ord_b, forbidden=forbidden + forb_b)
        self._gen3_raise(problems, f"linked_faint_active[{case}]: engine faint {ka} -> {cmd} -> the "
                                   f"hand-off commit on active {kb} -> engine KO in battle with no "
                                   f"input; memorials saved (last mon kept: "
                                   f"{','.join(i for i in 'ab' if lone[i]) or 'none'})")

    assert_linked_faint_active_whiteout_gen3_saved = assert_linked_faint_active_gen3_saved
    assert_linked_faint_active_trainer_gen3_saved = assert_linked_faint_active_gen3_saved
    assert_linked_faint_active_lhammer_gen3_saved = assert_linked_faint_active_gen3_saved
    assert_linked_faint_active_mega_gen3_saved = assert_linked_faint_active_gen3_saved

    def assert_active_end_gen3_saved(self, results):
        """A2 under P+H: A's chain (active_faint_chain "command"), its saved party the fixture's
        with the key's HP 0 in slot 0 (the ENGINE wrote it: no SLink HP write, ACTIVE_KO) and no
        boxed copy; B idle -- no game-state write (only its companion's own
        native-arena config/link_panel writes), no state command, no save, battery unchanged."""
        self._gen3_flush_boundary()
        key = self._link_keys["a"]
        required, ordered, forbidden = active_faint_chain(key, "command")
        forbidden += [gen3_rx("memorialize", key), r"(?m)^TX whiteout "]
        problems = gen3_receipt_problems("a", results["a"], required=required, ordered=ordered,
                                         forbidden=forbidden)
        # B's companion writes its own config / link_panel mailbox on every run (and how many before the
        # driver finishes is timing): the contract is "no Soul Link game-state write", by scope, not count.
        with open(gen3_profile_path(self._gen3_title("b")), encoding="utf-8") as handle:
            arena = json.load(handle)["native"]["BASE"]
        problems += gen3_receipt_problems("b", results["b"], forbidden=[r"(?m)^SAVE_WITNESS_DUMP "])
        problems += gen3_idle_peer_write_problems("b", results["b"], arena, arena + GEN3_NATIVE_ARENA_SIZE)
        party, boxes = self._gen3_saved("a")
        f_party, _ = self._gen3_fixture_saved("a")
        keys = [gen3_key(m) for m in party]
        if keys != [gen3_key(m) for m in f_party] or not keys or keys[0] != key:
            problems.append(f"a: saved party {keys} is not the fixture's with {key} in slot 0")
        elif party[0]["hp"] != 0:
            problems.append(f"a: the saved {key} has HP {party[0]['hp']}, not 0")
        else:
            problems += gen3_record_problems(f"a: the saved {key}", party[0], self._gen3_rr,
                                             self._gen3_limits("a"))
        if any(gen3_key(m) == key for m in boxes.values()):
            problems.append(f"a: {key} also has a boxed copy")
        codec = gen3_codec()
        if codec.split_rtc(self._gen3_flushed("b"))[0] != codec.split_rtc(self._gen3_fixture_bytes("b"))[0]:
            problems.append("b: the idle peer's battery changed")
        self._gen3_raise(problems, f"active_end: force_faint {key} -> P+H commit -> hand-off -> Perish "
                                   f"KO in battle with no input -> send-out -> RUN; saved HP 0")

    def assert_boxsync_gen3_saved(self, results):
        """The linked pair made a full round trip on both cartridges: deposit and withdraw by hand
        on A, mirrored by the server and executed by B's client, keyed ACKs in order, and the
        saved party holds each half once with the fixture's record (level/stats/exp) and no copy
        left in any box."""
        self._gen3_flush_boundary()
        self._gen3_one_link("alive")
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        problems = []
        for inst, key in (("a", ka), ("b", kb)):
            # only A walks with its linked mon in the party (grass -> PC); B's is idle
            problems += gen3_round_trip_problems(inst, self._gen3_saved(inst),
                                                 self._gen3_fixture_saved(inst), key,
                                                 rr=self._gen3_rr, limits=self._gen3_limits(inst),
                                                 walked=inst == "a")
        chain_a = [gen3_tx("party_to_box", ka), gen3_boxed(ka), gen3_tx("box_to_party", ka),
                   gen3_returned(ka)]
        problems += gen3_receipt_problems(
            "a", results["a"], required=chain_a, ordered=list(zip(chain_a, chain_a[1:], strict=False)))
        chain = [gen3_rx("box_mon", kb), gen3_tx("stats_cache", kb), gen3_boxed(kb),
                 gen3_rx("party_mon", kb), gen3_tx("sync_retrieve_done", kb), gen3_returned(kb)]
        problems += gen3_receipt_problems(
            "b", results["b"], required=chain, ordered=list(zip(chain, chain[1:], strict=False)),
            forbidden=[gen3_tx("box_mon_failed", kb), gen3_tx("sync_retrieve_failed", kb)])
        self._gen3_raise(problems, f"boxsync: {ka} / {kb} deposited and withdrawn, saved once in "
                                   f"party with the fixture record")

    def assert_center_controls_gen3_saved(self, results):
        """G4 item 2a (4): A's one save (the Cable Club's) holds the linked mon in its party with
        the fixture record -- it is taken BEFORE any probe moves a byte, so it is no persistence
        proof of the later deposit/withdraw; each keyed probe arrived only after its CONTROL_LIVE,
        was REFUSED in its witnessed state by a named clause, and landed only after the release
        (ACK + read-back). The cable probe stays the same box_mon from the menu through the link
        wait."""
        self._gen3_flush_boundary()
        self._gen3_one_link("alive")
        ka = self._link_keys["a"]
        problems = gen3_round_trip_problems("a", self._gen3_saved("a"), self._gen3_fixture_saved("a"),
                                            ka, rr=self._gen3_rr, limits=self._gen3_limits("a"),
                                            walked=True)
        chain = center_controls_chain(ka)
        problems += gen3_receipt_problems(
            "a", results["a"], required=chain, ordered=list(zip(chain, chain[1:], strict=False)),
            forbidden=[gen3_tx("box_mon_failed", ka), gen3_tx("sync_retrieve_failed", ka)])
        problems += cable_callback_problems(
            "a", results["a"], gen3_sym(self._gen3_title("a"), "LinkCB_RequestPlayerDataExchange"))
        self._gen3_raise(problems, f"center_controls: {ka} held at the Cable Club menu, the cable "
                                   f"link wait and the Union Room attendant; landed once released")

    def assert_save_then_write_gen3_saved(self, results):
        """The regression's persisted half: two saves (the witness already checked the second is
        the flushed battery), the stale sSaveDialogCB observed between them, the keyed box_mon
        received only after WRITE_PROBE_READY, ACKed with no failure, read back boxed, and the
        final save holding A's linked mon in a box and not in the party. The pair stays alive."""
        self._gen3_flush_boundary()
        self._gen3_one_link("alive")
        ka = self._link_keys["a"]
        party, boxes = self._gen3_saved("a")
        problems = []
        if ka in [gen3_key(m) for m in party]:
            problems.append(f"a: the final save still holds {ka} in the party")
        if ka not in [gen3_key(m) for m in boxes.values()]:
            problems.append(f"a: the final save holds {ka} in no box")
        required, ordered = save_then_write_order(ka)
        problems += gen3_receipt_problems("a", results["a"], required=required, ordered=ordered,
                                          forbidden=save_then_write_forbidden(ka))
        self._gen3_raise(problems, f"save_then_write: {ka} boxed by a keyed write on an idle field after "
                                   f"an in-game save (stale sSaveDialogCB observed); saved twice")

    def assert_whiteout_gen3_saved(self, results):
        """One whiteout, one rebuild, no deaths: A whited out with the pair boxed on both sides,
        the server rebuilt the pair (rebuild_start, party_mon, rebuild_done on A; party_mon on B),
        each half is back in its saved party with the fixture's record, the pair is still alive
        and nobody memorialized it."""
        self._gen3_flush_boundary()
        self._gen3_one_link("alive")
        ka, kb = self._link_keys["a"], self._link_keys["b"]
        problems = []
        for inst, key in (("a", ka), ("b", kb)):
            # only A walks with its linked mon in the party (grass -> PC); B's is idle
            problems += gen3_round_trip_problems(inst, self._gen3_saved(inst),
                                                 self._gen3_fixture_saved(inst), key,
                                                 rr=self._gen3_rr, limits=self._gen3_limits(inst),
                                                 walked=inst == "a")
        # TX whiteout, not the driver's WHITED_OUT marker, opens the chain: the rebuild commands
        # answer the event and can land while the whiteout scene is still playing.
        # BOXED_OBSERVED / RETURNED_OBSERVED are the cartridge read back (absent from the party
        # AND in a box; back in the party AND in no box): an ACK alone is the client's word, and a
        # no-op deposit that still ACKed would otherwise pass (Codex C4-6b finding 2).
        # rebuild_start is NOT ordered against party_mon: the server queues the party_mons first
        # and rebuild_start after them, in one reply (server/state.py _queue_rebuild_commands;
        # live whiteout_gen3 r3 on 123c6c45: `RX party_mon` then `RX rebuild_start`). It must
        # answer the whiteout and precede rebuild_done -- the Gen 1 oracle's own rule.
        chain_a = [gen3_boxed(ka), gen3_tx("whiteout", "-"),
                   gen3_rx("party_mon", ka), gen3_tx("sync_retrieve_done", ka),
                   r"(?m)^RX rebuild_done\b", gen3_returned(ka)]
        start = r"(?m)^RX rebuild_start\b"
        # G4 item 2a (docs/gen3/G4_request_draft.md): the Center state with the Union Room set
        # live, the rebuild write landing at the Center landing tile, and -- after the save -- a
        # queued box_mon held with zero writes while the nurse's script is live. A never ACKs
        # that box_mon (TX stats_cache would be the held write landing).
        center, landed = r"(?m)^CENTER_STATE ", r"(?m)^WRITE_IN_CENTER "
        if gen3_emerald_engine(self._gen3_title("a")):
            # E4c replacements, each for a stated Emerald fact (pret pokeemerald c65e93f2):
            #  CENTER_STATE (Union Room set live at the Center landing) -> LANDING_STATE at A's
            #    fixture's own lastHealLocation (SaveBlock1 +0x1C WarpData, include/global.h:581-588,
            #    990; the pc fixture's is 0.10 (6,17), HEAL_LOCATION_OLDALE_TOWN) with every party
            #    mon at max HP: DoWhiteOut heals in C and warps there, outdoors
            #    (overworld.c:357-366,665-668), so there is no Center landing and no Union Room set.
            #  WRITE_IN_CENTER -> WRITE_AT_LANDING at that same tile: same checks (checkpoint
            #    clean, pointers unchanged, keyed 100-byte record in the write frame), minus the
            #    Union Room set, which an outdoor tile never has.
            tile = self._gen3_fixture_heal_tile("a")
            center = rf"(?m)^LANDING_STATE {tile} .* healed=\S+ "
            landed = rf"(?m)^WRITE_AT_LANDING {tile} "
        control_name = self._gen3_whiteout_control()
        control = [gen3_returned(ka), rf"(?m)^CONTROL_LIVE {control_name} {re.escape(ka)} ",
                   gen3_rx("box_mon", ka),
                   rf"(?m)^CONTROL_REFUSED {control_name} box_mon {re.escape(ka)} clause=\S+ "]
        problems += gen3_receipt_problems(
            "a", results["a"], required=chain_a + [start,r"(?m)^WHITED_OUT\b", center, landed] + control,
            ordered=list(zip(chain_a, chain_a[1:], strict=False))
            + [(gen3_tx("whiteout", "-"), start), (start, r"(?m)^RX rebuild_done\b")]
            + [(gen3_tx("whiteout", "-"), center), (center, landed),
               (gen3_tx("sync_retrieve_done", ka), landed)]
            + list(zip(control, control[1:], strict=False)),
            forbidden=[gen3_rx("memorialize", ka), r"(?m)^RX force_faint ", gen3_tx("stats_cache", ka)])
        chain_b = [gen3_rx("box_mon", kb), gen3_tx("stats_cache", kb), gen3_boxed(kb),
                   gen3_rx("party_mon", kb), gen3_tx("sync_retrieve_done", kb), gen3_returned(kb)]
        problems += gen3_receipt_problems(
            "b", results["b"], required=chain_b, ordered=list(zip(chain_b, chain_b[1:], strict=False)),
            forbidden=[gen3_rx("memorialize", kb), r"(?m)^RX force_faint "])
        self._gen3_raise(problems, f"whiteout: A whited out, the pair {ka} / {kb} was rebuilt "
                                   f"from both PCs and saved in party, still alive")

    def _gen3_fixture_heal_tile(self, inst) -> str:
        """The regex matching `map=G.N at=(X,Y)` for the instance's fixture lastHealLocation: SaveBlock1
        +0x1C struct WarpData {s8 mapGroup, mapNum, warpId; pad; s16 x, y} (pret pokeemerald
        include/global.h:581-588,990). Where an Emerald whiteout lands (overworld.c:665-668)."""
        codec = gen3_codec()
        body = codec.split_rtc(self._gen3_fixture_bytes(inst))[0]
        sb1 = codec.parse_flash(body, title=gen3_codec_title(self._gen3_title(inst)))["sb1"]
        group, num = int.from_bytes(sb1[0x1C:0x1D], "little", signed=True), int.from_bytes(
            sb1[0x1D:0x1E], "little", signed=True)
        x, y = (int.from_bytes(sb1[at:at + 2], "little", signed=True) for at in (0x20, 0x22))
        if self._gen3_title(inst) == GEN3_EXP_TITLE:
            # X3: the expansion build whites out INTO the heal location's respawn map
            # (OW_WHITEOUT_CUTSCENE >= GEN_4), harness_facts.json whiteout_respawns
            facts = os.path.join(REPO, "data", "games", GEN3_PACKS[GEN3_EXP_TITLE], "harness_facts.json")
            with open(facts, encoding="utf-8") as handle:
                rows = json.load(handle)["whiteout_respawns"]
            row = next(r for r in rows if r["heal"] == [group, num, x, y] and "respawn" in r)
            group, num, x, y = row["respawn"]
        return rf"map={group}\.{num} at=\({x},{y}\)"

    def _gen3_whiteout_control(self) -> str:
        """whiteout_gen3's post-save negative control. FR/LG: the Center nurse's live script.
        G5-RR-NURSE-2: RR's nurse cutscene never refuses (no message/waitmessage in her script),
        so RR holds the probe in the START menu (start_menu). E4c: Emerald lands OUTDOORS (no
        nurse), so it takes the START-menu control too, with its own menu witness."""
        return "start_menu" if self._gen3_rr or gen3_emerald_engine(self._gen3_title("a")) else "nurse"

    def _gen3_sent_event(self, text, event, key):
        """The decoded JSON of the first `TX <event> <key> {...}` line, or None."""
        match = re.search(gen3_tx(event, key) + r" (\{.*\})\s*$", text or "", re.M)
        return json.loads(match.group(1)) if match else None

    def _gen3_sent_field(self, text, event, key, field):
        match = re.search(gen3_tx(event, key) + r".*\"" + field + r"\":(\"[^\"]*\"|-?\d+)", text or "")
        return json.loads(match.group(1)) if match else None

    def assert_link_gen3_saved(self, results, *, native_ball_grant=0):
        """D-1 persisted on FRLG: one ALIVE route_1 pair of the two real captures; each capture is
        in its saved party exactly once (appended, boxed nowhere, species as sent), and each
        saved POKe BALLS pocket is the fixture's minus the throws that half logged."""
        self._gen3_flush_boundary()
        row, area = self._gen3_one_link("alive"), self._hunt_area
        problems = [] if row.get("area_id") == area else [f"link area {row.get('area_id')!r}"]
        for inst in ("a", "b"):
            key, text = self._link_keys[inst], results[inst]
            sent = self._gen3_sent_event(text, "capture", key) or {}
            if sent.get("area_id") != area:
                problems.append(f"{inst}: no capture of {key} sent for {area}")
            problems += gen3_capture_problems(inst, self._gen3_saved(inst),
                                              self._gen3_fixture_saved(inst), key, sent,
                                              rr=self._gen3_rr, limits=self._gen3_limits(inst))
            throws = len(re.findall(r"(?m)^THREW \d+", text or ""))
            title = self._gen3_title(inst)
            before = gen3_ball_count(self._gen3_fixture_bytes(inst), title) + native_ball_grant
            after = gen3_ball_count(self._gen3_flushed(inst), title)
            self._pydec_note(f"BAG_BALLS baseline={before} final={after} throws={throws} inst={inst} native_grant={native_ball_grant}")
            if throws < 1 or after != before - throws:
                problems.append(f"{inst}: saved {after} Poke Balls; the fixture had {before} and "
                                f"the driver logged {throws} throw(s)")
        self._gen3_raise(problems, f"link: {self._link_keys['a']} <-> {self._link_keys['b']} "
                                   f"alive on {area} and saved once in each party")

    def assert_deadzone_gen3_saved(self, results):
        """D-3 persisted on FRLG: route_1 is a dead zone with exactly one dead_zone event and no
        alive link; A's saved state is the fixture's (it only ran, and its no_catch named the
        foe's species); B's catch there was force-fainted and memorialized, saved in the memorial
        box and nowhere else."""
        self._gen3_flush_boundary()
        b_key, area = self._deadzone_b_key, self._hunt_area
        problems = []
        if ((self._status() or {}).get("area_states") or {}).get(area) != "dead_zone":
            problems.append(f"{area} is not a dead zone after the run")
        # the server logs dead_zone for the player AND the partner (server/server.py:2217-2220):
        # exactly one row each, never one in total (live deadzone_gen3 attempt 2 on 684bbb7a)
        rows = {}
        for row in self._reconnect_events():
            if row.get("type") == "dead_zone":
                rows[row.get("player")] = rows.get(row.get("player"), 0) + 1
        if rows != {"a": 1, "b": 1}:
            problems.append(f"events.json dead_zone rows per player are {rows}, not one each")
        for entry in self._links_json():
            keys = {(entry.get("a") or {}).get("key"), (entry.get("b") or {}).get("key")}
            if b_key in keys and entry.get("status") == "alive":
                problems.append(f"B's dead-zone catch {b_key} is in an alive link")
        species = self._gen3_sent_field(results["a"], "no_catch", "-", "species_id")
        if not species or self._gen3_sent_field(results["a"], "no_catch", "-", "area_id") != area:
            problems.append(f"A sent no species-bearing no_catch for {area}")
        a_party, a_boxes = self._gen3_saved("a")
        f_party, f_boxes = self._gen3_fixture_saved("a")
        if [gen3_key(m) for m in a_party] != [gen3_key(m) for m in f_party] or {
                p: gen3_key(m) for p, m in a_boxes.items()} != {
                p: gen3_key(m) for p, m in f_boxes.items()}:
            problems.append("A's saved party/boxes are not the fixture's")
        problems += gen3_memorial_problems("b", self._gen3_saved("b"), self._gen3_fixture_saved("b"),
                                           b_key, self._gen3_memorial_box(), rr=self._gen3_rr,
                                           limits=self._gen3_limits("b"), battled=True,
                                           captured=self._gen3_sent_event(results["b"], "capture", b_key))
        problems += gen3_receipt_problems(
            "b", results["b"], required=[rf"(?m)^FAINTED {re.escape(b_key)}\b",
                                         gen3_rx("memorialize", b_key),
                                         gen3_tx("memorialize_done", b_key)])
        self._gen3_raise(problems, f"deadzone: {area} locked by A's no_catch (species {species}); "
                                   f"B's {b_key} saved only in the memorial box")

    def assert_reconnect_gen3_saved(self, results):
        """C-2/C-1 persisted: A never saved and never mutated its battery -- the same-save flush is
        the pre-kill bytes and the wrong-save flush is exactly the seeded wrong save -- while B's
        saved party still holds its half of the alive pair."""
        if not self._live_complete.get(self.scenario):
            raise RuntimeError("reconnect_gen3's live legs did not complete")
        self._gen3_flush_boundary()
        codec = gen3_codec()
        self._gen3_one_link("alive")
        problems = []
        same = self._artifact(self._same_save_artifact, "C-2 same-save battery")
        if codec.split_rtc(same.read_bytes())[0] != codec.split_rtc(self._gen3_before_kill)[0]:
            problems.append("A's same-save battery differs from its pre-kill bytes")
        if codec.split_rtc(self._gen3_flushed("a"))[0] != self._gen3_wrong_body:
            problems.append("the rejected wrong-save cartridge's battery changed")
        problems += gen3_receipt_problems("a", results["a"], required=[
            r"(?m)^WRITES 0$",
            r"(?m)^WRONG_SAVE_ZERO attempted=0 writes=0 party=unchanged box=unchanged$"])
        b_keys = [gen3_key(m) for m in self._gen3_saved("b")[0]]
        if self._link_keys["b"] not in b_keys:
            problems.append(f"B's saved party {b_keys} lacks its linked {self._link_keys['b']}")
        self._gen3_raise(problems, "reconnect: A's batteries untouched across both relaunches "
                                   "(zero client writes on the wrong save); B saved its half")

    # ── RR-only oracles (P5, card C5-5) ─────────────────────────────────────────────────────
    def assert_explode_gen3_saved(self, results):
        """explode_gen3 under owner ruling 19, a qualification row: the P+H carrier's explode
        chain -- keyed force_explode, the Explosion menu skip ending in the hand-off, no input,
        lastUsedMovePlayer 153 and a measured PP drop at the KO, the attacker's own faint site -- plus the linked
        oracle's saved-state half."""
        self.assert_linked_faint_active_gen3_saved(results)

    def assert_rival_swap_gen3_saved(self, results):
        """rival_swap_gen3 is a BLOCKED NEGATIVE CONTROL, not a qualification (SCENARIOS
        `control`; Codex C4-6b finding 6): the runner queues a dummy team (100 zero bytes) with no
        session/battle_id, and the only thing characterized is the refusal -- B reached a real
        battle, the command arrived and was answered stale_battle_id: the new client declares
        battle_identity, and the C5-10 gate refuses a missing identity before anything is staged
        (2dc1b750; rival_swap_refresh_window.md §3.3). A untouched. A PASS says nothing about a
        working swap."""
        problems = gen3_receipt_problems(
            "b", results["b"], required=[r"(?m)^READY_IN_BATTLE\b",
                                         r"(?m)^RX replace_rival_team\b",
                                         gen3_tx("rival_team_replaced", "-")])
        err = self._gen3_sent_field(results["b"], "rival_team_replaced", "-", "error")
        if err != "stale_battle_id":
            problems.append(f"b: rival_team_replaced error={err!r}, expected 'stale_battle_id' "
                            f"(the identity gate's refusal of an identity-less command)")
        a_party, _ = self._gen3_saved("a")
        f_party, _ = self._gen3_fixture_saved("a")
        if [gen3_key(m) for m in a_party] != [gen3_key(m) for m in f_party]:
            problems.append("a's saved party is not the fixture's (rival_swap only drives b)")
        self._gen3_raise(problems, "rival_swap NEGATIVE CONTROL (not qualification): the dummy, "
                                   "identity-less team was refused with stale_battle_id")

    def assert_clean_rr_refused_gen3_saved(self, results):
        """clean_rr_refused_gen3: the clean-RR REFUSAL PROOF. B (raw dump): lua/gen3/run.lua's own
        "needs the SLink companion patch" refusal line, the driver's REFUSED_AT_LAUNCH, WRITES 0,
        and nothing a working client leaves (no MYKEY, no TX, no RX, no admission line, no write, no
        save dump). A (companion): admitted, hello sent, a settled window with no write and no
        command, none of the partner-driven commands. The server: B never connected and no link
        exists. Neither side saves."""
        partner_cmds = "|".join(("apply_prepare", "apply_trade", "force_faint", "force_explode", "memorialize",
                                 "party_mon", "box_mon", "replace_rival_team"))
        problems = gen3_receipt_problems(
            "a", results["a"],
            required=[r"(?m)^TX hello\b", r"(?m)^PROBE_SETTLED writes=0 rx=0$"],
            forbidden=[rf"(?m)^RX (?:{partner_cmds})\b", r"(?m)^\[client\] \[SLink-gen3\] refused"])
        problems += gen3_refused_side_problems("b", results["b"])
        problems += gen3_refused_server_problems(self._status(), "b", self._links_json())
        self._gen3_raise(problems, "clean_rr_refused REFUSAL PROOF: the clean RR was refused at launch (needs the "
                                   "SLink companion patch), never connected, linked nothing and wrote nothing; "
                                   "the companion side stayed quiet")

    def _run_oracle(self, results):
        """Revalidate the family contract and run its injected evidence stages.

        Gen 3 battery rows (FAMILY_EVIDENCE: check_save_witness_gen3, oracle required) also refuse a
        +dirty source and note every marker line their oracle consumed.
        """
        contract = evidence_contract(getattr(self, "game", "legacy"))
        if not getattr(self, "is_gen3_battery", False):
            run_pipeline(self, contract, self.cfg, results)
            return
        dirty = getattr(self, "_gen3_source_dirty", None)
        if dirty:
            raise RuntimeError(f"the receipt's source is +dirty ({', '.join(dirty[:5])}"
                               f"{', ...' if len(dirty) > 5 else ''}): a G4 receipt must come "
                               f"from a clean cut")
        self._gen3_require_companion_admission(results)
        global _CONSUMED_MARKERS
        _CONSUMED_MARKERS = consumed = []
        try:
            run_pipeline(self, contract, self.cfg, results)
        finally:
            _CONSUMED_MARKERS = None
            seen = set()
            for label, line in consumed:
                if (label, line) not in seen:
                    seen.add((label, line))
                    # the WHOLE line: CENTER_STATE / WRITE_IN_CENTER / CONTROL_REFUSED carry their
                    # tail predicates and keyed-write/ACK suffix past 400 chars (Codex, 86245d1e)
                    self._pydec_note(f"MARKER {label}: {line}")

    def _note_result_lines(self, texts):
        """Each side's own RESULT line, verbatim, beside the runner's verdict (Gen 3 rows)."""
        if not self.is_gen3_battery:
            return
        for inst in ("a", "b"):
            lines = [ln for ln in (texts.get(inst) or "").splitlines() if ln.startswith("RESULT:")]
            self._pydec_note(f"RESULT_LINE {inst}: {lines[-1] if lines else '(none)'}")

    def _live_ok(self) -> bool:
        """True when every live leg this scenario needs ran to completion."""
        if getattr(self, "cfg", {}).get("gen3_rand") or getattr(self, "cfg", {}).get("gen3_native_trade"):
            return bool(self._live_complete.get(self.scenario))
        if self.scenario not in LIVE_LEG_SCENARIOS:
            return True
        return bool(self._live_complete.get(self.scenario))

    def run(self):
        passed = False
        if getattr(self, "_pydec_path", None):
            os.makedirs(os.path.dirname(self._pydec_path), exist_ok=True)
            with open(self._pydec_path, "w", encoding="utf-8") as handle:
                handle.write(f"attempt {self.attempt} of "
                             f"{scenario_attempt_limit(self.scenario, getattr(self, 'game', ''))}\n")
        try:
            # Match __init__'s default for bare runners used by lifecycle unit tests.
            validate_pipeline(self, evidence_contract(getattr(self, "game", "legacy")),
                              self.cfg)
            if getattr(self, "is_gen3_battery", False) and (launch_problems := self._gen3_launch_refusal_problems()):
                raise RuntimeError("; ".join(launch_problems))
            if getattr(self, "gcfg", {}).get("launch_profile") == "gen2":
                from tools.gen2_source_data import shared_contexts
                with shared_contexts(), self._timed("preflight"):   # one verified context per title per run
                    self._prepare_gen2_lane()
                self._clear_attempt_artifacts()  # startup waits must not see an older RESULT
            if self.cfg.get("gen3_rand"):
                self._prepare_gen3_rand()
            if self.cfg.get("gen3_probe_flip"):
                self._prepare_gen3_probe_flip()
            if self.cfg.get("gen3_native_trade"):
                self._prepare_native_trade()
            if self.scenario == "admit_randomized_new":
                self.prepare_admit_randomized_new()
            if self.is_gen3_battery:
                # every Gen 3 receipt names its own cut (Codex receipt audit 2026-09-23)
                self._pydec_note(self._gen3_identity())
            with self._timed("server"):
                self.start_server()
            self.start_instances()
            try:
                with self._timed("orchestrate"):
                    self.orchestrate()
            except GameRngMiss:
                ra, rb = self.wait_results()
                self.assert_gen1_synth_setup({"a": ra, "b": rb}, complete=False)
                if not retryable_gen1_rng(self.game, {"a": ra, "b": rb}, self.attempt,
                                          scenario_attempt_limit(self.scenario, self.game), scenario=self.scenario):
                    raise  # an unrelated failed half is never a game-RNG retry
            else:
                with self._timed("results"):
                    ra, rb = self.wait_results()
            self._note_result_lines({"a": ra, "b": rb})
            pa = "RESULT: PASS" in ra
            pb = "RESULT: PASS" in rb
            self.assert_gen1_synth_setup({"a": ra, "b": rb}, complete=pa and pb)
            if pa and pb:
                if scenario_family(getattr(self, "game", "")) == "gen2_new":
                    self._wait_gen2_exit_flush()
                self._gen2_verified_facts = None
                with self._timed("oracle"):
                    self._run_oracle({"a": ra, "b": rb})
                if scenario_family(getattr(self, "game", "")) == "gen2_new" and not self._gen2_verified_facts:
                    raise RuntimeError("Gen 2 oracle did not report verified facts")
            if pa and pb and rng_retry_family(getattr(self, "game", "")):
                # Harness finding, not a scenario verdict: the harness wrote the expected count
                # into the stub, so the driver's echo is checkable without the game. Checked
                # only on a double PASS so a real failure keeps its own error, not this one.
                # The oracle above has already validated expected launch refusals, whose
                # drivers finish before gameplay jitter. Keep checking the other side.
                expected_jitter = self.expected_idle_jitter()
                jitter_findings = [f"{inst}: {problem}" for inst, text in (("a", ra), ("b", rb))
                                   for problem in jitter_problems(
                                       text, expected_jitter,
                                       validated_launch_refusal=(getattr(self, "is_gen3_battery", False)
                                                                 and self._gen3_expect_refused(inst)))]
                if jitter_findings:
                    raise RuntimeError("idle-jitter contract violated — "
                                       + "; ".join(jitter_findings))
            passed = pa and pb and self._live_ok()
            if getattr(self, "_pydec_path", None):
                reason = ("asserted scenario facts" if passed else
                          "live leg did not complete" if pa and pb and not self._live_ok() else
                          "client RESULT before saved-state oracle")
                if passed and scenario_family(getattr(self, "game", "")) == "gen2_new":
                    reason = " ".join(f"{key}={self._gen2_verified_facts[key]}"
                                      for key in ("a", "b", "area", "titles", "status"))
                    if self.scenario in ("gen2_reconnect", "gen2_soft_reset"):
                        reason += f" scenario={self.scenario}"
                    if self.scenario == "gen2_admit_wrong_rom":
                        reason += f" scenario={self.scenario} rom_b={self._gen2_verified_facts['rom_b']}"
                    if self.scenario == "gen2_faint_active":
                        reason += " scenario=gen2_faint_active death=active"
                    if self.scenario == "gen2_faint_active_trainer":
                        reason += " scenario=gen2_faint_active_trainer death=active battle=trainer"
                    if self.scenario in GEN2_WAVE_C:
                        key = GEN2_WAVE_C[self.scenario][1]
                        reason += f" scenario={self.scenario} {key}={self._gen2_verified_facts[key]}"
                    if self.scenario in GEN2_TRADE_SCENARIOS:
                        reason += f" scenario={self.scenario} admission_scope=PHYSICAL_RECEIPTED"
                    if self.scenario in GEN2_CLAUSE_SCENARIOS:
                        extra = ("clause", "rerolls") if self.scenario == "gen2_species_clause" else ("clause", "rejected", "ending")
                        reason += f" scenario={self.scenario} " + " ".join(
                            f"{key}={self._gen2_verified_facts[key]}" for key in extra)
                if getattr(self, "_code_stamp", None) is not None:
                    self._pydec_note("CODE_DIGEST " + json.dumps(self._code_stamp, sort_keys=True))
                self._pydec_note(f"PYDEC: {'PASS' if passed else 'FAIL'} {reason}")
            print(f"[duo] {self.scenario}: a={'PASS' if pa else 'FAIL'} "
                  f"b={'PASS' if pb else 'FAIL'}")
            if not passed:
                for inst, text in (("a", ra), ("b", rb)):
                    print(f"--- {self.scenario} {inst} result ---")
                    print("\n".join(text.splitlines()[-25:]))
            if self.args.keep_alive:
                input("[duo] --keep-alive: press Enter to tear down…")
        except ClientFinishedEarly as exc:
            if self.cfg.get("gen1_synth") == "explode":
                try:
                    self.assert_gen1_synth_setup(
                        {inst: self._read_receipt(inst) or "" for inst in ("a", "b")}, complete=False)
                except RuntimeError as binding_error:
                    self._pydec_note(f"PYDEC: FAIL {binding_error}")
                    raise
            if self.scenario == "gen2_species_clause":
                # Preserve the pre-cleanup process/RESULT observation: cleanup kills A before
                # its normal waiting-for-link timeout when B exhausts the duplicate hunt.
                self._gen2_species_early_finish = exc
            # Not an error of the run: the cartridges ended while a wait was still pending, so
            # the receipts on disk are the verdict. Recorded, printed (the lane's evidence
            # collection reads these tails), torn down, and returned as False so
            # run_scenario_with_rng_retry still classifies them (a mid-wait ball miss keeps its
            # retry; anything else fails as the receipts say).
            if getattr(self, "_pydec_path", None):
                self._note_result_lines({inst: self._read_receipt(inst) or "" for inst in ("a", "b")})
                self._pydec_note(f"PYDEC: FAIL client RESULT before {exc.awaited}")
            print(f"[duo] {self.scenario}: {exc}")
            for inst in ("a", "b"):
                text = self._read_receipt(inst) or ""
                print(f"--- {self.scenario} {inst} result (last 25 lines) ---")
                print("\n".join(text.splitlines()[-25:]))
            return False
        except Exception as exc:
            if getattr(self, "_pydec_path", None):
                self._pydec_note(f"PYDEC: FAIL {exc}")
            raise
        finally:
            self.cleanup(passed)
        return passed


def list_lines(game):
    """`--list`'s output: each scenario with the attempt limit and the fixtures it boots.

    Lane cards quote these, so the table's own two load-bearing numbers are printed rather than
    looked up again: `scenario_attempt_limit` (how many whole runs the scenario may take) and
    the per-instance `target` (which fixture each half boots).
    """
    lines = []
    for name in scenarios_for(game):
        targets = (GAMES[game]["fixture"] if GAMES[game].get("launch_profile") == "gen2"
                   else scenario_target(SCENARIOS[name], game))
        if name == "gen2_admit_wrong_rom":
            targets = {**targets, "b": "crystal_battle_ot2"}
        if name in GEN2_TRADE_SCENARIOS:
            targets = GEN2_TRADE_FIXTURES[game]
        if name == "gen2_trade_evolve":
            targets = GEN2_TRADE_EVOLVE_FIXTURES[game]
        if name == "gen2_poison":
            targets = {**targets, **GEN2_POISON_FIXTURES.get(game, {})}
        if name == "gen2_ball_gate":
            targets = GEN2_BALL_GATE_FIXTURES[game]
        if name == "gen2_faint_active_trainer":
            targets = GEN2_TRAINER_FIXTURES[game]
        if name in GEN2_SYNTH_SCENARIOS:
            targets = {inst: gen2_synth_name(name, game, inst) for inst in ("a", "b")}
        shown = (", ".join(f"{inst}:{targets[inst]}" for inst in ("a", "b"))
                 if isinstance(targets, dict) else targets)
        lines.append(f"{name}  attempts={scenario_attempt_limit(name, game)}  targets={shown}")
        if name in GEN2_TRADE_SCENARIOS:
            lines[-1] += " artifact=overlay admission=PHYSICAL_RECEIPTED"
    return lines


def summary_lines(results, game):
    """One line per scenario, with a failure's reason attached when it has one. A scenario that
    declares `control` is labelled as such: its PASS is a characterization, not qualification."""
    lines = []
    for name, outcome in results.items():
        ok, attempt = outcome[0], outcome[1]
        reason = f" — {outcome[2]}" if len(outcome) > 2 else ""
        if ok is None:
            allowed = len(outcome) > 3 and outcome[3]
            lines.append(f"  {name}: SKIP{' (allowed: signed limit)' if allowed else ''}{reason}")
            continue
        control = SCENARIOS.get(name, {}).get("control")
        if control:
            reason += f" [CONTROL, not a qualification pass: {control}]"
        lines.append(f"  {name}: {'PASS' if ok else 'FAIL'} "
                     f"(attempt {attempt} of {scenario_attempt_limit(name, game)}){reason}")
    return lines


def lua_literal(v) -> str:
    """A Python value as a Lua literal for the SLINK_DUO stub: strings quoted, bools lowercase,
    dicts as string-keyed tables (the Lua side indexes clause_facts by tostring(species)), lists
    as sequences. Dicts/lists used to be written with Python repr -- a Lua syntax error that
    stopped both EmuHawk instances at load (CLAUSE-ROWS-G3 live, 2026-09-27)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, dict):
        return "{" + ", ".join(f"[{lua_literal(str(k))}] = {lua_literal(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_literal(x) for x in v) + "}"
    return str(v)


def scenario_target(entry, game):
    """A scenario's fixture `target` (a string, or {"a", "b"}) on `game`: `target_by_game[game]`
    when the row needs a different fixture there (RR R1 boots rr_battle2)."""
    return (entry.get("target_by_game") or {}).get(game, entry.get("target", "town"))


def skip_reason(name, game):
    """(why, allowed) when `name` cannot launch on `game`, else None. `signed_limit` is an owner-
    signed limit: an ALLOWED skip (exit_code 0). A missing fixture on an RR row (GAMES[...]["rr"])
    is not: rr_battle2 is still being built, and a FAIL at fixture lookup would read as a product
    failure, so it SKIPs by name and exit_code says 3. A SKIP is never a PASS."""
    entry, row = SCENARIOS.get(name, {}), GAMES.get(game, {})
    if entry.get("gen3_rand"):
        problems = gen3_rand_dependencies()
        if problems:
            return "BLOCKED: " + "; ".join(problems), False
    if entry.get("signed_limit"):
        return f"SIGNED LIMIT: {entry['signed_limit']}", True
    if row.get("rr") and row.get("sides"):
        target = scenario_target(entry, game)
        missing = []
        for inst in ("a", "b"):
            stem = row["sides"][inst][1].format(target=target[inst] if isinstance(target, dict) else target)
            if not os.path.isfile(os.path.join(GEN3_FIXTURES, stem + ".sav")):
                missing.append(f"tests/fixtures/gen3/{stem}.sav")
        if missing:
            return f"fixture(s) {', '.join(missing)} not built yet (RR battle fixtures)", False
    return None


def exit_code(results) -> int:
    """0 when every scenario PASSed or took an ALLOWED skip (a signed limit, outcome
    (None, 0, why, True)); a FAIL is 1; any other SKIP with no FAIL is 3."""
    if any(outcome[0] is False for outcome in results.values()):
        return 1
    blocking = [o for o in results.values() if o[0] is None and not (len(o) > 3 and o[3])]
    return 3 if blocking else 0


def _archive_attempt(name, attempt, receipts, *, archive_dir=None):
    """Keep every attempt's receipts and PYDEC copy before clearing the live files.

    Gen 1 uses a fresh pairing/invocation directory outside the live cleanup prefix;
    exclusive writes keep those copies immutable. Live runtime paths stay unchanged.
    Called on EVERY exit path, after any final D-4 annotation has been written.
    """
    directory = BUILD if archive_dir is None else archive_dir
    archived = _archive_gen2_witnesses(name, attempt, receipts)
    for inst in ("a", "b"):
        if inst in archived:
            continue
        if receipts[inst] is not None:
            with open(os.path.join(directory, f"e2e_{name}_{inst}_attempt{attempt}_result.txt"),
                      "w" if archive_dir is None else "x", encoding="utf-8") as handle:
                handle.write(receipts[inst])
    pydec = os.path.join(BUILD, f"e2e_{name}_pydec_result.txt")
    if os.path.exists(pydec):
        target = os.path.join(directory, f"e2e_{name}_pydec_attempt{attempt}_result.txt")
        if archive_dir is None:
            shutil.copyfile(pydec, target)
        else:
            with open(pydec, "rb") as source, open(target, "xb") as kept:
                shutil.copyfileobj(source, kept)


def _archive_gen2_witnesses(name, attempt, receipts):
    """Authenticate and preserve immutable witnesses, retaining original marker text.

    Reconnect's final A receipt has no witness; its initial-phase receipt still names the
    original A snapshot. A manifest links those original paths to byte-identical archives.
    """
    if not any(line.startswith("DUO_GEN2 ") for text in receipts.values() for line in (text or "").splitlines()):
        return set()
    sources = {side: text for side, text in receipts.items() if text is not None}
    for phase in ("initial", "same_save", "wrong_save"):
        path = Path(BUILD, f"e2e_{name}_a_{phase}_result.txt")
        if path.exists():
            sources["a_" + phase] = path.read_text(encoding="utf-8")
    planned, witnesses = {}, []
    for side, text in sources.items():
        headers = [json.loads(line.partition(" ")[2]) for line in text.splitlines() if line.startswith("DUO_GEN2 ")]
        rows = [json.loads(line.partition(" ")[2]) for line in text.splitlines() if line.startswith("SAVE_WITNESS ")]
        if not rows:
            continue
        if (len(headers) != 1 or not isinstance(headers[0], dict)
                or headers[0].get("player") != side.split("_")[0] or headers[0].get("attempt") != attempt):
            raise RuntimeError("Gen 2 archived witness has stale or contradictory header")
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("Gen 2 archive requires one final SAVE_WITNESS")
        witness = rows[0]
        if "snapshot_path" not in witness:
            continue  # Older failed attempts may predate the additive snapshot producer.
        original_side = "a" if side == "a_initial" else side
        source = Path(witness["snapshot_path"])
        expected = Path(BUILD, f"e2e_{name}_{original_side}_witness.SaveRAM")
        if not source.is_absolute() or source.resolve() != expected.resolve():
            raise RuntimeError("Gen 2 witness snapshot belongs to another lane or phase")
        raw = source.read_bytes()
        if len(raw) != 32790 or hashlib.sha256(raw[:32768]).hexdigest() != witness.get("cartram_sha256"):
            raise RuntimeError("Gen 2 archived snapshot differs from witness digest/length")
        target = Path(BUILD, f"e2e_{name}_{side}_attempt{attempt}_witness.SaveRAM")
        exit_source = Path(witness["saveram_path"])
        if not exit_source.is_absolute():
            raise RuntimeError("Gen 2 witness exit save path must be absolute")
        exit_raw = exit_source.read_bytes()
        exit_target = Path(BUILD, f"e2e_{name}_{side}_attempt{attempt}_exit.SaveRAM")
        receipt_path = Path(BUILD, f"e2e_{name}_{side}_attempt{attempt}_result.txt")
        planned[target] = raw
        planned[exit_target] = exit_raw  # Preserve failures too: do not replace this with the snapshot.
        if side not in ("a", "b"):
            planned[receipt_path] = text.encode("utf-8")
        witnesses.append({"source": str(source), "archive": str(target), "receipt": str(receipt_path),
                          "sha256": hashlib.sha256(raw).hexdigest(), "receipt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          "exit_source": str(exit_source), "exit_archive": str(exit_target),
                          "exit_sha256": hashlib.sha256(exit_raw).hexdigest(), "exit_bytes": len(exit_raw)})
    if not planned:
        return set()
    # Byte-preserve every phase receipt, including the relaunches without a native save.
    for side, text in sources.items():
        planned[Path(BUILD, f"e2e_{name}_{side}_attempt{attempt}_result.txt")] = text.encode("utf-8")
    manifest = Path(BUILD, f"e2e_{name}_attempt{attempt}_manifest.json")
    planned[manifest] = json.dumps({"attempt": attempt, "witnesses": witnesses}, sort_keys=True).encode("utf-8")
    if any(path.exists() for path in planned):
        raise RuntimeError("Gen 2 witness archive already exists; refusing overwrite")
    for path, raw in planned.items():
        with path.open("xb") as handle:
            handle.write(raw)
    return set(sources)


def run_scenario_with_rng_retry(name, args):
    """A new DuoRun for each attempt means a new server/data dir and reseeded battery saves.

    Two retry reasons share one attempt budget. The ball RNG is the original one
    (`retryable_gen1_rng`, attempt 1 only). The second is species_clause_new's own: D-4's
    reroll branch is a coin flip on Route 1's 6-Pidgey/4-Rattata table, so a run that PASSED
    without observing it is not D-4 evidence — the whole scenario is re-run (fresh server,
    fresh data dir, reseeded battery saves, jittered idle count) up to the limit, and if it is
    still unobserved the PASS stands with a line saying so. A FAILED attempt is never
    re-run for this reason: retrying a real failure would only multiply it.
    """
    limit = scenario_attempt_limit(name, args.game)
    reroll_retry = name == "species_clause_new" and args.game.startswith("gen1")
    unobserved_pass = None  # the last attempt that PASSED without the reroll branch
    archive_dir = None
    if args.game.startswith("gen1"):
        parent = os.path.join(BUILD, "gen1-attempts")
        os.makedirs(parent, exist_ok=True)
        archive_dir = tempfile.mkdtemp(prefix=f"{args.game}_{name}_", dir=parent)
        print(f"[duo] immutable attempt evidence: {archive_dir}")
    archive_kwargs = {"archive_dir": archive_dir} if archive_dir is not None else {}
    for attempt in range(1, limit + 1):
        print(f"[duo] {name}: attempt {attempt} of {limit}")
        print(f"[duo] JITTER requested={jitter_for_attempt(args.idle_jitter, attempt)} "
              f"attempt={attempt}")
        artifact = name
        try:
            run = DuoRun(name, args, attempt=attempt)
            artifact = getattr(run, "artifact_name", name)
            ok = run.run()
        except Exception as exc:
            # An oracle failure is not RNG: the scenario is lost, and the lane needs the
            # summary block with the reason rather than a traceback (run() has already written
            # its own PYDEC: FAIL line).
            receipts = {inst: read_result(artifact, inst) for inst in ("a", "b")}
            _archive_attempt(artifact, attempt, receipts, **archive_kwargs)
            reason = f"{type(exc).__name__}: {exc}"
            if SCENARIOS[name].get("rule_kind"):
                from gen3_clause_rows import ClauseUnobserved
                if isinstance(exc, ClauseUnobserved) and attempt < limit:
                    print(f"[duo] {name}: {exc}; retrying fresh lane")
                    continue
            if name in GEN2_CLAUSE_SCENARIOS and scenario_family(args.game) == "gen2_new":
                oracle = importlib.import_module("gen2_duo_oracles")
                if isinstance(exc, oracle.ClauseUnobserved) and attempt < limit:
                    print(f"[duo] {name}: clause unobserved; retrying fresh lane")
                    continue
            print(f"[duo] {name}: attempt {attempt} aborted — {reason}")
            return False, attempt, reason
        receipts = {inst: read_result(artifact, inst) for inst in ("a", "b")}
        if reroll_retry and ok:
            state = species_reroll_state(receipts)
            if state == "observed":
                _archive_attempt(name, attempt, receipts, **archive_kwargs)
                print(f"[duo] species_clause_new: reroll branch observed on attempt {attempt}")
                return ok, attempt
            if attempt < limit:
                unobserved_pass = attempt
                _archive_attempt(name, attempt, receipts, **archive_kwargs)
                print("[duo] species_clause_new: reroll branch not observed on attempt "
                      f"{attempt}; re-running the whole scenario with fresh state")
                continue
            line = (f"[duo] species_clause_new: reroll branch NOT observed after {limit} "
                    f"attempts (D-4 stays partial)")
            print(line)
            pydec = os.path.join(BUILD, f"e2e_{name}_pydec_result.txt")
            if os.path.exists(pydec):
                with open(pydec, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            _archive_attempt(name, attempt, receipts, **archive_kwargs)  # AFTER the annotation
            return ok, attempt
        _archive_attempt(artifact, attempt, receipts, **archive_kwargs)
        if (not ok and name == "gen2_species_clause" and scenario_family(args.game) == "gen2_new"
                and attempt < limit and gen2_species_rng_miss(receipts,
                    early_finish=getattr(run, "_gen2_species_early_finish", None),
                    pending_snapshot=getattr(run, "_gen2_clause_pending", None))):
            print(f"[duo] {name}: duplicates-only hunt; retrying fresh lane")
            continue
        gen2_failures = [line for inst in ("a", "b") for line in (receipts.get(inst) or "").splitlines()
                         if line.startswith("RESULT: FAIL")]
        if (not ok and name == "gen2_ball_gate" and attempt < limit
                and gen2_failures and all(GEN2_OUT_OF_BALLS in line for line in gen2_failures)):
            print(f"[duo] {name}: a side ran out of the aide's natural Balls; retrying fresh lane")
            continue
        if (not ok and (name in GEN2_TRADE_SCENARIOS or name in GEN2_ROUTE_LOSS_SCENARIOS)
                and scenario_family(args.game) == "gen2_new"
                and attempt < limit
                and gen2_failures and all(GEN2_ROUTE_BATTLE_LOST in line for line in gen2_failures)):
            print(f"[duo] {name}: the route's wild battle was lost on attempt {attempt}; retrying fresh lane "
                  f"on the next pinned clock minute")
            continue
        if SCENARIOS[name].get("battle_window_case"):
            classification = battle_window_failure(name, receipts, attempt) if not ok else None
            if classification:
                print(f"[duo] {name}: NOT_SUBJECT retry={classification['retry']} {classification['reason']}")
            if not ok and attempt < limit and classification and classification["retry"]:
                print(f"[duo] {name}: restarting the whole T2 attempt with fresh server and seeds")
                continue
            return ok, attempt
        if ok or attempt >= limit or not retryable_gen1_rng(args.game, receipts, attempt, limit, scenario=name):
            # A re-run that only went looking for the reroll branch and then lost the game's
            # RNG (a ball miss past its retries) does not undo the PASS it re-ran; a real
            # (FINAL) failure still fails. The attempt-1 rule is the "RNG-shaped" test.
            if (not ok and unobserved_pass
                    and retryable_gen1_rng(args.game, receipts, 1, limit=2)):
                line = (f"[duo] species_clause_new: attempt {attempt} ended on the game's RNG; "
                        f"the unobserved PASS of attempt {unobserved_pass} stands "
                        f"(D-4 stays partial)")
                print(line)
                with open(os.path.join(BUILD, f"e2e_{name}_pydec_result.txt"), "a",
                          encoding="utf-8") as handle:
                    handle.write(line + "\n")
                return True, unobserved_pass
            return ok, attempt
        why = ("B's first wild foe shared no type with A" if name == "type_clause_gen3" and
               "RESULT: FAIL (RNG: type first encounter has no overlap)" in
               (receipts.get("b") or "").splitlines() else "the game reported a retryable RNG result")
        print(f"[duo] {name}: {why}; restarting attempt {attempt + 1} of {limit} "
              "with a fresh server, run directory and SaveRAM seeds")
    return False, limit


def gen2_species_rng_miss(receipts, *, early_finish=None, pending_snapshot=None):
    """Only the driver's bounded duplicate hunt and its waiting partner may retry."""
    expected = {"a": "RESULT: FAIL (the link never formed)",
                "b": "RESULT: FAIL (RNG: the species hunt met only duplicates within its battle budget)"}
    results = {inst: [line for line in (receipts.get(inst) or "").splitlines()
                      if line.startswith("RESULT:")] for inst in expected}
    if all(results[inst] == [line] for inst, line in expected.items()):
        return True
    if (results != {"a": [], "b": [expected["b"]]}
            or not isinstance(early_finish, ClientFinishedEarly)
            or early_finish.awaited != "both RESULT lines" or early_finish.exited
            or early_finish.finished != {"a": "", "b": expected["b"]}):
        return False
    try:
        markers = [json.loads(line.partition(" ")[2]) for line in receipts["a"].splitlines()
                   if line.startswith("PENDING_CAPTURE ")]
        entry = pending_snapshot["links"]["pending_captures"]["route_29"]["a"]
        return (len(markers) == 1 and markers[0]["area_id"] == "route_29"
                and bool(markers[0]["key"]) and markers[0]["key"] == entry["key"]
                and type(markers[0]["species_id"]) is int and markers[0]["species_id"] == entry["species"])
    except (KeyError, TypeError, ValueError):
        return False


def main():
    # The client logs contain Unicode arrows; don't let a cp1252 console kill the runner.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game", default="gen3_rr", choices=sorted(GAMES),
                    help="the GAMES pairing row: gen3_rr (new client on Radical Red, default), "
                         "gen3_frlg (new client, FireRed A / LeafGreen B), gen3_lgfr "
                         "(LeafGreen A / FireRed B), gen1_new and the gen1_pure* rows "
                         "(new Gen 1 client), gen2 (Crystal both sides); --list shows a row's scenarios")
    ap.add_argument("--scenario", default="faint",
                    choices=list(SCENARIOS) + ["all"])
    ap.add_argument("--keep-alive", action="store_true",
                    help="pause before teardown for manual inspection")
    ap.add_argument("--keep-data", action="store_true",
                    help="never delete the temp server data dir")
    ap.add_argument("--lane", default=None,
                    help="this run's lane id; keys the generated stub, the BizHawk config copy, "
                         "the SaveRAM directory and the window offset (default: the run's TCP port). "
                         "Two DuoRuns in one process must not share them.")
    ap.add_argument("--speed-percent", type=int, default=None,
                    help="Gen 2 duo emulator speed (O-36): 100-6400, or 0 = unthrottled; default 300. "
                         "Qualification gates are never run through here and stay at 100")
    ap.add_argument("--gen2-artifact", choices=("clean", "overlay"), default=None,
                    help="executed Gen 2 artifact for every scenario (native trade requires overlay)")
    ap.add_argument("--idle-jitter", type=int, default=0,
                    help="extra idle frames before the first hunt; each RNG retry adds 37 per "
                         "attempt")
    ap.add_argument("--wrong-save", default=None,
                    help="second-OT Red SaveRAM for reconnect_new's fail-closed C-1 leg; for "
                         "reconnect_gen3 a second-OT FR flash save (default: B's own fixture)")
    ap.add_argument("--server-flags", nargs="*", default=[],
                    help="extra flags for server.server")
    ap.add_argument("--wire-log", action="store_true",
                    help="capture every c2s/s2c line to "
                         "tests/fixtures/gen3/wire/<scenario>_<player>_gen3_new.jsonl")
    ap.add_argument("--list", action="store_true",
                    help="print the scenarios --scenario all would run for --game, then exit")
    ap.add_argument("--gen3-companion", action="store_true",
                    help="boot the PATCHED FR/LG/Emerald companion cartridges (patch/build/slink_*.gba, sha1-pinned to "
                         "patch/dist/gen3_companions.json) instead of the clean dumps; each client's own admission line must "
                         "say `companion by hash` (recorded as COMPANION_ADMISSION). Opt-in per run; not for RR/expansion")
    args = ap.parse_args()
    if args.gen3_companion and gen3_companion_game_problem(args.game):
        sys.exit(f"[duo] {gen3_companion_game_problem(args.game)}")

    if args.list:
        for line in list_lines(args.game):
            print(line)
        sys.exit(0)

    if args.scenario == "all":
        names = scenarios_for(args.game)
        # NAME what was dropped. A silent filter and a table that genuinely has nothing for
        # this title look identical from the summary, and "all passed" over a silently empty
        # selection is the worst possible way to report no coverage.
        skipped = [n for n in SCENARIOS if n not in names]
        if skipped:
            print(f"[duo] {args.game}: skipping {len(skipped)} scenario(s) that do not apply "
                  f"— {', '.join(skipped)}")
        if not names:
            sys.exit(f"[duo] no scenarios apply to {args.game}")
    else:
        # Fail NOW rather than after two emulators boot and time out on a missing savestate.
        # If the pairing really should work, the fix is the scenario's `games` tuple.
        if not scenario_applies(args.scenario, args.game):
            allowed = ", ".join(SCENARIOS[args.scenario]["games"])
            sys.exit(f"[duo] scenario '{args.scenario}' does not apply to --game {args.game} "
                     f"(it declares games: {allowed}). Add {args.game} to its `games` tuple "
                     f"in SCENARIOS if it should.")
        names = [args.scenario]
    results = {}
    for name in names:
        print(f"\n========== scenario: {name} ==========")
        skip = skip_reason(name, args.game)
        if skip:
            why, allowed = skip
            print(f"[duo] {name}: SKIP ({args.game}{', allowed' if allowed else ''}) — {why}")
            results[name] = (None, 0, why, allowed)
            continue
        results[name] = run_scenario_with_rng_retry(name, args)
    print("\n========== summary ==========")
    for line in summary_lines(results, args.game):
        print(line)
    sys.exit(exit_code(results))


if __name__ == "__main__":
    main()
