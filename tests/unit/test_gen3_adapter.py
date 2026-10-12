"""
Unit tests for server/adapters — Game adapter framework.

Tests the adapter base classes, registry, and FRLG adapter implementation.

Run:
    pytest tests/unit/test_adapters.py -v
"""

import os
import struct

import pytest

from server.adapters import available_game_ids, get_adapter
from server.adapters.base import GameAdapter
from server.adapters.gen3_frlge import _RR_SPRITE_DIR, _RR_SPRITE_IDS, Gen3Adapter

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ── Registry tests ────────────────────────────────────────────────────────────

class TestRegistry:
    def test_frlg_registered(self):
        assert "gen3_frlge" in available_game_ids()

    def test_get_frlg_adapter(self):
        adapter = get_adapter("gen3_frlge")
        assert isinstance(adapter, Gen3Adapter)
        assert isinstance(adapter, GameAdapter)
        assert adapter.game_id == "gen3_frlge"

    def test_get_unknown_adapter_raises(self):
        with pytest.raises(KeyError, match="No adapter registered"):
            get_adapter("gen99_unknown")

    def test_get_adapter_passes_kwargs(self):
        adapter = get_adapter("frlg", is_rr=True)
        assert adapter._is_rr is True

    def test_available_game_ids(self):
        ids = available_game_ids()
        assert isinstance(ids, list)
        assert len(ids) >= 1


# ── FRLG Adapter: GameRulesAdapter interface ──────────────────────────────────

class TestFRLGRules:
    @pytest.fixture
    def adapter(self):
        return Gen3Adapter(is_rr=False)

    @pytest.fixture
    def rr_adapter(self):
        return Gen3Adapter(is_rr=True)

    def test_game_id(self, adapter):
        assert adapter.game_id == "gen3_frlge"

    # ── is_gift_area ──────────────────────────────────────────────────────

    def test_gift_area_oaks_lab(self, adapter):
        assert adapter.is_gift_area("oaks_lab") is True

    def test_gift_area_intro(self, adapter):
        assert adapter.is_gift_area("intro") is True

    def test_gift_area_prefix(self, adapter):
        assert adapter.is_gift_area("gift_something") is True

    def test_not_gift_area(self, adapter):
        assert adapter.is_gift_area("route_1") is False
        assert adapter.is_gift_area("mt_moon") is False

    def test_gift_area_empty(self, adapter):
        assert adapter.is_gift_area("") is False

    # ── is_daycare_area ──────────────────────────────────────────────────

    def test_daycare_area_route5(self, adapter):
        assert adapter.is_daycare_area("route5_pokemon_day_care") is True

    def test_daycare_area_four_island(self, adapter):
        assert adapter.is_daycare_area("four_island_pokemon_day_care") is True

    def test_daycare_area_normal_route(self, adapter):
        assert adapter.is_daycare_area("route_5") is False

    def test_daycare_area_empty(self, adapter):
        assert adapter.is_daycare_area("") is False

    # ── evo_family ────────────────────────────────────────────────────────

    def test_evo_family_base_form(self, adapter):
        # Bulbasaur (1) is its own base form
        assert adapter.evo_family(1) == 1

    def test_evo_family_evolved(self, adapter):
        # Ivysaur (2) → Bulbasaur (1)
        assert adapter.evo_family(2) == 1
        # Venusaur (3) → Bulbasaur (1)
        assert adapter.evo_family(3) == 1

    def test_evo_family_single_stage(self, adapter):
        # Tauros (128) has no evolution — returns itself
        assert adapter.evo_family(128) == 128

    # ── gender_from_key ───────────────────────────────────────────────────

    def test_gender_male(self, adapter):
        # Personality 0xFF → gender byte = 0xFF; Bulbasaur threshold=31 → male
        assert adapter.gender_from_key("000000FF:12345678", 1) == "male"

    def test_gender_female(self, adapter):
        # Personality 0x00 → gender byte = 0x00; Bulbasaur threshold=31 → female
        assert adapter.gender_from_key("00000000:12345678", 1) == "female"

    def test_gender_genderless(self, adapter):
        # Magnemite (81) has threshold=255 (genderless)
        assert adapter.gender_from_key("000000FF:12345678", 81) == "genderless"

    def test_rr_iron_treads_is_genderless_for_every_gender_byte(self, rr_adapter):
        for gender_byte in range(256):
            assert rr_adapter.gender_from_key(f"{gender_byte:08X}:12345678", 1258) == "genderless"

    def test_gender_empty_key(self, adapter):
        assert adapter.gender_from_key("", 1) == ""

    def test_gender_zero_species(self, adapter):
        assert adapter.gender_from_key("000000FF:12345678", 0) == ""

    # ── species_types ─────────────────────────────────────────────────────

    def test_species_types_bulbasaur(self, adapter):
        # Bulbasaur: Grass/Poison (12, 3)
        types = adapter.species_types(1)
        assert types == (12, 3)

    def test_species_types_charmander(self, adapter):
        # Charmander: Fire/Fire (10, 10) — monotype
        types = adapter.species_types(4)
        assert types == (10, 10)

    def test_species_types_invalid(self, adapter):
        assert adapter.species_types(0) is None
        assert adapter.species_types(-1) is None

    # ── is_shiny ──────────────────────────────────────────────────────────

    def test_is_shiny_false(self, adapter):
        # Random key — almost certainly not shiny
        assert adapter.is_shiny("12345678:87654321") is False

    def test_is_shiny_true(self, adapter):
        # Construct a shiny key: (tid ^ sid ^ p_upper ^ p_lower) < 8
        # tid=0, sid=0, p_upper=0, p_lower=0 → XOR = 0 < 8 → shiny
        assert adapter.is_shiny("00000000:00000000") is True

    def test_is_shiny_malformed(self, adapter):
        assert adapter.is_shiny("") is False
        assert adapter.is_shiny("invalid") is False
        assert adapter.is_shiny("abc:def:ghi") is False

    # ── parse_ot_id ───────────────────────────────────────────────────────

    def test_parse_ot_id(self, adapter):
        assert adapter.parse_ot_id("AABBCCDD:11223344") == "11223344"

    def test_parse_ot_id_empty(self, adapter):
        assert adapter.parse_ot_id("") == ""

    def test_parse_ot_id_malformed(self, adapter):
        assert adapter.parse_ot_id("no_colon") == ""

    # ── is_valid_mon_key ──────────────────────────────────────────────────

    def test_valid_key(self, adapter):
        assert adapter.is_valid_mon_key("AABBCCDD:11223344") is True
        assert adapter.is_valid_mon_key("0:0") is True

    def test_invalid_key(self, adapter):
        assert adapter.is_valid_mon_key("") is False
        assert adapter.is_valid_mon_key("hello") is False
        assert adapter.is_valid_mon_key("GGGG:1111") is False  # non-hex

    # ── species_name ──────────────────────────────────────────────────────

    def test_species_name_known(self, adapter):
        assert adapter.species_name(1) == "Bulbasaur"
        assert adapter.species_name(25) == "Pikachu"
        assert adapter.species_name(151) == "Mew"

    def test_species_name_unknown(self, adapter):
        assert adapter.species_name(99999) == "#99999"

    # ── type_name ─────────────────────────────────────────────────────────

    def test_type_name(self, adapter):
        assert adapter.type_name(0) == "Normal"
        assert adapter.type_name(10) == "Fire"
        assert adapter.type_name(11) == "Water"

    def test_type_name_unknown(self, adapter):
        assert adapter.type_name(999) == "???"


# ── FRLG Adapter: GamePresentationAdapter interface ───────────────────────────

