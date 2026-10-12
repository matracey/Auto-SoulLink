"""Tests for the Gen 4 HGSS adapter — every table it serves now comes from data/games/gen4_hgss.

Platinum is gone: it is not routed (server/adapters/__init__.py), the hello guard refuses it by
name, and data/games/gen4_hgsspt/ is deleted. What is left of the old per-title split is one
pack, and the tests below pin the adapter against THAT file rather than against a hand-curated
table it can drift from.
"""

import ast
import inspect
import json
import logging
import os

import pytest

from server.adapters import gen4_hgsspt as gen4
from server.adapters.gen4_hgsspt import Gen4Adapter


@pytest.fixture
def adapter():
    return Gen4Adapter()


# ── game_id ──────────────────────────────────────────────────────────────

def test_game_id(adapter):
    assert adapter.game_id == "gen4_hgsspt"


# ── Gift areas ───────────────────────────────────────────────────────────
# The HGSS gift set is the generated pack's `gift_areas.ids`
# (data/games/gen4_hgss/area_map.json), pinned area-by-area against that pack in
# tests/unit/test_gen4_gift_areas_server.py. What is left here is the two shapes the adapter adds
# on top of any set — the unmapped-area fallback, the `gift_` prefix, and Pal Park.

def test_gift_fallback_area(adapter):
    assert adapter.is_gift_area("gift") is True


def test_gift_prefix_area(adapter):
    assert adapter.is_gift_area("gift_something") is True


@pytest.mark.parametrize("area_id", [
    "route_1",
    "route_29",
    "national_park",
    "dark_cave",
    "bell_tower",
])
def test_non_gift_areas_return_false(adapter, area_id):
    assert adapter.is_gift_area(area_id) is False


# ── Pal Park, and the Sinnoh ids that are gone ────────────────────────────────────────────────

def test_pal_park_is_a_gift_area(adapter):
    """Pal Park is not a Platinum id that survived by accident. It is a real HGSS/SS area —
    the generated map owns `pal_park` (MAPSEC_PAL_PARK, Kanto) — and a migrated mon arrives
    there with no Pokéballs and no wild encounter to fail, so it stays exempt."""
    assert adapter.is_gift_area("pal_park") is True


@pytest.mark.parametrize("area_id", [
    "twinleaf_town",   # Starter (Turtwig/Chimchar/Piplup from Prof. Rowan)
    "sandgem_town",    # Dawn/Lucas Egg + other town events
    "eterna_city",     # Togepi egg (Underground Man) / Cleffa
    "hearthome_city",  # Eevee from Bebe
    "iron_island",     # Riolu egg from Riley
    "veilstone_city",  # Porygon (condominiums)
    "route_212",       # Togepi egg from Cynthia
])
def test_the_sinnoh_ids_are_not_gift_areas_any_more(adapter, area_id):
    """Platinum is no longer routed, so no cartridge the server serves can emit these ids, and
    none of them is an HGSS area (the generated map owns none of them). Exempting an area drops
    that area's no_catch and its wild-slot quarantine, so an id nothing can reach is only a way
    to exempt the wrong thing by accident later."""
    assert adapter.is_gift_area(area_id) is False


def test_a_sinnoh_route_is_not_a_gift_area(adapter):
    assert adapter.is_gift_area("route_201") is False


# ── PID:OTID key parsing ────────────────────────────────────────────────

def test_parse_ot_id_normal(adapter):
    assert adapter.parse_ot_id("AABBCCDD:11223344") == "11223344"


def test_parse_ot_id_lowercase(adapter):
    assert adapter.parse_ot_id("aabbccdd:11223344") == "11223344"


def test_parse_ot_id_invalid_no_colon(adapter):
    assert adapter.parse_ot_id("AABBCCDD") == ""


def test_parse_ot_id_empty(adapter):
    assert adapter.parse_ot_id("") == ""


# ── is_shiny ─────────────────────────────────────────────────────────────

def test_is_shiny_known_shiny(adapter):
    # Craft a shiny: tid ^ sid ^ p_hi ^ p_lo == 0
    # PID = 0x00000000, OTID = 0x00000000 → xor = 0 < 8 → shiny
    assert adapter.is_shiny("00000000:00000000") is True


