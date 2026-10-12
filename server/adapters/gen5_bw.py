"""
server/adapters/gen5_bw.py — Game adapter for Gen 5 (Black/White/Black2/White2).

Gen 5 uses National Pokédex IDs natively (1-649). Mon keys use PID:OTID format,
identical to Gen 4. PKM struct is 220 bytes (vs Gen 4's 236), same LCRNG
block encryption. No CFRU/RR-style extensions.

Supports all 4 US variants: pokemon_black, pokemon_white, pokemon_black_2,
pokemon_white_2. All route via game_id = "gen5_bw".
"""

import json
import logging
import os

from server.pokemon_data import (
    CFRU_FORM_SPRITE_ID,
    GENDER_RATIO,
    GENDER_SYMBOL,
    NATIONAL_SPECIES_NAMES,
    SPECIES_NAMES,
    _parse_pid_otid_key,
    ability_description as _ability_description,
    ability_name as _ability_name,
    natdex_base_form as _natdex_base_form,
    pid_otid_shiny,
    species_types as _species_types,
    to_cfru as _to_cfru,
    to_national as _to_national,
    type_name as _type_name,
)

from .base import GameAdapter, humanize_area_id

log = logging.getLogger(__name__)

# Gift/static encounter area_ids — Pokémon obtained without requiring Pokéballs.
# Only include areas where the mon is received via scripted gift/egg (no wild battle).
# Legendary static encounters (Reshiram, Zekrom, Kyurem) are NOT gift areas —
# they are wild battles and consume a nuzlocke slot.
_GIFT_AREAS_BW1 = frozenset({
    "nuvema_town",    # Starter (Prof. Juniper's lab)
    "striaton_city",  # Elemental monkey gift (restaurant)
    "castelia_city",  # Eevee from Amanita (Bianca's sister)
    "nacrene_city",   # Fossil revives (Nacrene Museum)
    "gift",           # Fallback for unmapped gift areas
})

_GIFT_AREAS_BW2 = frozenset({
    "aspertia_city",  # Starter (Bianca's gift)
    "castelia_city",  # Eevee from Amanita
    "nacrene_city",   # Fossil revives
    "gift",           # Fallback
})

# Area display names loaded from data/games/gen5_bw/area_map_bw.json
_AREA_DISPLAY_NAMES: dict[str, str] = {}
_data_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "games", "gen5_bw",
)
for _map_file in ("area_map_bw.json", "area_map_bw2.json"):
    _area_map_path = os.path.join(_data_dir, _map_file)
    if os.path.exists(_area_map_path):
        with open(_area_map_path) as _f:
            for _entry in json.load(_f):
                _area_id = _entry.get("area_id", "")
                _name = _entry.get("name", "")
                if _area_id and _name and _area_id not in _AREA_DISPLAY_NAMES:
                    _AREA_DISPLAY_NAMES[_area_id] = _name

# Per-version encounter tables (lazy-loaded; one JSON per ROM variant).
# Schema: {area_id: {method_label: [{name, species_id, rate, min_level, max_level}, ...]}}
# Source: veekun/pokedex encounters.csv (auto-generated; see commit history for the
# generator one-liner). Multi-sub-area locations (e.g. Pinwheel Forest outside+inside)
# use MAX rate per (method, species) across sub-areas, so totals can exceed 100% when
# different sub-areas have different mons — that's accurate "what can spawn here at all"
# representation rather than a per-step probability.
_ENCOUNTER_TABLES: dict[str, dict] = {}
for _rom in ("pokemon_black", "pokemon_white", "pokemon_black_2", "pokemon_white_2"):
    _enc_path = os.path.join(_data_dir, f"encounters_{_rom}.json")
    if os.path.exists(_enc_path):
        with open(_enc_path, encoding="utf-8") as _f:
            _ENCOUNTER_TABLES[_rom] = json.load(_f)

# Gen 5 (BW/BW2) item names — full 1-638 range loaded from server/data/items/gen5.py.
# Source: veekun/pokedex item_names.csv (auto-generated; see tools workflow).
# Shared across all 4 ROM variants.
from server.data.items.gen5 import ITEM_NAMES as _GEN5_ITEM_NAMES  # noqa: E402


