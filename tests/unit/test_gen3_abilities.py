"""Read-only ability resolution over synthetic ROM/RAM; no emulator process."""
from tests.unit.gen3_world import ROM_BASE, World, lua_to_py, mon_record
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
