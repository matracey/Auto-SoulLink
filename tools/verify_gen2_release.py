#!/usr/bin/env python3
"""Gen 2 release manifest over the shared fail-closed release_lanes runner.

--list describes obligations without executing or qualifying them. --quick runs the
source/MODEL frontier; --lane runs only the named obligations. Neither is a release
verdict.

Owner 2026-10-02: the Gen 2 release is OVERLAY ONLY. Clean manifest rows/receipts
remain history; no fresh clean gameplay proof or code stamp is required. Source
builds and shared fixture/O-33 byte checks remain prerequisites.

--fixtures (the fixtures lane, BINDING P3b.2) judges every Gen 2 fixture the release lanes reference,
with no emulator. Each SaveRAM must pass the strict checksum witness and be either PLAYED or SYNTH.
PLAYED means a sha256-pinned full-chain qualification receipt (GAME/PYDEC boot, re-save and reload
stages) that binds these exact bytes. SYNTH means an O-33 disclosure whose rebuild from a PLAYED base
reproduces the bytes. A missing, stale or undisclosed fixture is RED.

--live-gates, --trade-gates, --duo-pairs and --release-evidence (lanes live-gates,
live-trade-gates, duo-pairs, release-evidence) judge committed PHYSICAL receipts with no
emulator, reusing the validators below: each is RED while a required receipt is missing, edited
or proves another overlay build than the one published now, and green only when every required
cell is present and valid. patch-build rebuilds the overlays (tools/build_gen2_companion.py
--check) and compares them to the published provenance.

--duo-matrix (the duo-link lane) checks the release duo matrix of
tests/gen2_release_requirements.json: C<->C, G<->S and C<->G, every scenario the pairing
registers in tools/e2e_duo.py, each with its post-result oracle and pinned PASS receipts.
A missing pair, scenario, oracle or receipt is RED, never green. Enabling them requires their reviewed
implementation and prerequisite/receipt contracts, not removing a missing-input check.

--c5 <dir> (no lane; C-5 was closed by owner ruling 2026-10-04) judges tools/c5_runner.py's randomized
duo manifests against tests/gen2_c5_requirements.json: the required cells, the current code digest and
the G-g named limit. Stale evidence is RED.

--new-gates (a live-new-gates precondition) checks the committed U1 engine-site, U2 write-window
(each overlay has its own proof) and overlay fixture-qualification receipts against
their pinned sha256 and their production Lua validators (reusing tests/unit/test_gen2_physical_receipts.py's
`validate()`, never re-deriving PASS). It runs with no emulator; a gap there fails live-new-gates before
the lane spawns EmuHawk. R-1/R-2/R-3/R-4/R-5g still need a fresh SLINK_LIVE=1 run of the live inspect
gate on real hardware, which the lane runs directly once this check is clean.

Every executing lane checks its declared input paths before spawning a command. The
source/data tools then verify hashes and provenance themselves. Missing scripts, data,
dependencies and pytest skips are failures. This manifest grants no evidence-cell or
artifact-admission status; the P3b machine ledger is not generated here.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import gen2_code_digest as code_digest
import release_lanes
from release_lanes import Lane

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PY = sys.executable
TITLES = ("crystal", "gold", "silver")
# Owner 2026-10-02: these played fixtures underpin the overlay gates/duos.
# Pin the census independently of the manifest so deleting a row cannot waive it.
OVERLAY_QUALIFICATIONS = (
    "crystal_town", "crystal_battle", "crystal_town_ot2", "crystal_battle_ot2",
    "crystal_battle_errand", "crystal_battle_ot2_errand", "gold_town", "gold_battle",
    "gold_battle_ot2", "gold_battle_errand", "silver_town", "silver_battle", "silver_battle_errand",
)
REQUIREMENT_IDS = (
    "F-1", "F-2", "F-3", "F-4", "F-5", "F-6", "F-7g",
    "R-1", "R-2", "R-3", "R-4", "R-5g",
    "S-1", "S-2", "S-3", "S-4", "S-5", "S-6", "S-7", "S-8", "S-9g", "S-10g",
    "W-1", "W-2", "W-3", "W-4", "W-5", "W-6", "W-7",
    "C-0", "C-1", "C-2", "C-3", "C-4", "C-5", "C-6g",
    "D-1", "D-2", "D-3", "D-5", "D-6", "D-7", "D-11", "D-12", "D-13", "D-14",
    "T-1", "T-2", "T-3", "T-4", "N-1", "N-2", "N-3",
)
# These rows remain mapped. This is a scope description, not a signed disposition.
CONDITIONAL_OR_DEFERRED = {
    "W-3": "conditional Explode Mode; owner enablement or signed disabled disposition",
    "W-4": "conditional rival swap; owner enablement or signed disabled disposition",
    "C-5": "conditional randomized admission; no admission without an enabling ruling",
    "D-11": "conditional duo behavior follows W-3/W-4",
    "N-3": "post-G6 ghost artifact and its separate G5 qualification",
}

_GENERATORS = (
    ("species-generated", "species", ("F-5",)),
    ("evos-generated", "evos", ("F-5",)),
    ("items-generated", "items", ("F-5",)),
    ("moves-generated", "moves", ("F-5",)),
    ("charmap-generated", "charmap", ("R-1",)),
    ("map-names-generated", "map_names", ("F-7g",)),
    ("engine-sites-generated", "engine_signals", ("F-2", "F-3")),
    ("checkpoint-generated", "write_checkpoint", ("R-4", "W-6")),
    ("area-map-generated", "area_map", ("F-7g", "S-8", "S-9g", "S-10g")),
    ("encounters-generated", "encounters", ("F-4", "F-7g", "S-9g", "S-10g")),
    ("statics-generated", "statics", ("S-8",)),
    ("trainers-generated", "trainers", ("R-3",)),
    ("admission-generated", "admission", ("F-7g", "C-1", "C-6g")),
)

# The committed admission matrices carry BUILT rows, which the generator only re-derives from the
# explicit build receipts; a bare --check refuses them as a downgrade (the lane was red for that alone).
_GENERATOR_ARGS = {"admission": ("--provenance", "data/gen2/build_provenance.json",
                                 "--overlay-provenance", "data/gen2/overlay_provenance.json")}


def _overlay_version() -> str:
    """The version the committed overlay provenance records (the builder refuses a malformed one)."""
    try:
        path = ROOT / "data/gen2/overlay_provenance.json"
        return str(json.loads(path.read_text(encoding="utf-8"))["overlay"]["version"])
    except (OSError, ValueError, KeyError):
        return "unrecorded"


def _pytest(path: str) -> list[str]:
    return [_PY, "-m", "pytest", path, "-q", "-p", "no:randomly", "-rs"]


LANES = [
    Lane("unit", _pytest("tests/unit"), why="SOURCE/MODEL tests; no cartridge proof"),
    Lane("source-build", [_PY, "tools/build_gen2_syms.py", "--check"],
         why="rebuild four pinned ROMs and compare the published lock, symbols and maps"),
    Lane("rom-layout", [_PY, "tools/verify_gen2_rom_layout.py"],
         why="built-ROM bytes, banks and independently decoded tables; SOURCE only"),
    Lane("lua-parse", [_PY, "tools/lua_syntax_check.py"],
         why="parse the production Lua files; MODEL only"),
    *[Lane(f"profile-generated-{title}",
           [_PY, "tools/gen_gen2_profile.py", "--title", title, "--check"],
           why=f"{title} profile against its locked source/build; SOURCE only")
      for title in TITLES],
    *[Lane(name, [_PY, f"tools/gen_gen2_{tool}.py", *_GENERATOR_ARGS.get(tool, ()), "--check"],
           why="all three title packs regenerated from pinned facts; SOURCE only")
      for name, tool, _ids in _GENERATORS],
    Lane("coverage-map", [
        _PY, "tools/coverage_map.py", "--map", "docs/gen2/gen2_coverage_map.md",
        "--requirements", "docs/gen2/gen2_requirements.md", "--protocol", "docs/protocol.md",
        "--protocol-section", "9", "--protocol-layers", "SOURCE", "PHYSICAL",
        "--artifact-policy", "data/gen2_sources.lock.json", "--artifact-collection", "outputs",
        "--artifact-digest-field", "sha1", "--artifact-id", "pokecrystal",
        "--artifact-id", "pokegold", "--artifact-id", "pokesilver",
        # Mapping may name future targets; these flags confer no evidence eligibility.
        # Native and post-RC ghost scope is game policy, never inferred by the validator.
        "--planned-target", "crystal_overlay=pokecrystal",
        "--planned-target", "crystal_ghost=pokecrystal",
        "--planned-target", "gold_overlay=pokegold",
        "--planned-target", "gold_ghost=pokegold",
        "--planned-target", "silver_overlay=pokesilver",
        "--planned-target", "silver_ghost=pokesilver",
        "--target-binding", (
            "requirement:C-3,requirement:T-1,requirement:T-2,requirement:T-3,requirement:T-4,"
            "requirement:N-1,requirement:N-2,protocol:9.39,protocol:9.40,protocol:9.41,"
            "protocol:9.42,protocol:9.43,protocol:9.47=crystal_overlay,gold_overlay,silver_overlay"
        ),
        "--target-binding", "requirement:N-3=crystal_ghost,gold_ghost,silver_ghost",
        "--mode", "mapping",
    ],
         why="P2 mapping completeness only; unrun PHYSICAL obligations remain OPEN"),
    Lane("fixtures", [_PY, "tools/verify_gen2_release.py", "--fixtures"],
         why="PHYSICAL receipts (BINDING P3b.2; F-6, S-7): every referenced fixture passes the checksum"
             " witness and is PLAYED (pinned full-chain GAME/PYDEC boot/re-save/reload receipt over these"
             " bytes) or SYNTH (O-33 disclosure that rebuilds the bytes from a PLAYED base)"),
    # TITLE-VERSION: --check rebuilds with the version the published provenance records
    Lane("patch-build", [_PY, "tools/build_gen2_companion.py", "--check", "--version", _overlay_version()],
         why="SOURCE (PLAN §6 P4 'UPS byte-reproducible'; BINDING P4.1): rebuild the three overlays from"
             " the pinned checkouts and compare UPS, sym, map and data/gen2/overlay_provenance.json"
             " byte-for-byte; any drift is red"),
    Lane("live-gates", [_PY, "tools/verify_gen2_release.py", "--live-gates"],
         why="PHYSICAL receipts (BINDING P4.1 panel + GBC fade, P4.2 sound, W-6 writer exclusion): the"
             " committed panel/sfx/w6 gate receipts of tests/gen2_live_gate_requirements.json for Crystal,"
             " Gold and Silver, each a PASS on the overlay published NOW; runners are"
             " tests/live/test_gen2_{panel,sfx,w6}_gate.py. Phone (P4.5, O-29 flavour) stays in live-new-gates"),
    Lane("live-new-gates", _pytest("tests/live/test_gen2_new_gates.py"),
         # a lane run re-proves; only the final sweep's gate/inspect_run cell writes the pinned attestation
         # (tests/live/conftest.py), else every lane run would unpin it
         env={"SLINK_LIVE": "1", "SLINK_GEN2_NO_ATTEST": "1", "SLINK_GEN2_ARTIFACT": "overlay"},
         why="PHYSICAL overlay receipts: U1 engine-site, per-title U2 write-window and fixture"
             " qualification rows of tests/gen2_live_gate_requirements.json are bound and pinned by"
             " sha256 (new_gates_errors, no emulator); a gap there fails the lane before it spawns"
             " EmuHawk. R-1/R-2/R-3/R-4/R-5g have no committed receipt and still need a fresh"
             " SLINK_LIVE=1 run of the live inspect gate (lua/tests/gen2_inspect_gate.lua) on real"
             " hardware; client-conformance (P3b.6/P3b.7) stays a separate later card"),
    Lane("live-trade-gates", [_PY, "tools/verify_gen2_release.py", "--trade-gates"],
         why="PHYSICAL receipts (BINDING P4.3; T-1..T-4): every native trade case of TRADE_END_STATUS"
             " declared and receipted on C-C, G-S and C-G (owner O-34)"
             " in the release duo matrix, judged by the"
             " production overlay trade oracle against the published overlay; runner is"
             " tools/e2e_duo.py's gen2_trade_* cases (tests/e2e/test_duo_gen2_new.py)"),
    Lane("duo-link", [_PY, "tools/verify_gen2_release.py", "--duo-matrix"],
         why="PHYSICAL receipts: the C-C, G-S and C-G link matrix of tests/gen2_release_requirements.json;"
             " a missing pair, scenario, oracle or receipt is red"),
    Lane("duo-pairs", [_PY, "tools/verify_gen2_release.py", "--duo-pairs"],
         why="PHYSICAL receipts (BINDING P3b.7 + P4.3): the whole release duo matrix, and on C-C and G-S"
             " every P3b.7 scenario (DUO_PAIRS_SCENARIOS; shiny_bonus is the O-26 recorded limit) plus"
             " every trade case (C-G owes the trade cases too, O-34); red until each is registered in"
             " tools/e2e_duo.py and receipted"),
    Lane("release-evidence", [_PY, "tools/verify_gen2_release.py", "--release-evidence"],
         why="G4 packet (PLAN §6 P4 exit + §6.1 ledger; BINDING P4.4, P6.3): published overlay bytes match"
             " their provenance, overlay rows ADMITTED at the published hashes, G4 ledger row signed,"
             " the release bundle ships every overlay UPS, and every receipt lane above is clean"),
]

REQUIREMENTS = {
    "unit": ["F-1", "F-2", "F-4", "F-5", "R-1", "R-2", "C-0", "C-4", "D-13"],
    "source-build": ["F-1"],
    "rom-layout": ["F-2", "F-3", "F-4"],
    "lua-parse": ["C-4"],
    **{f"profile-generated-{title}": ["F-1"] for title in TITLES},
    **{name: list(ids) for name, _tool, ids in _GENERATORS},
    "coverage-map": list(REQUIREMENT_IDS),
    "fixtures": ["F-6", "S-7"],
    "patch-build": ["F-2", "F-3", "W-6", "C-1", "T-1", "N-1", "N-2"],
    "live-gates": ["C-3", "N-1", "N-2"],
    "live-new-gates": [
        "F-2", "F-3", "F-6", "R-1", "R-2", "R-3", "R-4", "R-5g",
        "S-1", "S-2", "S-3", "S-4", "S-5", "S-6", "S-7", "S-8", "S-9g", "S-10g",
        "W-1", "W-2", "W-5", "W-6", "W-7", "C-1",
    ],
    "live-trade-gates": ["T-1", "T-2", "T-3", "T-4"],
    "duo-link": ["D-1", "C-6g"],
    "duo-pairs": [
        "F-6", "S-2", "S-3", "S-5", "S-6", "S-7", "S-8", "S-9g", "S-10g",
        "W-5", "W-7", "C-2", "C-6g", "D-1", "D-2", "D-3", "D-5", "D-6", "D-7",
        "D-12", "D-14", "T-3", "T-4",
    ],
    "release-evidence": list(REQUIREMENT_IDS),
}

UNIMPLEMENTED: dict[str, str] = {}
# --quick is source/MODEL feedback, including the P2 coverage map once bound. All
# later-phase obligations stay in the full manifest; omitting them grants no release verdict.
_SLOW = {"fixtures", "patch-build", "live-gates", "live-new-gates",
         "live-trade-gates", "duo-link", "duo-pairs", "release-evidence"}
ALLOWED_SKIPS = ()
_REQUIRED_LANES = frozenset(REQUIREMENTS)
_REQUIRED_MAPPINGS = {name: frozenset(ids) for name, ids in REQUIREMENTS.items()}

_SOURCE_INPUTS = (
    "data/gen2_sources.lock.json", "data/gen2/build_provenance.json",
    *(f"data/gen2/{artifact}.{suffix}"
      for artifact in ("pokecrystal", "pokecrystal11", "pokegold", "pokesilver")
      for suffix in ("sym", "map")),
    *(f".cache/gen2-build/{repo}/{filename}"
      for repo, filename in (("pokecrystal", "pokecrystal.gbc"),
                             ("pokecrystal", "pokecrystal11.gbc"),
                             ("pokegold", "pokegold.gbc"),
                             ("pokegold", "pokesilver.gbc"))),
)
_OVERLAY_PROVENANCE = "data/gen2/overlay_provenance.json"
_DUO_INPUTS = ("tests/gen2_release_requirements.json", "tools/e2e_duo.py", "data/gen2_sources.lock.json",
               _OVERLAY_PROVENANCE)
PREREQUISITES = {
    "unit": ("tests/unit",),
    "source-build": _SOURCE_INPUTS,
    "rom-layout": _SOURCE_INPUTS,
    "lua-parse": ("lua",),
    **{f"profile-generated-{title}": _SOURCE_INPUTS for title in TITLES},
    **{name: _SOURCE_INPUTS for name, _tool, _ids in _GENERATORS},
    "coverage-map": ("docs/gen2/gen2_coverage_map.md", "docs/gen2/gen2_requirements.md",
                     "docs/protocol.md", "data/gen2_sources.lock.json"),
    "fixtures": (
        *(f"tests/fixtures/gen2/{name}.SaveRAM"
          for name in (*(f"{title}_{kind}" for title in TITLES for kind in ("town", "battle")),
                       "crystal_town_ot2", "crystal_battle_ot2", "gold_battle_ot2")),
        "tests/gen2_live_gate_requirements.json", "tests/fixtures/gen2/receipts",
        "tests/gen2_release_requirements.json",
    ),
    "patch-build": (*_SOURCE_INPUTS, "patch/gen2/src", _OVERLAY_PROVENANCE),
    "live-gates": ("tests/gen2_live_gate_requirements.json", "tests/fixtures/gen2/receipts",
                   _OVERLAY_PROVENANCE),
    "live-new-gates": ("tests/gen2_live_gate_requirements.json", "tests/fixtures/gen2/receipts"),
    "live-trade-gates": _DUO_INPUTS,
    "duo-link": _DUO_INPUTS[:3],
    "duo-pairs": _DUO_INPUTS,
    "release-evidence": (*_DUO_INPUTS, "tests/gen2_live_gate_requirements.json", "docs/gen2/PLAN.md",
                         "tools/make_release.py"),
}


DUO_MATRIX = "tests/gen2_release_requirements.json"
# The release matrix itself, (initiator, partner) per O-16. Pinned here so that deleting a row
# from the JSON cannot shrink the matrix silently.
DUO_PAIRS = (("crystal", "crystal"), ("crystal", "gold"), ("gold", "silver"))
DUO_REQUIRED_SCENARIOS = frozenset({"link"})


def _execution_identity(root, title, artifact="clean"):
    if artifact not in ("clean", "overlay"):
        raise ValueError("unknown Gen 2 artifact kind")
    if artifact == "clean":
        lock = json.loads((root / "data/gen2_sources.lock.json").read_text())["outputs"]
        return {"kind": "clean", "rom_sha1": lock["poke" + title]["sha1"], "binding_sha256": None}
    publication = json.loads((root / "data/gen2/overlay_provenance.json").read_text())
    raw = (root / f"data/games/gen2_{title}/overlay/binding.json").read_bytes().replace(b"\r\n", b"\n")
    binding = json.loads(raw)
    sha = publication["outputs"]["poke" + title]["sha1"]
    if binding.get("rom_sha1") != sha or binding.get("title") != title or binding.get("kind") != "overlay":
        raise ValueError("overlay binding identity differs from publication")
    return {"kind": "overlay", "rom_sha1": sha, "base_sha1": binding.get("base_sha1"),
            "binding_sha256": hashlib.sha256(raw).hexdigest()}


def _overlay_startup_errors(lines, identity, title):
    markers = [line.partition(" ")[2] for line in lines if line.startswith("CLIENT ")]
    try:
        client = json.loads(markers[0]) if len(markers) == 1 else {}
        want = {"production_admitted": True, "qualification": "PHYSICAL_RECEIPTED",
                "artifact_kind": "overlay", "rom_sha1": identity["rom_sha1"],
                "binding_sha256": identity["binding_sha256"], "title": title}
        if any(type(client.get(key)) is not type(value) or client.get(key) != value for key, value in want.items()):
            return ["overlay production startup does not bind the executed artifact"]
    except (ValueError, AttributeError):
        return ["overlay production startup is missing or malformed"]
    return []



# Codex's H5 PYDEC format (review O16 F1): "PYDEC: PASS a=<key> b=<key> area=<id>
# titles=<a-title>/<b-title> status=<alive|dead|memorial>" -- link ends alive; gen2_faint ends dead, or
# memorial once the Gen 2 memorialize NACK lets the server finish the pair (owner, via Codex H5).
SCENARIO_END_STATUS = {"link": {"alive"}, "gen2_faint": {"dead", "memorial"}, "gen2_faint_active": {"memorial"},
                       # DUO-WAVE-C contract (whiteout, pc_ops, changebox, poison); pc_ops per O-35
                       "gen2_whiteout": {"memorial"}, "gen2_pc_ops": {"memorial"}, "gen2_changebox": {"memorial"},
                       "gen2_poison": {"dead", "memorial"}, "gen2_whiteout_rebuild": {"alive"},
                       # DUO-WAVE-D (D-2, S-8/O-15, S-2/S-3/D-3, S-5/D-1) and O-30 MINOR-5
                       "gen2_ball_gate": {"alive"}, "gen2_egg_hatch": {"alive"}, "gen2_gift": {"alive"},
                       "gen2_boxed_capture": {"alive"}, "gen2_npc_trade": {"alive"}, "gen2_evolution": {"alive"},
                       "gen2_faint_active_trainer": {"memorial"}}
# Per-scenario PYDEC tokens beyond a/b/titles/status (DUO-WAVE-C contract; the death=active rule's shape).
SCENARIO_TOKENS = {"gen2_whiteout": {"repair": "run_over"},   # owner ruling (a), 4aa1ad5c: game over
                   "gen2_pc_ops": {"release": "propagated"},   # O-35: a PC release kills the partner
                   "gen2_changebox": {"box_change": "BOX1->BOX14->BOX1"},
                   "gen2_poison": {"death": "poison"},
                   "gen2_whiteout_rebuild": {"rebuild": "restored"},   # D-7, owner ruling (c), e8eb21df
                   "gen2_faint_active_trainer": {"death": "active", "battle": "trainer"}}   # 68277bdb


def _fixture_sha256(root: Path, fixture: str) -> str | None:
    try:
        return hashlib.sha256((root / "tests/fixtures/gen2" / f"{fixture}.SaveRAM").read_bytes()).hexdigest()
    except OSError:
        return None


def _engine_capture_key(lines: list[str]) -> str | None:
    line = next((one for one in lines if one.startswith("ENGINE_CAPTURE ")), None)
    if not line:
        return None
    try:
        return json.loads(line[len("ENGINE_CAPTURE "):]).get("key")
    except ValueError:
        return None


# Cells whose linked mon changes key in the run (S-5/D-1 key_change), so the PYDEC names the final key: the
# ENGINE_CAPTURE key followed through each ENGINE_KEY_CHANGE whose old_key is the current one, in order.
KEY_CHANGE_SCENARIOS = frozenset({"gen2_npc_trade", "gen2_evolution"})   # 4b08e007: key_change per side


def _followed_key(lines: list[str], key: str | None) -> str | None:
    for line in lines:
        if line.startswith("ENGINE_KEY_CHANGE "):
            try:
                change = json.loads(line[len("ENGINE_KEY_CHANGE "):])
            except ValueError:
                return None
            if isinstance(change, dict) and key is not None and change.get("old_key") == key:
                key = change.get("new_key")
    return key


def _pydec_tokens(lines: list[str]) -> dict | None:
    line = next((one for one in lines if one.startswith("PYDEC: PASS ")), None)
    if line is None:
        return None
    tokens = {}
    for part in line[len("PYDEC: PASS "):].split():
        key, sep, value = part.partition("=")
        if sep:
            tokens[key] = value
    return tokens


def _pydec_cell_errors(lines: list[str], scenario: str, axes: dict, capture_keys: dict,
                       lock: dict | None = None) -> list[str]:
    """The pydec receipt names its own cell, not just a bare PASS (review O16 F1)."""
    tokens = _pydec_tokens(lines)
    if not tokens:
        return ["pydec receipt does not name this cell"]
    want = {"a": capture_keys.get("a"), "b": capture_keys.get("b"),
            "titles": f"{axes['initiator']}/{axes['partner']}",
            "status": SCENARIO_END_STATUS.get(scenario)}
    if scenario == "gen2_admit_wrong_rom":
        want = {"scenario": scenario, "a": "admitted", "b": "refused", "area": "none",
                "titles": f"{axes['initiator']}/crystal", "status": "refused",
                "rom_b": (lock or {}).get("pokecrystal11", {}).get("sha1", "")}
    elif scenario == "gen2_soft_reset":
        want = {"scenario": scenario, "a": "reset", "b": "idle", "area": "none",
                "titles": f"{axes['initiator']}/{axes['partner']}", "status": "unchanged"}
    elif scenario == "gen2_faint_active":
        want.update(scenario=scenario, area="route_29", death="active")
    elif scenario in SCENARIO_TOKENS:
        want.update(scenario=scenario, **SCENARIO_TOKENS[scenario])
    errors = [f"pydec receipt does not name this cell: {key}={tokens.get(key)!r}, want {value!r}"
              for key, value in want.items()
              if value is not None and not (tokens.get(key) in value if isinstance(value, set) else tokens.get(key) == value)]
    if not tokens.get("area"):
        errors.append("pydec receipt does not name this cell: area= is empty or missing")
    return errors


def _refused_receipt_errors(lines: list[str], rom_sha1: str | None) -> list[str]:
    """Only H7's B half may replace a save with pinned no-client/no-traffic evidence."""
    rows = {}
    for tag in ("DUO_GEN2", "ADMISSION_REFUSED", "NO_TRAFFIC", "CARTRAM_UNCHANGED", "RECEIPT"):
        bodies = [line[len(tag) + 1:] for line in lines if line.startswith(tag + " ")]
        if len(bodies) != 1:
            return [f"b refused receipt requires exactly one {tag}"]
        try:
            rows[tag] = json.loads(bodies[0])
        except ValueError:
            return [f"b refused receipt has malformed {tag}"]
        if not isinstance(rows[tag], dict):
            return [f"b refused receipt has malformed {tag}"]
    errors = []
    forbidden = ("CLIENT", "BOOTED", "HELLO", "HELLO_AGAIN", "TX", "HOLD", "SAVE_WITNESS", "ENGINE_CAPTURE")
    if any(line.split(" ", 1)[0] in forbidden for line in lines):
        errors.append("b refused receipt contains admitted-client activity")
    refusal, quiet, cart, receipt = (rows[tag] for tag in
                                    ("ADMISSION_REFUSED", "NO_TRAFFIC", "CARTRAM_UNCHANGED", "RECEIPT"))
    console = refusal.get("console")
    if (not rom_sha1 or refusal.get("rom_sha1") != rom_sha1 or refusal.get("client") is not False
            or not isinstance(console, str) or "refused" not in console):
        errors.append("b refused receipt has no pinned admission refusal")
    if quiet.get("tx") != 0 or type(quiet.get("frames")) is not int or quiet["frames"] < 600:
        errors.append("b refused receipt has no 600-frame traffic-free hold")
    digest = cart.get("before")
    if (not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            or cart.get("after") != digest):
        errors.append("b refused receipt has no unchanged CartRAM digest")
    tags = list(rows)
    positions = [next(i for i, line in enumerate(lines) if line.startswith(tag + " ")) for tag in tags]
    if positions != sorted(positions):
        errors.append("b refused receipt markers out of order")
    head = rows["DUO_GEN2"]
    if (receipt.get("schema") != "gen2-duo-admit-wrong-rom-v1"
            or any(receipt.get(key) != head.get(key) for key in
                   ("player", "scenario", "attempt", "title", "rom_sha1", "expect_admission"))):
        errors.append("b refused receipt does not bind its header")
    return errors


