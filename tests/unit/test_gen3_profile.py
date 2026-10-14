"""The Gen 3 packs' profile.json preserves Lua literals and pinned pret additions.

Legacy values are independently re-derived from Lua sources without executing Lua.
RR-P5 facts additionally check the admitted binaries, instruction/literal anchors,
independent direct pool reads, and mutations that must refuse stale witnesses.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys

import pytest

from tools import rr_companion

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "lua" / "games" / "gen3_frlge.lua"
PROFILES = {p: REPO / "data" / "games" / p / "profile.json" for p in ("gen3_frlg", "gen3_rr")}

# which Lua profile table each admitted/unadmitted title reads today
TITLE_TABLE = {
    "firered": "vanilla", "leafgreen": "vanilla", "firered_ap": "ap",
    "radical_red": "radical_red",
}

# C4-LGSE: same bug class as BASESTATS_ADDR (below) -- the shared `vanilla` Lua table can only
# carry one literal per key, and it carries FireRed's for these.  leafgreen's generator output
# instead names its own pokeleafgreen.sym symbol, so these keys are exempted from the "literal
# survives verbatim" checks and checked against their own .sym address here instead.
LEAFGREEN_OWN_ROM_VALUES = {
    "BASESTATS_ADDR": 0x08254760,
    "CB2_EVOLUTION_LOAD_ADDR": 0x080CE0BD,
    "CB2_EVOLUTION_BEGIN_ADDR": 0x080CDCED,
    "CB2_EVOLUTION_UPDATE_ADDR": 0x080CE6E5,
    "CB2_TRADE_EVOLUTION_UPDATE_ADDR": 0x080CE701,
}
LEAFGREEN_SE_SONG_HEADERS = {
    "16": 0x086B5260, "17": 0x086B52B0, "22": 0x086B53B8, "25": 0x086B548C,
    "26": 0x086B54BC, "95": 0x086B674C,
}


def _load(pack: str) -> dict:
    return json.loads(PROFILES[pack].read_text(encoding="utf-8"))


def _title(name: str) -> dict:
    pack = "gen3_rr" if name == "radical_red" else "gen3_frlg"
    return _load(pack)["titles"][name]


def _flat(title: dict) -> dict:
    """{key: value} across ram/rom/derived -- section placement is checked separately."""
    out = {}
    for section in ("ram", "rom", "derived"):
        for key, val in title[section].items():
            assert key not in out, f"{key} appears in two sections"
            out[key] = val
    return out


# ── the Lua tables, sliced with an independent regex ─────────────────────────────
def _table_text(table: str) -> str:
    text = SRC.read_text(encoding="utf-8")
    if table == "emerald":
        start = re.search(r"^GEN3\.profiles\.emerald = \{", text, re.M).end()
    else:
        start = re.search(r"^    " + table + r" = \{", text, re.M).end()
    depth, i = 1, start
    while depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start:i]


SCALAR = re.compile(r"^\s+([A-Z][A-Z0-9_]*)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,", re.M)
SE_ENTRY = re.compile(r"^\s+\[(\d+)\]\s*=\s*(0x[0-9A-Fa-f]+)\s*,", re.M)


def _num(lit: str) -> int:
    return int(lit, 16) if lit[:2].lower() == "0x" else int(lit)


@pytest.mark.parametrize("name", sorted(TITLE_TABLE))
def test_every_scalar_literal_survives_at_the_same_key(name: str) -> None:
    body = _table_text(TITLE_TABLE[name])
    flat = _flat(_title(name))
    found = dict(SCALAR.findall(body))
    assert len(found) >= 15, f"{name}: the slice regex found only {len(found)} literals"
    for key, lit in found.items():
        assert key in flat, f"{name}: {key} is missing from profile.json"
        if name == "leafgreen" and key in LEAFGREEN_OWN_ROM_VALUES:
            assert flat[key] == LEAFGREEN_OWN_ROM_VALUES[key]  # LG symbol, not the shared FR default
            continue
        assert flat[key] == _num(lit), f"{name}: {key} changed value"


@pytest.mark.parametrize("name", ["firered", "leafgreen", "firered_ap", "radical_red"])
def test_se_song_headers_survive(name: str) -> None:
    body = _table_text(TITLE_TABLE[name])
    se_block = body[body.index("SE_SONG_HEADERS"):]
    se_block = se_block[:se_block.index("}")]
    want = {k: _num(v) for k, v in SE_ENTRY.findall(se_block)}
    assert want, f"{name}: no SE_SONG_HEADERS entries sliced"
    if name == "leafgreen":
        # C4-LGSE: leafgreen's own addresses, translated by symbol name -- never the shared
        # Lua literal sliced above, which is FireRed's.
        want = {k: LEAFGREEN_SE_SONG_HEADERS[k] for k in want}
    assert _title(name)["rom"]["SE_SONG_HEADERS"] == want


def test_cfru_box_bases_are_the_25_computed_lua_expressions() -> None:
    bases = _title("radical_red")["derived"]["CFRU_BOX_BASES"]
    assert len(bases) == 25
    expected = [0x02029318 + 1740 * i for i in range(19)]
    expected += [0x0203CB44 + 1740 * i for i in range(3)]
    expected += [0x02027434 + 1740 * i for i in range(2)]
    expected += [0x02024638]
    assert bases == expected


def test_rr_storage_flags() -> None:
    derived = _title("radical_red")["derived"]
    assert derived["PARTY_IN_SB1"] is False
    assert derived["CFRU_NO_ENCRYPT"] is True


@pytest.mark.parametrize("name", ["firered", "leafgreen"])
def test_vanilla_storage_and_party_facts(name: str) -> None:
    title = _title(name)
    pin = "pret/pokefirered@c75f352304d529f6ba92d4f74b9cf8b5c3810788"
    header = f"{pin}:include/pokemon_storage_system.h"
    path = f"data/gen3/pret/poke{name}.sym"
    lines = (REPO / path).read_text(encoding="utf-8").splitlines()
    storage = [(i, line.split()) for i, line in enumerate(lines, 1)
               if line.split()[-1:] == ["gPokemonStorage"]]
    assert len(storage) == 1
    line, fields = storage[0]
    assert title["ram"]["POKEMON_STORAGE_BASE"] == int(fields[0], 16) == 0x02029314
    # Offset 4 accounts for u32 alignment, despite the header's stale 0x0001 comment.
    expected = {"BOX_DATA_OFFSET": 4, "BOXES_PER_STORE": 14,
                "MONS_PER_BOX": 30, "PARTY_CAPACITY": 6}
    for key, value in expected.items():
        assert title["derived"][key] == value
    base_src = {
        "ram.POKEMON_STORAGE_BASE": f"{path}:{line} (gPokemonStorage; {pin})",
        "derived.BOX_DATA_OFFSET": f"{header}:44-48; "
                                   f"{pin}:include/pokemon.h:105-108 (u32 alignment)",
        "derived.BOXES_PER_STORE": f"{header}:7 (TOTAL_BOXES_COUNT)",
        "derived.MONS_PER_BOX": f"{header}:8-10 (IN_BOX_ROWS * IN_BOX_COLUMNS)",
        "derived.PARTY_CAPACITY": f"{pin}:include/constants/global.h:78 (PARTY_SIZE)",
    }
    # P4 card C4-2a: every additional trainer/location/badges/bag/battle citation this card
    # adds, present for exactly the two vanilla titles and nothing else.
    c4_2a_derived_keys = {
        "EXPERIENCE_TABLE_ENTRY_COUNT", "MAX_LEVEL",
        "SB2_OT_ID_OFFSET", "SB2_NAME_OFFSET", "SB1_LOCATION_MAP_GROUP_OFFSET",
        "SB1_LOCATION_MAP_NUM_OFFSET", "SB1_BADGE_BYTE_OFFSET", "OUTCOME_WON", "OUTCOME_LOST",
        "OUTCOME_DREW", "OUTCOME_RAN", "OUTCOME_CAUGHT", "BATTLE_TYPE_TRAINER_MASK",
        "BATTLE_TYPE_DOUBLE_MASK", "GMAIN_INBATTLE_OFFSET", "GMAIN_INBATTLE_MASK",
        "BATTLE_MOVE_ENTRY_SIZE", "BATTLE_MOVE_PP_OFFSET", "BASESTATS_GROWTH_RATE_OFFSET",
        "SHEDINJA_SPECIES_ID",
        # C4-ACTIVE-FAINT-P (mechanism P)
        "STATUS3_PERISH_SONG", "DISABLE_STRUCT_SIZE", "DISABLE_STRUCT_PERISH_TIMER_OFF",
        "B_ACTION_NOTHING_FAINTED",
        "BATTLE_MON_STAT_STAGES_OFF",   # C5-6 (tests/unit/test_stat_stages.py)
        # G5-STAGES-COHERENCE (tests/unit/test_stat_stages.py TestStagesCoherence)
        "BATTLE_MON_PERSONALITY_OFF", "BATTLE_MON_OT_ID_OFF", "BATTLE_TYPE_LINK_MASK",
        "BATTLE_STRUCT_MOVE_TARGET_OFF", "BATTLE_STRUCT_CHOSEN_MOVE_POS_OFF",
    }
    c4_2a_sym_keys = {"ram.TRAINER_OPPONENT_ADDR", "rom.EXPERIENCE_TABLES_ADDR",
                       "rom.BATTLE_MOVES_ADDR", "rom.PP_UP_GET_MASK_ADDR",
                       "ram.STATUS3_ADDR", "ram.DISABLE_STRUCTS_ADDR", "ram.CHOSEN_ACTION_ADDR",
                       "ram.BATTLE_COMM_ADDR", "ram.CHOSEN_MOVE_ADDR", "ram.BATTLE_STRUCT_PTR_ADDR"}
    for key in c4_2a_derived_keys:
        assert f"derived.{key}" in title["_src"], f"{name}: derived.{key} has no _src citation"
        base_src[f"derived.{key}"] = title["_src"][f"derived.{key}"]
    for key in c4_2a_sym_keys:
        assert key in title["_src"], f"{name}: {key} has no _src citation"
        assert path in title["_src"][key], f"{name}: {key} citation does not name its own .sym file"
        base_src[key] = title["_src"][key]
    # C5-9: the intro window's Thumb values, each cited from this title's own .sym.
    for key in ("rom.BEGIN_BATTLE_INTRO_ADDR", "rom.BEGIN_BATTLE_INTRO_DUMMY_ADDR",
                "rom.BATTLE_INTRO_GET_MONS_DATA_ADDR"):
        assert key in title["_src"], f"{name}: {key} has no _src citation"
        assert path in title["_src"][key], f"{name}: {key} citation does not name its own .sym file"
        base_src[key] = title["_src"][key]
    if name == "leafgreen":
        base_src["rom.BASESTATS_ADDR"] = title["_src"]["rom.BASESTATS_ADDR"]
        # C4-LGSE: SE_SONG_HEADERS + the evolution CB2s, translated by symbol name.
        for key in LEAFGREEN_OWN_ROM_VALUES:
            if key == "BASESTATS_ADDR":
                continue
            base_src[f"rom.{key}"] = title["_src"][f"rom.{key}"]
        for song_id in LEAFGREEN_SE_SONG_HEADERS:
            base_src[f"rom.SE_SONG_HEADERS.{song_id}"] = title["_src"][f"rom.SE_SONG_HEADERS.{song_id}"]
    assert title["_src"] == base_src


def test_rr_party_capacity_comes_from_its_existing_detector() -> None:
    title = _title("radical_red")
    text = SRC.read_text(encoding="utf-8")
    start = text.index("local function _detectRR()")
    match = re.search(r"if partyCount > (\d+) then return false end", text[start:])
    assert match
    line = text.count("\n", 0, start + match.start()) + 1
    assert title["derived"]["PARTY_CAPACITY"] == int(match[1]) == 6
    # The legacy pointer row is documented where it lives: 0x03003840 is the old client's, not
    # the pack's pointer (card C3-33), so a reader cannot mistake it for the new client's source.
    legacy = title["ram"]["SB1_PTR_ADDR"]
    assert legacy == 0x03003840
    hit = re.search(rf"^\s*SB1_PTR_ADDR\s*=\s*0x{legacy:08X}\s*,", text, re.M)
    assert hit, "the radical_red SB1_PTR_ADDR literal is gone from the Lua table"
    legacy_line = text.count("\n", 0, hit.start()) + 1
    base_src = {
        "derived.PARTY_CAPACITY":
            f"lua/games/gen3_frlge.lua:{line} (_detectRR partyCount limit)",
        "ram.SB1_PTR_ADDR":
            f"lua/games/gen3_frlge.lua:{legacy_line} "
            "(the old client's address, kept for parity: it is a literal-pool constant inside "
            "IntrMain_Buffer, not the pointer -- the new client reads "
            "write_checkpoint.pointers.gSaveBlock1Ptr, which is ROM-derived)",
    }
    # P4 card C4-2a addendum 4/5: old-client-evidenced RR facts, every citation naming
    # archive/gen3-old-client:lua/memory_gba.lua (production-tested), never a pret path (RR has no pret source).
    rr_c4_2a_keys = {
        "SB1_LOCATION_MAP_GROUP_OFFSET", "SB1_LOCATION_MAP_NUM_OFFSET", "SB1_BADGE_BYTE_OFFSET",
        "BATTLE_TYPE_TRAINER_MASK", "BATTLE_TYPE_DOUBLE_MASK", "OUTCOME_WON", "OUTCOME_LOST",
        "OUTCOME_DREW", "BATTLE_MON_STAT_STAGES_OFF", "SB2_NAME_OFFSET",
    }
    for key in rr_c4_2a_keys:
        cite = title["_src"].get(f"derived.{key}")
        assert cite and "archive/gen3-old-client:lua/memory_gba.lua" in cite, f"radical_red: derived.{key} needs an old-client citation"
        base_src[f"derived.{key}"] = cite
    # SHEDINJA_SPECIES_ID comes from the RR species table, not the old client.
    shedinja_cite = title["_src"].get("derived.SHEDINJA_SPECIES_ID")
    assert shedinja_cite and "rr_species.json" in shedinja_cite
    base_src["derived.SHEDINJA_SPECIES_ID"] = shedinja_cite
    base_src["abilities"] = "RR 4.1 ROM routines pinned by RR_ABILITY_WITNESSES"
    for section, key in RR_BINARY_VALUES:
        cite = title["_src"].get(f"{section}.{key}")
        # the witnessed build is named by its canonical sha1 (version field zeroed), which a release stamp does not move
        assert cite and cite.startswith(f"rom:patch/build/slink_RR.gba canonical-sha1={rr_companion.canonical_sha1()} ")
        base_src[f"{section}.{key}"] = cite
    assert title["_src"] == base_src
    assert title["derived"]["SHEDINJA_SPECIES_ID"] == 303
    # values already agree with the pinned pret constants used for FR/LG (never RR-only guesses)
    assert title["derived"]["SB1_LOCATION_MAP_GROUP_OFFSET"] == 0x04
    assert title["derived"]["SB1_BADGE_BYTE_OFFSET"] == 0x104
    assert title["derived"]["OUTCOME_CAUGHT"] == 7 and title["derived"]["OUTCOME_RAN"] == 4
    # values this card could NOT find evidence for stay absent (RR-OPEN, reported to the owner).
    # SB2_NAME_OFFSET left this list at RR-DURABLE: the old client's M.readTrainerName reads
    # playerName generically at SB2+0 (archive/gen3-old-client:lua/memory_gba.lua:721-725),
    # the same owner-accepted old-client evidence as the keys above.
    for key in ("GMAIN_INBATTLE_OFFSET", "GMAIN_INBATTLE_MASK"):
        assert key not in title["derived"] and key not in title["ram"] and key not in title["rom"]


RR_BINARY_VALUES = {
    ("ram", "BATTLE_CONTROLLER_EXEC_FLAGS_ADDR"): 0x02023BC8,
    ("derived", "SB2_OT_ID_OFFSET"): 0xA,
    ("rom", "EXPERIENCE_TABLES_ADDR"): 0x0915514C,
    ("derived", "EXPERIENCE_TABLE_ENTRY_COUNT"): 256,
    ("derived", "MAX_LEVEL"): 250,
    ("rom", "BATTLE_MOVES_ADDR"): 0x091521D0,
    ("derived", "BATTLE_MOVE_ENTRY_SIZE"): 12,
    ("derived", "BATTLE_MOVE_PP_OFFSET"): 4,
    ("rom", "PP_UP_GET_MASK_ADDR"): 0x0825DEA1,
    ("derived", "BASESTATS_GROWTH_RATE_OFFSET"): 0x13,
    ("rom", "HANDLE_TURN_ACTION_SELECTION_ADDR"): 0x08014041,
    ("rom", "BEGIN_BATTLE_INTRO_ADDR"): 0x080123C1,
    ("rom", "BEGIN_BATTLE_INTRO_DUMMY_ADDR"): 0x080123BD,
    ("rom", "BATTLE_INTRO_GET_MONS_DATA_ADDR"): 0x08012FAD,
    # G4-PH: mechanism P on RR (rr_active_faint_parity_scope_2026-09-23.md §2, §5.1), each read
    # out of CFRU's Perish case / the HTAS absent path, and equal to FR/LG's pret values.
    ("ram", "STATUS3_ADDR"): 0x02023DFC,
    ("ram", "DISABLE_STRUCTS_ADDR"): 0x02023E0C,
    ("ram", "CHOSEN_ACTION_ADDR"): 0x02023D7C,
    ("derived", "STATUS3_PERISH_SONG"): 0x20,
    ("derived", "DISABLE_STRUCT_SIZE"): 0x1C,
    ("derived", "DISABLE_STRUCT_PERISH_TIMER_OFF"): 0x0F,
    ("derived", "B_ACTION_NOTHING_FAINTED"): 13,
    # G5-STAGES-COHERENCE: gBattleMons identity (CopyPlayerMonData, byte-identical to FireRed's)
    # and BATTLE_TYPE_LINK (InitBattleControllers), equal to FR/LG's pret values.
    ("derived", "BATTLE_MON_PERSONALITY_OFF"): 0x48,
    ("derived", "BATTLE_MON_OT_ID_OFF"): 0x54,
    ("derived", "BATTLE_TYPE_LINK_MASK"): 0x02,
}


def test_rr_rom_pins_are_in_the_generated_profile():
    from tools.gen_gen3_profile import rr_rom_facts

    facts = rr_rom_facts()
    title = _title("radical_red")
    for (section, key), expected in RR_BINARY_VALUES.items():
        assert title[section][key] == facts[section, key][0] == expected
    assert "HANDLE_TURN_ACTION_SELECTION_ADDR" in title["rom_thumb"]
    # C5-9: the intro window's values are Thumb pointers, marked so no consumer re-strips bit 0.
    for key in ("BEGIN_BATTLE_INTRO_ADDR", "BEGIN_BATTLE_INTRO_DUMMY_ADDR",
                "BATTLE_INTRO_GET_MONS_DATA_ADDR"):
        assert key in title["rom_thumb"], key
        assert title["rom"][key] & 1 == 1, key
    # Odd-address byte data must not accidentally become a Thumb function pin.
    assert "PP_UP_GET_MASK_ADDR" not in title["rom_thumb"]


@pytest.mark.parametrize("anchor", ["calculate_pp", "box_level", "trainer_id", "pp_up_masks",
                                    "action_callback_store", "action_callback_pool",
                                    "action_callback_entry", "controller_exec_marker",
                                    "controller_exec_reader", "controller_exec_reader_pool",
                                    "intro_store_controllers", "intro_store_begin",
                                    "intro_getmons_body", "perish_state34",
                                    "perish_state34_pool", "htas_absent_action",
                                    "htas_absent_action_pool", "turn_actions_nothing_fainted",
                                    "battlemon_personality_store", "battlemon_otid_store"])
def test_rr_rom_anchor_mutation_refuses_the_facts(anchor):
    from tools.gen_gen3_profile import RR_ROM_ANCHORS, rr_rom_facts

    end = max(off + len(bytes.fromhex(raw)) for off, raw in RR_ROM_ANCHORS.values())
    image = bytearray(end)
    for offset, raw in RR_ROM_ANCHORS.values():
        image[offset:offset + len(bytes.fromhex(raw))] = bytes.fromhex(raw)
    assert rr_rom_facts(bytes(image))
    image[RR_ROM_ANCHORS[anchor][0]] ^= 1
    with pytest.raises(ValueError, match=f"anchor mismatch: {anchor}"):
        rr_rom_facts(bytes(image))


@pytest.mark.parametrize("path,accepted", [
    # the companion: the exact published build or an earlier one its pin row lists as canonical-equal (patch/dist/companion_pins.json)
    (REPO / "patch/build/slink_RR.gba", rr_companion.accepted_sha1s()),
    (pathlib.Path("E:/Google Drive/SLink/Pokemon - Radical Red.gba"),
     {"964f951a0fdaf209e4ea1344883ef0d557bb3a80"}),
])
def test_rr_rom_anchors_match_both_admitted_binaries(path, accepted):
    from tools.gen_gen3_profile import RR_ROM_ANCHORS, rr_rom_facts

    if not path.exists():
        pytest.skip("local copyrighted RR ROM absent; embedded-anchor MODEL tests still run")
    raw = path.read_bytes()
    assert hashlib.sha1(raw).hexdigest() in accepted
    facts = rr_rom_facts(raw)
    for (section, key), value in RR_BINARY_VALUES.items():
        assert facts[section, key][0] == value
    for name, (offset, expected) in RR_ROM_ANCHORS.items():
        assert _title("radical_red")["_rom_anchors"][name] == {
            "rom_offset": offset, "expected_hex": expected.upper(),
        }
    # Independent direct literal readers corroborate the generator's Thumb LDR decoder.
    assert int.from_bytes(raw[0x4105C:0x41060], "little") == 0x091521D0
    assert int.from_bytes(raw[0x3E894:0x3E898], "little") == 0x0915514C
    assert raw[0x25DEA1:0x25DEA5] == bytes([3, 12, 48, 192])
    assert raw[0x11521D0 + 153 * 12 + 4] == 5  # Explosion PP, relocated table
    # G4-PH: the P pool words, read directly (not through the LDR decoder)
    assert int.from_bytes(raw[0x1092764:0x1092768], "little") == 0x02023DFC
    assert int.from_bytes(raw[0x1092770:0x1092774], "little") == 0x02023E0C
    assert int.from_bytes(raw[0x14164:0x14168], "little") == 0x02023D7C
    # G5-STAGES-COHERENCE: CopyPlayerMonData is FireRed's own code, so the struct it fills is
    # FireRed's BattlePokemon (personality +0x48, otId +0x54).
    fr = REPO / "patch/build/gen3_Pokemon_-_FireRed_Version_(USA).gba"
    if fr.exists():
        fr_raw = fr.read_bytes()
        if hashlib.sha1(fr_raw).hexdigest() == "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc":
            assert raw[0x30C04:0x313B0] == fr_raw[0x30C04:0x313B0]


def test_rr_p_fields_cite_the_cfru_perish_case_and_the_htas_absent_path():
    """§5.1: each P field's _src names the ROM site it was read from."""
    src = _title("radical_red")["_src"]
    for key, site in (("ram.STATUS3_ADDR", "LDR@0x090923CA"), ("ram.DISABLE_STRUCTS_ADDR", "LDR@0x09092402"),
                      ("derived.STATUS3_PERISH_SONG", "0x090923C8"),
                      ("derived.DISABLE_STRUCT_SIZE", "0x090923EE..0x090923FE"),
                      ("derived.DISABLE_STRUCT_PERISH_TIMER_OFF", "0x09092406"),
                      ("derived.B_ACTION_NOTHING_FAINTED", "0x08014132"),
                      ("ram.CHOSEN_ACTION_ADDR", "LDR@0x0801412E")):
        assert site in src[key], key
    assert "0x0825006C" in src["derived.B_ACTION_NOTHING_FAINTED"]


