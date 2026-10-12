#!/usr/bin/env python3
"""Resolve Polished Crystal's givepoke / loadwildmon script sites against the release ROM.

data/games/polished_crystal/upr_polished_entries.ini lists 63 script sites that are all `Unresolved` because a
script position is not in a .sym: only the enclosing script LABEL is. This generator closes that
gap by reading the release ROM and decoding each macro expansion in place.

The two expansions are

    givepoke    <species>, <form>[, level[, item[, ball[, move]]]]   ->  db $2F
                                                                       dp species, form (2 B)
                                                                       db level, item, ball, move
    loadwildmon <species>, <form>, level  |  <species>, level        ->  db $5C
                                                                       dp species, form (2 B)
                                                                       db level[, ...]

`dp` emits `db LOW(species)` then `db HIGH(species) << MON_EXTSPECIES_F | form`
(macros/scripts/events.asm:319-342, :619-629; MON_EXTSPECIES_F == 5). So for an expansion at `E`:
species low byte is `E + 1`, form byte is `E + 2`, level is `E + 3`.

The opcode values are NOT written in the source -- `const givepoke_command` and
`const loadwildmon_command` carry no literal (macros/scripts/events.asm:318, :619). They were
derived by scanning a known site for the expected (species, form) pair and are recorded in
OPCODES; the test file re-proves both against the ROM independently.

A site resolves only when the number of matching expansions in its window equals the number of ini
sites asking for the same (label, kind, species, form). Otherwise it stays Unresolved with a
reason. Nothing is guessed, and an unresolved site's `offset` is null -- the label offset is carried
separately as `label_offset` so it can never be mistaken for a resolved position.

--check re-renders the JSON and exits 1 on drift (the convention of tools/gen_upr_polished_ini.py).

Inputs default to $SLINK_WORK_ROOT/cache/polished/{src,release}.
Use --src DIR and --roms DIR to select another source/ROM directory; the ROM and
.sym identity pins still apply. Output: data/games/polished_crystal/script_sites.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections import defaultdict

from slink_space import work_root

ROOT = pathlib.Path(__file__).resolve().parent.parent
INI = ROOT / "data" / "games" / "polished_crystal" / "upr_polished_entries.ini"
OUT = ROOT / "data" / "games" / "polished_crystal" / "script_sites.json"
SPECIES_PACK = ROOT / "data" / "games" / "polished_crystal" / "species_index.json"
LOCK = ROOT / "data" / "polished_sources.lock.json"
RELEASE = work_root("cache") / "polished" / "release"
SRC = work_root("cache") / "polished" / "src"

ROM_SHA1 = "6930b48af5844d373e3c9130f26d6dd1084cf4ed"
ROM_NAME = "polishedcrystal-3.2.3.gbc"
SYM_NAME = "polishedcrystal-3.2.3.sym"
SYMS_SHA256 = "be1ca87ad487ade20701ed71fcae4e99bf16b908af727b84575c862b73ea2955"

SCHEMA = "polished-script-sites-v1"
WINDOW = 512
# Proved on the release ROM by this generator's own scan; re-proved by the test file.
OPCODES = {"givepoke": 0x2F, "loadwildmon": 0x5C}
KINDS = {"GivePokeSite": "givepoke", "LoadWildMonSite": "loadwildmon"}
EXTSPECIES_F = 5
# `Arg2=NONE` means the macro was called in its two-argument form, whose else-branch emits
# `dp species, PLAIN_FORM` (macros/scripts/events.asm:325 and loadwildmon's equivalent). It is not
# "form unknown": the species byte is determinate and the form byte is PLAIN_FORM.
FORM_ALIASES = {"NONE": "PLAIN_FORM"}


class ResolveError(RuntimeError):
    """The ROM or a pack fact does not support resolving a site."""


def _require(ok, message):
    if not ok:
        raise ResolveError(message)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rom(path: pathlib.Path) -> bytes:
    """The pinned release ROM, or nothing. A different cartridge is refused, never analysed."""
    _require(path.is_file(), f"release ROM absent: {path}")
    rom = path.read_bytes()
    _require(hashlib.sha1(rom).hexdigest() == ROM_SHA1,
             f"ROM SHA1 differs from the pin {ROM_SHA1}: {path}")
    return rom


def load_syms(path: pathlib.Path) -> list[tuple[int, str]]:
    """[(flat offset, name)] in .sym order. rgblink prints banks in HEX."""
    _require(path.is_file(), f"release .sym absent: {path}")
    _require(_sha256(path) == SYMS_SHA256, f".sym SHA256 differs from the pin {SYMS_SHA256}")
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{2}):([0-9a-fA-F]{4})\s+(\S+)", line.strip())
        if m:
            bank, addr = int(m.group(1), 16), int(m.group(2), 16)
            out.append((addr if bank == 0 else bank * 0x4000 + addr - 0x4000, m.group(3)))
    _require(out, "no link symbols in .sym")
    return out


def load_species() -> dict[str, int]:
    """In-game constant name -> internal index, from the generated species pack."""
    pack = json.loads(SPECIES_PACK.read_text(encoding="utf-8"))
    table = {row["const"]: int(key) for key, row in pack["species"].items() if row.get("const")}
    _require(table, "species pack has no const names")
    return table


def load_forms(src: pathlib.Path = SRC) -> dict[str, int]:
    """Form constant -> value, from the pinned source (constants/pokemon_constants.asm)."""
    text = (src / "constants" / "pokemon_constants.asm").read_text(encoding="utf-8")
    # `DEF NAME EQU n` for the plain forms (PLAIN_FORM EQU 1, GALARIAN_FORM EQU 3,
    # constants/pokemon_constants.asm:346,:463) and `ext_const NAME ; n` for the variant forms
    # that widen the species byte (e.g. MAGIKARP_MASK1_FORM, :418). Both name a form byte.
    forms = {m.group(1): int(m.group(2).replace("$", "0x"), 16)
             for m in re.finditer(r"^DEF\s+(\w+)\s+EQU\s+(\$[0-9A-Fa-f]+|\d+)\s*$", text, re.M)}
    forms.update({m.group(1): int(m.group(2))
                  for m in re.finditer(r"^\s*ext_const\s+(\w+)\s*;\s*(\d+)", text, re.M)})
    # MALE / FEMALE are gender bits OR'd into a form argument (`FEMALE | PLAIN_FORM`,
    # constants/pokemon_data_constants.asm:273-274), written as %binary literals.
    data = (src / "constants" / "pokemon_data_constants.asm").read_text(encoding="utf-8")
    forms.update({m.group(1): int(m.group(2), 2)
                  for m in re.finditer(r"^DEF\s+(MALE|FEMALE)\s+EQU\s+%([01]+)\s*$", data, re.M)})
    _require(forms, "no form constants found in pokemon_constants.asm")
    return forms


def eval_form(expr: str, forms: dict[str, int]) -> int | None:
    """A form argument: one constant or an `A | B` expression of them (None if any name is unknown)."""
    value = 0
    for part in (FORM_ALIASES.get(p.strip(), p.strip()) for p in expr.split("|")):
        if part not in forms:
            return None
        value |= forms[part]
    return value


def parse_ini(path: pathlib.Path) -> list[dict]:
    """The GivePokeSite* / LoadWildMonSite* blocks, in ini order."""
    text = path.read_text(encoding="utf-8")
    sites, current = [], None
    for line in text.splitlines():
        m = re.match(r"(GivePokeSite|LoadWildMonSite)(\d+)=(.*)$", line.strip())
        if m:
            current = {"index": int(m.group(2)), "prefix": m.group(1),
                       "kind": KINDS[m.group(1)], "status": m.group(3).strip(),
                       "order": len(sites)}
            sites.append(current)
            continue
        m = re.match(r"(GivePokeSite|LoadWildMonSite)\d+(\w+)=(.*)$", line.strip())
        if m and current is not None:
            current[m.group(2)] = m.group(3).strip()
    for site in sites:
        for field in ("Label", "ScriptOffset", "Species", "Arg2", "Source"):
            _require(site.get(field), f"site {site['prefix']}{site['index']} has no {field}")
        site["label_offset"] = int(site["ScriptOffset"], 16)
        site["offset"] = None
    _require(sites, "no script sites in the ini")
    return sites


def window_end(syms: list[tuple[int, str]], label: str, start: int) -> int:
    """End of the scan window: 512 bytes, or the next symbol that is NOT this script.

    A dotted child (`Label.Child`) is the same script -- CherrygroveBayGalarianBirdsScript holds all
    three Galarian birds in `.Galarian_Moltres` / `.Galarian_Articuno` / `.Galarian_Zapdos`, so
    stopping at any symbol would cut the window before the second and third givepoke.
    """
    end = start + WINDOW
    for offset, name in syms:
        if offset > start and name != label and not name.startswith(label + "."):
            return min(end, offset)
    return end


def find_expansions(rom: bytes, syms: list[tuple[int, str]], label: str, kind: str,
                    start: int, species: int, form: int) -> list[int]:
    """Flat offsets of every matching expansion between the label and its window end."""
    opcode = OPCODES[kind]
    low = species & 0xFF
    # `dp` emits `db LOW(sp), HIGH(sp) << MON_EXTSPECIES_F | form` (macros/data.asm:89-91) and the
    # `| form` term is a full assembly-time OR, NOT a 5-bit field: FEMALE is %10000000
    # (constants/pokemon_data_constants.asm:274) and the variant forms are ext_const values such as
    # MAGIKARP_MASK1_FORM = 157 (constants/pokemon_constants.asm:418). Masking to 5 bits silently
    # drops both, so the second byte is masked to a byte, not to a nibble.
    high = (((species >> 8) << EXTSPECIES_F) | form) & 0xFF
    end = window_end(syms, label, start)
    return [i for i in range(start, min(end, len(rom) - 3))
            if rom[i] == opcode and rom[i + 1] == low and rom[i + 2] == high]


def resolve(sites: list[dict], rom: bytes, syms: list[tuple[int, str]], species: dict,
            forms: dict) -> list[dict]:
    """Assign expansions to sites; anything not exactly determined stays Unresolved."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for site in sites:
        if site["Species"] not in species:
            site["reason"] = f"species constant {site['Species']} is not in the species pack"
            continue
        form_const = FORM_ALIASES.get(site["Arg2"], site["Arg2"])
        form_value = eval_form(site["Arg2"], forms)
        if form_value is None:
            site["reason"] = f"form constant {site['Arg2']} is not in pokemon_constants.asm"
            continue
        site["species"] = species[site["Species"]]
        site["form"] = form_value
        groups[(site["Label"], site["kind"], site["Species"], form_const)].append(site)

    for (label, kind, sp_const, form_const), members in groups.items():
        head = members[0]
        hits = find_expansions(rom, syms, label, kind, head["label_offset"],
                               head["species"], head["form"])
        if len(hits) != len(members):
            # A zero count is not a window problem: search the WHOLE cartridge with the same
            # byte rule. If the pattern is absent everywhere, the expansion is simply not in this
            # build (debug-only script text), and no window would have found it. Only a count
            # mismatch inside a window that DOES contain matches is genuinely ambiguous.
            opcode = OPCODES[kind]
            low = head["species"] & 0xFF
            form_byte = (((head["species"] >> 8) << EXTSPECIES_F) | head["form"]) & 0xFF
            anywhere = [i for i in range(len(rom) - 3)
                        if rom[i] == opcode and rom[i + 1] == low and rom[i + 2] == form_byte]
            if not anywhere:
                why = (f"no ${opcode:02X} expansion for {sp_const}/{form_const} "
                       f"(bytes ${opcode:02X} ${low:02X} ${form_byte:02X}) anywhere in the release "
                       f"ROM, so the command behind {label} is not in this build; no window can "
                       f"locate it")
            else:
                why = (f"{len(hits)} matching {kind} expansion(s) for {sp_const}/{form_const} in "
                       f"{label}, but {len(members)} site(s) ask for it "
                       f"({len(anywhere)} such expansion(s) exist cartridge-wide)")
            for member in members:
                member["reason"] = why
            continue
        for member, at in zip(sorted(members, key=lambda s: s["order"]), hits, strict=True):
            member["offset"] = at + 1                      # the species LOW byte
            member["level"] = rom[at + 3]
            if not 1 <= member["level"] <= 100:
                member["reason"] = f"decoded level {member['level']} at {at:#x} is not 1..100"
                member["offset"] = None
    return sites


