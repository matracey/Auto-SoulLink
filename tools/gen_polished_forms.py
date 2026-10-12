"""tools/gen_polished_forms.py — (species, form) -> BaseData record index,
Polished Crystal 3.2.3. Implements FORMS.md H2.

Derived from SOURCE, proved against the ROM:

  * `data/pokemon/base_stats.asm`'s INCLUDE order numbers the records 1..N. Records
    1..NUM_SPECIES are the species; the tail after NUM_SPECIES are non-cosmetic
    variant forms.
  * `constants/pokemon_constants.asm` gives the species constants and the form
    constants. Regional forms are plain DEFs (ALOLAN_FORM 2, GALARIAN_FORM 3,
    HISUIAN_FORM 4, PALDEAN_FORM 5); species-specific forms are `ext_const`s
    numbered from FIRST_COSMETIC_FORM_MON upwards, which is what makes them cosmetic.

Every emitted record is checked against the ROM: BaseData's byte 0 must equal the
record's species low byte. A mismatch aborts generation rather than shipping.

Usage:
    python tools/gen_polished_forms.py            # writes the index
    python tools/gen_polished_forms.py --check    # exit 1 if the committed index is stale
    python tools/gen_polished_forms.py --src DIR --roms DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

from slink_space import work_root

REPO = pathlib.Path(__file__).resolve().parent.parent
PACK = REPO / "data" / "games" / "polished_crystal"
OUT = PACK / "forms_index.json"
LOCK = REPO / "data" / "polished_sources.lock.json"
DEFAULT_SRC = work_root("cache") / "polished" / "src"
DEFAULT_ROMS = work_root("cache") / "polished" / "release"
BASE_STATS_STRIDE = 34  # constants/pokemon_data_constants.asm:35 BASE_DATA_SIZE

INCLUDE_RE = re.compile(r'^INCLUDE "data/pokemon/base_stats/([a-z0-9_]+)\.asm"')
SPECIES_RE = re.compile(r"^\s*const ([A-Z_0-9]+)\s*(?:;.*)?$")
REGIONAL_RE = re.compile(r"^DEF ([A-Z_]+_FORM) EQU (\d+)$")
FIRST_COSMETIC_RE = re.compile(r"^DEF FIRST_COSMETIC_FORM_MON EQU const_value")
EXT_CONST_DEF_RE = re.compile(r"^\s*ext_const_def\s+([^,;]+?)\s*(?:,\s*([A-Z_0-9]+))?\s*(?:;.*)?$")
DEF_EQU_RE = re.compile(r"^DEF ([A-Z_0-9]+) EQU (\d+)")
EXT_CONST_RE = re.compile(r"^\s*ext_const\s+([A-Z_0-9]+)")
NUM_SPECIES_RE = re.compile(r"^DEF NUM_SPECIES EQU const_value")


EVOS_START_RE = re.compile(r"^\s*evos_attacks ([A-Z][A-Za-z_0-9]*)\s*$")
EVO_DATA_RE = re.compile(r"^\s*evo_data ([A-Z_0-9]+)(?:, ([A-Z_0-9]+))?(?:, ([A-Z_0-9]+)(?:, ([A-Z_0-9]+))?)?\s*(?:;.*)?$")


def label_key(label: str) -> str:
    """`RattataAlolan` and the base_stats stem `rattata_alolan` name the same record."""
    return re.sub(r"[^a-z0-9]", "", label.lower())


def read_evolutions(src: pathlib.Path, forms: dict) -> dict[str, list[tuple[str, str]]]:
    """EvosAttacks label -> [(target species CONSTANT, target form CONSTANT or "")].

    `data/pokemon/evos_attacks.asm` defines every record with `evos_attacks <Label>`
    followed by its `evo_data` lines, e.g. `evo_data EVOLVE_LEVEL, 20, RATICATE, ALOLAN_FORM`
    (evos_attacks.asm:377-378). The next `evos_attacks` line closes the record.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None
    for line in (src / "data" / "pokemon" / "evos_attacks.asm").read_text(encoding="utf-8").splitlines():
        m = EVOS_START_RE.match(line)
        if m:
            current = label_key(m.group(1))
            out.setdefault(current, [])
            continue
        if current is None:
            continue
        d = EVO_DATA_RE.match(line)
        if not d:
            continue
        tokens = [t.strip() for t in line.split(";")[0].split(",")]
        # `evo_data METHOD [, PARAM,] TARGET[, FORM]`. The trailing token is a FORM only when
        # it is a known form constant: EVOLVE_HOLDING carries an extra PARAM instead
        # (evo_data.asm `evo_data EVOLVE_HOLDING, RAZOR_CLAW, TR_MORNDAY, SNEASLER`), so the
        # token count alone cannot decide it.
        last = tokens[-1]
        as_form = last if (last in forms or f"{last}_FORM" in forms) else ""
        if as_form and len(tokens) >= 4:
            target, target_form = tokens[-2], as_form
        else:
            target, target_form = last, ""
        out[current].append((target, target_form))
    return out