@pytest.mark.parametrize("name", ["firered", "leafgreen"])
def test_frlg_experience_dimensions_are_explicit(name):
    d = _title(name)["derived"]
    assert d["EXPERIENCE_TABLE_ENTRY_COUNT"] == 101
    assert d["MAX_LEVEL"] == 100


def test_rr_exec_flags_checkpoint_reads_the_new_profile_pin():
    from tools.gen_gen3_write_checkpoint import battle_block

    profile = _load("gen3_rr")
    block, dropped = battle_block("radical_red", {}, True, profile)
    clause = next(c for c in block["clauses"] if c["name"] == "battle_exec_flags_input")
    assert (clause["address"], clause["width"], clause["expect"]) == (0x02023BC8, 4, 1)
    # no ROMs passed: only the ROM-pool controller pin is unproven (C5-RR-BW)
    assert "rom:" in clause["source"] and [d.split(":")[0] for d in dropped] == ["battle_input_controller"]
    profile["titles"]["radical_red"]["ram"]["BATTLE_CONTROLLER_EXEC_FLAGS_ADDR"] = 123456
    block, _ = battle_block("radical_red", {}, True, profile)
    assert next(c for c in block["clauses"] if c["name"] == "battle_exec_flags_input")["address"] == 123456


def test_leafgreen_base_stats_address_comes_from_its_own_symbol():
    lines = (REPO / "data/gen3/pret/pokeleafgreen.sym").read_text().splitlines()
    address = int(next(line.split()[0] for line in lines if line.endswith(" gSpeciesInfo")), 16)
    assert _title("leafgreen")["rom"]["BASESTATS_ADDR"] == address == 0x08254760


