"""lua/gen2/polished_stats.lua is the Lua twin of polished_codec.savemon_to_party: byte-for-byte equal over seeded
random savemons (every option-flag combination, hyper training, eggs, variant forms, PP-Up cases), plus source-mutant
red controls proving the cross-check can tell a wrong twin from the right one."""
from __future__ import annotations

import random
import re

import pytest

from server.adapters import polished_codec as pc
from tests.unit import test_polished_stats as stats
from tests.unit.test_polished_stats import STATS, make_mon

move_rows = stats.move_rows
species_rows = stats.species_rows
variants = stats.variants

lupa = pytest.importorskip("lupa")
LUA_PATH = pc.ROOT / "lua/gen2/polished_stats.lua"
SOURCE = LUA_PATH.read_text(encoding="utf-8")
FLAGS = [(e, n, p) for e in (False, True) for n in (False, True) for p in (False, True)]


def load(source=SOURCE):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    return lua, lua.eval("function(src) return assert(load(src, '=polished_stats'))() end")(source)


def options(lua, species_rows, move_rows, variants, flags):
    tab = lua.table_from
    base = {int(k): tab([v["base_stats"][n] for n in STATS]) for k, v in species_rows.items()}
    base.update({r: tab(list(b)) for r, b in variants.items()})
    return tab({"base_stats": tab(base),
                "variant_record": tab({s * 32 + f: r for (s, f), r in pc.variant_records().items()}),
                "move_pp": tab(dict(move_rows)),
                "apply_evs": flags[0], "natures_on": flags[1], "perfect_ivs": flags[2]})


def vectors(species_rows):
    """(mon, flags) pairs: seeded PP-cap vectors first, then 500 random ones."""
    rng = random.Random(0x7A15)
    records = sorted(int(k) for k in species_rows)
    forms = sorted(pc.variant_records())
    # base PP 40 + 3 ups = 61 (not 64), and base PP 1 / 40 mixes; PP > 35 moves
    out = [(make_mon(rng, 1, level=50, moves=(45, 166, 81, 255), ups=(3, 3, 3, 3), ev=[255] * 6,
                     dv=[3, 7, 11, 5, 9, 13], nature=7, extra=0x24), f) for f in FLAGS]
    for n in range(500):
        form = forms[rng.randrange(len(forms))] if n % 5 == 0 else None
        mon = make_mon(rng, form[0] if form else records[rng.randrange(len(records))], form[1] if form else 0,
                       level=rng.choice([1, 50, 100, rng.randrange(1, 101)]),
                       dv=[rng.choice([0, 15, rng.randrange(16)]) for _ in STATS],
                       ev=[rng.choice([0, 255, rng.randrange(256)]) for _ in STATS],
                       moves=tuple(rng.choice([0, rng.randrange(1, 256)]) for _ in range(4)),
                       ups=tuple(rng.randrange(4) for _ in range(4)),
                       extra=rng.choice([0, 0xFC, rng.randrange(256)]))
        mon["is_egg"] = n % 7 == 0
        out.append((mon, FLAGS[n % 8]))
    return out


def mismatches(module, lua, species_rows, move_rows, variants):
    """Vectors on which the Lua module's 48 bytes differ from the Python codec's."""
    bad = 0
    for mon, flags in vectors(species_rows):
        box = pc.encode_savemon(mon)
        want = pc.savemon_to_party(box, variant_base_stats=variants, apply_evs=flags[0], natures_on=flags[1],
                                   perfect_ivs=flags[2])
        party, view = module.party_from_savemon(lua.table_from(list(box)),
                                                options(lua, species_rows, move_rows, variants, flags))
        assert party is not None, (view, mon)
        got = bytes(party[i] for i in range(1, 49))
        if got != want:
            bad += 1
        elif view.max_hp != int.from_bytes(want[36:38], "big") or bool(view.is_egg) != mon["is_egg"]:
            bad += 1
    return bad


def test_the_lua_twin_equals_the_python_codec_over_500_random_savemons(species_rows, move_rows, variants):
    lua, module = load()
    assert mismatches(module, lua, species_rows, move_rows, variants) == 0
    # the 61 ceiling, spelled out
    assert module.max_pp(40, 3) == 61 == pc.max_pp(40, 3)


LUA_STAT_KEYS = [("attack", "atk"), ("defense", "def"), ("speed", "spe"),
                 ("special_attack", "spa"), ("special_defense", "spd")]


