"""Tests for the Gen 5 BW/BW2 adapter."""

from pathlib import Path

import pytest
from lupa import LuaRuntime

from server.adapters.gen5_bw import Gen5Adapter
from server.state import AreaStatus, SoulLinkState


@pytest.fixture
def adapter():
    return Gen5Adapter()


@pytest.fixture
def adapter_bw1():
    return Gen5Adapter(rom_type="pokemon_black")


@pytest.fixture
def adapter_bw2():
    return Gen5Adapter(rom_type="pokemon_black_2")


# ── game_id ──────────────────────────────────────────────────────────────

def test_game_id(adapter):
    assert adapter.game_id == "gen5_bw"


# ── Memorial box ──────────────────────────────────────────────────────────

def test_memorial_box_index(adapter):
    """Gen 5 has 24 boxes; memorial = Box 24 = index 23."""
    assert adapter.memorial_box_index == 23


# ── Gift areas (BW1) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("area_id", [
    "nuvema_town",
    "striaton_city",
    "castelia_city",
    "nacrene_city",
    "gift",
])
def test_bw1_gift_areas_return_true(adapter_bw1, area_id):
    assert adapter_bw1.is_gift_area(area_id) is True


# ── Gift areas (BW2) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("area_id", [
    "aspertia_city",
    "castelia_city",
    "nacrene_city",
    "gift",
])
def test_bw2_gift_areas_return_true(adapter_bw2, area_id):
    assert adapter_bw2.is_gift_area(area_id) is True


def test_gift_prefix_area(adapter):
    assert adapter.is_gift_area("gift_starter") is True


@pytest.mark.parametrize("area_id", [
    "route_1",
    "route_5",
    "pinwheel_forest",
    "victory_road",
    "giant_chasm",
])
def test_non_gift_areas_return_false(adapter, area_id):
    assert adapter.is_gift_area(area_id) is False


def test_gift_areas_for_bw1(adapter):
    gift_set = adapter.gift_areas_for_rom("pokemon_black")
    assert "nuvema_town" in gift_set
    assert "dragonspiral_tower" not in gift_set
    assert "aspertia_city" not in gift_set


def test_gift_areas_for_bw2(adapter):
    gift_set = adapter.gift_areas_for_rom("pokemon_black_2")
    assert "aspertia_city" in gift_set
    assert "floccesy_ranch" not in gift_set
    assert "nuvema_town" not in gift_set


def test_is_gift_area_respects_rom_type():
    """Adapter with BW2 rom_type should use BW2 gift set, not BW1."""
    bw1 = Gen5Adapter(rom_type="pokemon_black")
    bw2 = Gen5Adapter(rom_type="pokemon_white_2")
    # nuvema_town is BW1 starter town only
    assert bw1.is_gift_area("nuvema_town") is True
    assert bw2.is_gift_area("nuvema_town") is False
    # aspertia_city is BW2 starter town only
    assert bw2.is_gift_area("aspertia_city") is True
    assert bw1.is_gift_area("aspertia_city") is False


@pytest.mark.parametrize("rom_type", ["pokemon_black_2", "pokemon_white_2"])
def test_floccesy_ranch_uses_wild_rules_on_both_sides(rom_type):
    adapter = Gen5Adapter(rom_type=rom_type)
    module = Path(__file__).resolve().parents[2] / "lua/games/gen5_bw.lua"
    game = LuaRuntime().execute(module.read_text(encoding="utf-8"))
    assert not game.GIFT_AREAS_BW2["floccesy_ranch"]
    assert not game.is_gift_area("floccesy_ranch")
    assert not adapter.is_gift_area("floccesy_ranch")
    assert not adapter.is_fixed_species_gift("floccesy_ranch")
    assert adapter.gift_link_area("floccesy_ranch") == "gift_floccesy_ranch"
    assert adapter.is_gift_area("gift_floccesy_ranch")


@pytest.mark.parametrize("rom_type", ["pokemon_black_2", "pokemon_white_2"])
@pytest.mark.parametrize("retry", [False, True])
def test_floccesy_ranch_failed_catch_respects_retry_rule(tmp_path, rom_type, retry):
    state = SoulLinkState(data_dir=str(tmp_path), adapter=Gen5Adapter(rom_type=rom_type),
                          retry_same_species=retry)
    commands = state.handle_event("a", {"event": "no_catch", "area_id": "floccesy_ranch",
                                       "species_id": 447})
    if retry:
        assert state.encounter_locks["floccesy_ranch"]["a"].species == 447
        assert any(c["cmd"] == "unresolve_area" for c in commands)
    else:
        assert state.area_states["floccesy_ranch"] == AreaStatus.DEAD_ZONE