def test_is_shiny_known_non_shiny(adapter):
    # PID = 0xFFFF0000, OTID = 0x00000000 → 0 ^ 0 ^ FFFF ^ 0 = 0xFFFF → not shiny
    assert adapter.is_shiny("FFFF0000:00000000") is False


def test_is_shiny_crafted_shiny(adapter):
    # PID = 0x12345678, OTID = TID=0x1234, SID=0x5678
    # p_hi=0x1234, p_lo=0x5678, tid=0x5678, sid=0x1234 (otid stored as SID<<16|TID)
    # But otid as u32 = 0x12345678 → tid=0x5678, sid=0x1234
    # xor = 0x5678 ^ 0x1234 ^ 0x1234 ^ 0x5678 = 0 < 8 → shiny
    assert adapter.is_shiny("12345678:12345678") is True


def test_is_shiny_invalid_key(adapter):
    assert adapter.is_shiny("not_a_key") is False


# ── species_name ─────────────────────────────────────────────────────────

def test_species_name_turtwig(adapter):
    assert adapter.species_name(387) == "Turtwig"


def test_species_name_arceus(adapter):
    assert adapter.species_name(493) == "Arceus"


def test_species_name_pikachu(adapter):
    assert adapter.species_name(25) == "Pikachu"


def test_species_name_unknown(adapter):
    assert adapter.species_name(99999) == "#99999"


# ── evo_family ───────────────────────────────────────────────────────────

def test_evo_family_turtwig_line(adapter):
    base = adapter.evo_family(387)
    assert adapter.evo_family(388) == base  # Grotle
    assert adapter.evo_family(389) == base  # Torterra


# ── gender_from_key ──────────────────────────────────────────────────────

def test_gender_male(adapter):
    # Nidoran♂ (32): gender ratio 0 → 100% male
    assert adapter.gender_from_key("00000001:12345678", 32) == "male"


def test_gender_female(adapter):
    # Chansey (113): gender ratio 254 → 100% female
    assert adapter.gender_from_key("00000001:12345678", 113) == "female"


def test_gender_genderless(adapter):
    # Magnemite (81): gender ratio 255 → genderless
    assert adapter.gender_from_key("00000001:12345678", 81) == "genderless"


def test_gender_pid_dependent(adapter):
    # Bulbasaur (1): gender ratio 31 → male if PID_low >= 31
    assert adapter.gender_from_key("000000FF:12345678", 1) == "male"
    assert adapter.gender_from_key("00000000:12345678", 1) == "female"


# ── species_types ────────────────────────────────────────────────────────

def test_species_types_fire(adapter):
    # Charmander (4): Fire
    types = adapter.species_types(4)
    assert types is not None
    assert len(types) >= 1


def test_species_types_dual(adapter):
    # Charizard (6): Fire/Flying
    types = adapter.species_types(6)
    assert types is not None
    assert len(types) == 2


# ── area_display_name ────────────────────────────────────────────────────

def test_area_display_name_fallback_keeps_the_raw_id(adapter):
    # Renamed: a second def with the same name lower in this file shadowed it, so this
    # assertion had never actually run.
    name = adapter.area_display_name("unknown_area_xyz")
    assert "unknown" in name.lower() or "xyz" in name.lower()


def test_area_display_name_known(adapter):
    # Resolves from the pack's areas.<id>.display — data/games/gen4_hgss/area_map.json.
    name = adapter.area_display_name("route_1")
    assert name == "Route 1"


def test_evo_family_single_stage(adapter):
    # Pachirisu (NatDex 417) is single-stage
    assert adapter.evo_family(417) == 417


def test_evo_family_pikachu_line(adapter):
    # Pichu (172) → Pikachu (25) → Raichu (26)
    base = adapter.evo_family(172)
    assert adapter.evo_family(25) == base
    assert adapter.evo_family(26) == base


# ── is_valid_mon_key ─────────────────────────────────────────────────────

def test_valid_key(adapter):
    assert adapter.is_valid_mon_key("AABBCCDD:11223344") is True


def test_valid_key_short(adapter):
    assert adapter.is_valid_mon_key("1:2") is True


def test_invalid_key_no_colon(adapter):
    assert adapter.is_valid_mon_key("AABBCCDD11223344") is False