def render(sites: list[dict]) -> dict:
    rows = []
    for site in sorted(sites, key=lambda s: s["order"]):
        rows.append({
            "kind": site["kind"],
            "source": site["Source"],
            "label": site["Label"],
            "label_offset": site["label_offset"],
            "offset": site.get("offset"),
            "species_const": site["Species"],
            "species": site.get("species"),
            "form": site.get("form"),
            "level": site.get("level"),
            "reason": site.get("reason", "resolved: exactly one matching expansion in the window"),
        })
    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.is_file() else {}
    return {
        "schema": SCHEMA,
        "source": {
            "rom_sha1": ROM_SHA1,
            "rom_file": ROM_NAME,
            "sym_sha256": SYMS_SHA256,
            "tag": lock.get("tag"),
            "commit": lock.get("commit"),
            "opcodes": {k: f"${v:02X}" for k, v in sorted(OPCODES.items())},
            "window_bytes": WINDOW,
        },
        "sites": rows,
    }


def build(src: pathlib.Path = SRC, roms: pathlib.Path = RELEASE) -> dict:
    rom = load_rom(roms / ROM_NAME)
    syms = load_syms(roms / SYM_NAME)
    return render(resolve(parse_ini(INI), rom, syms, load_species(), load_forms(src)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=str(SRC), help="Polished source checkout (constants/*.asm)")
    ap.add_argument("--roms", default=str(RELEASE), help="directory containing the pinned release ROM and .sym")
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed JSON has drifted")
    args = ap.parse_args()
    document = json.dumps(build(pathlib.Path(args.src), pathlib.Path(args.roms)), indent=2, sort_keys=True) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != document:
            print(f"[polished-script-sites] DRIFT in {OUT.name}", file=sys.stderr)
            return 1
        print("[polished-script-sites] reproduces every artifact", file=sys.stderr)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(document)
    rows = json.loads(document)["sites"]
    done = [s for s in rows if s["offset"] is not None]
    print(f"[polished-script-sites] {len(done)}/{len(rows)} resolved -> {OUT}", file=sys.stderr)
    for row in rows:
        if row["offset"] is None:
            print(f"  unresolved {row['source']} {row['species_const']}: {row['reason']}",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