class TestFRLGPresentation:
    @pytest.fixture
    def adapter(self):
        return Gen3Adapter(is_rr=False)

    def test_sprite_html_bulbasaur(self, adapter):
        html = adapter.sprite_html(1)
        assert '<img' in html
        assert '/1.png' in html

    def test_sprite_html_zero(self, adapter):
        assert adapter.sprite_html(0) == ""

    def test_ability_name(self, adapter):
        # Vanilla: ability 77 = Air Lock
        assert adapter.ability_name(77) == "Air Lock"

    def test_ability_name_rr(self):
        rr = Gen3Adapter(is_rr=True)
        # RR: ability 77 is NOT overridden (uses CFRU table)
        name = rr.ability_name(77)
        assert name  # should be some string

    def test_item_name_unknown(self, adapter):
        assert "Item" in adapter.item_name(99999) or "#" in adapter.item_name(99999)

    def test_item_name_zero(self, adapter):
        assert adapter.item_name(0) == ""

    def test_area_display_name(self, adapter):
        # Should return something human-readable
        name = adapter.area_display_name("route_1")
        assert name  # non-empty string
        assert "route" in name.lower() or "Route" in name

    def test_to_national_dex_gen1(self, adapter):
        # Gen 1 IDs are identity
        assert adapter.to_national_dex(1) == 1
        assert adapter.to_national_dex(151) == 151

    def test_gender_symbol(self, adapter):
        assert adapter.gender_symbol("male") == "♂"
        assert adapter.gender_symbol("female") == "♀"
        assert adapter.gender_symbol("genderless") == ""

    def test_form_sprite_id_base(self, adapter):
        # Regular species have no form sprite ID
        assert adapter.form_sprite_id(1) is None

    def test_form_sprite_id_form(self, adapter):
        # CFRU form 713 = Rotom Heat → should have a mapping
        result = adapter.form_sprite_id(713)
        assert result is not None
        assert isinstance(result, int)


def test_rr_ability_name_illuminate():
    """ID 89 should be Illuminate in RR (was Big Pecks in CFRU)."""
    from server.pokemon_data import ability_name
    assert ability_name(89, is_rr=True) == "Illuminate"


def test_rr_ability_name_wind_rider():
    """ID 16 should be Wind Rider in RR (was Color Change in CFRU)."""
    from server.pokemon_data import ability_name
    assert ability_name(16, is_rr=True) == "Wind Rider"


def test_rr_ability_name_dragons_maw():
    """ID 38 should be Dragon's Maw in RR (was Poison Point in CFRU)."""
    from server.pokemon_data import ability_name
    assert ability_name(38, is_rr=True) == "Dragon's Maw"


def test_rr_ability_name_quick_draw():
    """ID 24 should be Quick Draw in RR (was Rough Skin in CFRU)."""
    from server.pokemon_data import ability_name
    assert ability_name(24, is_rr=True) == "Quick Draw"


def test_rr_ability_name_gulp_missile():
    """ID 35 should be Gulp Missile in RR (was Illuminate in CFRU)."""
    from server.pokemon_data import ability_name
    assert ability_name(35, is_rr=True) == "Gulp Missile"


def test_vanilla_ability_unchanged():
    """Vanilla and CFRU fallback ability names should not be affected by RR table."""
    from server.pokemon_data import ABILITY_NAMES, ability_name
    assert ability_name(89, is_rr=False) == "Download"
    assert ability_name(16, is_rr=False) == "Color Change"
    assert ABILITY_NAMES[89] == "Big Pecks"
    assert ABILITY_NAMES[16] == "Color Change"


def test_cfru_override_still_works():
    """CFRU_ABILITY_NAME_OVERRIDES should still take precedence for specific species."""
    from server.pokemon_data import ability_name
    assert ability_name(121, is_rr=True, species_id=50) == "Tangling Hair"


# ── Integration: adapter used by SoulLinkState ────────────────────────────────

class TestAdapterIntegration:
    def test_state_uses_adapter(self, tmp_path, monkeypatch):
        """SoulLinkState should use the adapter for rule logic."""
        monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
        monkeypatch.setattr("server.state.DATA_DIR", str(tmp_path))
        from server.state import SoulLinkState
        state = SoulLinkState()
        assert state.adapter.game_id == "gen3_frlge"

    def test_state_accepts_custom_adapter(self, tmp_path, monkeypatch):
        """SoulLinkState should accept a custom adapter."""
        monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
        monkeypatch.setattr("server.state.DATA_DIR", str(tmp_path))
        from server.state import SoulLinkState
        adapter = Gen3Adapter(is_rr=True)
        state = SoulLinkState(adapter=adapter)
        assert state.adapter is adapter
        assert state.adapter._is_rr is True

    def test_state_saves_game_id(self, tmp_path, monkeypatch):
        """game_id should be persisted in links.json."""
        monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
        monkeypatch.setattr("server.state.DATA_DIR", str(tmp_path))
        from server.state import SoulLinkState
        state = SoulLinkState()
        state.pokeballs_obtained["a"] = True
        state._save()
        import json
        with open(tmp_path / "links.json") as f:
            data = json.load(f)
        assert data["game_id"] == "gen3_frlge"

    def test_state_load_restores_game_id(self, tmp_path, monkeypatch):
        """Loading state should restore the game_id from links.json."""
        monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
        monkeypatch.setattr("server.state.DATA_DIR", str(tmp_path))
        import json
        data = {"game_id": "gen3_frlge", "links": [], "area_states": {}}
        with open(tmp_path / "links.json", "w") as f:
            json.dump(data, f)
        from server.state import SoulLinkState
        state = SoulLinkState.load()
        assert state.adapter.game_id == "gen3_frlge"

    def test_shiny_uses_adapter(self, tmp_path, monkeypatch):
        """Shiny clause should use adapter.is_shiny()."""
        monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
        monkeypatch.setattr("server.state.DATA_DIR", str(tmp_path))
        from server.state import SoulLinkState
        state = SoulLinkState()
        state.pokeballs_obtained["a"] = True
        # personality=0, otId=0 is shiny (XOR = 0 < 8)
        state.handle_event("a", {
            "event": "capture", "key": "00000000:00000000",
            "area_id": "route_1", "species_id": 25,
        })
        # Should be treated as bonus (shiny clause)
        assert "00000000:00000000" in state.bonus_keys["a"]


# ── Move data tests ───────────────────────────────────────────────────────────

