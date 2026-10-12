"""Falsifiers for the Gen 3 record half of server/adapters/gen3_codec.py.

Everything here is synthetic: no ROM, no save file, no emulator.
"""

import json
from pathlib import Path

import pytest

from server.adapters import gen3_codec as codec


def test_rr_saved_party_matches_ram_side_lua_decoder_on_same_fixture_bytes():
    lupa = pytest.importorskip("lupa")
    root = Path(__file__).resolve().parents[2]
    image = (root / "tests/fixtures/gen3/rr_town.sav").read_bytes()
    sb1 = codec.parse_flash(image, cfru=True)["sb1"]
    profile = json.loads((root / "data/games/gen3_rr/profile.json").read_text())["titles"]["radical_red"]
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    module = runtime.execute((root / "lua/gen3/reads.lua").read_text(encoding="utf-8"))
    reads = module.new(runtime.table_from(profile, recursive=True), runtime.table(
        read_u8=lambda *_: 0, read_u32=lambda *_: 0,
        read_bytes=lambda *_: runtime.table()))
    expected = codec.party_from_save(image, rr=True)
    for slot, py in enumerate(expected):
        start = codec.SB1_PARTY_OFFSET + slot * codec.PARTY_MON_SIZE
        lua = reads.decode_party_mon(runtime.table(*sb1[start:start + codec.PARTY_MON_SIZE]))
        for field in ("personality", "ot_id", "nickname", "ot_name", "species", "level",
                      "hp", "max_hp", "held_item", "experience", "status"):
            assert lua[field] == py[field], field
        assert list(lua.moves.values()) == py["moves"]
        assert list(lua.pp.values()) == py["pp"]


def _mon(personality: int, *, party: bool = True) -> dict:
    """A fully populated synthetic record, distinct value in every field."""
    mon = {
        "personality": personality,
        "ot_id": 0xDEADBEEF,
        "nickname": "NIDOKING",
        "language": 2,
        "is_bad_egg": 0, "has_species": 1, "is_egg_flag": 0,
        "block_box_rs": 0, "flags_unused": 0,
        "ot_name": "ASH",
        "markings": 0x0A,
        "unknown": 0x1234,
        "species": 34, "held_item": 0x0C, "experience": 125000,
        "pp_bonuses": 0b11010011, "friendship": 200, "growth_filler": 0,
        "moves": [85, 89, 63, 231], "pp": [15, 10, 5, 10],
        "evs": {"hp": 252, "attack": 128, "defense": 6,
                "speed": 64, "sp_attack": 32, "sp_defense": 8},
        "contest": [1, 2, 3, 4, 5, 6],
        "pokerus": 0xF3, "met_location": 88, "met_level": 37,
        "met_game": 4, "pokeball": 3, "ot_gender": 1,
        "ivs": {"hp": 31, "attack": 30, "defense": 29,
                "speed": 28, "sp_attack": 27, "sp_defense": 26},
        "is_egg": 0, "ability_num": 1,
        "ribbons": 0x0000_2AAA,
    }
    if party:
        mon.update({"status": 0x00000040, "level": 54, "mail": 0xFF,
                    "hp": 111, "max_hp": 160, "attack": 120, "defense": 90,
                    "speed": 95, "sp_attack": 85, "sp_defense": 80})
    return mon


def _assert_same(sent: dict, got: dict) -> None:
    for field, value in sent.items():
        assert got[field] == value, field


# --- CONTROL: decode(encode(x)) == x for every permutation index -------------

@pytest.mark.parametrize("index", range(24))
def test_party_round_trip_every_permutation(index):
    mon = _mon(0x18000 + index)          # personality % 24 == index
    assert mon["personality"] % 24 == index
    raw = codec.encode_party_mon(mon)
    assert len(raw) == codec.PARTY_MON_SIZE
    back = codec.decode_party_mon(raw)
    _assert_same(mon, back)
    assert back["checksum_ok"] is True
    assert codec.encode_party_mon(back) == raw


@pytest.mark.parametrize("index", range(24))
def test_box_round_trip_every_permutation(index):
    mon = _mon(0x18000 + index, party=False)
    raw = codec.encode_box_mon(mon)
    assert len(raw) == codec.BOX_MON_SIZE
    back = codec.decode_box_mon(raw)
    _assert_same(mon, back)
    assert back["checksum_ok"] is True


