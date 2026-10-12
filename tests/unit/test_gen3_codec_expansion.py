"""X2/X3: the pokeemerald-expansion reference build's flash layout in gen3_codec.

Every size/offset is bound to this build's own compiler facts
(data/games/gen3_exp/28877d73/facts.json, the X1 offsetof probe at expansion e8bd1cd7),
and records decode through the build's masked layout (profile.derived, X2).
"""
import json
from pathlib import Path

import pytest

from server.adapters import gen3_codec as C
from server.adapters.gen3_expansion import EXPANSION_PARTY_LAYOUT

ROOT = Path(__file__).resolve().parents[2]
FACTS = json.loads((ROOT / "data/games/gen3_exp/28877d73/facts.json").read_text(encoding="utf-8"))
T = C.TITLE_EXPANSION


def test_expansion_save_constants_are_the_builds_compiler_facts():
    s = FACTS["structs"]
    assert C._TITLE_SAVE_SIZES[T] == (s["SaveBlock2"]["size"], s["SaveBlock1"]["size"])
    assert C._TITLE_STORAGE_SIZES[T] == s["PokemonStorage"]["size"]
    assert C._TITLE_PARTY_OFFSETS[T] == (s["SaveBlock1"]["fields"]["playerPartyCount"]["offset"],
                                         s["SaveBlock1"]["fields"]["playerParty"]["offset"])
    storage = s["PokemonStorage"]["fields"]
    assert (storage["boxes"]["offset"], storage["boxNames"]["offset"]) == (C.BOX_DATA_OFFSET, C.BOX_NAMES_OFFSET)
    # the rom_type the client reports names the same layout
    assert C._title("emerald_expansion_28877d73") == T


def test_expansion_storage_is_bigger_than_vanilla_so_its_last_sector_is_too():
    layout = C.slot_layout(title=T)
    assert [e["size"] for e in layout if e["id"] == 13] == [FACTS["structs"]["PokemonStorage"]["size"] - 8 * 0xF80]
    assert [e["size"] for e in C.slot_layout(title="emerald") if e["id"] == 13] == [C.STORAGE_SIZE - 8 * 0xF80]


def _mon(personality, species):
    return {"personality": personality, "ot_id": 0x20250927, "nickname": "EXP", "language": 2,
            "is_bad_egg": 0, "has_species": 1, "is_egg_flag": 0, "block_box_rs": 0, "flags_unused": 0,
            "ot_name": "EXP", "markings": 0, "unknown": 0, "species": species, "held_item": 0,
            "experience": 135, "pp_bonuses": 0, "friendship": 70, "growth_filler": 0,
            "moves": [33, 45, 0, 0], "pp": [35, 40, 0, 0],
            "evs": dict.fromkeys(("hp", "attack", "defense", "speed", "sp_attack", "sp_defense"), 0),
            "contest": [0] * 6, "pokerus": 0, "met_location": 16, "met_level": 5, "met_game": 3,
            "pokeball": 4, "ot_gender": 0,
            "ivs": dict.fromkeys(("hp", "attack", "defense", "speed", "sp_attack", "sp_defense"), 10),
            "is_egg": 0, "ability_num": 0, "ribbons": 0, "status": 0, "level": 5, "mail": 0xFF,
            "max_hp": 20, "hp": 20, "attack": 11, "defense": 10, "speed": 9, "sp_attack": 10,
            "sp_defense": 10}


@pytest.fixture
def expansion_image():
    layout = C.slot_layout(title=T)
    sb2_size, sb1_size = C._TITLE_SAVE_SIZES[T]
    count_off, party_off = C._TITLE_PARTY_OFFSETS[T]
    sb1 = bytearray(sb1_size)
    sb1[count_off] = 1
    # species 1572 needs all 11 bits; nothing in a vanilla-shaped record sets the new lanes
    sb1[party_off:party_off + C.PARTY_MON_SIZE] = C.encode_party_mon(_mon(0x18000, 1572))
    storage = bytearray(C._TITLE_STORAGE_SIZES[T])
    where = C.BOX_DATA_OFFSET + (13 * C.MONS_PER_BOX + 29) * C.BOX_MON_SIZE   # the last slot
    storage[where:where + C.BOX_MON_SIZE] = C.encode_box_mon(_mon(0x18007, 258))
    storage[-1] = 0x5A   # fusions[] (expansion-only tail): checksummed only at expansion's size
    blocks = {"sb2": bytes(sb2_size), "sb1": bytes(sb1), "storage": bytes(storage)}
    image = bytearray(b"\xFF" * C.FLASH_SIZE)
    for entry in layout:
        chunk = blocks[entry["object"]][entry["offset"]:entry["offset"] + entry["size"]]
        image[entry["id"] * C.SECTOR_SIZE:(entry["id"] + 1) * C.SECTOR_SIZE] = \
            C.write_sector(chunk, entry["id"], 2, layout)   # counter 2 -> slot 0
    return bytes(image)


def test_synthetic_expansion_flash_round_trips_through_the_masked_layout(expansion_image):
    image = expansion_image
    assert C.qualify_flash(image, title=T) == (True, "ok")
    # the vanilla Emerald layout checksums the last storage sector over too few bytes
    assert C.qualify_flash(image, title="emerald")[0] is False
    party = C.party_from_save(image, title=T, layout=EXPANSION_PARTY_LAYOUT)
    assert [(m["species"], m["checksum_ok"]) for m in party] == [(1572, True)]
    boxes = C.boxes_from_save(image, title=T, layout=EXPANSION_PARTY_LAYOUT)
    assert len(boxes) == 14 and boxes[13][29]["species"] == 258


def test_expansion_records_refuse_a_decode_without_the_builds_layout():
    image = bytes(C.FLASH_SIZE)
    with pytest.raises(ValueError, match="layout"):
        C.party_from_save(image, title=T)
    with pytest.raises(ValueError, match="layout"):
        C.boxes_from_save(image, title=T)


@pytest.mark.parametrize("decode", [C.party_from_save, C.boxes_from_save])
@pytest.mark.parametrize("missing", list(EXPANSION_PARTY_LAYOUT))
def test_expansion_save_requires_every_record_field(expansion_image, decode, missing):
    layout = {key: value for key, value in EXPANSION_PARTY_LAYOUT.items() if key != missing}
    with pytest.raises(ValueError, match=missing):
        decode(expansion_image, title=T, layout=layout)


@pytest.mark.parametrize("decode", [C.party_from_save, C.boxes_from_save])
def test_unrelated_nonempty_layout_cannot_enable_expansion_decode(expansion_image, decode):
    with pytest.raises(ValueError, match="layout"):
        decode(expansion_image, title=T, layout={"unrelated": 1})