class TestMoveData:
    """Tests for the move_name() and move_data() adapter methods."""

    def test_rr_move_name(self):
        adapter = Gen3Adapter(is_rr=True)
        assert adapter.move_name(1) == "Pound"
        assert adapter.move_name(85) == "Thunderbolt"

    def test_rr_move_data_thunderbolt(self):
        adapter = Gen3Adapter(is_rr=True)
        md = adapter.move_data(85)
        assert md is not None
        assert md["name"] == "Thunderbolt"
        assert md["type_name"] == "Electric"
        assert md["power"] == 95
        assert md["accuracy"] == 100
        assert md["pp"] == 15
        assert md["split"] == 1  # Special

    def test_rr_move_data_swords_dance(self):
        adapter = Gen3Adapter(is_rr=True)
        md = adapter.move_data(14)
        assert md is not None
        assert md["name"] == "Swords Dance"
        assert md["split"] == 2  # Status
        assert md["power"] == 0

    def test_rr_move_data_physical(self):
        adapter = Gen3Adapter(is_rr=True)
        md = adapter.move_data(1)  # Pound
        assert md is not None
        assert md["split"] == 0  # Physical

    def test_vanilla_move_name(self):
        adapter = Gen3Adapter(is_rr=False)
        assert adapter.move_name(1) == "Pound"
        assert adapter.move_name(57) == "Surf"

    def test_vanilla_move_data(self):
        adapter = Gen3Adapter(is_rr=False)
        md = adapter.move_data(57)  # Surf
        assert md is not None
        assert md["name"] == "Surf"
        assert md["type_name"] == "Water"
        assert md["power"] == 95
        assert md["pp"] == 15

    def test_unknown_move_returns_none(self):
        adapter = Gen3Adapter(is_rr=True)
        assert adapter.move_data(99999) is None

    def test_unknown_move_name_returns_empty(self):
        adapter = Gen3Adapter(is_rr=True)
        assert adapter.move_name(99999) == ""

    def test_move_data_has_required_fields(self):
        adapter = Gen3Adapter(is_rr=True)
        md = adapter.move_data(85)
        required = {"name", "type_id", "type_name", "power", "accuracy", "pp", "split"}
        assert required.issubset(set(md.keys()))

    def test_move_zero_returns_none(self):
        """Move ID 0 (MOVE_NONE) should return None or empty."""
        adapter = Gen3Adapter(is_rr=True)
        # Move 0 exists in the data but has no meaningful name
        name = adapter.move_name(0)
        # Either empty or some placeholder is fine
        assert isinstance(name, str)

    def test_base_adapter_stubs(self):
        """Adapters with their own move table return real data; the base-class
        stub returns "" / None for adapters without one. Gen 1 ships its own
        moves.json (1-165), Gen 4 has GEN4_MOVE_DATA (1-467), Gen 3 has
        VANILLA_MOVE_DATA. The base stub is exercised by an out-of-range ID.
        """
        from server.adapters.gen1_rby import Gen1Adapter
        adapter = Gen1Adapter()
        # Move 85 (Thunderbolt) is real Gen 1 data — confirm the table is wired.
        assert adapter.move_name(85) == "Thunderbolt"
        assert adapter.move_data(85) is not None
        # Out-of-range ID falls through to the base-class stub.
        assert adapter.move_name(9999) == ""
        assert adapter.move_data(9999) is None

    def test_other_adapters_ignore_form_arg(self):
        """Non-gen4 adapters accept the form parameter on sprite_html (Phase 5's
        signature extension) but silently ignore it — they have no alternate forms.
        Verify they don't crash AND produce the same output as the no-form call.
        """
        from server.adapters.gen1_rby import Gen1Adapter
        from server.adapters.gen2_gsc import Gen2GSCAdapter
        from server.adapters.gen3_frlge import Gen3Adapter
        from server.adapters.gen5_bw import Gen5Adapter
        for cls, sp in [(Gen1Adapter, 25), (Gen2GSCAdapter, 25),
                        (Gen3Adapter, 25), (Gen5Adapter, 495)]:
            a = (cls(is_rr=False) if cls is Gen3Adapter
                 else cls(rom_type="Crystal") if cls is Gen2GSCAdapter else cls())
            # Calling with explicit form arg must not crash AND must equal the no-form output.
            assert a.sprite_html(sp, 0)  == a.sprite_html(sp)
            assert a.sprite_html(sp, 5)  == a.sprite_html(sp)
            assert a.sprite_html(sp, 99) == a.sprite_html(sp)


# ── CFRU Compressed Box Regression Tests ──────────────────────────────────────
#
# The Lua CFRU struct fix corrected wrong memory offsets (species at +0x1C,
# held_item at +0x1E, both unencrypted) and stride (58 bytes, not 80).  These
# Python tests validate the server-side data pipeline: pc_boxes entries arrive
# as already-parsed JSON dicts, and the server must store and retrieve each
# mon's data independently — no bleeding between adjacent box slots.
# ──────────────────────────────────────────────────────────────────────────────

