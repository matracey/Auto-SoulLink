"""Read-only ability resolution over synthetic ROM/RAM; no emulator process."""
from tests.unit.gen3_world import ROM_BASE, World, key_of, lua_to_py, mon_record
from tools.gen_gen3_profile import RR_ABILITY_READER

BASE_STATS = 0x08100000
TRAINERS = 0x08110000
OT = 0x12345678


def put_rom(world, address, data):
    world.rom.update({address - ROM_BASE + i: value for i, value in enumerate(data)})


def flag(world, name, on=True):
    spec = RR_ABILITY_READER
    fid = spec["flags"][name]
    address = spec["flags_base"] + (fid - spec["flags_start"]) // 8
    byte = world._read(address, 1)
    world.poke_int(address, byte | (1 << (fid % 8)) if on else byte & ~(1 << (fid % 8)), 1)


def world_with_abilities():
    world = World("gen3_rr", "radical_red")
    for anchor in RR_ABILITY_READER["anchors"]:
        put_rom(world, ROM_BASE + anchor["rom_offset"], bytes.fromhex(anchor["expected_hex"]))
    put_rom(world, RR_ABILITY_READER["base_stats_pointer"], BASE_STATS.to_bytes(4, "little"))
    put_rom(world, RR_ABILITY_READER["trainer_table_pointer"], TRAINERS.to_bytes(4, "little"))
    put_rom(world, BASE_STATS + 476 * 28 + 22, bytes([60, 131, 0, 0, 95]))
    put_rom(world, RR_ABILITY_READER["normal_pool"], bytes([5, 11, 22, 33, 44, 55]))
    put_rom(world, RR_ABILITY_READER["restricted_pool"], bytes([3, 66, 77, 88]))
    world.set_trainer(OT)
    world.set_party([mon_record(2, OT, species=476)])
    return world


def resolve(world, mon=None):
    if mon is None:
        mon = world.parts.reads.read_party()[1]
    return lua_to_py(world.parts.reads.read_abilities(mon))


def test_selection_uses_parity_and_hidden_bit_not_catalog():
    world = world_with_abilities()
    for personality, hidden, slot, aid in ((2, 0, 1, 60), (3, 0, 2, 131), (2, 1, 3, 95)):
        mon = mon_record(personality, OT, species=476)
        mon["ability_num"] = hidden
        world.set_party([mon])
        got = resolve(world)
        assert got["ability_slot"] == slot and got["ability_id"] == aid
        assert got["ability_slots"] == [60, 131, 95] and not got["ability_randomized"]
    put_rom(world, BASE_STATS + 476 * 28 + 23, b"\0")
    put_rom(world, BASE_STATS + 476 * 28 + 26, b"\0")
    assert resolve(world)["ability_slot"] == 1


def test_randomizer_uses_live_player_seed_and_rom_pool():
    world = world_with_abilities()
    flag(world, "randomizer")
    # The mon's OT never changes, including after a save switch.
    for seed in (0, 1, 0x12345678, 0xABCDEF01):
        world.set_trainer(seed)
        got = resolve(world)
        ids = []
        for aid in (60, 131, 95):
            value = (max(seed, 1) % 65536 % 5 + 476 + aid) % 65536
            if value >= 5:
                value = (value - 3) % 65536
            ids.append([11, 22, 33, 44, 55][(value ^ ((max(seed, 1) >> 16) % 255)) % 5])
        assert got["ability_slots"] == ids and got["ability_randomized"]
    before = resolve(world)["ability_id"]
    put_rom(world, RR_ABILITY_READER["normal_pool"], bytes([5, 91, 92, 93, 94, 95]))
    assert resolve(world)["ability_id"] != before
    for name in ("facility", "temporary_team", "link"):
        flag(world, name)
        assert resolve(world)["ability_slots"] == [60, 131, 95]
        flag(world, name, False)


def test_difficulty_and_scripted_opponent_context():
    world = world_with_abilities()
    flag(world, "restricted")
    put_rom(world, BASE_STATS + 476 * 28 + 22, bytes([2, 3, 0, 0, 45]))
    assert resolve(world)["ability_slots"] == [88, 102, 95]
    world.set_trainer(OT + 1)
    assert resolve(world)["ability_slots"] == [2, 3, 45]
    flag(world, "restricted", False)
    flag(world, "randomizer")
    foe = world.lua.table_from(mon_record(3, OT + 2, species=476), recursive=True)
    world.enter_battle([], trainer_id=7, fire=False)
    put_rom(world, TRAINERS + 7 * 40 + 1, bytes([1]))
    assert resolve(world, foe)["ability_randomized"] is False
    assert resolve(world)["ability_randomized"] is True
    put_rom(world, TRAINERS + 7 * 40 + 1, bytes([6]))
    assert resolve(world, foe)["ability_randomized"] is True
    flag(world, "scripted_battle")
    assert resolve(world, foe)["ability_randomized"] is False
    flag(world, "scripted_battle", False)
    flag(world, "hardcore")
    put_rom(world, RR_ABILITY_READER["hardcore_trainers"] + 8, (7).to_bytes(4, "little"))
    assert resolve(world, foe)["ability_randomized"] is False


def test_restricted_pool_and_species_specific_remaps():
    world = world_with_abilities()
    flag(world, "restricted")
    flag(world, "randomizer")
    assert resolve(world)["ability_slots"] == [77, 77, 77]
    flag(world, "randomizer", False)
    for species, original, expected in ((891, 3, 240), (1320, 3, 3), (248, 45, 22),
                                        (579, 45, 100), (1104, 182, 22), (981, 176, 176)):
        put_rom(world, BASE_STATS + species * 28 + 22, bytes([original, 0, 0, 0, 0]))
        world.set_party([mon_record(2, OT, species=species)])
        assert resolve(world)["ability_slots"] == [expected, 0, 0]
    assert world.writes == []


def test_missing_evidence_never_returns_default_abilities():
    world = world_with_abilities()
    put_rom(world, ROM_BASE + RR_ABILITY_READER["anchors"][0]["rom_offset"], b"\xff")
    value, why = resolve(world)
    assert value is None and "anchor mismatch" in why
    world = world_with_abilities()
    flag(world, "randomizer")
    put_rom(world, RR_ABILITY_READER["normal_pool"], b"\0")
    value, why = resolve(world)
    assert value is None and "pool" in why
    world = world_with_abilities()
    world.poke_int(world.wc["pointers"]["gSaveBlock2Ptr"]["address"], 0, 4)
    value, why = resolve(world)
    assert value is None and "null" in why


def test_production_wire_sends_actual_party_box_enemy_and_capture_abilities():
    world = world_with_abilities()
    flag(world, "randomizer")
    world.set_box(0, 0, mon_record(3, OT, species=476))
    world.step_to(60)
    hello = world.events("hello")[0]
    assert hello["party"][0]["ability_slots"] == resolve(world)["ability_slots"]
    assert hello["pc_boxes"][0]["ability_slot"] == 2
    assert hello["pc_boxes"][0]["ability_randomized"] is True
    assert "ability_record" not in hello["pc_boxes"][0]
    world.enter_battle([mon_record(4, OT + 1, species=476)], fire=False)
    world.step(30)
    assert world.events("tick")[-1]["enemy_party"][0]["ability_slots"]
    world.leave_battle(fire=False)
    world.set_party([mon_record(2, OT, species=476), mon_record(5, OT, species=476)])
    world.fire("mon_given")
    world.step(30)
    capture = world.events("capture")[-1]
    assert capture["key"] == key_of(5, OT) and capture["ability_slot"] == 2
    assert capture["ability_id"] == capture["ability_slots"][1]