@pytest.mark.parametrize("name,symbol,section,key", [
    ("firered", "gTrainerBattleOpponent_A", "ram", "TRAINER_OPPONENT_ADDR"),
    ("leafgreen", "gTrainerBattleOpponent_A", "ram", "TRAINER_OPPONENT_ADDR"),
    ("firered", "gExperienceTables", "rom", "EXPERIENCE_TABLES_ADDR"),
    ("leafgreen", "gExperienceTables", "rom", "EXPERIENCE_TABLES_ADDR"),
    ("firered", "gBattleMoves", "rom", "BATTLE_MOVES_ADDR"),
    ("leafgreen", "gBattleMoves", "rom", "BATTLE_MOVES_ADDR"),
    ("firered", "gPPUpGetMask", "rom", "PP_UP_GET_MASK_ADDR"),
    ("leafgreen", "gPPUpGetMask", "rom", "PP_UP_GET_MASK_ADDR"),
    *[(t, sym, "ram", key) for t in ("firered", "leafgreen") for sym, key in (
        ("gStatuses3", "STATUS3_ADDR"), ("gDisableStructs", "DISABLE_STRUCTS_ADDR"),
        ("gChosenActionByBattler", "CHOSEN_ACTION_ADDR"),
        ("gBattleCommunication", "BATTLE_COMM_ADDR"))],
])
def test_p4_c4_2a_sym_addresses_match_the_titles_own_sym_file(name, symbol, section, key) -> None:
    """P4 card C4-2a: independently re-derives each new address straight from the title's own
    .sym file (never trusts the generator's own regex)."""
    path = f"data/gen3/pret/poke{name}.sym"
    text = (REPO / path).read_text(encoding="utf-8")
    matches = re.findall(rf"^([0-9a-fA-F]{{8}})\s+g\s+[0-9a-fA-F]+\s+{symbol}$", text, re.M)
    assert len(matches) == 1, f"{path}: expected exactly one {symbol}"
    assert _title(name)[section][key] == int(matches[0], 16)