def _reconnect_receipt_errors(root: Path, proof: dict, axes: dict, lock: dict) -> list[str]:
    """Bind all reconnect legs and immutable staged bytes; only killed initial A lacks RESULT."""
    def need(condition, why):
        if not condition:
            raise ValueError(why)

    def pinned(entry, binary=False):
        raw = (root / entry["path"]).read_bytes()
        if not binary:
            raw = raw.replace(b"\r\n", b"\n")
        need(hashlib.sha256(raw).hexdigest() == entry["sha256"], f"{entry['path']}: sha256 differs")
        return raw

    def one(lines, tag):
        bodies = [line[len(tag) + 1:] for line in lines if line.startswith(tag + " ")]
        need(len(bodies) == 1, f"expected one {tag}")
        row = json.loads(bodies[0])
        need(isinstance(row, dict), f"malformed {tag}")
        return row

    def position(lines, tag):
        return next(i for i, line in enumerate(lines) if line.startswith(tag + " "))

    try:
        receipts = proof["receipts"]
        legs = {side: pinned(receipts[side]).decode("utf-8").splitlines()
                for side in ("a", "b", "a_same_save", "a_wrong_save", "pydec")}
        stages = proof["staged_saves"]
        seeds = {phase: pinned(stages[phase], binary=True) for phase in ("same_save", "wrong_save")}
        wrong_case = f"{axes['initiator']}_battle_ot2"
        need(stages["wrong_save"]["case"] == wrong_case, "wrong-save fixture case differs")
        need(hashlib.sha256(seeds["wrong_save"]).hexdigest() == _fixture_sha256(root, wrong_case),
             "wrong-save staged bytes differ from fixture")
        need(seeds["same_save"] != seeds["wrong_save"], "relaunch seeds are identical")
        keys = {side: one(legs[side], "ENGINE_CAPTURE")["key"] for side in ("a", "b")}
        need(all(isinstance(key, str) and key for key in keys.values()), "missing capture keys")
        for side in ("a", "b", "a_same_save", "a_wrong_save"):
            lines = legs[side]
            player = "b" if side == "b" else "a"
            phase = side[2:] if side.startswith("a_") else "initial"
            title = axes["partner"] if player == "b" else axes["initiator"]
            case = axes["fixtures"][player] if phase == "initial" else f"{title}_battle"
            fingerprint = (_fixture_sha256(root, case) if phase == "initial"
                           else hashlib.sha256(seeds[phase]).hexdigest())
            head = one(lines, "DUO_GEN2")
            want = {"player": player, "scenario": "gen2_reconnect", "title": title, "case": case,
                    "rom_sha1": (_execution_identity(root, title, "overlay")["rom_sha1"] if axes.get("artifact_kind", "clean") == "overlay" else lock[f"poke{title}"]["sha1"]), "fixture_sha256": fingerprint}
            if axes.get("artifact_kind", "clean") == "overlay":
                identity = _execution_identity(root, title, "overlay")
                want.update(artifact_kind="overlay", binding_sha256=identity["binding_sha256"])
                need(not _overlay_startup_errors(lines, identity, title), f"{side}: overlay production startup differs")
            need(fingerprint and all(head.get(key) == value for key, value in want.items()),
                 f"{side}: header does not bind its fixture and ROM")
            verdicts = [line for line in lines if line.startswith("RESULT:")]
            need(not verdicts if side == "a" else
                 len(verdicts) == 1 and verdicts[0].split()[1:2] == ["PASS"], f"{side}: wrong RESULT contract")
            need(one(lines, "CLIENT").get("production_admitted") is True, f"{side}: no production client")
            one(lines, "BOOTED")
            one(lines, "HELLO")
            need(not any(line.startswith("HELLO_AGAIN ") for line in lines), f"{side}: repeated hello")
            if phase == "initial":
                save, ready = one(lines, "SAVE_WITNESS"), one(lines, "RECONNECT_READY")
                need(ready.get("key") == keys[player] and ready.get("phase") == "initial"
                     and ready.get("player") == player, f"{side}: wrong RECONNECT_READY")
                need(position(lines, "RECONNECT_READY") > position(lines, "SAVE_WITNESS"), "ready before save")
                if player == "a":
                    seed = seeds["same_save"]
                    need(len(seed) == save.get("saveram_bytes") == 32790, "same-save stage length differs")
                    snapshot_entry = (proof.get("witness_snapshots") or {}).get("a")
                    if snapshot_entry is not None or hashlib.sha256(seed[:32768]).hexdigest() != save.get("cartram_sha256"):
                        from server.adapters import gen2_codec as codec
                        from tools.gen2_duo_oracles import normalized_gameplay_cartram

                        need(snapshot_entry is not None, "same-save scratch change lacks authenticated witness snapshot")
                        baseline = pinned(snapshot_entry, binary=True)
                        need(len(baseline) in (32768, 32790)
                             and hashlib.sha256(baseline[:32768]).hexdigest() == save.get("cartram_sha256"),
                             "same-save witness snapshot does not bind initial raw CartRAM digest")
                        layout = codec.for_foundation(title)
                        need(normalized_gameplay_cartram(seed, layout) == normalized_gameplay_cartram(baseline, layout),
                             "same-save stage changed bytes outside native scratch")
                    need(not any(line.startswith("RECEIPT ") for line in lines), "killed initial A has receipt")
                else:
                    stayed = one(lines, "B_STAYED")
                    need(stayed.get("hellos") == 1 and stayed.get("force_faint") == 0
                         and stayed.get("box_mon") == 0, "B did not stay connected unchanged")
                    need(position(lines, "B_STAYED") > position(lines, "RECONNECT_READY"), "B_STAYED early")
                    tail = lines[position(lines, "RECONNECT_READY") + 1:]
                    need(not any(line.startswith(("RX force_faint", "RX box_mon")) for line in tail),
                         "B received destructive command")
            else:
                need(not any(line.startswith(("SAVE_WITNESS ", "ENGINE_CAPTURE ", "RECONNECT_READY ",
                                               "RX force_faint", "RX box_mon")) for line in lines),
                     f"{side}: relaunch emitted capture/save or destructive command")
                back = one(lines, "RECONNECT_HELLO")
                need(back.get("phase") == phase and back.get("expected_key") == keys["a"]
                     and back.get("linked") is (phase == "same_save") and back.get("hellos") == 1,
                     f"{side}: reconnect identity mismatch")
                need(position(lines, "RECONNECT_HELLO") > position(lines, "HELLO"), "reconnect before hello")
                if phase == "wrong_save":
                    hud = one(lines, "WRONG_SAVE_HUD")
                    received = [(i, json.loads(line[len("RX_TEXT "):])) for i, line in enumerate(lines)
                                if line.startswith("RX_TEXT ")]
                    wrong = [i for i, value in received if isinstance(value, dict)
                             and value.get("cmd") == "hud_show" and value.get("text") == "[x] WRONG SAVE: slot A"]
                    need(hud.get("text") == "[x] WRONG SAVE: slot A" and wrong, "missing wrong-save rejection HUD")
                    need(min(wrong) > position(lines, "HELLO")
                         and position(lines, "WRONG_SAVE_HUD") > position(lines, "RECONNECT_HELLO"), "HUD early")
                else:
                    need(not any(line.startswith("WRONG_SAVE_HUD ") for line in lines), "same save refused")
            if side != "a":
                receipt = one(lines, "RECEIPT")
                need(receipt.get("schema") == "gen2-duo-reconnect-v1" and receipt.get("phase") == phase
                     and all(receipt.get(key) == head.get(key) for key in (*want, "attempt")),
                     f"{side}: receipt does not bind header/phase")
                if phase != "initial":
                    need(receipt.get("expected_key") == keys["a"] and receipt.get("linked") is (phase == "same_save"),
                         f"{side}: receipt identity mismatch")
        tokens = _pydec_tokens(legs["pydec"])
        want = {"scenario": "gen2_reconnect", **keys, "area": "route_29", "status": "alive",
                "titles": f"{axes['initiator']}/{axes['partner']}"}
        need(tokens and all(tokens.get(key) == value for key, value in want.items()), "pydec does not bind reconnect cell")
        need(sum(line.startswith("PYDEC:") for line in legs["pydec"]) == 1, "ambiguous pydec verdict")
    except (KeyError, TypeError, ValueError, AttributeError, OSError, RuntimeError, StopIteration) as exc:
        return [f"reconnect proof invalid: {exc}"]
    return []


