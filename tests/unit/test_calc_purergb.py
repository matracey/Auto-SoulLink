"""calc_species (base.GameRulesAdapter) and the pureRGB damage-calc naming/setdex pipeline.

Three things this file pins:

1. `calc_species` is a pure refactor for every adapter that doesn't override it -- same
   `calc_name("species", species_name(id))` expression `_build_mon_entry` used inline before,
   now reachable by id alone. Checked directly against Gen1Adapter (vanilla) and Gen3Adapter
   (RR) so a future edit can't quietly change either game's behaviour.
2. Gen1PureRGBAdapter's `calc_species` override: every species id the adapter can emit resolves
   to a species key `calc/calc/src/data/purergb.ts` actually has, including the 7 alternate
   forms whose `species_name()` collides with their base species' name (the bug
   docs/calc_multigen/PURERGB_MECHANICS.md §2 documents) -- and every pureRGB move name
   (through `calc_name`) resolves to a purergb.ts move key the same way.
3. tools/gen_purergb_setdex.py's output (calc/src/js/data/sets/games/PureRGB.js) is up to date
   and every species/move name it emits is one purergb.ts actually has.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from server.adapters.gen1_purergb import Gen1PureRGBAdapter  # noqa: E402
from server.adapters.gen1_rby import Gen1Adapter  # noqa: E402
from server.adapters.gen3_frlge import Gen3Adapter  # noqa: E402
from server.server import _build_mon_entry  # noqa: E402

DATA = REPO / "data" / "games" / "gen1_purergb"
PURERGB_TS = REPO / "calc" / "calc" / "src" / "data" / "purergb.ts"
SETDEX_JS = REPO / "calc" / "src" / "js" / "data" / "sets" / "games" / "PureRGB.js"

_TS_KEY = r'^  "((?:[^"\\]|\\.)*)":'


def _ts_table_keys(block_label: str) -> set[str]:
    """Top-level string keys of purergb.ts's `const <block_label>_DATA = {...};` object."""
    text = PURERGB_TS.read_text(encoding="utf-8")
    block = re.search(rf"const {block_label}_DATA.*?\n\}};", text, re.S).group(0)
    return {re.sub(r"\\(.)", r"\1", m) for m in re.findall(_TS_KEY, block, re.M)}


PURERGB_SPECIES_KEYS = _ts_table_keys("SPECIES")
PURERGB_MOVE_KEYS = _ts_table_keys("MOVES")


@pytest.fixture(scope="module")
def purergb() -> Gen1PureRGBAdapter:
    return Gen1PureRGBAdapter(rom_type="PureRed")


# ── 1. calc_species is an identity-preserving refactor for the default (un-overridden) case ──

def test_default_calc_species_matches_the_old_inline_expression_gen1():
    rby = Gen1Adapter(rom_type="red")
    for dex in (1, 25, 151):
        assert rby.calc_species(dex) == rby.calc_name("species", rby.species_name(dex))


def test_default_calc_species_matches_the_old_inline_expression_rr():
    rr = Gen3Adapter(is_rr=True)
    for sid in (1, 4, 7, 150):
        assert rr.calc_species(sid) == rr.calc_name("species", rr.species_name(sid))


def test_gen1purergb_class_does_not_accidentally_shadow_calc_species_for_non_forms():
    """Everything except the 7 forms still goes through the inherited default expression."""
    purergb = Gen1PureRGBAdapter(rom_type="PureRed")
    for sid, entry in purergb._species.items():
        if entry["classification"] != "ordinary":
            continue
        assert purergb.calc_species(sid) == purergb.calc_name(
            "species", purergb.species_name(sid))


# ── 2a. pureRGB species: every id the adapter can emit resolves in purergb.ts ───────────────

def test_purergb_calc_species_resolves_for_every_modelled_species(purergb):
    checked = 0
    for sid, entry in purergb._species.items():
        if entry["classification"] not in ("ordinary", "form", "spirit"):
            continue  # unused/missingno/picture_only: purergb.ts doesn't model these either
        name = purergb.calc_species(sid)
        assert name in PURERGB_SPECIES_KEYS, (sid, entry["name"], name)
        checked += 1
    assert checked == len(PURERGB_SPECIES_KEYS) == 163  # 151 ordinary + 7 forms + 5 spirits


