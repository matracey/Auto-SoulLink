"""Reference-build adapter contract; literals checked against expansion e8bd1cd7."""
import json
from pathlib import Path

import pytest

from server.adapters import (
    foundation_for_rom_type,
    game_id_for_rom_type,
    get_adapter,
    variant_label,
)
from server.adapters.base import GamePresentationAdapter, GameRulesAdapter

ROM_TYPE = "emerald_expansion_28877d73"


def test_reference_build_is_registered_and_routed():
    """The approved exact expansion build routes directly to its own adapter."""
    adapter = get_adapter("gen3_exp", rom_type=ROM_TYPE)
    assert isinstance(adapter, (GameRulesAdapter, GamePresentationAdapter))
    assert adapter.game_id == "gen3_exp"
    assert adapter.rom_type == ROM_TYPE
    assert game_id_for_rom_type(ROM_TYPE) == "gen3_exp"
    assert foundation_for_rom_type(ROM_TYPE) == "gen3_exp"
    assert variant_label(ROM_TYPE) == "Emerald Expansion 1.17.0 (28877d73)"
    # Bulbasaur, Pikachu and Pound in pinned include/constants + src/data tables.
    assert adapter.species_name(1) == "Bulbasaur"
    assert adapter.species_types(1) == (13, 4)
    assert adapter.type_name(13) == "Grass"
    assert adapter.evo_family(25) == 172
    assert adapter.move_name(1) == "Pound"


def test_reference_presentation_and_explicit_unsupported_surfaces():
    a = get_adapter("gen3_exp")
    assert a.gender_from_key("00000000:12345678", 1010) == "female"
    assert a.gender_from_key("000000FF:12345678", 678) == "male"
    assert a.gender_from_key("00000000:12345678", 137) == "genderless"
    assert a.to_national_dex(1572) == 970
    assert a.species_name(1572) == "Glimmora"
    assert a.species_abilities(1) == (65, 0, 34)
    assert a.ability_name(318) == "Spicy Spray"
    assert a.item_name(1) == "Poké Ball"  # vanilla item 1 is Master Ball
    assert a.item_name(873) == "Glimmoranite"
    assert a.move_data(847) == {"name": "Malignant Chain", "type_id": 4,
                              "type_name": "Poison", "power": 100, "accuracy": 100,
                              "pp": 5, "split": 1}
    assert a.move_data(1)["split"] == 0
    assert a.move_data(14)["split"] == 2  # Swords Dance
    assert a.sprite_src(1572).endswith("/970.png")
    assert '/970.png' in a.sprite_html(1572)
    assert a.form_sprite_id(1572) is None
    assert a.gym_badge_slugs(ROM_TYPE)[0] == (17, "Stone Badge")
    assert a.gym_badge_slugs(ROM_TYPE)[-1] == (24, "Rain Badge")
    assert a.calc_profile() == {"gen": 9, "dex": "expansion", "sets": {"file": "EmeraldExpansion.js", "var": "CUSTOMSETDEX_EE"}}
    assert a.calc_stats({}) is None
    assert a.calc_nature("00000000:00000000") == "Hardy"
    assert a.calc_name("species", "Mr. Mime") == "Mr. Mime"
    assert a.calc_name("species", "Aegislash") == "Aegislash-Shield"
    wild = json.loads((Path(__file__).resolve().parents[2] /
                       "data/games/gen3_exp/28877d73/expansion_encounters.json").read_text(encoding="utf-8"))
    assert a.encounter_table("route_101") == wild["encounters"]["route_101"]
    assert a.encounter_table("__unknown_area__") is None
    assert a.trainers_for_area("route_101") == []
    # XC4: trainer panels now come from the pinned build's gTrainers (Sawyer = TRAINER_SAWYER_1)
    assert [m["species"] for m in a.trainer_party(1)] == ["Geodude"] and a.trainer_brief(1)["area"] == "jagged_pass"
    assert a.trainer_info(1) == ("Sawyer", "Hiker")
    assert a.trainer_brief(1)["calc_label"] == "Hiker Sawyer"  # F5: setdex base string
    # Card XC4c item 1: fight_label now reaches trainer_brief (server.py's Upcoming Key
    # Trainers grouping reads it straight from there).
    assert a.trainer_brief(520)["fight_label"] == "Rival has Treecko"
    assert "fight_label" not in a.trainer_brief(1)  # no starter/rematch suffix -> omitted
    assert a.area_display_name("mt_pyre") == "Mt Pyre"  # F6: humanize_area_id fallback, no table
    assert a.area_display_name("") == ""
    assert a.rival_trainer_ids() == set()
    assert not a.supports_info_panel() and not a.supports_explode_mode()
    assert not a.native_trade_ui()
    assert a.party_blob_size() == 100 and a.memorial_box_index == 13
    assert a.is_valid_mon_key("12345678:87654321")
    assert a.parse_ot_id("12345678:87654321") == "87654321"
    assert a.status_token(0x88) == "TOX"
    assert a.gift_link_area("route_101") == "gift_route_101"
    # EXP-ADAPTER-POLICY: fixed-species policy is census-derived (was a pinned limitation);
    # "gift_beldum" is not a real area id, the real one is mossdeep_city_stevens_house.
    assert not a.is_fixed_species_gift("gift_beldum")
    assert a.is_fixed_species_gift("mossdeep_city_stevens_house")
    assert a.area_pack == "gen3_exp/28877d73"
    assert a.pairing_kind("companion") == "companion"


