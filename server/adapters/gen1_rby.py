"""Gen 1 Red/Blue/Yellow adapter over pret-derived tables and the raw-byte codec.

Wire species are *internal* indices throughout, including indices below 152.
The constants below are derived from pinned pret/pokered 405b624 (Yellow's
shared dex order, stat layout, and evolution data agree at pokeyellow 0a08515).
The contract test reads pret directly and checks every shipped table entry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from server.data.items.gen1 import ITEM_NAMES
from server.pokemon_data import species_name as national_species_name

from . import gen1_codec
from .base import GameAdapter, companion_required_reason, gb_status_token, humanize_area_id

_DATA = Path(__file__).resolve().parents[2] / "data" / "games" / "gen1_rby"


def _json(name: str) -> dict:
    with (_DATA / name).open(encoding="utf-8") as stream:
        return json.load(stream)


_AREAS = _json("area_map.json")
_AREA_BY_MAP = {int(map_id): row["area_id"] for map_id, row in _AREAS.items()}
_AREA_NAMES: dict[str, str] = {}
for _row in _AREAS.values():
    _AREA_NAMES.setdefault(_row["area_id"], _row["name"])
# every map's name (tools/gen_gen1_map_names.py from pret): the client reports a map that is
# no encounter area as "map_<id>", and the board should say Viridian City, not Map 1
_MAP_NAMES = {int(key): value for key, value in _json("map_names.json").items()}
_MAP_ID = re.compile(r"map_(\d+)")
# an out-of-battle acquisition (starter, gift): the client names it gift_map_<map id>
_GIFT_MAP_ID = re.compile(r"gift_map_(\d+)\Z")
_INDEX_JSON = {int(key): int(value)
               for key, value in _json("species_index.json")["index_to_national"].items()}
_FAMILY = {int(key): int(value) for key, value in _json("evolutions.json")["family"].items()}
_ENCOUNTERS = _json("encounter_tables.json")
_MOVES = {int(row["id"]): row for row in _json("moves.json")["moves"]}
_TRAINERS = _json("trainers.json")

# Gen 1 (RBY, and pureRGB which inherits this via Gen1Adapter.calc_name) display name →
# damage-calc Gen 1 name, per kind. Pinned by tests/unit/test_calc_names_multigen.py
# against calc/calc/src/data/*.ts's RBY block.
_CALC_NAMES: dict[str, dict[str, str]] = {}
_calc_names_path = _DATA / "calc_names.json"
if _calc_names_path.exists():
    with _calc_names_path.open(encoding="utf-8") as _f:
        _CALC_NAMES = {k: v for k, v in json.load(_f).items() if isinstance(v, dict)}

# constants/type_constants.asm:5-26, data/types/names.asm:1-29.
_TYPE_NAMES = {
    0x00: "Normal", 0x01: "Fighting", 0x02: "Flying", 0x03: "Poison",
    0x04: "Ground", 0x05: "Rock", 0x06: "Bird", 0x07: "Bug", 0x08: "Ghost",
    0x14: "Fire", 0x15: "Water", 0x16: "Grass", 0x17: "Electric",
    0x18: "Psychic", 0x19: "Ice", 0x1A: "Dragon",
}
_TYPE_ID = {name: ident for ident, name in _TYPE_NAMES.items()}
_SPLIT = {"Physical": 0, "Special": 1, "Status": 2}

# This tuple was generated from all 151 pret/data/pokemon/base_stats/*.asm
# `db DEX_*` and `db TYPE1, TYPE2 ; type` lines, using the IDs in
# constants/type_constants.asm:5-26. The independent test parses those source
# files and checks every tuple; there is no ROM dependency at server runtime.
_NATIONAL_TYPES = (
    (0x16, 0x03),  # data/pokemon/base_stats/bulbasaur.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/ivysaur.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/venusaur.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/charmander.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/charmeleon.asm:5
    (0x14, 0x02),  # data/pokemon/base_stats/charizard.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/squirtle.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/wartortle.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/blastoise.asm:5
    (0x07, 0x07),  # data/pokemon/base_stats/caterpie.asm:5
    (0x07, 0x07),  # data/pokemon/base_stats/metapod.asm:5
    (0x07, 0x02),  # data/pokemon/base_stats/butterfree.asm:5
    (0x07, 0x03),  # data/pokemon/base_stats/weedle.asm:5
    (0x07, 0x03),  # data/pokemon/base_stats/kakuna.asm:5
    (0x07, 0x03),  # data/pokemon/base_stats/beedrill.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/pidgey.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/pidgeotto.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/pidgeot.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/rattata.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/raticate.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/spearow.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/fearow.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/ekans.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/arbok.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/pikachu.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/raichu.asm:5
    (0x04, 0x04),  # data/pokemon/base_stats/sandshrew.asm:5
    (0x04, 0x04),  # data/pokemon/base_stats/sandslash.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/nidoranf.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/nidorina.asm:5
    (0x03, 0x04),  # data/pokemon/base_stats/nidoqueen.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/nidoranm.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/nidorino.asm:5
    (0x03, 0x04),  # data/pokemon/base_stats/nidoking.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/clefairy.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/clefable.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/vulpix.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/ninetales.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/jigglypuff.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/wigglytuff.asm:5
    (0x03, 0x02),  # data/pokemon/base_stats/zubat.asm:5
    (0x03, 0x02),  # data/pokemon/base_stats/golbat.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/oddish.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/gloom.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/vileplume.asm:5
    (0x07, 0x16),  # data/pokemon/base_stats/paras.asm:5
    (0x07, 0x16),  # data/pokemon/base_stats/parasect.asm:5
    (0x07, 0x03),  # data/pokemon/base_stats/venonat.asm:5
    (0x07, 0x03),  # data/pokemon/base_stats/venomoth.asm:5
    (0x04, 0x04),  # data/pokemon/base_stats/diglett.asm:5
    (0x04, 0x04),  # data/pokemon/base_stats/dugtrio.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/meowth.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/persian.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/psyduck.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/golduck.asm:5
    (0x01, 0x01),  # data/pokemon/base_stats/mankey.asm:5
    (0x01, 0x01),  # data/pokemon/base_stats/primeape.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/growlithe.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/arcanine.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/poliwag.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/poliwhirl.asm:5
    (0x15, 0x01),  # data/pokemon/base_stats/poliwrath.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/abra.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/kadabra.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/alakazam.asm:5
    (0x01, 0x01),  # data/pokemon/base_stats/machop.asm:5
    (0x01, 0x01),  # data/pokemon/base_stats/machoke.asm:5
    (0x01, 0x01),  # data/pokemon/base_stats/machamp.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/bellsprout.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/weepinbell.asm:5
    (0x16, 0x03),  # data/pokemon/base_stats/victreebel.asm:5
    (0x15, 0x03),  # data/pokemon/base_stats/tentacool.asm:5
    (0x15, 0x03),  # data/pokemon/base_stats/tentacruel.asm:5
    (0x05, 0x04),  # data/pokemon/base_stats/geodude.asm:5
    (0x05, 0x04),  # data/pokemon/base_stats/graveler.asm:5
    (0x05, 0x04),  # data/pokemon/base_stats/golem.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/ponyta.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/rapidash.asm:5
    (0x15, 0x18),  # data/pokemon/base_stats/slowpoke.asm:5
    (0x15, 0x18),  # data/pokemon/base_stats/slowbro.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/magnemite.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/magneton.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/farfetchd.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/doduo.asm:5
    (0x00, 0x02),  # data/pokemon/base_stats/dodrio.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/seel.asm:5
    (0x15, 0x19),  # data/pokemon/base_stats/dewgong.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/grimer.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/muk.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/shellder.asm:5
    (0x15, 0x19),  # data/pokemon/base_stats/cloyster.asm:5
    (0x08, 0x03),  # data/pokemon/base_stats/gastly.asm:5
    (0x08, 0x03),  # data/pokemon/base_stats/haunter.asm:5
    (0x08, 0x03),  # data/pokemon/base_stats/gengar.asm:5
    (0x05, 0x04),  # data/pokemon/base_stats/onix.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/drowzee.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/hypno.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/krabby.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/kingler.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/voltorb.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/electrode.asm:5
    (0x16, 0x18),  # data/pokemon/base_stats/exeggcute.asm:5
    (0x16, 0x18),  # data/pokemon/base_stats/exeggutor.asm:5
    (0x04, 0x04),  # data/pokemon/base_stats/cubone.asm:5
    (0x04, 0x04),  # data/pokemon/base_stats/marowak.asm:5
    (0x01, 0x01),  # data/pokemon/base_stats/hitmonlee.asm:5
    (0x01, 0x01),  # data/pokemon/base_stats/hitmonchan.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/lickitung.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/koffing.asm:5
    (0x03, 0x03),  # data/pokemon/base_stats/weezing.asm:5
    (0x04, 0x05),  # data/pokemon/base_stats/rhyhorn.asm:5
    (0x04, 0x05),  # data/pokemon/base_stats/rhydon.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/chansey.asm:5
    (0x16, 0x16),  # data/pokemon/base_stats/tangela.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/kangaskhan.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/horsea.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/seadra.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/goldeen.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/seaking.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/staryu.asm:5
    (0x15, 0x18),  # data/pokemon/base_stats/starmie.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/mrmime.asm:5
    (0x07, 0x02),  # data/pokemon/base_stats/scyther.asm:5
    (0x19, 0x18),  # data/pokemon/base_stats/jynx.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/electabuzz.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/magmar.asm:5
    (0x07, 0x07),  # data/pokemon/base_stats/pinsir.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/tauros.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/magikarp.asm:5
    (0x15, 0x02),  # data/pokemon/base_stats/gyarados.asm:5
    (0x15, 0x19),  # data/pokemon/base_stats/lapras.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/ditto.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/eevee.asm:5
    (0x15, 0x15),  # data/pokemon/base_stats/vaporeon.asm:5
    (0x17, 0x17),  # data/pokemon/base_stats/jolteon.asm:5
    (0x14, 0x14),  # data/pokemon/base_stats/flareon.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/porygon.asm:5
    (0x05, 0x15),  # data/pokemon/base_stats/omanyte.asm:5
    (0x05, 0x15),  # data/pokemon/base_stats/omastar.asm:5
    (0x05, 0x15),  # data/pokemon/base_stats/kabuto.asm:5
    (0x05, 0x15),  # data/pokemon/base_stats/kabutops.asm:5
    (0x05, 0x02),  # data/pokemon/base_stats/aerodactyl.asm:5
    (0x00, 0x00),  # data/pokemon/base_stats/snorlax.asm:5
    (0x19, 0x02),  # data/pokemon/base_stats/articuno.asm:5
    (0x17, 0x02),  # data/pokemon/base_stats/zapdos.asm:5
    (0x14, 0x02),  # data/pokemon/base_stats/moltres.asm:5
    (0x1a, 0x1a),  # data/pokemon/base_stats/dratini.asm:5
    (0x1a, 0x1a),  # data/pokemon/base_stats/dragonair.asm:5
    (0x1a, 0x02),  # data/pokemon/base_stats/dragonite.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/mewtwo.asm:5
    (0x18, 0x18),  # data/pokemon/base_stats/mew.asm:5
)

# Every in-script nonbattle grant in pret/scripts/*.asm: `GivePokemon` occurs
# in the six files below; OaksLab.asm:931 calls AddPartyMon for the starter.
# Prize Menu calls GivePokemon at engine/events/prize_menu.asm:225 in map $89.
# Map IDs and area IDs: constants/map_constants.asm:41,107,171,219,251,256;
# data/games/gen1_rby/area_map.json is tested against those constants.
_GIFT_AREAS = frozenset({
    "oaks_lab",                 # scripts/OaksLab.asm:931; map_constants.asm:82
    "celadon_mansion_roof",    # scripts/CeladonMansionRoofHouse.asm:15-16
    "cinnabar_island",         # scripts/CinnabarLabFossilRoom.asm:79-80
    "saffron_city",            # scripts/FightingDojo.asm:245,279
    "mt_moon_pokecenter",      # scripts/MtMoonPokecenter.asm:47-48
    "silph_co",                # scripts/SilphCo7F.asm:310-311
    "celadon_game_corner",     # engine/events/prize_menu.asm:225
})
# The three scripts below set one species; Oak, Dojo, Cinnabar and Game Corner
# offer choices (same pret script lines as above).
_FIXED_GIFTS = frozenset({"celadon_mansion_roof", "mt_moon_pokecenter", "silph_co"})
# Existing read-only test imports used these names; aliases preserve the data
# surface without restoring any pre-rewrite National-Dex interpretation.
_FIXED_SPECIES_GIFTS = _FIXED_GIFTS
_GEN1_ENCOUNTERS = _ENCOUNTERS

# Script-set fixed wild battles, by (map id, National Dex number). Read from
# static_encounters.json's "statics" map (map id -> internal species ids), converted to
# (map, natdex) here because that is the namespace the client's area_id already uses
# (lua/gen1/client.lua:644 builds "static_<map>_<dex>"). The JSON's own species list is
# INTERNAL-index (matching the file's own doc comment); it was hand-verified to equal the
# nine (map, natdex) pairs this frozenset used to carry literally, so this is a data-driven
# equivalent, not a behaviour change: Route12.asm:25-36; Route16.asm:25-36;
# PowerPlant.asm:39-52,113; SeafoamIslandsB4F.asm:148,162; VictoryRoad2F.asm:100,142;
# CeruleanCaveB1F.asm:25,37; PokemonTower6F.asm:35-39. PowerPlant's six Voltorb and two
# Electrode object events are additionally pinned by data/maps/objects/PowerPlant.asm:28-36.
_STATICS_JSON = _json("static_encounters.json")
_STATIC_SITES = frozenset(
    (int(map_id), gen1_codec.internal_to_natdex(internal))
    for map_id, species_list in _STATICS_JSON["statics"].items()
    for internal in species_list
)
_STATIC_ID = re.compile(r"static_(\d+)_(\d+)\Z")
_KEY = re.compile(r"[0-9A-F]{4}:[0-9A-F]{4}:[0-9A-F]{2}\Z")
_ROM_VARIANT = {"Red": "red", "red": "red", "red_ap": "red",
                "Blue": "blue", "blue": "blue", "blue_ap": "blue",
                "Yellow": "yellow", "yellow": "yellow"}


def _natdex(internal: int) -> int:
    try:
        return gen1_codec.internal_to_natdex(internal)
    except (TypeError, ValueError):
        return 0


# Red/Blue require the SLink companion patch (owner 2026-10-02); Yellow (zero free WRAM) and the
# Archipelago builds (red_ap/blue_ap) do not.
_COMPANION_ROM_TYPES = frozenset({"Red", "Blue", "red", "blue"})


class Gen1Adapter(GameAdapter):
    """One per-player view of a Gen 1 cartridge; ROM encounters can override retail."""

    @staticmethod
    def companion_refusal(hello):
        # A patched Red/Blue is only the header-named family on the wire ("named"), so the
        # evidence is the client's own probe of the patch's mailbox: `panel` (lua/gen1/client.lua).
        rom_type = hello.get("rom_type")
        if rom_type in _COMPANION_ROM_TYPES and hello.get("panel") is not True:
            return companion_required_reason(rom_type)
        return None

    @classmethod
    def supports_randomized(cls, rom_type: str) -> bool:
        return isinstance(rom_type, str) and rom_type in _ROM_VARIANT

    def __init__(self, **kwargs):
        rom_type = kwargs.get("rom_type") or "red"
        if rom_type not in _ROM_VARIANT:
            # PLAN.md §4 row 2 / §11.2 B2: an unrecognised rom_type used to default to
            # "red" silently — the exact bug class the Gen 2 registry comment warns about.
            # Fail closed instead: an unknown variant is a routing bug, not a preference.
            raise ValueError(f"unrecognised Gen 1 rom_type: {rom_type!r}")
        self._variant = _ROM_VARIANT[rom_type]
        self._enc_variant = self._variant  # existing AP/table consumers inspect this label
        self._supports_companion = rom_type in _COMPANION_ROM_TYPES
        self._rom_encounters: dict[str, dict[str, list[dict]]] | None = None

    @property
    def game_id(self) -> str:
        return "gen1_rby"

    def is_gift_area(self, area_id: str) -> bool:
        if not isinstance(area_id, str):
            return False
        static = _STATIC_ID.fullmatch(area_id)
        return (area_id in _GIFT_AREAS or area_id == "gift" or area_id.startswith("gift_")
                or bool(static and (int(static[1]), int(static[2])) in _STATIC_SITES))

    def is_fixed_species_gift(self, area_id: str) -> bool:
        static = _STATIC_ID.fullmatch(area_id) if isinstance(area_id, str) else None
        return area_id in _FIXED_GIFTS or bool(
            static and (int(static[1]), int(static[2])) in _STATIC_SITES)

    def evo_family(self, species_id: int) -> int:
        # Representative in the SAME id space as the input (internal index), so the clause's
        # equality test composes: evo_family(evo_family(x)) == evo_family(x). Ids with no dex
        # entry (MissingNo. holes, glitch bytes) are their own family, never one shared bucket.
        dex = _natdex(species_id)
        if not dex:
            return species_id
        return gen1_codec.natdex_to_internal(_FAMILY.get(dex, dex))

    def gender_from_key(self, key: str, species_id: int) -> str:
        # No gender byte/ratio in the R/B/Y struct: pokemon_data_constants.asm:26-56.
        return ""

    def species_types(self, species_id: int) -> tuple[int, int] | None:
        dex = _natdex(species_id)
        return _NATIONAL_TYPES[dex - 1] if dex else None

    def is_shiny(self, key: str) -> bool:
        # No shiny predicate in the Gen 1 mon struct: pokemon_data_constants.asm:26-56.
        return False

    def parse_ot_id(self, key: str) -> str:
        return key.split(":")[1] if self.is_valid_mon_key(key) else ""

    def is_valid_mon_key(self, key: str) -> bool:
        return isinstance(key, str) and _KEY.fullmatch(key) is not None

    def species_name(self, species_id: int) -> str:
        dex = _natdex(species_id)
        # The contract test checks all 151 shared display names against
        # pret/data/pokemon/names.asm:1-193 in internal-index order.
        return national_species_name(dex, False) if dex else f"#{species_id}"

    def type_name(self, type_id: int) -> str:
        return _TYPE_NAMES.get(type_id, f"Type #{type_id}")

    def rival_trainer_ids(self) -> set[int]:
        # trainer_constants.asm:42,59-60; OPP_ID_OFFSET at :1.
        return {int(ident) for ident, name in _TRAINERS["classes"].items() if name == "Rival"}

    def party_blob_size(self) -> int:
        # pokemon_data_constants.asm:47,56,58-61; text_constants.asm:3.
        return gen1_codec.PARTY_MON_SIZE + 2 * gen1_codec.NAME_SIZE

    def validate_party_blob(self, blob_hex: str | bytes) -> bool:
        """Validate the 66-byte transfer blob. The base interface has no call hook."""
        try:
            blob = bytes.fromhex(blob_hex) if isinstance(blob_hex, str) else blob_hex
            if not isinstance(blob, bytes) or len(blob) != self.party_blob_size():
                return False
            mon = gen1_codec.decode_party_mon(blob[:gen1_codec.PARTY_MON_SIZE])
            return _natdex(mon["species"]) > 0
        except ValueError:
            return False

    def supports_abilities(self) -> bool:
        return False

    def supports_explode_mode(self) -> bool:
        return True

    def status_token(self, status_cond: int) -> str:
        # constants/status_constants.asm: SLP low three bits, PSN/BRN/FRZ/PAR.
        return gb_status_token(status_cond)

    def reports_box_census(self) -> bool:
        return True   # lua/gen1/client.lua pc_boxes_generation

    def supports_info_panel(self) -> bool:
        return self._supports_companion

    def native_trade_ui(self) -> bool:
        return self._supports_companion

    def info_panel_width(self) -> int:
        # Game Boy tilemap width; constants/map_constants.asm map geometry uses 20.
        return 20 if self.supports_info_panel() else 0

    def sprite_src(self, species_id: int) -> str:
        dex = _natdex(species_id)
        if not dex:
            return ""
        return ("https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/"
                f"versions/generation-i/red-blue/transparent/{dex}.png")

    def sprite_html(self, species_id: int, form: int = 0) -> str:
        dex = _natdex(species_id)
        if not dex:
            return ""
        url = self.sprite_src(species_id)
        # The transparent Gen 1 art is a 56px cell centred on a 96px canvas (20px pad),
        # so the 40px window shows the image at 40*96/56 = 69px, offset by 20*69/96 = 14px:
        # the mon then fills the box the way Gen 2's 56px sprites do. Inline sizes, so the
        # shared `.mon-sprite` width in slink.css cannot undo the crop.
        return (
            '<span style="display:inline-block;width:40px;height:40px;'
            'overflow:hidden;vertical-align:middle">'
            f'<img class="mon-sprite" data-species="{dex}" src="{url}" '
            'width="69" height="69" loading="lazy" alt="" '
            'onerror="this.style.visibility=&#39;hidden&#39;" '
            'style="image-rendering:pixelated;width:69px;height:69px;max-width:none;'
            'margin:-14px"></span>'
        )

    def ability_name(self, ability_id: int, species_id: int = 0) -> str:
        return ""

    def ability_description(self, ability_id: int) -> str:
        return ""

    def trainer_info(self, trainer_id: int) -> tuple[str, str]:
        cls = _TRAINERS["classes"].get(str(trainer_id), "")
        if not cls:
            return "", ""
        named = _TRAINERS["named_trainers"].get(str(trainer_id), {})
        names = set(named.values())
        return (next(iter(names)) if len(names) == 1 else "", cls)

    def item_name(self, item_id: int) -> str:
        return ITEM_NAMES.get(item_id, f"Item #{item_id}") if item_id else ""

    def calc_name(self, kind: str, name: str) -> str:
        if not name:
            return name
        return _CALC_NAMES.get(kind, {}).get(name, name)

    def calc_profile(self) -> dict | None:
        """Vanilla Gen 1 calc numbers are verified. Red/Blue share one vendored,
        pret-verified trainer setdex; Yellow has its own (docs/calc_multigen/HANDOFF.md
        task 5/8). Gen1PureRGBAdapter overrides this with its OWN dex ("purergb") and
        setdex rather than disabling the calc; its known limits are in
        docs/calc_multigen/PURERGB_MECHANICS.md."""
        if self._variant == "yellow":
            return {"gen": 1, "dex": "vanilla",
                    "sets": {"file": "Yellow.js", "var": "CUSTOMSETDEX_Y"}}
        return {"gen": 1, "dex": "vanilla",
                "sets": {"file": "RedBlue.js", "var": "CUSTOMSETDEX_RB"}}

    def calc_stats(self, detail: dict) -> dict | None:
        """Decode DVs/stat exp/computed stats from a party blob (the 44-byte party
        struct alone, or the 66-byte transfer blob with the OT/nick tail -- either
        way only the first 44 bytes are read). Shared by pureRGB, whose blob layout
        is identical (Gen1PureRGBAdapter.validate_party_blob decodes the same 44
        bytes via the same gen1_codec).

        gen1_codec's DV/stat_exp/stat dicts key the Speed field "spd" (home/move_mon.asm
        nibble order); the calc_stats contract keys it "spe" -- map on the way out.

        With no blob_hex but a `dvs_raw` word (a Gen 1 enemy's live wEnemyMon struct,
        lua/gen1/client.lua enemy_party -- wild mons have no party record to read stat
        exp from), return DVs decoded from the raw word and stat_exp all 0. No "stats"
        key: the in-game computed stats are unknown without the party record.
        """
        blob_hex = detail.get("blob_hex")
        if blob_hex:
            try:
                blob = bytes.fromhex(blob_hex) if isinstance(blob_hex, str) else blob_hex
                mon = gen1_codec.decode_party_mon(bytes(blob[:gen1_codec.PARTY_MON_SIZE]))
            except (ValueError, TypeError):
                return None
            dvs, exp = mon["dvs"], mon["stat_exp"]
            return {
                "dvs": {"atk": dvs["atk"], "def": dvs["def"], "spe": dvs["spd"], "spc": dvs["spc"]},
                "stat_exp": {"hp": exp["hp"], "atk": exp["atk"], "def": exp["def"],
                             "spe": exp["spd"], "spc": exp["spc"]},
                "stats": {"hp": mon["max_hp"], "atk": mon["atk"], "def": mon["def"],
                          "spa": mon["spc"], "spd": mon["spc"], "spe": mon["spd"]},
            }
        dvs_raw = detail.get("dvs_raw")
        if not isinstance(dvs_raw, int) or isinstance(dvs_raw, bool) or not 0 <= dvs_raw < 1 << 16:
            return None
        # home/move_mon.asm:109-153 nibble order, same as gen1_codec._dv_parts.
        atk, def_ = (dvs_raw >> 12) & 0xF, (dvs_raw >> 8) & 0xF
        spd, spc = (dvs_raw >> 4) & 0xF, dvs_raw & 0xF
        return {
            "dvs": {"atk": atk, "def": def_, "spe": spd, "spc": spc},
            "stat_exp": {"hp": 0, "atk": 0, "def": 0, "spe": 0, "spc": 0},
        }

    def area_display_name(self, area_id: str) -> str:
        if area_id in _AREA_NAMES:
            return _AREA_NAMES[area_id]
        plain = _MAP_ID.fullmatch(area_id)
        if plain and int(plain[1]) in _MAP_NAMES:
            return _MAP_NAMES[int(plain[1])]
        gift = _GIFT_MAP_ID.fullmatch(area_id)
        if gift:
            return f"{_MAP_NAMES.get(int(gift[1]), f'Map {gift[1]}')} — Gift"
        static = _STATIC_ID.fullmatch(area_id)
        if static:
            map_id, dex = int(static[1]), int(static[2])
            location = _AREA_NAMES.get(_AREA_BY_MAP.get(map_id, ""), f"Map {map_id}")
            name = national_species_name(dex, False) if 1 <= dex <= 151 else f"#{dex}"
            return f"{location} — {name}"
        return humanize_area_id(area_id)

    def to_national_dex(self, species_id: int) -> int:
        # codec table is generated from pret/data/pokemon/dex_order.asm:3-192.
        return _natdex(species_id)

    def gender_symbol(self, gender: str) -> str:
        return ""

    def form_sprite_id(self, species_id: int) -> int | None:
        return None

    def move_name(self, move_id: int) -> str:
        row = _MOVES.get(move_id)
        return row["name"] if row else ""

    def move_data(self, move_id: int) -> dict | None:
        row = _MOVES.get(move_id)
        if not row:
            return None
        return {"name": row["name"], "type_id": _TYPE_ID[row["type"]],
                "type_name": row["type"], "power": row["power"],
                "accuracy": row["accuracy"], "pp": row["pp"],
                "split": _SPLIT[row["split"]]}

    def encounter_table(self, area_id: str) -> dict[str, list[dict]] | None:
        if self._rom_encounters is not None:
            raw = self._rom_encounters.get(area_id)
        else:
            raw = _ENCOUNTERS[self._variant].get(area_id)
        if raw is None:
            return None
        # Shipped/scanned encounter entries are NatDex-keyed (see
        # tools/gen_gen1_encounters.py:350-390 and gen1_rom_scan.py:682-692),
        # while the shared renderer calls sprite_html(entry.species_id) and the
        # client/adapter contract makes that parameter an INTERNAL index.
        return {
            method: [{**entry, "species_id": gen1_codec.natdex_to_internal(entry["species_id"])}
                     for entry in entries]
            for method, entries in raw.items()
        }

    def ingest_rom_content(self, payload: dict) -> dict[str, dict[str, list[dict]]] | None:
        from .gen1_rom_scan import build_encounter_tables, parse_client_content

        content = parse_client_content(payload)
        # The scanner names a NatDex entry before the presentation projection.
        return build_encounter_tables(content, _AREA_BY_MAP, _INDEX_JSON,
                                      lambda dex: national_species_name(dex, False))

    def rom_content_fingerprint(self, payload: dict) -> str | None:
        from .gen1_rom_scan import content_fingerprint, parse_client_content

        content = parse_client_content(payload)
        return content_fingerprint(content["variant"], content["wild"], content["fishing"])

    def use_rom_encounters(self, tables: dict[str, dict[str, list[dict]]] | None) -> None:
        self._rom_encounters = tables

    def stat_stage_labels(self) -> list[str]:
        # ram/wram.asm:543-576; base renderer has 7 slots, Gen 1 only 6 mods.
        return ["ATK", "DEF", "SPD", "SPC", "", "ACC", "EVA"]

    @property
    def mons_per_box(self) -> int:
        # constants/pokemon_data_constants.asm:58-61.
        return gen1_codec.BOX_CAPACITY

    @property
    def memorial_box_index(self) -> int:
        # constants/pokemon_data_constants.asm:61: twelve boxes, last is index 11.
        return gen1_codec.BOX_COUNT - 1