def test_purergb_forms_resolve_to_their_own_disambiguated_name_not_their_base_species(purergb):
    """The bug PURERGB_MECHANICS.md §2 documents: species_name() for a form returns the SAME
    text as its base species (that's the whole trick of an in-game "form"), so calc_species
    must diverge from it for these 7 ids."""
    forms = {
        172: ("Hardened Onix", "Onix"), 52: ("Volcanic Magmar", "Magmar"),
        56: ("Floating Magneton", "Magneton"), 94: ("Winter Dragonair", "Dragonair"),
        146: ("Floating Weezing", "Weezing"), 174: ("Armored Mewtwo", "Mewtwo"),
        175: ("Powered Haunter", "Haunter"),
    }
    for sid, (calc_name, base_display) in forms.items():
        assert purergb.species_name(sid) == base_display  # unchanged: still what the ROM shows
        calc_species = purergb.calc_species(sid)
        assert calc_species == calc_name
        assert calc_species in PURERGB_SPECIES_KEYS


def test_build_mon_entry_sends_the_disambiguated_form_name(purergb):
    entry = _build_mon_entry("k", {"species_id": 172, "level": 50}, purergb)
    assert entry["species_name"] == "Hardened Onix"
    assert purergb.species_name(172) == "Onix"  # species_name() itself is unchanged


# ── 2b. pureRGB moves: every id resolves through calc_name the same way ─────────────────────

def test_purergb_calc_moves_resolve_for_every_move(purergb):
    for mid, row in purergb._moves.items():
        calc_name = purergb.calc_name("move", purergb.move_name(mid))
        assert calc_name in PURERGB_MOVE_KEYS, (mid, row["name"], calc_name)
    assert len(purergb._moves) == len(PURERGB_MOVE_KEYS - {"(No Move)"}) == 165


def test_empty_move_sentinel_matches_the_generator_and_generated_table():
    from tools import gen_purergb_calc_patch as gen

    moves, warnings = gen.build_moves_data(gen.load_json("moves.json")["moves"], gen.load_calc_move_names())
    assert not warnings
    assert moves["(No Move)"] == {"bp": 0, "type": "Normal", "category": "Status"}
    assert set(moves) == PURERGB_MOVE_KEYS
    block = "\n".join(gen.build_table_ts(moves))
    assert gen.MIDDLE_MOVES + block + "\n" + gen.MIDDLE_TYPES in PURERGB_TS.read_text(encoding="utf-8")


def test_generator_retains_bonemerangs_fixed_hit_count():
    from tools import gen_purergb_calc_patch as gen

    moves, _ = gen.build_moves_data(gen.load_json("moves.json")["moves"], gen.load_calc_move_names())
    assert moves["Bonemerang"] == {"bp": 60, "type": "Bonemerang", "category": "Physical", "multihit": 2}


def test_purergb_calc_profile_uses_the_purergb_dex():
    assert Gen1PureRGBAdapter(rom_type="PureRed").calc_profile()["dex"] == "purergb"


# ── 3. tools/gen_purergb_setdex.py output ────────────────────────────────────────────────────

def _need_purergb_source():
    """Absent input skips (tests/TESTING.md); a present checkout at the wrong commit still
    fails inside gen1_foundation.source_root."""
    from tools import gen1_foundation
    f = gen1_foundation.foundation("purergb")
    root = Path(os.environ.get(f["source_env"]) or (REPO / f["source_default"]))
    if not root.is_dir():
        pytest.skip(f"purergb source checkout absent: {root} (set {f['source_env']})")


def test_setdex_generator_check_passes():
    _need_purergb_source()
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "gen_purergb_setdex.py"), "--check"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _load_vendored(path: Path) -> dict:
    """Same parse tests/unit/test_calc_trainer_sets.py uses for the sibling *.js setdex files."""
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("//")]
    body = "\n".join(lines).strip()
    assert body.startswith("var "), path
    body = body[body.index("=") + 1:].strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)


def test_setdex_species_and_moves_all_exist_in_purergb_ts():
    data = _load_vendored(SETDEX_JS)
    assert data  # non-empty
    bad_species = sorted(sp for sp in data if sp not in PURERGB_SPECIES_KEYS)
    assert not bad_species, bad_species

    bad_moves = set()
    for sets in data.values():
        for entry in sets.values():
            assert len(entry["moves"]) == 4
            for mv in entry["moves"]:
                if mv != "No Move" and mv not in PURERGB_MOVE_KEYS:
                    bad_moves.add(mv)
    assert not bad_moves


def test_setdex_missingno_slot_is_the_only_species_gap():
    """Documented in tools/gen_purergb_setdex.py's module docstring: GYM_GUIDE's MISSINGNO
    party slot is the only species trainers.json references that purergb.ts can't model (that
    classification is dropped outright), so it should be the only skipped mon."""
    _need_purergb_source()
    sys.path.insert(0, str(REPO / "tools"))
    from gen_purergb_setdex import generate  # noqa: E402

    _text, warnings = generate()
    assert len(warnings) == 2
    assert all("MISSINGNO" in w for w in warnings)