def test_invalid_key_non_hex(adapter):
    assert adapter.is_valid_mon_key("GGGGGGGG:11223344") is False


def test_invalid_key_empty(adapter):
    assert adapter.is_valid_mon_key("") is False


def test_invalid_key_too_many_parts(adapter):
    assert adapter.is_valid_mon_key("AA:BB:CC") is False


# ── to_national_dex (identity) ───────────────────────────────────────────

def test_to_national_dex_identity(adapter):
    assert adapter.to_national_dex(387) == 387
    assert adapter.to_national_dex(493) == 493
    assert adapter.to_national_dex(1) == 1


# ── Presentation ─────────────────────────────────────────────────────────

def test_sprite_html_contains_species_id(adapter):
    html = adapter.sprite_html(25)
    assert "25.png" in html
    assert "<img" in html


def test_sprite_html_zero(adapter):
    assert adapter.sprite_html(0) == ""


def test_item_name(adapter):
    assert adapter.item_name(55) == "Guard Spec."
    assert adapter.item_name(0) == ""


def test_area_display_name_fallback(adapter):
    # dark_cave has a display name in the area map; test true fallback with unknown area
    assert adapter.area_display_name("unknown_forest") == "Unknown Forest"


def test_form_sprite_id_always_none(adapter):
    assert adapter.form_sprite_id(25) is None
    assert adapter.form_sprite_id(493) is None


# ── Registry ─────────────────────────────────────────────────────────────

def test_adapter_registered():
    from server.adapters import get_adapter
    a = get_adapter("gen4_hgsspt")
    assert a.game_id == "gen4_hgsspt"


# ── ability_name species_id parameter is ignored ─────────────────────────────

def test_ability_name_species_id_ignored(adapter):
    """Gen 4 adapter ignores species_id — result must be the same with or without it."""
    base = adapter.ability_name(1)
    with_species = adapter.ability_name(1, species_id=999)
    assert with_species == base

def test_ability_name_species_id_does_not_inject_cfru_override(adapter):
    """Gen 4 adapter must not return an RR-specific override name even when an override species_id is passed."""
    # (121, 50) is "Tangling Hair" in RR — Gen 4 must return the plain vanilla name instead
    name = adapter.ability_name(121, species_id=50)
    assert name != "Tangling Hair"


# ── Phase 2-7 additions ──────────────────────────────────────────────────────

class TestFormSprites:
    """Form-aware sprite resolution (Phase 5)."""

    def test_rotom_heat_form(self, adapter):
        assert adapter.form_sprite_url(479, 1) == "479-heat"
        assert "479-heat" in adapter.sprite_html(479, 1)

    def test_giratina_origin_form(self, adapter):
        assert adapter.form_sprite_url(487, 1) == "487-origin"

    def test_shaymin_sky_form(self, adapter):
        assert adapter.form_sprite_url(492, 1) == "492-sky"

    def test_deoxys_attack_form(self, adapter):
        assert adapter.form_sprite_url(386, 1) == "386-attack"

    def test_unown_letter_b(self, adapter):
        assert adapter.form_sprite_url(201, 1) == "201-b"

    def test_arceus_fire_plate(self, adapter):
        assert adapter.form_sprite_url(493, 9) == "493-fire"

    def test_base_form_returns_none(self, adapter):
        # form=0 is the base form for every species — no override needed
        assert adapter.form_sprite_url(479, 0) is None
        assert adapter.form_sprite_url(487, 0) is None
        assert adapter.form_sprite_url(25, 0) is None  # Pikachu has no Gen 4 forms

    def test_sprite_html_uses_base_for_unknown_form(self, adapter):
        # An unmapped (species, form) pair falls back to the base species sprite.
        html = adapter.sprite_html(25, 99)
        assert "/25.png" in html


