"""Read Gen 1's rule-bearing tables straight out of a ROM, clean or randomized.

WHY THIS EXISTS. A Soul Link run may be played on ROMs randomized with Universal Pokemon
Randomizer ZX: both players use the same settings but DIFFERENT seeds, so their wild
encounter tables genuinely differ from each other and from the cartridge. Every table SLink
ships in ``data/games/gen1_rby/`` is derived from the pret decomps, which describe the
vanilla cartridge and nothing else. Showing that data beside a randomized ROM is not a
cosmetic problem: it tells a player a route holds Pidgey when it holds Abra.

So the ROM itself becomes the source of truth, and this module is the reader.

NO HARDCODED ROM OFFSETS. Every address comes from ``data/pret_rom_syms.json``, which is
generated from the decomps and carries the SHA-1 of the ROM each symbol set describes. A
symbol's value is ``bank << 16 | address`` -- NOT a flat file offset -- and the difference
is not subtle: ``BaseStats`` is ``0x0E43DE`` as a symbol and ``0x383DE`` in the file. Read it
the wrong way and every table scans plausible-looking garbage rather than failing.

WHAT MAKES A SCAN TRUSTWORTHY. On a CLEAN ROM the scan must reproduce the decomp-derived
tables exactly. That is a free known-positive control -- we have all three cartridges and
know what they contain -- and ``tests/unit/test_gen1_rom_scan.py`` enforces it. A scanner
that cannot reproduce the vanilla answer has no business reporting a randomized one.

VERIFIED LAYOUTS (all read from the real dumps, not inferred):

  Wild encounters   WildDataPointers, one 2-byte bank-local pointer per map id. The table's
                    LENGTH is not assumed: it ends where the first record it points at
                    begins, which self-terminates at 249 entries (R/B) and 250 (Yellow).
                    Each record is  db grass_rate, [10 x (level, species)],
                                    db water_rate, [10 x (level, species)]
                    where a block of ten is present ONLY when its rate is non-zero, so an
                    encounterless map is two zero bytes.

  Old Rod           An inline immediate, not a table: ItemUseOldRod holds `lb bc, 5, MAGIKARP`
                    which assembles to 01 85 05 -- opcode, species, level.

  Good Rod          GoodRodMons, two (level, species) pairs.

  Super Rod         R/B: SuperRodData, 3-byte (map, pointer) records terminated by 0xFF,
                    pointing at groups of `db count, [count x (level, species)]`.
                    YELLOW IS A DIFFERENT FORMAT and shares no code: SuperRodFishingSlots,
                    flat 9-byte records of `map, [4 x (species, level)]` -- note SPECIES
                    FIRST, the opposite order from every other table here.

  Base stats        28-byte records ordered by POKEDEX number, 150 of them. Mew is not in
                    the table on R/B; it sits alone at MewBaseStats. Yellow has no separate
                    Mew symbol. Record offset 19 is BASE_GROWTH_RATE, the curve byte
                    level_from_exp needs -- read from the record rather than inferred.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

log = logging.getLogger(__name__)

_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_SYMS_PATH = os.path.join(_REPO, "data", "pret_rom_syms.json")
_PUREGB_DATA = os.path.join(_REPO, "data", "games", "gen1_purergb")

# The ROM header's title field. Randomizing changes the contents but never this, so it is
# how a randomized ROM is identified -- the SHA-1 only tells us whether it is UNTOUCHED.
_TITLE_OFFSET, _TITLE_END = 0x134, 0x143
_HEADER_TITLE_TO_SYMS = {
    "POKEMON RED": "pokered",
    "POKEMON BLUE": "pokeblue",
    "POKEMON YELLOW": "pokeyellow",
}
# The variant key the rest of SLink uses (encounter_tables.json, adapter _enc_variant).
_SYMS_TO_VARIANT = {"pokered": "red", "pokeblue": "blue", "pokeyellow": "yellow"}

BASE_STATS_RECORD = 28
BASE_STATS_COUNT = 150            # R/B: Bulbasaur..Mewtwo; Mew is stored apart
BASE_STATS_COUNT_YELLOW = 151     # Yellow keeps Mew IN the table, at record 150
WILD_SLOTS = 10                   # both grass and water always carry exactly ten
GEN1_ROM_SIZE = 1024 * 1024

_SLOT_RATES = (20, 20, 15, 10, 10, 10, 5, 5, 4, 1)   # data/wild/probabilities.asm


class RomScanError(Exception):
    """The ROM could not be read as a Gen 1 ROM. Never raised for merely-unexpected data."""


# ── symbols ──────────────────────────────────────────────────────────────────────────────
_SYMS_CACHE: dict | None = None
_PUREGB_ADMISSION_CACHE: dict | None = None
_PUREGB_PACK_CACHE: dict[str, dict] = {}
# The pack file for an artifact base kind: `<name>.json` for clean, `<name>_overlay.json` for
# the SLink companion overlay (ROM symbols relocate, RAM does not: PLAN M3).
_PURE_SUFFIX = {"clean": "", "overlay": "_overlay"}
# identify()'s `kind` -> the base kind whose pack files describe the cartridge.
BASE_KIND = {"clean": "clean", "rand": "clean", "overlay": "overlay", "rand_overlay": "overlay"}


def _load_syms() -> dict:
    global _SYMS_CACHE
    if _SYMS_CACHE is None:
        with open(_SYMS_PATH, encoding="utf-8") as f:
            _SYMS_CACHE = json.load(f)
    return _SYMS_CACHE


def _load_purergb_admission() -> dict:
    """sha1 -> {title, kind, header_title, base_sha1?, ...}: data/games/gen1_purergb/admission.json
    (kind clean) plus admission_overlay.json (kind overlay, base_sha1 = the clean ROM's).

    This is the ONLY correct way to identify a pureRGB cartridge (PLAN.md §11.2 trap 7):
    PureRed/PureBlue's header titles are byte-identical to vanilla Red/Blue ("POKEMON
    RED"/"POKEMON BLUE"), so a header-only lookup would silently hand a pure ROM to the
    vanilla pret symbol table. The sha1 table is checked before the header-based path.
    """
    global _PUREGB_ADMISSION_CACHE
    if _PUREGB_ADMISSION_CACHE is None:
        rows: dict = {}
        for name in ("admission.json", "admission_overlay.json"):
            with open(os.path.join(_PUREGB_DATA, name), encoding="utf-8") as f:
                rows.update(json.load(f))
        # Earlier stamps of the same canonical build (only the menu-version field differs) are
        # still on players' cartridges: each resolves to the row that lists it.
        for row in list(rows.values()):
            for old in row.get("equivalent_sha1s") or ():
                rows.setdefault(old, row)
        _PUREGB_ADMISSION_CACHE = rows
    return _PUREGB_ADMISSION_CACHE


def _load_pure_pack(name: str, kind: str = "clean") -> dict:
    """A pureRGB pack file (profile / engine_signals / write_checkpoint) for a base kind."""
    key = f"{name}{_PURE_SUFFIX[BASE_KIND[kind]]}.json"
    if key not in _PUREGB_PACK_CACHE:
        with open(os.path.join(_PUREGB_DATA, key), encoding="utf-8") as f:
            _PUREGB_PACK_CACHE[key] = json.load(f)
    return _PUREGB_PACK_CACHE[key]


def _load_purergb_profile(kind: str = "clean") -> dict:
    return _load_pure_pack("profile", kind)


def _pure_syms(title: str, kind: str = "clean") -> dict:
    """name -> bank<<16|addr, in the SAME encoding pret's syms use, so sym_to_offset() and
    every scan_* function below work unchanged over either source.

    Verified: profile.json's own precomputed "flat" field for every ROM symbol equals
    sym_to_offset(bank<<16|addr) exactly (checked against all three built ROMs), so this
    round-trip introduces no drift. Entries the profile records as null (a symbol pureRGB's
    build does not carry, e.g. MewBaseStats, EvosMovesPointerTable) are omitted, which is
    exactly the "in syms" absence the vanilla branches below already test for.
    """
    rom_syms = _load_purergb_profile(kind)["titles"][title]["rom"]
    return {name: (entry["bank"] << 16 | entry["addr"])
            for name, entry in rom_syms.items() if entry is not None}


def pure_anchors_hold(rom: bytes, title: str, kind: str) -> bool:
    """Whether every engine site (+ prelude) and checkpoint slice of the pure pack for
    `title`/`kind` reads as pinned in `rom` -- the same anchors lua/gen1/entry.lua admits
    a randomized artifact by (A3 rand / rand_overlay): the UPR fork writes data tables
    only, so a randomized ROM keeps every code byte of exactly one base kind."""
    def slice_ok(off, hexs: str) -> bool:
        want = bytes.fromhex(hexs)
        return isinstance(off, int) and rom[off:off + len(want)] == want

    sites = _load_pure_pack("engine_signals", kind)["titles"][title]["sites"]
    for site in sites.values():
        if not slice_ok(site["rom_offset"], site["expected_hex"]):
            return False
        pre = site.get("prelude")
        if pre and not slice_ok(pre["rom_offset"], pre["expected_hex"]):
            return False
    ws = _load_pure_pack("write_checkpoint", kind)[title]["write_safe"]
    return all(slice_ok(ws.get(key), hexs) for key, hexs in ws.get("expected_hex", {}).items())


def sym_to_offset(value: int) -> int:
    """``bank << 16 | address`` -> flat file offset.

    Bank 0 is not banked, so its address IS the offset. Every other bank is mapped into the
    0x4000-0x7FFF window, so the address has to be de-based before the bank is applied.
    Getting this backwards is the single easiest way to scan garbage that looks like data.
    """
    bank, addr = value >> 16, value & 0xFFFF
    return addr if bank == 0 else bank * 0x4000 + (addr - 0x4000)


# ── identification ───────────────────────────────────────────────────────────────────────
def rom_title(rom: bytes) -> str:
    return rom[_TITLE_OFFSET:_TITLE_END].rstrip(b"\x00").decode("ascii", "replace").strip()


def identify(rom: bytes) -> dict:
    """Say which cartridge this is, and whether it is untouched.

    ``clean`` is a statement about the bytes and nothing else. It is NOT provenance: a
    randomized ROM is not clean, but neither is a patched one, and a ROM being clean says
    nothing about which settings or seed produced a different one.

    A pureRGB cartridge is identified by a full-ROM sha1 lookup against admission.json
    FIRST, before the header-title path below runs at all: PureRed/PureBlue's header
    titles are byte-identical to vanilla Red/Blue ("POKEMON RED"/"POKEMON BLUE"), so
    checking the header first would silently route a pure ROM through the vanilla pret
    symbol table (PLAN.md §11.2 trap 7). The table holds both admitted kinds: a clean
    build (kind "clean", ``clean`` True) and the SLink companion overlay (kind "overlay",
    ``clean`` False, ``pinned`` True, ``clean_sha1`` = the clean base's). A pure sha1 in
    neither is a modified pure ROM (the UPR fork's output, A3): its base kind is named by
    the pack anchors (kind "rand" / "rand_overlay", ``pinned`` False); a pure ROM matching
    neither anchor set has a changed code byte and raises RomScanError.

    ``pinned`` is what the randomizer pipeline may start from: a byte-exact admitted
    artifact, clean or overlay (A5: the overlay is randomized as an overlay).
    """
    if len(rom) != GEN1_ROM_SIZE:
        raise RomScanError(
            f"expected a {GEN1_ROM_SIZE}-byte Gen 1 ROM, got {len(rom)} bytes")
    sha1 = hashlib.sha1(rom).hexdigest()
    pure_entry = _load_purergb_admission().get(sha1)
    if pure_entry is not None:
        kind = pure_entry.get("kind") or "clean"
        return {
            "title": pure_entry.get("header_title") or rom_title(rom),
            "syms_key": f"purergb:{pure_entry['title']}",
            "variant": pure_entry["title"],
            "foundation": "gen1_purergb",
            "kind": kind,
            "sha1": sha1,
            "clean": kind == "clean",
            "pinned": True,
            "clean_sha1": pure_entry.get("base_sha1") or sha1,
            "header_checksum": int.from_bytes(rom[0x14E:0x150], "big"),
        }
    title = rom_title(rom)
    # A MODIFIED pure ROM (the UPR fork's output, M5) no longer matches any admitted sha1
    # and its header title is vanilla's. The pure PokedexOrder table is what tells them
    # apart: it sits at an offset vanilla uses for other data, and nothing the fork can
    # write touches it, so a valid dex permutation there means a pure cartridge. The base
    # kind is then whichever pack's anchors hold (overlay first: its sites are the
    # relocated ones, so a clean ROM never matches them); a ROM matching neither has a
    # code byte changed and is refused.
    pure_title = _purergb_title_for_modified(rom, title)
    if pure_title is not None:
        if pure_anchors_hold(rom, pure_title, "overlay"):
            base = "overlay"
        elif pure_anchors_hold(rom, pure_title, "clean"):
            base = "clean"
        else:
            # a pure dex order but a changed code byte: not the fork's output of either base,
            # and the Lua gate would refuse it -- so the pipeline must too (review cx-6aacc4f1 #3)
            raise RomScanError(
                f"pureRGB {pure_title} ROM with a modified engine site: neither the clean nor "
                f"the overlay anchor set holds (sha1 {sha1})")
        clean_sha1 = next(k for k, v in _load_purergb_admission().items()
                          if v.get("title") == pure_title and v.get("kind") == base)
        return {
            "title": title,
            "syms_key": f"purergb:{pure_title}",
            "variant": pure_title,
            "foundation": "gen1_purergb",
            "kind": "rand" if base == "clean" else "rand_overlay",
            "sha1": sha1,
            "clean": False,
            "pinned": False,
            "clean_sha1": clean_sha1,
            "header_checksum": int.from_bytes(rom[0x14E:0x150], "big"),
        }
    syms_key = _HEADER_TITLE_TO_SYMS.get(title)
    if not syms_key:
        raise RomScanError(
            f"ROM header title {title!r} is not a supported Gen 1 title "
            f"(expected one of {sorted(_HEADER_TITLE_TO_SYMS)})")
    entry = _load_syms()[syms_key]
    return {
        "title": title,
        "syms_key": syms_key,
        "variant": _SYMS_TO_VARIANT[syms_key],
        "foundation": "gen1_rby",
        "kind": "clean" if sha1 == entry["rom_sha1"] else "rand",
        "sha1": sha1,
        "clean": sha1 == entry["rom_sha1"],
        "pinned": sha1 == entry["rom_sha1"],
        "clean_sha1": entry["rom_sha1"],
        "header_checksum": int.from_bytes(rom[0x14E:0x150], "big"),
    }


_HEADER_TITLE_TO_PURE = {"POKEMON RED": "purered", "POKEMON BLUE": "pureblue",
                         "POKEMON GREEN": "puregreen"}


def _purergb_title_for_modified(rom: bytes, header_title: str) -> str | None:
    """The pure title whose PokedexOrder table (190 internal ids -> dex 0..151, every dex
    number 1..151 present exactly once) this ROM carries at the pure offset, else None."""
    pure_title = _HEADER_TITLE_TO_PURE.get(header_title)
    if pure_title is None:
        return None
    try:
        off = sym_to_offset(_pure_syms(pure_title)["PokedexOrder"])
    except (KeyError, OSError, ValueError):
        return None
    table = rom[off:off + INTERNAL_POKEMON_COUNT]
    seen = [b for b in table if b]
    if len(set(seen)) == 151 and max(seen) == 151:
        return pure_title
    return None


def _syms_for(rom: bytes) -> tuple[dict, dict]:
    ident = identify(rom)
    if ident.get("foundation") == "gen1_purergb":
        return ident, _pure_syms(ident["variant"], ident.get("kind", "clean"))
    return ident, _load_syms()[ident["syms_key"]]["symbols"]


# ── wild encounters ──────────────────────────────────────────────────────────────────────
def _read_slots(rom: bytes, off: int) -> list[dict]:
    """Ten (level, species) pairs. Levels and species are validated by the caller."""
    return [{"level": rom[off + 2 * i], "species_index": rom[off + 2 * i + 1]}
            for i in range(WILD_SLOTS)]


def scan_wild(rom: bytes) -> dict[int, dict]:
    """map id -> {"grass": {...} | None, "water": {...} | None}.

    Maps with neither are omitted entirely, so the result is the set of maps that actually
    hold wild Pokemon -- which is what every caller wants and is a much smaller thing to
    ship than 249 mostly-empty records.
    """
    ident, syms = _syms_for(rom)
    if "WildDataPointers" not in syms:
        raise RomScanError(f"{ident['syms_key']} has no WildDataPointers symbol")
    table_sym = syms["WildDataPointers"]
    table = sym_to_offset(table_sym)
    bank = table_sym >> 16

    def resolve(ptr: int) -> int:
        # Bank-local pointers live in the 0x4000-0x7FFF window. Anything else means we have
        # walked off the table or the ROM is not laid out the way the symbols claim.
        if not (0x4000 <= ptr <= 0x7FFF):
            raise RomScanError(
                f"wild pointer 0x{ptr:04X} is outside the bank window 0x4000-0x7FFF")
        return bank * 0x4000 + (ptr - 0x4000)

    # The table's length is DERIVED, not assumed. pret ends it with a real terminator --
    # `assert_table_length NUM_MAPS` then `dw -1 ; end` (data/wild/grass_water.asm) -- so
    # 0xFFFF is the authoritative stop. The "never reach the first record we point at"
    # bound is kept as a second line of defence: if a randomizer ever dropped the
    # terminator, that catches it instead of walking into the data as if it were pointers.
    out: dict[int, dict] = {}
    first_record = len(rom)
    map_id = 0
    while True:
        off = table + 2 * map_id
        raw = int.from_bytes(rom[off:off + 2], "little")
        if raw == 0xFFFF:
            break
        if off >= first_record:
            raise RomScanError(
                f"wild pointer table reached 0x{off:05X}, which is inside the record data "
                f"at 0x{first_record:05X} — the 0xFFFF terminator is missing")
        record = resolve(raw)
        first_record = min(first_record, record)

        entry: dict[str, dict | None] = {}
        cursor = record
        for method in ("grass", "water"):
            rate = rom[cursor]
            cursor += 1
            if rate == 0:
                entry[method] = None
                continue
            if cursor + 2 * WILD_SLOTS > len(rom):
                raise RomScanError(
                    f"map 0x{map_id:02X} {method} slots run past the end of the ROM")
            entry[method] = {"rate": rate, "slots": _read_slots(rom, cursor)}
            cursor += 2 * WILD_SLOTS
        if entry["grass"] or entry["water"]:
            out[map_id] = entry
        map_id += 1
    return out


# ── fishing (pureRGB) ────────────────────────────────────────────────────────────────────
def _scan_old_rod_purergb(rom: bytes, syms: dict) -> list[dict]:
    """pureRGB's Old Rod is a 50/50 species choice, not vanilla's one fixed species.

    engine/items/item_effects.asm's ItemUseOldRod (pureRGB 7e7a4653) is::

        call Random
        and 1
        jr z, .goldeen
        lb bc, 10, MAGIKARP
        jr .done
    .goldeen
        lb bc, 10, GOLDEEN
    .done

    so there are TWO `lb bc, 10, SPECIES` immediates a few bytes apart, each compiling to
    `ld bc,nn` (01 <species> <level>) exactly like vanilla's single one. The fixed-length
    instructions ahead of them (call FishingInit + jp c,.. + call Random + and 1 + jr z,..
    = 13 bytes, then jr .done + the second `lb bc` = 5 more) put them at ItemUseOldRod+13
    and +18 in all three built ROMs (only CableClubNPC and Evolution_ChangeMonPic differ
    across purered/pureblue/puregreen, and neither is upstream of this routine). Each
    offset asserts the `ld bc,nn` opcode first, the same defence vanilla's ItemUseOldRod+6
    check uses, so a reassembled routine that moves them fails loudly instead of a
    pattern scan silently matching a `01` operand byte inside `and 1` (0xE6 0x01) instead.
    """
    base = sym_to_offset(syms["ItemUseOldRod"])
    entries = []
    for offset in (13, 18):
        if rom[base + offset] != 0x01:
            raise RomScanError(
                f"ItemUseOldRod+{offset} is 0x{rom[base + offset]:02X}, expected the "
                f"0x01 `ld bc,nn` opcode")
        entries.append({"species_index": rom[base + offset + 1], "level": rom[base + offset + 2]})
    return entries


def _scan_good_rod_purergb(rom: bytes, syms: dict) -> tuple[list[dict], list[dict]]:
    """pureRGB's Good Rod picks from one of TWO tables (land vs ocean), each up to ten
    (level, species) pairs terminated by -1,-1 (data/wild/good_rod.asm). GoodRodMons is the
    only symbol recorded (GoodRodMonsOcean is not in the profile); the ocean table is the
    next `INCLUDE`d block, immediately after the land table's own terminator — confirmed
    against the built ROM, where the land table's 4 pairs + FF FF are followed directly by
    the ocean table's own 4 pairs + FF FF with no gap.
    """
    def read_pairs(off: int) -> tuple[list[dict], int]:
        entries, cur = [], off
        for _ in range(WILD_SLOTS):
            level, species = rom[cur], rom[cur + 1]
            cur += 2
            if level == 0xFF and species == 0xFF:
                return entries, cur
            entries.append({"level": level, "species_index": species})
        raise RomScanError("GoodRodMons: no -1,-1 terminator within 10 entries")

    base = sym_to_offset(syms["GoodRodMons"])
    land, after_land = read_pairs(base)
    ocean, _ = read_pairs(after_land)
    return land, ocean


# ── fishing ──────────────────────────────────────────────────────────────────────────────
def scan_fishing(rom: bytes) -> dict:
    """The three rods. Old and Good are global; Super is per-map in both titles' formats."""
    ident, syms = _syms_for(rom)
    out: dict = {}

    if ident.get("foundation") == "gen1_purergb":
        out["old_rod"] = _scan_old_rod_purergb(rom, syms)
        out["good_rod"], out["good_rod_ocean"] = _scan_good_rod_purergb(rom, syms)
    else:
        # Old Rod: `lb bc, 5, MAGIKARP` -> 01 85 05 at ItemUseOldRod+6. Assert the opcode
        # rather than trusting the displacement, so a relocated routine fails loudly
        # instead of reporting whatever byte happens to sit there.
        old = sym_to_offset(syms["ItemUseOldRod"]) + 6
        if rom[old] != 0x01:
            raise RomScanError(
                f"ItemUseOldRod+6 is 0x{rom[old]:02X}, expected the 0x01 `ld bc,nn` opcode")
        out["old_rod"] = [{"level": rom[old + 2], "species_index": rom[old + 1]}]

        good = sym_to_offset(syms["GoodRodMons"])
        out["good_rod"] = [{"level": rom[good + 2 * i], "species_index": rom[good + 2 * i + 1]}
                           for i in range(2)]

    if "SuperRodFishingSlots" in syms:
        # Yellow. Flat 9-byte records, SPECIES FIRST, terminated by 0xFF.
        cur = sym_to_offset(syms["SuperRodFishingSlots"])
        per_map: dict[int, list[dict]] = {}
        while rom[cur] != 0xFF:
            map_id = rom[cur]
            per_map[map_id] = [
                {"species_index": rom[cur + 1 + 2 * i], "level": rom[cur + 2 + 2 * i]}
                for i in range(4)]
            cur += 9
            if cur >= len(rom):
                raise RomScanError("SuperRodFishingSlots ran off the end without a 0xFF")
        out["super_rod"] = per_map
    elif "SuperRodData" in syms:
        # Red/Blue. (map, pointer) -> a group of `db count, [count x (level, species)]`.
        table_sym = syms["SuperRodData"]
        cur = sym_to_offset(table_sym)
        bank = table_sym >> 16
        per_map = {}
        while rom[cur] != 0xFF:
            map_id = rom[cur]
            ptr = int.from_bytes(rom[cur + 1:cur + 3], "little")
            if not (0x4000 <= ptr <= 0x7FFF):
                raise RomScanError(
                    f"super rod group pointer 0x{ptr:04X} is outside the bank window")
            grp = bank * 0x4000 + (ptr - 0x4000)
            count = rom[grp]
            if not (1 <= count <= 10):
                raise RomScanError(
                    f"super rod group for map 0x{map_id:02X} claims {count} entries")
            per_map[map_id] = [
                {"level": rom[grp + 1 + 2 * i], "species_index": rom[grp + 2 + 2 * i]}
                for i in range(count)]
            cur += 3
            if cur >= len(rom):
                raise RomScanError("SuperRodData ran off the end without a 0xFF")
        out["super_rod"] = per_map
    else:
        raise RomScanError(f"{ident['syms_key']} has neither super rod symbol")
    return out


# ── base stats ───────────────────────────────────────────────────────────────────────────
def scan_base_stats(rom: bytes) -> dict[int, dict]:
    """dex number -> base stats. Keyed by DEX, because that is how the ROM orders them.

    Carries types, which the type clause enforces on, and the five stats plus catch rate
    and the growth rate, which is what a from-ROM stat rebuild needs: CalcStat is
    deterministic from base stat, stat exp, DV and level, and the level is recovered from
    the stored exp through the GROWTH curve this record names -- a per-species fact no
    exported table carries.
    """
    ident, syms = _syms_for(rom)
    base = sym_to_offset(syms["BaseStats"])
    record_size, count, mew_symbol = _base_stats_shape(ident, syms)
    out: dict[int, dict] = {}

    def read(off: int) -> dict:
        r = rom[off:off + record_size]
        if len(r) < record_size:
            raise RomScanError("base stats record runs past the end of the ROM")
        return {
            "dex": r[0], "hp": r[1], "attack": r[2], "defense": r[3],
            "speed": r[4], "special": r[5],
            "type1": r[6], "type2": r[7], "catch_rate": r[8], "base_exp": r[9],
            # Offset 19, counted in the record, not assumed: BASE_GROWTH_RATE follows
            # BASE_MOVES rb NUM_MOVES (4 bytes) in the rsreset block at pret
            # constants/pokemon_data_constants.asm:20-21, and the `db GROWTH_MEDIUM_SLOW`
            # that lands there is data/pokemon/base_stats/bulbasaur.asm:14. The value is
            # one of the six constants at constants/pokemon_data_constants.asm:87-93
            # (MEDIUM_FAST=0, SLIGHTLY_FAST=1, SLIGHTLY_SLOW=2, MEDIUM_SLOW=3, FAST=4,
            # SLOW=5); Gen 1 only ever uses 0, 3, 4 and 5. pureRGB's 35-byte record keeps
            # the same first 20 bytes (PLAN.md §11.2 A13); only the stride grows.
            "growth_rate": r[19],
        }

    for i in range(count):
        rec = read(base + record_size * i)
        # The table is dex-ordered, so record i MUST describe dex i+1. If it does not, the
        # stride or the symbol is wrong and everything downstream is fiction.
        if rec["dex"] != i + 1:
            raise RomScanError(
                f"base stats record {i} reports dex {rec['dex']}, expected {i + 1} — "
                f"the {record_size}-byte stride or the BaseStats symbol is wrong")
        out[rec["dex"]] = rec

    if mew_symbol:
        mew = read(sym_to_offset(syms[mew_symbol]))
        if mew["dex"] != 151:
            raise RomScanError(f"{mew_symbol} reports dex {mew['dex']}, expected 151")
        out[151] = mew
    return out


def _base_stats_shape(ident: dict, syms: dict) -> tuple[int, int, str | None]:
    """(record size, how many main-table records, the separate-Mew symbol name or None).

    HOW MANY RECORDS THE MAIN TABLE HOLDS DEPENDS ON THE TITLE.
    pokered/data/pokemon/base_stats.asm ends `assert_table_length NUM_POKEMON - 1 ;
    discount Mew` and stores Mew at its own symbol; pokeyellow's ends
    `assert_table_length NUM_POKEMON` with mew.asm included, so Yellow's Mew is record 150.
    Reading a flat 150 on Yellow silently dropped dex 151 from every Python scan -- while
    the Lua client got it right, so the two disagreed -- and _check_content diffs
    the scan against itself, giving the pipeline's only rule-data guard a species-shaped
    hole on Yellow.

    pureRGB has NO separate Mew symbol either (verified: absent from every title's `rom`
    symbol table), so a "MewBaseStats" in syms" test alone would take the Yellow branch
    for the right COUNT but by the wrong reasoning (PLAN.md §11.2 trap 5) — and the wrong
    STRIDE (28, not 35: trap 4). This reads both facts from profile.derived instead of
    inferring them from symbol absence.
    """
    if ident.get("foundation") == "gen1_purergb":
        derived = _load_purergb_profile()["titles"][ident["variant"]]["derived"]
        # dex_count is 152 = dex ids 0..151 (PLAN.md §11.2 trap 6); Mew is inline as the
        # last of the 151 ordinary records, so there is no separate symbol to read.
        return derived["base_stats_stride"], derived["dex_count"] - 1, None
    if "MewBaseStats" in syms:
        return BASE_STATS_RECORD, BASE_STATS_COUNT, "MewBaseStats"
    return BASE_STATS_RECORD, BASE_STATS_COUNT_YELLOW, None


# ── evolutions and learnsets ─────────────────────────────────────────────────────────────
NUM_POKEMON_INDEXES = 190         # pointer table length; internal indexes, MissingNo included
EVOLVE_LEVEL, EVOLVE_ITEM, EVOLVE_TRADE = 1, 2, 3     # pokemon_data_constants.asm:78-80


def scan_evos_moves(rom: bytes) -> dict[int, dict]:
    """internal species index -> {"evolutions": [...], "moves": [(level, move), ...]}.

    WHY THIS EXISTS EVEN THOUGH WE DO NOT RANDOMIZE EVOLUTIONS. UPR repacks and repoints
    these records on EVERY save -- `savingRom()` -> `savePokemonStats()` ->
    `writeEvosAndMovesLearnt` -- so a wild-only run still rewrites the whole region and
    moves every record. Comparing the bytes, or the offsets, would therefore report a
    difference on a run that changed nothing. The only way to tell "repacked" from
    "altered" is to walk the pointers and compare the LOGICAL graph, which is what this
    returns.

    Nothing read these tables at all before, so evolutions randomized outside the Manager
    were invisible: `evo_family` went on enforcing vanilla families against a cartridge
    that no longer had them, blocking a legal pair and permitting an illegal one.

    Structure (evos_moves.asm:1-9), per species, in one bank:
        db EVOLVE_LEVEL, level,      species
        db EVOLVE_ITEM,  item, 1,    species
        db EVOLVE_TRADE, 1,          species
        db 0                      ; evolutions end
        db level, move            ; learnset, ascending
        db 0                      ; learnset ends

    The pointer table is in INTERNAL index order (entry 0 is Rhydon), not dex order.
    """
    ident, syms = _syms_for(rom)
    base = sym_to_offset(syms["EvosMovesPointerTable"])
    bank_base = base & ~0x3FFF        # the records are bank-local to the pointer table

    out: dict[int, dict] = {}
    for i in range(NUM_POKEMON_INDEXES):
        lo, hi = rom[base + 2 * i], rom[base + 2 * i + 1]
        addr = lo | (hi << 8)
        if not (0x4000 <= addr <= 0x7FFF):
            raise RomScanError(
                f"EvosMovesPointerTable entry {i} points outside the bank window "
                f"(0x{addr:04X}) — the symbol or the table length is wrong")
        off = bank_base + (addr - 0x4000)
        evolutions, moves = [], []

        # Evolutions, terminated by a 0 method byte.
        guard = 0
        while True:
            guard += 1
            if guard > 8:
                raise RomScanError(f"species index {i}: evolution list has no terminator")
            method = rom[off]
            if method == 0:
                off += 1
                break
            if method == EVOLVE_LEVEL:
                evolutions.append(("level", rom[off + 1], rom[off + 2]))
                off += 3
            elif method == EVOLVE_ITEM:
                evolutions.append(("item", rom[off + 1], rom[off + 3]))
                off += 4
            elif method == EVOLVE_TRADE:
                evolutions.append(("trade", rom[off + 1], rom[off + 2]))
                off += 3
            else:
                raise RomScanError(
                    f"species index {i}: evolution method {method} is not one of "
                    f"LEVEL/ITEM/TRADE — the record layout is wrong")

        # Learnset: (level, move) pairs, terminated by a 0 level.
        guard = 0
        while rom[off] != 0:
            guard += 1
            if guard > 64:
                raise RomScanError(f"species index {i}: learnset has no terminator")
            moves.append((rom[off], rom[off + 1]))
            off += 2

        out[i + 1] = {"evolutions": evolutions, "moves": moves}
    return out


def evolution_graph(rom: bytes) -> dict[int, list]:
    """Just the evolution edges, which is what the rules actually depend on.

    Separated from the learnset so a moveset-only difference cannot be mistaken for an
    evolution change, and so the comparison in upr_pipeline can name which of the two
    drifted.
    """
    return {idx: rec["evolutions"] for idx, rec in scan_evos_moves(rom).items()}


# ── profile ──────────────────────────────────────────────────────────────────────────────
def scan(rom: bytes) -> dict:
    """The whole content profile for one ROM."""
    ident = identify(rom)
    return {
        "identity": ident,
        "wild": scan_wild(rom),
        "fishing": scan_fishing(rom),
        "base_stats": scan_base_stats(rom),
    }


def profile_hash(profile: dict) -> str:
    """A stable digest of the CONTENT only.

    Deliberately excludes ``identity``: two players on the same settings and different seeds
    must produce different hashes because their tables differ, not because their files do,
    and a patched-but-identical-content ROM must produce the SAME hash as its unpatched
    source. Hashing the file would conflate all three.
    """
    body = {k: v for k, v in profile.items() if k != "identity"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def slot_rates() -> tuple[int, ...]:
    """Per-slot encounter probability, data/wild/probabilities.asm. Sums to 100."""
    return _SLOT_RATES


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rom", help="path to a Gen 1 ROM (clean or randomized)")
    ap.add_argument("--json", action="store_true", help="dump the whole profile as JSON")
    args = ap.parse_args(argv)

    with open(args.rom, "rb") as f:
        rom = f.read()
    profile = scan(rom)
    if args.json:
        print(json.dumps(profile, indent=2, sort_keys=True, default=str))
        return 0

    ident = profile["identity"]
    print(f"{ident['title']}  variant={ident['variant']}")
    print(f"  sha1   {ident['sha1']}  {'CLEAN' if ident['clean'] else 'MODIFIED'}")
    if not ident["clean"]:
        print(f"         clean would be {ident['clean_sha1']}")
    print(f"  header checksum 0x{ident['header_checksum']:04X}")
    wild = profile["wild"]
    grass = sum(1 for v in wild.values() if v["grass"])
    water = sum(1 for v in wild.values() if v["water"])
    print(f"  wild   {len(wild)} maps with encounters ({grass} grass, {water} water)")
    fish = profile["fishing"]
    print(f"  fishing old={len(fish['old_rod'])} good={len(fish['good_rod'])} "
          f"super={len(fish['super_rod'])} maps")
    print(f"  base   {len(profile['base_stats'])} species")
    print(f"  content hash {profile_hash(profile)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ── the client's payload ─────────────────────────────────────────────────────────────────
# The Lua client reads the same tables out of the ROM it is running and ships them as raw
# hex. It deliberately does NOT interpret them: the layout rules -- ten slots per method, a
# block present only when its rate is non-zero, Yellow's reversed super-rod fields -- live
# here and nowhere else, so a ROM file and a client payload cannot be read differently.
MAX_CLIENT_MAPS = 512               # 249 real entries; a payload larger than this is hostile


def _hex_to_bytes(hexstr: str, what: str) -> bytes:
    if not isinstance(hexstr, str):
        raise RomScanError(f"{what}: expected a hex string, got {type(hexstr).__name__}")
    try:
        return bytes.fromhex(hexstr)
    except ValueError as exc:
        raise RomScanError(f"{what}: not valid hex ({exc})") from exc


def parse_wild_record(buf: bytes, what: str = "record") -> dict:
    """One map's grass+water record, from exactly the bytes the ROM holds."""
    entry: dict[str, dict | None] = {}
    cur = 0
    for method in ("grass", "water"):
        if cur >= len(buf):
            raise RomScanError(f"{what}: ran out of bytes before the {method} rate")
        rate = buf[cur]
        cur += 1
        if rate == 0:
            entry[method] = None
            continue
        if cur + 2 * WILD_SLOTS > len(buf):
            raise RomScanError(
                f"{what}: {method} claims rate {rate} but only {len(buf) - cur} bytes remain")
        entry[method] = {"rate": rate, "slots": _read_slots(buf, cur)}
        cur += 2 * WILD_SLOTS
    if cur != len(buf):
        raise RomScanError(f"{what}: {len(buf) - cur} trailing bytes after the record")
    return entry


def _validate_slots(block, what: str) -> None:
    if block is None:
        return
    for s in block["slots"]:
        # 100 is the real ceiling everywhere except one pureRGB placeholder: Cinnabar
        # Island/Route 19/Route 20 have no land encounters at all, but WildDataPointers
        # still needs a ten-slot grass block, so pureRGB stubs it with ten (level 120,
        # MISSINGNO) entries -- deliberately unreachable in play (data/wild/maps/
        # SeaRoutes.asm), not a client's malformed report.
        if not 1 <= s["level"] <= 120:
            raise RomScanError(f"{what}: level {s['level']} outside 1-120")
        if s["species_index"] == 0:
            raise RomScanError(f"{what}: species index 0 is NO_MON")


def parse_client_content(payload: dict) -> dict:
    """Validate and decode what a client sent, into the same shape ``scan`` produces.

    Everything here is untrusted input from a process we do not control, so it is checked
    rather than assumed: hex validity, record length, slot count, level range, species != 0,
    map-id range and a cap on how many maps may be declared. A payload that fails ANY of
    these raises -- and the caller's correct response is to mark the encounter data
    unavailable, never to fall back to the decomp tables, because vanilla species shown
    beside a randomized cartridge is the exact misinformation this exists to remove.
    """
    if not isinstance(payload, dict):
        raise RomScanError("rom_content payload is not an object")
    variant = payload.get("variant")
    # Vanilla's three ("red"/"blue"/"yellow") plus the pack's own three pureRGB titles —
    # a second foundation's client reports its OWN variant string (Gen1PureRGBAdapter's
    # ``_variant``), not one of vanilla's.
    if variant not in _SYMS_TO_VARIANT.values() and variant not in ("purered", "pureblue", "puregreen"):
        raise RomScanError(f"rom_content declares unknown variant {variant!r}")

    raw_wild = payload.get("wild")
    if not isinstance(raw_wild, dict) or not raw_wild:
        raise RomScanError("rom_content carries no wild tables")
    if len(raw_wild) > MAX_CLIENT_MAPS:
        raise RomScanError(f"rom_content declares {len(raw_wild)} maps")

    wild: dict[int, dict] = {}
    for key, hexstr in raw_wild.items():
        try:
            map_id = int(key)
        except (TypeError, ValueError):
            raise RomScanError(f"rom_content wild key {key!r} is not a map id") from None
        if not 0 <= map_id <= 0xFF:
            raise RomScanError(f"rom_content wild map id {map_id} outside 0-255")
        label = f"map 0x{map_id:02X}"
        rec = parse_wild_record(_hex_to_bytes(hexstr, label), label)
        _validate_slots(rec["grass"], label + " grass")
        _validate_slots(rec["water"], label + " water")
        if rec["grass"] or rec["water"]:
            wild[map_id] = rec

    fishing: dict = {}
    # (species, level) pair count is 1 for vanilla's fixed Old Rod, 2 for pureRGB's 50/50
    # choice; (level, species) pair count is 2 for vanilla's Good Rod, 4 for pureRGB's —
    # generalised to "however many whole pairs the client sent" so both foundations share
    # one parse without vanilla's own exactly-2/exactly-4-byte payloads changing meaning.
    old = _hex_to_bytes(payload.get("old_rod") or "", "old_rod")
    if old and len(old) % 2 == 0:
        fishing["old_rod"] = [{"species_index": old[2 * i], "level": old[2 * i + 1]}
                              for i in range(len(old) // 2)]
    good = _hex_to_bytes(payload.get("good_rod") or "", "good_rod")
    if good and len(good) % 2 == 0:
        fishing["good_rod"] = [{"level": good[2 * i], "species_index": good[2 * i + 1]}
                               for i in range(len(good) // 2)]
    # pureRGB-only: a second, ocean-specific Good Rod table (data/wild/good_rod.asm). Absent
    # from vanilla's payload shape, so this key is simply never populated for vanilla.
    ocean = _hex_to_bytes(payload.get("good_rod_ocean") or "", "good_rod_ocean")
    if ocean and len(ocean) % 2 == 0:
        fishing["good_rod_ocean"] = [{"level": ocean[2 * i], "species_index": ocean[2 * i + 1]}
                                     for i in range(len(ocean) // 2)]

    super_rod: dict[int, list[dict]] = {}
    raw_super = payload.get("super_rod") or {}
    if not isinstance(raw_super, dict):
        raise RomScanError("rom_content super_rod is not an object")
    if len(raw_super) > MAX_CLIENT_MAPS:
        raise RomScanError(f"rom_content declares {len(raw_super)} super rod maps")
    for key, hexstr in raw_super.items():
        try:
            map_id = int(key)
        except (TypeError, ValueError):
            raise RomScanError(f"super_rod key {key!r} is not a map id") from None
        label = f"super_rod map 0x{map_id:02X}"
        buf = _hex_to_bytes(hexstr, label)
        if variant == "yellow":
            # Flat 4 x (species, level) -- species FIRST, the opposite order from R/B.
            if len(buf) != 8:
                raise RomScanError(
                    f"{label}: Yellow records are 8 bytes, got {len(buf)}")
            entries = [{"species_index": buf[2 * i], "level": buf[2 * i + 1]}
                       for i in range(4)]
        else:
            count = buf[0] if buf else 0
            if not 1 <= count <= 10 or len(buf) != 1 + 2 * count:
                raise RomScanError(
                    f"{label}: count {count} does not match {len(buf)} bytes")
            entries = [{"level": buf[1 + 2 * i], "species_index": buf[2 + 2 * i]}
                       for i in range(count)]
        for e in entries:
            if not 1 <= e["level"] <= 100 or e["species_index"] == 0:
                raise RomScanError(f"{label}: implausible entry {e}")
        super_rod[map_id] = entries
    fishing["super_rod"] = super_rod

    return {"variant": variant, "wild": wild, "fishing": fishing}


# ── turning slots into what the UI shows ─────────────────────────────────────────────────
def aggregate_slots(slots: list[dict], *, uniform: bool = False) -> list[dict]:
    """Aggregate weighted grass/water slots or uniform fishing entries by species."""
    by_species: dict[int, dict] = {}
    for i, slot in enumerate(slots):
        sid = slot["species_index"]
        rec = by_species.setdefault(
            sid, {"species_index": sid, "rate": 0,
                  "min_level": slot["level"], "max_level": slot["level"]})
        rec["rate"] += 1 if uniform else _SLOT_RATES[i]
        rec["min_level"] = min(rec["min_level"], slot["level"])
        rec["max_level"] = max(rec["max_level"], slot["level"])
    if uniform:
        for rec in by_species.values():
            rate = 100 * rec["rate"] / len(slots)
            rec["rate"] = int(rate) if rate.is_integer() else rate
    # First-appearance order here, deliberately: the ordering the UI shows is applied one
    # stage later, in build_encounter_tables, because it sorts by NATIONAL DEX number and
    # that is not known until the internal index has been mapped. Sorting on the internal
    # index instead puts Dodrio before Venomoth in Cerulean Cave, where the shipped table
    # has Venomoth first.
    return list(by_species.values())


def build_encounter_tables(content: dict, map_to_area, index_to_natdex,
                           species_name, floor_labels: dict[str, str] | None = None) -> dict:
    """area_id -> method -> entries, in exactly the shape encounter_tables.json uses.

    FIRST-WINS BY MAP ID, matching tools/gen_gen1_encounters.py: several floors of one
    dungeon share an area_id and only the lowest map id contributes. That is a real
    limitation -- it is why some wild tables are unreachable in the UI today -- but
    mirroring it here means a CLEAN ROM reproduces the shipped tables exactly, which is a
    control worth more than a partial improvement. Sub-area ids are the proper fix and are
    a separate piece of work.

    ``floor_labels`` defaults to vanilla's own file (the historical behaviour, unchanged
    for every existing caller); a foundation whose maps are renumbered -- pureRGB's are
    (docs/purergb/PLAN.md §11.2 A13/W1) -- MUST pass its own
    data/games/<foundation>/floor_labels.json instead, or a floor suffix would be applied
    using the WRONG map id's meaning.
    """
    out: dict = {}
    floors = _floor_labels() if floor_labels is None else floor_labels

    def add(area_id: str, method: str, slots: list[dict], map_id: int | None = None,
            *, uniform: bool = False) -> None:
        # ONE RULE AREA PER DUNGEON, BUT EVERY FLOOR'S TABLE -- the same split
        # tools/gen_gen1_encounters.py performs, using the labels it publishes, because a
        # clean ROM scanned here has to reproduce the shipped file exactly. That equality is
        # the known-positive control for this whole scanner.
        if map_id is not None:
            method += floors.get(str(map_id), "")
        block = out.setdefault(area_id, {})
        if method in block:
            return                          # first-wins WITHIN a floor
        entries = []
        for agg in aggregate_slots(slots, uniform=uniform):
            natdex = index_to_natdex.get(agg["species_index"])
            if natdex is None:
                continue                    # a randomizer can emit an unused index
            entries.append({
                "species_id": natdex,
                "name": species_name(natdex),
                "rate": agg["rate"],
                "min_level": agg["min_level"],
                "max_level": agg["max_level"],
            })
        if entries:
            # Matches tools/gen_gen1_encounters.py exactly: most likely first, ties broken
            # by national dex number. Any other tiebreak silently reorders rows the UI and
            # its tests already pin.
            entries.sort(key=lambda e: (-e["rate"], e["species_id"]))
            block[method] = entries

    for map_id in sorted(content["wild"]):
        area_id = map_to_area.get(map_id)
        if not area_id:
            continue
        rec = content["wild"][map_id]
        for method, key in (("Grass", "grass"), ("Water", "water")):
            if rec[key]:
                add(area_id, method, rec[key]["slots"], map_id)

    fishing = content.get("fishing") or {}
    for map_id, entries in sorted((fishing.get("super_rod") or {}).items()):
        area_id = map_to_area.get(map_id)
        if area_id:
            add(area_id, "Super Rod", entries, uniform=True)
    return out


_FLOOR_LABELS: dict[str, str] | None = None


def _floor_labels() -> dict[str, str]:
    """map id -> " B1F", as published by tools/gen_gen1_encounters.py.

    Loaded lazily and cached. An absent file degrades to unsuffixed methods, which is the
    pre-floor behaviour: worse, but not wrong.
    """
    global _FLOOR_LABELS
    if _FLOOR_LABELS is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "..", "data", "games", "gen1_rby", "floor_labels.json")
        try:
            with open(os.path.normpath(path), encoding="utf-8") as f:
                _FLOOR_LABELS = json.load(f)
        except (OSError, ValueError):
            _FLOOR_LABELS = {}
    return _FLOOR_LABELS


def content_fingerprint(variant: str, wild: dict, fishing: dict) -> str:
    """A digest of the tables a CLIENT can report, so both sides compute the same value.

    Deliberately narrower than ``profile_hash``: it covers only wild encounters and fishing,
    because that is all the Lua client reads out of the cartridge. A hash that
    included base stats could never be reproduced by a client and so could not be used to
    answer the question this exists for -- "is the player running the ROM we made for them?"

    Sensitive to species AND level in every slot, so a different seed produces a different
    fingerprint. Insensitive to anything outside those tables, so a companion patch that
    changes the file's SHA-1 without touching them still matches.
    """
    body = {
        "variant": variant,
        "wild": {str(k): v for k, v in sorted(wild.items())},
        "fishing": {
            "old_rod": fishing.get("old_rod") or [],
            "good_rod": fishing.get("good_rod") or [],
            "super_rod": {str(k): v for k, v in sorted((fishing.get("super_rod") or {}).items())},
        },
    }
    # pureRGB-only table; adding an always-empty key here would change every EXISTING
    # (vanilla) fingerprint's hash for no reason, so it is included only when present.
    if fishing.get("good_rod_ocean"):
        body["fishing"]["good_rod_ocean"] = fishing["good_rod_ocean"]
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fingerprint_rom(rom: bytes) -> str:
    """The same fingerprint, computed from a ROM file rather than a client's report.

    The Manager uses this when it builds a pair; the server uses ``content_fingerprint`` on
    what the client sends. They must agree, which tests/unit/test_gen1_admission.py checks
    by running both over the same ROM.
    """
    ident = identify(rom)
    return content_fingerprint(ident["variant"], scan_wild(rom), scan_fishing(rom))


INTERNAL_POKEMON_COUNT = 190        # UPR's gen1_offsets.ini agrees; pret's table is 190 long


def scan_pokedex_order(rom: bytes) -> dict[int, int]:
    """internal species index -> national dex number, read from the cartridge.

    EVERYTHING depends on this mapping. A wild slot, a party mon and a box mon all store the
    INTERNAL index, and every species name, sprite and rule lookup goes through the
    conversion to dex. data/games/gen1_rby/species_index.json ships it, and if a randomizer
    ever reordered it every one of those would silently name the wrong Pokemon.

    UPR reads PokedexOrder and never writes it -- there is no code path that does -- so the
    shipped table stays correct for randomized ROMs. That is a claim about someone else's
    software, so tests/unit/test_gen1_rom_scan.py checks it against real randomized output
    rather than trusting it.

    Entries of 0 are the MissingNo holes and are omitted.
    """
    _ident, syms = _syms_for(rom)
    base = sym_to_offset(syms["PokedexOrder"])
    if base + INTERNAL_POKEMON_COUNT > len(rom):
        raise RomScanError("PokedexOrder runs past the end of the ROM")
    out: dict[int, int] = {}
    for i in range(INTERNAL_POKEMON_COUNT):
        dex = rom[base + i]
        if dex == 0:
            continue                # an unused internal index
        if dex > 151:
            raise RomScanError(
                f"PokedexOrder entry {i + 1} is {dex}, which is not a Gen 1 dex number")
        out[i + 1] = dex            # the table is indexed from internal index 1
    return out