def _soft_reset_receipt_errors(lines: list[str], side: str) -> list[str]:
    """Check reset observations without inventing an ENGINE occurrence for the WRAM-clear path."""
    def need(condition, why):
        if not condition:
            raise ValueError(why)

    def one(tag):
        rows = [(i, line[len(tag) + 1:]) for i, line in enumerate(lines) if line.startswith(tag + " ")]
        need(len(rows) == 1, f"expected one {tag}")
        index, body = rows[0]
        row = json.loads(body)
        need(isinstance(row, dict), f"malformed {tag}")
        return index, row

    try:
        tags = ("DUO_GEN2", "CLIENT", "BOOTED", "HELLO", "SAVE_WITNESS", "RECEIPT")
        marks = {tag: one(tag) for tag in tags}
        a_only = ("HELLO_AT_CHECKPOINT", "CHORD_GATE", "CHORD", "RESET_SEEN", "HELLO_CLEARED",
                  "WRITES_PAUSED", "REBOOTED", "WRITES_RESUMED", "HELLO_AGAIN", "REHELLO", "NO_WRITES_IN_WINDOW")
        if side == "a":
            marks.update({tag: one(tag) for tag in a_only})
        else:
            need(not any(line.split(" ", 1)[0] in a_only for line in lines), "idle B emitted reset/rehello marker")
            marks["IDLE_PARTNER"] = one("IDLE_PARTNER")

        def value(tag):
            return marks[tag][1]

        def after(later, earlier):
            need(marks[later][0] > marks[earlier][0], f"{later} before {earlier}")
            if "frame" in value(later) and "frame" in value(earlier):
                need(type(value(later)["frame"]) is int and type(value(earlier)["frame"]) is int
                     and value(later)["frame"] >= value(earlier)["frame"], f"{later} frame before {earlier}")

        def delta(tag, start, low, high):
            row, baseline = value(tag), value(start)
            need(type(row.get("frame")) is int and type(baseline.get("frame")) is int
                 and type(row.get("delta")) is int and row["delta"] == row["frame"] - baseline["frame"]
                 and low <= row["delta"] <= high, f"{tag} inconsistent/out-of-bounds delta")

        need(value("CLIENT").get("production_admitted") is True, "no production client")
        need(not any(line.startswith(("ENGINE_CAPTURE ", "ENGINE_SOFT_RESET ")) for line in lines),
             "soft reset receipt must not substitute an engine occurrence")
        hello, save, receipt, head = (value(tag) for tag in ("HELLO", "SAVE_WITNESS", "RECEIPT", "DUO_GEN2"))
        need(type(hello.get("ot_id")) is int and 0 < hello["ot_id"] <= 65535, "hello has no live OT")
        digest = save.get("cartram_sha256")
        need(save.get("flushed_matches") is True and save.get("cartram_bytes") == 32768
             and isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
             and type(save.get("gate_saves")) is int and save["gate_saves"] >= 1
             and type(save.get("client_saves")) is int and save["client_saves"] >= 1, "incomplete native save")
        after("RECEIPT", "SAVE_WITNESS")
        need(receipt.get("schema") == "gen2-duo-soft-reset-v1"
             and all(receipt.get(key) == head.get(key) for key in
                     ("player", "scenario", "attempt", "case", "title", "rom_sha1", "fixture_sha256"))
             and receipt.get("hello") == hello and receipt.get("save") == save
             and receipt.get("client") == value("CLIENT"), "receipt does not bind its markers/header")
        if side == "a":
            at = value("HELLO_AT_CHECKPOINT")
            need(at.get("hellos") == 1 and at.get("writes_enabled") is True
                 and at.get("ot_id") == hello["ot_id"], "reset checkpoint not initially admitted")
            for later, earlier in (("HELLO_AT_CHECKPOINT", "HELLO"), ("CHORD_GATE", "HELLO_AT_CHECKPOINT"),
                    ("CHORD", "CHORD_GATE"), ("RESET_SEEN", "CHORD"), ("HELLO_CLEARED", "RESET_SEEN"),
                    ("WRITES_PAUSED", "RESET_SEEN"), ("REBOOTED", "HELLO_CLEARED"), ("REBOOTED", "WRITES_PAUSED"),
                    ("WRITES_RESUMED", "REBOOTED"), ("HELLO_AGAIN", "WRITES_PAUSED"), ("REHELLO", "REBOOTED"),
                    ("REHELLO", "HELLO_AGAIN"), ("NO_WRITES_IN_WINDOW", "REHELLO"),
                    ("SAVE_WITNESS", "NO_WRITES_IN_WINDOW")):
                after(later, earlier)
            need(type(value("CHORD").get("frames")) is int and value("CHORD")["frames"] >= 1, "empty chord")
            delta("RESET_SEEN", "CHORD", 30, 60)
            delta("HELLO_CLEARED", "RESET_SEEN", 0, 180)
            delta("WRITES_PAUSED", "RESET_SEEN", 180, 420)
            resumed = value("WRITES_RESUMED")
            need(type(resumed.get("delta")) is int
                 and resumed["delta"] == resumed["frame"] - value("RESET_SEEN")["frame"], "resume delta differs")
            need(value("HELLO_AGAIN").get("n") == value("REHELLO").get("hellos") == 2
                 and value("HELLO_AGAIN").get("ot_id") == value("REHELLO").get("ot_id") == hello["ot_id"],
                 "rehello count or OT differs")
            need(value("NO_WRITES_IN_WINDOW").get("writes") == 0, "writes occurred in reset window")
            need(type(save.get("save_completed_frame")) is int
                 and save["save_completed_frame"] > value("REHELLO")["frame"], "save completed before rehello")
            need(receipt.get("reset") == value("RESET_SEEN") and receipt.get("paused") == value("WRITES_PAUSED")
                 and receipt.get("rehello") == value("REHELLO"), "receipt reset detail differs")
        else:
            need(value("IDLE_PARTNER").get("hellos") == 1, "idle partner hello count differs")
            after("IDLE_PARTNER", "HELLO")
            after("SAVE_WITNESS", "IDLE_PARTNER")
            need(receipt.get("idle") == value("IDLE_PARTNER"), "receipt idle detail differs")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return [f"{side} soft-reset receipt invalid: {exc}"]
    return []


def _clause_cell_errors(legs: dict, scenario: str, axes: dict) -> list[str]:
    """Only observed clause branches qualify; bind the PYDEC facts to the saved driver evidence."""
    kind = scenario.removeprefix("gen2_").removesuffix("_clause")

    def need(condition, why):
        if not condition:
            raise ValueError(why)

    def rows(side, tag):
        found = [(i, json.loads(line[len(tag) + 1:])) for i, line in enumerate(legs[side])
                 if line.startswith(tag + " ")]
        need(all(isinstance(row, dict) for _, row in found), f"malformed {side} {tag}")
        return found

    def one(side, tag):
        found = rows(side, tag)
        need(len(found) == 1, f"expected one {side} {tag}")
        return found[0]

    try:
        caps = {side: one(side, "ENGINE_CAPTURE")[1] for side in ("a", "b")}
        receipts = {}
        verdicts = {}
        for side in ("a", "b"):
            cap, head = caps[side], one(side, "DUO_GEN2")[1]
            save_at, save = one(side, "SAVE_WITNESS")
            receipt_at, receipt = one(side, "RECEIPT")
            receipts[side] = receipt
            need(one(side, "CLIENT")[1].get("production_admitted") is True, "clause client not admitted")
            need(cap.get("area_id") == "route_29" and isinstance(cap.get("key"), str) and cap["key"], "wrong capture area/key")
            need(receipt_at > save_at and receipt.get("schema") == f"gen2-duo-{kind}-clause-v1"
                 and receipt.get("save") == save and receipt.get("capture") == cap and receipt.get("key") == cap["key"]
                 and receipt.get("species_id") == cap.get("species_id")
                 and all(receipt.get(key) == head.get(key) for key in
                         ("player", "scenario", "attempt", "case", "title", "rom_sha1", "fixture_sha256")),
                 "clause receipt does not bind header/capture/final save")
            need(type(save.get("gate_saves")) is int and save["gate_saves"] >= 2
                 and type(save.get("client_saves")) is int and save["client_saves"] >= 2
                 and save.get("flushed_matches") is True and save.get("cartram_bytes") == 32768,
                 "clause final second native save missing")
            digest = save.get("cartram_sha256")
            need(isinstance(digest, str) and len(digest) == 64
                 and all(c in "0123456789abcdef" for c in digest), "clause final save digest missing")
            last_tag = "LINKED" if kind == "species" else "CLAUSE_VERDICT"
            outcome_at, outcome = one(side, last_tag)
            need(save_at > outcome_at and type(save.get("save_completed_frame")) is int
                 and type(outcome.get("frame")) is int and save["save_completed_frame"] > outcome["frame"],
                 "clause final save precedes outcome")
            if kind != "species":
                cc_at, cc = one(side, "CLAUSE_CAPTURE")
                need(cc_at < outcome_at and all(cc.get(key) == cap.get(key) for key in ("key", "species_id", "area_id")),
                     "clause capture differs from engine")
                verdict = outcome.get("verdict")
                verdicts[side] = verdict
                need(verdict in ("rejected", "partner_rejected") and receipt.get("verdict") == verdict
                     and receipt.get("clause") == kind and receipt.get("path") == "clause_observed",
                     "clause rejection unobserved")
                if verdict == "rejected":
                    mon_at, mon = one(side, "REJECTED_MON")
                    ack_at, ack = one(side, "MEMORIAL_ACK")
                    writes = rows(side, "PARTY_HP_WRITE")
                    need(writes and all(value.get("key") == cap["key"] and value.get("ok") is True
                                        and at < mon_at for at, value in writes), "rejected mon lacks successful production write")
                    need(ack_at < mon_at < save_at and mon.get("key") == ack.get("key") == cap["key"]
                         and save["save_completed_frame"] > mon["frame"], "rejected ending not saved")
                    ending = mon.get("ending")
                    need(receipt.get("ending") == ending and (
                        ending == "dead" and ack.get("event") == "memorialize_failed"
                        and mon.get("in_party") is True and mon.get("hp") == 0 or
                        ending == "memorial" and ack.get("event") == "memorialize_done"
                        and mon.get("in_party") is False and mon.get("box") == ack.get("box") == 13),
                        "rejected ending disagrees with memorial acknowledgement")
                else:
                    need(not rows(side, "REJECTED_MON"), "partner marked rejected")
        tokens = _pydec_tokens(legs["pydec"])
        want = {"scenario": scenario, "a": caps["a"]["key"], "b": caps["b"]["key"], "area": "route_29",
                "titles": f"{axes['initiator']}/{axes['partner']}", "clause": kind}
        if kind != "species":
            need(sorted(verdicts.values()) == ["partner_rejected", "rejected"], "clause requires one rejected half")
            rejected = next(side for side in verdicts if verdicts[side] == "rejected")
            want.update(status="clause_observed", rejected=rejected, ending=receipts[rejected]["ending"])
        else:
            pending_at, pending = one("a", "PENDING_CAPTURE")
            need(pending_at > one("a", "ENGINE_CAPTURE")[0] and pending_at < one("a", "LINKED")[0]
                 and all(pending.get(key) == caps["a"].get(key) for key in ("key", "species_id", "area_id"))
                 and receipts["a"].get("role") == receipts["a"].get("path") == "pending", "invalid pending capture")
            ap_at, ap = one("b", "A_PENDING")
            need(ap.get("species_id") == caps["a"]["species_id"] != caps["b"]["species_id"], "reroll does not bind partner species")
            encounters, rerolls = rows("b", "ENCOUNTER"), rows("b", "REROLL")
            need(len(encounters) >= 2 and len(rerolls) == len(encounters) - 1, "species reroll unobserved")
            need(receipts["b"].get("role") == "reroller" and receipts["b"].get("path") == "reroll_observed"
                 and receipts["b"].get("rerolls") == len(rerolls)
                 and receipts["b"].get("dupe_species") == ap["species_id"], "species receipt does not bind rerolls")
            for i, (at, encounter) in enumerate(encounters):
                dupe = i < len(rerolls)
                species = ap["species_id"] if dupe else caps["b"]["species_id"]
                need(at > ap_at and encounter.get("n") == i + 1 and encounter.get("dupe") is dupe
                     and encounter.get("species_id") == species, "encounter species/order differs")
                if dupe:
                    rr_at, rr = rerolls[i]
                    need(at < rr_at < encounters[i + 1][0] and rr.get("n") == i + 1
                         and rr.get("species_id") == species, "reroll order/species differs")
                    prompt = rr.get("prompt")
                    # Native intro can prompt before ENCOUNTER; never reuse the prior battle's prompt.
                    prompt_start = ap_at if i == 0 else rerolls[i - 1][0]
                    need(isinstance(prompt, str) and prompt.startswith("Dupes clause: ") and prompt.endswith(" -- reroll!")
                         and any(prompt_start < rx_at < rr_at and rx.get("cmd") == "gui_prompt" and rx.get("text") == prompt
                                 for rx_at, rx in rows("b", "RX_TEXT")), "reroll lacks observed server prompt")
            need(encounters[-1][0] < one("b", "ENGINE_CAPTURE")[0], "catch precedes final encounter")
            want.update(status="alive", rerolls=str(len(rerolls)))
        need(tokens and all(tokens.get(key) == value for key, value in want.items()), "pydec does not bind observed clause cell")
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration) as exc:
        return [f"clause proof invalid: {exc}"]
    return []


def _memorial_receipt_errors(lines: list[str], side: str) -> list[str]:
    """Box records have no HP: memorial qualification needs the actual HP-zero party preimage."""
    def need(condition, why):
        if not condition:
            raise ValueError(why)

    def one(tag):
        found = [(i, json.loads(line[len(tag) + 1:])) for i, line in enumerate(lines)
                 if line.startswith(tag + " ")]
        need(len(found) == 1, f"expected one {tag}")
        need(isinstance(found[0][1], dict), f"malformed {tag}")
        return found[0]

    try:
        pre_at, pre = one("MEMORIAL_PREIMAGE")
        ack_at, ack = one("MEMORIAL_ACK")
        save_at, save = one("SAVE_WITNESS")
        _, cap = one("ENGINE_CAPTURE")
        _, head = one("DUO_GEN2")
        from server.adapters import gen2_codec as codec

        layout = codec.for_foundation(head["title"])
        mon = codec.decode_party_mon(bytes.fromhex(pre["raw_hex"]), layout,
            species_marker=pre["species_marker"], ot=bytes.fromhex(pre["ot_raw_hex"]),
            nickname=bytes.fromhex(pre["nickname_raw_hex"]))
        need(codec.key(mon) == pre.get("key") == cap.get("key") and mon["hp"] == 0
             and mon["species_id"] == cap.get("species_id") and not mon["is_egg"],
             "preimage is not the captured HP-zero party mon")
        need(type(pre.get("slot")) is int and 0 <= pre["slot"] < layout.constants["PARTY_LENGTH"],
             "preimage party slot invalid")
        need(ack.get("event") == "memorialize_done" and ack.get("key") == cap["key"]
             and type(ack.get("box")) is int and ack["box"] == layout.constants["NUM_BOXES"] - 1 == 13,
             "successful Box 14 memorial acknowledgement missing")
        need(pre_at < ack_at < save_at and type(pre.get("frame")) is int and type(ack.get("frame")) is int
             and type(save.get("save_completed_frame")) is int
             and pre["frame"] <= ack["frame"] < save["save_completed_frame"], "preimage/ack/final-save chronology differs")
        for i, line in enumerate(lines):
            if line.startswith("PARTY_HP_WRITE "):
                write = json.loads(line[len("PARTY_HP_WRITE "):])
                need(i < pre_at and type(write.get("frame")) is int and write["frame"] <= pre["frame"],
                     "HP write after memorial preimage")
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        return [f"{side} memorial receipt invalid: {exc}"]
    return []


def _active_faint_cell_errors(legs: dict, axes: dict, trainer: bool = False) -> list[str]:
    """Bind archived active-death receipts; bench-death evidence cannot fill this cell. trainer: the O-30
    MINOR-5 variant (gen2_faint_active_trainer) - a Route 30 youngster battle, ForcePlayerMonChoice, no NEXT_MON,
    a live enemy turn OR a witnessed enemy faint after REPLACED (TRAINER-FAINT-LIVE-TURN, post-RC: a crit-KO can
    zero the foe before it ever moves; the shared validator's trainer=True), and B's receipt names the trainer."""
    def need(condition, why):
        if not condition:
            raise ValueError(why)

    def one(side, tag):
        rows = [json.loads(line[len(tag) + 1:]) for line in legs[side] if line.startswith(tag + " ")]
        need(len(rows) == 1 and isinstance(rows[0], dict), f"expected one {side} {tag}")
        return rows[0]

    try:
        from tools.gen2_duo_oracles import validate_faint_active_markers

        caps = {side: one(side, "ENGINE_CAPTURE") for side in ("a", "b")}
        results = {side: "\n".join(legs[side]) for side in ("a", "b")}
        # Same SOURCE validator as the independent save oracle: exact battle-hold PC/bank,
        # four ordered permit spans, trace sequence, replacement and observed HP/status.
        validate_faint_active_markers(results, title_b=axes["partner"], key_a=caps["a"]["key"],
                                     key_b=caps["b"]["key"], species_b=caps["b"]["species_id"], trainer=trainer)
        schema = "gen2-duo-faint-active-trainer-v1" if trainer else "gen2-duo-faint-active-v1"
        for side in ("a", "b"):
            head, receipt, save, link = (one(side, tag) for tag in ("DUO_GEN2", "RECEIPT", "SAVE_WITNESS", "LINK_SAVE"))
            need(receipt.get("schema") == schema
                 and all(receipt.get(key) == head.get(key) for key in
                         ("player", "scenario", "attempt", "case", "title", "rom_sha1", "fixture_sha256"))
                 and receipt.get("capture") == caps[side] and receipt.get("key") == caps[side]["key"]
                 and receipt.get("save") == save and receipt.get("link_save") == link,
                 f"{side}: active faint receipt does not bind header/capture/saves")
            client = one(side, "CLIENT")
            need(client.get("production_admitted") is True and "battle_faint" in client.get("registered_sites", []),
                 f"{side}: active faint production hook missing")
            need(link.get("key") == caps[side]["key"] and link.get("cartram_bytes") == 32768
                 and link.get("saveram_bytes") == 32790, f"{side}: link save identity/size differs")
            for counter in ("gate_saves", "client_saves"):
                need(type(link.get(counter)) is int and type(save.get(counter)) is int
                     and 1 <= link[counter] < save[counter], f"{side}: final native save missing")
            if side == "a":
                need(receipt.get("b_active") == one("a", "B_ACTIVE"), "A receipt does not bind B_ACTIVE")
                faint, sent = one("a", "ENGINE_FAINT"), one("a", "FAINT_SENT")
                need(faint.get("site_id") == "battle_faint" and faint.get("cause") == "battle"
                     and faint.get("key") == sent.get("key") == caps["a"]["key"]
                     and type(sent.get("frame")) is int and faint["frame"] <= sent["frame"] < save["save_completed_frame"],
                     "A faint send does not bind its natural engine faint/final save")
                a_lines = legs["a"]
                faint_at, sent_at, save_at = (next(i for i, line in enumerate(a_lines) if line.startswith(tag + " "))
                                              for tag in ("ENGINE_FAINT", "FAINT_SENT", "SAVE_WITNESS"))
                need(faint_at < sent_at < save_at
                     and not any(line.startswith(("PARTY_HP_WRITE ", "BATTLE_HOLD_WRITE ")) for line in a_lines),
                     "A natural faint was written or sent out of order")
                need(receipt.get("faint") == faint and receipt.get("faint_sent") == sent,
                     "A receipt faint evidence differs")
            else:
                write = one("b", "BATTLE_HOLD_WRITE")
                expected_write = {key: write[key] for key in
                    ("frame", "seq", "slot", "pc", "hrom_bank", "battle_hp_before_hex", "action_after_hex")}
                traces = [json.loads(line[len("BATTLE_TRACE "):]) for line in legs["b"] if line.startswith("BATTLE_TRACE ")]
                first = next(row for row in traces if row["seq"] > write["seq"])
                need(receipt.get("linked_active") == one("b", "LINKED_ACTIVE")
                     and receipt.get("force_faint_key") == caps["b"]["key"] and receipt.get("battle_write") == expected_write
                     and receipt.get("native_faint") == first and receipt.get("replaced") == one("b", "REPLACED"),
                     "B receipt does not bind active write/native faint/replacement")
                if trainer:
                    active = one("b", "LINKED_ACTIVE")
                    need(receipt.get("trainer") == {"class": active.get("other_trainer_class"),
                                                    "id": active.get("other_trainer_id")},
                         "B receipt does not name the opposing trainer from LINKED_ACTIVE")
                memorial = receipt.get("memorial") or {}
                need(memorial.get("preimage_frame") == one("b", "MEMORIAL_PREIMAGE").get("frame")
                     and memorial.get("ack") == one("b", "MEMORIAL_ACK"), "B receipt memorial differs")
    except (KeyError, TypeError, ValueError, AttributeError, RuntimeError, ImportError, OSError, StopIteration) as exc:
        return [f"active faint proof invalid: {exc}"]
    return []


# P4.3e native trade cases (tools/gen2_trade_lane.py SCENARIOS) -> the oracle's end status.
TRADE_END_STATUS = {"gen2_trade_new": "committed", "gen2_trade_evolve": "committed",
                    "gen2_trade_reset_commit": "committed", "gen2_trade_decline_new": "unchanged",
                    "gen2_trade_timeout": "unchanged", "gen2_trade_reset_wait": "unchanged",
                    "gen2_trade_refuse_item": "unchanged"}