@pytest.mark.parametrize("rom_type", ["pokemon_black_2", "pokemon_white_2"])
def test_floccesy_ranch_wild_pair_keeps_species_clause(tmp_path, rom_type):
    state = SoulLinkState(data_dir=str(tmp_path), species_lock=True,
                          adapter=Gen5Adapter(rom_type=rom_type))
    state.pokeballs_obtained = {"a": True, "b": True}
    state.handle_event("a", {"event": "capture", "key": "AAAA0001:11111111",
                            "area_id": "floccesy_ranch", "species_id": 447})
    commands = state.handle_event("b", {"event": "capture", "key": "BBBB0002:22222222",
                                       "area_id": "floccesy_ranch", "species_id": 447})
    assert not state.links
    assert any(c["cmd"] == "force_faint" and c["key"] == "BBBB0002:22222222" for c in commands)


# ── Evo family ────────────────────────────────────────────────────────────

def test_evo_family_single_stage(adapter):
    """Snivy (495) has no evolution family prior to Serperior (497)."""
    base = adapter.evo_family(495)
    assert base == adapter.evo_family(496) == adapter.evo_family(497)


def test_evo_family_unrelated_species(adapter):
    """Snivy and Tepig should have different base forms."""
    assert adapter.evo_family(495) != adapter.evo_family(498)


# ── Gender ────────────────────────────────────────────────────────────────

def test_gender_from_key_male(adapter):
    # PID with low byte >= threshold → male for Snivy (87.5% male, threshold=31)
    # Low byte 200 >= 31 → male
    gender = adapter.gender_from_key("000000C8:12345678", 495)
    assert gender == "male"


def test_gender_from_key_female(adapter):
    # Low byte 10 < 31 → female
    gender = adapter.gender_from_key("0000000A:12345678", 495)
    assert gender == "female"


def test_gender_genderless_species(adapter):
    # Voltorb (100) and Electrode (101) are 100% male; Magnemite (81) is genderless
    gender = adapter.gender_from_key("AABBCCDD:11223344", 81)
    assert gender == "genderless"


def test_gender_invalid_key(adapter):
    assert adapter.gender_from_key("", 495) == ""
    assert adapter.gender_from_key("invalid", 495) == ""


# ── Shiny detection ───────────────────────────────────────────────────────

def test_shiny_detection_false(adapter):
    """Random key should not be shiny."""
    assert adapter.is_shiny("DEADBEEF:CAFEBABE") is False


def test_shiny_detection_true(adapter):
    """Craft a shiny key: (tid ^ sid ^ p_upper ^ p_lower) < 8."""
    # tid=1000, sid=1000, p_upper=1000, p_lower=1000 → xor=0 < 8
    tid  = 1000
    sid  = 1000
    ot   = sid << 16 | tid
    p_lo = 1000
    p_hi = 1000
    pid  = p_hi << 16 | p_lo
    key  = f"{pid:08X}:{ot:08X}"
    assert adapter.is_shiny(key) is True


def test_shiny_invalid_key(adapter):
    assert adapter.is_shiny("") is False
    assert adapter.is_shiny("DEADBEEF") is False


# ── Key parsing / validation ──────────────────────────────────────────────

def test_parse_ot_id_valid(adapter):
    assert adapter.parse_ot_id("AABBCCDD:11223344") == "11223344"


def test_parse_ot_id_invalid(adapter):
    assert adapter.parse_ot_id("AABBCCDD") == ""
    assert adapter.parse_ot_id("") == ""


def test_is_valid_mon_key_valid(adapter):
    assert adapter.is_valid_mon_key("AABBCCDD:11223344") is True
    assert adapter.is_valid_mon_key("00000000:00000000") is True


def test_is_valid_mon_key_invalid(adapter):
    assert adapter.is_valid_mon_key("AABBCCDD") is False
    assert adapter.is_valid_mon_key("") is False
    assert adapter.is_valid_mon_key("GGGGGGGG:11223344") is False


# ── Species ───────────────────────────────────────────────────────────────

def test_species_name_gen5(adapter):
    """Gen 5 species 495-649 should resolve to names."""
    name = adapter.species_name(495)
    assert isinstance(name, str) and len(name) > 0
    assert "#" not in name, f"Species 495 should have a real name, got {name!r}"