class Gen5Adapter(GameAdapter):
    """Adapter for Gen 5: Black/White/Black2/White2.

    Uses National Pokédex IDs (1-649). Mon keys are PID:OTID format,
    same as Gen 3/4. PKM struct is 220 bytes with same LCRNG encryption.
    Same shiny and gender formulas as Gen 3/4.
    """

    def __init__(self, rom_type=None, **kwargs):
        # rom_type is passed on hello so is_gift_area() can select the correct set.
        self._rom_type = rom_type or ""

    @property
    def game_id(self) -> str:
        return "gen5_bw"

    # ── GameRulesAdapter ─────────────────────────────────────────────────

    def is_gift_area(self, area_id: str) -> bool:
        gift_set = self.gift_areas_for_rom(self._rom_type)
        return area_id in gift_set or area_id.startswith("gift_")

    def is_fixed_species_gift(self, area_id: str) -> bool:
        # Fixed-species gifts in BW1/BW2
        _FIXED = frozenset({
            "dreamyard",        # Munna (fixed)
        })
        return area_id in _FIXED

    def gift_areas_for_rom(self, rom_type: str) -> frozenset[str]:
        """Return gift areas specific to a BW1 or BW2 ROM."""
        if rom_type in ("pokemon_black_2", "pokemon_white_2"):
            return _GIFT_AREAS_BW2
        return _GIFT_AREAS_BW1

    def evo_family(self, species_id: int) -> int:
        return _natdex_base_form(species_id)

    def gender_from_key(self, key: str, species_id: int) -> str:
        """Derive gender from PID:OTID key and NatDex species ID.

        Gen 5 uses the same formula as Gen 3/4: personality & 0xFF vs threshold.
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
        """Gen V shiny formula: (tid ^ sid ^ p_upper ^ p_lower) < 8.

        Same formula as Gen 3/4.
        """
        parsed = _parse_pid_otid_key(key)
        if parsed is None:
            return False
        return pid_otid_shiny(*parsed)

    def species_name(self, species_id: int) -> str:
        # CFRU form variant IDs (700+) get form-aware names like
        # "Basculin (Blue)" or "Deerling Summer" — the Lua client passes
        # the CFRU display ID when a non-zero form byte is decoded.
        if species_id >= 700:
            name = SPECIES_NAMES.get(species_id)
            if name:
                return name
        return NATIONAL_SPECIES_NAMES.get(species_id, f"#{species_id}")

    def type_name(self, type_id: int) -> str:
        return _type_name(type_id)

    # ── GamePresentationAdapter ──────────────────────────────────────────

    def sprite_html(self, species_id: int, form: int = 0) -> str:
        # form not yet used by Gen 5 adapter — to be wired when Gen 5 form sprites land
        if not species_id or species_id < 1:
            return ""
        # Form variants (CFRU 700+) map to PokeAPI alt-form IDs (10001+).
        # Falls back to base-NatDex sprite when the form has no separate PokeAPI ID.
        form_pid = CFRU_FORM_SPRITE_ID.get(species_id)
        if form_pid:
            url = f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{form_pid}.png"
        else:
            # For CFRU form IDs without a dedicated PokeAPI sprite (e.g. Deerling seasons),
            # fall back to the base-species NatDex sprite.
            nat = _to_national(species_id) if species_id >= 700 else species_id
            sid = nat if (nat and 1 <= nat <= 1025) else species_id
            url = f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{sid}.png"
        # class + data-species: the enc-sprite swap in server.py keys on them, as does
        # every CSS rule that greys a fainted mon or crops a tombstone.
        return (f'<img class="mon-sprite" data-species="{species_id}" src="{url}" '
                f'width="40" height="40" loading="lazy">')

    def ability_name(self, ability_id: int, species_id: int = 0) -> str:
        return _ability_name(ability_id, is_rr=False)

    def ability_description(self, ability_id: int) -> str:
        return _ability_description(ability_id, is_rr=False)

    def trainer_info(self, trainer_id: int) -> tuple[str, str]:
        return ("", "")

    def item_name(self, item_id: int) -> str:
        return _GEN5_ITEM_NAMES.get(item_id, f"Item #{item_id}") if item_id else ""

    def encounter_table(self, area_id: str) -> dict[str, list[dict]] | None:
        """Return wild encounter data for `area_id` in the current ROM variant.

        Returns `{method_label: [entries]}` or None if the area has no encounter
        data for this ROM. Entries include name, species_id, rate (sum across
        slots, 0-100 typical), and min/max level.

        Per-ROM tables are loaded at import time from
        `data/games/gen5_bw/encounters_<rom_type>.json`. Falls back to Black
        (BW1) or Black 2 (BW2) if `rom_type` was not set on the adapter.
        """
        rom = self._rom_type
        if rom not in _ENCOUNTER_TABLES:
            # Pick a sensible default by detecting which gen the area_id belongs to.
            # BW1 areas appear in encounters_pokemon_black.json; BW2-exclusive areas
            # (aspertia_city, virbank_complex, etc.) only in BW2 tables.
            rom = "pokemon_black"
        table = _ENCOUNTER_TABLES.get(rom)
        if not table:
            return None
        result = table.get(area_id)
        if result is None and rom in ("pokemon_black", "pokemon_white"):
            # Fallback: area might only exist in BW2 (e.g. user passed BW1 rom_type
            # but the zone is BW2-exclusive). Try BW2 tables.
            for fallback in ("pokemon_black_2", "pokemon_white_2"):
                result = _ENCOUNTER_TABLES.get(fallback, {}).get(area_id)
                if result:
                    break
        return result or None

    def area_display_name(self, area_id: str) -> str:
        if area_id in _AREA_DISPLAY_NAMES:
            return _AREA_DISPLAY_NAMES[area_id]
        return humanize_area_id(area_id)

    def to_national_dex(self, species_id: int) -> int:
        # Gen 5 species IDs are already NatDex (1-649). CFRU form variants
        # (700+) map back to their base NatDex species via CFRU_TO_NATIONAL.
        if species_id >= 700:
            return _to_national(species_id)
        return species_id

    def gender_symbol(self, gender: str) -> str:
        return GENDER_SYMBOL.get(gender, "")

    def form_sprite_id(self, species_id: int) -> int | None:
        # CFRU_FORM_SPRITE_ID maps CFRU form variant IDs (e.g. 736=Basculin Blue)
        # to PokeAPI alt-form sprite IDs (e.g. 10016). Returns None for base
        # forms or unknown species, mirroring Gen 3 adapter behavior.
        return CFRU_FORM_SPRITE_ID.get(species_id)

    @property
    def memorial_box_index(self) -> int:
        # Gen 5: 24 boxes; Box 24 = index 23.
        return 23

    def gym_badge_slugs(self, rom_type: str) -> list[tuple[int, str]]:
        # All BW/BW2 variants have exactly 8 Unova badges (single badge byte).
        return [
            (1, "Trio Badge"),
            (2, "Basic Badge"),
            (3, "Insect Badge"),
            (4, "Bolt Badge"),
            (5, "Quake Badge"),
            (6, "Jet Badge"),
            (7, "Freeze Badge"),
            (8, "Legend Badge"),
        ]

    # ── Move data (Gen 4-5 range 355-559) ────────────────────────────────

    def move_name(self, move_id: int) -> str:
        """Return display name for a move ID.

        Falls through Gen 4-5 table (355-559) → vanilla Gen 3 table (1-354).
        """
        if not move_id:
            return ""
        from server.data.moves import move_name as _move_name
        return _move_name(move_id, generation=5)

    def move_data(self, move_id: int) -> dict | None:
        """Return move details dict {name, type_id, type_name, power, accuracy, pp, split}.

        Returns None if the move ID is unknown.
        """
        if not move_id:
            return None
        from server.data.moves import move_data as _move_data
        raw = _move_data(move_id, generation=5)
        if raw is None:
            return None
        type_id = raw.get("type", 0)
        return {
            "name": self.move_name(move_id),
            "type_id": type_id,
            "type_name": self.type_name(type_id),
            "power": raw.get("power", 0),
            "accuracy": raw.get("accuracy", 0),
            "pp": raw.get("pp", 0),
            "split": raw.get("split", 0),
        }