def test_permutation_table_matches_pret():
    """src/pokemon.c#L2846-L2890. Rows 3 and 4 are the ones the Lua table
    (archive/gen3-old-client:lua/memory_gba.lua:622) has swapped; pin them explicitly."""
    assert codec.SUBSTRUCT_ORDER[0] == (0, 1, 2, 3)
    assert codec.SUBSTRUCT_ORDER[3] == (0, 3, 1, 2)
    assert codec.SUBSTRUCT_ORDER[4] == (0, 2, 3, 1)
    assert codec.SUBSTRUCT_ORDER[23] == (3, 2, 1, 0)
    assert len(set(codec.SUBSTRUCT_ORDER)) == 24
    for row in codec.SUBSTRUCT_ORDER:
        assert sorted(row) == [0, 1, 2, 3]


def test_substructs_land_at_the_permuted_position():
    """The Growth substruct of a PID%24==9 record sits at slot 3 (order
    (3,0,1,2)), so the species must be readable there after decryption."""
    mon = _mon(0x18000 + 9)
    raw = codec.encode_party_mon(mon)
    key = mon["personality"] ^ mon["ot_id"]
    plain = codec._xor_words(raw[0x20:0x50], key)
    growth_pos = codec.SUBSTRUCT_ORDER[9][0]
    assert growth_pos == 3
    at = growth_pos * 12
    assert int.from_bytes(plain[at:at + 2], "little") == mon["species"]
    assert int.from_bytes(plain[at + 2:at + 4], "little") == mon["held_item"]


# --- Checksum ---------------------------------------------------------------

def test_checksum_is_the_u16_sum_of_the_decrypted_halfwords():
    mon = _mon(0x10007)
    raw = codec.encode_party_mon(mon)
    plain = codec._xor_words(raw[0x20:0x50], mon["personality"] ^ mon["ot_id"])
    expected = sum(int.from_bytes(plain[i:i + 2], "little")
                   for i in range(0, 48, 2)) & 0xFFFF
    assert int.from_bytes(raw[0x1C:0x1E], "little") == expected
    assert codec.secure_checksum(plain) == expected


def test_corrupted_secure_block_is_refused():
    raw = bytearray(codec.encode_party_mon(_mon(0x10001)))
    raw[0x30] ^= 0xFF
    assert codec.decode_party_mon(bytes(raw))["checksum_ok"] is False


def test_corrupted_checksum_field_is_refused():
    raw = bytearray(codec.encode_party_mon(_mon(0x10001)))
    raw[0x1C] ^= 0x01
    assert codec.decode_party_mon(bytes(raw))["checksum_ok"] is False


def test_wrong_size_is_refused():
    with pytest.raises(ValueError):
        codec.decode_party_mon(b"\x00" * 80)
    with pytest.raises(ValueError):
        codec.decode_box_mon(b"\x00" * 100)


# --- RR / CFRU fixed-order, unencrypted -------------------------------------

def test_rr_record_is_plaintext_in_fixed_order():
    """CFRU_NO_ENCRYPT: Growth/Attacks/EVs/Misc sit at +0x20/+0x2C/+0x38/+0x44
    with no XOR (lua/games/gen3_frlge.lua:342, archive/gen3-old-client:lua/memory_gba.lua:570-572)."""
    mon = _mon(0x18005)          # a PID whose vanilla order is NOT fixed
    assert codec.SUBSTRUCT_ORDER[5] != (0, 1, 2, 3)
    raw = codec.encode_party_mon(mon, rr=True)
    assert int.from_bytes(raw[0x20:0x22], "little") == mon["species"]
    assert int.from_bytes(raw[0x22:0x24], "little") == mon["held_item"]
    assert int.from_bytes(raw[0x2C:0x2E], "little") == mon["moves"][0]
    assert raw[0x34] == mon["pp"][0]
    assert raw[0x38] == mon["evs"]["hp"]
    assert raw[0x44] == mon["pokerus"]
    back = codec.decode_party_mon(raw, rr=True)
    _assert_same(mon, back)
    assert back["checksum_ok"] is None   # CFRU never validates it


def test_rr_decode_of_a_vanilla_record_disagrees():
    """Guard against silently decoding an encrypted record in rr mode."""
    mon = _mon(0x18005)
    vanilla = codec.encode_party_mon(mon)
    assert codec.decode_party_mon(vanilla, rr=True)["species"] != mon["species"]


def test_rr_checksum_field_round_trips_as_stored():
    mon = _mon(0x10000)
    mon["checksum"] = 0
    raw = codec.encode_party_mon(mon, rr=True)
    assert int.from_bytes(raw[0x1C:0x1E], "little") == 0
    assert codec.decode_party_mon(raw, rr=True)["checksum"] == 0