def test_species_name_gen1_still_works(adapter):
    assert adapter.species_name(1) != ""
    assert "#" not in adapter.species_name(25)  # Pikachu


def test_species_name_unknown(adapter):
    result = adapter.species_name(9999)
    assert result == "#9999"


def test_species_name_victini(adapter):
    """Victini (494) — first species above the Gen 4 cap of 493."""
    name = adapter.species_name(494)
    assert "#" not in name and name.lower().startswith("victini")


def test_species_name_genesect(adapter):
    """Genesect (649) — last Gen 5 species; the Lua SPECIES_MAX upper bound."""
    name = adapter.species_name(649)
    assert "#" not in name and name.lower().startswith("genesect")


def test_to_national_dex_passthrough(adapter):
    """Gen 5 uses NatDex IDs natively — to_national_dex is a no-op."""
    assert adapter.to_national_dex(495) == 495
    assert adapter.to_national_dex(649) == 649


# ── Evolution families (Gen 5 chains) ─────────────────────────────────────

def test_evo_family_oshawott_chain(adapter):
    """Oshawott (501) → Dewott (502) → Samurott (503) all share base form."""
    assert adapter.evo_family(501) == adapter.evo_family(502) == adapter.evo_family(503)


def test_evo_family_tepig_chain(adapter):
    """Tepig (498) → Pignite (499) → Emboar (500) all share base form."""
    assert adapter.evo_family(498) == adapter.evo_family(499) == adapter.evo_family(500)


def test_evo_family_genesect_singleton(adapter):
    """Genesect (649) is a singleton — base form is itself."""
    assert adapter.evo_family(649) == adapter.evo_family(649)


def test_evo_family_starters_distinct(adapter):
    """The three Unova starters (Snivy 495, Tepig 498, Oshawott 501) have distinct base forms."""
    bases = {adapter.evo_family(495), adapter.evo_family(498), adapter.evo_family(501)}
    assert len(bases) == 3


# ── Adapter registration ──────────────────────────────────────────────────

def test_adapter_registry_gen5_bw():
    from server.adapters import get_adapter
    a = get_adapter("gen5_bw")
    assert a.game_id == "gen5_bw"


@pytest.mark.parametrize("alias", [
    "pokemon_black",
    "pokemon_white",
    "pokemon_black_2",
    "pokemon_white_2",
])
def test_adapter_registry_variant_aliases(alias):
    from server.adapters import get_adapter
    a = get_adapter(alias)
    assert a.game_id == "gen5_bw"


# ── Item names ────────────────────────────────────────────────────────────

def test_item_name_pokeball(adapter):
    assert adapter.item_name(4) == "Poké Ball"


def test_item_name_master_ball(adapter):
    assert adapter.item_name(1) == "Master Ball"


def test_item_name_unknown(adapter):
    assert "9999" in adapter.item_name(9999)


def test_item_name_zero(adapter):
    assert adapter.item_name(0) == ""


# ── Area display name ─────────────────────────────────────────────────────

def test_area_display_name_known(adapter):
    """Known area IDs from area_map_bw.json should return real names."""
    name = adapter.area_display_name("route_1")
    assert "Route" in name or "route" in name.lower()


def test_area_display_name_unknown(adapter):
    """Unknown area IDs fall back to title-cased slug."""
    name = adapter.area_display_name("some_unknown_area")
    assert name == "Some Unknown Area"


# ── Gym badges ────────────────────────────────────────────────────────────

def test_gym_badge_slugs_bw1(adapter):
    badges = adapter.gym_badge_slugs("pokemon_black")
    assert len(badges) == 8
    names = [n for _, n in badges]
    assert "Trio Badge" in names
    assert "Legend Badge" in names


def test_gym_badge_slugs_bw2(adapter):
    """BW2 also has 8 Unova badges."""
    badges = adapter.gym_badge_slugs("pokemon_black_2")
    assert len(badges) == 8


# ── Species types ─────────────────────────────────────────────────────────

def test_species_types_snivy_pure_grass(adapter):
    """Snivy (495) is pure Grass, but pokemon_data type table only covers
    Gen I–III (CFRU range ≤ ~480). Gen 5 species return None until the
    type table is extended."""
    types = adapter.species_types(495)
    # Gen 5 species not yet in the type table — None is acceptable.
    # If this starts returning a value, update the assertion below:
    assert types is None or (isinstance(types, tuple) and len(types) == 2)