# Coordinator ruling 2026-09-24: MODEL-ONLY trade cases, never a PHYSICAL cell. Server 2676c2f9 blocks a
# trade during the Bug-Catching Contest before any prompt, and the contest cannot be saved mid-way, so the
# cartridge refusal is unreachable in real play (defence in depth). Its evidence is these unit tests.
TRADE_MODEL_ONLY = {
    "gen2_trade_refuse_contest": (
        "tests/unit/test_gen2_trade_service.py::test_compiled_proposer_refuses_before_query",
        "tests/unit/test_gen2_trade_service.py::test_compiled_responder_refuses_contest_party_at_pickup",
        "tests/unit/test_gen2_client.py::test_trade_in_the_contest_declines_a_prompt_without_arming_it_and_answers_a_zero_mask",
        "tests/unit/test_gen2_client.py::test_trade_blocked_rides_the_tick_while_the_contest_masks_the_party",
        "tests/unit/test_state_trade_hardening.py::test_a_trade_blocked_player_makes_no_pair_eligible_on_either_side",
    ),
    # Coordinator ruling 2026-09-24: the never-saved refusal is MODEL-only too (its oracle MODEL rows).
    "gen2_trade_refuse_unsaved": (
        "tests/unit/test_gen2_trade_gates.py::test_refusal_model_rows_leave_both_saves_unchanged",
        "tests/unit/test_gen2_trade_gates.py::test_refusal_model_rows_require_their_native_proof",
        "tests/unit/test_gen2_trade_service.py::test_compiled_proposer_refuses_before_query",
        "tests/unit/test_gen2_trade_service.py::test_compiled_never_saved_responder_is_saved_then_trades",
    ),
}
# O-33 SYNTH poisoned-lead setups a U1 leg may disclose: (title, base fixture) -> tools/gen2_synth_fixtures.PSN_RECIPES key
POISON_SETUP_RECIPE = {("gold", "gold_battle_errand"): "psn", ("crystal", "crystal_battle"): "psn_u1"}
TRADE_PLANTED = {"gen2_trade_refuse_item", "gen2_trade_evolve"}   # O-31: a's disclosed HARNESS_WRITE
# O-33 (post-RC card TRADE-EVOLVE-CATCH): the only synthetic trade boot seed, gen2_trade_evolve's a (its errand base
# with Master Balls, tools/gen2_synth_fixtures.TRADE_RECIPES; gen2_trade_oracles.SEEDED). axes.scenario_fixtures names it.
TRADE_SEEDED = {"gen2_trade_evolve": frozenset("a")}
TRADE_VARIANTS = {("crystal", "crystal"): "cc", ("gold", "silver"): "gs", ("crystal", "gold"): "cg"}
# Owner ruling O-34 (8a42b7f7) supersedes the Q10 reading: native trades are allowed and tested on every
# release pair, C-G included, so each pair owes every TRADE_END_STATUS case.


def _trade_receipt_errors(root: Path, proof: dict, scenario: str, axes: dict) -> list[str]:
    """A production overlay trade proof: the driver RECEIPTs and the PYDEC name this cell, its
    errand fixtures, the CURRENT published overlay pins and the O-31 disclosure; never a clean-ROM PASS."""
    errors = []
    want_status = TRADE_END_STATUS[scenario]
    titles = {"a": axes["initiator"], "b": axes["partner"]}
    errands = axes.get("trade_fixtures") or {}
    fixtures = (axes.get("scenario_fixtures") or {}).get(scenario) or errands
    for side in ("a", "b"):
        name = fixtures.get(side, "")
        if "_synth_" not in name:
            continue
        try:
            disclosure = json.loads((root / "tests/fixtures/gen2" / f"{name}.synth.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            disclosure = None
        base = disclosure.get("base_fixture") if isinstance(disclosure, dict) else None
        if (side not in TRADE_SEEDED.get(scenario, ()) or base is None or base != errands.get(side)
                or disclosure.get("sha256") != _fixture_sha256(root, name)
                or disclosure.get("base_sha256") != _fixture_sha256(root, base)):
            errors.append(f"{side}: boot seed {name} is not an O-33 disclosure over its bytes on the errand "
                          f"base {errands.get(side)} (only {sorted(TRADE_SEEDED)} a boots a seed)")
    try:
        outputs = json.loads((root / "data/gen2/overlay_provenance.json").read_text(encoding="utf-8"))["outputs"]
    except (OSError, ValueError, KeyError):
        return ["trade proof: data/gen2/overlay_provenance.json unreadable"]
    legs = {}
    for side in ("a", "b", "pydec"):
        entry = (proof.get("receipts") or {}).get(side)
        path = root / entry["path"] if entry else None
        if not entry or not path.is_file():
            errors.append(f"{side} receipt not registered or missing")
            continue
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            errors.append(f"{side} receipt {entry['path']} sha256 differs from its pin")
            continue
        legs[side] = raw.decode("utf-8", errors="replace").splitlines()
    for side in ("a", "b"):
        lines = legs.get(side)
        if lines is None:
            continue
        verdicts = [line.split()[1:2] for line in lines if line.startswith("RESULT:")]
        if not verdicts or any(verdict != ["PASS"] for verdict in verdicts):
            errors.append(f"{side} receipt has no RESULT: PASS verdict, or a non-PASS one")
        bodies = [line[len("RECEIPT "):] for line in lines if line.startswith("RECEIPT ")]
        try:
            receipt = json.loads(bodies[0]) if len(bodies) == 1 else None
        except ValueError:
            receipt = None
        pin = next((row for row in outputs.values() if row.get("slink_title") == titles[side]), {})
        clients = [line[len("CLIENT "):] for line in lines if line.startswith("CLIENT ")]
        try:
            client = json.loads(clients[0]) if len(clients) == 1 else {}
            identity = _execution_identity(root, titles[side], "overlay")
            if (client.get("qualification") != "PHYSICAL_RECEIPTED" or client.get("production_admitted") is not True
                    or client.get("artifact_kind") != "overlay" or client.get("rom_sha1") != identity["rom_sha1"]
                    or client.get("binding_sha256") != identity["binding_sha256"]):
                errors.append(f"{side}: trade has no unmodified production overlay startup")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{side}: trade startup binding unavailable: {exc}")
        want = {"schema": "gen2-duo-trade-v1", "case": scenario, "player": side, "title": titles[side],
                "variant": TRADE_VARIANTS.get((titles["a"], titles["b"])), "outcome": want_status,
                "admission_scope": "PHYSICAL_RECEIPTED",
                "fixture_sha256": _fixture_sha256(root, fixtures.get(side, "")),
                "harness_exception": "O-31" if scenario in TRADE_PLANTED and side == "a" else None}
        accepted = _accepted_overlay_shas(pin)
        if (not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in want.items())
                or not accepted or receipt.get("rom_sha1") not in accepted or want["fixture_sha256"] is None):
            errors.append(f"{side} trade RECEIPT does not name {want} with rom_sha1 in {sorted(accepted)}")
    tokens = _pydec_tokens(legs.get("pydec", [])) or {}
    want = {"scenario": scenario, "admission_scope": "PHYSICAL_RECEIPTED",
            "titles": f"{titles['a']}/{titles['b']}", "status": want_status}
    errors.extend(f"pydec receipt does not name this cell: {key}={tokens.get(key)!r}, want {value!r}"
                  for key, value in want.items() if tokens.get(key) != value)
    errors.extend(f"pydec receipt does not name this cell: {key}= is empty or missing"
                  for key in ("a", "b", "area") if not tokens.get(key))
    return errors


def _cell_fixture(root: Path, axes: dict, scenario: str, side: str) -> tuple[str, str | None, str | None]:
    """(staged fixture, header case, synth name) for one side of a cell. axes.scenario_fixtures overrides the
    pairing's fixtures per scenario (duo_matrix_errors checks it against the runner). An O-33 synth fixture
    boots through its PLAYED base, so the header names the base as `case`, the synth as `synth`, and the
    synth bytes as `fixture_sha256`."""
    fixture = ((axes.get("scenario_fixtures") or {}).get(scenario) or axes["fixtures"])[side]
    if "_synth_" not in fixture:
        return fixture, fixture, None
    try:
        base = json.loads((root / "tests/fixtures/gen2" / f"{fixture}.synth.json").read_text(encoding="utf-8"))
        base = base["base_fixture"]
    except (OSError, ValueError, KeyError, TypeError):
        base = None
    return fixture, base, fixture


def _runner_cell_fixtures(duo, game: str, scenario: str) -> dict | None:
    """What tools/e2e_duo.py stages for this cell (gen2_preflight's rules), or None for trade cells (trade_fixtures)
    other than gen2_trade_evolve (its A seed, GEN2_TRADE_EVOLVE_FIXTURES).
    ponytail: mirrors the runner's staging maps; a new runner override must be added here too."""
    if scenario == "gen2_trade_evolve":
        return (getattr(duo, "GEN2_TRADE_EVOLVE_FIXTURES", {}) or {}).get(game)
    if scenario in TRADE_END_STATUS:
        return None
    if scenario == "gen2_ball_gate":
        return (getattr(duo, "GEN2_BALL_GATE_FIXTURES", {}) or {}).get(game)
    if scenario == "gen2_faint_active_trainer":
        return (getattr(duo, "GEN2_TRAINER_FIXTURES", {}) or {}).get(game)
    if scenario in (getattr(duo, "GEN2_SYNTH_SCENARIOS", {}) or {}):
        return {inst: duo.gen2_synth_name(scenario, game, inst) for inst in ("a", "b")}
    fixtures = dict(duo.GAMES[game].get("fixture") or {})
    if scenario == "gen2_poison":
        fixtures.update((getattr(duo, "GEN2_POISON_FIXTURES", {}) or {}).get(game, {}))
    return fixtures


def _receipt_errors(root: Path, proof: dict, scenario: str, axes: dict, lock: dict) -> list[str]:
    """One registered proof: pinned bytes, PASS verdicts, and headers naming this exact cell."""
    if scenario == "gen2_reconnect":
        return _reconnect_receipt_errors(root, proof, axes, lock)
    if scenario in TRADE_END_STATUS:
        return _trade_receipt_errors(root, proof, scenario, axes)
    errors = []
    receipts = proof.get("receipts") or {}
    titles = {"a": axes["initiator"], "b": axes["partner"]}
    capture_keys = {}
    legs = {}
    for side in ("a", "b", "pydec"):
        entry = receipts.get(side)
        if not entry:
            errors.append(f"{side} receipt not registered")
            continue
        path = root / entry["path"]
        if not path.is_file():
            errors.append(f"{side} receipt {entry['path']} missing")
            continue
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            errors.append(f"{side} receipt {entry['path']} sha256 differs from its pin")
            continue
        lines = raw.decode("utf-8", errors="replace").splitlines()
        legs[side] = lines
        prefix = "PYDEC:" if side == "pydec" else "RESULT:"
        verdicts = [line.split()[1:2] for line in lines if line.startswith(prefix)]
        if not verdicts or any(verdict != ["PASS"] for verdict in verdicts):
            errors.append(f"{side} receipt has no {prefix} PASS verdict, or a non-PASS one")
        if side == "pydec":
            errors.extend(_pydec_cell_errors(lines, scenario, axes, capture_keys, lock))
            continue
        capture_keys[side] = _engine_capture_key(lines)
        if scenario in KEY_CHANGE_SCENARIOS:
            capture_keys[side] = _followed_key(lines, capture_keys[side])
        title = titles[side]
        lock_key = f"poke{title}"
        refused = scenario == "gen2_admit_wrong_rom" and side == "b"
        if refused:
            title, lock_key = "crystal", "pokecrystal11"
        if lock_key not in lock:
            errors.append(f"{side}: axes title {title!r} has no {lock_key!r} entry in "
                          f"data/gen2_sources.lock.json")
            continue
        fixture, case, synth = _cell_fixture(root, axes, scenario, side)
        want = {"player": side, "scenario": scenario, "case": case, "title": title,
                "rom_sha1": (_execution_identity(root, title, "overlay")["rom_sha1"] if not refused and axes.get("artifact_kind", "clean") == "overlay" else lock[lock_key].get("sha1")), "fixture_sha256": _fixture_sha256(root, fixture)}
        if not refused and axes.get("artifact_kind", "clean") == "overlay":
            identity = _execution_identity(root, title, "overlay")
            want.update(artifact_kind="overlay", binding_sha256=identity["binding_sha256"])
            errors.extend(f"{side}: {error}" for error in _overlay_startup_errors(lines, identity, title))
        if refused:
            want = {"player": side, "scenario": scenario, "title": title,
                    "rom_sha1": lock[lock_key].get("sha1"), "expect_admission": "refused"}
        headers = [line[len("DUO_GEN2 "):] for line in lines if line.startswith("DUO_GEN2 ")]
        try:
            header = json.loads(headers[0]) if len(headers) == 1 else None
        except ValueError:
            header = None
        if not refused and (synth is not None or (isinstance(header, dict) and header.get("synth"))):
            want["synth"] = synth   # O-33: a synth name exactly when the cell stages a synthetic setup
        if not isinstance(header, dict) or any(header.get(key) != value for key, value in want.items()):
            errors.append(f"{side} receipt header does not name {want}")
        if refused:
            errors.extend(_refused_receipt_errors(lines, lock[lock_key].get("sha1")))
        elif not any(line.startswith("SAVE_WITNESS ") for line in lines):
            errors.append(f"{side} receipt has no SAVE_WITNESS line")
        if scenario == "gen2_soft_reset":
            errors.extend(_soft_reset_receipt_errors(lines, side))
    if scenario in ("gen2_species_clause", "gen2_type_clause", "gen2_gender_clause"):
        errors.extend(_clause_cell_errors(legs, scenario, axes))
    if scenario == "gen2_faint_active":
        errors.extend(_active_faint_cell_errors(legs, axes))
    if scenario == "gen2_faint_active_trainer":
        errors.extend(_active_faint_cell_errors(legs, axes, trainer=True))
    tokens = _pydec_tokens(legs.get("pydec", [])) or {}
    memorial_sides = ()
    # gen2_changebox's memorial is the faint half (DUO-WAVE-C). Whiteout's preimage is the REVIVED record
    # (HP > 0), so the HP-zero faint rule cannot apply to it; its oracle checks it instead.
    if (scenario in ("gen2_faint_active", "gen2_faint_active_trainer", "gen2_changebox")
            or scenario == "gen2_faint" and tokens.get("status") == "memorial"):
        memorial_sides = ("a", "b")
    elif scenario in ("gen2_type_clause", "gen2_gender_clause") and tokens.get("ending") == "memorial":
        memorial_sides = (tokens.get("rejected"),)
    for side in memorial_sides:
        errors.extend(_memorial_receipt_errors(legs.get(side, []), side))
    return errors


def duo_matrix_errors(root: Path | None = None, duo=None, *, required: dict | None = None,
                      only: frozenset | None = None, artifact_kind="overlay") -> list[str]:
    """Every gap in the overlay release matrix; artifact_kind=None explicitly audits both histories.

    Registry side (tools/e2e_duo.py): every Gen 2 pairing is declared, every scenario it
    registers is in the matrix, and each has a post-result oracle under a require_oracle
    evidence contract. Evidence side: each cell has pinned PASS receipts for its own pair.
    `required` maps (initiator, partner) to scenarios that pair must declare on top of
    DUO_REQUIRED_SCENARIOS; `only` narrows the per-cell checks to those scenarios (row-level
    identity checks always run), so a narrowed lane still cannot pass on an empty row."""
    root = ROOT if root is None else root
    required = required or {}
    if duo is None:
        import e2e_duo as duo
    doc = json.loads((root / DUO_MATRIX).read_text(encoding="utf-8"))
    lock = json.loads((root / "data/gen2_sources.lock.json").read_text(encoding="utf-8"))["outputs"]
    rows = [row for row in doc.get("requirements", []) if row.get("stage") == "live-duos"
            and (artifact_kind is None or (row.get("axes") or {}).get("artifact_kind", "clean") == artifact_kind)]
    errors = []
    # .get(): a malformed row (missing "axes"/"initiator"/etc.) surfaces as a pairs mismatch here,
    # never a crash -- the per-row loop below gives it its own dedicated "malformed row" message too.
    pairs = sorted((((row.get("axes") or {}).get("artifact_kind", "clean"), (row.get("axes") or {}).get("initiator"), (row.get("axes") or {}).get("partner"))
                   for row in rows), key=str)  # str key: a malformed row's None must not crash the sort
    expected_pairs = sorted((kind, *pair) for kind in ((artifact_kind,) if artifact_kind else ("clean", "overlay"))
                            for pair in DUO_PAIRS)
    if pairs != expected_pairs:
        errors.append(f"release matrix pairs {pairs} != required {expected_pairs}")
    declared = {row.get("axes", {}).get("pairing") for row in rows}
    errors.extend(f"{game}: Gen 2 duo pairing in tools/e2e_duo.py is not in the release matrix"
                  for game in duo.GAMES
                  if duo.scenario_family(game) == "gen2_new" and game not in declared)
    for row in rows:
        try:
            rid, axes = row["id"], row["axes"]
            game, scenarios = axes["pairing"], axes.get("scenarios") or []
            pair = (axes.get("initiator"), axes.get("partner"))
            artifact = axes.get("artifact_kind", "clean")
            if artifact not in ("clean", "overlay"):
                raise ValueError("unknown matrix artifact")
            need = DUO_REQUIRED_SCENARIOS | required.get((artifact, *pair), required.get(pair, frozenset()) if artifact == "clean" else frozenset())
            missing_required = need - set(scenarios)
            model_only = sorted(set(scenarios) & set(TRADE_MODEL_ONLY))
            if model_only:
                errors.append(f"{rid}: {model_only} are MODEL-only (TRADE_MODEL_ONLY), never a PHYSICAL duo cell")
            if missing_required:
                errors.append(f"{rid}: required scenario(s) {sorted(missing_required)} not declared")
            registered = []
            if game not in duo.GAMES or duo.scenario_family(game) != "gen2_new":
                errors.append(f"{rid}: pairing {game} is not a gen2_new row in tools/e2e_duo.py")
            else:
                registered = duo.scenarios_for(game)
                if artifact == "clean":
                    registered = [name for name in registered if name not in TRADE_END_STATUS]
                elif pair == ("crystal", "gold"):
                    registered = [name for name in registered if name == "link" or name in TRADE_END_STATUS]
                if duo.GAMES[game].get("fixture") != axes["fixtures"]:
                    errors.append(f"{rid}: tools/e2e_duo.py {game} fixtures "
                                  f"{duo.GAMES[game].get('fixture')} != matrix {axes['fixtures']}")
                try:
                    if not duo.evidence_contract(game).require_oracle:
                        errors.append(f"{rid}: {game} evidence contract does not require an oracle")
                except RuntimeError as exc:
                    errors.append(f"{rid}: {exc}")
                errors.extend(f"{rid}: registered scenario {name} is not in the release matrix"
                              for name in registered
                              if name not in scenarios and (only is None or name in only))
                for name in scenarios:
                    staged = _runner_cell_fixtures(duo, game, name) if name in registered else None
                    declared = (axes.get("scenario_fixtures") or {}).get(name) or axes["fixtures"]
                    if staged is not None and staged != declared:
                        errors.append(f"{rid}/{name}: tools/e2e_duo.py stages {staged} but the matrix binds "
                                      f"{declared} (axes.scenario_fixtures)")
                trade = getattr(duo, "GEN2_TRADE_FIXTURES", {}).get(game)
                if set(scenarios) & set(TRADE_END_STATUS) and trade != axes.get("trade_fixtures"):
                    errors.append(f"{rid}: tools/e2e_duo.py {game} trade fixtures {trade} != matrix "
                                  f"{axes.get('trade_fixtures')}")
            # F4 (review O16): a scenario proof-listed twice, or naming a scenario axes.scenarios
            # never declared, is reported -- a dict comprehension would silently keep only one.
            proof_list = row.get("proofs", [])
            proof_names = [proof.get("scenario") for proof in proof_list]
            dupes = sorted({name for name in proof_names if proof_names.count(name) > 1}, key=str)
            if dupes:
                errors.append(f"{rid}: duplicate proof scenario(s) {dupes}")
            undeclared = sorted({name for name in proof_names if name not in scenarios}, key=str)
            if undeclared:
                errors.append(f"{rid}: proof scenario(s) {undeclared} not declared in axes.scenarios")
            proofs = {}
            for proof in proof_list:
                proofs.setdefault(proof.get("scenario"), proof)
            for name in scenarios:
                if only is not None and name not in only:
                    continue
                cell = f"{rid}/{name}"
                if name not in registered:
                    errors.append(f"{cell}: scenario not registered for {game} in tools/e2e_duo.py")
                else:
                    oracle = duo.SCENARIOS[name].get("oracle")
                    if not oracle or not callable(getattr(duo.DuoRun, oracle, None)):
                        errors.append(f"{cell}: no post-result oracle ({oracle!r})")
                if name not in proofs:
                    errors.append(f"{cell}: no receipt registered (an empty proof is a release blocker)")
                    continue
                errors.extend(f"{cell}: {problem}"
                              for problem in _receipt_errors(root, proofs[name], name, axes, lock))
        except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
            # F5 (review O16): a malformed row is its own red gap, never an uncaught crash that
            # takes the whole matrix (and every other row's real gaps) down with it.
            cell = row.get("id", "<row without id>") if isinstance(row, dict) else "<malformed row>"
            errors.append(f"{cell}: malformed row: {exc!r}")
    return errors


def _duo_matrix_main() -> int:
    errors = duo_matrix_errors()
    for error in errors:
        print(f"RED  {error}")
    _warn_stale("duo.")
    if errors:
        print(f"duo matrix: {len(errors)} gap(s); a missing pair/scenario/oracle/receipt is not a pass")
        return 1
    print("duo matrix: every pair x scenario cell RECEIPTED")
    return 0


NEW_GATES = "tests/gen2_live_gate_requirements.json"


def _new_gate_receipt(root: Path, entry: dict) -> tuple[dict | None, str | None]:
    """The receipt at entry['path'], loaded only after its bytes match entry['sha256'] (CRLF->LF)."""
    path = root / entry["path"]
    if not path.is_file():
        return None, f"{entry['path']} missing"
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        return None, f"{entry['path']} sha256 differs from its pin"
    return json.loads(raw.decode("utf-8")), None


def _qualification_row_errors(root: Path, fixture: str, receipt: dict, artifact_kind="clean") -> list[str]:
    """A committed *.qualification.json report: passed, and still binding the staged fixture's bytes.

    `physical_qualification` is never true on ANY qualification report by design (tools/gen2_fixtures.py
    qualify()/qualification_report(): "the report never claims physical qualification" -- it always
    stamps False pending a separate coordinator sign-off), so it is not a pass/fail signal here."""
    errors = []
    if not receipt.get("passed") or receipt.get("errors"):
        errors.append("fixture qualification receipt is not a passed run")
    rows = receipt.get("fixtures") or []
    if not rows or rows[0].get("name") != fixture or not rows[0].get("passed"):
        errors.append(f"qualification report does not confirm {fixture} passed")
        return errors
    if artifact_kind == "overlay":
        provenance = rows[0].get("provenance") or {}
        title = fixture.split("_")[0]
        try:
            identity = _execution_identity(root, title, artifact_kind)
            if provenance.get("rom_sha1") != identity["rom_sha1"]:
                errors.append("qualification belongs to another executed artifact")
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"overlay qualification binding unavailable: {exc}")
    saveram = root / "tests/fixtures/gen2" / f"{fixture}.SaveRAM"
    want = (rows[0].get("artifacts") or {}).get("fixture", {}).get("sha256")
    if not saveram.is_file():
        errors.append(f"{fixture}.SaveRAM missing")
    elif want and hashlib.sha256(saveram.read_bytes()).hexdigest() != want:
        errors.append(f"{fixture}.SaveRAM does not match the bytes its qualification receipt covers")
    return errors


