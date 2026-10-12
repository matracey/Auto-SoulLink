"""R4 (docs/gen2/RANDOMIZER.md): a randomized companion cartridge answers from its own bytes.

Server: server/adapters/gen2_gsc.scan_randomized reads the wild, tree, fishing, roamer and contest tables plus the
species at every static/gift pack row's own script command; the player's adapter adopts them (use_rom_encounters),
and a randomized adapter never answers with the vanilla tables. server.py _contracted_rom hands the adapter the
Manager's file for that player. Lua: lua/gen2/signals.lua on rand_overlay reads the species (and level/item) at the
same sites, so a randomized static/gift/roamer links at its site, a wrong site is still refused, and clean/overlay
are unchanged.

ROM-backed tests need the pinned pret builds under .cache/gen2-build/ (absent skips, a wrong sha1 fails). The "UPR"
cartridge is the admitted Crystal overlay with the bytes one real pipeline run wrote at the decoded sites
(cartridges.provision, Crystal seed 43691697051008, player a), whose UPR log names the expectations below.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from server.adapters.gen2_gsc import Gen2GSCAdapter, scan_randomized
from server.adapters.gen2_rom_scan import RomScanError
from tests.unit.test_gen2_signals import World
from tests.unit.test_upr_gen2_pipeline import REPO, _need, roms  # noqa: F401 -- roms is a fixture

TITLES = ("crystal", "gold", "silver")

# Every byte that run changed inside the decoded sites: InitRoamMons, Route 29's grass record, the static/gift
# commands, ContestMons. Its log: starters Rattata/Togepi/Clefairy, Raikou => Cyndaquil, Entei => Lugia,
# Lapras => Krabby, Eevee => Koffing, Route 29 Machoke/Golem/Drowzee/Psyduck/Cleffa/Donphan/Elekid.
UPR_CRYSTAL_A = {
    0x02A2A1: 0x9B, 0x02A2A6: 0xF9, 0x02AE03: 0x43, 0x02AE05: 0x4C, 0x02AE07: 0x60, 0x02AE09: 0x36, 0x02AE0B: 0xAD,
    0x02AE0D: 0xE8, 0x02AE0F: 0xEF, 0x02AE11: 0x43, 0x02AE13: 0x4C, 0x02AE15: 0x60, 0x02AE17: 0x36, 0x02AE19: 0xAD,
    0x02AE1B: 0xE8, 0x02AE1D: 0xEF, 0x02AE1F: 0x43, 0x02AE21: 0x4C, 0x02AE23: 0x60, 0x02AE25: 0x36, 0x02AE27: 0xAD,
    0x02AE29: 0xE8, 0x02AE2B: 0xEF, 0x054C06: 0x6D, 0x056D4A: 0x73, 0x056D78: 0x42, 0x056DA6: 0x95, 0x05A324: 0x62,
    0x0694E2: 0xC3, 0x069D65: 0x78, 0x06CA43: 0x58, 0x06CA56: 0xCB, 0x06CA69: 0xE4, 0x06D105: 0x56, 0x06D130: 0x10,
    0x06D15B: 0xD0, 0x07006E: 0x8A, 0x072811: 0x2D, 0x07283F: 0xBB, 0x07286D: 0xB1, 0x077256: 0x0C, 0x078CA3: 0x13,
    0x078CE5: 0xAF, 0x078D21: 0x23, 0x07E22A: 0xCC, 0x097D88: 0x4C, 0x097D8C: 0xD1, 0x097D90: 0x62, 0x097D94: 0xB5,
    0x097D98: 0x82, 0x097D9C: 0xE9, 0x097DA0: 0x42, 0x097DA4: 0xCA, 0x097DA8: 0x07, 0x097DAC: 0xA7, 0x1850EA: 0xED,
    0x18C52A: 0xFA, 0x18D1D7: 0x47, 0x194068: 0x41, 0x1A0F90: 0x53, 0x1A0FC6: 0x53, 0x1A100E: 0x53, 0x1AA9B8: 0x79,
}


def _pack(title, name):
    return json.loads((REPO / f"data/games/gen2_{title}/{name}.json").read_text(encoding="utf-8"))


def _patched(rom: bytes, changes: dict[int, int]) -> bytes:
    out = bytearray(rom)
    for offset, value in changes.items():
        out[offset] = value
    return bytes(out)


def _upr_crystal(roms):  # noqa: F811
    return _patched(_need(roms, "crystal")[1], UPR_CRYSTAL_A)


def _row(rows, prefix):
    return next(row for row in rows if row["id"].startswith(prefix))


# ── server decoders ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("title", TITLES)
def test_the_unrandomized_overlay_decodes_to_the_vanilla_pack(roms, title):  # noqa: F811
    """A decoder offset or record-shape bug would read the overlay differently from the generated pack."""
    scan = scan_randomized(_need(roms, title)[1], title)
    vanilla = _pack(title, "encounter_tables")
    for name in ("wild", "tree", "fishing", "roamers"):
        assert scan[name] == vanilla[name], name
    assert scan["contest"] == {key: vanilla["contest"][key] for key in ("slots", "fallback")}
    for name, key, fields in (("static_encounters", "encounters", ("species", "level")),
                              ("gifts", "gifts", ("species", "level"))):
        decoded = {mon["id"]: mon for mon in scan["statics" if key == "encounters" else "gifts"]}
        for row in _pack(title, name)[key]:
            assert all(decoded[row["id"]][field] == row[field] for field in fields), row["id"]
            if row.get("operation") == "givepoke":
                assert decoded[row["id"]]["item"] == row["item"], row["id"]


def test_a_real_upr_cartridge_decodes_to_its_log(roms):  # noqa: F811
    scan = scan_randomized(_upr_crystal(roms), "crystal")
    gifts = {mon["id"]: mon for mon in scan["gifts"]}
    statics = {mon["id"]: mon for mon in scan["statics"]}
    assert [gifts[row["id"]]["species"] for row in _pack("crystal", "gifts")["gifts"]
            if row["id"].startswith("ElmsLab:")] == [19, 175, 35]               # Rattata, Togepi, Clefairy
    assert [row["species"] for row in scan["roamers"]["initial"]] == [155, 249]   # Cyndaquil, Lugia
    assert statics["UnionCaveB2F:UnionCaveLapras:31"] == {"id": "UnionCaveB2F:UnionCaveLapras:31",
                                                          "species": 98, "level": 20}   # Krabby, level kept
    assert gifts["BillsFamilysHouse:BillScript:27"]["species"] == 109              # Koffing
    assert statics["IlexForest:IlexForestShrineScript.CelebiEvent:466"]["species"] == 251   # UPR leaves Celebi
    adapter = Gen2GSCAdapter("crystal", artifact_kind="rand_overlay")
    adapter.use_rom_encounters(scan)
    assert [slot["name"] for slot in adapter.encounter_table("route_29")["Day"]] == [
        "Machoke", "Golem", "Drowzee", "Psyduck", "Cleffa", "Donphan", "Elekid"]


@pytest.mark.parametrize("offset", [0, 4])   # the givepoke opcode, then its trainer byte (past the arguments)
def test_a_moved_or_rewritten_script_command_refuses(roms, offset):  # noqa: F811
    bill = _row(_pack("crystal", "gifts")["gifts"], "BillsFamilysHouse:")["rom"]["flat"]
    rom = _upr_crystal(roms)
    with pytest.raises(RomScanError, match="BillScript"):
        scan_randomized(_patched(rom, {bill + offset: rom[bill + offset] ^ 1}), "crystal")


def test_the_cartridge_header_must_name_the_title(roms):  # noqa: F811
    with pytest.raises(ValueError, match="header"):
        scan_randomized(_upr_crystal(roms), "gold")


# ── the adapter ───────────────────────────────────────────────────────────────────────────
def test_a_randomized_adapter_answers_only_from_its_own_cartridge(roms):  # noqa: F811
    rom = _upr_crystal(roms)
    clean = Gen2GSCAdapter("crystal")
    assert clean.encounter_table("route_29") is not None          # clean and overlay keep the shipped tables
    adapter = Gen2GSCAdapter("crystal", artifact_kind="rand_overlay")
    assert adapter.encounter_table("route_29") is None            # never the vanilla tables beside it
    tables = adapter.ingest_rom_content({"rom": rom, "rom_type": "Crystal", "rom_sha1": hashlib.sha1(rom).hexdigest()})
    adapter.use_rom_encounters(tables)
    assert adapter.encounter_table("route_29") != clean.encounter_table("route_29")
    assert adapter.encounter_table("national_park_contest")["Contest"][0]["species_id"] == 76   # Golem
    gold = Gen2GSCAdapter("gold", artifact_kind="rand_overlay")
    with pytest.raises(ValueError, match="another title"):
        gold.use_rom_encounters(tables)
    unreadable = Gen2GSCAdapter("crystal", artifact_kind="rand_overlay")
    unreadable.use_rom_encounters({})
    assert unreadable.encounter_table("route_29") is None


def test_a_randomized_roamers_area_is_named_for_the_species_its_cartridge_holds(roms):  # noqa: F811
    """Review cx-db933785 F1: the legend area id stays the vanilla slot's id; its display name is the ROM's species."""
    adapter = Gen2GSCAdapter("crystal", artifact_kind="rand_overlay")
    adapter.use_rom_encounters(scan_randomized(_upr_crystal(roms), "crystal"))
    assert adapter.area_display_name("legend_243") == "Cyndaquil"      # Raikou's slot, UPR log: Raikou => Cyndaquil
    assert adapter.area_display_name("legend_244") == "Lugia"          # Entei's slot
    assert Gen2GSCAdapter("crystal").area_display_name("legend_243") == "Raikou"   # clean/overlay unchanged
    unreadable = Gen2GSCAdapter("crystal", artifact_kind="rand_overlay")
    unreadable.use_rom_encounters({})
    assert unreadable.area_display_name("legend_243") == "Raikou"      # no tables: nothing to remap


@pytest.mark.parametrize("fault", ["client_json", "sha1", "rom_type"])
def test_ingest_takes_only_the_servers_contract_checked_bytes(fault):
    rom = b"\x00" * 0x200
    payload = {"rom": rom, "rom_type": "crystal", "rom_sha1": hashlib.sha1(rom).hexdigest()}
    if fault == "client_json":
        payload["rom"] = rom.hex()          # a JSON hello can carry text, never bytes
    elif fault == "sha1":
        payload["rom_sha1"] = "0" * 40
    else:
        payload["rom_type"] = "crystal_ap"
    with pytest.raises(ValueError):
        Gen2GSCAdapter("crystal", artifact_kind="rand_overlay").ingest_rom_content(payload)


def test_the_server_reads_the_contracted_file_for_a_sha1_bound_randomized_hello(tmp_path):
    from server.server import SLinkServer
    server = SLinkServer.__new__(SLinkServer)
    server.adapter, server._data_dir = Gen2GSCAdapter("crystal"), str(tmp_path)
    server._rom_contract = {"players": {"a": {"rom_sha1": "ab" * 20}}}
    (tmp_path / "roms").mkdir()
    (tmp_path / "roms" / "a.gbc").write_bytes(b"cart")
    hello = {"artifact_kind": "rand_overlay", "rom_type": "Crystal"}
    assert server._contracted_rom("a", hello) == {"rom": b"cart", "rom_type": "Crystal", "rom_sha1": "ab" * 20}
    assert server._contracted_rom("b", hello)["rom"] is None      # no file: ingest fails, no tables shown
    assert server._contracted_rom("a", {**hello, "artifact_kind": "overlay"}) is None
    server.adapter = object()                                      # a foundation not bound by sha1
    assert server._contracted_rom("a", hello) is None


# ── Lua: lua/gen2/signals.lua reads the species at the same site on rand_overlay ─────────────
def _stamp(world, row, *values):
    """Write the row's anchored command into the model ROM with its argument bytes replaced by ``values``."""
    data = bytearray.fromhex(row["rom"]["expected_hex"])
    data[1:1 + len(values)] = bytes(values)
    for offset, value in enumerate(data):
        world.rom[row["rom"]["flat"] + offset] = value