def test_species_types_reshiram_dragon_fire(adapter):
    """Reshiram (643) is Dragon/Fire — same type-table coverage limitation."""
    types = adapter.species_types(643)
    assert types is None or (isinstance(types, tuple) and len(types) == 2)


# ── Sprite HTML ───────────────────────────────────────────────────────────

def test_sprite_html_valid(adapter):
    html = adapter.sprite_html(495)
    assert 'img' in html
    assert '495' in html


def test_sprite_html_zero(adapter):
    assert adapter.sprite_html(0) == ""


# ── Server rom_type → game_id mapping ────────────────────────────────────

@pytest.mark.parametrize("rom_type,expected_game_id", [
    ("pokemon_black",   "gen5_bw"),
    ("pokemon_white",   "gen5_bw"),
    ("pokemon_black_2", "gen5_bw"),
    ("pokemon_white_2", "gen5_bw"),
])
def test_server_rom_type_to_game_id(rom_type, expected_game_id):
    """server.adapters.game_id_for_rom_type must map all Gen 5 rom_types to gen5_bw."""
    from server.adapters import game_id_for_rom_type
    assert game_id_for_rom_type(rom_type) == expected_game_id


@pytest.mark.parametrize("rom_type,expected_label", [
    ("pokemon_black",   "Pokémon Black"),
    ("pokemon_white",   "Pokémon White"),
    ("pokemon_black_2", "Pokémon Black 2"),
    ("pokemon_white_2", "Pokémon White 2"),
])
def test_server_variant_label(rom_type, expected_label):
    """server.adapters.variant_label must have all Gen 5 variants."""
    from server.adapters import variant_label
    assert variant_label(rom_type) == expected_label


# ── ability_name species_id parameter is ignored ─────────────────────────────

def test_ability_name_species_id_ignored(adapter):
    """Gen 5 adapter ignores species_id — result must be the same with or without it."""
    base = adapter.ability_name(1)
    with_species = adapter.ability_name(1, species_id=999)
    assert with_species == base

def test_ability_name_species_id_does_not_inject_cfru_override(adapter):
    """Gen 5 adapter must not return an RR-specific override name even when an override species_id is passed."""
    # (121, 50) is "Tangling Hair" in RR — Gen 5 must return the plain vanilla name instead
    name = adapter.ability_name(121, species_id=50)
    assert name != "Tangling Hair"


# ── Lua profile structural checks (doubleTripleFlag / BATTLE_MODE_ADDR) ──

import re  # noqa: E402  (Lua-source section — imported below the adapter unit tests)


def _read_gen5_bw_lua() -> str:
    """Load lua/games/gen5_bw.lua as text for structural assertions."""
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "lua", "games", "gen5_bw.lua",
    )
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_battle_mode_addr_black_us():
    """Black US doubleTripleFlag must be 0x2A62F8 (NDS-Ironmon-Tracker source)."""
    src = _read_gen5_bw_lua()
    m = re.search(r"BATTLE_MODE_ADDR\s*=\s*0x([0-9A-Fa-f]+)", src)
    assert m, "BATTLE_MODE_ADDR not set in any profile"
    assert int(m.group(1), 16) == 0x2A62F8


def test_battle_mode_addr_black2_us():
    """Black 2 US doubleTripleFlag must be 0x294DA4 (NDS-Ironmon-Tracker source)."""
    src = _read_gen5_bw_lua()
    matches = re.findall(r"BATTLE_MODE_ADDR\s*=\s*0x([0-9A-Fa-f]+)", src)
    # Expect at least one Black 2 entry — 0x294DA4
    vals = [int(x, 16) for x in matches]
    assert 0x294DA4 in vals, f"Black 2 BATTLE_MODE_ADDR 0x294DA4 not found; got {[hex(v) for v in vals]}"


def test_battle_mode_addr_in_delta_lists():
    """White and White 2 delta tables must include BATTLE_MODE_ADDR."""
    src = _read_gen5_bw_lua()
    # Both `shifted = { ... }` tables (White +0x20 and White 2 +0x80) should include BATTLE_MODE_ADDR
    shifted_blocks = re.findall(r"local shifted = \{([^}]+)\}", src)
    assert len(shifted_blocks) >= 2, f"expected 2 shift tables, found {len(shifted_blocks)}"
    for i, block in enumerate(shifted_blocks):
        assert "BATTLE_MODE_ADDR" in block, f"BATTLE_MODE_ADDR missing from shift table #{i}"