@pytest.mark.parametrize("value", [0, 9999, -1])
def test_unknown_tables_have_no_vanilla_fallback(value):
    a = get_adapter("gen3_exp")
    assert a.to_national_dex(value) == 0
    assert a.species_types(value) is None
    assert a.move_data(value) is None
    assert a.sprite_src(value) == ""


def test_shiny_modifier_is_an_xor_and_key_only_gap_is_explicit():
    a = get_adapter("gen3_exp")
    assert a.is_shiny("00000007:00000000")
    assert not a.is_shiny("00000008:00000000")
    assert a.is_shiny("00000008:00000000", shiny_modifier=1)
    assert not a.is_shiny("00000007:00000000", shiny_modifier=1)
    assert not a.is_shiny("bad", shiny_modifier=1)


def test_masked_party_record_feeds_only_expansion_facts():
    import struct

    from server.adapters.gen3_codec import decode_party_mon_masked
    from tests.unit.test_gen3_expansion_masks import EXPANSION_LAYOUT

    # PID=OTID=0: identity substruct order and XOR key zero. Source constants:
    # species.h:1069 Hisuian Sneasel=999; moves.h Malignant Chain=847.
    # Place unrelated bitfields above species/move IDs to falsify vanilla decode.
    raw = bytearray(100)
    raw[0x08:0x12] = b"\xff" * 10
    raw[0x14:0x1b] = b"\xff" * 7
    raw[0x13] = 2
    struct.pack_into("<H", raw, 0x1e, 1 << 14)  # shinyModifier
    struct.pack_into("<HH", raw, 0x20, 999 | (3 << 11), 873 | (7 << 10))
    struct.pack_into("<4H", raw, 0x2c, 847 | (3 << 11), 14 | (3 << 11), 1, 0)
    struct.pack_into("<I", raw, 0x4c, 2 << 29)  # hidden ability selector
    struct.pack_into("<H", raw, 0x1c, sum(struct.unpack_from("<24H", raw, 0x20)) & 0xffff)
    mon = decode_party_mon_masked(raw, layout=EXPANSION_LAYOUT)
    a = get_adapter("gen3_exp")
    assert mon["species"] == 999 and mon["held_item"] == 873
    assert a.species_name(mon["species"]) == "Sneasel"
    assert a.item_name(mon["held_item"]) == "Glimmoranite"
    assert [a.move_name(move) for move in mon["moves"]] == [
        "Malignant Chain", "Swords Dance", "Pound", "",
    ]
    assert len(a.species_abilities(mon["species"])) == 3
    assert a.is_shiny(f"{mon['personality']:08X}:{mon['ot_id']:08X}",
                      shiny_modifier=mon["shiny_modifier"]) is False
    # No encoder receives a masked record: this adapter is read-only.