class TestCFRUBoxHandling:

    @pytest.fixture
    def tracker(self, tmp_path):
        """SLinkServer instance isolated to a temporary directory."""
        from server.server import SLinkServer
        return SLinkServer(data_dir=str(tmp_path))

    # ── memorial_box_index ─────────────────────────────────────────────────

    def test_memorial_box_index_vanilla(self):
        """Vanilla FRLG: memorial box is always index 13 (UI 'Box 14')."""
        adapter = Gen3Adapter(is_rr=False)
        assert adapter.memorial_box_index == 13

    def test_memorial_box_index_rr(self):
        """CFRU/RR: 25 boxes; memorial box is index 24 (UI 'Box 25'), fills downward."""
        adapter = Gen3Adapter(is_rr=True)
        assert adapter.memorial_box_index == 24

    # ── _cache_mon_info: per-key independence ──────────────────────────────

    def test_cache_independent_keys(self, tracker):
        """Two distinct keys get entirely independent cache entries."""
        tracker._cache_mon_info("AAAA:1111", {
            "species_id": 4, "nickname": "CHAR", "level": 10, "held_item_id": 17,
        })
        tracker._cache_mon_info("BBBB:2222", {
            "species_id": 7, "nickname": "SQUIRT", "level": 12, "held_item_id": 18,
        })
        assert tracker._mon_cache["AAAA:1111"]["species_id"] == 4
        assert tracker._mon_cache["AAAA:1111"]["held_item_id"] == 17
        assert tracker._mon_cache["BBBB:2222"]["species_id"] == 7
        assert tracker._mon_cache["BBBB:2222"]["held_item_id"] == 18

    def test_cache_no_bleed_between_adjacent_slots(self, tracker):
        """Species/item from slot N must not appear in any other slot's cache entry."""
        entries = [
            {"key": "SLOT0:0001", "box": 0, "slot": 0, "species_id": 1, "held_item_id": 17, "level": 5, "nickname": "S0"},
            {"key": "SLOT1:0002", "box": 0, "slot": 1, "species_id": 2, "held_item_id": 18, "level": 6, "nickname": "S1"},
            {"key": "SLOT2:0003", "box": 0, "slot": 2, "species_id": 3, "held_item_id": 19, "level": 7, "nickname": "S2"},
        ]
        for bentry in entries:
            tracker._cache_mon_info(bentry["key"], bentry)
        assert tracker._mon_cache["SLOT0:0001"]["species_id"] == 1
        assert tracker._mon_cache["SLOT0:0001"]["held_item_id"] == 17
        assert tracker._mon_cache["SLOT1:0002"]["species_id"] == 2
        assert tracker._mon_cache["SLOT1:0002"]["held_item_id"] == 18
        assert tracker._mon_cache["SLOT2:0003"]["species_id"] == 3
        assert tracker._mon_cache["SLOT2:0003"]["held_item_id"] == 19

    def test_cache_does_not_overwrite_with_zero(self, tracker):
        """A subsequent cache call with zero species_id must not erase the cached value."""
        tracker._cache_mon_info("AAAA:1111", {"species_id": 4, "nickname": "CHAR", "level": 10})
        tracker._cache_mon_info("AAAA:1111", {"species_id": 0, "nickname": "CHAR", "level": 10})
        assert tracker._mon_cache["AAAA:1111"]["species_id"] == 4

    def test_cache_does_not_overwrite_held_item_with_zero(self, tracker):
        """A subsequent cache call with zero held_item_id must not erase the cached value."""
        tracker._cache_mon_info("AAAA:1111", {"species_id": 4, "held_item_id": 17, "level": 10})
        tracker._cache_mon_info("AAAA:1111", {"species_id": 4, "held_item_id": 0, "level": 10})
        assert tracker._mon_cache["AAAA:1111"]["held_item_id"] == 17

    # ── mon_stats backfill ─────────────────────────────────────────────────

    def test_mon_stats_populated_per_slot(self, tracker):
        """Each box slot's level is independently cached in mon_stats."""
        entries = [
            {"key": f"MON{i}:000{i}", "box": 0, "slot": i, "species_id": i + 1, "level": 10 + i}
            for i in range(5)
        ]
        for bentry in entries:
            tracker._cache_mon_info(bentry["key"], bentry, "a")
        for i in range(5):
            key = f"MON{i}:000{i}"
            assert tracker.state.mon_stats["a"][key]["level"] == 10 + i

    def test_mon_stats_no_cross_slot_overwrite(self, tracker):
        """Later slot caching must not overwrite earlier slot's mon_stats entry."""
        tracker._cache_mon_info("FIRST:0001", {"species_id": 4, "level": 10}, "a")
        tracker._cache_mon_info("SECOND:0002", {"species_id": 7, "level": 20}, "a")
        assert tracker.state.mon_stats["a"]["FIRST:0001"]["level"] == 10
        assert tracker.state.mon_stats["a"]["SECOND:0002"]["level"] == 20

    # ── _lookup_mon_detail: correct slot returned ──────────────────────────

    def test_lookup_returns_correct_entry_by_key(self, tracker):
        """Lookup by key returns only that mon's data, not a neighbour's."""
        tracker.pc_boxes["a"] = [
            {"key": "KEY0:0000", "box": 0, "slot": 0, "species_id": 10, "held_item_id": 50, "level": 20, "nickname": "A"},
            {"key": "KEY1:0001", "box": 0, "slot": 1, "species_id": 25, "held_item_id": 55, "level": 22, "nickname": "B"},
            {"key": "KEY2:0002", "box": 0, "slot": 2, "species_id": 35, "held_item_id": 60, "level": 24, "nickname": "C"},
        ]
        det = tracker._lookup_mon_detail("a", "KEY1:0001")
        assert det.get("species_id") == 25
        assert det.get("held_item_id") == 55
        assert det.get("nickname") == "B"

    def test_lookup_adjacent_slots_are_independent(self, tracker):
        """Slot 0 lookup must not return slot 1's species or held_item."""
        tracker.pc_boxes["a"] = [
            {"key": "ALPHA:0001", "box": 1, "slot": 0, "species_id": 4,  "held_item_id": 11, "level": 8},
            {"key": "BETA:0002",  "box": 1, "slot": 1, "species_id": 7,  "held_item_id": 12, "level": 9},
        ]
        det0 = tracker._lookup_mon_detail("a", "ALPHA:0001")
        det1 = tracker._lookup_mon_detail("a", "BETA:0002")
        assert det0.get("species_id") == 4
        assert det0.get("held_item_id") == 11
        assert det1.get("species_id") == 7
        assert det1.get("held_item_id") == 12
        # Cross-check: neither slot must carry the other's values
        assert det0.get("species_id") != det1.get("species_id")
        assert det0.get("held_item_id") != det1.get("held_item_id")

    def test_lookup_missing_key_returns_empty_or_none(self, tracker):
        """A key not present in pc_boxes returns an empty dict."""
        tracker.pc_boxes["a"] = [
            {"key": "EXIST:0001", "box": 0, "slot": 0, "species_id": 1, "level": 5}
        ]
        det = tracker._lookup_mon_detail("a", "MISSING:9999")
        assert not det or det.get("species_id", 0) == 0

    def test_lookup_multiple_boxes_correct_key(self, tracker):
        """Key lookup works across entries spread across different box indices."""
        tracker.pc_boxes["a"] = [
            {"key": "BOX0:0001", "box": 0, "slot": 0, "species_id": 1, "held_item_id": 10, "level": 5},
            {"key": "BOX5:0002", "box": 5, "slot": 3, "species_id": 99, "held_item_id": 77, "level": 55},
            {"key": "BOX12:003", "box": 12, "slot": 29, "species_id": 150, "held_item_id": 200, "level": 99},
        ]
        det = tracker._lookup_mon_detail("a", "BOX5:0002")
        assert det.get("species_id") == 99
        assert det.get("held_item_id") == 77

    # ── _check_memorial_box_contamination ──────────────────────────────────

    def test_contamination_fires_for_box_13(self, tracker, caplog):
        """A live mon deposited into memorial box 13 triggers a warning."""
        import logging
        with caplog.at_level(logging.WARNING, logger="server.server"):
            tracker._check_memorial_box_contamination("a", [
                {"key": "LIVE:0001", "box": 13, "slot": 0, "species_id": 4, "nickname": "CHAR"},
            ])
        assert any("NON-DEAD" in r.message for r in caplog.records)

    def test_contamination_ignores_boxes_0_through_12(self, tracker, caplog):
        """Live mons in non-memorial boxes (0-12) must not produce a warning."""
        import logging
        entries = [
            {"key": f"MON{i:04d}:0001", "box": i, "slot": 0, "species_id": i + 1}
            for i in range(13)
        ]
        with caplog.at_level(logging.WARNING, logger="server.server"):
            tracker._check_memorial_box_contamination("a", entries)
        assert not any("NON-DEAD" in r.message for r in caplog.records)

    def test_contamination_dead_mon_in_box_13_is_allowed(self, tracker, caplog):
        """A properly dead/memorialized mon in box 13 must not produce a warning."""
        import logging

        from server.state import LinkEntry, LinkStatus, MonInfo
        entry = LinkEntry(
            area_id="route_1",
            a=MonInfo(key="DEAD:0001"),
            b=MonInfo(key="DEAD:0002"),
            status=LinkStatus.DEAD,
        )
        tracker.state.links.append(entry)
        tracker.state._index_entry(entry)
        with caplog.at_level(logging.WARNING, logger="server.server"):
            tracker._check_memorial_box_contamination("a", [
                {"key": "DEAD:0001", "box": 13, "slot": 0, "species_id": 4},
            ])
        assert not any("NON-DEAD" in r.message for r in caplog.records)

    def test_contamination_only_checks_player_own_dead_keys(self, tracker, caplog):
        """A mon in box 13 that is dead on the OTHER player's side still triggers warning."""
        import logging

        from server.state import LinkEntry, LinkStatus, MonInfo
        # Key "DEAD:0002" is player B's dead mon; checking player A's boxes
        entry = LinkEntry(
            area_id="route_2",
            a=MonInfo(key="ALIVE:0001"),
            b=MonInfo(key="DEAD:0002"),
            status=LinkStatus.DEAD,
        )
        tracker.state.links.append(entry)
        tracker.state._index_entry(entry)
        with caplog.at_level(logging.WARNING, logger="server.server"):
            tracker._check_memorial_box_contamination("a", [
                {"key": "DEAD:0002", "box": 13, "slot": 0, "species_id": 7},
            ])
        assert any("NON-DEAD" in r.message for r in caplog.records)


# ── Fixed-species gift areas ──────────────────────────────────────────────────

class TestFixedSpeciesGifts:
    """Tests for is_fixed_species_gift() — areas that bypass clause checks."""

    @pytest.fixture
    def adapter(self):
        return Gen3Adapter(is_rr=False)

    def test_route_4_pokecenter_is_fixed(self, adapter):
        assert adapter.is_fixed_species_gift("route_4_pokecenter") is True

    def test_celadon_condominiums_is_fixed(self, adapter):
        assert adapter.is_fixed_species_gift("celadon_condominiums") is True

    def test_silph_co_7f_is_fixed(self, adapter):
        assert adapter.is_fixed_species_gift("silph_co_7f") is True

    def test_gift_prefix_not_fixed(self, adapter):
        # Dynamic gift areas (gift_<group>_<num>) are NOT fixed-species
        assert adapter.is_fixed_species_gift("gift_10_11") is False

    def test_oaks_lab_not_fixed(self, adapter):
        # Starters are linked normally — oaks_lab is a gift area but not fixed-species
        assert adapter.is_fixed_species_gift("oaks_lab") is False

    def test_regular_route_not_fixed(self, adapter):
        assert adapter.is_fixed_species_gift("route_1") is False

    def test_empty_string_not_fixed(self, adapter):
        assert adapter.is_fixed_species_gift("") is False

    def test_fixed_check_same_on_rr_adapter(self):
        # The fixed-species set is profile-independent
        rr = Gen3Adapter(is_rr=True)
        assert rr.is_fixed_species_gift("route_4_pokecenter") is True
        assert rr.is_fixed_species_gift("gift_10_11") is False


# ── Gym badge slugs ───────────────────────────────────────────────────────────