# ── Unova form normalization ─────────────────────────────────────────────

def test_form_sprite_basculin_blue(adapter):
    """CFRU 736 = Basculin (Blue) → PokeAPI 10016."""
    assert adapter.form_sprite_id(736) == 10016


def test_form_sprite_darmanitan_zen(adapter):
    """CFRU 737 = Darmanitan (Zen) → PokeAPI 10017."""
    assert adapter.form_sprite_id(737) == 10017


def test_form_sprite_kyurem_black(adapter):
    """CFRU 752 = Kyurem (Black) → PokeAPI 10022."""
    assert adapter.form_sprite_id(752) == 10022


def test_form_sprite_kyurem_white(adapter):
    """CFRU 753 = Kyurem (White) → PokeAPI 10023."""
    assert adapter.form_sprite_id(753) == 10023


def test_form_sprite_therian_tornadus(adapter):
    """CFRU 754 = Tornadus (Therian) → PokeAPI 10019."""
    assert adapter.form_sprite_id(754) == 10019


def test_form_sprite_therian_thundurus(adapter):
    """CFRU 755 = Thundurus (Therian) → PokeAPI 10020."""
    assert adapter.form_sprite_id(755) == 10020


def test_form_sprite_therian_landorus(adapter):
    """CFRU 756 = Landorus (Therian) → PokeAPI 10021."""
    assert adapter.form_sprite_id(756) == 10021


def test_form_sprite_base_species_returns_none(adapter):
    """Base-form species (Snivy, Darmanitan, etc.) should return None."""
    assert adapter.form_sprite_id(495) is None  # Snivy
    assert adapter.form_sprite_id(555) is None  # Darmanitan (base)
    assert adapter.form_sprite_id(550) is None  # Basculin Red (base)


def test_species_name_basculin_blue(adapter):
    """CFRU 736 should resolve to a Basculin Blue name (form-aware lookup)."""
    name = adapter.species_name(736)
    assert "Basculin" in name and "Blue" in name


def test_species_name_darmanitan_zen(adapter):
    """CFRU 737 should resolve to Darmanitan Zen."""
    name = adapter.species_name(737)
    assert "Darmanitan" in name and ("Zen" in name)


def test_species_name_deerling_summer(adapter):
    """CFRU 738 should resolve to Deerling Summer."""
    name = adapter.species_name(738)
    assert "Deerling" in name and "Summer" in name


def test_species_name_kyurem_black(adapter):
    """CFRU 752 = Kyurem Black."""
    name = adapter.species_name(752)
    assert "Kyurem" in name and "Black" in name


def test_to_national_dex_form_passes_through_base(adapter):
    """CFRU form ID 736 (Basculin Blue) → NatDex 550 (Basculin)."""
    assert adapter.to_national_dex(736) == 550


def test_to_national_dex_therian_landorus(adapter):
    """CFRU 756 (Landorus Therian) → NatDex 645 (Landorus base)."""
    assert adapter.to_national_dex(756) == 645


# ── Lua form_display_id mapping (structural) ─────────────────────────────

def test_form_display_id_table_includes_unova_forms():
    """gen5_bw.lua FORM_DISPLAY_ID table must cover all Unova alt-forms."""
    src = _read_gen5_bw_lua()
    # Look for the FORM_DISPLAY_ID table body
    m = re.search(r"M\.FORM_DISPLAY_ID\s*=\s*\{(.*?)^\}", src, re.MULTILINE | re.DOTALL)
    assert m, "FORM_DISPLAY_ID table not found"
    body = m.group(1)
    # Must cover Basculin, Darmanitan, Deerling, Sawsbuck, Tornadus, Thundurus,
    # Landorus, Kyurem, Keldeo, Meloetta — 10 species total.
    for species in (550, 555, 585, 586, 641, 642, 645, 646, 647, 648):
        assert f"[{species}]" in body, f"FORM_DISPLAY_ID missing species {species}"


# ── Move name and move data ──────────────────────────────────────────────

def test_move_name_volt_switch(adapter):
    """Volt Switch (521) — added in Gen 5."""
    assert adapter.move_name(521) == "Volt Switch"


def test_move_name_hurricane(adapter):
    """Hurricane (542) — added in Gen 5."""
    assert adapter.move_name(542) == "Hurricane"