def _accepted_overlay_shas(row: dict | None) -> set[str]:
    """The exact overlay hashes a receipt may name for one published build: the published sha1 and every earlier exact hash
    the builder proved version-equivalent (`equivalent_sha1s`: same canonical identity, patch/tools/rom_identity.py), so a
    release stamp that only changes the version field does not strand receipts bound to the build that was qualified."""
    if not row or not row.get("sha1"):
        return set()
    return {row["sha1"], *(row.get("equivalent_sha1s") or ())}


def _overlay_gate_errors(root: Path, title: str, receipt: dict, schema: str, what: str) -> list[str]:
    """A PHYSICAL PASS of `schema` on the overlay build that is published NOW (data/gen2/overlay_provenance.json),
    from the committed fixture's bytes. A rebuilt overlay makes the receipt stale until the gate is re-run."""
    errors = []
    if (receipt.get("schema") != schema or receipt.get("result") != "PASS"
            or receipt.get("evidence_level") != "PHYSICAL" or receipt.get("title") != title):
        errors.append(f"{what} receipt is not a PHYSICAL PASS for {title}")
    provenance = root / "data/gen2/overlay_provenance.json"
    outputs = json.loads(provenance.read_text(encoding="utf-8"))["outputs"] if provenance.is_file() else {}
    published = _accepted_overlay_shas(next((row for row in outputs.values() if row.get("slink_title") == title), None))
    if not published or receipt.get("overlay_sha1") not in published:
        errors.append(f"{what} receipt proves another overlay build than the published one")
    try:
        identity = _execution_identity(root, title, "overlay")
        want_identity = {"artifact_kind": "overlay", "rom_sha1": identity["rom_sha1"],
                         "base_sha1": identity["base_sha1"], "binding_sha256": identity["binding_sha256"]}
        if identity["base_sha1"] is None or any(receipt.get(key) != value for key, value in want_identity.items()):
            errors.append(f"{what} receipt lacks the executed overlay artifact identity/binding")
    except (OSError, KeyError, ValueError, TypeError) as exc:
        errors.append(f"{what} published overlay binding unavailable: {exc}")
    fixture, want = receipt.get("fixture"), receipt.get("fixture_sha256")
    # Both present first: a missing fixture and a missing hash must not compare None == None.
    if (not isinstance(fixture, str) or not fixture or not isinstance(want, str) or not want
            or _fixture_sha256(root, fixture) != want):
        errors.append(f"{what} receipt does not bind the committed fixture's bytes")
    return errors


def _panel_gate_row_errors(root: Path, title: str, receipt: dict) -> list[str]:
    """P4.1g (tests/live/test_gen2_panel_gate.py): the overlay gate binding plus a positive minimum-SP margin."""
    errors = _overlay_gate_errors(root, title, receipt, "gen2-panel-gate-v1", "panel gate")
    margin = (receipt.get("minimum_sp") or {}).get("margin_bytes")
    if type(margin) is not int or margin <= 0:
        errors.append("panel gate receipt has no positive minimum-SP margin")
    return errors


# P4.2c (tests/live/test_gen2_sfx_gate.py): every context the plan names (N-2), each played in its deadline.
SFX_GATE_CONTEXTS = ("idle_1", "idle_2", "idle_3", "idle_4", "movement", "transition", "start_menu", "text",
                     "battle_anim", "battle_menu")


def _sfx_gate_row_errors(root: Path, title: str, receipt: dict) -> list[str]:
    """P4.2c: the overlay gate binding, every sound context PASS within its deadline (the transition held
    through its fade, the battle's worst no-service stretch in bound) and the reset drop."""
    errors = _overlay_gate_errors(root, title, receipt, "gen2-sfx-gate-v1", "sfx gate")
    contexts, deadline = receipt.get("contexts") or {}, receipt.get("deadline_frames")
    for name in SFX_GATE_CONTEXTS:
        c = contexts.get(name) or {}
        played, posted = c.get("played"), c.get("posted")
        start = c.get("fade_end") if name == "transition" else posted
        if (c.get("result") != "PASS" or type(deadline) is not int or type(played) is not int
                or type(start) is not int or not 0 <= played - start <= deadline):
            errors.append(f"sfx gate context {name} did not play within its deadline")
    gap = (contexts.get("battle_anim") or {}).get("battle_service_gap")
    if type(gap) is not int or type(deadline) is not int or not 0 < gap <= deadline:
        errors.append("sfx gate battle worst-case service gap is missing or past the deadline")
    reset = receipt.get("reset") or {}
    if (reset.get("result") != "PASS" or reset.get("pending_at_entry") is not True
            or reset.get("played_id") is not None or reset.get("on_channel") is not None):
        errors.append("sfx gate reset did not drop a pending request")
    return errors


# card gen2-p4-w6 (tests/live/test_gen2_w6_gate.py): the live mailbox write-watch over the whole scripted corpus.
W6_GATE_LEGS = ("panel", "sfx", "u1")
# Legs that must carry an O-33 clock setup (tests/live/test_gen2_w6_gate.py U1_CLOCK: Silver hunts by day; Gold boots the SYNTH psn lead).
# U1 legs run on the CURRENT drivers (tests/live/test_gen2_w6_gate.py U1_CHAINS["head"]) and a disclosed SYNTH poisoned
# lead: their receipt must bind each driver file to the released tree and carry the synth/start_phase disclosure.
W6_HEAD_DRIVER_LEGS = {("gold", "u1")}
W6_CLOCK_LEGS = {("silver", "u1"), ("gold", "u1")}   # Gold: the SYNTH poisoned lead + the pinned clock (O-33)


def _clock_setup_errors(root: Path, title: str, leg: dict) -> list[str]:
    """clock-setup-v1: the disclosure re-derives exactly from the committed fixture through
    tools/gen2_synth_fixtures.day_clock (same hour/minute/second and host time). That binds the base bytes, the start time,
    both hashes and the trailer, and it proves that only bytes 32768..32790 (the RTC trailer) differ."""
    import gen2_synth_fixtures as synth
    setup = leg.get("clock_setup")
    try:
        if not isinstance(setup, dict):
            raise ValueError("clock setup must be an object")
        minute, second = setup.get("game_minute", 0), setup.get("game_second", 0)
        for name, value, maximum in (("hour", setup["game_hour"], 23), ("minute", minute, 59), ("second", second, 59)):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"invalid clock {name}")
        if type(setup["host_time"]) is not int or setup["host_time"] < 0:
            raise ValueError("invalid clock host time")
        if "u1_attempt" in leg:
            attempt = leg["u1_attempt"]
            if (type(attempt) is not int or attempt not in (1, 2, 3) or "game_minute" not in setup
                    or (setup["game_hour"], minute, second) != (11, (0, 23, 46)[attempt - 1], 0)):
                raise ValueError("u1_attempt differs from the disclosed clock schedule")
        raw = (root / "tests/fixtures/gen2" / f"{leg['fixture']}.SaveRAM").read_bytes()
        if "poison_setup" in leg:
            recipe = POISON_SETUP_RECIPE.get((title, leg["fixture"]))
            if recipe is None:
                raise ValueError("poison setup is only the Gold errand / Crystal battle recipe")
            # Rebuild from THIS root's base bytes using the pinned synthesis rules,
            # as day_clock does for its source layout; never trust disclosed edits.
            raw, poison = synth.build(title, raw, synth.PSN_RECIPES[recipe][1], base_name=leg["fixture"])
            if leg["poison_setup"] != poison:
                raise ValueError("poison setup differs from its named recipe re-derivation")
        out, again = synth.day_clock(raw, hour=setup["game_hour"], now=setup["host_time"], title=title,
                                    minute=minute, second=second)
        # Historical zero-minute disclosures omit these keys. New U1 captures
        # spell them out; both must describe exactly the same rebuilt bytes.
        # This loop only normalizes key presence: the minute is bound by new_hex/sha256,
        # which day_clock computed WITH it, so never compare just the two clock fields.
        # ponytail: u1_attempt stays optional because round-1 Crystal (11:23) predates it.
        for key, value in (("game_minute", minute), ("game_second", second)):
            if key in setup:
                again[key] = value
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        return [f"clock setup does not re-derive from the committed fixture: {exc!r}"]
    if setup != again:
        wrong = sorted(key for key in set(setup) | set(again) if setup.get(key) != again.get(key))
        return [f"clock setup differs from its day_clock re-derivation in {wrong}"]
    if out[:synth.CART] != raw[:synth.CART] or len(out) != synth.SAVERAM:
        return ["clock setup changes CartRAM (only the RTC trailer may differ)"]
    return []


def _w6_head_driver_errors(root: Path, leg: dict) -> list[str]:
    """The current-drivers U1 chain ("head") is bound to the RELEASED tree: every driver file the leg ran has the sha256
    of the same file here (a driver edited after the receipt, or a receipt from other drivers, is refused), and the leg
    discloses the SYNTH poisoned lead it booted (poison_setup is re-derived by the clock check)."""
    errors = []
    driver = leg.get("driver")
    files = driver.get("files") if isinstance(driver, dict) else None
    if not isinstance(files, dict) or not files or driver.get("ref") != "head":
        return ["driver chain is not the pinned current-drivers ('head') chain"]
    for rel, entry in sorted(files.items()):
        ref, want = (entry or {}).get("ref"), (entry or {}).get("sha256")
        if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{40}", ref):
            errors.append(f"driver {rel} is not pinned to an exact commit")
        elif _lf_sha256(root / rel) != want:
            errors.append(f"driver {rel} differs from the released tree's file")
    if (not isinstance(leg.get("poison_setup"), dict) or leg.get("synth_psn") is not True
            or leg.get("poison_start_phase") != "tick"):
        errors.append("the SYNTH poisoned-lead disclosure (poison_setup, synth_psn, start_phase tick) is missing")
    return errors


def _w6_gate_row_errors(root: Path, title: str, receipt: dict) -> list[str]:
    """O-27 D1 tripwire: the overlay gate binding, then every corpus leg ran to completion on its committed
    fixture with no mailbox writer outside SLink code, and both known-positive controls fired in it."""
    errors = _overlay_gate_errors(root, title, receipt, "gen2-w6-gate-v1", "w6 gate")
    legs = receipt.get("legs") or {}
    if set(legs) != set(W6_GATE_LEGS):
        errors.append("w6 gate receipt does not cover the panel, sfx and u1 legs")
    for name in sorted(legs):
        leg = legs[name] or {}
        fixture, want = leg.get("fixture"), leg.get("fixture_sha256")
        if (not isinstance(fixture, str) or not fixture or not isinstance(want, str) or not want
                or _fixture_sha256(root, fixture) != want):
            errors.append(f"w6 gate leg {name} does not bind the committed fixture's bytes")
        if leg.get("overlay_sha1") != receipt.get("overlay_sha1"):
            errors.append(f"w6 gate leg {name} ran another overlay build")
        frames, writes = leg.get("corpus_frames"), leg.get("allowed_writes")
        if (leg.get("inner_completed") is not True or type(frames) is not int or frames <= 0
                or type(writes) is not int or writes <= 0):
            errors.append(f"w6 gate leg {name} did not watch a completed corpus")
        if leg.get("violation_count") != 0 or leg.get("violations"):
            errors.append(f"w6 gate leg {name} saw a mailbox writer outside SLink code")
        control = leg.get("control") or {}
        if control.get("native_caught") is not True or control.get("lua_caught") is not True:
            errors.append(f"w6 gate leg {name} known-positive control did not fire")
        if leg.get("clock_setup") is not None:
            errors.extend(f"w6 gate leg {name}: {e}" for e in _clock_setup_errors(root, title, leg))
        elif (title, name) in W6_CLOCK_LEGS:
            errors.append(f"w6 gate leg {name} has no clock setup disclosure (it hunts by day)")
        if (title, name) in W6_HEAD_DRIVER_LEGS:
            errors.extend(f"w6 gate leg {name}: {e}" for e in _w6_head_driver_errors(root, leg))
    if receipt.get("violation_count") != 0:
        errors.append("w6 gate receipt records mailbox violations")
    return errors


# P4.5d (tests/live/test_gen2_phone_gate.py): every phone case the plan names, each ringing only after its step;
# v2 adds the PHONE-NAMES cases (docs/gen2/POST_RC_CARDS.md).
PHONE_GATE_CASES = ("battle", "town", "start_menu", "save", "native", "named_map_change", "named_fallback")


def _phone_gate_row_errors(root: Path, title: str, receipt: dict) -> list[str]:
    """P4.5d: the overlay gate binding; every case rang SLink after its counted step; the save really ran with
    ARMED != 0 and left 0 in both SRAM copies; a pending native call rang first."""
    errors = _overlay_gate_errors(root, title, receipt, "gen2-phone-gate-v2", "phone gate")
    cases = receipt.get("cases") or {}
    for name in PHONE_GATE_CASES:
        c = cases.get(name) or {}
        ring, step = c.get("ring") or {}, c.get("step_at")
        if (c.get("result") != "PASS" or type(step) is not int or type(ring.get("frame")) is not int
                or ring["frame"] < step or ring.get("caller") != 0):
            errors.append(f"phone gate case {name} did not ring SLink after its step")
    save = (cases.get("save") or {}).get("extra") or {}
    if (not save.get("armed_at_save") or save.get("sram_primary") != 0 or save.get("sram_backup") != 0
            or type(save.get("sram_changed_bytes")) is not int or save["sram_changed_bytes"] <= 0):
        errors.append("phone gate save did not prove a real save left the SRAM phone id 0")
    native = ((cases.get("native") or {}).get("extra") or {}).get("native_ring") or {}
    slink = (cases.get("native") or {}).get("ring") or {}
    if (native.get("caller") in (None, 0) or type(native.get("frame")) is not int
            or type(slink.get("frame")) is not int or native["frame"] >= slink["frame"]):
        errors.append("phone gate native call did not keep precedence")
    moved = (cases.get("named_map_change") or {}).get("extra") or {}
    if not (moved.get("staged_before_crossing") is True and type(moved.get("wipes")) is int and moved["wipes"] > 0
            and moved.get("restaged") is True):
        errors.append("phone gate named call did not survive a map change (stage, wipe, re-stage)")
    return errors


# SP-LOWWATER (tests/live/test_gen2_sp_lowwater_gate.py, docs/gen2/POST_RC_CARDS.md): three fresh boots per title,
# each re-judged here from its recorded facts, not from its own verdict string.
SP_LOWWATER_MODES = {"A_held": 1, "B_held": 2, "released": 0}   # the hJoyDown A/B bits at the trigger
SP_LOWWATER_GUARDS = ("SlinkDelayFrameBridge", "SlinkService", "SlinkSfxService", "SlinkPhoneService", "PlaySFX",
                      "_PlaySFX", "VBlank")