def _check_evolutions_in_rom(rom: bytes, sym: dict, index: int, edges: list, species: dict, forms: dict) -> None:
    """Prove the source's evolution targets against the ROM: the record's EvosAttacks block (up to its $FF
    terminator) must contain each target's `dp` pair (LOW(species), HIGH(species) << 5 | form)."""
    bank, addr = sym["EvosAttacksPointers"]
    table = bank * 0x4000 + addr - 0x4000
    ptr = rom[table + (index - 1) * 2] | rom[table + (index - 1) * 2 + 1] << 8
    off = bank * 0x4000 + ptr - 0x4000
    block = bytes(rom[off:off + 40])
    block = block[:block.index(0xFF)] if 0xFF in block else block
    for tc, fc in edges:
        sid = species[tc]
        if fc and fc not in ("PLAIN", "PLAIN_FORM", "NO_FORM"):
            wanted = [forms[fc if fc in forms else f"{fc}_FORM"]]
        else:  # a plain target is stored with form 0 or PLAIN_FORM (1): `evo_data` appends PLAIN_FORM by default
            wanted = [0, forms.get("PLAIN_FORM", 1)]
        pairs = [bytes([sid & 0xFF, ((sid >> 8) << 5) | form]) for form in wanted]
        if not any(pair in block for pair in pairs):
            raise SystemExit(f"record {index}: ROM evolution block {block.hex()} lacks target {tc}/{fc} ({[x.hex() for x in pairs]})")


def effective_id(species_const: str, form_const: str, species: dict, forms: dict,
                 by_pair: dict) -> int | None:
    """A variant target resolves to its record index (292..337); a plain one to its species id."""
    sid = species.get(species_const)
    if sid is None:
        return None
    form = 0
    if form_const and form_const not in ("PLAIN", "PLAIN_FORM", "NO_FORM"):  # PLAIN_FORM names the plain species
        name = form_const if form_const in forms else f"{form_const}_FORM"
        if name not in forms:
            return None
        form = forms[name]
    if form:
        return by_pair.get((sid, form))
    return sid


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_species(src: pathlib.Path) -> dict[str, int]:
    """CONST -> species id, from the generated pack (ids skip 255 Egg and 256; a consecutive recount is wrong)."""
    pack = json.loads((REPO / "data" / "games" / "polished_crystal" / "species_index.json").read_text(encoding="utf-8"))
    return {row["const"]: int(k) for k, row in pack["species"].items()}


def eval_const(expr: str, consts: dict[str, int]) -> int:
    """`7`, `NUM_X` or `NUM_X + 1` from a macro argument; anything else aborts rather than guessing."""
    total = 0
    for part in expr.split("+"):
        part = part.strip()
        if part.isdigit():
            total += int(part)
        elif part in consts:
            total += consts[part]
        else:
            raise SystemExit(f"cannot evaluate ext_const_def argument {expr!r} (unknown {part!r})")
    return total