class TestMoveData:
    """Gen 4 move table (Phase 7)."""

    def test_gen4_only_moves(self, adapter):
        assert adapter.move_name(369) == "U-turn"
        assert adapter.move_name(444) == "Stone Edge"
        assert adapter.move_name(467) == "Shadow Force"

    def test_inherits_gen3_moves(self, adapter):
        # Move IDs 1-354 should fall through to VANILLA_MOVE_NAMES.
        assert adapter.move_name(33) == "Tackle"
        assert adapter.move_name(1) == "Pound"

    def test_unknown_move_returns_empty(self, adapter):
        # ID 0 is "no move" sentinel — the vanilla table has a placeholder entry there.
        # IDs beyond the Gen 4 max (467) should return empty.
        assert adapter.move_name(9999) == ""
        assert adapter.move_name(468) == ""  # one past Shadow Force

    def test_move_data_shape(self, adapter):
        md = adapter.move_data(369)  # U-turn
        assert md is not None
        assert md["name"] == "U-turn"
        assert md["type_id"] == 6     # Bug
        assert md["type_name"] == "Bug"
        assert md["power"] == 70
        assert md["pp"] == 20
        assert md["split"] == 0       # Physical

    def test_move_data_status_split(self, adapter):
        md = adapter.move_data(355)  # Roost
        assert md["split"] == 2
        assert md["type_name"] == "Flying"


class TestEggPickupArea:
    """Egg-pickup detection (Phase 4)."""

    def test_egg_prefix_recognized(self, adapter):
        assert adapter.is_egg_pickup_area("egg_violet_city") is True
        assert adapter.is_egg_pickup_area("egg_ilex_forest") is True

    def test_violet_city_recognized(self, adapter):
        # The pack's acquisition inventory puts every `kind: "egg"` site in violet_city: Mr.
        # Pokémon's Togepi on MAP_VIOLET_POKEMART and Mareep/Wooper/Slugma in the Poké Center.
        assert adapter.is_egg_pickup_area("violet_city") is True
        # Mr. Pokémon's house is NOT on Route 30 — no acquisition site resolves into that area.
        assert adapter.is_egg_pickup_area("route_30") is False
        # Kiyo's Tyrogue is `kind: "gift"` (a mon handed over), not an egg.
        assert adapter.is_egg_pickup_area("mt_mortar") is False

    def test_non_egg_area_returns_false(self, adapter):
        assert adapter.is_egg_pickup_area("new_bark_town") is False
        assert adapter.is_egg_pickup_area("route_29") is False

    def test_egg_area_also_gift_area(self, adapter):
        # Egg pickups are treated as gifts for clause-bypass purposes.
        assert adapter.is_gift_area("egg_violet_city") is True

    def test_egg_fixed_species_strips_prefix(self, adapter):
        # "egg_dragons_den" should map to dragons_den (Dratini from the Elder, fixed species).
        assert adapter.is_fixed_species_gift("egg_dragons_den") is True
        # route_30 is not a fixed-species gift either: it has no acquisition at all, so listing it
        # only disabled the species clause for Route 30's grass.
        assert adapter.is_fixed_species_gift("route_30") is False


class TestDaycareArea:
    """Daycare detection (Phase 4 addendum) — distinguishes NPC eggs from bred eggs."""

    def test_hgss_daycare_route_34(self, adapter):
        assert adapter.is_daycare_area("route_34") is True


    def test_non_daycare_route_returns_false(self, adapter):
        assert adapter.is_daycare_area("route_30") is False
        assert adapter.is_daycare_area("cianwood_city") is False

    def test_daycare_egg_prefix_recognized(self, adapter):
        # "egg_route_34" → strip prefix → route_34 → daycare.
        assert adapter.is_daycare_area("egg_route_34") is True

    def test_daycare_overrides_egg_pickup(self, adapter):
        # Daycare-bred eggs aren't NPC pickups — clause logic gets different treatment.
        assert adapter.is_egg_pickup_area("route_34") is False
        assert adapter.is_egg_pickup_area("egg_route_34") is False
        # But violet_city (Mr. Pokémon, the Poké Center eggs) IS an NPC egg pickup, not daycare.
        assert adapter.is_egg_pickup_area("violet_city") is True
        assert adapter.is_daycare_area("violet_city") is False