def _binder(world, kind):
    options = world.options()
    options.statics = world.lua.table_from(world.read_pack("static_encounters"), recursive=True)
    options.gifts = world.lua.table_from(world.read_pack("gifts"), recursive=True)
    if kind:
        options.artifact_kind = kind
    result = world.module.new_model(options)
    binder = result[0] if isinstance(result, tuple) else result
    assert binder is not None, result
    return binder


def _captures(world, binder):
    return [(e.acquisition, e.area_id, e.mon.species_id) for e in world.events(binder) if e.kind == "capture"]


LAPRAS = "UnionCaveB2F:UnionCaveLapras:31"   # crystal map 3:39, NORMAL battle type, static_union_cave_b2f_131


@pytest.mark.parametrize("kind,species,number,published", [
    ("rand_overlay", 98, 39, True),     # the ROM's species at the Lapras site
    ("rand_overlay", 131, 39, False),   # the vanilla species no longer qualifies there
    ("rand_overlay", 98, 40, False),    # the ROM's species on another map: wrong site
    (None, 131, 39, True), ("overlay", 131, 39, True),       # clean/overlay: the pack, unchanged
    (None, 98, 39, False), ("overlay", 98, 39, False),       # ...whatever the ROM bytes say
])
def test_a_randomized_static_links_at_its_own_site_only(kind, species, number, published):
    world = World()
    _stamp(world, _row(world.read_pack("static_encounters")["encounters"], LAPRAS), 98, 20)
    binder = _binder(world, kind)
    world.field("wBattleScriptFlags", 128)
    world.field("wBattleType", 0)
    world.field("wMapGroup", 3)
    world.field("wMapNumber", number)
    world.party([world.mon(), world.mon(species=species, dvs=0x7AAA)])
    world.fire("capture_party")
    world.events(binder)
    world.fire("capture_party_finalized")
    assert _captures(world, binder) == ([("static", "static_union_cave_b2f_131", species)] if published else [])