SP_LOWWATER_BOUNDS = {"margin_floor": 32, "exact_window": 64, "resume_frames": 300, "deadline_frames": 300}
SP_LOWWATER_STATIC = {"print_letter_delay": 12, "service_chain": 38, "vblank": 36}   # 86 B, the design's bound
SP_LOWWATER_NESTED_POLICY = ("nested VBlank bounded by composition (service depth + handler depth), "
                             "not required to be observed")


def _sp_lowwater_run_errors(mode: str, run: dict) -> list[str]:
    """One run against the design's pass criteria (lua/tests/gen2_sfx_gate.lua P.lowwater_verdict); a missing
    composition component is its own INCONCLUSIVE error."""
    def num(v):
        return type(v) is int

    def rows(v):
        return v if isinstance(v, dict) else {}
    floor, exact = SP_LOWWATER_BOUNDS["margin_floor"], SP_LOWWATER_BOUNDS["exact_window"]
    errors = []
    t, s, p, st = rows(run.get("trigger")), rows(run.get("sfx")), rows(run.get("phone")), rows(run.get("stack"))
    if run.get("verdict") != "PASS":
        errors.append(f"verdict {run.get('verdict')!r}, not PASS")
    if t.get("battle_mode") != 1 or not num(t.get("text_delay")) or t["text_delay"] <= 0 or not num(t.get("frame")):
        errors.append("the trigger is not wBattleMode 1 with wTextDelayFrames > 0")
    if t.get("joy_down") != SP_LOWWATER_MODES[mode]:
        errors.append("hJoyDown A/B at the trigger is not the mode's")
    for where, row in (("the trigger", t), ("the first service", rows(run.get("service_irq")))):
        if row.get("hvblank") != 0 or not num(row.get("rie")) or not row["rie"] & 1:
            errors.append(f"hVBlank is not VBLANK_NORMAL or rIE lacks VBlank at {where}")
    start = t.get("frame") if num(t.get("frame")) else None
    if mode != "released" and not (start is not None and num(run.get("delay_path")) and run["delay_path"] >= start):
        errors.append("the held mode never took PrintLetterDelay.delay after the trigger")
    resumed = run.get("resumed")
    if start is None or not num(resumed) or not 0 <= resumed - start <= SP_LOWWATER_BOUNDS["resume_frames"]:
        errors.append("the text did not resume within the bound")
    posted, consumed, played = s.get("posted"), s.get("consumed"), s.get("played")
    if (s.get("accepted") is not True or s.get("pre_playing") or not all(map(num, (posted, consumed, played)))
            or not posted <= consumed <= played or played - posted > SP_LOWWATER_BOUNDS["deadline_frames"]):
        errors.append("the SFX was not posted, consumed and played within the deadline")
    if (p.get("accepted") is not True or not num(p.get("posted")) or not num(p.get("acked"))
            or p.get("armed_id") != 2 or p.get("rings_in_battle") != 0):
        errors.append("the phone was not posted, acked and ARMED, or it rang in battle")
    bottom, top, canary, low = st.get("bottom"), st.get("top"), rows(st.get("canary")), st.get("low_water")
    covered = (num(bottom) and num(top) and canary.get("hit") is True and num(canary.get("address"))
               and bottom <= canary["address"] <= top and st.get("hook_failures") == 0
               and st.get("armed_start") == bottom and st.get("armed_end") == min(top, bottom + floor + exact - 1)
               and st.get("armed_count") == st["armed_end"] - bottom + 1)
    if not covered:
        errors.append("the stack witness is not live over [wStackBottom, floor + 64) (canary/coverage)")
    elif st.get("low_water_state") == "exact" and isinstance(low, dict):
        if not (num(low.get("stack_addr")) and num(low.get("sp"))
                and min(low["stack_addr"], low["sp"]) - bottom >= floor):
            errors.append(f"the stack low water is within {floor} bytes of wStackBottom")
    elif not (st.get("low_water_state") == f">floor+{exact}" and low is None):
        errors.append("the stack low water is neither exact nor above the armed window")
    guards = rows(run.get("guards"))
    for site in SP_LOWWATER_GUARDS:
        g = rows(guards.get(site))
        sp = rows(g.get("low")).get("sp")
        if not (num(g.get("hits")) and g["hits"] > 0 and num(sp) and num(bottom) and num(top)
                and bottom + floor <= sp <= top + 1):
            errors.append(f"SP guard {site} has no hit or saw SP outside the floor")
    if run.get("excursions"):
        errors.append("an SP guard saw SP below the floor or off the stack")
    # the composed bound (option 2): deepest service-chain SP - wStackBottom - deepest VBlank-handler depth >= N;
    # an observed nested VBlank must keep N too, but it is not required
    # (OMP cx-cd30c22b F2) every composition fact is bounded: absent = INCONCLUSIVE, out of range = an error
    c = rows(run.get("composition"))
    stack_ok = num(bottom) and num(top) and top > bottom
    service = num(c.get("service_windows")) and c["service_windows"] >= 1 and c.get("service_min_sp") is not None
    handler = num(c.get("vblank_samples")) and c["vblank_samples"] >= 1 and c.get("vblank_max_depth") is not None
    service_ok = service and stack_ok and num(c["service_min_sp"]) and bottom <= c["service_min_sp"] <= top + 1
    handler_ok = handler and stack_ok and num(c["vblank_max_depth"]) and 0 <= c["vblank_max_depth"] <= top - bottom
    if service and not service_ok:
        errors.append("the deepest service-chain SP is outside [wStackBottom, wStackTop + 1]")
    if handler and not handler_ok:
        errors.append("the VBlank handler depth is outside [0, the stack capacity]")
    if service_ok and handler_ok and c["service_min_sp"] - bottom - c["vblank_max_depth"] < floor:
        errors.append(f"the composed margin (service depth + VBlank handler depth) is under {floor} bytes")
    nested = rows(run.get("nested_vblank"))
    count = nested.get("count")
    if not num(count) or count < 0:
        errors.append("nested_vblank.count is not an integer >= 0")
    elif count > 0:
        low, deepest = nested.get("min_sp"), rows(nested.get("deepest"))
        if not (stack_ok and num(low) and bottom + floor <= low <= top + 1 and deepest.get("sp") == low):
            errors.append(f"an observed nested VBlank came within {floor} bytes of wStackBottom, left the stack, "
                          "or its deepest record is not the deepest SP sampled in the handler")
    if not service:
        errors.append("INCONCLUSIVE: no service window was measured")
    if not handler:
        errors.append("INCONCLUSIVE: no VBlank handler was measured")
    return errors


def _sp_lowwater_gate_row_errors(root: Path, title: str, receipt: dict) -> list[str]:
    """SP-LOWWATER: the overlay gate binding, the design's bounds, and all three modes PASS on their facts."""
    errors = _overlay_gate_errors(root, title, receipt, "gen2-sp-lowwater-v1", "sp-lowwater gate")
    if receipt.get("bounds") != SP_LOWWATER_BOUNDS:
        errors.append("sp-lowwater gate bounds are not the design's (N=32, window 64, 300/300 frames)")
    if receipt.get("nested_vblank_policy") != SP_LOWWATER_NESTED_POLICY:
        errors.append("sp-lowwater gate receipt does not state the composed nested-VBlank policy")
    runs = receipt.get("runs") if isinstance(receipt.get("runs"), dict) else {}
    if set(runs) != set(SP_LOWWATER_MODES):
        errors.append("sp-lowwater gate receipt does not cover A_held, B_held and released")
    # (OMP cx-cd30c22b F3) the values the producer asserts: normal buttons, no harness writes, the design's static
    # bound, and each run's stack bounds equal to the title's own (the provenance-bound overlay .sym, as
    # tests/live/test_gen2_panel_gate.panel_facts reads it)
    if (receipt.get("static_bound") != SP_LOWWATER_STATIC or receipt.get("core_mode") != "CGB"
            or receipt.get("input_mode") != "normal_buttons" or receipt.get("harness_write_scopes") != []):
        errors.append("sp-lowwater gate receipt lacks static_bound, core_mode CGB, input_mode normal_buttons or an "
                      "empty harness_write_scopes")
    bounds, why = _stack_bounds(root, title)
    if why:
        errors.append(f"sp-lowwater gate: {why}")
    for mode in SP_LOWWATER_MODES:
        run = runs.get(mode) if isinstance(runs.get(mode), dict) else {}
        if run.get("mode") != mode:
            errors.append(f"sp-lowwater gate {mode}: the run's mode is {run.get('mode')!r}")
        stack = run.get("stack") if isinstance(run.get("stack"), dict) else {}
        if bounds and (stack.get("bottom"), stack.get("top")) != bounds:
            errors.append(f"sp-lowwater gate {mode}: stack bounds differ from the title's wStackBottom/wStackTop")
        errors.extend(f"sp-lowwater gate {mode}: {e}" for e in _sp_lowwater_run_errors(mode, run))
    return errors


def _stack_bounds(root: Path, title: str) -> tuple[tuple[int, int] | None, str | None]:
    """(wStackBottom, wStackTop) from data/gen2/<title>_slink.sym, once its sha256 matches the overlay provenance."""
    sym = root / "data/gen2" / f"{title}_slink.sym"
    try:
        raw = sym.read_bytes()
        pinned = json.loads((root / "data/gen2/overlay_provenance.json").read_text(encoding="utf-8"))["symbols"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, f"{sym.name} or its provenance pin is missing"
    if hashlib.sha256(raw).hexdigest() != pinned.get(sym.name):
        return None, f"{sym.name} differs from its overlay provenance pin"
    found = {name: int(addr, 16) for addr, name in
             re.findall(r"^[0-9a-f]{2}:([0-9a-f]{4}) (wStackBottom|wStackTop)$", raw.decode("utf-8"), re.M)}
    if set(found) != {"wStackBottom", "wStackTop"}:
        return None, f"{sym.name} lacks wStackBottom/wStackTop"
    return (found["wStackBottom"], found["wStackTop"]), None


def new_gates_errors(root: Path | None = None, receipt_validate=None, kinds=None, artifact_kind="overlay") -> list[str]:
    """Overlay U1/U2 and fixture-qualification receipts, each pinned by sha256.
    Explicit artifact_kind="clean"/None retains historical diagnostic checks. An empty proof is a
    release blocker. R-1/R-2/R-3/R-4/R-5g are NOT covered here -- they have no committed receipt and
    still need a fresh live run (see tests/gen2_live_gate_requirements.json's scope=).

    engine_sites/write_window rows are handed to tests/unit/test_gen2_physical_receipts.py's own
    `validate()` (N18) instead of re-deriving PASS through the Lua modules a second time; that function
    is also what makes the Silver-via-O-23 row red the moment Gold's and Silver's checkpoint rows
    differ (M.qualified compares receipt.checkpoint against Silver's own pack)."""
    root = ROOT if root is None else root
    path = root / NEW_GATES
    if not path.is_file():
        return [f"{NEW_GATES} missing"]
    if receipt_validate is None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tests.unit.test_gen2_physical_receipts import validate as receipt_validate
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    declared = {(row.get("axes", {}).get("artifact_kind", "clean"),
                 row.get("axes", {}).get("kind"), row.get("axes", {}).get("title"))
                for row in doc.get("requirements", [])}
    required = {(artifact, kind, title) for artifact in ((artifact_kind,) if artifact_kind else ("clean", "overlay")) for title in TITLES
                for kind in ("engine_sites", "write_window")}
    required |= {("overlay", kind, title) for title in TITLES
                 for kind in ("panel_gate", "sfx_gate", "w6_gate", "phone_gate", "sp_lowwater_gate")}
    for artifact, kind, title in sorted(required):
        if ((kinds is None or kind in kinds) and (artifact_kind is None or artifact == artifact_kind)
                and (artifact, kind, title) not in declared):
            errors.append(f"missing required {artifact}/{kind}/{title} receipt row")
    if artifact_kind in (None, "overlay") and (kinds is None or "qualification" in kinds):
        fixtures = {row.get("axes", {}).get("fixture") for row in doc.get("requirements", [])
                    if row.get("axes", {}).get("artifact_kind") == "overlay"
                    and row.get("axes", {}).get("kind") == "qualification"}
        errors.extend(f"missing required overlay qualification {name} receipt row"
                      for name in OVERLAY_QUALIFICATIONS if name not in fixtures)
    for row in doc.get("requirements", []):
        artifact = (row.get("axes") or {}).get("artifact_kind", "clean")
        if artifact_kind is not None and artifact != artifact_kind:
            continue
        rid, axes, kind = row["id"], row["axes"], row["axes"]["kind"]
        if artifact not in ("clean", "overlay"):
            errors.append(f"{rid}: unknown artifact kind")
            continue
        if kinds is not None and kind not in kinds:
            continue
        proofs = row.get("proofs") or []
        if not proofs:
            errors.append(f"{rid}: no receipt registered (an empty proof is a release blocker)")
            continue
        for proof in proofs:
            entry = (proof.get("receipts") or {}).get("receipt")
            if not entry:
                errors.append(f"{rid}: receipt not registered")
                continue
            if kind in ("panel_gate", "sfx_gate", "w6_gate", "phone_gate", "sp_lowwater_gate"):
                expected = f"tests/fixtures/gen2/receipts/overlay/{axes['title']}.{kind}.json"
                if artifact != "overlay" or entry.get("path") != expected:
                    errors.append(f"{rid}: feature gate requires explicit overlay kind and current receipt path {expected}")
                    continue
            receipt, why = _new_gate_receipt(root, entry)
            if receipt is None:
                errors.append(f"{rid}: {why}")
            elif kind in ("engine_sites", "write_window"):
                proven, why = (receipt_validate(kind, axes["title"], receipt, artifact) if artifact == "overlay"
                               else receipt_validate(kind, axes["title"], receipt))
                if proven is None:
                    errors.append(f"{rid}: {why}")
                if kind == "engine_sites":
                    runs = receipt.get("runs", [receipt])
                    runs = list(runs.values()) if isinstance(runs, dict) else runs
                    for index, run in enumerate(runs):
                        if "clock_setup" in run or "u1_attempt" in run:
                            errors.extend(f"{rid}: run {index} clock setup: {error}"
                                          for error in _clock_setup_errors(root, axes["title"], run))
            elif kind == "qualification":
                errors.extend(f"{rid}: {e}" for e in _qualification_row_errors(root, axes["fixture"], receipt, artifact))
            elif kind == "panel_gate":
                errors.extend(f"{rid}: {e}" for e in _panel_gate_row_errors(root, axes["title"], receipt))
            elif kind == "sfx_gate":
                errors.extend(f"{rid}: {e}" for e in _sfx_gate_row_errors(root, axes["title"], receipt))
            elif kind == "w6_gate":
                errors.extend(f"{rid}: {e}" for e in _w6_gate_row_errors(root, axes["title"], receipt))
            elif kind == "phone_gate":
                errors.extend(f"{rid}: {e}" for e in _phone_gate_row_errors(root, axes["title"], receipt))
            elif kind == "sp_lowwater_gate":
                errors.extend(f"{rid}: {e}" for e in _sp_lowwater_gate_row_errors(root, axes["title"], receipt))
            elif kind == "inspect_run":
                errors.extend(f"{rid}: {e}" for e in _inspect_run_row_errors(receipt, artifact, root))
            else:
                errors.append(f"{rid}: no validator for receipt kind {kind!r}")
    return errors


def _new_gates_main() -> int:
    errors = new_gates_errors()
    for error in errors:
        print(f"RED  {error}")
    _warn_stale("new-gates.")
    if errors:
        print(f"new gates: {len(errors)} gap(s); a missing/edited/unproven receipt is not a pass")
        return 1
    print("new gates: every engine-site, write-window and qualification receipt bound and PHYSICAL")
    return 0


# live-gates (BINDING P4.1 panel/fade, P4.2 sound, W-6 writer exclusion, SP-LOWWATER): one PHYSICAL row per
# kind x title, pinned here so deleting a row from tests/gen2_live_gate_requirements.json cannot shrink the lane.
LIVE_GATE_KINDS = ("panel_gate", "sfx_gate", "w6_gate", "sp_lowwater_gate")


def live_gates_errors(root: Path | None = None, receipt_validate=None) -> list[str]:
    root = ROOT if root is None else root
    try:
        rows = json.loads((root / NEW_GATES).read_text(encoding="utf-8"))["requirements"]
        have = {(row["axes"].get("artifact_kind", "clean"), row["axes"]["kind"], row["axes"].get("title"))
                for row in rows if (row.get("axes") or {}).get("artifact_kind", "clean") == "overlay"}
    except (OSError, ValueError, KeyError, TypeError):
        return [f"{NEW_GATES} missing or malformed"]
    errors = [f"{kind} {title}: no overlay row in {NEW_GATES}"
              for kind in LIVE_GATE_KINDS for title in TITLES if ("overlay", kind, title) not in have]
    return errors + new_gates_errors(root, receipt_validate, kinds=LIVE_GATE_KINDS)


def trade_gates_errors(root: Path | None = None, duo=None) -> list[str]:
    """live-trade-gates (BINDING P4.3, T-1..T-4): every trade case receipted on C-C, G-S and C-G (O-34)."""
    cases = frozenset(TRADE_END_STATUS)
    return duo_matrix_errors(root, duo, required=dict.fromkeys((("overlay", *pair) for pair in DUO_PAIRS), cases), only=cases)


# BINDING P3b.7's C-C / G-S scenario list, named as tools/e2e_duo.py registers Gen 2 cells (gen2_<name>;
# linked_faint_bench is gen2_faint). shiny_bonus is absent: O-26 records it as a release limit.
# ponytail: names not yet registered are guesses from docs/gen2/reviews/DUO_SCENARIO_ROADMAP_2026-09-23.md;
# a different registered name keeps the lane red until this set is edited, never green.
DUO_PAIRS_SCENARIOS = frozenset({
    "link", "gen2_ball_gate", "gen2_boxed_capture", "gen2_faint", "gen2_faint_active", "gen2_poison",
    "gen2_whiteout", "gen2_pc_ops", "gen2_changebox", "gen2_species_clause", "gen2_gender_clause",
    "gen2_type_clause", "gen2_reconnect", "gen2_soft_reset", "gen2_evolution", "gen2_npc_trade", "gen2_gift",
    "gen2_egg_hatch", "gen2_admit_wrong_rom",
})


def duo_pairs_errors(root: Path | None = None, duo=None) -> list[str]:
    """duo-pairs: the whole matrix, every P3b.7 scenario on C-C and G-S, every trade case on all three (O-34)."""
    trades = frozenset(TRADE_END_STATUS)
    required = dict.fromkeys((("overlay", *pair) for pair in DUO_PAIRS), trades)
    for kind in ("overlay",):
        required.update(dict.fromkeys(((kind, "crystal", "crystal"), (kind, "gold", "silver")), DUO_PAIRS_SCENARIOS | (trades if kind == "overlay" else frozenset())))
    return duo_matrix_errors(root, duo, required=required)


def _lf_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


def _published_grant(root, provenance):
    components = [("lock", (root / "data/gen2_sources.lock.json").read_bytes()),
                  ("build-provenance", (root / "data/gen2/build_provenance.json").read_bytes()),
                  ("overlay-provenance", (root / "data/gen2/overlay_provenance.json").read_bytes())]
    for title in TITLES:
        pin = provenance["outputs"]["poke" + title]["ups"]
        raw = (root / pin["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
            raise ValueError("published UPS differs while checking grant")
        components.append(("ups:" + title, raw))
    digest = hashlib.sha256()
    for label, raw in components:
        encoded = label.encode("ascii")
        digest.update(len(encoded).to_bytes(2, "big"))
        digest.update(encoded)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def g4_packet_errors(root: Path | None = None, release_ups=None, require_admitted: bool = True) -> list[str]:
    """The non-receipt half of the G4 packet (PLAN §6 P4 exit, §6.1; BINDING P4.4): published overlay
    bytes match their provenance, each overlay row is ADMITTED at those hashes, the owner signed the G4
    ledger row, and the release bundle (tools/make_release.py) ships every overlay UPS."""
    root = ROOT if root is None else root
    try:
        provenance = json.loads((root / _OVERLAY_PROVENANCE).read_text(encoding="utf-8"))
        published = {row["slink_title"]: row for row in provenance["outputs"].values()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [f"{_OVERLAY_PROVENANCE} missing or malformed"]
    if release_ups is None:
        import make_release
        # The release's companion set, computed from the SAME catalog test make_release packages with, so
        # this check and the ZIP can never disagree about which titles are activated.
        release_ups = make_release.gb_companion_ups(root)
    errors = []
    symbols = provenance.get("symbols") or {}
    for title in TITLES:
        out = published.get(title)
        if out is None:
            errors.append(f"{title}: no published overlay in {_OVERLAY_PROVENANCE}")
            continue
        ups = out.get("ups") or {}
        try:
            ups_sha = hashlib.sha256((root / ups["file"]).read_bytes()).hexdigest()
        except (OSError, KeyError, TypeError):
            ups_sha = None
        if ups_sha is None or ups_sha != ups.get("sha256"):
            errors.append(f"{title}: {ups.get('file')} is missing or differs from its published sha256")
        for ext in ("sym", "map"):
            name = f"{title}_slink.{ext}"
            if not symbols.get(name) or _lf_sha256(root / "data/gen2" / name) != symbols[name]:
                errors.append(f"{title}: data/gen2/{name} is missing or differs from its published sha256")
        try:
            matrix = json.loads((root / f"data/games/gen2_{title}/admission.json").read_text(encoding="utf-8"))
            row = next(r for r in matrix["artifacts"] if r.get("kind") == "overlay")
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            row = {}
        # require_admitted=False is the P4.4 promotion's own precondition (tools/gen_gen2_admission.py
        # --promote-overlays): every other packet item, before the rows it is about to write.
        if require_admitted and (row.get("selection") != "SELECTED" or row.get("status") != "ADMITTED" or row.get("sha1") != out.get("sha1")
                                 or (row.get("ups") or {}).get("sha256") != ups.get("sha256")):
            errors.append(f"{title}_overlay: admitted-artifact row is not ADMITTED at the published overlay "
                          f"hashes (P4.4 promotion; today status={row.get('status')!r})")
        try:
            # The SIDECAR-vs-PUBLISHED identity holds in BOTH modes: the binding must name this title's
            # published overlay sha1 and be a well-formed overlay binding. That is a property of the
            # artifact, not of the catalog row.
            identity = _execution_identity(root, title, "overlay")
            # The ROW's binding pin and its runtime grant are written BY the promotion, so they are
            # release-only: with require_admitted=False this is the precondition -- the precondition
            # checks every other packet item BEFORE the rows it is about to write, and demanding them
            # here made --promote-overlays unable to ever run.
            if require_admitted:
                if row.get("binding_sha256") != identity["binding_sha256"]:
                    errors.append(f"{title}_overlay: binding pin differs")
                gate = row.get("runtime_gate") or {}
                grant = _published_grant(root, provenance)
                if (gate.get("id") != "G4" or gate.get("state") != "ADMITTED"
                        or gate.get("grant_fingerprint") != grant):  # the grant lives only in runtime_gate (gen_gen2_admission.overlay_row)
                    errors.append(f"{title}_overlay: runtime G4 grant invalid")
        except (OSError, KeyError, ValueError, TypeError) as exc:
            errors.append(f"{title}_overlay: binding/grant unavailable: {exc}")
        # A release must carry the UPS of a title it admits, and must not be blocked by one it does not:
        # make_release.gb_companion_ups ships the Gen 2 UPS exactly for ADMITTED overlays, so membership is
        # only a meaningful question once admission is required.
        if require_admitted and Path(ups.get("file") or "?").name not in release_ups:
            errors.append(f"{title}: tools/make_release.py does not ship {ups.get('file')}")
    try:
        plan = (root / "docs/gen2/PLAN.md").read_text(encoding="utf-8")
    except OSError:
        plan = ""
    errors.extend(f"docs/gen2/PLAN.md §6.1: {e}" for e in g4_signature_errors(plan))
    return errors


# PLAN §6.1: "owner signatures are chat rulings, quoted". The Signed cell of the G4 row must read
#     owner YYYY-MM-DD: "<the owner's ruling, quoted verbatim>"
# and the G4 row must be the only one, inside §6.1. Placeholders never sign.
_SIGNATURE = re.compile(r'owner (\d{4}-\d{2}-\d{2}): "([^"|]+)"')
_PLACEHOLDERS = frozenset({"", "tbd", "x", "pending", "awaiting owner", "-", "—", "signed", "todo", "n/a"})


def g4_signature_errors(plan: str) -> list[str]:
    lines = plan.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("### 6.1 ")), None)
    if start is None:
        return ["no §6.1 gate ledger section"]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith(("## ", "### "))), len(lines))
    rows = [i for i, line in enumerate(lines) if line.replace(" ", "").startswith("|G4|")]
    inside = [i for i in rows if start < i < end]
    if len(inside) != 1 or len(rows) != 1:
        return [f"the G4 ledger row must appear exactly once, inside §6.1 (found {len(inside)} inside, "
                f"{len(rows)} in the file)"]
    cells = [cell.strip() for cell in lines[inside[0]].strip().strip("|").split("|")]
    signed = cells[1] if len(cells) > 1 else ""
    match = _SIGNATURE.fullmatch(signed)
    if not match:
        return ["the G4 ledger row carries no owner signature"]   # grammar: see the comment above
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError:
        return [f"the G4 signature date {match.group(1)!r} is not a date"]
    if match.group(2).strip().lower() in _PLACEHOLDERS:
        return [f"the G4 signature quotes a placeholder ({match.group(2)!r}), not an owner ruling"]
    return []


