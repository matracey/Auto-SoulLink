"""
server/adapters/gen4_hgsspt.py — Game adapter for Gen 4 (HeartGold/SoulSilver/hg-engine).

One class serves two cartridges that do not share a memory layout (HeartGold/SoulSilver and the
hg-engine fork), so anything describing a CARTRIDGE (box geometry, the pairing foundation) is
keyed on the foundation rather than on the game_id they all share. Platinum is NOT served: it has
no routing row in server/adapters/__init__.py and its hello is refused by name, so this adapter
reads ONE data directory — the generated HeartGold/SoulSilver pack, data/games/gen4_hgss/. The
legacy Sinnoh tables (area_map_platinum.json, trainers_pt.json, encounters_pt.json and the
rp_*.json override chain) are gone.

Gen 4 uses National Pokédex IDs natively (1-493). Mon keys use the same
PID:OTID format as Gen 3. No CFRU/RR support.
"""

import json
import logging
import os

from server.data.items.gen4 import ITEM_CATALOGS as _GEN4_ITEM_CATALOGS
from server.pokemon_data import (
    GENDER_RATIO,
    GENDER_SYMBOL,
    NATIONAL_SPECIES_NAMES,
    _parse_pid_otid_key,
    ability_description as _ability_description,
    ability_name as _ability_name,
    natdex_base_form as _natdex_base_form,
    pid_otid_shiny,
    species_types as _species_types,
    to_cfru as _to_cfru,
    type_name as _type_name,
)

# `foundation_for_rom_type` is a NAME bound by this package's __init__, not a module, so the
# import below is only valid because __init__ binds that name (adapters/__init__.py:236)
# BEFORE it imports this file (:276). Moving either line raises here, loudly, rather than
# handing the adapter a second copy of the foundation table that could drift from the
# registry's — the pairing decision stays in one place.
from . import foundation_for_rom_type, gen4_codec
from .base import GameAdapter, humanize_area_id

log = logging.getLogger(__name__)

_GAMES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "games",
)
# The GENERATED HeartGold/SoulSilver pack: tools/gen_gen4_area_map.py (area map + gift split),
# tools/gen_gen4_trainers.py and tools/gen_gen4_encounters.py, all from the pinned
# pret/pokeheartgold clone. Same wire area ids the client reads. This is the adapter's ONLY data
# directory — the legacy data/games/gen4_hgsspt/ tables are deleted.
_HGSS_PACK_DIR = os.path.join(_GAMES_DIR, "gen4_hgss")

# Gift/static encounter area_ids — Pokémon obtained without requiring Pokéballs.
#
# The HGSS half is the generated pack's own list, not a hand-typed one:
# data/games/gen4_hgss/area_map.json `gift_areas.ids`. The rule is that file's `rules.gift_areas`:
# an id is an area a scripted starter/gift/egg/loan reaches where NO map in the area owns a
# wildEncounterBank, so a failed wild encounter there is impossible. Every candidate whose area DOES
# own a wild map is named in `gift_areas.on_wild_area` instead — a gift landing there is already
# handled by the base `gift_link_area` remap (`gift_<area>`), and exempting the whole area would
# swallow a real no_catch (server/state.py:3053) and quarantine its wild slot (:2365). The client
# builds the same predicate from the same `ids` (lua/gen4/inputs.lua Inputs.gift_area), so the two
# cannot drift on a pin bump.