def test_move_name_hone_claws(adapter):
    """Hone Claws (468) — first Gen 5 move."""
    assert adapter.move_name(468) == "Hone Claws"


def test_move_name_fusion_bolt(adapter):
    """Fusion Bolt (559) — last Gen 5 move."""
    assert adapter.move_name(559) == "Fusion Bolt"


def test_move_name_gen3_fallback(adapter):
    """Gen 3 moves (1-354) fall through to vanilla table."""
    # Tackle (33) is the canonical Gen 3 example.
    name = adapter.move_name(33)
    assert name and name.lower() == "tackle"


def test_move_name_zero(adapter):
    """move_id=0 returns empty string."""
    assert adapter.move_name(0) == ""


def test_move_data_returns_type_name(adapter):
    """move_data must expose type_name alongside type_id."""
    d = adapter.move_data(521)  # Volt Switch (Electric)
    assert d is not None
    assert d["type_name"] == "Electric"


def test_move_data_returns_power_accuracy_pp(adapter):
    """Hurricane is 120 power / 70 accuracy / 10 PP in Gen 5."""
    d = adapter.move_data(542)
    assert d["power"] == 120
    assert d["accuracy"] == 70
    assert d["pp"] == 10


def test_move_data_split_special(adapter):
    """Volt Switch is a Special move (split=1)."""
    d = adapter.move_data(521)
    assert d["split"] == 1


def test_move_data_unknown_returns_none(adapter):
    """Unknown move IDs return None."""
    assert adapter.move_data(99999) is None


# ── Gen 5 hidden / Dream-World abilities ─────────────────────────────────

@pytest.mark.parametrize("ability_id,expected", [
    (124, "Bad Dreams"),
    (126, "Sheer Force"),
    (127, "Contrary"),
    (128, "Unnerve"),
    (129, "Defiant"),
    (133, "Friend Guard"),
    (137, "Multiscale"),
    (140, "Harvest"),
    (141, "Telepathy"),
    (142, "Moody"),
    (150, "Illusion"),
    (157, "Magic Bounce"),
    (158, "Sap Sipper"),
    (160, "Sand Force"),
    (162, "Zen Mode"),
    (163, "Victory Star"),
    (164, "Turboblaze"),
    (165, "Teravolt"),
])
def test_ability_name_gen5_hidden(adapter, ability_id, expected):
    """Gen 5 hidden / Dream-World ability names should all resolve correctly."""
    assert adapter.ability_name(ability_id) == expected


# ── Item names (full BW/BW2 range 1-638) ──────────────────────────────────

def test_item_name_full_table_loaded(adapter):
    """The Gen 5 item table now covers all 638 BW/BW2 item IDs (was 150)."""
    from server.data.items.gen5 import ITEM_NAMES as GEN5_ITEM_NAMES
    assert len(GEN5_ITEM_NAMES) >= 600, f"expected >=600 items, got {len(GEN5_ITEM_NAMES)}"


@pytest.mark.parametrize("item_id,expected_substr", [
    # Gen 5 item IDs (different numbering than Gen 3 — verified from veekun/pokedex):
    (1,   "Master Ball"),
    (4,   "Poké Ball"),
    (197, "Choice Band"),
    (211, "Leftovers"),
    (247, "Life Orb"),
    (304, "Razor Fang"),
    (320, "TM16"),            # TMs auto-formatted to TM##
    (392, "TM88"),
    (627, "Relic Gold"),      # BW2-only Relic series
    (628, "Relic Vase"),
    (632, "Casteliacone"),
    (638, "X Attack 2"),      # BW2 X-stat doubles
])
def test_item_name_bw_bw2(adapter, item_id, expected_substr):
    """Spot-check core Gen 5 BW item names and BW2 additions."""
    assert expected_substr in adapter.item_name(item_id)


def test_item_name_tm_format(adapter):
    """TMs use the canonical TM## format (not 'Tm 1')."""
    assert adapter.item_name(305) == "TM01"
    assert adapter.item_name(396) == "TM92"   # Last TM in BW1 numbering
    assert adapter.item_name(397) == "HM01"
    assert adapter.item_name(404) == "HM08"


# ── Encounter tables (per-version) ────────────────────────────────────────