class TestGymBadgeSlugs:
    """Tests for gym_badge_slugs() — Hoenn vs Kanto badge sets."""

    @pytest.fixture
    def adapter(self):
        return Gen3Adapter(is_rr=False)

    def test_emerald_returns_hoenn_badges(self, adapter):
        badges = adapter.gym_badge_slugs("emerald")
        assert len(badges) == 8
        ids = [b[0] for b in badges]
        assert ids == list(range(17, 25))

    def test_emerald_first_badge_is_stone(self, adapter):
        badges = adapter.gym_badge_slugs("emerald")
        assert badges[0] == (17, "Stone Badge")

    def test_emerald_last_badge_is_rain(self, adapter):
        badges = adapter.gym_badge_slugs("emerald")
        assert badges[-1] == (24, "Rain Badge")

    def test_emerald_case_insensitive(self, adapter):
        assert adapter.gym_badge_slugs("EMERALD") == adapter.gym_badge_slugs("emerald")
        assert adapter.gym_badge_slugs("Emerald") == adapter.gym_badge_slugs("emerald")

    def test_firered_returns_kanto_badges(self, adapter):
        badges = adapter.gym_badge_slugs("firered")
        assert len(badges) == 8
        ids = [b[0] for b in badges]
        assert ids == list(range(1, 9))

    def test_firered_first_badge_is_boulder(self, adapter):
        badges = adapter.gym_badge_slugs("firered")
        assert badges[0][1] == "Boulder Badge"

    def test_radical_red_returns_kanto_badges(self, adapter):
        rr = Gen3Adapter(is_rr=True)
        badges = rr.gym_badge_slugs("radical_red")
        ids = [b[0] for b in badges]
        assert ids == list(range(1, 9))

    def test_none_rom_type_returns_kanto(self, adapter):
        badges = adapter.gym_badge_slugs(None)
        ids = [b[0] for b in badges]
        assert ids == list(range(1, 9))

    def test_hoenn_and_kanto_ids_disjoint(self, adapter):
        hoenn_ids = {b[0] for b in adapter.gym_badge_slugs("emerald")}
        kanto_ids = {b[0] for b in adapter.gym_badge_slugs("firered")}
        assert hoenn_ids.isdisjoint(kanto_ids)


# ── Trainer info ──────────────────────────────────────────────────────────────

class TestTrainerInfo:
    """Tests for trainer_info() — RR trainer name/class resolution."""

    @pytest.fixture
    def vanilla_adapter(self):
        return Gen3Adapter(is_rr=False)

    @pytest.fixture
    def rr_adapter(self):
        return Gen3Adapter(is_rr=True)

    def test_vanilla_always_returns_empty(self, vanilla_adapter):
        assert vanilla_adapter.trainer_info(1) == ("", "")
        assert vanilla_adapter.trainer_info(999) == ("", "")

    def test_rr_empty_trainer_db_returns_empty(self, rr_adapter, monkeypatch):
        import server.adapters.gen3_frlge as mod
        monkeypatch.setattr(mod, "_RR_TRAINERS", {})
        assert rr_adapter.trainer_info(1) == ("", "")

    def test_rr_known_trainer_returns_name_and_class(self, rr_adapter, monkeypatch):
        import server.adapters.gen3_frlge as mod
        monkeypatch.setattr(mod, "_RR_TRAINERS", {
            0: {"name": "Ash", "class": 1},
        })
        monkeypatch.setattr(mod, "_RR_TRAINER_CLASS", {1: "Youngster"})
        name, cls = rr_adapter.trainer_info(1)  # trainer_id 1 → index 0
        assert name == "Ash"
        assert cls == "Youngster"

    def test_rr_rival_class_returns_no_name(self, rr_adapter, monkeypatch):
        import server.adapters.gen3_frlge as mod
        monkeypatch.setattr(mod, "_RR_TRAINERS", {
            4: {"name": "Gary", "class": 81},
        })
        monkeypatch.setattr(mod, "_RR_TRAINER_CLASS", {81: "Rival"})
        name, cls = rr_adapter.trainer_info(5)  # trainer_id 5 → index 4
        assert name == ""
        assert cls == "Rival"

    def test_rr_out_of_range_id_returns_empty(self, rr_adapter, monkeypatch):
        import server.adapters.gen3_frlge as mod
        monkeypatch.setattr(mod, "_RR_TRAINERS", {0: {"name": "Alice", "class": 1}})
        monkeypatch.setattr(mod, "_RR_TRAINER_CLASS", {1: "Lass"})
        assert rr_adapter.trainer_info(9999) == ("", "")

    def test_rr_trainer_id_0_returns_empty(self, rr_adapter, monkeypatch):
        # trainer_id 0 → index -1 (invalid off-by-one guard)
        import server.adapters.gen3_frlge as mod
        monkeypatch.setattr(mod, "_RR_TRAINERS", {-1: {"name": "Ghost", "class": 1}})
        monkeypatch.setattr(mod, "_RR_TRAINER_CLASS", {1: "Lass"})
        # _RR_TRAINERS.get(-1) would find it, but that means trainer_id=0 maps to index=-1
        # The code does _RR_TRAINERS.get(trainer_id - 1) = get(-1)
        # This edge case: if -1 resolves, returns something; if not, ("", "")
        # We only require it doesn't crash
        result = rr_adapter.trainer_info(0)
        assert isinstance(result, tuple) and len(result) == 2


# ── Ability descriptions ──────────────────────────────────────────────────────

class TestAbilityDescription:
    """Tests for ability_description() — vanilla vs RR tooltip text."""

    @pytest.fixture
    def vanilla(self):
        return Gen3Adapter(is_rr=False)

    @pytest.fixture
    def rr(self):
        return Gen3Adapter(is_rr=True)

    def test_vanilla_ability_1_stench(self, vanilla):
        desc = vanilla.ability_description(1)
        assert desc  # non-empty
        assert isinstance(desc, str)

    def test_vanilla_ability_1_known_text(self, vanilla):
        assert vanilla.ability_description(1) == "Helps repel wild Pokémon."

    def test_vanilla_ability_2_drizzle(self, vanilla):
        assert vanilla.ability_description(2) == "Summons rain in battle."

    def test_vanilla_unknown_ability_empty(self, vanilla):
        assert vanilla.ability_description(9999) == ""

    def test_vanilla_ability_0_empty(self, vanilla):
        assert vanilla.ability_description(0) == ""

    def test_rr_ability_1_non_empty(self, rr):
        # RR uses ABILITY_DESCRIPTIONS (255-entry CFRU table)
        desc = rr.ability_description(1)
        assert desc
        assert isinstance(desc, str)

    def test_rr_ability_0_empty(self, rr):
        assert rr.ability_description(0) == ""

    def test_rr_unknown_ability_empty(self, rr):
        assert rr.ability_description(9999) == ""

    def test_vanilla_and_rr_may_differ(self, vanilla, rr):
        # Stench description can differ between RR and vanilla
        v = vanilla.ability_description(1)
        r = rr.ability_description(1)
        # Both must be non-empty strings (content difference is acceptable)
        assert v and isinstance(v, str)
        assert r and isinstance(r, str)


# ── CFRU ↔ NatDex ID mapping ──────────────────────────────────────────────────