@pytest.mark.parametrize("name,symbol,key", [
    ("firered", "BeginBattleIntro", "BEGIN_BATTLE_INTRO_ADDR"),
    ("leafgreen", "BeginBattleIntro", "BEGIN_BATTLE_INTRO_ADDR"),
    ("firered", "BeginBattleIntroDummy", "BEGIN_BATTLE_INTRO_DUMMY_ADDR"),
    ("leafgreen", "BeginBattleIntroDummy", "BEGIN_BATTLE_INTRO_DUMMY_ADDR"),
    ("firered", "BattleIntroGetMonsData", "BATTLE_INTRO_GET_MONS_DATA_ADDR"),
    ("leafgreen", "BattleIntroGetMonsData", "BATTLE_INTRO_GET_MONS_DATA_ADDR"),
])
def test_c59_intro_values_come_from_the_titles_own_sym(name, symbol, key) -> None:
    """C5-9: independently re-derive each intro value from the title's .sym, in the Thumb form
    gBattleMainFunc holds (|1), accepting any scope letter (BattleIntroGetMonsData is local)."""
    path = f"data/gen3/pret/poke{name}.sym"
    text = (REPO / path).read_text(encoding="utf-8")
    matches = re.findall(rf"^([0-9a-fA-F]{{8}})\s+[glt]\s+[0-9a-fA-F]+\s+{symbol}$", text, re.M)
    assert len(matches) == 1, f"{path}: expected exactly one {symbol}"
    title = _title(name)
    assert title["rom"][key] == int(matches[0], 16) | 1
    assert "|1" in title["_src"][f"rom.{key}"]