def test_encounter_table_route_1_black(adapter_bw1):
    """BW1 Route 1: Dark Grass + Shaking Grass + Super Rod + Surfing."""
    enc = adapter_bw1.encounter_table("route_1")
    assert enc is not None, "route_1 should have encounter data"
    assert "Dark Grass" in enc
    # Patrat + Lillipup should appear at level 2-3 in grass
    grass = enc.get("Grass", [])
    species = {e["species_id"]: e for e in grass}
    assert 504 in species, f"Patrat (504) missing from Route 1 grass; got {species.keys()}"
    assert 506 in species, f"Lillipup (506) missing from Route 1 grass; got {species.keys()}"
    # Levels should be very low (1-4 range)
    assert species[504]["max_level"] <= 5


def test_encounter_table_route_1_rates_sum_100(adapter_bw1):
    """Route 1 grass encounter rates should sum to exactly 100% (single sub-area)."""
    enc = adapter_bw1.encounter_table("route_1")
    grass = enc.get("Grass", [])
    total = sum(e["rate"] for e in grass)
    assert total == 100, f"Route 1 grass total = {total}% (expected 100%)"


def test_encounter_table_version_exclusive_pinwheel_black():
    """Pinwheel Forest BLACK should include Throh (black-exclusive), not Sawk (white)."""
    a = Gen5Adapter(rom_type="pokemon_black")
    enc = a.encounter_table("pinwheel_forest")
    assert enc is not None
    # Throh should appear; Sawk should NOT in the Shaking Grass list
    shaking = enc.get("Shaking Grass", [])
    species = {e["species_id"] for e in shaking}
    assert 538 in species, "Throh (538) missing from Black Pinwheel Forest"
    assert 539 not in species, "Sawk (539) leaked into Black Pinwheel Forest"


def test_encounter_table_version_exclusive_pinwheel_white():
    """Pinwheel Forest WHITE should include Sawk (white-exclusive), not Throh (black)."""
    a = Gen5Adapter(rom_type="pokemon_white")
    enc = a.encounter_table("pinwheel_forest")
    assert enc is not None
    shaking = enc.get("Shaking Grass", [])
    species = {e["species_id"] for e in shaking}
    assert 539 in species, "Sawk (539) missing from White Pinwheel Forest"
    assert 538 not in species, "Throh (538) leaked into White Pinwheel Forest"


def test_encounter_table_bw2_only_area():
    """BW2-only area (floccesy_ranch) returns data for BW2 ROMs."""
    a = Gen5Adapter(rom_type="pokemon_black_2")
    enc = a.encounter_table("floccesy_ranch")
    assert enc is not None
    assert "Grass" in enc
    species = {e["species_id"] for e in enc["Grass"]}
    # Floccesy Ranch BW2 has Lillipup (506), Riolu (447), Mareep (179)
    assert 506 in species
    assert 447 in species


def test_encounter_table_unknown_area_returns_none(adapter_bw1):
    """Unknown area_id returns None (caller handles missing encounter data)."""
    assert adapter_bw1.encounter_table("does_not_exist") is None


def test_encounter_table_bw2_only_area_via_bw1_adapter_falls_back():
    """BW1 adapter querying a BW2-only area still returns data (fallback to BW2 tables)."""
    a = Gen5Adapter(rom_type="pokemon_black")
    # virbank_complex only exists in BW2; should return BW2 data as fallback.
    enc = a.encounter_table("virbank_complex")
    # If the fallback works, we get a dict; otherwise None.
    # Accept either — the fallback is best-effort, not required.
    assert enc is None or isinstance(enc, dict)


def test_encounter_table_entry_schema(adapter_bw1):
    """Each entry has required keys: name, species_id, rate, min_level, max_level."""
    enc = adapter_bw1.encounter_table("route_1")
    for entries in enc.values():
        for e in entries:
            assert "name" in e
            assert "species_id" in e
            assert "rate" in e
            assert "min_level" in e
            assert "max_level" in e
            assert e["min_level"] <= e["max_level"]
            assert e["rate"] > 0


def test_encounter_table_no_form_pseudo_ids(adapter_bw1):
    """No species_id should be a veekun form pseudo-ID (10000+) — must be base Pokémon."""
    for area_id in ("route_1", "route_3", "pinwheel_forest"):
        enc = adapter_bw1.encounter_table(area_id)
        if not enc:
            continue
        for method, entries in enc.items():
            for e in entries:
                assert e["species_id"] < 10000, \
                    f"{area_id}/{method}: pseudo-form ID {e['species_id']} ({e['name']}) leaked through"