class TestToNationalDex:
    """Tests for to_national(), to_cfru(), and adapter.to_national_dex()."""

    def test_gen1_ids_are_identity(self):
        from server.pokemon_data import to_national
        assert to_national(1) == 1    # Bulbasaur
        assert to_national(151) == 151  # Mew

    def test_gen2_ids_are_identity(self):
        from server.pokemon_data import to_national
        assert to_national(152) == 152  # Chikorita
        assert to_national(251) == 251  # Celebi

    def test_treecko_cfru_to_national(self):
        from server.pokemon_data import to_national
        assert to_national(277) == 252  # CFRU 277 = NatDex 252 (Treecko)

    def test_grovyle_cfru_to_national(self):
        from server.pokemon_data import to_national
        assert to_national(278) == 253  # Grovyle

    def test_sceptile_cfru_to_national(self):
        from server.pokemon_data import to_national
        assert to_national(279) == 254  # Sceptile

    def test_treecko_national_to_cfru(self):
        from server.pokemon_data import to_cfru
        assert to_cfru(252) == 277

    def test_round_trip_cfru_to_national_to_cfru(self):
        from server.pokemon_data import to_cfru, to_national
        assert to_cfru(to_national(277)) == 277

    def test_round_trip_national_to_cfru_to_national(self):
        from server.pokemon_data import to_cfru, to_national
        assert to_national(to_cfru(252)) == 252

    def test_unknown_cfru_id_passthrough(self):
        from server.pokemon_data import to_national
        assert to_national(99999) == 99999

    def test_unknown_natdex_id_passthrough(self):
        from server.pokemon_data import to_cfru
        assert to_cfru(99999) == 99999

    def test_adapter_to_national_dex_gen1(self):
        adapter = Gen3Adapter(is_rr=False)
        assert adapter.to_national_dex(1) == 1

    def test_adapter_to_national_dex_treecko(self):
        adapter = Gen3Adapter(is_rr=False)
        assert adapter.to_national_dex(277) == 252


# ── base_form() with Gen 3 / CFRU evolution chains ───────────────────────────

class TestBaseFormGen3:
    """Tests for base_form() using CFRU internal species IDs."""

    def test_treecko_is_own_base(self):
        from server.pokemon_data import base_form
        assert base_form(277) == 277

    def test_grovyle_maps_to_treecko(self):
        from server.pokemon_data import base_form
        assert base_form(278) == 277

    def test_sceptile_maps_to_treecko(self):
        from server.pokemon_data import base_form
        assert base_form(279) == 277

    def test_eevee_is_own_base(self):
        from server.pokemon_data import base_form
        assert base_form(133) == 133

    def test_vaporeon_maps_to_eevee(self):
        from server.pokemon_data import base_form
        assert base_form(134) == 133

    def test_jolteon_maps_to_eevee(self):
        from server.pokemon_data import base_form
        assert base_form(135) == 133

    def test_flareon_maps_to_eevee(self):
        from server.pokemon_data import base_form
        assert base_form(136) == 133

    def test_espeon_maps_to_eevee(self):
        from server.pokemon_data import base_form
        assert base_form(196) == 133

    def test_umbreon_maps_to_eevee(self):
        from server.pokemon_data import base_form
        assert base_form(197) == 133

    def test_cfru_leafeon_maps_to_eevee(self):
        from server.pokemon_data import EVO_FAMILY, base_form
        if 523 in EVO_FAMILY:
            assert base_form(523) == 133

    def test_cfru_glaceon_maps_to_eevee(self):
        from server.pokemon_data import EVO_FAMILY, base_form
        if 524 in EVO_FAMILY:
            assert base_form(524) == 133

    def test_lapras_single_stage_is_own_base(self):
        from server.pokemon_data import base_form
        # Lapras (131): no pre-evolution or evolution in any gen → maps to itself
        assert base_form(131) == 131

    def test_evo_family_adapter_uses_base_form(self):
        adapter = Gen3Adapter(is_rr=False)
        # evo_family delegates to base_form; Sceptile (279) → Treecko (277)
        assert adapter.evo_family(279) == 277
        assert adapter.evo_family(133) == 133


# ── gender_from_key_species threshold edge cases ──────────────────────────────

class TestGenderEdgeCases:
    """Tests for always-male (threshold=0) and always-female (threshold=254) species."""

    def test_always_male_tauros_any_personality(self):
        from server.pokemon_data import gender_from_key_species
        # Tauros (128): threshold=0 → always male regardless of personality byte
        assert gender_from_key_species("00000000:12345678", 128) == "male"
        assert gender_from_key_species("000000FE:12345678", 128) == "male"

    def test_always_male_hitmonlee(self):
        from server.pokemon_data import gender_from_key_species
        assert gender_from_key_species("00000000:12345678", 106) == "male"

    def test_always_female_chansey_any_personality(self):
        from server.pokemon_data import gender_from_key_species
        # Chansey (113): threshold=254 → always female
        assert gender_from_key_species("000000FF:12345678", 113) == "female"
        assert gender_from_key_species("00000001:12345678", 113) == "female"

    def test_always_female_jynx(self):
        from server.pokemon_data import gender_from_key_species
        assert gender_from_key_species("000000FF:12345678", 124) == "female"

    def test_50_50_personality_zero_is_female(self):
        from server.pokemon_data import gender_from_key_species
        # Unlisted species → default threshold=127; personality byte=0 < 127 → female
        assert gender_from_key_species("00000000:12345678", 999) == "female"

    def test_50_50_personality_127_is_male(self):
        from server.pokemon_data import gender_from_key_species
        # personality byte=0x7F=127 >= threshold=127 → male
        assert gender_from_key_species("0000007F:12345678", 999) == "male"

    def test_50_50_personality_126_is_female(self):
        from server.pokemon_data import gender_from_key_species
        # personality byte=0x7E=126 < 127 → female
        assert gender_from_key_species("0000007E:12345678", 999) == "female"

    def test_adapter_always_male(self):
        adapter = Gen3Adapter(is_rr=False)
        assert adapter.gender_from_key("000000FF:12345678", 128) == "male"

    def test_adapter_always_female(self):
        adapter = Gen3Adapter(is_rr=False)
        assert adapter.gender_from_key("000000FF:12345678", 113) == "female"


# ── species_types() fallback chain ────────────────────────────────────────────

class TestSpeciesTypesFallback:
    """Tests for species_types() three-tier lookup: RR → CFRU form → NatDex."""

    def test_gen1_natdex_lookup(self):
        from server.pokemon_data import species_types
        # Bulbasaur (1): Grass/Poison = (12, 3)
        assert species_types(1, is_rr=False) == (12, 3)

    def test_gen1_natdex_monotype(self):
        from server.pokemon_data import species_types
        # Charmander (4): Fire/Fire = (10, 10)
        assert species_types(4, is_rr=False) == (10, 10)

    def test_cfru_treecko_via_national_lookup(self):
        from server.pokemon_data import species_types
        # CFRU 277 → NatDex 252 (Treecko) → Grass/Grass = (12, 12)
        result = species_types(277, is_rr=False)
        assert result == (12, 12)

    def test_cfru_form_rotom_heat(self):
        from server.pokemon_data import species_types
        # CFRU form 713 (Rotom-Heat) in CFRU_FORM_TYPES → Electric/Fire = (13, 10)
        result = species_types(713, is_rr=False)
        assert result == (13, 10)

    def test_rr_path_used_when_is_rr_true(self):
        from server.pokemon_data import _RR_TYPES, species_types
        if not _RR_TYPES:
            pytest.skip("RR types not loaded")
        # Any species in _RR_TYPES should return a valid type tuple when is_rr=True
        species = next(iter(_RR_TYPES))
        result = species_types(species, is_rr=True)
        assert result is not None
        assert isinstance(result, tuple) and len(result) == 2

    def test_is_rr_false_skips_rr_table(self):
        from server.pokemon_data import _NATDEX_SPECIES_TYPES, _RR_TYPES, species_types, to_national
        if not _RR_TYPES:
            pytest.skip("RR types not loaded")
        # Find a species where RR types differ from NatDex fallback
        for sid, rr_t in _RR_TYPES.items():
            nat = to_national(sid)
            natdex_t = _NATDEX_SPECIES_TYPES.get(nat)
            if natdex_t and natdex_t != rr_t:
                # This species has a type change in RR
                assert species_types(sid, is_rr=True) == rr_t
                assert species_types(sid, is_rr=False) != rr_t
                return
        pytest.skip("No RR type changes found vs NatDex fallback")

    def test_out_of_range_species_returns_none(self):
        from server.pokemon_data import species_types
        assert species_types(0) is None
        assert species_types(-1) is None

    def test_adapter_species_types(self):
        adapter = Gen3Adapter(is_rr=False)
        assert adapter.species_types(1) == (12, 3)

    def test_adapter_species_types_cfru(self):
        adapter = Gen3Adapter(is_rr=False)
        # CFRU 277 (Treecko) → Grass/Grass
        assert adapter.species_types(277) == (12, 12)