def _load_hgss_gift_areas() -> frozenset[str]:
    """HGSS gift areas from the pack's `gift_areas.ids`; an EMPTY set when the pack cannot answer.

    Fail CLOSED, and never back to the old hand-typed list: a missing pack, an unreadable file, a
    `gift_areas` block that is not a list of non-empty strings, or an id the pack itself lists under
    `on_wild_area` all leave that area NOT exempt. Wrongly EXEMPTING an area is the dangerous
    direction — the exemption drops that area's no_catch and its wild-slot quarantine, so a genuine
    KO on Route 35 or Mt. Mortar would vanish and both players would keep a slot the run spent.
    Wrongly failing to exempt is cheap: the capture is flagged `gift`/`is_egg` on the wire
    (lua/gen4/poll_events.lua:306,384; server/state.py:2401) and `gift_link_area` still forms the
    standalone `gift_<area>` pair. This is the same refusal the client makes for a pack with no
    valid list (lua/gen4/inputs.lua:113-116).
    """
    path = os.path.join(_HGSS_PACK_DIR, "area_map.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        log.error("Gen 4: no usable HGSS gift-area pack at %s (%s) — no HGSS area is exempt from "
                  "no_catch and every HGSS gift links under gift_<area>", path, e)
        return frozenset()
    block = doc.get("gift_areas") if isinstance(doc, dict) else None
    ids = block.get("ids") if isinstance(block, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        log.error("Gen 4: %s .gift_areas.ids is not a list of non-empty strings (%r) — refused; no "
                  "HGSS area is exempt from no_catch", path, ids)
        return frozenset()
    # on_wild_area is the pack's own record of the candidates the generator removed BECAUSE the area
    # owns a wild-encounter map; it refuses to emit such a pack (check_gift_ids), so a hit is a
    # contradiction between two halves of one file and the id is dropped rather than trusted.
    on_wild_area = block.get("on_wild_area", {})
    if not isinstance(on_wild_area, dict) or not all(
        isinstance(area, str) and area for area in on_wild_area.values()
    ):
        log.error("Gen 4: %s .gift_areas.on_wild_area is not a mapping of non-empty strings - "
                  "refused; no HGSS area is exempt from no_catch", path)
        return frozenset()
    wild = set(on_wild_area.values())
    contradiction = sorted(set(ids) & wild)
    if contradiction:
        log.error("Gen 4: %s .gift_areas.ids covers wild-encounter areas %s — exempting one would "
                  "drop a real no_catch there; dropped", path, contradiction)
    return frozenset(i for i in ids if i not in wild)


_HGSS_GIFT_AREAS = _load_hgss_gift_areas()

# Pal Park is the one hand-typed gift id, and it is NOT a Platinum one. `pal_park` is a real
# HGSS/SS area — data/games/gen4_hgss/area_map.json owns it (MAPSEC_PAL_PARK, Kanto region) — and
# a mon migrated in arrives there with no Pokéballs and no
# wild encounter to fail, so it stays exempt.
#
# Every OTHER id the old `_PLATINUM_GIFT_AREAS` carried is GONE. Platinum is no longer routed
# (server/adapters/__init__.py `_ROM_TYPE_TO_GAME_ID` has no `platinum` / `renegade_platinum`
# row, so `foundation_for_rom_type` answers None and the hello guard refuses both by name), so
# twinleaf_town / sandgem_town / eterna_city / hearthome_city / iron_island / veilstone_city /
# route_212 could not reach this predicate from any cartridge the server serves, and `pal_park`
# is the only one of them the generated HGSS map owns at all — which is exactly what
# tests/unit/test_gen4_gift_areas_server.py walks, area by area.
_HGSS_EXTRA_GIFT_AREAS = frozenset({
    "pal_park",        # migrated mons (Kanto Pal Park), backs no wild encounters
})

# `gift` stays the fallback for an unmapped area nothing else names.
_GIFT_AREAS = _HGSS_GIFT_AREAS | _HGSS_EXTRA_GIFT_AREAS | frozenset({"gift"})


# Egg-pickup areas — locations where an NPC hands the player a Pokémon egg.
# These are fixed-species (the NPC always gives the same Pokémon's egg) and should
# bypass species/gender clauses, since both linked players receive the same species
# from the egg. Distinct from `_DAYCARE_AREAS` (player-bred eggs from breeding pair).
# HGSS is violet_city and only violet_city: the pack's acquisition inventory has exactly four
# `kind: "egg"` script sites, all in it — Mr. Pokémon's Togepi on MAP_VIOLET_POKEMART
# (scr_seq_0858_T22FS0101:53) and Mareep / Wooper / Slugma on MAP_VIOLET_POKECENTER_1F
# (scr_seq_0860_T22PC0101:79, :91, :103). NOT route_30: Mr. Pokémon's house is in Violet City and
# no acquisition site is in route_30 at all. NOT mt_mortar: Kiyo's Tyrogue is `kind: "gift"`
# (GiveMon SPECIES_TYROGUE, level 10) — a mon handed over, not an egg. The Spiky-eared Pichu in
# Ilex Forest is `kind: "special_gift"`, likewise not an egg. Note an egg site can sit in a wild
# area (violet_city owns an encounter bank), so membership here is independent of `_GIFT_AREAS`.
_EGG_PICKUP_AREAS = frozenset({
    # HGSS
    "violet_city",     # Togepi from Mr. Pokémon; Mareep/Wooper/Slugma in the Poké Center
})


# Daycare areas — eggs ORIGINATE from a breeding pair the player deposited.
# Treated separately from egg pickups: daycare eggs hatch into a random species
# determined by the parents' species/breeding rules, so the linked players may get
# different species. Clause logic uses this to decide whether to skip the egg.
# HGSS: the Day-Care Couple on Route 34 (Goldenrod) and the egg handoff in Goldenrod City.
_DAYCARE_AREAS = frozenset({
    # HGSS
    "route_34",          # Pokémon Day Care (Goldenrod outskirts)
    "goldenrod_city",    # parents handed off egg in town
})

# Gift areas with a forced, identical species (no player choice).
# Excludes starters, Odd Egg / random eggs, and variable Game Corner prizes.
# This is an AREA-level bypass of the species/dupes clause and of the pair-formation clause
# (server/state.py:2668 and :2771), so every id here is one where an ordinary wild catch in that
# area cannot exist either — it must not name a route. route_30 is gone for that reason: it has no
# acquisition at all (no acquisition.json script site resolves into it), so listing it only
# disabled both clauses for Route 30's grass.
_FIXED_SPECIES_GIFTS = frozenset({
    "dragons_den",     # Dratini from Elder
    "mt_mortar",       # Tyrogue from Kiyo
    "ilex_forest",     # Spiky-eared Pichu (event)
    "goldenrod_city",  # Eevee from Bill (HGSS)
})

# ── Generated-pack loaders ──────────────────────────────────────────────────────────────────
#
# Every table this adapter serves comes out of _HGSS_PACK_DIR at import, from the files the three
# tools/gen_gen4_*.py generators write from the pinned pret/pokeheartgold clone. A pack that cannot
# be read leaves its table EMPTY with an error naming the path — never a hand-typed substitute.
# An empty table shows an honest blank board; a stale hand-typed one would keep asserting facts
# the pack no longer carries, which is the failure the Platinum deletion exists to stop.

def _read_pack(path: str) -> dict | None:
    """Parse one generated pack JSON, or None — with an error naming the path — on any miss."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        log.error("Gen 4: cannot read the generated pack at %s (%s)", path, e)
        return None
    if not isinstance(doc, dict):
        log.error("Gen 4: %s is a %s, not a JSON object — refused", path, type(doc).__name__)
        return None
    return doc


def _load_hgss_area_display_names() -> dict[str, str]:
    """area_id -> display name, from the pack's `areas` block.

    The pack nests its rows under `areas` (the legacy file had them at the top level) and every
    row carries the pret map-section name the generator read out of msg 0279, e.g.
    `areas.route_1.display == "Route 1"`. A row with no usable `display` contributes nothing and
    that area falls through to humanize_area_id, exactly as an unmapped id always did.
    """
    path = os.path.join(_HGSS_PACK_DIR, "area_map.json")
    doc = _read_pack(path)
    areas = (doc or {}).get("areas")
    if not isinstance(areas, dict):
        log.error("Gen 4: %s has no `areas` block (%r) — every area falls back to its id",
                  path, areas)
        return {}
    return {aid: row["display"] for aid, row in areas.items()
            if isinstance(row, dict) and isinstance(row.get("display"), str) and row["display"]}


_AREA_DISPLAY_NAMES: dict[str, str] = _load_hgss_area_display_names()


def _load_hgss_trainers() -> dict[str, dict]:
    """trainer_id -> {"name", "class"}, flattened from the pack's `trainers` block.

    The pack is version-independent (`version_split: false`: HG and SS share one trainers NARC)
    and already writes the class NAME on each trainer row, so the legacy two-level
    {trainers, classes} join happens once here and `trainer_info` reads one flat row. `class`
    comes off the row, falling back to `classes[class_id].name`. TRAINER_NONE is the pack's "no
    trainer here" marker and is dropped, which is how trainer_info(0) keeps answering ("", "").
    """
    path = os.path.join(_HGSS_PACK_DIR, "trainers.json")
    doc = _read_pack(path)
    trainers = (doc or {}).get("trainers")
    if not isinstance(trainers, dict):
        log.error("Gen 4: %s has no `trainers` block (%r) — no trainer resolves by name",
                  path, trainers)
        return {}
    classes = doc.get("classes") if isinstance(doc.get("classes"), dict) else {}
    out: dict[str, dict] = {}
    for tid, row in trainers.items():
        if not isinstance(row, dict) or row.get("const") == "TRAINER_NONE":
            continue
        cls_row = classes.get(str(row.get("class_id")))
        out[str(tid)] = {
            "name": (row.get("name") or "").strip(),
            "class": row.get("class") or (cls_row or {}).get("name", ""),
        }
    return out


_HGSS_TRAINERS: dict[str, dict] = _load_hgss_trainers()


# Encounter projection. The pack is keyed by ENCDATA token: `banks.<token>.areas` names the areas
# a token backs and `versions.<title>.banks.<token>` holds that title's tables. The adapter's
# contract is area-first, so the whole inversion happens once here.
_ENCOUNTER_METHOD_LABELS = {
    "surf": "Surfing", "old_rod": "Old Rod", "good_rod": "Good Rod", "super_rod": "Super Rod",
    "rock_smash": "Rock Smash", "headbutt": "Headbutt",
}
# A `land` table is split by hour as morn/day/nite; the legacy tables showed Day/Night.
_ENCOUNTER_TIME_LABELS = {"morn": "Morn", "day": "Day", "nite": "Night"}
# Bank keys that are not a table a walker can roll for:
#   `rates` — each bank's per-method WEIGHT (which of the banks backing one area the game picks),
#             not a slot percentage. Folding it into a printed rate would invent a number.
#   `radio` — the Tune Radio table (hoenn/sinnoh), only present while a radio station plays.
#   `swarm` — the single-species swarm table the game flags rather than rolls.
# The legacy encounters_hgss.json had no row for any of the three, so dropping them preserves the
# old board exactly. A card that wants them should first decide what a radio or a swarm means for
# a Soul Link slot.
_ENCOUNTER_NON_TABLE_KEYS = frozenset({"rates", "radio", "swarm"})


def _merge_slots(bucket: dict[int, dict], slots) -> None:
    """Fold `slots` into `bucket`, keyed by species: rates summed, level range widened.

    The pack keeps one entry per SLOT (12 land slots, each with its own rate and level band), while
    the legacy tables kept one row per species. Aggregating per species is the same projection
    gen1_rby and gen1_rom_scan.build_encounter_tables make, and the board only ever prints the
    distinct species names anyway (server/templates/_board.html:237).
    """
    if not isinstance(slots, list):
        return
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("species_id"), int):
            continue
        sid = slot["species_id"]
        row = bucket.get(sid)
        if row is None:
            bucket[sid] = {
                "name": slot.get("name", ""), "species_id": sid,
                "rate": slot.get("rate", 0),
                "min_level": slot.get("min_level", 0), "max_level": slot.get("max_level", 0),
            }
            continue
        row["rate"] += slot.get("rate", 0)
        row["min_level"] = min(row["min_level"], slot.get("min_level", row["min_level"]))
        row["max_level"] = max(row["max_level"], slot.get("max_level", row["max_level"]))


def _load_hgss_encounters() -> dict[str, dict[str, list[dict]]]:
    """title -> area_id -> method label -> entries, projected from the pack's encounter banks.

    Several tokens back one area (a route's own bank plus the banks for its headbutt trees), so
    the slots of every token naming an area are folded into that area's per-method buckets.

    `safari` is deliberately NOT projected: its tables sit under `safari.areas` with one `level`
    per slot and conditional `bonus` rows — neither the shape nor the certainty of a wild roll —
    and the legacy table carried no safari area either, so `encounter_table("safari_plains")`
    answers None today and still does. A safari card should decide what a bonus-only species means
    for a Soul Link slot first.
    """
    path = os.path.join(_HGSS_PACK_DIR, "encounters.json")
    doc = _read_pack(path)
    banks = (doc or {}).get("banks")
    versions = (doc or {}).get("versions")
    if not isinstance(banks, dict) or not isinstance(versions, dict):
        log.error("Gen 4: %s has no `banks`/`versions` blocks — no wild table resolves", path)
        return {}
    unmapped: set[str] = set()
    out: dict[str, dict[str, list[dict]]] = {}
    for title, version in versions.items():
        vbanks = version.get("banks") if isinstance(version, dict) else None
        if not isinstance(vbanks, dict):
            log.error("Gen 4: %s versions.%s has no `banks` block — that title has no wild table",
                      path, title)
            continue
        areas_out = out.setdefault(title, {})
        for token, tables in vbanks.items():
            bank = banks.get(token)
            areas = bank.get("areas") if isinstance(bank, dict) else None
            if not isinstance(areas, list) or not isinstance(tables, dict):
                continue
            buckets: dict[str, dict[int, dict]] = {}
            for key, value in tables.items():
                if key in _ENCOUNTER_NON_TABLE_KEYS:
                    continue
                if key == "land":
                    if isinstance(value, dict):
                        for hour, slots in value.items():
                            label = _ENCOUNTER_TIME_LABELS.get(hour)
                            if label is None:
                                unmapped.add(f"land.{hour}")
                                continue
                            _merge_slots(buckets.setdefault(label, {}), slots)
                    else:
                        _merge_slots(buckets.setdefault("Grass", {}), value)
                    continue
                label = _ENCOUNTER_METHOD_LABELS.get(key)
                if label is None:
                    unmapped.add(key)
                    continue
                _merge_slots(buckets.setdefault(label, {}), value)
            for area_id in areas:
                if not isinstance(area_id, str):
                    continue
                area_out = areas_out.setdefault(area_id, {})
                for label, bucket in buckets.items():
                    # Most likely first, then by name, so the board's order is stable.
                    area_out.setdefault(label, []).extend(
                        sorted(bucket.values(), key=lambda r: (-r["rate"], r["name"])))
    if unmapped:
        log.error("Gen 4: %s has bank keys with no method label in %s: %s — those tables are NOT "
                  "projected (a pin bump added a method; map it before trusting the board)",
                  path, sorted(_ENCOUNTER_METHOD_LABELS), sorted(unmapped))
    return out


_HGSS_ENCOUNTERS: dict[str, dict[str, list[dict]]] = _load_hgss_encounters()


# Gen 4 item names (HGSS item IDs — differ from Gen 3)


# Gen 4 alternate-form sprite mapping.
# Keys: (species_id, form_byte) tuple matching the form byte from Block B.
# Values: PokeAPI URL slug — replaces `species_id` in the sprite filename.
# Form-byte semantics per pret/pokeheartgold include/constants/forms.h (Bulbapedia
# corroboration: https://bulbapedia.bulbagarden.net/wiki/Pok%C3%A9mon_with_form_differences).
# Only non-default forms are mapped here — form 0 (the base form) falls back to
# the plain `<species_id>.png` sprite via the None default in form_sprite_url.
_FORM_SPRITE: dict[tuple[int, int], str] = {
    # Unown (201): form byte = letter index (0=A, 1=B, ..., 25=Z, 26=!, 27=?).
    # PokeAPI uses /pokemon/201-b.png .. 201-z.png plus 201-question and 201-exclamation.
    **{(201, i): f"201-{chr(ord('a') + i)}" for i in range(1, 26)},
    (201, 26): "201-exclamation",
    (201, 27): "201-question",
    # Castform (351): 0=Normal, 1=Sunny, 2=Rainy, 3=Snowy
    (351, 1): "351-sunny", (351, 2): "351-rainy", (351, 3): "351-snowy",
    # Deoxys (386): 0=Normal, 1=Attack, 2=Defense, 3=Speed
    (386, 1): "386-attack", (386, 2): "386-defense", (386, 3): "386-speed",
    # Burmy (412) cloaks: 0=Plant, 1=Sandy, 2=Trash
    (412, 1): "412-sandy", (412, 2): "412-trash",
    # Wormadam (413) cloaks: 0=Plant, 1=Sandy, 2=Trash
    (413, 1): "413-sandy", (413, 2): "413-trash",
    # Cherrim (421): 0=Overcast (sprite default), 1=Sunshine
    (421, 1): "421-sunshine",
    # Shellos (422) / Gastrodon (423): 0=West Sea, 1=East Sea
    (422, 1): "422-east", (423, 1): "423-east",
    # Rotom (479): 0=Normal, 1=Heat, 2=Wash, 3=Frost, 4=Fan, 5=Mow
    (479, 1): "479-heat", (479, 2): "479-wash", (479, 3): "479-frost",
    (479, 4): "479-fan",  (479, 5): "479-mow",
    # Giratina (487): 0=Altered (sprite default), 1=Origin
    (487, 1): "487-origin",
    # Shaymin (492): 0=Land (sprite default), 1=Sky
    (492, 1): "492-sky",
    # Arceus (493): each plate sets form byte to 1..17 corresponding to type ID.
    # PokeAPI hosts 493-<type-slug>.png; mapping by canonical Gen 4 plate order.
    (493,  1): "493-fighting", (493,  2): "493-flying",   (493,  3): "493-poison",
    (493,  4): "493-ground",   (493,  5): "493-rock",     (493,  6): "493-bug",
    (493,  7): "493-ghost",    (493,  8): "493-steel",    (493,  9): "493-fire",
    (493, 10): "493-water",    (493, 11): "493-grass",    (493, 12): "493-electric",
    (493, 13): "493-psychic",  (493, 14): "493-ice",      (493, 15): "493-dragon",
    (493, 16): "493-dark",
    # Pichu (172): HGSS introduces the Spiky-eared Pichu event form (form byte 1).
    (172, 1): "172-spiky-eared",
}

# ── Per-foundation box geometry ─────────────────────────────────────────────────────────────
#
# `gen4_hgsspt` is ONE adapter for three cartridges with two different PC layouts, so a fact
# about a CARTRIDGE rather than about the generation is keyed on the foundation
# (`server/adapters/__init__.py _ROM_TYPE_TO_FOUNDATION`), never on the game_id they all share.
# This is a lookup into gen4_codec, not a second table of box counts: the foundation rows and
# the codec profiles are the two places each number already lived.
_FOUNDATION_CODEC_PROFILE = {"gen4_hgss": "hgss", "gen4_hge": "hge"}

# Mons per PC box: 30 in every Gen 4 foundation, so unlike the box COUNT this does not vary.
# gen4_codec records the PC stride as 0x1000 and states the arithmetic — `box_stride=0x1000,
# # 30*0x88 + 16 pad` (gen4_codec.py:175 for hgss, :183 for hge) — and both packs give the
# number outright (data/games/gen4_hgss/profile.json `mons_per_box`, same in
# data/games/gen4_hge/profile.json). tests/unit/test_gen4_adapter_foundation.py pins the
# adapter against all three so they cannot drift.
_MONS_PER_BOX = 30

# Gen 4 status bits -> the token the board renders; first match wins. Bit meanings are the
# pinned pret/pokeheartgold ones (include/constants/battle.h:296-305): STATUS_SLEEP_0/1/2 =
# bits 0-2, STATUS_POISON bit 3, STATUS_BURN bit 4, STATUS_FREEZE bit 5, STATUS_PARALYSIS
# bit 6, STATUS_BAD_POISON bit 7, STATUS_POISON_COUNT = 15 << 8. Two orderings are
# load-bearing: bad poison sets STATUS_POISON as well, so TOX must be tested before PSN; and
# sleep is a COUNTER, so 0x07 must be MASKED — `status_cond == 1` is asleep, and so is
# turn 2's 0x02. Bits 8-11 are the poison counter and carry no condition, so nothing here
# matches a word that has only those.
_STATUS_TOKENS = ((0x07, "SLP"), (0x80, "TOX"), (0x08, "PSN"),
                  (0x10, "BRN"), (0x20, "FRZ"), (0x40, "PAR"))


class Gen4Adapter(GameAdapter):
    """Adapter for Gen 4: HeartGold, SoulSilver and hg-engine (not Platinum; see module docstring).

    Uses National Pokédex IDs (1-493). Mon keys are PID:OTID format,
    identical to Gen 3.
    """

    def __init__(self, rom_type: str = "heartgold", **kwargs):
        self._rom_type = (rom_type or "heartgold").lower()
        # The LAYOUT this cartridge has, as opposed to the game_id every Gen 4 title shares:
        # hg-engine stores 30 PC boxes where HeartGold stores 18, so a property that answers
        # "the last box" has to ask which foundation it is speaking for. None for a rom_type
        # the registry does not route (Platinum) — which is what makes the box properties
        # refuse below instead of answering for HeartGold.
        self._foundation = foundation_for_rom_type(self._rom_type)
        self._item_names = _GEN4_ITEM_CATALOGS.get(self._foundation, {})
        if self._foundation not in _GEN4_ITEM_CATALOGS:
            log.error("Gen 4: no item catalog for unrouted cartridge %r", self._rom_type)
        # Trainers are version-independent (the pack says so: `version_split: false`), so every
        # routed title reads the same table. Only the WILD tables split, by title, and hg-engine
        # has no title of its own in the pack: it is a HeartGold fork, so it reads HeartGold's.
        self._trainers = _HGSS_TRAINERS
        self._encounters = _HGSS_ENCOUNTERS.get(
            "soulsilver" if self._rom_type == "soulsilver" else "heartgold", {})

    @property
    def game_id(self) -> str:
        return "gen4_hgsspt"

    # ── GameRulesAdapter ─────────────────────────────────────────────────

    def is_gift_area(self, area_id: str) -> bool:
        """True for a gift/static area. The set is the pack's own `gift_areas.ids`
        (`_HGSS_GIFT_AREAS`) plus Pal Park and the `gift` unmapped-area fallback; see
        `_HGSS_EXTRA_GIFT_AREAS` for why no Sinnoh id is in it any more. The `gift_` and
        `egg_` prefixes the client/remap emit are recognised regardless, so a remapped
        `gift_<area>` (base.gift_link_area) still reads as a gift downstream."""
        if area_id in _GIFT_AREAS or area_id.startswith("gift_"):
            return True
        return area_id.startswith("egg_")

    def is_fixed_species_gift(self, area_id: str) -> bool:
        # Strip "egg_" prefix so e.g. "egg_dragons_den" still matches the Dratini entry.
        bare = area_id[4:] if area_id.startswith("egg_") else area_id
        return bare in _FIXED_SPECIES_GIFTS

    def is_egg_pickup_area(self, area_id: str) -> bool:
        # NPC-given eggs — in HGSS that is violet_city alone (Togepi from Mr. Pokémon; Mareep,
        # Wooper and Slugma in the Poké Center). Daycare eggs use is_daycare_area instead — they
        # aren't pickups, they're player-bred. Strip the "egg_" prefix the client emits to consult
        # the underlying area.
        bare = area_id[4:] if area_id.startswith("egg_") else area_id
        # Daycare always wins over egg-pickup classification (player-bred ≠ NPC gift).
        if bare in _DAYCARE_AREAS:
            return False
        if area_id.startswith("egg_"):
            return True
        return bare in _EGG_PICKUP_AREAS

    def is_daycare_area(self, area_id: str) -> bool:
        # Pokémon Day Care locations: Route 34 (Goldenrod) and Goldenrod City.
        bare = area_id[4:] if area_id.startswith("egg_") else area_id
        return bare in _DAYCARE_AREAS

    def evo_family(self, species_id: int) -> int:
        return _natdex_base_form(species_id)

    def gender_from_key(self, key: str, species_id: int) -> str:
        """Derive gender from PID:OTID key and NatDex species ID.

        Gen 4 uses the same formula as Gen 3: personality & 0xFF vs threshold.
        GENDER_RATIO is keyed by CFRU ID, so we convert first.
        """
        if not key or not species_id:
            return ""
        try:
            personality = int(key.split(":")[0], 16)
        except (ValueError, IndexError):
            return ""
        cfru_id = _to_cfru(species_id)
        threshold = GENDER_RATIO.get(cfru_id, 127)
        if threshold == 255:
            return "genderless"
        if threshold == 254:
            return "female"
        if threshold == 0:
            return "male"
        return "female" if (personality & 0xFF) < threshold else "male"

    def species_types(self, species_id: int) -> tuple[int, int] | None:
        cfru_id = _to_cfru(species_id)
        return _species_types(cfru_id, is_rr=False)

    def is_shiny(self, key: str) -> bool:
        """Gen IV shiny: (tid ^ sid ^ p_upper ^ p_lower) < 8."""
        parsed = _parse_pid_otid_key(key)
        if parsed is None:
            return False
        return pid_otid_shiny(*parsed)

    def species_name(self, species_id: int) -> str:
        return NATIONAL_SPECIES_NAMES.get(species_id, f"#{species_id}")

    def type_name(self, type_id: int) -> str:
        return _type_name(type_id)

    # ── GamePresentationAdapter ──────────────────────────────────────────

    def sprite_html(self, species_id: int, form: int = 0) -> str:
        if not species_id or species_id < 1:
            return ""
        slug = self.form_sprite_url(species_id, form) or str(species_id)
        url = f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{slug}.png"
        # class + data-species: the enc-sprite swap in server.py keys on them, as does
        # every CSS rule that greys a fainted mon or crops a tombstone.
        return (f'<img class="mon-sprite" data-species="{species_id}" src="{url}" '
                f'width="40" height="40" loading="lazy">')

    def ability_name(self, ability_id: int, species_id: int = 0) -> str:
        return _ability_name(ability_id, is_rr=False)

    def ability_description(self, ability_id: int) -> str:
        return _ability_description(ability_id, is_rr=False)

    def status_token(self, status_cond: int) -> str:
        """Gen 4 `status` -> SLP/TOX/PSN/BRN/FRZ/PAR, or "" when no condition is set.

        The word is the u32 at 0x088 of the party record — pret/pokeheartgold
        include/pokemon_types_def.h:199-200, `typedef struct PartyPokemon { /* 0x088 */ u32
        status; // slp:3, psn:1, brn:1, frz:1, prz:1, tox:1`, so 0x88 + 0xEC total with
        pokemon_types_def.h:214-217 (`Pokemon` = BoxPokemon then PartyPokemon). The pack agrees:
        data/games/gen4_hgss/profile.json `pkm` gives box_size 136 and party_size 236, so the
        tail begins at 0x88, and gen4_codec unpacks `status` there (gen4_codec.py:53 TAIL_OFF,
        :324-325).

        The value that arrives IS that word. lua/gen4/pk4.lua:137 reads it as
        `mon.status = u(p, TAIL, 4)` from the decoded party record and lua/gen4/client.lua:331
        forwards the field verbatim as `status_cond`, so this decodes the representation the
        client sends rather than re-encoding it into Gen 3's status1 — the two happen to agree
        bit for bit, which is why Gen 3's own table was not copied from and this one was not.

        No faint bits hide in here: Gen 3's FNT counter lives in status1 bits 3-6, which Gen 4
        gives to psn/brn/frz/prz, and the pinned decomp defines no STATUS_FNT at all. A fainted
        mon's status_cond is still whatever its status condition is, and the board draws FNT
        from HP instead (server.py:1896).
        """
        if not status_cond:
            return ""
        for mask, token in _STATUS_TOKENS:
            if status_cond & mask:
                return token
        return ""

    def trainer_info(self, trainer_id: int) -> tuple[str, str]:
        """(name, class) for a trainer_id, or ("", "") when the pack has no such row.

        `_HGSS_TRAINERS` is the pack's own `trainers` block (data/games/gen4_hgss/trainers.json,
        keyed by the wire trainer_id) flattened once at import. The class is the PACK's word for
        it — "Leader" for the Johto gym leaders, where the deleted hand-curated table said
        "Gym Leader" — and it is a presentation label only: nothing keys on it (server.py:2512
        stores it as `opponent_class`). Regenerate with tools/gen_gen4_trainers.py against a
        cloned pret/pokeheartgold; there is no hand-editable table any more.
        """
        if not trainer_id:
            return ("", "")
        entry = self._trainers.get(str(trainer_id))
        if not entry:
            return ("", "")
        return (entry.get("name", ""), entry.get("class", ""))

    def item_name(self, item_id: int) -> str:
        return self._item_names.get(item_id, f"Item #{item_id}") if item_id else ""

    def move_name(self, move_id: int) -> str:
        # Gen 4 lookup chain handled inside server.data.moves: gen4 → gen3_vanilla.
        from server.data.moves import move_name as _move_name
        return _move_name(move_id, generation=4)

    def move_data(self, move_id: int) -> dict | None:
        from server.data.moves import move_data as _move_data
        raw = _move_data(move_id, generation=4)
        if raw is None:
            return None
        type_id = raw.get("type", 0)
        return {
            "name":      self.move_name(move_id),
            "type_id":   type_id,
            "type_name": self.type_name(type_id),
            "power":     raw.get("power", 0),
            "accuracy":  raw.get("accuracy", 0),
            "pp":        raw.get("pp", 0),
            "split":     raw.get("split", 0),
        }

    def area_display_name(self, area_id: str) -> str:
        if area_id in _AREA_DISPLAY_NAMES:
            return _AREA_DISPLAY_NAMES[area_id]
        return humanize_area_id(area_id)

    def encounter_table(self, area_id: str) -> dict | None:
        """Return wild encounter data for an area, or None if no data.

        Returns a dict mapping method label (Morn/Day/Night/Grass/Surfing/Old Rod/
        Good Rod/Super Rod/Rock Smash/Headbutt) → list of entries
        {name, species_id, rate, min_level, max_level}.

        `self._encounters` is `_load_hgss_encounters()`'s projection of the pack's encounter
        banks for this cartridge's title (see there for what is deliberately not projected).
        Areas the pack backs no bank for — towns, Safari sub-areas, and every Sinnoh id now that
        Platinum is gone — answer None.
        """
        return self._encounters.get(area_id) or None

    def to_national_dex(self, species_id: int) -> int:
        return species_id

    def gender_symbol(self, gender: str) -> str:
        return GENDER_SYMBOL.get(gender, "")

    def form_sprite_id(self, species_id: int) -> int | None:
        return None

    def form_sprite_url(self, species_id: int, form: int = 0) -> str | None:
        """Return the PokeAPI URL slug for the given (species, form), or None
        for the base form (caller uses str(species_id) as the slug).

        Form byte values per pret/pokeheartgold include/constants/forms.h
        (and Bulbapedia / PKHeX PK4 form documentation). Slugs match the
        PokeAPI sprite repository filename convention.
        """
        return _FORM_SPRITE.get((species_id, form))

    @property
    def _codec_profile(self):
        """The gen4_codec.Profile for this cartridge's foundation, or None if unrouted."""
        return gen4_codec.PROFILES.get(_FOUNDATION_CODEC_PROFILE.get(self._foundation or ""))

    @property
    def memorial_box_index(self) -> int:
        """The LAST PC box, per foundation: hgss has 18 boxes (index 17), hg-engine 30 (29).

        This was a bare 17 with the comment "last of 18 boxes" — HeartGold's answer written as
        if it were the generation's. On hg-engine it named a box twelve past the end of
        storage, so every burial overflowed into boxes the cartridge does not have.

        -1 is the base contract's "this game has no dedicated memorial box", and it is what an
        UNROUTED rom_type gets (Platinum): the server then counts no memorial boxes at all
        (server.py:5127-5128) instead of counting boxes of a game it refuses at hello. Keeping
        17 for an unknown rom_type would keep the old wrong answer alive for exactly the
        cartridge it was never right for.
        """
        profile = self._codec_profile
        if profile is None or not profile.box_count:
            return -1
        return profile.box_count - 1

    @property
    def mons_per_box(self) -> int:
        """See _MONS_PER_BOX: 30 on every Gen 4 foundation, 0 when the foundation is unknown.

        The 0 mirrors memorial_box_index's refusal rather than inventing a capacity, and every
        consumer divides by it only after a `mem_idx < 0` early return (server.py:5127-5134).
        """
        return _MONS_PER_BOX if self._codec_profile is not None else 0

    def gym_badge_slugs(self, rom_type: str) -> list[tuple[int, str]]:
        # HeartGold / SoulSilver → Johto + Kanto (16 badges)
        return [
            ( 9, "Zephyr Badge"),
            (10, "Hive Badge"),
            (11, "Plain Badge"),
            (12, "Fog Badge"),
            (13, "Storm Badge"),
            (14, "Mineral Badge"),
            (15, "Glacier Badge"),
            (16, "Rising Badge"),
            # Kanto (bits 8-15 via kanto_badges)
            (1, "Boulder Badge"),
            (2, "Cascade Badge"),
            (3, "Thunder Badge"),
            (4, "Rainbow Badge"),
            (5, "Soul Badge"),
            (6, "Marsh Badge"),
            (7, "Volcano Badge"),
            (8, "Earth Badge"),
        ]