@pytest.mark.parametrize("kind,species,store,area", [
    ("rand_overlay", 155, True, "legend_243"),   # slot 1 (Raikou's) now Cyndaquil: Raikou's area
    ("rand_overlay", 249, True, "legend_244"),   # slot 2 (Entei's) now Lugia
    ("rand_overlay", 243, True, None),           # vanilla Raikou is no roamer on this cart
    ("rand_overlay", 155, False, None),          # not the wRoamMon1Species store: refused
    (None, 243, True, "legend_243"), ("overlay", 243, True, "legend_243"),
    (None, 155, True, None),
])
def test_a_randomized_roamer_keeps_its_slots_area(kind, species, store, area):
    world = World()
    init = world.p["rom"]["InitRoamMons"]["flat"]
    for index, mon in enumerate((155, 249)):
        ram = world.p["ram"][f"wRoamMon{index + 1}Species"] + (0 if store else 1)
        for offset, value in enumerate((0x3E, mon, 0xEA, ram & 255, ram >> 8)):
            world.rom[init + 5 * index + offset] = value
    binder = _binder(world, kind)
    world.party([world.mon(species=species)])
    world.field("wCurPartySpecies", species)
    world.field("wBattleType", 5)
    world.fire("capture_party")
    world.events(binder)
    world.fire("capture_party_finalized")
    assert _captures(world, binder) == ([("roamer", area, species)] if area else [])