class TestRomTypeVariants:
    """ROM-type-aware adapter behavior (Phase 6 + 8)."""

    def test_hgss_default(self):
        a = Gen4Adapter()  # default rom_type=heartgold
        assert a._rom_type == "heartgold"


    def test_hgss_uses_hgss_trainers(self):
        from server.adapters.gen4_hgsspt import _HGSS_TRAINERS
        a = Gen4Adapter(rom_type="heartgold")
        assert a._trainers is _HGSS_TRAINERS

    def test_trainer_info_known_ids(self):
        # Curated seed data: HGSS gym leaders.
        hgss = Gen4Adapter(rom_type="heartgold")
        name, cls = hgss.trainer_info(20)  # Falkner
        assert name == "Falkner"
        assert cls == "Leader"      # the pack's word for TRAINERCLASS_LEADER_FALKNER
        name, cls = hgss.trainer_info(244)  # Lance
        assert name == "Lance"
        assert cls == "Champion"


    def test_trainer_info_empty_for_unknown_id(self, adapter):
        assert adapter.trainer_info(99999) == ("", "")
        assert adapter.trainer_info(0) == ("", "")


class TestEncounterTable:
    """Wild encounter tables (Phase 10 — closing a Gen3 parity gap)."""

    def test_hgss_route_29_day_is_the_pack_land_table(self):
        """Route 29 has a Day grass table, and it IS the pack's — slot rates summed per species.

        The old hand-curated table printed Pidgey at 40%; the pack's twelve R29 day slots carry
        20/10/10/10/4/1 for it. What matters is that the number comes from the file, so this
        compares against the file rather than re-pinning a rate that a pin bump can move.
        """
        hgss = Gen4Adapter(rom_type="heartgold")
        enc = hgss.encounter_table("route_29")
        assert enc is not None
        assert "Day" in enc
        expected = _aggregate(_pack_land_slots("route_29"))
        assert expected, "the pack must back route_29 with a day land table"
        assert enc["Day"] == sorted(expected, key=lambda r: (-r["rate"], r["name"]))
        pidgey = next(r for r in enc["Day"] if r["name"] == "Pidgey")
        assert pidgey["species_id"] == 16
        assert pidgey["rate"] == sum(s["rate"] for s in _pack_land_slots("route_29")
                                     if s["species_id"] == 16)

    def test_hgss_land_is_split_by_hour_and_rods_are_labelled(self):
        enc = Gen4Adapter(rom_type="heartgold").encounter_table("route_29")
        assert {"Morn", "Day", "Night"} <= set(enc)
        rods = Gen4Adapter(rom_type="heartgold").encounter_table("new_bark_town")
        assert "Old Rod" in rods and "Surfing" in rods


    def test_unknown_area_returns_none(self, adapter):
        assert adapter.encounter_table("nonexistent_area") is None

    def test_hgss_uses_hgss_encounters(self):
        # HGSS should have Route 29 (Johto), not Route 201 (Sinnoh).
        hgss = Gen4Adapter(rom_type="heartgold")
        assert hgss.encounter_table("route_29") is not None
        assert hgss.encounter_table("route_201") is None


    def test_soulsilver_reads_the_soulsilver_banks(self):
        """Only the wild tables split by title (the pack's trainers do not)."""
        from server.adapters.gen4_hgsspt import _HGSS_ENCOUNTERS
        assert Gen4Adapter(rom_type="soulsilver")._encounters is _HGSS_ENCOUNTERS["soulsilver"]
        assert Gen4Adapter(rom_type="heartgold")._encounters is _HGSS_ENCOUNTERS["heartgold"]
        assert Gen4Adapter(rom_type="heartgold_hge")._encounters is _HGSS_ENCOUNTERS["heartgold"]


# ── the tables are the generated pack, and a missing pack is loud ─────────────────────────────

