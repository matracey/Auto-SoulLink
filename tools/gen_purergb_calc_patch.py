"""Generate calc/calc/src/data/purergb.ts: the pureRGB type/species/move patch for the damage
calc's Gen 1 tables, consumed at runtime by ``useDex('purergb')`` (calc/calc/src/data/purergb.ts
itself carries the runtime swap logic too - this tool overwrites the whole file, generated code
and runtime logic together, so the file has exactly one source of truth).

Inputs (never hand-typed, per data/games/gen1_purergb/README.md):
  - data/games/gen1_purergb/types.json    (type_names, species_types)
  - data/games/gen1_purergb/species_index.json (per-species types/stats/classification)
  - data/games/gen1_purergb/moves.json    (per-move type/power/split)
  - data/games/gen1_rby/calc_names.json   (ROM display name -> damage-calc Gen 1 name; the same
      table server/adapters/gen1_rby.py's Gen1Adapter.calc_name() applies, inherited unchanged by
      Gen1PureRGBAdapter - see server/adapters/gen1_purergb.py's move_name()/species_name())
  - the pinned purergb-src checkout (via tools/gen1_foundation.py), for:
      data/types/type_matchups.asm      - full type effectiveness table
      data/pokemon/names.asm            - MonsterNames, in-game display name per internal index
      constants/pokemon_constants.asm   - internal species constant name -> internal index (hex)

See docs/calc_multigen/PURERGB_MECHANICS.md for the naming policy and mechanics this does (and
does not) model.

Usage:
    python tools/gen_purergb_calc_patch.py           # write calc/calc/src/data/purergb.ts
    python tools/gen_purergb_calc_patch.py --check    # exit 1 if the file would change
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import gen1_foundation as gf  # noqa: E402

DATA_DIR = REPO / "data" / "games" / "gen1_purergb"
CALC_NAMES_PATH = REPO / "data" / "games" / "gen1_rby" / "calc_names.json"
OUT_PATH = REPO / "calc" / "calc" / "src" / "data" / "purergb.ts"

# Species classifications worth exposing to the damage calc: ordinary dex mons, alternate forms,
# and spirits (battle-only bosses; not catchable, but a player can still face one). "unused",
# "missingno" and "picture_only" slots are dead ROM space / glitch-only, not worth modelling.
INCLUDED_CLASSIFICATIONS = {"ordinary", "form", "spirit"}

# A handful of custom pureRGB moves whose in-game effect includes an HP drain the calc's generic
# `move.drain` field already knows how to render (calc/calc/src/desc.ts) - moves.json carries no
# `drain` field at all (see docs/calc_multigen/PURERGB_MECHANICS.md §2), so this is hand-pinned
# against the source: engine/battle/move_effects/siphon_snag.asm (`_SiphonSnagEffect`) drains the
# target for half the damage dealt back to the user, exactly like vanilla Absorb/Mega Drain/Dream
# Eater/Leech Life (all `drain: [1, 2]` in calc/calc/src/data/moves.ts's RBY table).
MOVE_DRAIN: dict[str, tuple[int, int]] = {
    "Siphon Snag": (1, 2),
}


# Same [a-z0-9]-only, lowercased id scheme as calc/calc/src/util.ts toID().
def to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


# A handful of ROM MonsterNames spellings the calc renders differently. Everything else is a
# plain Title Case of the all-caps ROM text (see build_ordinary_names below).
ORDINARY_NAME_FIXUPS = {
    "NIDORAN♂": "Nidoran-M",
    "NIDORAN♀": "Nidoran-F",
    "MR.MIME": "Mr. Mime",
    "FARFETCH'D": "Farfetch’d",
}


def title_case_word(word: str) -> str:
    return word[:1].upper() + word[1:].lower() if word else word


def load_calc_move_names() -> dict[str, str]:
    """ROM move display name -> damage-calc Gen 1 name (data/games/gen1_rby/calc_names.json's
    "move" table). Applied by server/adapters/gen1_rby.py's Gen1Adapter.calc_name("move", ...),
    which server/adapters/gen1_purergb.py's Gen1PureRGBAdapter inherits unchanged (it never
    overrides calc_name) - so this is the exact table the server applies to a pureRGB move name
    before it ever reaches the frontend/calc bridge. purergb.ts must use these same calc names (not
    moves.json's raw ROM spelling) for every move this table renames, or the calc's move lookup
    (keyed by toID(name)) silently misses and falls back to vanilla Gen 1 data for that move -
    see docs/calc_multigen/PURERGB_MECHANICS.md's naming-policy section for the worked example
    (Vicegrip vs Vise Grip) and the more serious Night Shade/Sonic Boom collision it uncovered."""
    data = json.loads(CALC_NAMES_PATH.read_text(encoding="utf-8"))
    return dict(data.get("move", {}))


def build_ordinary_names(purergb_root: Path) -> list[str]:
    """MonsterNames, 1-indexed by internal species index (index 0 = NO_MON, unused here)."""
    text = (purergb_root / "data" / "pokemon" / "names.asm").read_text(encoding="utf-8")
    names = re.findall(r'dname\s+"([^"]*)"', text)
    out = ["" for _ in range(len(names) + 1)]
    for i, raw in enumerate(names, start=1):
        if raw in ORDINARY_NAME_FIXUPS:
            out[i] = ORDINARY_NAME_FIXUPS[raw]
        else:
            out[i] = " ".join(title_case_word(w) for w in raw.split(" "))
    return out


def build_form_names(purergb_root: Path) -> dict[int, str]:
    """internal index (from the '; $XX' hex comment) -> Title Case of the symbolic constant name,
    for the handful of alternate-form species that reuse their base species' MonsterNames text
    in-game (e.g. internal index 172 displays as "ONIX" just like index 34, so the ROM's own
    display name can't disambiguate them - constants/pokemon_constants.asm's compile-time symbol
    can: HARDENED_ONIX, VOLCANIC_MAGMAR, etc).

    NOTE: server/adapters/gen1_purergb.py's species_name() does NOT do this - for a "form" species
    it returns `_display_case(species_index.json's own "name" field)`, which (verified directly:
    species_index.json's entry 172 has "name": "ONIX") is the SAME text as the base species, not
    this disambiguated one. The calc needs a unique name per species (it's both the dict key and
    the toID() lookup key), so this generator keeps the disambiguated pokemon_constants.asm name
    regardless - see docs/calc_multigen/PURERGB_MECHANICS.md's naming-policy section: the
    coordinator needs a pureRGB-only calc_names entry for each of these 7 forms once the server
    side sends the literal (colliding) adapter name."""
    text = (purergb_root / "constants" / "pokemon_constants.asm").read_text(encoding="utf-8")
    out: dict[int, str] = {}
    for m in re.finditer(r"const\s+([A-Z0-9_]+)\s*;\s*\$([0-9A-Fa-f]+)", text):
        name, hexval = m.group(1), m.group(2)
        out[int(hexval, 16)] = " ".join(title_case_word(w) for w in name.split("_"))
    return out


def build_type_chart(purergb_root: Path, type_names: dict[str, str]) -> dict[str, dict[str, float]]:
    """Full type x type matrix (default 1, EFFECTIVE) with type_matchups.asm's overrides applied,
    the same "sparse diff over an implicit 1x" convention pret's TypeEffects table (and the calc's
    own hand-written RBY/GSC charts in data/types.ts) both use."""
    text = (purergb_root / "data" / "types" / "type_matchups.asm").read_text(encoding="utf-8")
    # constants/type_constants.asm spells the Psychic type PSYCHIC_TYPE and Bonemerang
    # BONEMERANG_TYPE to dodge asm keyword/label clashes; map those back to the calc's TypeName.
    const_to_name = {"PSYCHIC_TYPE": "Psychic", "BONEMERANG_TYPE": "Bonemerang"}
    for v in type_names.values():
        const_to_name.setdefault(v.upper(), v)
    mult = {"SUPER_EFFECTIVE": 2, "NOT_VERY_EFFECTIVE": 0.5, "NO_EFFECT": 0, "EFFECTIVE": 1}

    all_types = list(type_names.values()) + ["???"]
    chart = {a: dict.fromkeys(all_types, 1) for a in all_types}

    row_re = re.compile(r"db\s+([A-Z0-9_]+),\s*([A-Z0-9_]+),\s*([A-Z_]+)")
    for line in text.splitlines():
        line = line.split(";", 1)[0]  # strip end-of-line comments before matching
        m = row_re.search(line)
        if not m:
            continue
        atk_const, def_const, mult_const = m.groups()
        if atk_const not in const_to_name or def_const not in const_to_name:
            continue  # the "db -1 ; end" sentinel line
        chart[const_to_name[atk_const]][const_to_name[def_const]] = mult[mult_const]
    return chart


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def ts_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def build_species_data(species_index: dict, ordinary_names: list[str], form_names: dict[int, str],
                        type_names: dict[str, str]) -> tuple[dict, list[str]]:
    """{display name: {types, bs, weightkg}} - the same SpeciesData shape (calc/calc/src/data/
    species.ts) every other gen's hand-written table uses, keyed by name like RBY/GSC/etc. are."""
    out: dict[str, dict] = {}
    warnings: list[str] = []
    for idx_str in sorted(species_index["species"], key=int):
        entry = species_index["species"][idx_str]
        classification = entry["classification"]
        if classification not in INCLUDED_CLASSIFICATIONS:
            continue
        idx = int(idx_str)
        if classification == "ordinary":
            name = ordinary_names[idx]
        elif classification == "spirit":
            name = " ".join(title_case_word(w) for w in entry["name"].split(" "))
        else:  # form
            name = form_names.get(idx)
            if not name:
                warnings.append(f"internal index {idx} ({entry['name']}) form has no "
                                 "pokemon_constants.asm match; skipped")
                continue
        if name in out:
            warnings.append(f"duplicate species name {name!r} (internal index {idx}); skipped")
            continue

        t1, t2 = entry["types"]
        types = [type_names[str(t1)]] if t1 == t2 else [type_names[str(t1)], type_names[str(t2)]]
        stats = entry["stats"]
        # Gen 1 has one Special stat; SpeciesData.bs's sa/sd default to bs.sl (species.ts's Specie
        # constructor: `gen >= 2 ? data.bs.sa : data.bs.sl`), so gen 1 only ever needs `sl` - unlike
        # the old prototype, which built a calc-internal Specie object directly and had to fill in
        # both spa/spd by hand.
        bs = {"hp": stats["hp"], "at": stats["atk"], "df": stats["def"], "sl": stats["spc"],
              "sp": stats["spd"]}
        # weightkg is unused by calculateRBYGSC (mechanics/gen12.ts) - see PURERGB_MECHANICS.md
        out[name] = {"types": types, "bs": bs, "weightkg": 0}
    return out, warnings


def build_moves_data(moves: list[dict], calc_names: dict[str, str]) -> tuple[dict, list[str]]:
    """{display name: {bp, type, category, drain?}} - the same MoveData shape every other gen's
    hand-written table uses. `name` is renamed through calc_names first (see load_calc_move_names)
    so the id the calc looks moves up by (toID(name)) matches what the server actually sends."""
    out: dict[str, dict] = {"(No Move)": {"bp": 0, "type": "Normal", "category": "Status"}}
    warnings: list[str] = []
    for m in moves:
        name = calc_names.get(m["name"], m["name"])
        if name in out:
            warnings.append(f"duplicate move name {name!r} (id {m['id']}); skipped")
            continue
        data: dict = {"bp": m["power"], "type": m["type"], "category": m["split"]}
        if name in MOVE_DRAIN:
            data["drain"] = list(MOVE_DRAIN[name])
        if name == "Bonemerang":
            data["multihit"] = 2
        out[name] = data
    return out, warnings


def ts_value(v) -> str:
    """A Python value (from build_species_data/build_moves_data/build_type_chart) as a TS object
    literal - just enough of a serializer for the plain dict/list/str/int/float shapes those
    produce (no need for a general-purpose one)."""
    if isinstance(v, str):
        return ts_str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(ts_value(x) for x in v) + "]"
    if isinstance(v, dict):
        # Always quote keys (never bare identifiers) - needed for "???", not just cosmetic: an
        # unquoted `???:` is a syntax error.
        return "{" + ", ".join(f"{ts_str(k)}: {ts_value(val)}" for k, val in v.items()) + "}"
    raise TypeError(f"unhandled value {v!r}")


def build_table_ts(table: dict[str, dict]) -> list[str]:
    return [f"  {ts_str(name)}: {ts_value(data)}," for name, data in table.items()]


HEADER = """\
// AUTO-GENERATED by tools/gen_purergb_calc_patch.py --check. Do not hand-edit.
// Source: data/games/gen1_purergb/{types,species_index,moves}.json, data/games/gen1_rby/
// calc_names.json, plus the pureRGB source checkout pinned at data/purergb_sources.lock.json
// (v2.7.6, commit 7e7a4653...). Mechanics and naming policy: docs/calc_multigen/
// PURERGB_MECHANICS.md.
//
// Swaps calc/calc/src/data/{species,moves,types}.ts's Gen 1 slot for pureRGB's own species,
// moves and type chart at runtime, and back again. Built as plain SpeciesData/MoveData/TypeChart
// tables (the same shapes species.ts/moves.ts/types.ts's own hand-written RBY/GSC/... tables use,
// keyed by display name) so they go through the exact same Specie/Move/Type construction path as
// every other gen - no separate object-literal-plus-cast path to keep in sync.
import {MoveData, setGen1Moves} from './moves';
import {SpeciesData, setGen1Species} from './species';
import {TypeChart, setGen1TypeChart} from './types';

const SPECIES_DATA: {[name: string]: SpeciesData} = {
"""

MIDDLE_MOVES = """\
};
export const PURERGB_SPECIES = SPECIES_DATA;

const MOVES_DATA: {[name: string]: MoveData} = {
"""

MIDDLE_TYPES = """\
};
export const PURERGB_MOVES = MOVES_DATA;

export const PURERGB_TYPE_CHART: TypeChart = {
"""

FOOTER = """\
};

/**
 * Swap the damage calc's Gen 1 species/moves/type chart between vanilla RBY and pureRGB. Affects
 * every Generation(1) instance (there's no per-instance opt-out) and every other generation is
 * untouched. Call once before a pureRGB Gen 1 calculation; call useDex('vanilla') to restore.
 *
 * Whether pureRGB is active is read back from the data itself (mechanics/util.ts's isPureRGB()
 * checks gen.types for pureRGB's Crystal type) rather than a flag here, so mechanics/ - compiled
 * into a separate bundle with no shared module scope - never needs to import this module.
 */
export function useDex(target: 'vanilla' | 'purergb'): void {
  const active = target === 'purergb';
  setGen1Species(active ? PURERGB_SPECIES : null);
  setGen1Moves(active ? PURERGB_MOVES : null);
  setGen1TypeChart(active ? PURERGB_TYPE_CHART : null);
}
"""


def generate() -> str:
    types_json = load_json("types.json")
    species_index = load_json("species_index.json")
    moves_json = load_json("moves.json")
    type_names = types_json["type_names"]
    calc_move_names = load_calc_move_names()

    purergb_root = gf.source_root("purergb")
    ordinary_names = build_ordinary_names(purergb_root)
    form_names = build_form_names(purergb_root)
    type_chart = build_type_chart(purergb_root, type_names)

    species_data, warnings = build_species_data(species_index, ordinary_names, form_names, type_names)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    moves_data, warnings = build_moves_data(moves_json["moves"], calc_move_names)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    parts = [
        HEADER,
        "\n".join(build_table_ts(species_data)), "\n",
        MIDDLE_MOVES,
        "\n".join(build_table_ts(moves_data)), "\n",
        MIDDLE_TYPES,
        "\n".join(build_table_ts(type_chart)), "\n",
        FOOTER,
    ]
    text = "".join(parts)
    return text.replace("\n", "\r\n")  # match the CRLF line endings of the other data/*.ts files


def _resolve_purergb_src_env() -> None:
    """Point SLINK_PURERGB_SRC at the pureRGB source checkout even when this tool runs from a git
    worktree. tools/gen1_foundation.py's REPO is `Path(__file__).resolve().parent.parent` - that
    file's OWN location, which in a worktree checkout is the worktree's copy of tools/, not the
    main checkout - so its default source path (`<REPO>/.cache/purergb`) resolves to a directory
    that only exists in the main checkout. `git rev-parse --git-common-dir` always names the main
    repo's .git dir regardless of which worktree runs it, so it finds the main checkout root from
    anywhere. Never overrides an SLINK_PURERGB_SRC the caller already set.
    """
    if os.environ.get("SLINK_PURERGB_SRC"):
        return
    try:
        common_dir = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    main_root = (REPO / common_dir).resolve().parent
    for candidate in (main_root / ".cache" / "purergb", main_root / ".cache" / "purergb-src"):
        if candidate.is_dir():
            os.environ["SLINK_PURERGB_SRC"] = str(candidate)
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if the output would change")
    args = ap.parse_args()

    _resolve_purergb_src_env()
    generated = generate()
    if args.check:
        current = None
        if OUT_PATH.is_file():
            with open(OUT_PATH, encoding="utf-8", newline="") as f:
                current = f.read()
        if current != generated:
            print(f"{OUT_PATH} is stale; run tools/gen_purergb_calc_patch.py", file=sys.stderr)
            return 1
        print("purergb.ts is up to date")
        return 0

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(generated)
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