BILL = "BillsFamilysHouse:BillScript:27"   # crystal 11:6, givepoke at 21:4C05


@pytest.mark.parametrize("kind,species,level,pos,opcode,published", [
    ("rand_overlay", 109, 23, 5, 0x2D, True),    # the ROM's species and (static level curve) level
    ("rand_overlay", 133, 20, 5, 0x2D, False),   # the vanilla gift no longer qualifies
    ("rand_overlay", 109, 20, 5, 0x2D, False),   # the vanilla level either
    ("rand_overlay", 109, 23, 4, 0x2D, False),   # not one command past the givepoke: wrong site
    ("rand_overlay", 109, 23, 5, 0x2E, False),   # the site no longer holds a givepoke: unselected
    (None, 133, 20, 5, 0x2D, True), ("overlay", 133, 20, 5, 0x2D, True),
    (None, 109, 23, 5, 0x2D, False),
])
def test_a_randomized_gift_links_at_its_own_site_only(kind, species, level, pos, opcode, published):
    world = World()
    row = _row(world.read_pack("gifts")["gifts"], BILL)
    _stamp(world, row, 109, 23)
    world.rom[row["rom"]["flat"]] = opcode
    binder = _binder(world, kind)
    world.field("wMapGroup", 11)
    world.field("wMapNumber", 6)
    world.field("wScriptBank", 21)
    world.field("wScriptPos", row["rom"]["addr"] + pos, 2)
    player = world.p["ram"]["wPlayerID"]
    world.memory["System Bus", player], world.memory["System Bus", player + 1] = 0x12, 0x34
    world.fire("gift_begin")
    world.party([world.mon(), world.mon(species=species, dvs=0x1357)])
    world.memory["System Bus", world.p["ram"]["wPartyCount"] + 8 + 48 + 31] = level   # slot 1's MON_LEVEL
    world.field("wCurPartyMon", 1)
    world.set_guards("gift_party_finalized")
    world.fire("gift_party_finalized")
    assert _captures(world, binder) == ([("gift", "goldenrod_city", species)] if published else [])