def test_calc_stats_decodes_iv_masks_and_party_tail_offsets_self_consistently():
    """XC3 (SYNTH, O-33): no ROM/save/emulator -- one hand-built 100-byte expansion
    party record with known IVs/EVs and computed stats, run through calc_stats().

    This is self-consistent, not an independent check of EXPANSION_PARTY_LAYOUT:
    the byte offsets used to pack IVs/EVs/party-tail stats here are the same ones
    calc_stats()/decode_party_mon_masked() assume, so a wrong offset on both sides
    would still agree. See test_expansion_party_layout_matches_facts_json_bitfields
    (review cx-7cb40977 MAJOR 3) for the independent derivation from facts.json."""
    import struct

    a = get_adapter("gen3_exp")
    raw = bytearray(100)
    # Growth (species=1 mon, no XOR since PID=OTID=0): only IVs/EVs/computed stats matter
    # to calc_stats, so species/item/moves are left at 0.
    ivs = {"hp": 31, "attack": 20, "defense": 15, "speed": 5, "sp_attack": 25, "sp_defense": 30}
    evs = {"hp": 252, "attack": 0, "defense": 4, "speed": 252, "sp_attack": 0, "sp_defense": 0}
    struct.pack_into("<6B", raw, 0x38, evs["hp"], evs["attack"], evs["defense"],
                      evs["speed"], evs["sp_attack"], evs["sp_defense"])
    packed_ivs = (ivs["hp"] | (ivs["attack"] << 5) | (ivs["defense"] << 10)
                  | (ivs["speed"] << 15) | (ivs["sp_attack"] << 20) | (ivs["sp_defense"] << 25))
    struct.pack_into("<I", raw, 0x48, packed_ivs)  # Misc substruct (0x44-0x4F) local offset 4
    struct.pack_into("<H", raw, 0x1c, sum(struct.unpack_from("<24H", raw, 0x20)) & 0xffff)
    # Party tail: computed stats (unaffected by the expansion mask -- outside the
    # encrypted substruct region, same offsets as vanilla).
    # _PARTY_TAIL order: hp, max_hp, attack, defense, speed, sp_attack, sp_defense.
    struct.pack_into("<7H", raw, 0x56, 200, 200, 120, 80, 150, 90, 95)
    detail = {"blob_hex": bytes(raw).hex()}
    stats = a.calc_stats(detail)
    assert stats == {
        "ivs": {"hp": 31, "atk": 20, "def": 15, "spa": 25, "spd": 30, "spe": 5},
        "evs": {"hp": 252, "atk": 0, "def": 4, "spa": 0, "spd": 0, "spe": 252},
        "stats": {"hp": 200, "atk": 120, "def": 80, "spa": 90, "spd": 95, "spe": 150},
    }


def test_calc_stats_returns_none_without_a_blob():
    a = get_adapter("gen3_exp")
    assert a.calc_stats({}) is None
    assert a.calc_stats({"blob_hex": "not hex"}) is None