def test_c59_rr_stores_the_intro_values_into_gBattleMainFunc() -> None:
    """C5-9: RR's evidence is the *store*, not the pool. Each captured site is a Thumb LDR pair
    (gBattleMainFunc pointer, then the value) followed by STR r0,[r1], so the pool word alone
    cannot make a fact; the write_checkpoint pack's HANDLE_TURN_ACTION_SELECTION pin uses the
    same shape (0x1070626)."""
    from tools.gen_gen3_profile import RR_ROM_ANCHORS

    def window(name: str) -> bytes:
        off, raw = RR_ROM_ANCHORS[name]
        return bytes.fromhex(raw)

    controllers, begin, getmons = window("intro_store_controllers"), window("intro_store_begin"), \
        window("intro_getmons_body")
    mbf = b"\x84\x4f\x00\x03"                                    # 0x03004F84, gBattleMainFunc
    for blob, base, ldr_ptr, ldr_val, store in (
            (controllers, 0xD27C, 0xD27E, 0xD280, 0xD282),
            (controllers, 0xD27C, 0xD374, 0xD376, 0xD378),
            (begin, 0x123C0, 0x123CC, 0x123CE, 0x123D0)):
        at = blob[ldr_ptr - base:ldr_val - base + 2]
        assert at[1] == 0x49 and at[3] == 0x48, (hex(ldr_ptr), at.hex())
        assert blob[store - base:store - base + 2] == b"\x08\x60"       # str r0,[r1]
        # both LDR immediates must resolve to the gBattleMainFunc pool / the value pool
        for ldr, want in ((ldr_ptr, 0x03004F84), (ldr_val, _title("radical_red")["rom"][
                {0xD280: "BEGIN_BATTLE_INTRO_DUMMY_ADDR", 0xD376: "BEGIN_BATTLE_INTRO_ADDR",
                 0x123CE: "BATTLE_INTRO_GET_MONS_DATA_ADDR"}[ldr_val]])):
            ins = int.from_bytes(blob[ldr - base:ldr - base + 2], "little")
            pool = ((ldr + 4) & ~3) + (ins & 0xFF) * 4
            assert blob[pool - base:pool - base + 4] == want.to_bytes(4, "little"), hex(ldr)
    # the data-request body still reads gBattleCommunication (pool 0x02023E82) and gBattleMainFunc
    assert getmons.count(mbf) == 1
    assert getmons.count(b"\x82\x3e\x02\x02") == 1