# The live-new-gates run attestation (R-1/R-2/R-3/R-4/R-5g): those rows have no receipt of their own; a
# SLINK_LIVE=1 pytest tests/live/test_gen2_new_gates.py run on all three titles writes one committed
# attestation, pinned in tests/gen2_live_gate_requirements.json as the single kind "inspect_run" row.
INSPECT_RUN_SCHEMA = "gen2-live-new-gates-attestation-v1"
INSPECT_RUN_IDS = ("R-1", "R-2", "R-3", "R-4", "R-5g")


def _inspect_run_row_errors(receipt: dict, artifact_kind="clean", root=None) -> list[str]:
    errors = []
    if (receipt.get("schema") != INSPECT_RUN_SCHEMA or receipt.get("result") != "PASS"
            or receipt.get("evidence_level") != "PHYSICAL" or receipt.get("test") != "tests/live/test_gen2_new_gates.py"):
        errors.append(f"inspect run attestation is not a PHYSICAL PASS {INSPECT_RUN_SCHEMA} of tests/live/test_gen2_new_gates.py")
    if not set(INSPECT_RUN_IDS) <= set(receipt.get("requirement_ids") or ()):
        errors.append(f"inspect run attestation does not cover {list(INSPECT_RUN_IDS)}")
    titles = receipt.get("titles") or {}
    if any(titles.get(title) != "PASS" for title in TITLES):
        errors.append("inspect run attestation lacks a PASS for every title (crystal, gold, silver)")
    if artifact_kind == "overlay":
        for title in TITLES:
            try:
                identity = _execution_identity(ROOT if root is None else root, title, artifact_kind)
                # the attestation records the identity triple; base_sha1 is bound through binding_sha256
                want = {key: identity[key] for key in ("kind", "rom_sha1", "binding_sha256")}
                if (receipt.get("artifacts") or {}).get(title) != want:
                    errors.append(f"inspect run {title}: wrong executed overlay identity")
            except (OSError, ValueError, KeyError) as exc:
                errors.append(f"inspect run {title}: binding unavailable: {exc}")
    counts = receipt.get("pytest") or {}
    if (type(counts.get("passed")) is not int or counts["passed"] <= 0
            or any(counts.get(key) != 0 for key in ("failed", "skipped", "errors", "xfailed", "xpassed", "deselected"))):
        errors.append("inspect run attestation is not a clean pytest run (passes only, zero skips)")
    return errors


def inspect_run_errors(root: Path | None = None) -> list[str]:
    """Presence of the one inspect_run row; its receipt is validated (pin, content) by new_gates_errors."""
    root = ROOT if root is None else root
    try:
        rows = json.loads((root / NEW_GATES).read_text(encoding="utf-8"))["requirements"]
    except (OSError, ValueError, KeyError, TypeError):
        return [f"{NEW_GATES} missing or malformed"]
    errors = []
    for kind in ("overlay",):
        found = [row for row in rows if (row.get("axes") or {}).get("kind") == "inspect_run"
                 and (row.get("axes") or {}).get("artifact_kind", "clean") == kind]
        if len(found) != 1 or not found[0].get("proofs"):
            errors.append(f"no committed {kind} live-new-gates run attestation ({'/'.join(INSPECT_RUN_IDS)}): "
                          f"{NEW_GATES} needs exactly one {kind} inspect_run row with a pinned {INSPECT_RUN_SCHEMA} receipt")
    return errors


# CODE-DIGEST: gate receipt kinds whose verdict runs through the shipped client or server code. Fixture
# qualification rows prove save bytes through the codec, not the client, so they are not included.
CLIENT_PATH_GATE_KINDS = ("engine_sites", "write_window", "panel_gate", "sfx_gate", "w6_gate", "phone_gate",
                          "sp_lowwater_gate", "inspect_run")


def code_staleness(root: Path | None = None, head: str | None = None, *, artifact_kind=None) -> list[tuple[str, str]]:
    """Diagnostic (cell, verdict) for registered proofs; release callers select artifact_kind="overlay".
    Inspect each duo proof (the pydec receipt's CODE_DIGEST line) and each
    client-path gate receipt (its "code_digest" object) that does not bind HEAD's production code.
    STALE: earned on other code or on a dirty tree. STALE-UNKNOWN: no stamp."""
    root = ROOT if root is None else root
    head = code_digest.head_digest(root) if head is None else head
    out = []
    for manifest, kinds in ((DUO_MATRIX, None), (NEW_GATES, CLIENT_PATH_GATE_KINDS)):
        try:
            rows = json.loads((root / manifest).read_text(encoding="utf-8")).get("requirements") or []
        except (OSError, ValueError):
            continue
        for row in rows:
            if artifact_kind is not None and (row.get("axes") or {}).get("artifact_kind", "clean") != artifact_kind:
                continue
            if kinds is not None and (row.get("axes") or {}).get("kind") not in kinds:
                continue
            for proof in row.get("proofs") or []:
                receipts = proof.get("receipts") or {}
                entry = receipts.get("pydec" if kinds is None else "receipt") or {}
                try:
                    text = (root / entry["path"]).read_text(encoding="utf-8")
                    if kinds is None:
                        bodies = [line[len("CODE_DIGEST "):] for line in text.splitlines()
                                  if line.startswith("CODE_DIGEST ")]
                        stamp = json.loads(bodies[0]) if len(bodies) == 1 else None
                    else:
                        stamp = json.loads(text).get("code_digest")
                except (OSError, KeyError, TypeError, ValueError, AttributeError):
                    stamp = None
                verdict = code_digest.stamp_verdict(stamp, head)
                if verdict:
                    cell = row.get("id") if kinds is not None else f"{row.get('id')}/{proof.get('scenario')}"
                    out.append((cell, verdict))
    return out


def stale_errors(root: Path | None = None, head: str | None = None) -> list[str]:
    try:
        return [f"{cell}: {verdict}" for cell, verdict in code_staleness(root, head, artifact_kind="overlay")]
    except (OSError, subprocess.CalledProcessError) as exc:
        return [f"cannot compute the HEAD production code digest: {exc}"]


def _warn_stale(prefix: str) -> None:
    """Outside release-evidence, STALE is a distinct warning verdict, never a failure."""
    for line in stale_errors():
        if line.startswith(prefix) or line.startswith("cannot compute"):
            print(f"STALE  {line}")


def release_evidence_errors(root: Path | None = None, duo=None, receipt_validate=None,
                            release_ups=None, require_admitted: bool = True, head: str | None = None) -> list[str]:
    """release-evidence: the G4 packet and every receipt lane it rests on, one prefixed line per gap.
    A receipt that does not bind HEAD's production code (STALE / STALE-UNKNOWN) is RED here."""
    parts = (("packet", g4_packet_errors(root, release_ups, require_admitted)),
             ("code", stale_errors(root, head)),
             ("fixtures", fixtures_errors(root, artifact_kind="overlay")),
             ("live-new-gates-run", inspect_run_errors(root)),
             ("live-new-gates", new_gates_errors(root, receipt_validate)),
             ("live-gates", live_gates_errors(root, receipt_validate)),
             ("live-trade-gates", trade_gates_errors(root, duo)),
             ("duo-pairs", duo_pairs_errors(root, duo)),
             # G2-C5-WIRE: C-5 is a real gate here, not a separate LANE, so it cannot be double-judged
             # and cannot be opted out of. An uninstalled packet is the named RED C5_NOT_INSTALLED.
             ("c5", c5_gate_errors(root, head)))
    return [f"{lane}: {error}" for lane, errors in parts for error in errors]


# fixtures (BINDING P3b.2; F-6, S-7). PLAYED = pinned full-chain qualification receipt; SYNTH = O-33 disclosure.
FIXTURES_DIR = "tests/fixtures/gen2"
SYNTH_SCHEMA = "gen2-synth-disclosure-v1"
# A field is written into both save copies (primary/backup) or, outside the mirrored regions, at one
# CartRAM offset (cart), e.g. the checksum words (tools/gen2_synth_fixtures.py).
SYNTH_FIELD_SHAPES = (frozenset({"symbol", "offset", "wram", "size", "primary", "backup", "old_hex", "new_hex"}),
                      frozenset({"symbol", "offset", "wram", "size", "cart", "old_hex", "new_hex"}))


def _fixture_names(root: Path) -> tuple[set[str], list[str]]:
    """Every fixture a release lane can stage: the played plan (tools/gen2_fixtures.py FIXTURES, which
    tools/e2e_duo.py requires of every Gen 2 cell), the O-33 synth registry, every committed SaveRAM and
    every name the release duo matrix pins."""
    from tools import gen2_fixtures, gen2_synth_fixtures
    names = set(gen2_fixtures.BY_NAME) | set(getattr(gen2_synth_fixtures, "SYNTH_FIXTURES", ()))
    names |= {path.stem for path in (root / FIXTURES_DIR).glob("*.SaveRAM")}
    try:
        for row in json.loads((root / DUO_MATRIX).read_text(encoding="utf-8"))["requirements"]:
            if (row.get("axes") or {}).get("artifact_kind", "clean") != "overlay":
                continue
            for key in ("fixtures", "trade_fixtures"):
                names |= set((row["axes"].get(key) or {}).values())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return names, [f"{DUO_MATRIX} missing or malformed"]
    return names, []