def read_forms(src: pathlib.Path) -> dict[str, int]:
    """NAME -> form number (the 5 FORM_MASK bits). Regional forms are plain DEFs (ALOLAN_FORM EQU 2 ...); species
    forms are ext_consts numbered per species group: `ext_const_def <first>, NAME` restarts the count and each
    following `ext_const NAME` adds one (constants/pokemon_constants.asm:327-343)."""
    text = (src / "constants" / "pokemon_constants.asm").read_text(encoding="utf-8")
    out: dict[str, int] = {}
    for line in text.splitlines():
        m = REGIONAL_RE.match(line)
        if m:
            out[m.group(1)] = int(m.group(2))
    consts = {m.group(1): int(m.group(2)) for m in (DEF_EQU_RE.match(line) for line in text.splitlines()) if m}
    in_ext_block = False
    value = 0
    for line in text.splitlines():
        if FIRST_COSMETIC_RE.match(line):
            in_ext_block = True
            continue
        if not in_ext_block:
            continue
        c = re.match(r"^DEF ([A-Z_0-9]+) EQU ext_const_value - 1", line)
        if c:  # e.g. DEF NUM_MAGIKARP EQU ext_const_value - 1 (the count so far)
            consts[c.group(1)] = value - 1
            continue
        d = EXT_CONST_DEF_RE.match(line)
        if d:  # `ext_const_def n[, NAME]`: the count restarts at n; with a NAME, NAME EQU n and the count goes on from n + 1
            start = eval_const(d.group(1), consts)
            if d.group(2):
                out.setdefault(d.group(2), start)
                start += 1
            value = start
            continue
        m = EXT_CONST_RE.match(line)
        if m:  # `ext_const NAME`: NAME EQU the current value, then +1 (pokemon_constants.asm:339-343)
            out.setdefault(m.group(1), value)
            value += 1
    return out


def read_records(src: pathlib.Path) -> list[tuple[str, str]]:
    """Record index 1..N -> (base_stats file stem, trailing comment), in INCLUDE order."""
    text = (src / "data" / "pokemon" / "base_stats.asm").read_text(encoding="utf-8")
    rx = re.compile(r'^\s*INCLUDE "data/pokemon/base_stats/([a-z0-9_]+)\.asm"\s*(?:;\s*(.*))?$')
    rows = [(m.group(1), (m.group(2) or "").strip()) for m in (rx.match(line) for line in text.splitlines()) if m]
    if len(rows) < 300:
        raise SystemExit(f"only {len(rows)} base_stats includes parsed")
    return rows


def split_stem(stem: str, species: dict[str, int]) -> tuple[str, int, str]:
    """`rattata_alolan` -> ('RATTATA', 19, 'ALOLAN'). Longest species prefix wins."""
    parts = stem.upper().split("_")
    for split in range(len(parts), 0, -1):
        name = "_".join(parts[:split])
        if name in species:
            return name, species[name], "_".join(parts[split:])
    return stem.upper(), 0, ""


def read_stats(path: pathlib.Path) -> tuple[int, ...]:
    """The first six-number `db` line outside an `if DEF(FAITHFUL)` branch: hp, atk, def, spe, sat, sdf."""
    faithful = None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.split(";")[0].strip()
        if s.startswith("if DEF(FAITHFUL)"):
            faithful = True
        elif s == "else" and faithful is not None:
            faithful = False
        elif s == "endc":
            faithful = None
        elif s.startswith("db ") and not faithful:
            nums = [x.strip() for x in s[3:].split(",")]
            if len(nums) == 6 and all(x.isdigit() for x in nums):
                return tuple(int(x) for x in nums)
    raise SystemExit(f"{path.name}: no stats line found")