@pytest.mark.parametrize("name,addr,size", [
    ("BeginBattleIntro", 0x080123C0, 36),
    ("BeginBattleIntroDummy", 0x080123BC, 2),
    ("BattleIntroGetMonsData", 0x08012FAC, 116),
])
def test_c59_rr_bodies_are_byte_identical_to_fr(name, addr, size) -> None:
    """C5-9: the strongest RR evidence for the window is that CFRU did not rewrite these three
    bodies -- so the RR addresses hold the same code the FR .sym names, and the per-battler
    request index is gBattleCommunication[1] on RR too. (BattleIntroDrawTrainersOrMonsSprites is
    deliberately NOT in this set: it differs in 26 bytes, the CFRU type/ability branch.)"""
    rr_path = REPO / "patch/build/slink_RR.gba"
    fr_path = REPO / "patch/build/gen3_Pokemon_-_FireRed_Version_(USA).gba"
    if not (rr_path.exists() and fr_path.exists()):
        pytest.skip("local copyrighted ROMs absent; the embedded anchors still pin the bytes")
    import hashlib
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    import gen3_final_cut
    pins = gen3_final_cut.rom_pins(str(REPO))              # absent skips; present-but-wrong fails
    for path, key in ((rr_path, "radical_red_companion"), (fr_path, "firered")):
        got = hashlib.sha1(path.read_bytes()).hexdigest()
        assert got == pins[key], f"{path} is present but not the pinned {key} ({got[:8]} != {pins[key][:8]})"
    rr, fr = rr_path.read_bytes(), fr_path.read_bytes()
    off = addr - 0x08000000
    assert rr[off:off + size] == fr[off:off + size], f"{name} differs between RR and FR"


def test_p4_c4_2a_engine_constants_match_the_pinned_pret_header() -> None:
    """The battle/flags/pokemon constants this card added, re-derived from the pinned pret
    header independent of the generator's own copies."""
    _pin_dir = pathlib.Path("E:/Google Drive/SLink/.cache/pret/pokefirered")
    pin_dir = _pin_dir if _pin_dir.exists() else None
    if pin_dir is None:
        pytest.skip("no local pret checkout to re-derive against (values are still pinned in "
                    "tools/gen_gen3_profile.py:FRLG_DERIVED with a file:line citation each)")
    battle_h = (pin_dir / "include" / "constants" / "battle.h").read_text(encoding="utf-8")
    flags_h = (pin_dir / "include" / "constants" / "flags.h").read_text(encoding="utf-8")
    pokemon_h = (pin_dir / "include" / "pokemon.h").read_text(encoding="utf-8")
    for name in ("firered", "leafgreen"):
        derived = _title(name)["derived"]
        for const, key in (("B_OUTCOME_WON", "OUTCOME_WON"), ("B_OUTCOME_LOST", "OUTCOME_LOST"),
                           ("B_OUTCOME_DREW", "OUTCOME_DREW"), ("B_OUTCOME_RAN", "OUTCOME_RAN"),
                           ("B_OUTCOME_CAUGHT", "OUTCOME_CAUGHT"),
                           ("BATTLE_TYPE_TRAINER", "BATTLE_TYPE_TRAINER_MASK"),
                           ("BATTLE_TYPE_DOUBLE", "BATTLE_TYPE_DOUBLE_MASK")):
            m = re.search(rf"^#define {const}\s+\(?(0x[0-9A-Fa-f]+|\d+)(\s*<<\s*(\d+))?", battle_h, re.M)
            assert m, const
            want = _num(m[1]) << int(m[3]) if m[3] else _num(m[1])
            assert derived[key] == want
        assert derived["SB1_BADGE_BYTE_OFFSET"] == 0x104
        # SYS_FLAGS itself is a chained macro (TRAINER_FLAGS_END + 1); its pinned value 0x800
        # is cross-checked by tests/unit/test_gen3_reads.py's live badge-byte reads instead of
        # re-expanded here. This only re-derives the FLAG_BADGE01_GET offset from it.
        assert re.search(r"^#define SYS_FLAGS \(TRAINER_FLAGS_END \+ 1\)", flags_h, re.M)
        badge01 = _num(re.search(r"^#define FLAG_BADGE01_GET\s+\(SYS_FLAGS \+ (0x[0-9A-Fa-f]+)\)",
                                  flags_h, re.M)[1])
        assert (0x800 + badge01) >> 3 == derived["SB1_BADGE_BYTE_OFFSET"]
        # C4-ACTIVE-FAINT-P: the mechanism-P constants and the DisableStruct geometry
        battle_struct_h = (pin_dir / "include" / "battle.h").read_text(encoding="utf-8")
        assert derived["STATUS3_PERISH_SONG"] == 1 << int(re.search(
            r"^#define STATUS3_PERISH_SONG\s+\(1 << (\d+)\)", battle_h, re.M)[1])
        assert derived["B_ACTION_NOTHING_FAINTED"] == int(re.search(
            r"^#define B_ACTION_NOTHING_FAINTED\s+(\d+)", battle_struct_h, re.M)[1])
        assert re.search(r"/\*0x0F\*/ u8 perishSongTimer : 4;", battle_struct_h)
        assert derived["DISABLE_STRUCT_PERISH_TIMER_OFF"] == 0x0F
        assert re.search(r"/\*0x1A\*/ u8 unk1A\[2\];\s*};", battle_struct_h)   # ends at 0x1C
        assert derived["DISABLE_STRUCT_SIZE"] == 0x1C
        sym = (REPO / f"data/gen3/pret/poke{name}.sym").read_text(encoding="utf-8")
        size = re.search(r"^[0-9a-f]{8}\s+g\s+([0-9a-f]{8})\s+gDisableStructs$", sym, re.M)[1]
        assert int(size, 16) == 4 * derived["DISABLE_STRUCT_SIZE"]           # MAX_BATTLERS_COUNT
        growth_off = re.search(r"/\* 0x13 \*/ u8 growthRate;", pokemon_h)
        assert growth_off, "growthRate moved in pokemon.h"
        assert derived["BASESTATS_GROWTH_RATE_OFFSET"] == 0x13