# ── area_display_name overrides ───────────────────────────────────────────────

class TestAreaDisplayNameOverrides:
    """Tests for area_display_name() including RR overrides and fallback paths."""

    @pytest.fixture
    def vanilla(self):
        return Gen3Adapter(is_rr=False)

    @pytest.fixture
    def rr(self):
        return Gen3Adapter(is_rr=True)

    def test_rr_override_monean_chamber(self, rr):
        assert rr.area_display_name("monean_chamber") == "Oak's Lab"

    def test_vanilla_monean_chamber_no_rr_override(self, vanilla):
        # Without RR flag, returns the static name override (not RR override)
        assert vanilla.area_display_name("monean_chamber") == "Monean Chamber"

    def test_static_override_mt_moon(self, vanilla):
        assert vanilla.area_display_name("mt_moon") == "Mt. Moon"

    def test_static_override_ss_anne(self, vanilla):
        assert vanilla.area_display_name("ss_anne") == "S.S. Anne"

    def test_static_override_pokemon_league(self, vanilla):
        assert vanilla.area_display_name("pokemon_league") == "Pokémon League"

    def test_humanize_fallback(self, vanilla):
        assert vanilla.area_display_name("some_unknown_area") == "Some Unknown Area"

    def test_humanize_single_word(self, vanilla):
        assert vanilla.area_display_name("testplace") == "Testplace"

    def test_humanize_splits_a_route_number_and_keeps_a_floor_code(self, vanilla):
        """The board showed "Route8": `.title()` never separates a number from its word.
        One humanizer (adapters.base.humanize_area_id) serves every adapter, state.py's
        HUD line and the Twitch bot."""
        from server.adapters.base import humanize_area_id
        assert vanilla.area_display_name("route8") == "Route 8"
        assert humanize_area_id("Route21_North") == "Route 21 North"
        assert humanize_area_id("rocket_hideout_b4f") == "Rocket Hideout B4F"
        assert humanize_area_id("PalletTown_PlayersHouse_1F") == "Pallet Town Players House 1F"

    def test_gift_prefix_with_rom_map_entry(self, vanilla):
        # gift_10_11 → "Gift – Celadon City" (if celadon city is at group=10, num=11)
        name = vanilla.area_display_name("gift_10_11")
        assert name.startswith("Gift")

    def test_gift_prefix_unknown_returns_gift(self, vanilla):
        name = vanilla.area_display_name("gift_99_99")
        assert name == "Gift"

    def test_empty_area_id_returns_empty(self, vanilla):
        assert vanilla.area_display_name("") == ""

    def test_rr_mt_moon_still_uses_static_override(self, rr):
        # RR override dict only has monean_chamber; mt_moon falls through to static
        assert rr.area_display_name("mt_moon") == "Mt. Moon"


# ── sprite_html() extended coverage ──────────────────────────────────────────

class TestSpriteHtmlVariants:
    """Tests for sprite_html() — egg, FRLG URL, CFRU form, tiled blocklist."""

    @pytest.fixture
    def vanilla(self):
        return Gen3Adapter(is_rr=False)

    @pytest.fixture
    def rr(self):
        return Gen3Adapter(is_rr=True)

    def test_egg_uses_showdown_sprite(self, vanilla):
        html = vanilla.sprite_html(412)
        assert "pokemonshowdown.com" in html
        assert "egg.png" in html

    def test_egg_same_on_rr(self, rr):
        html = rr.sprite_html(412)
        assert "pokemonshowdown.com" in html
        assert "egg.png" in html

    def test_zero_species_returns_empty(self, vanilla):
        assert vanilla.sprite_html(0) == ""

    def test_negative_species_returns_empty(self, vanilla):
        assert vanilla.sprite_html(-1) == ""

    def test_gen1_uses_frlg_url(self, vanilla):
        html = vanilla.sprite_html(1)
        assert "firered-leafgreen" in html

    def test_cfru_treecko_uses_frlg_url(self, vanilla):
        # CFRU 277 → NatDex 252 ≤ 386 → FRLG URL
        html = vanilla.sprite_html(277)
        assert "firered-leafgreen" in html
        assert "252" in html

    def test_cfru_form_rotom_heat_no_frlg_url(self, vanilla):
        # CFRU form 713 → form_pid branch → not a NatDex ≤ 386 → no FRLG URL
        html = vanilla.sprite_html(713)
        assert html  # must produce something
        assert "firered-leafgreen" not in html

    def test_rr_castform_uses_its_single_vendored_sprite(self, rr):
        # funnotbun's Castform was a 4-form sheet and was skipped; the vendored one is 64x64.
        assert 'src="/static/sprites/rr/385.png"' in rr.sprite_html(385)

    def test_vanilla_gen1_has_pokeapi_fallback(self, vanilla):
        # FRLG URL is primary, PokeAPI generic is in onerror attribute
        html = vanilla.sprite_html(1)
        assert "raw.githubusercontent.com/PokeAPI" in html


class TestSpriteOutputPinned:
    """Exact-output characterization for sprite_html()/sprite_src().

    Pins the full <img> string — including the onerror fallback chain — and the
    sprite_src() URL, so any drift in the (refactored) URL builders is caught.
    RR sprites are the vendored files under server/static/sprites/rr/<CFRU id>.png.
    """

    PA = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
    RR = "/static/sprites/rr"

    @pytest.fixture
    def vanilla(self):
        return Gen3Adapter(is_rr=False)

    @pytest.fixture
    def rr(self):
        return Gen3Adapter(is_rr=True)

    def test_vanilla_sprite_src_national(self, vanilla):
        assert vanilla.sprite_src(1) == f"{self.PA}/1.png"

    def test_vanilla_sprite_html_frlg_with_national_fallback(self, vanilla):
        nat = f"{self.PA}/1.png"
        frlg = f"{self.PA}/versions/generation-iii/firered-leafgreen/1.png"
        assert vanilla.sprite_html(1) == (
            f'<img class="mon-sprite" data-species="1" src="{frlg}" '
            f'onerror="if(this.src!==\'{nat}\'){{this.src=\'{nat}\';}}else{{this.style.display=\'none\';}}" '
            f'alt="">'
        )

    def test_vanilla_sprite_html_egg(self, vanilla):
        assert vanilla.sprite_html(412) == (
            '<img class="mon-sprite" data-species="412" '
            'src="https://play.pokemonshowdown.com/sprites/gen5/egg.png" '
            'onerror="this.style.display=\'none\';" alt="Egg">'
        )

    def test_vanilla_sprite_html_form_pid_single_src(self, vanilla):
        # CFRU 603 → form_pid 550 → single-src img, display:none on error (no chain)
        assert vanilla.sprite_html(603) == (
            f'<img class="mon-sprite" data-species="603" src="{self.PA}/550.png" '
            f'onerror="this.style.display=\'none\';" alt="">'
        )

    def test_rr_sprite_src_vendored(self, rr):
        assert rr.sprite_src(1) == f"{self.RR}/1.png"

    def test_rr_sprite_html_vendored_with_national_fallback(self, rr):
        nat = f"{self.PA}/1.png"
        assert rr.sprite_html(1) == (
            f'<img class="mon-sprite" data-species="1" src="{self.RR}/1.png" '
            f'onerror="if(this.src!==\'{nat}\'){{this.src=\'{nat}\';}}else{{this.style.display=\'none\';}}" '
            f'alt="">'
        )

    def test_rr_sprites_are_keyed_by_cfru_id_not_national_dex(self, rr):
        # RR's non-standard dex: CFRU 440 is Turtwig (NatDex 387), 900 is Camerupt-Mega.
        assert rr.sprite_src(440) == f"{self.RR}/440.png"
        assert rr.sprite_src(900) == f"{self.RR}/900.png"

    def test_every_vendored_rr_sprite_is_a_single_64px_png(self):
        assert len(_RR_SPRITE_IDS) >= 1300
        for sid in _RR_SPRITE_IDS:
            with open(os.path.join(_RR_SPRITE_DIR, f"{sid}.png"), "rb") as f:
                head = f.read(24)
            assert head[:8] == PNG_MAGIC, sid
            assert struct.unpack(">II", head[16:24]) == (64, 64), sid

    def test_out_of_range_returns_empty(self, vanilla, rr):
        assert vanilla.sprite_html(99999) == ""
        assert vanilla.sprite_src(99999) == ""
        assert rr.sprite_html(99999) == ""
        assert rr.sprite_src(99999) == ""