def read_ability_species(path: pathlib.Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*abilities_for\s+([A-Z0-9_]+)\s*,", line)
        if m:
            return m.group(1)
    raise SystemExit(f"{path.name}: no abilities_for line")


def build_index(src: pathlib.Path = DEFAULT_SRC, roms: pathlib.Path = DEFAULT_ROMS) -> dict:
    species = read_species(src)
    forms = read_forms(src)
    evolutions = read_evolutions(src, forms)
    stems = read_records(src)
    num_species = len(species)

    rom = (roms / "polishedcrystal-3.2.3.gbc").read_bytes()
    sym: dict[str, tuple[int, int]] = {}
    for line in (REPO / "data" / "polished" / "polishedcrystal.sym").read_text(
            encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^([0-9a-f]{2}):([0-9a-f]{4}) (\S+)$", line)
        if m and m.group(3) not in sym:
            sym[m.group(3)] = (int(m.group(1), 16), int(m.group(2), 16))
    bank, addr = sym["BaseData"]
    base = addr if bank == 0 else bank * 0x4000 + addr - 0x4000

    by_pair = {}
    for _i, _entry in enumerate(stems, start=1):
        _stem = _entry[0] if isinstance(_entry, tuple) else _entry
        _n, _sid, _sfx = split_stem(_stem, species)
        if not _sfx or _sfx == "PLAIN":
            continue
        _fc = next((c for c in (f"{_stem.upper()}_FORM", _stem.upper(),
                                f"{_sfx}_FORM", _sfx) if c in forms), "")
        if _fc:
            by_pair[(_sid, forms[_fc])] = _i

    variant_records = []
    last_species = max(species.values())  # records 1..last_species are species slots; the tail is the variant forms
    for index, (stem, comment) in enumerate(stems, start=1):
        if index <= last_species:
            continue
        name, sid, suffix = split_stem(stem, species)
        if suffix:
            form_const = next((c for c in (f"{stem.upper()}_FORM", f"{suffix}_FORM") if c in forms), "")
        else:  # same file as the base species, told apart by its include comment ("; red", "; three segment")
            form_const = f"{name}_{comment.upper().replace(' ', '_')}_FORM"
        if form_const not in forms:
            raise SystemExit(f"record {index} ({stem}; {comment}): unknown form constant {form_const!r}")
        off = base + (index - 1) * BASE_STATS_STRIDE
        if off + BASE_STATS_STRIDE > len(rom):
            raise SystemExit(f"record {index} runs past the ROM")
        t1, t2 = rom[off + 6], rom[off + 7]
        # Polished's BaseData record has NO species byte: it starts with hp/atk/def/spe/sat/sdf
        # (the non-FAITHFUL values, the standard build). Prove the record is this form's by its stats and by the
        # `abilities_for <SPECIES>` line naming the expected species.
        want = read_stats(src / "data" / "pokemon" / "base_stats" / f"{stem}.asm")
        got = tuple(rom[off:off + 6])
        if got != want:
            raise SystemExit(f"record {index} ({stem}): ROM stats {got} != source stats {want}")
        owner = read_ability_species(src / "data" / "pokemon" / "base_stats" / f"{stem}.asm")
        if owner not in (name, stem.upper()):
            raise SystemExit(f"record {index} ({stem}): abilities_for names {owner}, expected {name}")
        variant_records.append({
            "record_index": index, "species_id": sid, "species_constant": name,
            "form_id": forms[form_const], "form_constant": form_const, "kind": "variant",
            "base_stats_file": f"data/pokemon/base_stats/{stem}.asm",
            "rom_offset": off, "types": [t1, t2],
            "evolves_to": [
                {"species_id": t, "effective_species_id": t}
                for t in (
                    effective_id(tc, fc, species, forms, by_pair)
                    for tc, fc in evolutions.get(
                        label_key("".join(w.capitalize() for w in stem.split("_"))), [])
                )
                if t is not None
            ],
        })
        want_edges = len(evolutions.get(label_key("".join(w.capitalize() for w in stem.split("_"))), []))
        if len(variant_records[-1]["evolves_to"]) != want_edges:
            raise SystemExit(f"record {index} ({stem}): an evolution target did not resolve to an effective id")
        _check_evolutions_in_rom(rom, sym, index, evolutions.get(
            label_key("".join(w.capitalize() for w in stem.split("_"))), []), species, forms)

    if len(variant_records) != 46:
        raise SystemExit(f"expected 46 variant records, derived {len(variant_records)}")
    indices = {r["record_index"] for r in variant_records}
    if indices != set(range(min(indices), max(indices) + 1)):
        raise SystemExit("variant record indices are not contiguous")

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    return {
        "schema": "polished-forms-index-v1", "generator": "tools/gen_polished_forms.py",
        "source": {"url": lock["source"]["url"], "tag": lock["source"]["tag"],
                   "commit": lock["source"]["commit"]},
        "source_sha256": {
            "base_stats.asm": sha256_of(src / "data" / "pokemon" / "base_stats.asm"),
            "pokemon_constants.asm": sha256_of(src / "constants" / "pokemon_constants.asm"),
        },
        "rom": {"sha1": lock["outputs"]["polishedcrystal"]["sha1"],
                "BaseData": {"bank": bank, "addr": addr, "flat": base},
                "base_stats_stride": BASE_STATS_STRIDE},
        "counts": {"records": len(stems), "species": num_species,
                   "variant_records": len(variant_records)},
        "variant_forms": variant_records,
    }


def dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--roms", default=str(DEFAULT_ROMS))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = dump(build_index(pathlib.Path(args.src), pathlib.Path(args.roms)))
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is stale; rerun tools/gen_polished_forms.py", file=sys.stderr)
            return 1
        print(f"{OUT} is current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