@pytest.mark.parametrize("name", ["firered_ap"])
def test_pret_additions_do_not_admit_unverified_titles(name: str) -> None:
    title = _title(name)
    assert "_src" not in title
    assert "BOX_DATA_OFFSET" not in title["derived"]
    assert "PARTY_CAPACITY" not in title["derived"]


def test_thumb_addresses_are_kept_verbatim_and_marked() -> None:
    rr = _title("radical_red")
    assert rr["rom"]["CB2_EVOLUTION_LOAD_ADDR"] == 0x080CE0E9  # odd: the Thumb bit stays
    assert "CB2_EVOLUTION_LOAD_ADDR" in rr["rom_thumb"]
    assert "BASESTATS_ENTRY_SIZE" not in rr["rom_thumb"]
    for name in TITLE_TABLE:
        title = _title(name)
        for key in title["rom_thumb"]:
            vals = title["rom"][key]
            for val in (vals if isinstance(vals, list) else [vals]):
                assert val & 1, f"{name}: {key} is in rom_thumb but is even"


def test_c511a_the_rival_opcode_is_in_the_native_block() -> None:
    """C5-11a: the profile's native opcode keys are generated from patch/src/handlers.c (C5-6:
    the old archive/gen3-old-client:lua/mailbox.lua scrape target is deleted), so the new rival opcode must appear there
    with its ABI number and its own source citation -- the Lua side (native.lua's
    replace_rival_team) reads it from this block, and handlers.c owns the number."""
    title = _title("radical_red")
    native = _load("gen3_rr")["native"]
    assert native["OP_RIVAL_SWAP"] == 28
    assert native["OP_SET_ENEMY_PARTY"] == 16, "opcode 16 keeps its number (refused on the durable build)"
    assert "patch/src/handlers.c" in native["_src"]["OP_RIVAL_SWAP"]
    assert title["rom"]["BATTLE_INTRO_GET_MONS_DATA_ADDR"] == 0x08012FAD


def test_pinned_rom_hashes() -> None:
    assert _title("firered")["rom_sha1"] == "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc"
    assert _title("leafgreen")["rom_sha1"] == "574fa542ffebb14be69902d1d36f1ec0a4afd71e"
    rr = _title("radical_red")
    assert rr["rom_sha1"] == "964f951a0fdaf209e4ea1344883ef0d557bb3a80"
    assert rr["rom_md5"] == "8529f3a45d32bce4da637976fcf269d4"
    # LeafGreen reads the vanilla table today; divergence is UNVERIFIED (P2 card C2-3)
    assert _title("leafgreen")["ram"] == _title("firered")["ram"]


def test_admission_flags_and_source_block() -> None:
    frlg = _load("gen3_frlg")
    assert [t for t, v in frlg["titles"].items() if v["admitted"]] == ["firered", "leafgreen"]
    assert frlg["titles"]["firered_ap"]["admitted"] is False
    assert "emerald" not in frlg["titles"]
    assert _load("gen3_rr")["titles"]["radical_red"]["admitted"] is True
    for pack, data in ((p, _load(p)) for p in PROFILES):
        assert data["source"]["file"] == "lua/games/gen3_frlge.lua"
        assert re.fullmatch(r"[0-9a-f]{40}|unknown", data["source"]["git_head"]), pack


# ── the native (companion) block ─────────────────────────────────────────────────
def test_native_bindings_keep_rr_abi1_and_publish_vanilla_abi2() -> None:
    assert _load("gen3_frlg")["native"]["ABI"]==2
    assert json.loads((REPO/"data/games/gen3_emerald/profile.json").read_text())["native"]["ABI"]==2
    assert _load("gen3_rr")["native"]["ABI"]==1