def endianness_failures(module, lua, species_rows, move_rows, variants):
    """Vectors where decode_party_mon (the existing big-endian reader) disagrees with the Lua twin's own view."""
    bad = 0
    for mon, flags in vectors(species_rows)[:120]:
        party, view = module.party_from_savemon(lua.table_from(list(pc.encode_savemon(mon))),
                                                options(lua, species_rows, move_rows, variants, flags))
        got = pc.decode_party_mon(bytes(party[i] for i in range(1, 49)))
        ok = got["max_hp"] == view.max_hp and got["hp"] == (0 if mon["is_egg"] else view.max_hp)
        ok = ok and all(got["stats"][long] == view.stats[short] for long, short in LUA_STAT_KEYS)
        bad += not ok
    return bad


def test_the_lua_party_bytes_decode_big_endian_to_the_view_values(species_rows, move_rows, variants):
    lua, module = load()
    assert endianness_failures(module, lua, species_rows, move_rows, variants) == 0


@pytest.mark.parametrize("name,before,after", [
    ("HP little", "p[35], p[36] = hp >> 8, hp & 255", "p[35], p[36] = hp & 255, hp >> 8"),
    ("stats little", "stats[i] >> 8, stats[i] & 255", "stats[i] & 255, stats[i] >> 8"),
])
def test_the_endianness_check_goes_red_on_a_little_endian_twin(name, before, after, species_rows, move_rows, variants):
    assert SOURCE.count(before) == 1
    lua, module = load(SOURCE.replace(before, after))
    assert endianness_failures(module, lua, species_rows, move_rows, variants) > 0, name


def test_the_view_decodes_the_struct(species_rows, move_rows, variants):
    lua, module = load()
    mon = make_mon(random.Random(2), 1, level=50, dv=[15] * 6, ev=[0] * 6, nature=0)
    box = pc.encode_savemon(mon)
    party, view = module.party_from_savemon(lua.table_from(list(box)),
                                            options(lua, species_rows, move_rows, variants, (True, True, False)))
    assert (view.species, view.level, view.is_egg) == (1, 50, False)
    assert (view.hp, view.max_hp, view.stats.atk) == (120, 120, 69)      # ((45+15)*2+1)*50//100+60, ((49+15)*2+1)*50//100+5
    assert len(party) == 48


def test_refusals_return_nil_and_a_reason(species_rows, move_rows, variants):
    lua, module = load()
    box = lua.table_from(list(pc.encode_savemon(make_mon(random.Random(3), 1))))
    good = options(lua, species_rows, move_rows, variants, (True, True, False))
    for key in ("base_stats", "variant_record", "move_pp", "apply_evs", "natures_on", "perfect_ivs"):
        opts = options(lua, species_rows, move_rows, variants, (True, True, False))
        opts[key] = None
        assert module.party_from_savemon(box, opts)[0] is None, key
    assert module.party_from_savemon(lua.table_from([1, 2, 3]), good)[0] is None
    # a variant form whose record has no base stats is refused, not guessed
    gyarados = lua.table_from(list(pc.encode_savemon(make_mon(random.Random(4), 130, 21))))
    opts = options(lua, species_rows, move_rows, variants, (True, True, False))
    opts.base_stats[292] = None
    party, why = module.party_from_savemon(gyarados, opts)
    assert party is None and "292" in why
    # an unknown non-zero move is refused
    opts = options(lua, species_rows, move_rows, variants, (True, True, False))
    opts.move_pp[1] = None
    assert module.party_from_savemon(box, opts)[0] is None


MUTANTS = {
    "Def/Spe DV nibbles swapped": ("sv[19] >> 4, sv[19] & 15,", "sv[19] & 15, sv[19] >> 4,"),
    "nature raised/lowered swapped": ("local raised, lowered = nature // 5 + 2, nature % 5 + 2",
                                      "local raised, lowered = nature % 5 + 2, nature // 5 + 2"),
    "PERFECT_IVS override dropped": ("(opts.perfect_ivs or trained &", "(trained &"),
    "EV shift >>2 becomes >>3": ("sv[11 + i] >> 2", "sv[11 + i] >> 3"),
    "PP cap removed": ("math.min(base_pp // 5, PP_UP_CAP)", "(base_pp // 5)"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_red_control(name, species_rows, move_rows, variants):
    before, after = MUTANTS[name]
    assert SOURCE.count(before) == 1, f"{name}: anchor not found exactly once"
    lua, module = load(SOURCE.replace(before, after))
    assert mismatches(module, lua, species_rows, move_rows, variants) > 0, f"{name}: the cross-check cannot see it"


def test_the_module_is_pure():
    assert not re.search(r"\b(io|os|emu|memory|gui|dofile|loadfile|require)\s*[.(]", SOURCE)