def _pack(name: str) -> dict:
    with open(os.path.join(gen4._HGSS_PACK_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _pack_land_slots(area_id: str, title: str = "heartgold", hour: str = "day") -> list[dict]:
    """Every land slot the pack puts in `area_id`, read straight out of encounters.json."""
    doc = _pack("encounters.json")
    out: list[dict] = []
    for token, bank in doc["banks"].items():
        if area_id not in (bank.get("areas") or ()):
            continue
        land = doc["versions"][title]["banks"][token].get("land")
        if isinstance(land, dict):
            out.extend(land.get(hour, ()))
    return out


def _aggregate(slots: list[dict]) -> list[dict]:
    """The per-species fold the adapter applies, written out again here on purpose."""
    rows: dict[int, dict] = {}
    for s in slots:
        row = rows.setdefault(s["species_id"], {
            "name": s["name"], "species_id": s["species_id"], "rate": 0,
            "min_level": s["min_level"], "max_level": s["max_level"]})
        row["rate"] += s["rate"]
        row["min_level"] = min(row["min_level"], s["min_level"])
        row["max_level"] = max(row["max_level"], s["max_level"])
    return list(rows.values())


def test_display_names_are_the_packs_own():
    areas = _pack("area_map.json")["areas"]
    assert gen4._AREA_DISPLAY_NAMES
    assert {
        aid: row["display"] for aid, row in areas.items() if row.get("display")} == gen4._AREA_DISPLAY_NAMES
    assert Gen4Adapter().area_display_name("route_29") == areas["route_29"]["display"]


def test_trainers_are_the_packs_own():
    rows = _pack("trainers.json")["trainers"]
    assert gen4._HGSS_TRAINERS
    assert "Falkner" == Gen4Adapter().trainer_info(20)[0] == rows["20"]["name"]
    assert "Leader" == Gen4Adapter().trainer_info(20)[1] == rows["20"]["class"]
    assert "Lance" == Gen4Adapter().trainer_info(244)[0] == rows["244"]["name"]
    assert "Champion" == Gen4Adapter().trainer_info(244)[1] == rows["244"]["class"]
    # TRAINER_NONE (id 0) is the pack's "no trainer" marker and must not answer by name.
    assert Gen4Adapter().trainer_info(0) == ("", "")


def test_encounters_cover_every_area_the_pack_backs_a_bank_for():
    doc = _pack("encounters.json")
    backed = {a for bank in doc["banks"].values() for a in bank.get("areas", ())}
    tables = gen4._HGSS_ENCOUNTERS
    assert backed
    for title in ("heartgold", "soulsilver"):
        assert backed <= set(tables[title]), sorted(backed - set(tables[title]))
    # Every entry the adapter serves is one the pack wrote: name/species/level range and a rate.
    for area, methods in tables["heartgold"].items():
        assert methods, area
        for label, rows in methods.items():
            assert rows, (area, label)
            for r in rows:
                assert set(r) == {"name", "species_id", "rate", "min_level", "max_level"}
                assert r["species_id"] > 0 and r["min_level"] <= r["max_level"]


def test_a_missing_pack_empties_every_table_and_names_the_path(monkeypatch, tmp_path, caplog):
    """The red control: an unreadable pack must not leave a stale table standing in silence."""
    empty = tmp_path / "no_such_pack"
    empty.mkdir()
    monkeypatch.setattr(gen4, "_HGSS_PACK_DIR", str(empty))
    with caplog.at_level(logging.ERROR):
        assert gen4._load_hgss_area_display_names() == {}
        assert gen4._load_hgss_trainers() == {}
        assert gen4._load_hgss_encounters() == {}
    for name in ("area_map.json", "trainers.json", "encounters.json"):
        assert any(str(empty) in r.getMessage() for r in caplog.records
                   if name in r.getMessage()), caplog.records
    # And the adapter built on them answers empty rather than raising.
    monkeypatch.setattr(gen4, "_AREA_DISPLAY_NAMES", {})
    monkeypatch.setattr(gen4, "_HGSS_TRAINERS", {})
    monkeypatch.setattr(gen4, "_HGSS_ENCOUNTERS", {})
    a = Gen4Adapter()
    assert a.area_display_name("route_29") == "Route 29"   # humanize fallback
    assert a.trainer_info(20) == ("", "")
    assert a.encounter_table("route_29") is None


def test_the_module_names_no_legacy_data_path():
    """Only the game_id may still spell `gen4_hgsspt`; no data path may."""
    tree = ast.parse(inspect.getsource(gen4))
    legacy = sorted({n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and len(n.value) <= 40   # prose out: a docstring MENTIONING a file is not a path
                     and ("gen4_hgsspt" in n.value or n.value.endswith(".json"))
                     and not n.value.startswith(("tools/", "data/games/gen4_hgss/"))
                     and n.value not in {"area_map.json", "encounters.json", "trainers.json"}})  # the pack's own file names
    assert legacy == ["gen4_hgsspt"]