def test_native_matches_the_mailbox_and_ghost_sources() -> None:
    """C5-6 deleted archive/gen3-old-client:lua/mailbox.lua and archive/gen3-old-client:lua/peer_ghost_npc.lua; the native ABI block is now
    generated from patch/src/handlers.c (the companion is BUILT from it, so it is the actual
    authority). Every value is pinned here, independent of the generator's own extraction
    tables, since the ABI is frozen at v1 (ADDRESSES.md: "ABI version stays 1 ... the mailbox
    layout is unchanged") -- a regression here means the frozen ABI silently moved."""
    native = dict(_load("gen3_rr")["native"])
    src = native.pop("_src")
    want = {"ABI": 1, "BASE": 0x0203F800, "BATTLE_NOTIF": 0x0203FD00,
        "BLOB_BUF": 0x0203FA00, "CALC_OFF": 0x0203F8D8, "CAMERA_Y_ADDR": 0x02021BCA,
        "CB2_OVERWORLD": 0x080565B5, "EVR": 0x0203FD10, "EVR_PRIM": 0x0203FD16,
        "EV_EVOLVE": 5, "EV_FOE_FAINT": 2, "EV_OUTCOME": 3, "EV_PARTY_ADD": 4,
        "EV_PLAYER_FAINT": 1, "GH": 0x0203F850, "GHOST_PAL_BUF": 0x0203FC60,
        "GMAIN_CB2_PTR": 0x030030F4, "GPLAYER_AVATAR": 0x02037078, "INFO": 0x0203FD44,
        "INFO_BAR_W": 38, "INFO_LINEW": 32, "INFO_MAXLINES": 6, "INFO_PAGESLOT": 7,
        "LOCALID": 0xF0, "MENU_BUF": 0x0203FC90, "OBJECT_EVENTS_BASE": 0x02036E38,
        "OBJ_PALETTE_BUF": 0x020373F8, "OP_ARM_PEER_INTERACT": 13,
        "OP_CHOOSE_PARTY_MON": 20, "OP_CREATE_MON": 4, "OP_DEPOSIT_MON": 24,
        "OP_DESPAWN_PEER_NPC": 7, "OP_FORCE_FAINT": 2, "OP_FORCE_MOVE": 3,
        "OP_FORCE_MOVE_SLOT": 5, "OP_GHOST_CLEAR": 15, "OP_GHOST_SPAWN": 14,
        "OP_MEMORIALIZE": 26, "OP_PING": 1, "OP_PLAY_FANFARE": 9, "OP_PLAY_SE": 19,
        "OP_RIVAL_SWAP": 28, "OP_SET_ENEMY_PARTY": 16, "OP_SET_PARTY_MON": 18,
        "OP_SHOW_BATTLE_MESSAGE": 23, "OP_SHOW_CHOICES": 22, "OP_SHOW_INFO": 27,
        "OP_SHOW_MENU": 17, "OP_SHOW_MESSAGE": 8, "OP_SPAWN_PEER_NPC": 6,
        "OP_TRADE_SCENE": 21, "OP_WITHDRAW_MON": 25, "PI_COUNT": 0x0203F8D3,
        "SIG": 0x4B4E4C53, "SPRITES_BASE": 0x0202063C, "SW": 0x0203F840,
        "TEXT_BUF": 0x0203F900, "TN_ENABLE": 0x0203F8D4, "TRADE_BASE": 0x0203FE50,
        "OP_TRADE_PREPARE": 29, "OP_TRADE_WITHDRAW": 30, "OP_TRADE_STATUS": 31,
    }
    assert native == want
    # the ABI anchors the card names, spelled out so a silent regex drift is caught
    assert native["BASE"] == 0x0203F800 and native["BLOB_BUF"] == 0x0203FA00
    assert native["TEXT_BUF"] == 0x0203F900 and native["MENU_BUF"] == 0x0203FC90
    assert native["BATTLE_NOTIF"] == 0x0203FD00 and native["EVR"] == 0x0203FD10
    assert native["PI_COUNT"] == 0x0203F8D3 and native["TN_ENABLE"] == 0x0203F8D4
    assert native["CALC_OFF"] == 0x0203F8D8 and native["INFO"] == 0x0203FD44
    assert native["GH"] == 0x0203F850 and native["SW"] == 0x0203F840
    assert native["OBJECT_EVENTS_BASE"] == 0x02036E38
    assert set(src) == set(native)
    handlers = (REPO / "patch" / "src" / "handlers.c").read_text(encoding="utf-8").splitlines()
    for name, where in src.items():
        assert where.startswith("patch/src/handlers.c:"), f"{name}: {where} not from handlers.c"
        line = int(where.split(":", 1)[1].split(" ", 1)[0])
        text = handlers[line - 1]
        assert re.search(r"0x[0-9A-Fa-f]+|\d", text), f"{name}: {where} has no literal"


# ── the P2 exit condition + file hygiene ─────────────────────────────────────────
def test_check_mode_passes_on_the_committed_profiles() -> None:
    run = subprocess.run([sys.executable, "tools/gen_gen3_profile.py", "--check"],
                         cwd=REPO, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("pack", sorted(PROFILES))
def test_json_is_deterministic(pack: str) -> None:
    raw = PROFILES[pack].read_bytes()
    assert b"\r\n" not in raw, f"{pack}: CRLF line endings"
    text = raw.decode("utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


def test_rr_durable_trade_block_sits_in_the_proven_free_tail() -> None:
    """RR-DURABLE: native.TRADE_BASE is handlers.c's RT_BASE. The shadow mailbox, witness,
    producer state and pre-save flag (0x111 bytes, the C static assertions) start after
    SlinkInfo's 264 bytes and end before the EWRAM end: inside the run test_live_ewramtail paints."""
    native = _load("gen3_rr")["native"]
    base = native["TRADE_BASE"]
    assert "RT_BASE" in native["_src"]["TRADE_BASE"]
    assert base % 4 == 0 and base >= native["INFO"] + 264
    assert base + 0x111 <= 0x02040000
    handlers = (REPO / "patch" / "src" / "handlers.c").read_text(encoding="utf-8")
    assert "_Static_assert(RT_BASE + 0x111u <= 0x02040000u" in handlers
    assert "_Static_assert(0xA0u + sizeof(SlinkTradeProducer) <= 0x110u" in handlers


def test_rr_names_its_trainer_from_the_production_tested_generic_read() -> None:
    """RR-DURABLE: the trade journal binds to the trainer (reads.read_trainer needs
    SB2_NAME_OFFSET). The old client's M.readTrainerName read playerName at SB2+0 on RR."""
    title = _title("radical_red")
    assert title["derived"]["SB2_NAME_OFFSET"] == 0
    assert "readTrainerName" in title["_src"]["derived.SB2_NAME_OFFSET"]