# --- CFRU CompressedPokemon expansion ---------------------------------------

def test_expand_compressed_box_mon_matches_the_lua_field_map():
    """Mirrors archive/gen3-old-client:lua/memory_gba.lua:1037-1104 (createBoxMonFromCompressed)."""
    src = bytearray(codec.COMPRESSED_MON_SIZE)
    header = bytes(range(0x1C))
    src[0x00:0x1C] = header
    growth = bytes(range(0x40, 0x4B))            # 11 bytes
    src[0x1C:0x27] = growth
    moves = [0x3FF, 0x001, 0x2AA, 0x155]
    packed = sum(m << (10 * i) for i, m in enumerate(moves))
    src[0x27:0x2C] = packed.to_bytes(5, "little")
    evs = bytes([252, 6, 0, 252, 0, 0])
    src[0x2C:0x32] = evs
    misc = bytes([0xF3, 88, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
    src[0x32:0x3A] = misc

    out = codec.expand_compressed_box_mon(bytes(src))
    assert len(out) == codec.BOX_MON_SIZE
    assert out[0x00:0x1C] == header
    assert out[0x1C:0x20] == b"\x00\x00\x00\x00"        # checksum stays zero
    assert out[0x20:0x2B] == growth
    assert out[0x2B] == 0                               # Growth pad byte
    for i, move in enumerate(moves):
        assert int.from_bytes(out[0x2C + i * 2:0x2E + i * 2], "little") == move
    assert out[0x34:0x38] == b"\x00\x00\x00\x00"        # PP is not stored
    assert out[0x38:0x3E] == evs
    assert out[0x3E:0x44] == b"\x00" * 6                # contest bytes
    assert out[0x44:0x4C] == misc
    assert out[0x4C:0x50] == b"\x00\x00\x00\x80"       # RR ROM 0x090B696A..76

    # And the expansion is readable by the rr decoder.
    mon = codec.decode_box_mon(out, rr=True)
    assert mon["moves"] == moves
    assert mon["evs"]["hp"] == 252
    assert mon["pokerus"] == 0xF3


def test_expand_compressed_refuses_wrong_size():
    with pytest.raises(ValueError):
        codec.expand_compressed_box_mon(b"\x00" * 80)


# --- Names ------------------------------------------------------------------

def test_name_round_trip_and_unknown_glyph_token():
    assert codec.decode_name(codec.encode_name("ASH", 7)) == "ASH"
    assert codec.encode_name("A", 7) == bytes([0xBB] + [0xFF] * 6)
    assert codec.decode_name(bytes([0xBB, 0x7A, 0xFF, 0x00])) == "A<$7A>"
    assert codec.encode_name("A<$7A>", 7)[:2] == bytes([0xBB, 0x7A])
    assert codec.decode_name(bytes([0xFF, 0xBB])) == ""      # stops at EOS


@pytest.mark.parametrize("name", ["<$FF>", "<$FF>A", "A<$ff>B", "ASH<$FF>"])
def test_name_refuses_embedded_terminator_escape(name):
    with pytest.raises(ValueError, match="terminator"):
        codec.encode_name(name, 7)


def test_charmap_letters_and_digits_match_pret():
    # charmap.txt#L67-L156 at the pinned commit.
    assert codec.FR_CHARMAP[0xBB] == "A" and codec.FR_CHARMAP[0xD4] == "Z"
    assert codec.FR_CHARMAP[0xD5] == "a" and codec.FR_CHARMAP[0xEE] == "z"
    assert codec.FR_CHARMAP[0xA1] == "0" and codec.FR_CHARMAP[0xAA] == "9"
    assert codec.FR_CHARMAP[0xB8] == "," and codec.FR_CHARMAP[0xB7] == "¥"
    assert codec.FR_CHARMAP[0xB9] == "×" and codec.FR_CHARMAP[0xBA] == "/"
    assert codec.FR_CHARMAP[0x00] == " " and codec.FR_CHARMAP[0xF0] == ":"


def test_raw_name_bytes_are_preserved_on_re_encode():
    mon = _mon(0x10002)
    raw = bytearray(codec.encode_party_mon(mon))
    raw[0x08:0x12] = bytes([0xBB, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0, 0, 0])
    mon2 = codec.decode_party_mon(bytes(raw))
    assert codec.encode_party_mon(mon2)[0x08:0x12] == raw[0x08:0x12]


def test_unencodable_character_is_refused():
    with pytest.raises(ValueError):
        codec.encode_name("☃", 7)
    with pytest.raises(ValueError):
        codec.encode_name("TOOLONGNAME", 7)