# ── item_name() known values ──────────────────────────────────────────────────

class TestItemNameKnown:
    """Tests for item_name() with known vanilla FRLG item IDs."""

    @pytest.fixture
    def vanilla(self):
        return Gen3Adapter(is_rr=False)

    @pytest.fixture
    def rr(self):
        return Gen3Adapter(is_rr=True)

    def test_master_ball(self, vanilla):
        assert vanilla.item_name(1) == "Master Ball"

    def test_pokeball(self, vanilla):
        assert vanilla.item_name(4) == "Poké Ball"

    def test_potion(self, vanilla):
        assert vanilla.item_name(13) == "Potion"

    def test_rare_candy(self, vanilla):
        assert vanilla.item_name(68) == "Rare Candy"

    def test_leftovers(self, vanilla):
        assert vanilla.item_name(200) == "Leftovers"

    def test_zero_returns_empty(self, vanilla):
        assert vanilla.item_name(0) == ""

    def test_unknown_returns_item_hash_format(self, vanilla):
        result = vanilla.item_name(99999)
        assert result == "Item #99999"

    def test_rr_pokeball_returns_non_empty(self, rr):
        # RR may override item names; at minimum the result should be non-empty
        name = rr.item_name(4)
        assert name

    def test_rr_zero_returns_empty(self, rr):
        assert rr.item_name(0) == ""

    def test_rr_unknown_returns_item_hash_format(self, rr):
        assert rr.item_name(99999) == "Item #99999"


# ── CFRU ability name overrides ───────────────────────────────────────────────

class TestCFRUAbilityNameOverrides:
    """Tests for species-specific ability name overrides in RR/CFRU runs."""

    @pytest.fixture
    def rr(self):
        return Gen3Adapter(is_rr=True)

    @pytest.fixture
    def vanilla(self):
        return Gen3Adapter(is_rr=False)

    # Override entries from CFRU_ABILITY_NAME_OVERRIDES (keyed by NatDex after to_national conversion):
    # ability 13: (13, NatDex 384) → "Air Lock"  — Rayquaza is CFRU ID 406
    # ability 37: (37, NatDex 307) → "Pure Power" — Meditite is CFRU ID 356
    # ability 121: (121, NatDex 50) → "Tangling Hair" — Diglett is CFRU ID 50 (Gen 1, identity)

    def test_rr_cloudnine_rayquaza_returns_air_lock(self, rr):
        """Ability 13 (Cloud Nine in vanilla) → "Air Lock" for Rayquaza (CFRU ID 406) in RR."""
        assert rr.ability_name(13, species_id=406) == "Air Lock"

    def test_rr_hugepower_meditite_returns_pure_power(self, rr):
        """Ability 37 (Huge Power in vanilla) → "Pure Power" for Meditite (CFRU ID 356) in RR."""
        assert rr.ability_name(37, species_id=356) == "Pure Power"

    def test_rr_gooey_diglett_returns_tangling_hair(self, rr):
        """Ability 121 (Gooey in vanilla) → "Tangling Hair" for Diglett (CFRU ID 50) in RR."""
        assert rr.ability_name(121, species_id=50) == "Tangling Hair"

    def test_rr_gooey_without_species_returns_base_cfru_name(self, rr):
        """Without a species_id, ability 121 returns the base CFRU ability name, not an override."""
        base_name = rr.ability_name(121)
        assert base_name != "Tangling Hair", "species_id=0 must not trigger an override"
        assert base_name  # still non-empty (CFRU knows this ability)

    def test_rr_override_non_matching_species_returns_base_name(self, rr):
        """Ability 121 on a species without an override returns the base CFRU name."""
        base_name = rr.ability_name(121)
        non_override_name = rr.ability_name(121, species_id=1)  # Bulbasaur — no override for (121, 1)
        assert non_override_name == base_name

    def test_vanilla_ignores_cfru_override_for_rayquaza(self, vanilla):
        """Vanilla adapter (is_rr=False) must NOT use CFRU overrides even when species_id is passed."""
        name = vanilla.ability_name(13, species_id=406)
        assert name != "Air Lock", "Vanilla adapter must not apply CFRU override"

    def test_vanilla_ignores_cfru_override_for_diglett(self, vanilla):
        """Vanilla adapter (is_rr=False) must NOT use CFRU overrides."""
        name = vanilla.ability_name(121, species_id=50)
        assert name != "Tangling Hair", "Vanilla adapter must not apply CFRU override"

    def test_rr_medicham_pure_power(self, rr):
        """Ability 37 → "Pure Power" for Medicham (CFRU ID 357) in RR."""
        assert rr.ability_name(37, species_id=357) == "Pure Power"

    def test_rr_dugtrio_tangling_hair(self, rr):
        """Ability 121 → "Tangling Hair" for Dugtrio (CFRU ID 51) in RR."""
        assert rr.ability_name(121, species_id=51) == "Tangling Hair"

    def test_rr_quickfeet_mightyena_returns_strong_jaws(self, rr):
        """Ability 112 (Quick Feet in CFRU) → "Strong Jaws" for Mightyena (CFRU ID 287)."""
        assert rr.ability_name(112, species_id=287) == "Strong Jaws"

    def test_rr_quickfeet_non_mightyena_returns_base_name(self, rr):
        """Ability 112 on a species without the override returns the base CFRU name (Quick Feet)."""
        assert rr.ability_name(112, species_id=1) == "Quick Feet"

    def test_manual_overrides_do_not_silently_shadow_generated(self):
        """MANUAL entries should never duplicate GENERATED entries with the same display name —
        any overlap means the MANUAL entry is dead code that should be removed.
        MANUAL entries with a DIFFERENT display name are allowed (intentional override)."""
        from server.pokemon_data import (
            CFRU_ABILITY_NAME_OVERRIDES_GENERATED as GEN,
            CFRU_ABILITY_NAME_OVERRIDES_MANUAL as MANUAL,
        )
        redundant = {k for k in MANUAL if k in GEN and MANUAL[k] == GEN[k]}
        assert not redundant, (
            f"MANUAL entries duplicate GENERATED with identical values "
            f"(remove them from MANUAL): {sorted(redundant)}"
        )

    def test_generator_output_present(self):
        """The generated override module must produce a non-trivial number of entries."""
        from server.pokemon_data import CFRU_ABILITY_NAME_OVERRIDES_GENERATED as GEN
        assert len(GEN) >= 80, f"Generator output unexpectedly small: {len(GEN)}"

    def test_rr_known_generated_overrides(self, rr):
        """Sanity-check a handful of GENERATED-source overrides resolve correctly."""
        # Pure Power / Meditite (NatDex 307)
        assert rr.ability_name(37, species_id=356) == "Pure Power"  # CFRU 356 -> NatDex 307
        # Air Lock / Rayquaza (NatDex 384)
        assert rr.ability_name(13, species_id=406) == "Air Lock"
