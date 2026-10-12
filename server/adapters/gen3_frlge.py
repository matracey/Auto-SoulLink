"""
server/adapters/gen3_frlge.py — Game adapter for Gen 3 (FRLG + Emerald + AP + Radical Red).

This is the reference adapter that wraps the existing pokemon_data.py module,
maintaining 100% backward compatibility with the current FRLG implementation.
"""

import hashlib
import json
import logging
import os
import re
from functools import cache

from server.data.items.gen3_vanilla import (
    EMERALD_OVERLAY as _EMERALD_ITEM_OVERLAY,
    ITEM_NAMES as _FRLG_ITEM_NAMES,
)
from server.pokemon_data import (
    CFRU_FORM_SPRITE_ID,
    GENDER_SYMBOL,
    _parse_pid_otid_key,
    ability_description as _ability_description,
    ability_name as _ability_name,
    base_form,
    gender_from_key_species,
    pid_otid_shiny,
    species_name as _species_name,
    species_types as _species_types,
    to_national as _to_national,
    type_name as _type_name,
)

from . import gen3_codec, gen3_rom_tables
from .base import GameAdapter, companion_required_reason, humanize_area_id

log = logging.getLogger(__name__)

_GAMES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "games"
)
_DATA_DIR = os.path.join(_GAMES_DIR, "gen3_frlge")

# rom_type -> the data pack whose profile.json pins the companion mailbox ABI (profile.native.ABI). The ABI
# is read from the pack, never hard-coded per title, so a later companion (the Emerald Expansion's) only needs
# its pack listed here and its exemption removed.
_COMPANION_PACKS = {"firered": "gen3_frlg", "leafgreen": "gen3_frlg", "emerald": "gen3_emerald",
                    "firered_rr": "gen3_rr"}