def test_expansion_party_layout_matches_facts_json_bitfields():
    """review cx-7cb40977 MAJOR 3: EXPANSION_PARTY_LAYOUT (gen3_expansion.py) is a
    hand-kept copy of include/pokemon.h. Derive the same masks/shifts/offsets
    independently from this build's own compiler facts
    (data/games/gen3_exp/28877d73/facts.json's PokemonSubstruct0/1/3 bitfields)
    and assert the adapter's constant equals them, so a wrong offset there would
    actually fail a test -- unlike test_calc_stats_decodes_iv_masks_and_party_tail_
    offsets_self_consistently above, which decodes with the very assumptions it
    encoded with.
    """
    import json
    from pathlib import Path

    from server.adapters.gen3_expansion import EXPANSION_PARTY_LAYOUT

    facts_path = (Path(__file__).resolve().parents[2]
                  / "data/games/gen3_exp/28877d73/facts.json")
    structs = json.loads(facts_path.read_text(encoding="utf-8"))["structs"]

    def bitfield(struct_name, field_name):
        return structs[struct_name]["bitfields"][field_name]

    def global_bit(struct_name, field_name):
        f = bitfield(struct_name, field_name)
        return f["offset"] * 8 + f["shift"]

    # Plain (unrelocated) masks: the field lives in one substruct with no bit
    # moved elsewhere, so the mask alone is the whole story.
    assert EXPANSION_PARTY_LAYOUT["MON_SPECIES_MASK"] == int(bitfield("PokemonSubstruct0", "species")["mask"], 16)
    assert EXPANSION_PARTY_LAYOUT["MON_ITEM_MASK"] == int(bitfield("PokemonSubstruct0", "heldItem")["mask"], 16)
    assert EXPANSION_PARTY_LAYOUT["EXPERIENCE_MASK"] == int(bitfield("PokemonSubstruct0", "experience")["mask"], 16)
    for move_field in ("move1", "move2", "move3", "move4"):
        assert EXPANSION_PARTY_LAYOUT["MON_MOVE_MASK"] == int(bitfield("PokemonSubstruct1", move_field)["mask"], 16)
    for pp_field in ("pp1", "pp2", "pp3", "pp4"):
        assert EXPANSION_PARTY_LAYOUT["PP_MASK"] == int(bitfield("PokemonSubstruct1", pp_field)["mask"], 16)

    # Relocated fields: compare the struct-relative bit position (byte_offset*8 +
    # shift) -- representation-independent of which byte/word width the adapter's
    # layout happens to read -- plus the field's own bit width.
    def assert_relocated(layout_entry, struct_name, field_name):
        f = bitfield(struct_name, field_name)
        assert layout_entry["word_off"] * 8 + layout_entry["shift"] == global_bit(struct_name, field_name)
        assert layout_entry["width"] == f["bits"]

    nickname_extra = EXPANSION_PARTY_LAYOUT["NICKNAME_EXTRA"]["chars"]
    assert_relocated(nickname_extra[0], "PokemonSubstruct0", "nickname11")
    assert_relocated(nickname_extra[1], "PokemonSubstruct0", "nickname12")
    assert_relocated(EXPANSION_PARTY_LAYOUT["POKEBALL_FIELD"], "PokemonSubstruct0", "pokeball")
    assert_relocated(EXPANSION_PARTY_LAYOUT["ABILITY_NUM_FIELD"], "PokemonSubstruct3", "abilityNum")

    # PokemonSubstruct2 (EVs) is opaque in facts.json -- no bitfields, no fields --
    # so EVs at bytes 0-5 stay an unfalsified vanilla carry-over, not checked here.
    assert structs["PokemonSubstruct2"]["bitfields"] == {}
    assert structs["PokemonSubstruct2"]["fields"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["emerald", "firered", "leafgreen", "firered_rr"])
@pytest.mark.parametrize("exp_first", [True, False])
async def test_expansion_refuses_mixed_foundations_without_mutating_run(tmp_path, other, exp_first):
    from server.server import SLinkServer
    from tests.unit.companion_evidence import (
        companion,  # a patched cartridge for each companion title
    )
    from tests.unit.test_mixed_foundations import _hello, _refused, _session, _snapshot

    srv = SLinkServer(data_dir=str(tmp_path))
    send, close = await _session(srv)
    first, second = (ROM_TYPE, other) if exp_first else (other, ROM_TYPE)
    try:
        assert not _refused(await send(_hello("a", {"rom_type": first, "artifact_kind": "clean", **companion(first)})))
        before = _snapshot(srv)
        assert _refused(await send(_hello("b", {"rom_type": second, "artifact_kind": "clean", **companion(second)})))
        assert _snapshot(srv) == before
    finally:
        await close()


@pytest.mark.asyncio
async def test_reference_pairs_with_itself_and_unknown_build_stays_unrouted(tmp_path):
    from server.server import SLinkServer
    from tests.unit.test_mixed_foundations import _hello, _refused, _session

    assert game_id_for_rom_type("emerald_expansion_deadbeef") is None
    assert foundation_for_rom_type("emerald_expansion_deadbeef") is None
    srv = SLinkServer(data_dir=str(tmp_path))
    send, close = await _session(srv)
    try:
        for player in ("a", "b"):
            reply = await send(_hello(player, {"rom_type": ROM_TYPE, "artifact_kind": "clean"}))
            assert not _refused(reply) and not srv.state.identity_error.get(player)
        assert srv.adapter.game_id == "gen3_exp"
    finally:
        await close()