def _played_pins(root: Path, artifact_kind="clean") -> dict:
    try:
        rows = json.loads((root / NEW_GATES).read_text(encoding="utf-8"))["requirements"]
        return {row["axes"]["fixture"]: ((row.get("proofs") or [{}])[0].get("receipts") or {}).get("receipt")
                for row in rows if (row.get("axes") or {}).get("artifact_kind", "clean") == artifact_kind
                and row["axes"]["kind"] == "qualification"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _played_errors(root: Path, name: str, raw: bytes, pins: dict, identity, artifact_kind="clean") -> list[str]:
    """The pinned receipt, then tests/live/test_gen2_new_gates.py's qualified_identity(): a passed full chain
    (PYDEC qualify, GAME CONTINUE boot, native re-save + reload, post oracle) whose fixture sha256 is THESE bytes."""
    entry = pins.get(name)
    namespace = "/overlay" if artifact_kind == "overlay" else ""
    if not entry or entry.get("path") != f"{FIXTURES_DIR}/receipts{namespace}/{name}.qualification.json":
        return [f"no sha256-pinned qualification receipt in {NEW_GATES}"]
    _receipt, why = _new_gate_receipt(root, entry)
    if why:
        return [why]
    try:
        identity(name, raw, repo=root)
    except (AssertionError, ValueError, KeyError, TypeError, OSError) as exc:
        return [str(exc)]
    return []


def _synth_errors(root: Path, name: str, raw: bytes, disclosure: dict, played, rebuild) -> list[str]:
    """O-33: a tools/gen2_synth_fixtures.py disclosure over THESE bytes, built on a PLAYED fixture's current
    bytes; rebuilding it from that base and its edits must reproduce the file and its changed-field list."""
    title = name.split("_", 1)[0]
    errors = []
    if (disclosure.get("schema") != SYNTH_SCHEMA or disclosure.get("builder") != "tools/gen2_synth_fixtures.py"
            or disclosure.get("title") != title):
        errors.append(f"SYNTH disclosure is not a {SYNTH_SCHEMA} from tools/gen2_synth_fixtures.py for {title}")
    if disclosure.get("sha256") != hashlib.sha256(raw).hexdigest():
        errors.append("SYNTH disclosure does not cover the committed bytes")
    fields = disclosure.get("fields")
    if (not isinstance(fields, list) or not fields
            or any(not isinstance(field, dict) or set(field) not in SYNTH_FIELD_SHAPES for field in fields)):
        errors.append("SYNTH disclosure lists no changed fields")
    if not disclosure.get("source_facts") or not isinstance(disclosure.get("edits"), dict):
        errors.append("SYNTH disclosure names no edits or source facts")
    base, base_raw = disclosure.get("base_fixture"), None
    if not isinstance(base, str) or base == name or played(base):
        errors.append(f"SYNTH base {base!r} is not a PLAYED, qualified fixture")
    else:
        base_raw = (root / FIXTURES_DIR / f"{base}.SaveRAM").read_bytes()
        if hashlib.sha256(base_raw).hexdigest() != disclosure.get("base_sha256"):
            errors.append(f"SYNTH base {base} bytes differ from the disclosed base_sha256 (stale)")
    if errors:
        return errors
    try:
        rebuilt, again = rebuild(title, base_raw, disclosure["edits"], root=root, base_name=base)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return [f"SYNTH rebuild failed: {exc}"]
    if rebuilt != raw or again.get("fields") != fields:
        return ["SYNTH rebuild from the disclosed base and edits does not reproduce the file (undisclosed change)"]
    return []


_LAYOUTS: dict = {}


def _checksum_witness(root: Path, title: str, raw: bytes) -> str | None:
    """gen2_codec.strict_checksum_witness over the CartRAM (both copies, checksums, markers)."""
    from server.adapters import gen2_codec as codec
    if len(raw) != 0x8000 + 22:
        return "is not a 0x8000-byte CartRAM plus the 22-byte RTC trailer"
    try:
        key = (str(root), title)
        if key not in _LAYOUTS:
            _LAYOUTS[key] = codec.for_foundation(title, root=root)
        if not codec.strict_checksum_witness(raw[:0x8000], _LAYOUTS[key])["valid"]:
            return "fails the strict checksum/marker/copy witness"
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return f"checksum witness unavailable: {exc}"
    return None


def fixtures_errors(root: Path | None = None, *, names=None, identity=None, witness=None,
                    rebuild=None, artifact_kind="clean") -> list[str]:
    """Validate fixture bytes with qualifications for artifact_kind.
    Release/CLI callers explicitly select overlay; the clean default preserves historical diagnostics.
    Each referenced fixture exists, passes the checksum witness and is
    PLAYED (a pinned, passed full-chain receipt binding these bytes) or SYNTH (a valid O-33 disclosure over a
    PLAYED base). There is no third way in: a missing, stale or undisclosed fixture is RED."""
    root = ROOT if root is None else root
    errors = []
    if names is None:
        names, errors = _fixture_names(root)
    if not names:
        return errors + ["no fixtures referenced (an empty inventory is not a pass)"]
    if identity is None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tests.live.test_gen2_new_gates import qualified_identity
        def identity(name, raw, *, repo):
            return qualified_identity(name, raw, repo=repo, kind=artifact_kind)
    if rebuild is None:
        from tools.gen2_synth_fixtures import build as rebuild
    witness = witness or _checksum_witness
    pins = _played_pins(root, artifact_kind)
    verdicts: dict[str, list[str]] = {}

    def played(name: str) -> list[str]:
        if name not in verdicts:
            try:
                raw = (root / FIXTURES_DIR / f"{name}.SaveRAM").read_bytes()
            except OSError:
                verdicts[name] = ["SaveRAM missing"]
            else:
                verdicts[name] = _played_errors(root, name, raw, pins, identity, artifact_kind)
        return verdicts[name]

    for name in sorted(names):
        path = root / FIXTURES_DIR / f"{name}.SaveRAM"
        if not path.is_file():
            errors.append(f"{name}: {FIXTURES_DIR}/{name}.SaveRAM missing")
            continue
        raw = path.read_bytes()
        bad = witness(root, name.split("_", 1)[0], raw)
        if bad:
            errors.append(f"{name}: {bad}")
        synth = root / FIXTURES_DIR / f"{name}.synth.json"   # beside the SaveRAM; receipts/*.json are receipts only
        if name in pins:
            why = played(name)
        elif synth.is_file():
            try:
                disclosure = json.loads(synth.read_text(encoding="utf-8"))
            except ValueError:
                disclosure = None
            why = _synth_errors(root, name, raw, disclosure if isinstance(disclosure, dict) else {},
                                played, rebuild)
        else:
            why = ["undisclosed: no pinned PLAYED qualification receipt and no O-33 SYNTH disclosure"]
        errors.extend(f"{name}: {e}" for e in why)
    return errors


# C-5 (randomized admission; owner closed it 2026-10-04 on 10 cells, G-g a named limit). Reads the gen2-c5-duo-cell-v1
# manifests tools/c5_runner.py writes per PASS cell. Separate from the duo matrix on purpose: those rows pin the PUBLISHED
# overlay sha1 (_receipt_errors), a randomized cart has its own contract sha1. The cells and named limits are pinned here
# AND in C5_REQUIREMENTS, so editing either one alone cannot waive a cell or drop the limit.
C5_REQUIREMENTS = "tests/gen2_c5_requirements.json"
C5_SCHEMA = "gen2-c5-duo-cell-v1"
C5_MARKER = "gen2.requirement.C-5"
C5_CELLS = {
    "c5/cc/link": ["G-a"], "c5/gs/link": ["G-b"], "c5/cg/link": ["G-b"],
    "c5/cc/gen2_reconnect": ["G-d"], "c5/gs/gen2_reconnect": ["G-d"],
    "c5/cc/gen2_gift": ["G-e", "G-f"], "c5/gs/gen2_gift": ["G-e", "G-f"],
    "c5/ct/gen2_npc_trade": ["G-h"], "c5/cc/gen2_c5_wrong_rom": ["G-c"], "c5/cc/gen2_c5_no_contract": ["G-i"],
}
C5_NAMED_LIMITS = {"G-g": ("2026-10-04", "roamer catches are not detected on overlay carts")}


def _c5_requirement_errors(root: Path) -> list[str]:
    try:
        req = json.loads((root / C5_REQUIREMENTS).read_text(encoding="utf-8"))
        cells = {row["id"]: row["covers"] for row in req["cells"]}
        limits = {row["gate"]: (row.get("date"), row.get("text")) for row in req["named_limits"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"{C5_REQUIREMENTS}: unreadable or malformed ({exc})"]
    errors = []
    if len(cells) != len(req["cells"]) or cells != C5_CELLS:
        errors.append(f"{C5_REQUIREMENTS}: required cells differ from the pinned C-5 set {sorted(C5_CELLS)}")
    for gate_id, (date, text) in C5_NAMED_LIMITS.items():
        if limits.get(gate_id) != (date, text):
            errors.append(f"{C5_REQUIREMENTS}: named limit {gate_id} missing or altered (owner ruling {date}: {text!r})")
    return errors


def _c5_manifest_errors(folder: Path, m: dict, cid: str, head: str, jars: set, runner,
                        equivalent: str | None = None) -> list[str]:
    errors = []
    if m.get("schema") != C5_SCHEMA:
        errors.append(f"schema {m.get('schema')!r} is not {C5_SCHEMA}")
    if m.get("receipt_marker") != C5_MARKER:
        errors.append(f"receipt_marker {m.get('receipt_marker')!r} is not {C5_MARKER}")
    if m.get("evidence_level") != "PHYSICAL":
        errors.append(f"evidence_level {m.get('evidence_level')!r} is not PHYSICAL")
    if m.get("covers") != C5_CELLS[cid]:
        errors.append(f"covers {m.get('covers')} is not {C5_CELLS[cid]}")
    pair, scenario = str(m.get("pair")), str(m.get("scenario"))
    if f"c5/{pair}/{scenario}" != cid:
        errors.append(f"pair/scenario {pair}/{scenario} do not name the cell")
    disclosures = m.get("disclosures") if isinstance(m.get("disclosures"), dict) else {}
    for kind in ("SYNTH", "HARNESS", "NATIVE"):
        value = disclosures.get(kind)
        values = value if isinstance(value, list) else [value]
        if not value or not all(isinstance(v, str) and v.strip() for v in values):
            errors.append(f"missing {kind} disclosure")
    if m.get("code_digest") != head and (equivalent is None or m.get("code_digest") != equivalent):
        errors.append(f"STALE: manifest CODE_DIGEST {m.get('code_digest')} != current code digest {head} "
                      "(evidence earned on other production code; re-run tools/c5_runner.py run)")
    jar = (m.get("provision") or {}).get("jar_sha256")
    if jar not in jars:
        errors.append(f"jar sha256 {jar} is not pinned in data/upr_jars.json")
    try:
        contract = {"players": {s: {"rom_sha1": m["contract"]["players"][s]["rom_sha1"]} for s in ("a", "b")}}
    except (KeyError, TypeError):
        return errors + ["manifest records no per-side contract sha1"]
    for side in ("a", "b"):
        provisioned = (((m.get("provision") or {}).get("players") or {}).get(side) or {}).get("rom_sha1")
        if provisioned != contract["players"][side]["rom_sha1"]:
            errors.append(f"{side}: provisioned sha1 {provisioned} != contract sha1 {contract['players'][side]['rom_sha1']}")
    receipts = m.get("receipts") if isinstance(m.get("receipts"), dict) else {}
    if not receipts:
        errors.append("manifest pins no receipts")
    names = {}
    for rel, sha in receipts.items():
        path = folder / Path(str(rel)).name   # ponytail: keyed by file name, so the set judges in place or copied flat
        names[path.name] = path
        if not path.is_file():
            errors.append(f"receipt missing: {path.name}")
        elif runner.lf_sha256(path) != sha:
            errors.append(f"receipt tampered: {path.name} sha256 {runner.lf_sha256(path)[:12]} != manifest {str(sha)[:12]}")
    prefix = f"duo_{scenario.removeprefix('gen2_')}_{pair}_"
    sides = ["a", "b", "pydec"] + (["a_initial", "a_same_save", "a_wrong_save"] if scenario == "gen2_reconnect" else [])
    texts = {}
    for side in sides:
        path = names.get(f"{prefix}{side}_result.txt")
        if path is None or not path.is_file():
            errors.append(f"{side}: no pinned result receipt")
        else:
            texts[side] = path.read_text(encoding="utf-8", errors="replace").splitlines()
    stamps = [line for line in texts.get("pydec", []) if line.startswith("CODE_DIGEST ")]
    try:
        stamp = json.loads(stamps[0].split(" ", 1)[1]) if len(stamps) == 1 else None
        if isinstance(stamp, dict) and stamp.get("dirty") != []:
            errors.append(f"STALE: the run's CODE_DIGEST stamp is dirty ({stamp.get('dirty')}); dirty must be []")
        errors.extend(runner.c5_receipt_errors(texts, contract, m.get("code_digest")))
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        errors.append(f"unparseable result receipt line ({exc})")
    return errors


def c5_errors(receipts_dir, root: Path | None = None, head: str | None = None) -> list[str]:
    """--c5: every required C-5 cell exactly once, each a PHYSICAL PASS on the randomized carts its manifest's contract
    names, at the current or exactly equivalent production digest, receipts byte-identical to their pins, a pinned
    jar, and the named limit(s) still recorded. The PASS definition is c5_runner.c5_receipt_errors, never re-derived."""
    import c5_runner

    root = ROOT if root is None else root
    folder = Path(receipts_dir)
    errors = _c5_requirement_errors(root)
    try:
        head = code_digest.head_digest(root) if head is None else head
        equivalent = code_digest.c5_admission_equivalent(root, head)
    except (OSError, subprocess.CalledProcessError) as exc:
        return errors + [f"cannot compute the current production code digest: {exc}"]
    try:
        jars = set(json.loads((root / "data/upr_jars.json").read_text(encoding="utf-8")).values())
    except (OSError, ValueError, AttributeError) as exc:
        return errors + [f"data/upr_jars.json unreadable ({exc})"]
    if not folder.is_dir():
        return errors + [f"{folder}: no C-5 receipts directory"]
    seen = {}
    reused = False
    for path in sorted(folder.glob("*.manifest.json")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: unreadable manifest ({exc})")
            continue
        cid = m.get("cell") if isinstance(m, dict) else None
        if cid not in C5_CELLS:
            errors.append(f"{path.name}: cell {cid!r} is not a required C-5 cell")
            continue
        seen.setdefault(cid, []).append(path.name)
        errors.extend(f"{cid}: {e}" for e in _c5_manifest_errors(folder, m, cid, head, jars, c5_runner, equivalent))
        reused |= equivalent is not None and m.get("code_digest") == equivalent
    for cid in C5_CELLS:
        if cid not in seen:
            errors.append(f"{cid}: missing (no PASS manifest)")
        elif len(seen[cid]) > 1:
            errors.append(f"{cid}: duplicated ({', '.join(seen[cid])})")
    if reused and not errors:
        print(f"C-5: reused PHYSICAL evidence at {equivalent}; SOURCE/MODEL admission-error equivalence "
              f"to {head}; no new physical run")
    return errors


# G2-C5-WIRE: the INSTALLED C-5 packet. c5_errors above judges a folder the caller names; on its own it could
# never be a gate, because a repository with no C-5 receipts installed would simply not have anything to
# judge. These two make it one: the packet is expected at a fixed path, pinned by its own install manifest,
# at the current or exactly equivalent digest; absence is a NAMED RED, never a skip.
C5_RECEIPTS = "tests/fixtures/gen2/receipts/c5"
C5_INSTALL_MANIFEST = "install.json"
C5_INSTALL_SCHEMA = "gen2-c5-install-v1"
C5_NOT_INSTALLED = (f"{C5_RECEIPTS}: C-5 receipts are NOT installed; run `tools/c5_runner.py run` and then "
                    f"`tools/c5_install_receipts.py` to install them. An absent packet is RED, never a skip.")


def c5_install_errors(root: Path | None = None, head: str | None = None) -> list[str]:
    """Present, current or exactly equivalent code, every file byte as installed."""
    import c5_runner

    root = ROOT if root is None else root
    folder = root / C5_RECEIPTS
    if not folder.is_dir():
        return [C5_NOT_INSTALLED]
    try:
        head = code_digest.head_digest(root) if head is None else head
        equivalent = code_digest.c5_admission_equivalent(root, head)
    except (OSError, subprocess.CalledProcessError) as exc:
        return [f"cannot compute the current production code digest: {exc}"]
    try:
        install = json.loads((folder / C5_INSTALL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{C5_INSTALL_MANIFEST}: unreadable ({exc}); re-run tools/c5_install_receipts.py"]
    errors = []
    if install.get("schema") != C5_INSTALL_SCHEMA:
        errors.append(f"install manifest schema {install.get('schema')!r} is not {C5_INSTALL_SCHEMA}")
    if install.get("code_digest") != head and (equivalent is None or install.get("code_digest") != equivalent):
        errors.append(f"STALE: the installed C-5 packet is at code digest "
                      f"{str(install.get('code_digest'))[:12]}, the current production digest is {head[:12]} "
                      "(re-run tools/c5_runner.py run -- it needs EmuHawk -- then reinstall)")
    files = install.get("files") if isinstance(install.get("files"), dict) else {}
    if not files:
        errors.append("install manifest pins no files")
    for name, sha in sorted(files.items()):
        target = folder / str(name)
        if not target.is_file():
            errors.append(f"installed receipt missing: {name}")
        elif c5_runner.lf_sha256(target) != sha:
            errors.append(f"installed receipt tampered: {name} sha256 {c5_runner.lf_sha256(target)[:12]} "
                          f"!= installed pin {str(sha)[:12]}")
    # the pinned set must EQUAL the directory: an extra file is unjudged bytes the packet never vouched for
    for extra in sorted(p.name for p in folder.iterdir()):
        if extra != C5_INSTALL_MANIFEST and extra not in files:
            errors.append(f"unpinned file in the installed C-5 packet: {extra}")
    return errors


def c5_gate_errors(root: Path | None = None, head: str | None = None) -> list[str]:
    """What release-evidence asks of C-5: the installed packet is sound AND every required cell is a
    PHYSICAL PASS at the current or exactly equivalent digest. Both halves report, so a tampered install is never
    mistaken for a complete one."""
    root = ROOT if root is None else root
    errors = c5_install_errors(root, head)
    folder = root / C5_RECEIPTS
    if not folder.is_dir():
        return errors
    return errors + c5_errors(folder, root, head)


def _c5_main(receipts_dir: str) -> int:
    errors = c5_errors(receipts_dir)
    for error in errors:
        print(f"RED  {error}")
    for gate_id, (date, text) in C5_NAMED_LIMITS.items():
        print(f"NAMED LIMIT  {gate_id}: {text} (owner ruling {date}; {C5_REQUIREMENTS})")
    if errors:
        print(f"C-5: {len(errors)} gap(s); a missing, stale, edited or unproven cell is not a pass")
        return 1
    print(f"C-5: all {len(C5_CELLS)} required cells have verified PHYSICAL receipts")
    return 0


_RECEIPT_CLIS = {
    "--fixtures": ("fixtures", "fixtures_errors"),
    "--live-gates": ("live gates", "live_gates_errors"),
    "--trade-gates": ("trade gates", "trade_gates_errors"),
    "--duo-pairs": ("duo pairs", "duo_pairs_errors"),
    "--release-evidence": ("release evidence", "release_evidence_errors"),
}


def _receipt_cli(flag: str) -> int:
    label, check = _RECEIPT_CLIS[flag]
    errors = globals()[check](**({"artifact_kind": "overlay"} if flag == "--fixtures" else {}))
    for error in errors:
        print(f"RED  {error}")
    prefix = {"--live-gates": "new-gates.", "--trade-gates": "duo.", "--duo-pairs": "duo."}.get(flag)
    if prefix:
        _warn_stale(prefix)
    if errors:
        print(f"{label}: {len(errors)} gap(s); a missing, stale or unproven receipt is not a pass")
        return 1
    print(f"{label}: every required receipt bound and valid")
    return 0


def manifest_errors() -> list[str]:
    """Check declarations, not file availability or evidence qualification."""
    names = [lane.name for lane in LANES]
    errors = []
    if len(names) != len(set(names)):
        errors.append("duplicate lane names")
    if set(names) != _REQUIRED_LANES:
        errors.append("missing or unexpected required lanes")
    if set(REQUIREMENTS) != set(names) or set(PREREQUISITES) != set(names):
        errors.append("lane requirements/prerequisites do not match the manifest")
    for name in names:
        ids = REQUIREMENTS.get(name, [])
        if not ids or len(ids) != len(set(ids)) or set(ids) != _REQUIRED_MAPPINGS.get(name):
            errors.append(f"{name}: missing, duplicate or unexpected requirement mapping")
    if not set(names) >= _SLOW or not set(names) >= set(UNIMPLEMENTED):
        errors.append("unknown slow/unimplemented lane")
    for lane in LANES:
        if not lane.argv and lane.name not in UNIMPLEMENTED:
            errors.append(f"{lane.name}: no executable binding")
        if lane.is_pytest:
            options = lane.argv[lane.argv.index("pytest") + 1:]
            if any(arg in ("-k", "-m") or arg.startswith(("--deselect", "-k="))
                   for arg in options):
                errors.append(f"{lane.name}: pytest deselection is not lane coverage")
    return errors


def run_lane(lane: Lane, quiet: bool) -> tuple[bool, str]:
    """Bind prerequisites and zero skip exemptions; share execution and accounting."""
    if lane.name in UNIMPLEMENTED:
        return False, f"UNIMPLEMENTED: {UNIMPLEMENTED[lane.name]}"
    if lane.name == "live-new-gates":
        gaps = new_gates_errors()
        if gaps:
            return False, "; ".join(gaps)
    if lane.name not in PREREQUISITES:
        return False, "undeclared lane prerequisites"
    paths = list(PREREQUISITES[lane.name])
    if lane.is_pytest:
        paths.extend(arg.split("::", 1)[0] for arg in lane.argv if arg.startswith("tests/"))
    elif len(lane.argv) >= 2:
        paths.append(lane.argv[1])
    missing = [path for path in paths if not (ROOT / path).exists()]
    if missing:
        return False, "missing prerequisites: " + ", ".join(dict.fromkeys(missing))
    try:
        return release_lanes.run_lane(lane, quiet, ALLOWED_SKIPS)
    except OSError as exc:
        return False, f"cannot execute lane: {exc}"


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given == ["--duo-matrix"]:
        return _duo_matrix_main()
    if given == ["--new-gates"]:
        return _new_gates_main()
    if len(given) == 2 and given[0] == "--c5":
        return _c5_main(given[1])
    if len(given) == 1 and given[0] in _RECEIPT_CLIS:
        return _receipt_cli(given[0])
    errors = manifest_errors()
    if errors:
        print("Gen 2 manifest invalid: " + "; ".join(errors), file=sys.stderr)
        return 1
    return release_lanes.run_gate(
        title="Gen 2 release gate", lanes=LANES, requirements=REQUIREMENTS,
        slow=_SLOW, run_lane=run_lane, description=__doc__, argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