@cache
def _companion_abi(pack: str) -> int | None:
    path = os.path.join(_GAMES_DIR, pack, "profile.json")
    try:
        with open(path, encoding="utf-8") as fh:
            abi = json.load(fh)["native"]["ABI"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return abi if type(abi) is int and abi > 0 else None


# Gift/static encounter area_ids — Pokémon obtained here before Pokéballs.
_GIFT_AREAS = frozenset({
    "oaks_lab", "intro", "gift", "cinnabar_lab",
    "celadon_condominiums", "silph_co_7f", "saffron_dojo",
    "route_4_pokecenter",
})

# Gift areas where both players are guaranteed the SAME predetermined species
# (no player choice). These bypass clause checks entirely.
# Excludes starters (player choice), fossils (player choice), Hitmonlee/Hitmonchan (choice).
_FIXED_SPECIES_GIFTS = frozenset({
    "route_4_pokecenter",   # Magikarp from salesman
    "celadon_condominiums", # Eevee from Bill
    "silph_co_7f",          # Lapras
})

# Daycare areas — eggs picked up here are bred from deposited mons, not gifts.
_DAYCARE_AREAS = frozenset({
    "route5_pokemon_day_care",
    "four_island_pokemon_day_care",
})

# ── Emerald title data (docs/gen3_emerald/PLAN.md E3) ─────────────────────────────────
# The client sends area_map[group:num] (data/games/gen3_emerald/area_map.json): wild maps, plus a
# NAMED id for every statics.json map (FR/LG's precedent: gift areas like silph_co_7f, statics
# like navel_rock). The gift set is the pack's own gift_areas.ids, the list the client consumes,
# so the two cannot drift; the starter's wild route_101 is not in it (the starter links as
# gift_route_101 through gift_link_area, a choice gift). Fixed-species areas are statics.json's
# bypass_clauses rows: the Beldum/Wynaut/Castform gift areas and the Mew/Deoxys static areas
# (faraway_island, birth_island: no wild table, so the whole area is that one mon). No daycare
# id: the egg is handed over on wild Route 117 (pret data/maps/Route117/map.json:65), and a
# daycare id there would let an egg consume that route. A missing pack file leaves the sets empty rather than breaking the
# import for every game.
_EMERALD_DIR = os.path.join(_GAMES_DIR, "gen3_emerald")


def _emerald_json(name: str) -> dict:
    path = os.path.join(_EMERALD_DIR, name)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_emerald() -> tuple[frozenset[str], frozenset[str]]:
    area_map = _emerald_json("area_map.json")
    statics = _emerald_json("statics.json").get("entries", [])
    gifts = frozenset(_emerald_json("write_checkpoint.json").get("emerald", {})
                      .get("gift_areas", {}).get("ids", []))
    fixed = frozenset(area_map[e["map"]] for e in statics
                      if e["bypass_clauses"] and e["map"] in area_map)
    return gifts, fixed


_EMERALD_GIFT_AREAS, _EMERALD_FIXED_SPECIES_GIFTS = _load_emerald()
# The one Gen 3 move-table difference (EF-9): Nature Power's accuracy, pret pokeemerald
# src/data/battle_moves.h:3479 (.accuracy = 95) vs pokefirered's 0. Shown on the board.
_EMERALD_MOVE_ACCURACY = {267: 95}

# RR trainer table: trainer index → {name, class, party_size}
_RR_TRAINERS: dict[int, dict] = {}
_rr_trainers_path = os.path.join(_DATA_DIR, "rr_trainers.json")
if os.path.exists(_rr_trainers_path):
    with open(_rr_trainers_path) as _f:
        _raw_tr = json.load(_f)
        _RR_TRAINERS = {int(k): v for k, v in _raw_tr.get("trainers", {}).items()}

# RR priority trainer roster: 1-based runtime trainer ID → {name, class, party, area?}.
# Source: tools/gen_rr_priority_trainers.py merges the RR damage-calc
# normal.js sets (canonical party data) with the community boss spreadsheet
# (area mapping). Keys are stringified ints; values keep the same shape used
# by the dashboard's Upcoming Trainers widget.
_RR_PRIORITY_PARTIES: dict[int, dict] = {}
_RR_PRIORITY_BY_AREA: dict[str, list[int]] = {}
# Main-tab level-cap milestone progression. Used by the dashboard to flag
# Past / Current / Future fight variants relative to the player's highest
# party level — see Gen3Adapter.milestone_cap_for_label() below.
_RR_PRIORITY_MILESTONES_ORDER: list[dict] = []   # [{name, cap}, ...] in story order
_RR_PRIORITY_PRE_CAPS:  dict[str, int] = {}      # "Lt. Surge" → 34
_RR_PRIORITY_POST_CAPS: dict[str, int] = {}      # "Lt. Surge" → 44 (next pre cap)
_rr_priority_path = os.path.join(_DATA_DIR, "rr_priority_trainers.json")
if os.path.exists(_rr_priority_path):
    with open(_rr_priority_path, encoding="utf-8") as _f:
        _raw_pt = json.load(_f)
        _RR_PRIORITY_PARTIES = {
            int(k): v for k, v in (_raw_pt.get("parties") or {}).items()
        }
        _RR_PRIORITY_BY_AREA = {
            k: list(v) for k, v in (_raw_pt.get("trainers_by_area") or {}).items()
        }
        _ms = _raw_pt.get("milestones") or {}
        _RR_PRIORITY_MILESTONES_ORDER = list(_ms.get("order") or [])
        _RR_PRIORITY_PRE_CAPS  = {k: int(v) for k, v in (_ms.get("pre")  or {}).items()}
        _RR_PRIORITY_POST_CAPS = {k: int(v) for k, v in (_ms.get("post") or {}).items()}

def _load_trainer_table(path: str) -> dict:
    """Load the runtime trainer presentation from a pret-generated title pack."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        "titles": frozenset(raw.get("titles") or ()),
        "trainers": {int(k): v for k, v in (raw.get("trainers") or {}).items()},
        "trainers_by_area": {k: list(v) for k, v in (raw.get("trainers_by_area") or {}).items()},
    }


# These tables use the wire's gTrainers indexes directly, unlike RR's rr_trainers.json.
_FRLG_TRAINER_TABLE = _load_trainer_table(os.path.join(_DATA_DIR, "frlg_trainers.json"))
_EMERALD_TRAINER_TABLE = _load_trainer_table(os.path.join(_EMERALD_DIR, "emerald_trainers.json"))

# Rival trainer ID set for Radical Red (used by Rival Team Swap feature).
# Built at import time by scanning _RR_TRAINERS for entries whose name is
# "Terry" (RR's default rival name) and whose class is one of the rival
# classes (81/89/90 — Rival Early/Mid/Late).  Spot-check: 27 entries in
# RR4.1 spanning IDs 326-440 and 739-741 (post-game).  Class 98 (also
# labeled "Rival" in _RR_TRAINER_CLASS) is NOT used by Terry in the
# canonical table; filtering on Terry-by-name avoids false positives.
# The ids are WIRE ids (gTrainerBattleOpponent_A = the gTrainers index). rr_trainers.json's key k is
# gTrainers[k + 1] (its base 0x0823EAF0 is gTrainers 0x0823EAC8 + one 40-byte entry), the same
# offset trainer_info() applies; read from the RR ROM 2026-09-25: gTrainers[326..328] are Terry
# (class 81), [325] is Daisuke.
_RR_RIVAL_CLASSES = frozenset({81, 89, 90})
_RR_RIVAL_TRAINER_IDS: frozenset[int] = frozenset(
    tid + 1 for tid, tr in _RR_TRAINERS.items()
    if (tr.get("name") or "").strip() == "Terry"
    and tr.get("class") in _RR_RIVAL_CLASSES
)

# RR trainer class ID → display name
_RR_TRAINER_CLASS: dict[int, str] = {
    1: "Pokémon Trainer", 2: "Team Rocket", 3: "Team Rocket Boss",
    4: "Gym Leader", 5: "Pokémon Trainer", 7: "Champion",
    8: "Gym Leader", 9: "Pokémon Trainer", 10: "Champion",
    13: "Pokémon Trainer", 20: "Pokémon Trainer", 22: "Pokémon Trainer",
    25: "Elite Four", 27: "Elite Four", 32: "Professor",
    33: "Pokémon Trainer", 46: "Pokémon Trainer", 48: "Pokémon Trainer",
    49: "Pokémon Trainer", 57: "Youngster", 58: "Bug Catcher",
    59: "Lass", 60: "Sailor", 61: "Camper", 62: "Picnicker",
    63: "Poké Maniac", 64: "Super Nerd", 65: "Hiker",
    66: "Biker", 67: "Burglar", 68: "Fisherman",
    69: "Swimmer ♂", 70: "Cue Ball", 71: "Black Belt",
    72: "Gentleman", 73: "Beauty", 74: "Psychic",
    75: "Rocker", 76: "Juggler", 77: "Tamer",
    78: "Bird Keeper", 79: "Scientist", 80: "Ace Trainer",
    81: "Rival", 82: "Cooltrainer ♀", 83: "Team Rocket Boss",
    84: "Gym Leader", 85: "Team Rocket Grunt", 86: "Channeler",
    87: "Elite Four", 88: "Pokéfan", 89: "Rival",
    90: "Rival", 91: "Cooltrainer ♀",
    92: "Young Couple", 93: "Young Couple", 94: "Young Couple",
    95: "Young Couple", 96: "Young Couple",
    97: "Professor", 98: "Rival",
    99: "Aroma Lady", 100: "Battle Girl", 101: "Parasol Lady",
    102: "Pokémon Ranger", 103: "Twins", 104: "Ruin Maniac",
    105: "Lady", 106: "Painter",
}

# ROM map names scraped from the live RR ROM via test_map_names.lua + parse_map_names.py.
# Keys are "group:num" strings; values have a "name" field (mapsec-level display name).
# Used to resolve dynamic gift_<group>_<num> area_ids to human-readable names.
_ROM_MAP_NAMES: dict[str, dict] = {}
_rom_map_names_path = os.path.join(_DATA_DIR, "rom_map_names.json")
if os.path.exists(_rom_map_names_path):
    with open(_rom_map_names_path) as _f:
        _ROM_MAP_NAMES = json.load(_f)

# Manual overrides for special characters (apostrophes, accents, abbreviations)
_AREA_DISPLAY_OVERRIDES: dict[str, str] = {
    "mt_moon":           "Mt. Moon",
    "mt_ember":          "Mt. Ember",
    "digletts_cave":     "Diglett's Cave",
    "oaks_lab":          "Oak's Lab",
    "silph_co_7f":       "Silph Co. 7F",
    "silph_co":          "Silph Co.",
    "pokemon_mansion":   "Pokémon Mansion",
    "pokemon_tower":     "Pokémon Tower",
    "cerulean_cave":     "Cerulean Cave",
    "rock_tunnel":       "Rock Tunnel",
    "seafoam_islands":   "Seafoam Islands",
    "victory_road":      "Victory Road",
    "viridian_forest":   "Viridian Forest",
    "safari_zone_center":"Safari Zone",
    "safari_zone_east":  "Safari Zone East",
    "safari_zone_north": "Safari Zone North",
    "safari_zone_west":  "Safari Zone West",
    "power_plant":       "Power Plant",
    "berry_forest":      "Berry Forest",
    "icefall_cave":      "Icefall Cave",
    "dotted_hole":       "Dotted Hole",
    "pattern_bush":      "Pattern Bush",
    "lost_cave":         "Lost Cave",
    "birth_island":      "Birth Island",
    "navel_rock":        "Navel Rock",
    "water_labyrinth":   "Water Labyrinth",
    "altering_cave":     "Altering Cave",
    "ruin_valley":       "Ruin Valley",
    "sevault_canyon":    "Sevault Canyon",
    "saffron_dojo":      "Saffron Dojo",
    "route_4_pokecenter": "Route 4 Pokémon Center",
    "celadon_hotel":     "Celadon Hotel",
    "celadon_condominiums": "Celadon Condominiums",
    "cinnabar_lab":      "Cinnabar Lab",
    "rocket_hideout":    "Rocket Hideout",
    "rocket_warehouse":  "Rocket Warehouse",
    "dunsparce_tunnel":  "Three Isle Path",
    "monean_chamber":    "Monean Chamber",
    "liptoo_chamber":    "Liptoo Chamber",
    "weepth_chamber":    "Weepth Chamber",
    "dilford_chamber":   "Dilford Chamber",
    "scufib_chamber":    "Scufib Chamber",
    "rixy_chamber":      "Rixy Chamber",
    "viapois_chamber":   "Viapois Chamber",
    "tanoby_key":        "Tanoby Key",
    "pokemon_league":    "Pokémon League",
    "ss_anne":           "S.S. Anne",
}

# Radical Red repurposes some vanilla map slots
_AREA_DISPLAY_RR_OVERRIDES: dict[str, str] = {
    "monean_chamber":    "Oak's Lab",
}

# Emerald's own display overrides (Hoenn ids; the table above is Kanto's)
_AREA_DISPLAY_EMERALD_OVERRIDES: dict[str, str] = {
    "mt_pyre":           "Mt. Pyre",
    "cave_of_origin":    "Cave of Origin",
    "rustboro_city_devon_corp_2f":   "Devon Corp. 2F",
    "mossdeep_city_stevens_house":   "Steven's House",
    "route119_weather_institute_2f": "Weather Institute 2F",
}

# RR item names (loaded if available)
_RR_ITEMS: dict[int, str] = {}
_rr_items_path = os.path.join(_DATA_DIR, "rr_items.json")
if os.path.exists(_rr_items_path):
    with open(_rr_items_path) as _f:
        _raw_items = json.load(_f)
        # Support both nested {"items": {...}} and flat {id: name} formats
        _items_dict = _raw_items.get("items", _raw_items) if isinstance(_raw_items, dict) else {}
        _RR_ITEMS = {int(k): v for k, v in _items_dict.items() if k.isdigit()}

# RR wild encounter tables — area_id → {method → [entries]}
# Format: {"route_1": {"Day": [{"name":"Bidoof","species_id":452,"rate":20,"min_level":2,"max_level":4}]}}
_RR_ENCOUNTERS: dict[str, dict[str, list[dict]]] = {}
_rr_encounters_path = os.path.join(_DATA_DIR, "rr_encounters.json")
if os.path.exists(_rr_encounters_path):
    with open(_rr_encounters_path, encoding="utf-8") as _f:
        _RR_ENCOUNTERS = json.load(_f)

# Vanilla wild encounter tables for clean FireRed/LeafGreen/Emerald cartridges, one committed
# file per title (tools/gen_gen3_wild.py, from pinned pret source). Same shape as _RR_ENCOUNTERS
# and a randomized cartridge's own ingested table -- area_id -> method -> [entries].
_VANILLA_WILD: dict[str, dict[str, dict[str, list[dict]]]] = {}
for _title, _dir, _fname in (
    ("firered", _DATA_DIR, "firered_encounters.json"),
    ("leafgreen", _DATA_DIR, "leafgreen_encounters.json"),
    ("emerald", _EMERALD_DIR, "emerald_encounters.json"),
):
    _path = os.path.join(_dir, _fname)
    if os.path.exists(_path):
        with open(_path, encoding="utf-8") as _f:
            _VANILLA_WILD[_title] = json.load(_f)["encounters"]

# Archipelago carts (firered_ap/leafgreen_ap) are still clean FR/LG until ingested.
_VANILLA_TITLE = {"firered_ap": "firered", "leafgreen_ap": "leafgreen"}

# RR display name → damage-calc name, per kind (species/ability/item/move).
# Pinned by tests/unit/test_rr_calc_names.py against calc/calc/src/data/*.ts.
_RR_CALC_NAMES: dict[str, dict[str, str]] = {}
_calc_names_path = os.path.join(_DATA_DIR, "calc_names.json")
if os.path.exists(_calc_names_path):
    with open(_calc_names_path, encoding="utf-8") as _f:
        _RR_CALC_NAMES = {k: v for k, v in json.load(_f).items() if isinstance(v, dict)}

# RR reuses ability id "As One" for two distinct Calyrex Rider combos; the raw name
# alone can't tell them apart, so calc_name("ability", "As One") is ambiguous by the
# time it sees a string. Disambiguate by ability id instead, straight to the calc's
# own spelling (calc/calc/src/data/abilities.ts: 'As One (Glastrier)'/'As One (Spectrier)').
# Source: server/pokemon_data.py ABILITY_DESCRIPTIONS — id 73 "Both Unnerve and Grim
# Neigh" (Grim Neigh is Spectrier's own ability), id 77 "Both Unnerve and Moxie" (CFRU's
# reused stat-boost-on-KO ability id, overridden to display "Chilling Neigh" for natdex
# 896 Glastrier). Cross-checked against tools/gen_rr_priority_trainers.py's per-species
# table (Calyrex-Ice -> "As One (Glastrier)", Calyrex-Shadow -> "As One (Spectrier)").
_RR_AS_ONE_CALC_NAME: dict[int, str] = {
    73: "As One (Spectrier)",
    77: "As One (Glastrier)",
}

# Vanilla FRLG/Emerald display name → damage-calc Gen 3 name, per kind. Separate table
# (and separate calc generation -- RR runs the calc at gen 9, vanilla at gen 3): a vanilla
# ROM spelling can need a different calc name than RR's own patched dex uses for the same
# concept. Pinned by tests/unit/test_calc_names_multigen.py against calc/calc/src/data/*.ts.
_VANILLA_CALC_NAMES: dict[str, dict[str, str]] = {}
_calc_names_vanilla_path = os.path.join(_DATA_DIR, "calc_names_vanilla.json")
if os.path.exists(_calc_names_vanilla_path):
    with open(_calc_names_vanilla_path, encoding="utf-8") as _f:
        _VANILLA_CALC_NAMES = {k: v for k, v in json.load(_f).items() if isinstance(v, dict)}

# RR front sprites, vendored by tools/gen_rr_sprites.py as server/static/sprites/rr/<id>.png
# and served same-origin. Keyed by RR's own (CFRU) species ids, not the national dex.
_RR_SPRITE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "static", "sprites", "rr")
_RR_SPRITE_IDS: frozenset[int] = frozenset(
    int(f[:-4]) for f in (os.listdir(_RR_SPRITE_DIR) if os.path.isdir(_RR_SPRITE_DIR) else ())
    if f.endswith(".png") and f[:-4].isdigit())

# Vanilla FRLG item names (embedded — no circular import needed).
# Source: pret/pokefirered include/constants/items.h

# National-dex bounds used for sprite URL resolution.
_MAX_NATDEX = 1025      # PokeAPI national-dex sprite coverage ceiling
_GEN3_DEX_CAP = 386     # last Gen III dex no. (Deoxys) — has dedicated FRLG sprites
_SPECIES_EGG = 412      # CFRU SPECIES_EGG


def _rr_url(species_id: int) -> str:
    """Vendored RR front sprite for a CFRU species id."""
    return f"/static/sprites/rr/{species_id}.png"


def _pokeapi_url(dex: int) -> str:
    """PokeAPI front-sprite URL for a national-dex (or form-pid) number."""
    return (f"https://raw.githubusercontent.com/PokeAPI/sprites/master"
            f"/sprites/pokemon/{dex}.png")


def _gen3_url(nat: int, version: str) -> str:
    """PokeAPI title front-sprite URL (firered-leafgreen | emerald) for a Gen III dex number."""
    return (f"https://raw.githubusercontent.com/PokeAPI/sprites/master"
            f"/sprites/pokemon/versions/generation-iii/{version}/{nat}.png")


# Personality-value → nature, by (personality mod 25). Same table/derivation for RR and
# vanilla -- both keep the personality value in the same struct field. Moved here (was
# server.py's module-level _nature_from_key) so nature is an adapter fact: Gen 1/2 have no
# such struct field and the base class's calc_nature() default (None) covers them.
_NATURE_NAMES = (
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
    "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive",
    "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky",
)


class Gen3Adapter(GameAdapter):
    """Adapter for Gen 3: FireRed/LeafGreen, Emerald, Archipelago, and Radical Red.

    Wraps existing pokemon_data.py functions to implement the GameAdapter
    interface. This ensures full backward compatibility with the existing
    167-test suite.
    """

    def __init__(self, is_rr: bool = False, **kwargs):
        """Initialize with ROM variant flag.

        Args:
            is_rr: True for Radical Red / CFRU ROMs, False for vanilla/AP/Emerald.
        """
        self._is_rr = is_rr
        # The server passes the connecting client's rom_type (server.py get_adapter calls);
        # it selects the title's data: Emerald's gift sets, items, sprites, areas, calc sets.
        self._rom_type = kwargs.get("rom_type") or ""
        self._artifact_kind = kwargs.get("artifact_kind") or "clean"
        # This player's own cartridge tables (ingest_rom_content): None = none reported,
        # {} = reported but unreadable (shown as unavailable, never as the retail tables).
        self._rom_encounters: dict | None = None
        self._rom_trainers: dict | None = None
        self._emerald = self._rom_type == "emerald"
        profile = "Radical Red / CFRU" if is_rr else "vanilla / AP / Emerald"
        log.debug(f"[ADAPTER] Gen3Adapter initialized: profile={profile!r}")

    @property
    def game_id(self) -> str:
        return "gen3_frlge"

    @property
    def rom_type(self) -> str:
        """The rom_type this adapter was built for: a restored run whose saved rom_type differs
        is rebuilt (state.py load), since the title data hangs off it and not off game_id."""
        return self._rom_type

    @staticmethod
    def pairing_kind(kind: str) -> str:
        # The SLink-RR companion patch is applied per cartridge, exactly like Gen 1's
        # "named": a companion RR and a clean RR are the same layout and pair. The
        # committed kind stays "companion" -- only this comparison maps it.
        return {"named": "clean", "companion": "clean"}.get(kind, kind)

    # Owner 2026-10-02: FireRed, LeafGreen, Emerald and Radical Red require the companion. The AP
    # builds (firered_ap/leafgreen_ap) and the Emerald Expansion (rom_type emerald_expansion_*,
    # which inherits this method) are not listed, so they stay clean-admitted.
    _COMPANION_ROM_TYPES = frozenset(_COMPANION_PACKS)

    @classmethod
    def companion_refusal(cls, hello):
        # EVIDENCE from the cartridge, not the launcher's claim (review F1/F2): the hello carries
        # `companion_abi` only when this cartridge's own companion mailbox (signature + ABI) is live in
        # RAM (lua/gen3/native.lua companion_live), and it must equal the ABI the title's pack pins. That
        # also closes randomized-clean: a randomized cartridge declares "rand" on the wire whether or not
        # it carries the companion, so artifact_kind alone cannot tell them apart.
        rom_type = hello.get("rom_type")
        if rom_type not in cls._COMPANION_ROM_TYPES:
            return None
        pinned = _companion_abi(_COMPANION_PACKS[rom_type])
        if pinned is not None and hello.get("companion_abi") == pinned and type(hello.get("companion_abi")) is int:
            return None
        return companion_required_reason(rom_type)

    @classmethod
    def supports_randomized(cls, rom_type: str) -> bool:
        # Expansion has its own layout and remains outside this binding.
        return rom_type in ("firered", "leafgreen", "emerald")

    @classmethod
    def pairing_kind_for(cls, kind: str, rom_content: object) -> str:
        """Owner ruling 32: a `rand` cartridge whose trainers, wild tables and evolutions equal
        pret's (the Manager's contract fingerprint of the clean title) pairs as clean. Anything
        undecodable keeps `rand`, the stricter kind."""
        if kind == "rand" and rom_content:
            try:
                rom = parse_rom_content(rom_content)
                title = rom_title(rom)
                tables = decode_verified(rom, title)
                if gen3_rom_tables.gen3_content_fingerprint(tables) == CLEAN_CONTENT_SHA256[title]:
                    return "clean"
            except Exception:                         # noqa: BLE001 - fail closed to rand
                pass
        return cls.pairing_kind(kind)

    @classmethod
    def pairing_kind_for_title(cls, rom_type: str, kind: str, rom_content: object) -> str:
        """Do not normalize a rand declaration with another foundation's clean proof."""
        if kind == "rand" and rom_content:
            try:
                title = rom_title(parse_rom_content(rom_content))
                if (title == "emerald") != (rom_type == "emerald"):
                    return cls.pairing_kind(kind)
            except Exception:                         # noqa: BLE001 - unverified stays rand
                return cls.pairing_kind(kind)
        return cls.pairing_kind_for(kind, rom_content)

    def set_artifact_kind(self, kind: str) -> None:
        # "rand" (a UPR-randomized FR/LG, docs/gen3/research/randomized_gen3_design.md R1) makes
        # every trainer read come from this player's own cartridge, never the retail table.
        self._artifact_kind = kind or "clean"

    def supports_trade_recovery(self) -> bool:
        return self._rom_type in ("firered", "leafgreen", "emerald", "firered_ap", "leafgreen_ap", "firered_rr")

    def trade_unavailable_reason(self) -> str:
        # RR-DURABLE: only an RR client that did not declare hello trade_prepare (the old ABI1
        # UPS, which has no save witness) is refused by name; state.py applies that condition.
        return "Trade unavailable for Radical Red in this build." if self._is_rr else ""

    def refused_trade_recovery(self) -> str:
        return ("" if self.supports_trade_recovery() else
                f"Trade recovery extension unavailable for {self._rom_type or 'unbound Gen 3 cartridge'}")

    # ── GameRulesAdapter ─────────────────────────────────────────────────

    def is_gift_area(self, area_id: str) -> bool:
        return (area_id in (_EMERALD_GIFT_AREAS if self._emerald else _GIFT_AREAS)
                or area_id.startswith("gift_"))

    def is_fixed_species_gift(self, area_id: str) -> bool:
        # state.py runs gift_link_area first, so a gift received outside a gift area arrives as
        # gift_<area> (e.g. a Deoxys flagged gift on birth_island); strip it, as gen2_gsc does.
        # FR/RR's fixed ids are all gift areas, which gift_link_area never prefixes.
        return (area_id.removeprefix("gift_")
                in (_EMERALD_FIXED_SPECIES_GIFTS if self._emerald else _FIXED_SPECIES_GIFTS))

    def is_daycare_area(self, area_id: str) -> bool:
        return not self._emerald and area_id in _DAYCARE_AREAS

    @property
    def area_pack(self) -> str:
        """data/games/<dir> holding this title's area maps (the OBS/debug area catalog)."""
        return "gen3_emerald" if self._emerald else "gen3_frlge"

    def evo_family(self, species_id: int) -> int:
        return base_form(species_id, self._is_rr)

    def gender_from_key(self, key: str, species_id: int) -> str:
        return gender_from_key_species(key, species_id, self._is_rr)

    def species_types(self, species_id: int) -> tuple[int, int] | None:
        return _species_types(species_id, self._is_rr)

    def is_shiny(self, key: str) -> bool:
        """Gen III shiny: (tid ^ sid ^ p_upper ^ p_lower) < 8."""
        parsed = _parse_pid_otid_key(key)
        if parsed is None:
            return False
        return pid_otid_shiny(*parsed)

    def species_name(self, species_id: int) -> str:
        return _species_name(species_id, self._is_rr)

    def type_name(self, type_id: int) -> str:
        return _type_name(type_id)

    def rival_trainer_ids(self) -> set[int]:
        """Return the rival trainer IDs for the Rival Team Swap feature.

        Radical Red (CFRU): returns the precomputed Terry-by-name set
        (27 entries spanning Oak's Lab → Champion → post-game).
        Vanilla / AP / Emerald: returns empty — feature is RR-only for MVP.
        """
        if self._is_rr:
            return set(_RR_RIVAL_TRAINER_IDS)
        return set()

    def status_token(self, status_cond: int) -> str:
        """Gen 3 `status1` bitfield. Same ordering as html_render.status_icon_html — TOX is checked
        before PSN because Toxic sets both bits."""
        if not status_cond:
            return ""
        for mask, tok in ((0x07, "SLP"), (0x80, "TOX"), (0x08, "PSN"),
                          (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR")):
            if status_cond & mask:
                return tok
        return ""

    def reports_box_census(self) -> bool:
        """KEY-SCOPE-5: FR/LG and RR stamp each complete box scan with `pc_boxes_generation`.
        Pairs with the client stamp in lua/gen3/client.lua (Emerald T3 f4ec8c85); the two must
        land on master together. ponytail: Emerald shares this adapter and binds later."""
        return True

    def supports_info_panel(self) -> bool:
        """Companion builds carry a native SOULLINK info screen on each Gen 3 title."""
        return self._is_rr or self._rom_type in ("firered", "leafgreen", "emerald")

    def party_blob_size(self) -> int:
        """A full 100-byte boxmon, which is what the client already sends. This is the
        value `_ingest_party_blobs` used to hardcode, so behaviour here is unchanged."""
        return 100

    def supports_explode_mode(self) -> bool:
        """The bound Gen 3 titles coerce Explosion through their own battle engine.
        This does not require the companion's native trade capability."""
        return self._is_rr or self._rom_type in ("firered", "leafgreen", "emerald")

    # ── GamePresentationAdapter ──────────────────────────────────────────

    def sprite_html(self, species_id: int, form: int = 0) -> str:
        """Generate sprite <img> tag with vendored RR sprites + PokeAPI fallback.

        `form` is accepted for adapter-signature consistency but unused — Gen 3
        only has Unown letters, which share the same sprite.
        """
        if not species_id or species_id < 1:
            return ""

        # Egg — use Showdown egg sprite
        if species_id == _SPECIES_EGG:
            return ('<img class="mon-sprite" data-species="412" '
                    'src="https://play.pokemonshowdown.com/sprites/gen5/egg.png" '
                    'onerror="this.style.display=\'none\';" alt="Egg">')

        # Primary: vendored RR sprite (covers all RR species + forms)
        if self._is_rr and species_id in _RR_SPRITE_IDS:
            rr_url = _rr_url(species_id)
            fallback_url = None
            form_pid = CFRU_FORM_SPRITE_ID.get(species_id)
            if form_pid:
                fallback_url = _pokeapi_url(form_pid)
            else:
                nat = _to_national(species_id)
                if nat and 1 <= nat <= _MAX_NATDEX:
                    fallback_url = _pokeapi_url(nat)
            if fallback_url:
                return (f'<img class="mon-sprite" data-species="{species_id}" src="{rr_url}" '
                        f'onerror="if(this.src!==\'{fallback_url}\'){{this.src=\'{fallback_url}\';}}else{{this.style.display=\'none\';}}" '
                        f'alt="">')
            return (f'<img class="mon-sprite" data-species="{species_id}" src="{rr_url}" '
                    f'onerror="this.style.display=\'none\';" alt="">')

        # PokeAPI fallback: convert CFRU → NatDex for the URL
        form_pid = CFRU_FORM_SPRITE_ID.get(species_id)
        if form_pid:
            gen_url = _pokeapi_url(form_pid)
            return (f'<img class="mon-sprite" data-species="{species_id}" src="{gen_url}" '
                    f'onerror="this.style.display=\'none\';" alt="">')
        nat = _to_national(species_id)
        if not nat or nat < 1 or nat > _MAX_NATDEX:
            return ""
        gen_url = _pokeapi_url(nat)
        if nat <= _GEN3_DEX_CAP:
            frlg_url = _gen3_url(nat, "emerald" if self._emerald else "firered-leafgreen")
            return (f'<img class="mon-sprite" data-species="{nat}" src="{frlg_url}" '
                    f'onerror="if(this.src!==\'{gen_url}\'){{this.src=\'{gen_url}\';}}else{{this.style.display=\'none\';}}" '
                    f'alt="">')
        return (f'<img class="mon-sprite" data-species="{nat}" src="{gen_url}" '
                f'onerror="this.style.display=\'none\';" alt="">')

    def encounter_table(self, area_id: str) -> dict[str, list[dict]] | None:
        """Return wild encounter data for this area: this cartridge's own tables once
        ingested (randomized FR/LG/Emerald), else RR's shipped file, else the clean
        cartridge's own vanilla table (by title -- FireRed, LeafGreen and Emerald ship
        different tables, tools/gen_gen3_wild.py).

        Returns method → entries dict, or None for an unrecognized title or areas
        with no encounter data.
        """
        if self._rom_encounters is not None:
            return self._rom_encounters.get(area_id) or None
        if self._is_rr:
            return _RR_ENCOUNTERS.get(area_id) or None
        # A randomized cart with no (yet) ingested report shows nothing rather than the retail
        # table (same rule as _frlg_trainer_table: a randomized cartridge's tables are its own).
        if self._artifact_kind == "rand":
            return None
        title = _VANILLA_TITLE.get(self._rom_type, self._rom_type)
        return _VANILLA_WILD.get(title, {}).get(area_id) or None

    def sprite_src(self, species_id: int) -> str:
        """Return the best sprite URL for this species.

        For RR runs: the vendored sprite when there is one (correct CFRU/custom forms).
        Fallback: PokeAPI with CFRU→NatDex conversion.
        """
        if not species_id or species_id < 1:
            return ""
        if self._is_rr and species_id in _RR_SPRITE_IDS:
            return _rr_url(species_id)
        form_pid = CFRU_FORM_SPRITE_ID.get(species_id)
        if form_pid:
            return _pokeapi_url(form_pid)
        nat = _to_national(species_id)
        if nat and 1 <= nat <= _MAX_NATDEX:
            return _pokeapi_url(nat)
        return ""

    def ability_name(self, ability_id: int, species_id: int = 0) -> str:
        if self._is_rr and ability_id in _RR_AS_ONE_CALC_NAME:
            return _RR_AS_ONE_CALC_NAME[ability_id]
        return _ability_name(ability_id, self._is_rr, species_id)

    def ability_description(self, ability_id: int) -> str:
        return _ability_description(ability_id, self._is_rr)

    def _frlg_trainer_table(self) -> dict | None:
        """The vanilla trainer table for this cartridge, or None. Every vanilla trainer lookup goes
        through here. A randomized run (or any adapter that ingested its ROM) reads its own
        gTrainers (ingest_rom_content), and has NO table until then, or when that report was
        unreadable: retail parties beside a randomized cartridge are misinformation.
        """
        if self._is_rr:
            return None
        if self._artifact_kind == "rand" or self._rom_trainers is not None:
            return self._rom_trainers or None
        table = _EMERALD_TRAINER_TABLE if self._rom_type == "emerald" else _FRLG_TRAINER_TABLE
        if self._rom_type not in table.get("titles", ()):
            return None
        return table

    def _frlg_trainer(self, trainer_id: int) -> dict | None:
        table = self._frlg_trainer_table()
        return table["trainers"].get(trainer_id) if table else None

    def trainer_info(self, trainer_id: int) -> tuple[str, str]:
        """Resolve RR trainer name and class from 1-based trainer_id; vanilla FR/LG from the
        raw gTrainers index (rivals and the Champion print the player's rival name: "")."""
        if not self._is_rr:
            tr = self._frlg_trainer(trainer_id)
            return (tr["name"], tr["class"]) if tr else ("", "")
        if not _RR_TRAINERS:
            return ("", "")
        tr = _RR_TRAINERS.get(trainer_id - 1)
        if not tr:
            return ("", "")
        cls_id = tr.get("class", 0)
        cls_name = _RR_TRAINER_CLASS.get(cls_id, "")
        # Rival classes (81/89/90) — show class only, no personal name
        if cls_name == "Rival":
            return ("", cls_name)
        return (tr.get("name", "").strip(), cls_name)

    def trainers_for_area(self, area_id: str) -> list[int]:
        """Return runtime trainer IDs that appear in the given area.

        Source: data/games/gen3_frlge/rr_priority_trainers.json for RR, and
        the pret-generated title table's key trainers for vanilla FR/LG and Emerald.
        The returned IDs are the runtime IDs `trainer_info` takes and the Lua
        client's TRAINER_OPPONENT_ADDR read reports.
        """
        if not area_id:
            return []
        if not self._is_rr:
            table = self._frlg_trainer_table()
            return list(table["trainers_by_area"].get(area_id, [])) if table else []
        return list(_RR_PRIORITY_BY_AREA.get(area_id, []))

    def trainer_party(self, trainer_id: int) -> list[dict]:
        """Return the curated party for a trainer ID, or [] if unknown.

        Each entry has: species (str), level (int), and optionally nature,
        ability, item, moves (list[str]), evs (dict), ivs (dict). The
        species string is the calc-format name (e.g. "Geodude-Alola",
        "Charizard-Mega-Y") — callers needing a species_id should resolve
        it via the species table.
        """
        if not self._is_rr:
            tr = self._frlg_trainer(trainer_id)
            return [dict(m) for m in tr["party"]] if tr else []
        entry = _RR_PRIORITY_PARTIES.get(trainer_id)
        if not entry:
            return []
        return list(entry.get("party") or [])

    def milestone_cap_for_fight_label(self, fight_label: str) -> int | None:
        """Resolve a "Pre X" / "Post X" fight_label into a story-progression
        level cap, using the Main-tab milestone list.

        "Pre Lt. Surge"  → 34 (the cap when approaching that fight)
        "Post Lt. Surge" → 44 (the cap AFTER clearing it — next pre milestone)

        Returns None when the label doesn't match a known milestone (or the
        adapter is not in RR mode). Used by the dashboard's Upcoming Trainers
        widget to mark each fight variant as Past / Current / Future against
        the player's highest party mon level.
        """
        if not self._is_rr or not fight_label:
            return None
        s = fight_label.strip()
        # Match "Pre X" / "Post X" — milestone names may have spaces, dots,
        # or punctuation, so anchor on the leading "Pre"/"Post" keyword.
        m = re.match(r"(Pre|Post)[\s-]+(.+)", s, re.I)
        if not m:
            return None
        kind = m.group(1).lower()
        name = m.group(2).strip()
        table = _RR_PRIORITY_PRE_CAPS if kind == "pre" else _RR_PRIORITY_POST_CAPS
        # Lenient lookup: exact match first, then case-insensitive contains.
        if name in table:
            return table[name]
        n_lower = name.lower()
        for k, v in table.items():
            if k.lower() == n_lower or n_lower in k.lower():
                return v
        return None

    def trainer_brief(self, trainer_id: int) -> dict | None:
        """Return {name, class, party, area?, level_cap?} for a priority trainer.

        Convenience helper used by the dashboard's Upcoming Trainers widget:
        combines name/class lookup with party data in one call. Returns None
        when the trainer is not in the priority roster.
        """
        if not self._is_rr:
            return self._frlg_trainer_brief(trainer_id)
        entry = _RR_PRIORITY_PARTIES.get(trainer_id)
        if not entry:
            return None
        out = {
            "name":  entry.get("name", "") or "",
            "class": entry.get("class", "") or "",
            "party": list(entry.get("party") or []),
            "area":  entry.get("area", "") or "",
        }
        lc = entry.get("level_cap")
        if isinstance(lc, int):
            out["level_cap"] = lc
        for k in ("calc_label", "fight_label", "sprite_url"):
            v = entry.get(k)
            if v:
                out[k] = v
        return out

    def _frlg_trainer_brief(self, trainer_id: int) -> dict | None:
        tr = self._frlg_trainer(trainer_id)
        if not tr or not tr["party"]:
            return None
        out = {
            # a rival's in-game name is the player's choice; "Rival" groups its starter variants
            "name": tr["name"] or ("Rival" if tr.get("rival") else ""),
            "class": tr["class"],
            "party": [dict(m) for m in tr["party"]],
            "area": tr.get("area", ""),
        }
        for k in ("level_cap", "fight_label", "calc_label"):
            if tr.get(k):
                out[k] = tr[k]
        if self._emerald:
            # pokeemerald c65e93f2 Route103/scripts.inc:22-24 and
            # Route110/scripts.inc:386-388: MALE -> May, FEMALE -> Brendan.
            # UPR preserves these script identities even when names change.
            const = tr.get("const", "")
            if const.startswith("TRAINER_MAY_"):
                out["required_player_gender"] = 0
            elif const.startswith("TRAINER_BRENDAN_"):
                out["required_player_gender"] = 1
        return out

    def item_name(self, item_id: int) -> str:
        if not item_id:
            return ""
        if self._is_rr and item_id in _RR_ITEMS:
            return _RR_ITEMS[item_id]
        if self._emerald and item_id in _EMERALD_ITEM_OVERLAY:
            return _EMERALD_ITEM_OVERLAY[item_id]
        return _FRLG_ITEM_NAMES.get(item_id, f"Item #{item_id}")

    def calc_name(self, kind: str, name: str) -> str:
        if not name:
            return name
        table = _RR_CALC_NAMES if self._is_rr else _VANILLA_CALC_NAMES
        return table.get(kind, {}).get(name, name)

    def calc_profile(self) -> dict | None:
        """RR runs the calc at gen 9 with its own sets; vanilla FR/LG and Emerald at gen 3 with
        their vendored trainer sets (pret coverage per game: test_calc_trainer_sets.py)."""
        if self._is_rr:
            return {"gen": 9, "dex": "rr"}
        sets = ({"file": "Emerald.js", "var": "CUSTOMSETDEX_E"} if self._rom_type == "emerald"
                else {"file": "FRLG.js", "var": "CUSTOMSETDEX_FRLG"})
        return {"gen": 3, "dex": "vanilla", "sets": sets}

    def calc_nature(self, key: str) -> str | None:
        """Derive nature name from a monKey ('PERS_HEX:OTID_HEX...'). Same logic for RR
        and vanilla -- both use the personality value here (unlike Gen 1/2's DVs)."""
        try:
            return _NATURE_NAMES[int(key.split(":")[0], 16) % 25]
        except (ValueError, AttributeError):
            # No personality in the key (a "foe-N" enemy entry): unknown, not Hardy, so the
            # calc's trainer-set nature isn't masked by a made-up one.
            return None

    def calc_stats(self, detail: dict) -> dict | None:
        """Decode IVs/EVs/computed stats from the 100-byte party blob (gen3_codec,
        the same RR/vanilla substructure-order oracle the Rival Team Swap feature
        already decodes blob_hex with)."""
        blob_hex = detail.get("blob_hex")
        if not blob_hex:
            return None
        try:
            blob = bytes.fromhex(blob_hex) if isinstance(blob_hex, str) else blob_hex
            mon = gen3_codec.decode_party_mon(bytes(blob), rr=self._is_rr)
        except (ValueError, TypeError):
            return None

        def _short(d):
            return {"hp": d["hp"], "atk": d["attack"], "def": d["defense"],
                    "spa": d["sp_attack"], "spd": d["sp_defense"], "spe": d["speed"]}

        return {
            "ivs": _short(mon["ivs"]),
            "evs": _short(mon["evs"]),
            "stats": {"hp": mon["max_hp"], "atk": mon["attack"], "def": mon["defense"],
                      "spa": mon["sp_attack"], "spd": mon["sp_defense"], "spe": mon["speed"]},
        }

    def area_display_name(self, area_id: str) -> str:
        if not area_id:
            return area_id
        # RR overrides take highest priority
        if self._is_rr and area_id in _AREA_DISPLAY_RR_OVERRIDES:
            return _AREA_DISPLAY_RR_OVERRIDES[area_id]
        if self._emerald and area_id in _AREA_DISPLAY_EMERALD_OVERRIDES:
            return _AREA_DISPLAY_EMERALD_OVERRIDES[area_id]
        # Manual overrides (apostrophes, accents, abbreviations)
        if area_id in _AREA_DISPLAY_OVERRIDES:
            return _AREA_DISPLAY_OVERRIDES[area_id]
        # Dynamic gift area: "gift_<group>_<num>" → "Gift – <ROM map name>"
        if area_id.startswith("gift_"):
            parts = area_id[5:].split("_", 1)  # "10_11" → ["10", "11"]
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                # Kanto map names (RR ROM scrape); no Emerald producer emits gift_<g>_<n>
                entry = None if self._emerald else _ROM_MAP_NAMES.get(f"{parts[0]}:{parts[1]}")
                if entry and entry.get("name"):
                    return f"Gift \u2013 {entry['name']}"
                return "Gift"
            # Synthetic "gift_<area_id>" (e.g. gift_vermilion_city) -> name the host area.
            bare = area_id[5:]
            if bare and not bare.startswith("gift_") and not bare.isdigit():
                return f"Gift \u2013 {self.area_display_name(bare)}"
            return "Gift"
        # Fallback: humanize the area_id
        return humanize_area_id(area_id)

    def to_national_dex(self, species_id: int) -> int:
        return _to_national(species_id)

    def gender_symbol(self, gender: str) -> str:
        return GENDER_SYMBOL.get(gender, "")

    def form_sprite_id(self, species_id: int) -> int | None:
        return CFRU_FORM_SPRITE_ID.get(species_id)

    @property
    def memorial_box_index(self) -> int:
        # RR/CFRU: 25 boxes (0-indexed 0–24), memorial = index 24 (UI "Box 25"), fills downward
        # Vanilla/AP FRLG: 14 boxes (0-indexed 0–13), memorial = index 13 (UI "Box 14")
        return 24 if self._is_rr else 13

    # ── Move data ────────────────────────────────────────────────────────

    def move_name(self, move_id: int) -> str:
        from server.data.moves import move_name as _move_name
        return _move_name(move_id, generation=3, variant=("rr" if self._is_rr else "vanilla"))

    def move_data(self, move_id: int) -> dict | None:
        from server.data.moves import move_data as _move_data, move_name as _move_name
        variant = "rr" if self._is_rr else "vanilla"
        raw = _move_data(move_id, generation=3, variant=variant)
        if raw is None:
            return None
        name = _move_name(move_id, generation=3, variant=variant)
        type_id = raw.get("type", 0)
        return {
            "name": name,
            "type_id": type_id,
            "type_name": self.type_name(type_id),
            "power": raw.get("power", 0),
            "accuracy": (_EMERALD_MOVE_ACCURACY.get(move_id, raw.get("accuracy", 0))
                         if self._emerald else raw.get("accuracy", 0)),
            "pp": raw.get("pp", 0),
            "split": raw.get("split", 0),
        }

    def gym_badge_slugs(self, rom_type: str) -> list[tuple[int, str]]:
        if (rom_type or "").lower() == "emerald":
            return [
                (17, "Stone Badge"),
                (18, "Knuckle Badge"),
                (19, "Dynamo Badge"),
                (20, "Heat Badge"),
                (21, "Balance Badge"),
                (22, "Feather Badge"),
                (23, "Mind Badge"),
                (24, "Rain Badge"),
            ]
        # FireRed / LeafGreen / Radical Red → Kanto
        return super().gym_badge_slugs(rom_type)

    # ── Per-ROM content (randomized FR/LG/Emerald, design R2) ────────────

    def _read_rom_content(self, payload: dict) -> dict:
        rom = parse_rom_content(payload)
        title = rom_title(rom)
        if self._rom_type == "emerald" and title != "emerald":
            raise ValueError(f"rom_content title {title} is not an Emerald cartridge report")
        if self._rom_type in ("firered", "leafgreen") and title == "emerald":
            raise ValueError("Emerald rom_content is not an FR/LG cartridge report")
        return decode_verified(rom, title)

    def rom_content_fingerprint(self, payload: dict) -> str | None:
        if self._is_rr:
            return None
        # The Manager's contract value (server/upr_pipeline.py) over the same decode; a forbidden
        # cartridge is refused by name here too.
        return gen3_rom_tables.gen3_content_fingerprint(self._read_rom_content(payload))

    def ingest_rom_content(self, payload: dict) -> dict | None:
        """This cartridge's wild tables (area → method → entries), carrying its trainer table as
        `.trainers`, so the one existing adopt path (use_rom_encounters) takes both. Raises on a
        malformed payload and on a forbidden rule-table change (ForbiddenRomTables)."""
        if self._is_rr:
            return None
        tables = self._read_rom_content(payload)
        out = _RomTables(self._rom_encounter_tables(tables["wild_encounters"], tables["title"]))
        out.trainers = self._rom_trainer_table(tables)
        return out

    def refused_rom_content(self, payload: object, *, artifact_kind: str | None = None) -> str:
        kind = self._artifact_kind if artifact_kind is None else artifact_kind
        randomized = kind in ("rand", "rand_overlay")
        if randomized and not self.supports_randomized(self._rom_type):
            return (f"randomized cartridges are not supported for {self._rom_type}: "
                    "no randomized-ROM binding")
        if self._is_rr:
            return ""
        label = "Emerald" if self._emerald else "FR/LG"
        if randomized and not payload:
            return f"randomized {label} hello is missing rom_content; cartridge rules cannot be verified"
        try:
            self._read_rom_content(payload)
        except ForbiddenRomTables as exc:
            return str(exc)
        except Exception as exc:                      # noqa: BLE001 - failed proof must refuse rand
            if randomized:
                return f"randomized {label} rom_content could not be verified: {exc}"
            return ""
        return ""

    def use_rom_encounters(self, tables: dict | None) -> None:
        # The server adopts {} (a plain dict) when the report was unreadable: no trainers either.
        self._rom_encounters = tables
        self._rom_trainers = None if tables is None else getattr(tables, "trainers", {})

    def _rom_encounter_tables(self, wild: dict, title: str | None = None) -> dict:
        """Gen 1's shape and rules (gen1_rom_scan.build_encounter_tables): first map wins per
        area and method, slots aggregated per species, most likely first."""
        out: dict = {}
        area_map = _emerald_json("area_map.json") if (title or self._rom_type) == "emerald" else _frlg_area_map()
        for group_num in sorted(wild):
            area_id = area_map.get("{}:{}".format(*group_num))
            if not area_id:
                continue
            # ponytail: first header only; Altering Cave's other 8 sets need VAR_ALTERING_CAVE_WILD_SET.
            habitats = wild[group_num][0]
            block = out.setdefault(area_id, {})
            for habitat, methods in _WILD_METHODS:
                table = habitats.get(habitat)
                if not table:
                    continue
                mons = iter(table["mons"])
                for method, rates in methods:
                    slots = [(rate, next(mons)) for rate in rates]
                    if method in block:
                        continue
                    agg: dict[int, dict] = {}
                    for rate, mon in slots:
                        sp = mon["species"]
                        e = agg.setdefault(sp, {"species_id": sp, "name": self.species_name(sp),
                                                "rate": 0, "min_level": mon["min_level"],
                                                "max_level": mon["max_level"]})
                        e["rate"] += rate
                        e["min_level"] = min(e["min_level"], mon["min_level"])
                        e["max_level"] = max(e["max_level"], mon["max_level"])
                    block[method] = sorted(agg.values(), key=lambda e: (-e["rate"], e["species_id"]))
        return {area: block for area, block in out.items() if block}

    def _rom_trainer_table(self, tables: dict) -> dict:
        """frlg_trainers.json's shape, from the cartridge: party species, levels, held items and
        custom moves (ruling 31), trainer and class names (UPR can randomize both). Area, key,
        rival and fight_label stay pret's: they come from map scripts, which UPR does not move.
        No calc_label: the FRLG.js setdex describes retail parties (design risk 4). Sparse hello
        reports do not contain level-up learnsets, so they must never infer default moves, even
        when the report pairs as clean (RF-2 option B).
        """
        base_table = _EMERALD_TRAINER_TABLE if tables.get("title", self._rom_type) == "emerald" else _FRLG_TRAINER_TABLE
        pret = base_table.get("trainers", {})
        classes = tables["class_names"]
        trainers = {}
        for tid, tr in tables["trainers"].items():
            base = pret.get(tid, {})
            party = []
            for mon in tr["party"]:
                entry = {"species": self.calc_species(mon["species"]), "level": mon["level"]}
                if mon.get("held_item"):
                    entry["item"] = self.calc_name("item", self.item_name(mon["held_item"]))
                if "moves" in mon:
                    entry["moves"] = [self.calc_name("move", self.move_name(m)) for m in mon["moves"] if m]
                party.append(entry)
            t = {"name": "" if base.get("rival") else _pretty(tr["name"]),
                 "class": classes[tr["class"]] if tr["class"] < len(classes) else "",
                 "party": party}
            t.update({k: base[k] for k in ("const", "rival", "area", "key", "fight_label") if k in base})
            if t.get("key") and party:
                t["level_cap"] = max(m["level"] for m in party)
            trainers[tid] = t
        return {"titles": base_table.get("titles", frozenset()), "trainers": trainers,
                "trainers_by_area": base_table.get("trainers_by_area", {})}


# ── Randomized FR/LG: ROM content (docs/gen3/research/randomized_gen3_design.md §3, R2) ──

def default_moves(learnset, level: int) -> list:
    """GiveBoxMonInitialMoveset (pret src/pokemon.c): walk the (level, move) learnset up to
    `level`, skip a known move, push out the first when all four are full. Shared with
    tools/gen_gen3_trainers.py, which builds frlg_trainers.json with it."""
    moves: list = []
    for lv, mv in learnset:
        if lv > level:
            break
        if mv in moves:
            continue
        if len(moves) == 4:
            moves.pop(0)
        moves.append(mv)
    return moves


class ForbiddenRomTables(ValueError):
    """A cartridge whose rule tables differ from pret: Gen 1's forbidden set (owner ruling 31:
    evolutions, types, abilities, base stats). The server keeps pret's evo_family/species_types,
    so such a cartridge would be ruled wrongly; it is refused, never adopted."""


class _RomTables(dict):
    """area → method → entries (the encounter_table shape) plus `.trainers`."""
    trainers: dict


# Slot rates per method, pret src/wild_encounter.c:73-160 (ChooseWildMonIndex_*); fishing's 10
# slots are old rod 0-1, good rod 2-4, super rod 5-9.
_WILD_METHODS = (
    ("land", (("Grass", (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1)),)),
    ("water", (("Surfing", (60, 30, 5, 4, 1)),)),
    ("rock_smash", (("Rock Smash", (60, 30, 5, 4, 1)),)),
    ("fishing", (("Old Rod", (70, 30)), ("Good Rod", (60, 20, 20)),
                 ("Super Rod", (40, 40, 15, 4, 1)))),
)

# gen3_content_fingerprint of the pinned clean dumps (the value the Manager would record for
# them); tests/unit/test_gen3_rom_ingest.py recomputes both.
CLEAN_CONTENT_SHA256 = {
    "firered": "70693903debc1465942e4f1d5c9fba8cce7e9d03421f978dd359347a49d277ee",
    "leafgreen": "7ef4b95991a71ff59b86bcfa0685d274ed95b8d23929d18e0146ccdb2a53c2d0",
    "emerald": gen3_rom_tables.EMERALD_SPECIES_RULES_FACTS["clean_content_sha256"],
}
# sha256 of the clean FR and LG (identical) evolution table and of the rule fields of
# gSpeciesInfo, as projected by _evolutions_digest/_species_rules_digest.
# tests/unit/test_gen3_rom_ingest.py re-derives both from the pinned clean dumps.
# Emerald's independently extracted rule/evolution pins currently coincide by design:
# the fixed fields agree after title-specific Deoxys normalization. Its own facts file
# remains authoritative; equality is not permission to substitute these FR/LG constants.
_EVOLUTIONS_SHA256 = "cdbbae339af1f2c071349d709d92abae6b5915702f44f51011abc0b472cc7c5d"
_SPECIES_RULES_SHA256 = "bece12dddc1d36701f817930d8bb6effbd73213db94bec27ac4b96a70238287f"

SPECIES_INFO_SIZE = gen3_rom_tables.SPECIES_INFO_SIZE
_CONTENT_HEADS = ("gTrainers", "gWildMonHeaders", "gEvolutionTable", "gSpeciesInfo",
                  "gTrainerClassNames")
CLASS_NAME_SIZE = 13     # gTrainerClassNames[][TRAINER_CLASS_NAME_LENGTH + 1]
_DEOXYS = gen3_rom_tables.DEOXYS
# UPR writes FR's Attack / LG's Defense forme into Deoxys's row: the stats the game already uses
# (pret src/pokemon.c:1640-1661 sDeoxysBaseStats), HP/Atk/Def/Spe/SpA/SpD. Not a rule change.
_DEOXYS_NORMAL = gen3_rom_tables.DEOXYS_NORMAL
_DEOXYS_FORME = gen3_rom_tables.DEOXYS_FORME


@cache
def _frlg_area_map() -> dict:
    with open(os.path.join(_DATA_DIR, "area_map.json"), encoding="utf-8") as f:
        return json.load(f)


@cache
def _symbol(title: str, name: str) -> tuple[int, int]:
    """(address, size) of a pret symbol for this title (data/gen3/pret/poke<title>.sym)."""
    if title not in ("firered", "leafgreen", "emerald"):
        raise ValueError(f"unsupported Gen 3 title: {title!r}")
    path = gen3_rom_tables.SYMBOL_DIR / f"poke{title}.sym"
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[3] == name:
            return int(fields[0], 16), int(fields[2], 16)
    raise ValueError(f"{path}: no symbol {name}")


def parse_rom_content(payload: dict) -> dict[int, bytes]:
    """The hello `rom_content` (lua/gen3/rom_content.lua): {"tables": [{"addr": <GBA address>,
    "hex": "<hex>"}, ...], "fingerprint": "<sha1>"}, regions ascending and disjoint. Returns
    gen3_rom_tables' sparse input. Raises on anything else, including a `fingerprint` that is not
    the SHA-1 of the bytes shipped (recomputed, never trusted). That SHA-1 is transport integrity
    only; admission compares gen3_content_fingerprint (rom_content_fingerprint)."""
    rom = {}
    previous_end = None
    for row in payload["tables"]:
        addr, raw = row["addr"], bytes.fromhex(row["hex"])
        if not isinstance(addr, int) or isinstance(addr, bool) or not raw:
            raise ValueError("rom_content regions need an integer address and bytes")
        if previous_end is not None and addr < previous_end:
            raise ValueError("rom_content regions must be strictly ascending and non-overlapping")
        previous_end = addr + len(raw)
        rom[addr] = raw
    if not rom or _transport_sha1(rom) != payload["fingerprint"]:
        raise ValueError("rom_content fingerprint is not the SHA-1 of its bytes")
    title = rom_title(rom)
    referenced = gen3_rom_tables.rom_content_ranges(
        rom, {name: _symbol(title, name) for name in _CONTENT_HEADS})
    if any(not any(start <= addr and addr + len(raw) <= end for start, end in referenced)
           for addr, raw in rom.items()):
        raise ValueError("rom_content contains extra unreferenced ROM bytes")
    return rom


def _transport_sha1(rom: dict[int, bytes]) -> str:
    """lua/gen3/rom_content.lua's `fingerprint`: SHA-1 of the region bytes in ascending order."""
    return hashlib.sha1(b"".join(raw for _, raw in sorted(rom.items()))).hexdigest()


def rom_title(rom: dict[int, bytes]) -> str:
    """The one title whose five table heads the report covers. The payload
    names no title and the run adapter may be the partner's title (an FR+LG pair)."""
    reader = gen3_rom_tables._Rom(rom)

    def covered(addr, size):
        try:
            reader.read(addr, size, "title table head")
        except ValueError:
            return False
        return True

    titles = [t for t in ("firered", "leafgreen", "emerald")
              if all(covered(*_symbol(t, name)) for name in _CONTENT_HEADS)]
    if len(titles) != 1:
        raise ValueError(f"rom_content table heads match {titles or 'no'} FR/LG title or Emerald title")
    return titles[0]


def _evolutions_digest(evolutions: dict) -> str:
    return hashlib.sha256(json.dumps(sorted(evolutions.items())).encode()).hexdigest()


def _species_rules_digest(raw: bytes, title: str) -> str:
    """Rule fields, permitting UPR to fill only PINNED originally-empty ability slots."""
    return hashlib.sha256(gen3_rom_tables.normalised_species_rules(raw, title)).hexdigest()


def _pretty(name: str) -> str:
    # tools/gen_gen3_trainers.py pretty(): "{PKMN}" is glyphs 0x53 0x54 in the ROM.
    return name.replace("<$53><$54>", "POKéMON").strip().title()


def decode_verified(rom, title: str) -> dict:
    """gen3_rom_tables.decode_rom_tables plus the forbidden-table check and the ROM's trainer
    class names (`class_names`). `rom` is full ROM bytes or the sparse {address: bytes} map."""
    tables = gen3_rom_tables.decode_rom_tables(rom, title)
    reader = gen3_rom_tables._Rom(rom)
    bad = []
    facts = gen3_rom_tables.EMERALD_SPECIES_RULES_FACTS if title == "emerald" else {}
    evolutions_pin = facts["evolutions_sha256"] if title == "emerald" else _EVOLUTIONS_SHA256
    rules_pin = facts["normalised_species_rules_sha256"] if title == "emerald" else _SPECIES_RULES_SHA256
    if _evolutions_digest(tables["evolutions"]) != evolutions_pin:
        bad.append("evolutions")
    addr, size = _symbol(title, "gSpeciesInfo")
    if _species_rules_digest(reader.read(addr, size, "gSpeciesInfo"), title) != rules_pin:
        bad.append("types/abilities/base stats/growth rates/gender ratios")
    if bad:
        raise ForbiddenRomTables(f"{title} cartridge has randomized {' and '.join(bad)}, which "
                                 "SLink does not support (the server rules on pret's tables)")
    addr, size = _symbol(title, "gTrainerClassNames")
    raw = reader.read(addr, size, "gTrainerClassNames")
    tables["class_names"] = [_pretty(gen3_codec.decode_name(raw[i:i + CLASS_NAME_SIZE]))
                             for i in range(0, size, CLASS_NAME_SIZE)]
    tables["title"] = title
    return tables