def test_expansion_data_check_compares_without_rewriting(tmp_path, monkeypatch):
    import json
    import sys

    from tests.unit.test_extract_expansion_data import synthetic_expansion
    from tools import extract_expansion_data as ex

    rom, layout, _ = synthetic_expansion()
    rom_path, layout_path, output = tmp_path / "input.gba", tmp_path / "layout.json", tmp_path / "data.json"
    rom_path.write_bytes(rom)
    layout_path.write_text(json.dumps(layout))
    (tmp_path / "charmap.txt").write_text("'A' = BB\n'B' = BC\n")
    argv = ["extract", "--rom", str(rom_path), "--source", str(tmp_path),
            "--layout", str(layout_path), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    ex.main()
    original = output.read_bytes()
    monkeypatch.setattr(sys, "argv", [*argv, "--check"])
    ex.main()
    assert output.read_bytes() == original
    output.write_text("corrupted pack")
    with pytest.raises(ex.ExtractionError, match="differs"):
        ex.main()
    assert output.read_text() == "corrupted pack"
    output.unlink()
    with pytest.raises(ex.ExtractionError, match="missing"):
        ex.main()
    assert not output.exists()


# ── F1/F2 (review): OT keys carry the full 32-bit OTID (TID low16, SID high16) ──────────────

def test_ot_key_parser_is_the_shared_one_gen3_frlge_uses():
    # F1: no local reimplementation that could silently drop the SID half.
    from server.adapters.gen3_expansion import _parse_pid_otid_key as exp_parse
    from server.adapters.gen3_frlge import _parse_pid_otid_key as frlg_parse

    assert exp_parse is frlg_parse
    assert exp_parse("00000000:0008000C") == (0, 0x0008000C)


def test_shiny_with_a_high_half_ot_matches_the_hand_computed_formula():
    # F2: OT 0x0008000C -> TID=0x000C, SID=0x0008. personality=0 -> pid_hi=pid_lo=0.
    # (tid ^ sid ^ pid_hi ^ pid_lo) = 0x000C ^ 0x0008 = 0x0004 < 8 -> shiny.
    # A parser that dropped SID (treated ot_id as a bare 16-bit trainer id, sid=0)
    # would compute 0x000C = 12, not < 8, and get this wrong.
    tid, sid, pid_hi, pid_lo = 0x000C, 0x0008, 0, 0
    expected = (tid ^ sid ^ pid_hi ^ pid_lo) < 8
    assert expected is True
    a = get_adapter("gen3_exp")
    assert a.is_shiny("00000000:0008000C") is expected


# ── F3: move_data must not KeyError on a missing/unknown move category ──────────────────────

def test_move_data_defaults_split_to_status_when_category_is_missing_or_unknown():
    a = get_adapter("gen3_exp")
    a._moves[90001] = {"name": "No Category Move", "type": 0, "power": 40,
                        "accuracy": 100, "pp": 15}  # no "category" key at all
    a._moves[90002] = {"name": "Weird Category Move", "type": 0, "power": 40,
                        "accuracy": 100, "pp": 15, "category": 99}  # not in {1, 2, 3}
    assert a.move_data(90001)["split"] == 2
    assert a.move_data(90002)["split"] == 2


# ── F6: sentinel/placeholder pack names never surface as display names ──────────────────────

def test_name_helper_falls_back_on_every_sentinel_spelling_in_the_pack():
    # Literal id-0 sentinels shipped in data/games/gen3_exp/28877d73/data.json.
    from server.adapters.gen3_expansion import _name

    assert _name({"name": "??????????"}, "Species #0") == "Species #0"
    assert _name({"name": "-"}, "Move #0") == "Move #0"
    assert _name({"name": "????????"}, "Item #0") == "Item #0"
    assert _name({"name": "-------"}, "Ability #0") == "Ability #0"
    assert _name({}, "fallback") == "fallback"
    assert _name({"name": "Bulbasaur"}, "Species #1") == "Bulbasaur"


def test_sentinel_names_never_surface_through_the_adapter():
    a = get_adapter("gen3_exp")
    # species id 0 is the pack's "??????????" sentinel row.
    assert a.species_name(0) == "Species #0"
    # abilities 314 and 317 are "-------" sentinels at non-zero ids in the shipped pack.
    assert a.ability_name(314) == "Ability #314"
    assert a.ability_name(317) == "Ability #317"


# ── F4: the debug area catalog has no cross-game fallback ───────────────────────────────────

@pytest.mark.asyncio
async def test_expansion_debug_area_catalog_never_borrows_frlges(tmp_path):
    import json
    from pathlib import Path

    from aiohttp.test_utils import TestClient, TestServer

    from server.server import SLinkServer, build_app

    srv = SLinkServer(data_dir=str(tmp_path))
    srv.adapter = get_adapter("gen3_exp")
    client = TestClient(TestServer(build_app(srv)))
    await client.start_server()
    try:
        body = await (await client.get("/api/debug/manual_link_data")).json()
    finally:
        await client.close()
    # gen3_exp ships its own area_map.json (data/games/gen3_exp/28877d73/area_map.json):
    # the catalog is that pack's own Hoenn-first id set, plus the always-appended "gift".
    own_map = json.loads(
        (Path(__file__).resolve().parents[2] / "data/games/gen3_exp/28877d73/area_map.json")
        .read_text(encoding="utf-8")
    )
    expected = sorted({v for v in own_map.values() if isinstance(v, str)} | {"gift"})
    assert body["area_ids"] == expected
    # Never a FR/LG-only (Kanto/Sevii) id silently borrowed from gen3_frlge's catalog.
    for kanto_only in ("cinnabar_lab", "saffron_dojo", "digletts_cave"):
        assert kanto_only not in body["area_ids"]


@pytest.mark.asyncio
async def test_expansion_debug_area_catalog_is_empty_without_a_shipped_map(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from server.adapters.gen3_expansion import Gen3ExpansionAdapter
    from server.server import SLinkServer, build_app
    from server.state import AreaStatus

    # data/games/gen3_exp/ (the parent of the 28877d73 build dir) exists but ships no
    # area_map*.json of its own: a real "no map" directory, not a nonexistent one (which
    # would trip _load_known_area_ids' gen3_frlge fallback instead of testing the [] path).
    monkeypatch.setattr(Gen3ExpansionAdapter, "area_pack", property(lambda self: "gen3_exp"))
    srv = SLinkServer(data_dir=str(tmp_path))
    srv.adapter = get_adapter("gen3_exp")
    srv.state.area_states["some_visited_area"] = AreaStatus.LINKED
    client = TestClient(TestServer(build_app(srv)))
    await client.start_server()
    try:
        body = await (await client.get("/api/debug/manual_link_data")).json()
    finally:
        await client.close()
    # No shipped catalog: only "gift" (always appended) plus areas the run itself entered.
    assert body["area_ids"] == ["gift", "some_visited_area"]
    assert "pallet_town" not in body["area_ids"]


@pytest.mark.asyncio
async def test_a_vanilla_game_still_gets_its_own_area_catalog(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from server.server import SLinkServer, build_app

    srv = SLinkServer(data_dir=str(tmp_path))
    srv.adapter = get_adapter("gen3_frlge", is_rr=False, rom_type="firered")
    client = TestClient(TestServer(build_app(srv)))
    await client.start_server()
    try:
        body = await (await client.get("/api/debug/manual_link_data")).json()
    finally:
        await client.close()
    assert "pallet_town" in body["area_ids"]
    assert "route_101" not in body["area_ids"]  # that's Emerald's own map, not FR/LG's


# ── card XC4c item 5: copy-isolation, one level deeper than gen3_frlge's own check ──────────

def test_trainer_party_and_brief_are_deep_copies_not_shared_with_the_cached_pack():
    """Mirrors tests/unit/test_gen3_frlg_trainers.py's test_adapter_vanilla_titles copy-
    isolation check ("callers get copies"), extended one level deeper: this build's mons
    always carry a nested ivs dict (never optional, unlike vanilla pret's), so a caller
    mutating trainer_party(1)[0]["ivs"]["hp"] must not corrupt the functools.cache-shared
    pack that every other trainer_party()/trainer_brief() call reads from afterward. A plain
    `dict(m)` shallow copy (the bug: item 5) would share that nested dict by reference."""
    a = get_adapter("gen3_exp")
    party = a.trainer_party(1)
    party[0]["species"] = "X"
    party[0]["ivs"]["hp"] = 999
    party[0]["moves"].append("Fake Move")
    fresh = a.trainer_party(1)
    assert fresh[0]["species"] == "Geodude"
    assert fresh[0]["ivs"]["hp"] == 0
    assert "Fake Move" not in fresh[0]["moves"]

    brief = a.trainer_brief(1)
    brief["party"][0]["ivs"]["hp"] = 999
    assert a.trainer_brief(1)["party"][0]["ivs"]["hp"] == 0


def test_every_base_contract_method_has_a_decided_expansion_answer():
    """X2 (docs/gen3_emerald/PLAN.md X2 row): the full GameRulesAdapter/GamePresentationAdapter
    surface (server/adapters/base.py) on the reference build. Every public method is listed
    here, so a new base method fails this test until someone decides what expansion answers."""
    import inspect

    from server.adapters import base

    a = get_adapter("gen3_exp", rom_type=ROM_TYPE)
    decided = {
        # rules
        "game_id", "is_gift_area", "is_fixed_species_gift", "is_daycare_area", "gift_link_area",
        "is_egg_pickup_area", "evo_family", "gender_from_key", "species_types", "is_shiny",
        "parse_ot_id", "is_valid_mon_key", "species_name", "type_name", "rival_trainer_ids",
        "party_blob_size", "supports_abilities", "status_token", "info_panel_width",
        "reports_box_census", "supports_info_panel", "supports_explode_mode", "set_artifact_kind",
        "pairing_kind", "companion_refusal", "supports_randomized", "pairing_kind_for", "native_trade_ui",
        "supports_trade_recovery", "trade_unavailable_reason", "refused_trade_recovery",
        "rom_contract_by_sha1",
        # presentation
        "sprite_html", "ability_name", "ability_description", "trainer_info", "item_name",
        "area_display_name", "to_national_dex", "gender_symbol", "form_sprite_id",
        "form_sprite_url", "rom_content_fingerprint", "ingest_rom_content", "refused_rom_content",
        "encounter_table", "trainers_for_area", "trainer_party", "trainer_brief", "calc_name",
        "calc_species", "calc_profile", "calc_nature", "calc_stats", "sprite_src", "move_name",
        "move_data", "stat_stage_labels", "mons_per_box", "memorial_box_index", "gym_badge_slugs",
    }
    surface = {name for cls in (base.GameRulesAdapter, base.GamePresentationAdapter, base.GameAdapter)
               for name, _ in inspect.getmembers(cls) if not name.startswith("_")}
    assert surface == decided

    # the answers not already pinned by the tests above
    assert not a.is_egg_pickup_area("gift_lavaridge_town")   # no extracted static/egg policy
    assert a.supports_abilities() and a.info_panel_width() == 0
    assert not a.reports_box_census()
    assert a.rom_contract_by_sha1 is False   # expansion keeps client-fingerprint admission
    assert not a.supports_randomized(ROM_TYPE)
    assert a.pairing_kind_for("clean", None) == "clean"
    # patch-first (2026-10-02): the expansion's companion does not exist yet, so it is exempt and stays clean
    assert a.companion_refusal({"rom_type": ROM_TYPE, "artifact_kind": "clean"}) is None
    assert not a.supports_trade_recovery() and a.refused_trade_recovery()
    assert a.calc_species(258) == "Mudkip"
    assert a.form_sprite_url(258) is None
    assert a.gender_symbol("female") == "\u2640"
    assert a.stat_stage_labels()[0] == "ATK" and a.mons_per_box == 30
    # Randomized content is not bound for expansion: the server must never run the vanilla
    # Gen 3 table decoder over an expansion cartridge report (it would raise, and
    # _ingest_rom_content would then mark the player's encounter data unavailable).
    report = {"tables": []}
    assert a.rom_content_fingerprint(report) is None
    assert a.ingest_rom_content(report) is None
    assert a.refused_rom_content(None, artifact_kind="rand")
    assert a.refused_rom_content(None, artifact_kind="clean") == ""
