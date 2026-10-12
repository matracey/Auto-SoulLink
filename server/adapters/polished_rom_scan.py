"""Read-only decoder of a Polished Crystal 3.2.3 ROM's rule and wild tables, for the randomizer
pipeline (server/upr_pipeline.py `_check_content_polished`) and its tests.

Formats (polishedcrystal@3fa43192, tag v3.2.3; docs/polished/UPR_HANDLER.md §3):
  BaseData        34-byte records (BASE_DATA_SIZE, constants/pokemon_data_constants.asm:35): stats x6,
                  types x2, catch rate, base exp, items x2, gender|hatch, abilities x3, growth rate,
                  egg groups, EV yield x2, 14 TM/HM/tutor bit bytes. Records 1..291 are the species,
                  292..337 the variant forms (NUM_VARIANT_FORMS = 46).
  EvosAttacks     per record: evolutions [method, param (a 2-byte species for EVOLVE_PARTY), extra
                  (EVOLVE_HOLDING / EVOLVE_STAT), target dp] up to $FF, then (level, move) up to $FF.
  wild            eight map-keyed tables ended by $FF: grass = map (2), 3 rates, morn/day/nite x 7 x
                  (level, dp); water = map (2), rate, 3 x (level, dp) (data/wild/*.asm).
  FishGroups      15 rows of 2 chance bytes + 3 rod pointers; a rod list is (chance, dp, level) up to
                  chance $FF inclusive (data/wild/fish.asm).
  TreeMons        10 set pointers; a set is a common then a rare (chance, dp, level) list each ended
                  by $FF, the last set (ROCK) has only one (data/wild/treemons.asm).
  ContestMons     12 x (chance, dp, min, max), no terminator (data/wild/bug_contest_mons.asm).
  script sites    data/games/polished_crystal/script_sites.json: the species LOW byte of each resolved givepoke ($2F) /
                  loadwildmon ($5C) command (macros/scripts/events.asm), then its form byte and level byte.
  NPCTrades       9 x 33 bytes (NPCTRADE_STRUCT_LENGTH): dialog, dp wanted, dp given, nickname x11, DVs x3,
                  personality, ball, item, dw OT id, OT name x8, one skipped byte (constants/npc_trade_constants.asm).
  TrainerGroups   per class a 3-byte bank/address of size-prefixed records (data/trainers/macros.asm end_trainer):
                  db size, name@, db flags, then per mon level, dp and the flag-selected item, DVs, personality,
                  nickname@, EVs, 4 moves (engine/battle/read_trainer_party.asm); INVER's entry points at WRAM.
A dp species is (LOW, HIGH << 5 | form): 9-bit species, form in bits 0-4 (macros/data.asm:89-91). Forms (owner
ruling 2026-10-04): a variant form (forms_index.json, records 292..337) is its own mon, a cosmetic form is its
species; ``placed()`` lists every randomizable dp with its effective species (polished_codec.effective_species).

Offsets come from the generated UPR INI (data/games/polished_crystal/upr_polished_entries.ini, from the release
.sym). The SLink overlay (patch/dist/SLink-Polished.ups) touches only ROM0 $0070, the DelayFrame
lead-in, bank $7E and the header, so the same offsets hold on the overlay and on UPR's output of it.
``pinned=True`` (the default) admits only those two pinned artifacts; ``pinned=False`` exists for
the content check, which must read a randomized output.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from pathlib import Path

from server.adapters.gen2_rom_scan import RomScanError, _Cursor
from server.adapters.polished_codec import effective_species, is_variant_form

REPO = Path(__file__).resolve().parents[2]
TRAINERS = REPO / "data" / "games" / "polished_crystal" / "trainers.json"
INI = REPO / "data" / "games" / "polished_crystal" / "upr_polished_entries.ini"
SCRIPT_SITES = REPO / "data" / "games" / "polished_crystal" / "script_sites.json"
SCRIPT_OPCODES = {"givepoke": 0x2F, "loadwildmon": 0x5C}
STARTER_SOURCES = ("maps/ElmsLab.asm:215", "maps/ElmsLab.asm:255", "maps/ElmsLab.asm:293")   # UPR's order
NPC_TRADES, NPC_TRADE_LENGTH = 9, 33           # NUM_NPC_TRADES, NPCTRADE_STRUCT_LENGTH
EXT_SPECIES = 0x20                              # HIGH byte bit 5 = species bit 8; the rest is gender/form
ROM_SIZE = 0x200000
HEADER_TITLE = b"PKPCRYSTAL"
BASE_DATA_SIZE = 34
WILD_TABLES = ("JohtoGrass", "JohtoWater", "KantoGrass", "KantoWater",
               "OrangeGrass", "OrangeWater", "SwarmGrass", "SwarmWater")
GRASS_SLOTS = 7                                 # per time of day (wildmon rows)
FISH_GROUPS = 15                                # NUM_FISHGROUPS, constants/map_data_constants.asm:68
TREE_SETS = 10                                  # NUM_TREEMON_SETS, constants/pokemon_data_constants.asm:381
CONTEST_MONS = 12                               # (ContestMonsEnd - ContestMons) / 5
EVOLVE_HOLDING, EVOLVE_STAT, EVOLVE_PARTY = 4, 6, 10   # constants/pokemon_data_constants.asm:312-323
SPECIES_COUNT = 291                             # NUM_SPECIES (constants/pokemon_constants.asm:317)
EGG, UNUSED_SPECIES = 0xFF, 0x100               # constants/pokemon_constants.asm:280-281
GENDER_EGG = 0xC0                               # dp HIGH bits 7-6 (constants/pokemon_data_constants.asm:242-243)
# TRAINERTYPE_* = 1 << TRNTYPE_* (constants/trainer_data_constants.asm:43-59)
TR_ITEM, TR_EVS, TR_DVS, TR_PERSONALITY, TR_NICKNAME, TR_MOVES = 2, 4, 8, 16, 32, 64
INVER_CLASS = 122                               # INVER ($7a): TrainerGroups points at wInverGroup in WRAM
TEXT_END = 0x53                                 # '@' (charmap.asm:43)
_MAX_ROWS = 512                                 # bound on every terminator-ended walk


def placeable(species: int, form: int) -> bool:
    """What a randomizer may write into a dp: a real species with NO_FORM / PLAIN_FORM, or a variant form.
    A cosmetic form is never written (it is the species: a site keeps its own cosmetic form unless re-rolled)."""
    return (1 <= species <= SPECIES_COUNT and species not in (EGG, UNUSED_SPECIES)
            and (form <= 1 or is_variant_form(species, form)))


@functools.cache
def trainer_class_counts() -> tuple[int, ...]:
    """Trainers per class 1..147, from the generated pack (the fork INI's TrainerClassCounts are these)."""
    named = json.loads(TRAINERS.read_text(encoding="utf-8"))["named_trainers"]
    return tuple(len(named.get(str(c), {})) for c in range(1, len(named) + 1))


@functools.cache
def offsets() -> dict[str, int]:
    """Every numeric key of the generated INI (comments stripped)."""
    out = {}
    for line in INI.read_text(encoding="utf-8").splitlines():
        key, eq, value = line.split("//")[0].strip().partition("=")
        if eq and re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", value.strip()):
            out[key.strip()] = int(value.strip(), 0)
    return out


@functools.cache
def pinned_sha1s() -> dict[str, str]:
    """SHA1 -> kind for the generated runtime pins in the Polished game pack."""
    path = REPO / "data" / "games" / "polished_crystal" / "companion_abi.json"
    try:
        pins = json.loads(path.read_text(encoding="utf-8"))["rom_sha1"]
        clean, overlay = pins["clean"], pins["overlay"]
        if (not all(isinstance(pin, str) and re.fullmatch(r"[0-9a-f]{40}", pin)
                    for pin in (clean, overlay)) or clean == overlay):
            raise ValueError("invalid clean/overlay SHA1 pins")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RomScanError(f"invalid Polished companion runtime pack {path}: {exc}") from exc
    return {clean: "clean", overlay: "overlay"}


@functools.cache
def script_sites() -> tuple[dict, ...]:
    """The resolved script sites (offset known on the release build; the SLink overlay moves none)."""
    data = json.loads(SCRIPT_SITES.read_text(encoding="utf-8"))
    return tuple(site for site in data["sites"] if site["offset"] is not None)


def identify(rom: bytes) -> dict | None:
    """{"kind": "clean" | "overlay"} for a pinned Polished artifact, else None (by sha1 only)."""
    kind = pinned_sha1s().get(hashlib.sha1(rom).hexdigest())
    return {"kind": kind} if kind else None


def _dp(lo: int, hi: int) -> list[int]:
    return [lo | (hi & 0x20) << 3, hi & 0x1F]


class Rom:
    def __init__(self, rom: bytes, *, pinned: bool = True):
        if len(rom) != ROM_SIZE or rom[0x134:0x134 + len(HEADER_TITLE)] != HEADER_TITLE:
            raise RomScanError("not a 2 MiB PKPCRYSTAL cartridge")
        if pinned and identify(rom) is None:
            raise RomScanError("ROM SHA1 is neither the pinned Polished Crystal 3.2.3 release nor its SLink overlay")
        self.rom, self.o = rom, offsets()
        self.records = self.o["SpeciesCount"] + self.o["VariantFormCount"]

    def _at(self, offset: int, label: str) -> _Cursor:
        return _Cursor(self.rom, offset, label)

    def _pointer(self, table: int, word: int, label: str) -> _Cursor:
        if not 0x4000 <= word < 0x8000:
            raise RomScanError(f"{label}: pointer {word:04X} is not a ROMX address")
        return self._at(table // 0x4000 * 0x4000 + word - 0x4000, label)

    def _record(self, index: int) -> int:
        if type(index) is not int or not 1 <= index <= self.records:
            raise RomScanError(f"record {index!r} outside 1..{self.records}")
        return index

    def base_stats(self, index: int) -> dict:
        raw = self._at(self.o["PokemonStatsOffset"] + (self._record(index) - 1) * BASE_DATA_SIZE,
                       f"BaseData {index}").take(BASE_DATA_SIZE)
        return {"stats": list(raw[0:6]), "types": list(raw[6:8]), "catch_rate": raw[8], "base_exp": raw[9],
                "items": list(raw[10:12]), "gender_hatch": raw[12], "abilities": list(raw[13:16]),
                "growth_rate": raw[16], "egg_groups": raw[17], "ev_yield": list(raw[18:20]),
                "tmhm": list(raw[20:34])}

    def evos_attacks(self, index: int) -> dict:
        table = self.o["PokemonMovesetsTableOffset"]
        word = self._at(table + (self._record(index) - 1) * 2, "EvosAttacksPointers").word()
        cur = self._pointer(table, word, f"EvosAttacks {index}")
        evolutions, learnset = [], []
        while (method := cur.byte()) != 0xFF:
            if not 1 <= method <= EVOLVE_PARTY or len(evolutions) > 16:
                raise RomScanError(f"EvosAttacks {index}: unknown evolution method {method}")
            params = _dp(*cur.take(2))[:1] if method == EVOLVE_PARTY else [cur.byte()]
            if method in (EVOLVE_HOLDING, EVOLVE_STAT):
                params.append(cur.byte())
            evolutions.append([method, *params, *_dp(*cur.take(2))])
        while (level := cur.byte()) != 0xFF:
            if len(learnset) > 128:
                raise RomScanError(f"EvosAttacks {index}: learnset has no terminator")
            learnset.append([level, cur.byte()])
        return {"evolutions": evolutions, "learnset": learnset}

    def wild(self) -> dict[str, list[dict]]:
        """table -> [{map: [group, number], rates, slots: [[level, species, form], ...]}]; grass rows
        carry 21 slots (morn, day, nite x 7), water rows 3."""
        out = {}
        for table in WILD_TABLES:
            cur, rows = self._at(self.o[f"{table}WildMonsOffset"], f"{table}WildMons"), []
            grass = table.endswith("Grass")
            while cur.rom[cur.position] != 0xFF:
                if len(rows) > _MAX_ROWS:
                    raise RomScanError(f"{table}WildMons has no terminator")
                head = cur.take(2)
                rates = list(cur.take(3 if grass else 1))
                slots = [[s[0], *_dp(s[1], s[2])] for s in (cur.take(3) for _ in range(3 * GRASS_SLOTS if grass else 3))]
                rows.append({"map": list(head), "rates": rates, "slots": slots})
            out[table] = rows
        return out

    def _chance_list(self, cur: _Cursor, *, inclusive: bool) -> list[list[int]]:
        """(chance, dp, level) rows; a fishing list ends ON its chance-$FF row, a tree list after
        a lone $FF byte."""
        rows = []
        while True:
            if len(rows) > _MAX_ROWS:
                raise RomScanError(f"{cur.label}: no terminator")
            if not inclusive and cur.rom[cur.position] == 0xFF:
                cur.take(1)
                return rows
            chance, lo, hi, level = cur.take(4)
            rows.append([chance, *_dp(lo, hi), level])
            if inclusive and chance == 0xFF:
                return rows

    def fishing(self) -> list[dict]:
        table, out = self.o["FishingWildsOffset"], []
        for g in range(FISH_GROUPS):
            row = self._at(table + g * 8, f"FishGroups {g}")
            chances = list(row.take(2))
            rods = [self._chance_list(self._pointer(table, row.word(), f"fish group {g} rod {r}"), inclusive=True)
                    for r in range(3)]
            out.append({"chances": chances, "rods": rods})
        return out

    def trees(self) -> list[list[list[list[int]]]]:
        table, out = self.o["TreemonWildsOffset"], []
        for s in range(TREE_SETS):
            cur = self._pointer(table, self._at(table + s * 2, "TreeMons").word(), f"TreeMons set {s}")
            out.append([self._chance_list(cur, inclusive=False) for _ in range(1 if s == TREE_SETS - 1 else 2)])
        return out

    def contest(self) -> list[list[int]]:
        cur = self._at(self.o["BCCWildsOffset"], "ContestMons")
        return [[r[0], *_dp(r[1], r[2]), r[3], r[4]] for r in (cur.take(5) for _ in range(CONTEST_MONS))]

    def _species(self, lo: int, hi: int, label: str) -> int:
        species = lo | (hi & EXT_SPECIES) << 3
        if not 1 <= species <= self.o["SpeciesCount"]:
            raise RomScanError(f"{label}: species {species} outside 1..{self.o['SpeciesCount']}")
        return species

    def scripted_mons(self) -> list[dict]:
        """Species, form and level at every resolved script site. UPR rewrites only the species bytes (keeping
        the site's gender/egg bits; the form is the site's own unless re-rolled to a plain species or a variant
        form) and, under a static level setting, the level: so the opcode and the gender/egg bits must still be
        the site's, and a form other than the site's must be placeable, or the command moved -> refused."""
        out = []
        for site in script_sites():
            at, label = site["offset"], f"script site {site['source']}"
            op, lo, hi, level = self._at(at - 1, label).take(4)
            if op != SCRIPT_OPCODES[site["kind"]] or hi & GENDER_EGG != site["form"] & GENDER_EGG:
                raise RomScanError(f"{label}: the {site['kind']} command there differs (moved or rewritten)")
            species, form = self._species(lo, hi, label), hi & 0x1F
            if (species, form) != (site["species"], site["form"] & 0x1F) and not placeable(species, form):
                raise RomScanError(f"{label}: species {species} form {form} is no placeable mon (moved or rewritten)")
            if not 1 <= level <= 100:
                raise RomScanError(f"{label}: level {level} is not a plain level")
            out.append({"source": site["source"], "kind": site["kind"], "species": species, "form": form,
                        "level": level})
        return out

    def npc_trades(self) -> list[dict]:
        """Both species of each NPCTrades record, plus the fields UPR must leave alone."""
        out = []
        for i in range(NPC_TRADES):
            label = f"NPCTrades {i}"
            raw = self._at(self.o["TradeTableOffset"] + i * NPC_TRADE_LENGTH, label).take(NPC_TRADE_LENGTH)
            out.append({"trade_id": i, "requested": self._species(raw[1], raw[2], label), "requested_form": raw[2] & 0x1F,
                        "offered": self._species(raw[3], raw[4], label), "offered_form": raw[4] & 0x1F,
                        "nickname": raw[5:16].hex(), "dvs": list(raw[16:19]), "personality": raw[19], "ball": raw[20],
                        "item": raw[21], "ot_id": int.from_bytes(raw[22:24], "little"), "ot_name": raw[24:32].hex()})
        return out

    def trainer_mons(self) -> list[dict]:
        """Level, species and form of every trainer mon, walked as the fork handler walks TrainerGroups; a record
        that does not end exactly at its size byte is refused."""
        table, out = self.o["TrainerDataTableOffset"], []
        for c, count in enumerate(trainer_class_counts(), 1):
            if c == INVER_CLASS or not count:
                continue
            ptr = self._at(table + 3 * (c - 1), f"TrainerGroups {c}")
            bank, word = ptr.byte(), ptr.word()
            if not 0x4000 <= word < 0x8000:
                raise RomScanError(f"TrainerGroups {c}: pointer {bank:02X}:{word:04X} is not ROMX")
            cur = self._at(bank * 0x4000 + word - 0x4000, f"trainer class {c}")
            for t in range(count):
                end = cur.position + 1 + cur.byte()
                self._skip_text(cur)
                flags = cur.byte()
                while cur.position < end:
                    level, lo, hi = cur.take(3)
                    cur.take((1 if flags & TR_ITEM else 0) + (1 if flags & TR_DVS else 0)
                             + (1 if flags & TR_PERSONALITY else 0))
                    if flags & TR_NICKNAME:
                        self._skip_text(cur)
                    cur.take((1 if flags & TR_EVS else 0) + (4 if flags & TR_MOVES else 0))
                    out.append({"class": c, "trainer": t, "level": level, "species": lo | (hi & EXT_SPECIES) << 3,
                                "form": hi & 0x1F})
                if cur.position != end:
                    raise RomScanError(f"trainer {c}/{t + 1}: the record does not end at its size byte")
        return out

    @staticmethod
    def _skip_text(cur: _Cursor) -> None:
        for _ in range(_MAX_ROWS):
            if cur.byte() == TEXT_END:
                return
        raise RomScanError(f"{cur.label}: unterminated name")

    def placed(self) -> list[tuple[str, int, int, int]]:
        """(where, species, form, effective species) for every dp a randomizer may write: wild, fishing, tree and
        contest slots, the resolved script sites, both NPC trade sides and every trainer mon, in a fixed order, so
        a source and its output compare slot by slot. Fishing's species-0 time-of-day rows read as species 0."""
        out = []
        for table, rows in self.wild().items():
            out += [(f"{table} {r}/{s}", sp, f) for r, row in enumerate(rows) for s, (_lv, sp, f) in enumerate(row["slots"])]
        out += [(f"fish {g}/{rod}/{i}", e[1], e[2]) for g, group in enumerate(self.fishing())
                for rod, rows in enumerate(group["rods"]) for i, e in enumerate(rows)]
        out += [(f"tree {s}/{k}/{i}", e[1], e[2]) for s, lists in enumerate(self.trees())
                for k, rows in enumerate(lists) for i, e in enumerate(rows)]
        out += [(f"contest {i}", e[1], e[2]) for i, e in enumerate(self.contest())]
        out += [(m["source"], m["species"], m["form"]) for m in self.scripted_mons()]
        for t in self.npc_trades():
            out += [(f"trade {t['trade_id']} requested", t["requested"], t["requested_form"]),
                    (f"trade {t['trade_id']} offered", t["offered"], t["offered_form"])]
        out += [(f"trainer {m['class']}/{m['trainer']}", m["species"], m["form"]) for m in self.trainer_mons()]
        return [(where, sp, f, effective_species(sp, f)) for where, sp, f in out]

    def rule_tables(self) -> dict:
        """What the Soul Link rules read, per record 1..337: base data and EvosAttacks."""
        return {"base_stats": [self.base_stats(i) for i in range(1, self.records + 1)],
                "evos_attacks": [self.evos_attacks(i) for i in range(1, self.records + 1)]}

    def scan_all(self) -> dict:
        return {**self.rule_tables(), "wild": self.wild(), "fishing": self.fishing(), "trees": self.trees(),
                "contest": self.contest()}
