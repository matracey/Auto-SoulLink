"""Card POL-WDEXEC: the Polished `party_mon` withdraw write executor
(lua/gen2/polished_overworld.lua `O.boxes.withdraw`, composed by compose_polished in lua/gen2/entry.lua).

Same rig as tests/unit/test_polished_write_path.py: the REAL overlay ROM, a System Bus image laid out from
data/polished/polished_slink.sym, and a CartRAM save image the census accepts. Assertions are on the actual
WRAM/SRAM bytes, on the io write log, and against the Python oracle `polished_codec.savemon_to_party` for the
reconstructed 48-byte record.

Engine contract (docs/polished/WITHDRAW.md §1-§2, engine/pc/bills_pc.asm): append to the party (record 48 B +
nickname 11 B + OT 11 B including the 3 EXTRA bytes, wPartyCount raised) and, on the box side, clear ONLY the
slot's pointer byte and its Banks bit. The pokedb entry, its name-MSB checksum and its allocation flag are never
touched - a freed entry is reused by NewStoragePointer. The three RAM options are read LIVE at the hold.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from server.adapters import polished_codec as pc
from tests.unit.test_polished_boxes import FLAG_FLAT, Image
from tests.unit.test_polished_lua import ROOT
from tests.unit.test_polished_write_path import (
    PARTY_COUNT,
    PARTY_MONS,
    SYM,
    Rig,
    _pair,
    box_flat,
    entry_flat,
    entry_for,
    key_of,
    party,
    plant,
    sysbus,  # noqa: F401
)

lupa = pytest.importorskip("lupa")

REPO = Path(ROOT)
MODULE = (REPO / "lua/gen2/polished_overworld.lua").read_text(encoding="utf-8")
MOVES = json.loads((REPO / "data/games/polished_crystal/moves.json").read_text(encoding="utf-8"))
MOVE_PP = {row["id"]: row["pp"] for row in MOVES["moves"]}
OPT1, OPT2 = SYM["wInitialOptions"][1], SYM["wInitialOptions"][1] + 1
PERFECT_IVS_BIT, EV_MASK = 3, 0x03   # EV_OPTMASK EQU %11 (ram_constants.asm:125); bit 3 is the RTC option
assert (OPT1, OPT2) == (0xCFF6, 0xCFF7)


def oracle(entry, *, apply_evs, natures_on, perfect_ivs):
    return pc.savemon_to_party(entry, variant_base_stats={}, apply_evs=apply_evs, natures_on=natures_on,
                               perfect_ivs=perfect_ivs, move_pp=MOVE_PP)


def options(rig, apply_evs=False, natures_on=False, perfect_ivs=False):
    """The three RAM option bytes the executor must read live (wInitialOptions bit 0 natures / bit 3 perfect IVs,
    wInitialOptions2 & %11 for EVs)."""
    rig.mem[OPT1] = (1 if natures_on else 0) | (1 << PERFECT_IVS_BIT if perfect_ivs else 0)
    rig.mem[OPT2] = EV_MASK if apply_evs else 0x00


def boxed_mon(rng_seed=909, species=99):
    """A mon that is NOT in the test party (the party helper is deterministic, so party()[0] is in it)."""
    from tests.unit.test_polished_lua import _mon
    mon = _mon(random.Random(rng_seed), species)
    # 8 name bytes + three DISTINCTIVE EXTRA bytes (the savemon's 29..31, which the engine keeps as hyper
    # training); distinctive so an assertion that reads the wrong address cannot pass by coincidence
    mon.update(is_egg=False, hp=300, max_hp=300, ot_name="KURT", nickname="BOXED",
               ot_raw_hex=(pc.encode_text("KURT", 8) + bytes([0xA5, 0x5A, 0xA5])).hex(),
               nickname_raw_hex=pc.encode_text("BOXED", 11).hex(),
               stats=dict.fromkeys(pc.STAT_NAMES[1:], 100))
    # OPTION-SENSITIVE FIXTURE: six DV nibbles that are NOT 15 and six non-zero EV bytes, so that perfect IVs,
    # EVs and natures each provably change the rebuilt record (with all-15 DVs and zero EVs every option
    # combination yields the same bytes and the option tests pass vacuously).
    for name, nibble in zip(pc.STAT_NAMES, (3, 7, 11, 5, 9, 13), strict=True):
        mon["dvs"][name] = nibble
    for name, value in zip(pc.STAT_NAMES, (36, 52, 20, 68, 12, 28), strict=True):
        mon["evs"][name] = value
    mon["nature"] = 7                       # raised stat 3, lowered stat 2 -> not neutral
    mon["dv_bytes"] = int("".join(f"{mon['dvs'][n]:X}" for n in pc.STAT_NAMES), 16)
    return mon


def boxed(rig, mon, box=1, slot=1, bank=1, entry=7):
    raw = entry_for(mon)
    plant(rig.img, box, slot, bank, entry, raw)
    return raw


def record_bytes(rig, slot):
    return bytes((rig.mem[PARTY_MONS + slot * 48 + i] or 0) for i in range(48))


def _lua_withdraw(lua):
    return lua.eval("""
        function(boxes, k)
            return boxes.withdraw('' .. k)
        end
    """)


def withdraw(rig, key):
    """The COMPOSED executor, called from inside Lua so the key is a real Lua string. compose_polished hands the
    CLIENT an adapter around it (O.client_boxes: bare `true`, `party full`); these tests pin the EXECUTOR's own
    return shape, so they reach through the adapter's __index (tests/unit/test_polished_box_contract.py covers it)."""
    boxes = rig.overworld().boxes
    raw = rig.lua.eval("function(t) local m = getmetatable(t) return m and m.__index or t end")(boxes)
    return _lua_withdraw(rig.lua)(raw, key)


# a mutant loader that keeps the withdraw dependencies (the shared one in test_polished_write_path.py predates them)
RED = """
return function(parts, io, source)
    local Overworld = assert(load(source, "mutant"))()
    local Boxes = dofile(ROOTDIR .. "/lua/gen2/polished_boxes.lua")
    local P = dofile(ROOTDIR .. "/lua/gen2/polished.lua")
    local Stats = dofile(ROOTDIR .. "/lua/gen2/polished_stats.lua")
    assert(P.load_variants(parts.profile.derived.variant_forms))
    local Permit = dofile(ROOTDIR .. "/lua/write_permit.lua")
    local coords, census, reads = parts.overworld.coords, parts.overworld.census, parts.reads
    local reader = assert(Boxes.new(coords, io, P.mon_key, nil))
    local checkpoint = Overworld.checkpoint(parts.profile, io, nil, Overworld.HOLD)
    local policy = {
        authorize = function(op) return Overworld.KINDS[op] == true and checkpoint:check(op) == true end,
        pointer_stable = function() return true end,
        lifetime = {capture = function() return io.framecount() end, valid = function(t) return t == io.framecount() end},
        provenance = function() return {site = "red control"} end,
    }
    local writes = Overworld.writes(parts.profile, coords, io, Permit, policy, checkpoint)
    return Overworld.boxes({profile = parts.profile, reads = reads, census = census, boxes = Boxes, reader = reader,
                            writes = writes, io = io, coords = coords, stats = Stats, base_stats = BASE_STATS,
                            variant_record = VARIANTS, move_pp = MOVE_PPT})
end
"""


def build(rig, edits=(), io=None):
    """A (possibly mutated) copy of the module with the SAME withdraw dependencies the composition injects.
    `edits` = [(old, new)] source replacements (each must apply); `io` = an io the writes go through (FLAKY)."""
    source = MODULE
    for old, new in edits:
        assert old != new and old in source, f"the mutation did not apply: {old[:70]}"
        source = source.replace(old, new, 1)
    index = json.loads((REPO / "data/games/polished_crystal/species_index.json").read_text(encoding="utf-8"))
    moves = json.loads((REPO / "data/games/polished_crystal/moves.json").read_text(encoding="utf-8"))
    base, variants, pp = {}, {}, {}
    stat_keys = ("hp", "attack", "defense", "speed", "special_attack", "special_defense")
    for sid, row in index["species"].items():
        base[int(sid)] = rig.lua.table_from([row["base_stats"][k] for k in stat_keys])
    for row in index["forms"]:
        if row["kind"] == "variant" and row.get("base_stats"):
            base[row["ext"]] = rig.lua.table_from([row["base_stats"][k] for k in stat_keys])
            variants[row["species"] * 32 + row["form"]] = row["ext"]
    for row in moves["moves"]:
        pp[row["id"]] = row["pp"]
    rig.lua.globals().BASE_STATS = rig.lua.table_from(base)
    rig.lua.globals().VARIANTS = rig.lua.table_from(variants)
    rig.lua.globals().MOVE_PPT = rig.lua.table_from(pp)
    loader = rig.lua.execute(RED.replace("ROOTDIR", json.dumps(ROOT)))
    return loader(rig.parts, io if io is not None else rig.io, source)


def mutant(rig, old, new, io=None):
    return build(rig, [(old, new)], io)


# An io whose write_u8 can lose the power after `budget` byte writes (a thrown error: an exception or a lifetime
# loss mid-write), silently drop one CartRAM byte (`swallow`), or flip bit 0 of one byte on the wire (`tear`).
# Everything else (reads, bank_valid, framecount) falls through to the rig io; writes still reach the rig io's log.
FLAKY = """
return function(real)
    local io = setmetatable({flaky = {}}, {__index = real})
    function io.read_u8(a, d)
        local f = io.flaky
        if f.hook ~= nil and f.hook_at == a then
            local h = f.hook
            f.hook = nil
            h()
        end
        return real.read_u8(a, d)
    end
    function io.write_u8(a, v, d)
        local f = io.flaky
        if f.budget ~= nil then
            if f.budget <= 0 then error("injected loss", 0) end
            f.budget = f.budget - 1
        end
        if f.swallow == a and d == "CartRAM" then return end
        if f.tear == a and (d or "System Bus") == "System Bus" then v = v ~ 1 end
        return real.write_u8(a, v, d)
    end
    return io
end
"""
CLEAR = "function(t) for k in pairs(t) do t[k] = nil end end"


def flaky_io(rig):
    return rig.lua.execute(FLAKY)(rig.io)


def snapshot(rig):
    return dict(rig.mem.items()), rig.img.snap()


def restore(rig, snap):
    mem, img = snap
    for k in list(rig.mem.keys()):
        if k not in mem:
            rig.mem[k] = None
    for k, v in mem.items():
        rig.mem[k] = v
    for d, data in img.items():
        rig.img.mem[d][:] = data
    rig.lua.eval(CLEAR)(rig.log["writes"])


def entries_byte(rig, box=1, slot=1):
    return rig.img.mem["CartRAM"][box_flat(box) + slot - 1]


def count_of_key(rig, key):
    after, why = _pair(rig.parts.reads.read_party())
    assert after is not None, why
    return sum(1 for m in after.mons.values() if m["key"] == key)


# the source lines the red controls edit (exact text of lua/gen2/polished_overworld.lua)
ORDER = "            if append then append_to_party() end\n            remove_box_copy()\n"
ENTRIES_BATCH = '                    writes:write_batch({{domain = "CartRAM", addr = entries_at, bytes = {0}}})\n'
PARTY_LAST_SPAN = '                        {domain = "System Bus", addr = at_nick, bytes = nick},\n'
COUNT_SPAN = '                        {domain = "System Bus", addr = block, bytes = {party.count + 1}},\n'
FIRST_SPAN = ('                    writes:write_batch({\n'
              '                        {domain = "System Bus", addr = at_rec, bytes = record},\n')
BOX_NARROW = ('writes:arm("box_deposit", function(domain, addr, n)\n'
              '                    return domain == "CartRAM" and n == 1 and addr == entries_at\n'
              '                end)')
ENTRIES_CHECK = ('if io_.read_u8(entries_at, "CartRAM") ~= 0 then\n'
                 '                    refuse("withdraw read-back refused: Entries byte not cleared')
FINAL_ENTRIES_CHECK = 'if io_.read_u8(entries_at, "CartRAM") ~= 0 then refuse("read-back refused'
BANKS_STEP = '                local bits = io_.read_u8(bits_at, "CartRAM")\n'
STALE_APPEND = 'if live.count ~= party.count then refuse("party changed during withdraw") end'
STALE_RECONCILE = 'if target >= now.count then refuse("party changed during withdraw") end\n'

PARTY_NARROW = ('writes:arm("party_collection", function(domain, addr, n)\n'
                '                    return domain == "System Bus" and ((addr == at_rec and n == s.End) '
                'or (addr == at_ot and n == c.NAME_LENGTH)\n'
                '                           or (addr == at_nick and n == c.MON_NAME_LENGTH) '
                'or (addr == block and n == 1))\n'
                '                end)')
BOX_FIRST = [(ORDER, "            remove_box_copy()\n            if append then append_to_party() end\n")]
COUNT_FIRST = [(COUNT_SPAN + "                    })", "                    })"),
               (FIRST_SPAN, FIRST_SPAN.replace("                    writes:write_batch({\n",
                                               "                    writes:write_batch({\n" + COUNT_SPAN))]


# ── the happy path ──────────────────────────────────────────────────────────────────────────────────────

def test_withdraw_lands_the_mon_and_clears_only_the_box_pointer():
    mons = party()
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(mons)
    options(rig)
    entry = boxed(rig, mon)
    flag_before = rig.img.mem["WRAM"][FLAG_FLAT[1]]
    done, note = _pair(withdraw(rig, key))
    assert done is True, note
    assert rig.count() == len(mons) + 1
    # the record is exactly the Python oracle for the LIVE options, healed, status clear
    assert record_bytes(rig, 3) == oracle(entry, apply_evs=False, natures_on=False, perfect_ivs=False)
    assert rig.status(3) == 0
    assert note["hp"] == note["max_hp"] > 0
    # the box side: pointer byte 0, Banks bit clear ...
    assert rig.img.mem["CartRAM"][box_flat(1)] == 0
    assert rig.img.mem["CartRAM"][box_flat(1) + 0x14] & 1 == 0
    # ... and the pokedb entry plus its allocation flag BYTE-IDENTICAL (the engine never frees one)
    assert bytes(rig.img.mem["CartRAM"][entry_flat(1, 7):entry_flat(1, 7) + 49]) == entry
    assert rig.img.mem["WRAM"][FLAG_FLAT[1]] == flag_before
    # the census no longer finds it boxed, the party does
    boxes, why = rig.census_keys()
    assert why is None and key not in boxes[1]
    after, _ = _pair(rig.parts.reads.read_party())
    assert [m["key"] for m in after.mons.values()] == [key_of(m) for m in mons] + [key]


def test_the_extra_bytes_ride_in_the_ot_array():
    """The engine reads the hyper-training mask from wTempMonOT + PLAYER_NAME_LENGTH (bills_pc.asm:960), so the three
    EXTRA bytes are written at the tail of the 11-byte OT array, not inside the 48-byte record."""
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig)
    entry = boxed(rig, mon)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is True, why
    ot = SYM["wPartyMonOTs"][1]
    tail = bytes((rig.mem[ot + len(mons) * 11 + 8 + i] or 0) for i in range(3))
    assert bytes(entry[29:32]) != b"\x00\x00\x00", "the fixture must carry distinctive EXTRA bytes"
    assert tail == bytes(entry[29:32])


def count_is_last_and_box_after_party(writes):
    """The party half is (record, OT, nickname, wPartyCount) and the box half comes after ALL of it."""
    system = [i for i, w in enumerate(writes) if w["domain"] == "System Bus"]
    cart = [i for i, w in enumerate(writes) if w["domain"] == "CartRAM"]
    return (bool(system) and bool(cart) and writes[system[-1]]["addr"] == PARTY_COUNT
            and [w["addr"] for w in writes if w["domain"] == "System Bus"].count(PARTY_COUNT) == 1
            and min(cart) > max(system))


def test_party_half_first_count_last_then_the_box_half():
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig)
    boxed(rig, mon)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is True, why
    writes = rig.writes()
    assert len(writes) == 48 + 11 + 11 + 1 + 2          # record, OT, nickname, count; Entries byte, Banks byte
    assert [w["domain"] for w in writes] == ["System Bus"] * 71 + ["CartRAM"] * 2
    assert [w["addr"] for w in writes][:48] == [PARTY_MONS + 3 * 48 + i for i in range(48)]
    assert writes[70]["addr"] == PARTY_COUNT, "wPartyCount must be written LAST of the party half"
    assert count_is_last_and_box_after_party(writes)
    assert [w["addr"] for w in writes[71:]] == [box_flat(1), box_flat(1) + 0x14]


def test_red_control_box_half_first_is_caught_by_the_order_check():
    """RED: the two halves swapped in a copy of the module. The order predicate that passes the real run fails."""
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig)
    boxed(rig, mon)
    bad = build(rig, BOX_FIRST)
    done, why = _pair(_lua_withdraw(rig.lua)(bad, key_of(mon)))
    assert done is True, why                            # the mutant still withdraws, in the wrong order
    assert not count_is_last_and_box_after_party(rig.writes())


def test_red_control_writing_wpartycount_first():
    """RED: the count is written before the record. The order predicate fails on the wire, AND a loss right after
    the count leaves a count over a zeroed slot (the state WITHDRAW.md section 2 forbids); the real order never does."""
    def run(edits):
        rig = Rig(party())
        options(rig)
        mon = boxed_mon()
        entry = boxed(rig, mon)
        fio = flaky_io(rig)
        ex = build(rig, edits, fio)
        snap = snapshot(rig)
        done, why = _pair(_lua_withdraw(rig.lua)(ex, key_of(mon)))
        assert done is True, why
        ordered = count_is_last_and_box_after_party(rig.writes())
        restore(rig, snap)
        fio.flaky.budget = 1                            # power loss after exactly one byte
        done, _ = _pair(_lua_withdraw(rig.lua)(ex, key_of(mon)))
        assert done is None
        return ordered, rig.count(), record_bytes(rig, 3) == oracle(
            entry, apply_evs=False, natures_on=False, perfect_ivs=False)

    assert run([]) == (True, 3, False)                  # real: count untouched after one byte
    ordered, count, landed = run(COUNT_FIRST)
    assert not ordered
    assert count == 4 and not landed, "count-first leaves a raised count over a zeroed slot"


# ── power loss at every byte of the withdraw ─────────────────────────────────────────────────────────────────

TOTAL = 48 + 11 + 11 + 1 + 2


def sweep(edits=(), points=None, party_size=3):
    """Cut the write stream after k bytes (an exception or lifetime loss), for each k. Returns one dict per cut:
    where the mon lives afterwards, and what a clean retry leaves behind."""
    mons = party(party_size)
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(mons)
    options(rig)
    entry = boxed(rig, mon)
    want = oracle(entry, apply_evs=False, natures_on=False, perfect_ivs=False)
    fio = flaky_io(rig)
    ex = build(rig, edits, fio)
    snap = snapshot(rig)
    out = []
    for k in (range(TOTAL) if points is None else points):
        restore(rig, snap)
        fio.flaky.budget = k
        done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
        fio.flaky.budget = None
        assert done is None and "injected loss" in why, (k, done, why)
        slot = party_size
        row = {"k": k,
               "boxed": entries_byte(rig) != 0,
               "partied": rig.count() == party_size + 1 and record_bytes(rig, slot) == want,
               "pokedb_intact": bytes(rig.img.mem["CartRAM"][entry_flat(1, 7):entry_flat(1, 7) + 49]) == entry}
        done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
        row.update(retry_done=done, retry_why=why, copies=count_of_key(rig, key), count=rig.count(),
                   box_cleared_after_retry=entries_byte(rig) == 0)
        out.append(row)
    restore(rig, snap)
    done, why = _pair(_lua_withdraw(rig.lua)(ex, key))       # the control: no loss, the sweep's own clean run
    assert done is True, why
    assert len(rig.writes()) == TOTAL
    return out


def test_power_loss_at_every_byte_never_leaves_the_mon_in_neither_place():
    rows = sweep()
    assert [r["k"] for r in rows] == list(range(TOTAL))
    lost = [r["k"] for r in rows if not (r["boxed"] or r["partied"])]
    assert lost == [], f"cut points that lost the mon: {lost}"
    # before the count byte only the box holds it; from the count on, the party does too (or alone, once the
    # Entries byte is erased)
    assert all(r["boxed"] and not r["partied"] for r in rows if r["k"] < 71)
    assert rows[71]["boxed"] and rows[71]["partied"]
    assert rows[72]["partied"] and not rows[72]["boxed"]
    assert all(r["pokedb_intact"] for r in rows)
    # and a clean retry always leaves exactly ONE party copy (never a third, never none)
    assert all(r["copies"] == 1 and r["count"] == 4 for r in rows), [r for r in rows if r["copies"] != 1]
    assert all(r["retry_done"] is True and r["box_cleared_after_retry"] for r in rows if r["k"] <= 71), \
        [(r["k"], r["retry_why"]) for r in rows if r["k"] <= 71 and r["retry_done"] is not True]


def test_red_control_box_first_loses_the_mon_at_a_cut_point():
    """RED: the halves swapped. The same sweep finds cut points with the mon in NEITHER place (the false
    comment in the old source claimed both copies; Codex reproduced 72 of 73 lost)."""
    rows = sweep(BOX_FIRST, points=[0, 1, 2, 3, 40, 71, 72])
    lost = [r["k"] for r in rows if not (r["boxed"] or r["partied"])]
    assert lost, "the box-first order must lose the mon at some cut point"
    assert 2 in lost and 3 in lost


def test_a_both_places_state_with_a_full_party_still_reconciles():
    """The cut after the count byte with FIVE party mons: the party is now full (6/6) and the box still holds the
    mon. The retry must reconcile (no append is needed), not refuse as party full."""
    row = sweep(points=[71], party_size=5)[0]
    assert row["boxed"] and row["partied"]
    assert row["retry_done"] is True, row["retry_why"]
    assert row["count"] == 6 and row["copies"] == 1 and row["box_cleared_after_retry"]


# ── the three live options ────────────────────────────────────────────────────────────────────────────

def test_the_fixture_is_option_sensitive_for_every_flag():
    """A guard, not a claim: with all-15 DVs and zero EVs the oracle is identical for every flag combination and
    every option test would pass vacuously. Each flag must move the rebuilt record on THIS fixture."""
    mon = boxed_mon()
    entry = entry_for(mon)
    for flag in ("perfect_ivs", "apply_evs", "natures_on"):
        on = oracle(entry, apply_evs=True, natures_on=True, perfect_ivs=True)
        off = oracle(entry, **{**{"apply_evs": True, "natures_on": True, "perfect_ivs": True}, flag: False})
        assert on != off, f"{flag} cannot move the rebuilt record on this fixture"


@pytest.mark.parametrize("apply_evs,natures_on,perfect_ivs", [
    (False, False, False), (True, False, False), (False, True, False), (False, False, True), (True, True, True),
])
def test_each_option_is_read_live(apply_evs, natures_on, perfect_ivs):
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig, apply_evs, natures_on, perfect_ivs)
    entry = boxed(rig, mon)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is True, why
    assert record_bytes(rig, 3) == oracle(entry, apply_evs=apply_evs, natures_on=natures_on, perfect_ivs=perfect_ivs)


def test_the_rtc_option_bit_alone_does_not_enable_evs():
    """wInitialOptions2 bit 3 is the RTC option, NOT an EV option: EV_OPTMASK is %11 = $03. With $08 the EVs must
    stay off, so the record equals the apply_evs = false oracle - the $0B mask would have passed the EVs in."""
    mons = party()
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(mons)
    rig.mem[OPT1] = 0x00
    rig.mem[OPT2] = 0x08                                   # RTC bit only
    entry = boxed(rig, mon)
    done, why = _pair(withdraw(rig, key))
    assert done is True, why
    assert record_bytes(rig, 3) == oracle(entry, apply_evs=False, natures_on=False, perfect_ivs=False)


def test_the_ev_mask_bits_alone_do_enable_evs():
    """The control for the test above: $03 (the mask itself) turns the EVs on, so the two cases are not both inert."""
    mons = party()
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(mons)
    rig.mem[OPT1] = 0x00
    rig.mem[OPT2] = 0x03
    entry = boxed(rig, mon)
    done, why = _pair(withdraw(rig, key))
    assert done is True, why
    assert record_bytes(rig, 3) == oracle(entry, apply_evs=True, natures_on=False, perfect_ivs=False)


def test_unreadable_options_refuse_by_name():
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig)
    boxed(rig, mon)
    real = rig.io.read_u8
    rig.lua.globals().STUB_OPT1 = 0xCFF6
    rig.io.read_u8 = rig.lua.eval(
        "function(a, d) if a == STUB_OPT1 then return nil end return REAL_READ(a, d) end")
    rig.lua.globals().REAL_READ = real
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is None and "options" in why
    assert rig.writes() == [] and rig.count() == len(mons)


# ── refusals ───────────────────────────────────────────────────────────────────────────────────────────

def test_a_full_party_refuses():
    rig = Rig(party(6))
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is None and why.startswith("party full")
    assert rig.writes() == [] and rig.count() == 6


def test_an_unknown_key_refuses():
    rig = Rig(party())
    options(rig)
    done, why = _pair(withdraw(rig, "ABCDEF:1234:010:00"))
    assert done is None and "not boxed" in why and rig.writes() == []


def test_a_bad_checksum_never_withdraws():
    rig = Rig(party())
    options(rig)
    mon = boxed_mon()
    raw = bytearray(boxed(rig, mon))
    raw[30] ^= 0x40                                    # a name-MSB checksum bit
    rig.img.mem["CartRAM"][entry_flat(1, 7):entry_flat(1, 7) + 49] = bytes(raw)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is None and ("census" in why or "checksum" in why)
    assert rig.writes() == [] and rig.count() == 3


def test_an_incomplete_census_refuses():
    rig = Rig(party(), img=Image())
    options(rig)
    done, why = _pair(withdraw(rig, key_of(boxed_mon())))
    assert done is None and "census incomplete" in why and rig.writes() == []


@pytest.mark.parametrize("sym,value", [("wBattleMode", 1), ("wScriptRunning", 1), ("wGameLogicPaused", 1),
                                      ("wLinkMode", 1), ("wMapStatus", 1)])
def test_a_hold_that_does_not_hold_refuses(sym, value):
    rig = Rig(party())
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    rig.mem[SYM[sym][1]] = value
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is None, f"the withdraw landed with {sym} = {value}"
    assert rig.writes() == [] and rig.count() == 3


def test_red_control_the_ev_mask_itself_is_load_bearing():
    """RED: with EV_OPTMASK put back to $0B in a copy of the module, wInitialOptions2 = $08 (the RTC bit alone)
    enables EVs and the $08 case's assertion breaks - so that test can fail."""
    mons = party()
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(mons)
    options(rig, apply_evs=False, natures_on=False, perfect_ivs=False)
    rig.mem[OPT2] = 0x08                     # wInitialOptions2: the RTC bit ALONE (EV_OPTMASK %11 = $03 ignores it, $0B would not)
    entry = boxed(rig, mon)
    # the real executor: the RTC bit alone leaves the EVs off
    done, why = _pair(withdraw(rig, key))
    assert done is True, why
    assert record_bytes(rig, 3) == oracle(entry, apply_evs=False, natures_on=False, perfect_ivs=False)
    # the $0B mutant, on its OWN rig: the first withdraw already moved this mon into the party
    rig2 = Rig(mons)
    options(rig2, apply_evs=False, natures_on=False, perfect_ivs=False)
    rig2.mem[OPT2] = 0x08                    # wInitialOptions2: the RTC bit ALONE (EV_OPTMASK %11 = $03 ignores it, $0B would not)
    boxed(rig2, mon)
    bad = mutant(rig2, "local NATURES_OPT, PERFECT_IVS_OPT, EV_OPTMASK = 0, 3, 0x03",
                 "local NATURES_OPT, PERFECT_IVS_OPT, EV_OPTMASK = 0, 3, 0x0B")
    done, why = _pair(_lua_withdraw(rig2.lua)(bad, key))
    assert done is True, why
    assert record_bytes(rig2, 3) == oracle(entry, apply_evs=True, natures_on=False, perfect_ivs=False)


def test_a_graph_without_the_dependencies_refuses_by_name():
    """compose_polished always injects them; a graph that does not must refuse, not assume."""
    rig = Rig(party())
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    source = MODULE.replace("    local STATS, BASE, VARIANT, MOVE_PP = deps.stats, deps.base_stats, "
                            "deps.variant_record, deps.move_pp",
                            "    local STATS, BASE, VARIANT, MOVE_PP = nil, nil, nil, nil", 1)
    assert source != MODULE
    bare = mutant(rig, "    local STATS, BASE, VARIANT, MOVE_PP = deps.stats, deps.base_stats, "
                       "deps.variant_record, deps.move_pp",
                  "    local STATS, BASE, VARIANT, MOVE_PP = nil, nil, nil, nil")
    done, why = _pair(_lua_withdraw(rig.lua)(bare, key_of(mon)))
    assert done is None and "not composed" in why


# ── round trip ──────────────────────────────────────────────────────────────────────────────────────────

def test_withdraw_then_deposit_round_trips_the_entry_bytes():
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig)
    original = boxed(rig, mon)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is True, why
    boxes = rig.overworld().boxes
    done, note = _pair(boxes.deposit(key_of(mon)))
    assert done is True, note
    at = entry_flat(note["bank"], note["entry"])
    assert bytes(rig.img.mem["CartRAM"][at:at + 49]) == original, "the round trip altered the entry"


# ── red controls: every brake, applied to a copy of the module ────────────────────────────────────────

FLAG_AT = FLAG_FLAT[1]            # the WRAM allocation flag byte of pokedb bank 1 entries 1..8 (the fixture is entry 7)
SPECIES_AT = entry_flat(1, 7)     # the first byte of the boxed pokedb entry (its species byte)


def box_extra(domain, addr, value):
    return [(ENTRIES_BATCH,
             ENTRIES_BATCH.replace("bytes = {0}}})", f'bytes = {{0}}}}, {{domain = "{domain}", addr = {addr}, bytes = {{{value}}}}}}})'))]


def party_extra(addr, value):
    return [(PARTY_LAST_SPAN,
             PARTY_LAST_SPAN + f'                        {{domain = "System Bus", addr = {addr}, bytes = {{{value}}}}},\n')]


@pytest.mark.parametrize("domain,addr,value,what", [
    ("WRAM", FLAG_AT, 0, "the pokedb allocation flag"),
    ("CartRAM", SPECIES_AT, 0xFF, "a pokedb species byte"),
])
def test_an_extra_box_write_is_refused_by_the_narrowed_box_permit(domain, addr, value, what):
    """The box half may touch ONLY the Entries byte and its Banks byte. An injected extra write (the allocation
    flag, a pokedb byte) is refused by the permit and nothing of that batch is emitted; with the permit narrowing
    removed (RED) the same write lands - so the narrowing is the load-bearing brake."""
    def run(edits):
        rig = Rig(party())
        options(rig)
        mon = boxed_mon()
        boxed(rig, mon)
        before = rig.img.mem["WRAM" if domain == "WRAM" else "CartRAM"][addr]
        ex = build(rig, edits)
        done, why = _pair(_lua_withdraw(rig.lua)(ex, key_of(mon)))
        return rig, done, why, before, rig.img.mem["WRAM" if domain == "WRAM" else "CartRAM"][addr]

    rig, done, why, before, after = run([])                          # positive control: the real executor
    assert done is True, why
    assert after == before, f"the real executor changed {what}"
    rig, done, why, before, after = run(box_extra(domain, addr, value))
    assert done is None and "write refused" in why and "box half refused" in why, (done, why)
    assert after == before, f"{what} was written despite the permit"
    assert entries_byte(rig) == 7, "the refused batch must not have emitted its Entries byte"
    assert rig.count() == 4, "the mon is in both places (the party half already landed and verified)"
    wide = box_extra(domain, addr, value) + [(BOX_NARROW, 'writes:arm("box_deposit")')]
    rig, done, why, before, after = run(wide)                        # RED: permit un-narrowed
    assert done is True, why
    assert after != before, f"RED: with the permit wide open {what} should have been overwritten"


def test_an_extra_party_write_is_refused_by_the_narrowed_party_permit():
    """The party half may touch ONLY slot `count`'s record, OT, nickname and the count byte. An extra write into
    another live record (slot 0's species byte) used to be inside the whole-block permit."""
    slot0 = PARTY_MONS

    def run(edits):
        rig = Rig(party())
        options(rig)
        mon = boxed_mon()
        boxed(rig, mon)
        before = rig.mem[slot0]
        ex = build(rig, edits)
        done, why = _pair(_lua_withdraw(rig.lua)(ex, key_of(mon)))
        return rig, done, why, before, rig.mem[slot0]

    rig, done, why, before, after = run([])
    assert done is True, why
    assert after == before
    rig, done, why, before, after = run(party_extra(slot0, 0x01))
    assert done is None and "write refused" in why and "party half refused" in why, (done, why)
    assert after == before and rig.writes() == [], "a refused batch emits nothing"
    assert rig.count() == 3 and entries_byte(rig) == 7, "nothing moved"
    rig, done, why, before, after = run(party_extra(slot0, 0x01) + [(PARTY_NARROW, 'writes:arm("party_collection")')])
    assert done is True, why                                         # RED: permit un-narrowed
    assert after != before, "RED: the whole-block permit let the extra write corrupt another record"


def test_red_control_clearing_the_pokedb_allocation_flag():
    """RED: the free-list entry is released (the engine never does this; the flag is what NewStoragePointer scans).
    Executed against the narrowed permit it is refused and the flag survives; the un-narrowed mutant clears it."""
    rig = Rig(party())
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    before = rig.img.mem["WRAM"][FLAG_AT]
    assert before & (1 << 6), "the fixture entry 7 must be allocated"
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is True, why
    assert rig.img.mem["WRAM"][FLAG_AT] == before, "the executor must not touch the allocation flag"
    # the mutant: the flag-clearing write added to the box batch, permit narrowing intact -> REFUSED
    rig = Rig(party())
    options(rig)
    boxed(rig, mon)
    done, why = _pair(_lua_withdraw(rig.lua)(build(rig, box_extra("WRAM", FLAG_AT, before & ~(1 << 6))), key_of(mon)))
    assert done is None and "write refused" in why
    assert rig.img.mem["WRAM"][FLAG_AT] == before
    # the mutant with the narrowing also removed -> the flag is cleared (what the real permit prevents)
    rig = Rig(party())
    options(rig)
    boxed(rig, mon)
    edits = box_extra("WRAM", FLAG_AT, before & ~(1 << 6)) + [(BOX_NARROW, 'writes:arm("box_deposit")')]
    done, why = _pair(_lua_withdraw(rig.lua)(build(rig, edits), key_of(mon)))
    assert done is True, why
    assert rig.img.mem["WRAM"][FLAG_AT] == before & ~(1 << 6)


# ── ambiguity, reconciliation, silent failures ─────────────────────────────────────────────────────────────

def test_two_party_copies_refuse_by_name_before_any_write():
    """find_key reports an ambiguous duplicate in the PARTY; the old code tested only its first return and
    appended a THIRD copy while clearing the box."""
    mon = boxed_mon()
    rig = Rig(party(2) + [mon, mon])
    options(rig)
    boxed(rig, mon)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is None and why == "ambiguous duplicate key in the party", (done, why)
    assert rig.writes() == [] and rig.count() == 4 and entries_byte(rig) == 7
    # RED: ignore the party ambiguity -> a third copy is appended and the box copy is cleared
    rig = Rig(party(2) + [mon, mon])
    options(rig)
    boxed(rig, mon)
    bad = mutant(rig, 'if pwhy then refuse(pwhy .. " in the party") end', "-- RED: the ambiguity ignored")
    done, why = _pair(_lua_withdraw(rig.lua)(bad, key_of(mon)))
    assert rig.writes() != [] and rig.count() == 5, "RED: a third party copy was appended"
    assert count_of_key(rig, key_of(mon)) == 3


def both_places(rig, ex, fio, key):
    """Drive the real executor into the state an interrupted withdraw leaves: the count byte landed, the box half
    did not."""
    fio.flaky.budget = 71
    done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
    fio.flaky.budget = None
    assert done is None and "injected loss" in why
    assert entries_byte(rig) != 0 and count_of_key(rig, key) == 1, "fixture: the mon must be in BOTH places"


def test_a_both_places_state_reconciles_by_removing_only_the_box_copy():
    mons = party()
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(mons)
    options(rig)
    entry = boxed(rig, mon)
    flag_before = rig.img.mem["WRAM"][FLAG_FLAT[1]]
    fio = flaky_io(rig)
    ex = build(rig, io=fio)
    both_places(rig, ex, fio, key)
    rig.lua.eval(CLEAR)(rig.log["writes"])
    done, note = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is True, note
    assert note["reconciled"] is True and note["message"] == "withdraw reconciled: removed the box copy"
    assert {w["domain"] for w in rig.writes()} == {"CartRAM"}, "no party byte may be written on a reconcile"
    assert [w["addr"] for w in rig.writes()] == [box_flat(1), box_flat(1) + 0x14]
    assert rig.count() == 4 and count_of_key(rig, key) == 1 and entries_byte(rig) == 0
    assert bytes(rig.img.mem["CartRAM"][entry_flat(1, 7):entry_flat(1, 7) + 49]) == entry
    assert rig.img.mem["WRAM"][FLAG_FLAT[1]] == flag_before
    # and a third call is an idempotent DONE with no write (cx-4beeb281): the mon is only in the party now, which IS the
    # withdraw's postcondition; refusing `key not boxed` made the server re-box the partner on a duplicate delivery
    rig.lua.eval(CLEAR)(rig.log["writes"])
    done, note = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is True and note["already_in_party"] is True and rig.writes() == [] and rig.count() == 4


def test_reconcile_refuses_when_the_party_copy_differs_from_the_boxed_entry():
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(party())
    options(rig)
    boxed(rig, mon)
    fio = flaky_io(rig)
    ex = build(rig, io=fio)
    both_places(rig, ex, fio, key)
    rig.mem[PARTY_MONS + 3 * 48 + 35] = (rig.mem[PARTY_MONS + 3 * 48 + 35] or 0) ^ 0x10     # the copy was played with
    rig.lua.eval(CLEAR)(rig.log["writes"])
    done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is None and "reconcile refused" in why, (done, why)
    assert rig.writes() == [] and entries_byte(rig) == 7, "the box copy is kept"
    # RED: without the equality check the box copy is removed under a party copy that no longer matches it
    bad = build(rig, [("if not slot_is_want(praw, target) then", "if false then")], flaky_io(rig))
    _pair(_lua_withdraw(rig.lua)(bad, key))
    assert entries_byte(rig) == 0, "RED: the box copy was erased under a party copy that differs from it"


def test_a_silently_failed_entries_erase_is_refused_then_reconciled():
    """Codex finding 2: the Entries erase silently does nothing after the party append. The final read-back refuses
    (the count is already raised); once the erase works, a retry reconciles instead of refusing as already-in-party."""
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(party())
    options(rig)
    boxed(rig, mon)
    fio = flaky_io(rig)
    ex = build(rig, io=fio)
    fio.flaky.swallow = box_flat(1)
    done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is None and why == ("withdraw read-back refused: Entries byte not cleared "
                                    "(box untouched beyond that byte)"), (done, why)
    assert rig.count() == 4 and entries_byte(rig) == 7
    fio.flaky.swallow = None
    done, note = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is True and note["reconciled"] is True, (done, note)
    assert rig.count() == 4 and entries_byte(rig) == 0 and count_of_key(rig, key) == 1
    # RED: both erase read-backs removed -> the silent failure is reported as a success
    rig = Rig(party())
    options(rig)
    boxed(rig, mon)
    fio = flaky_io(rig)
    fio.flaky.swallow = box_flat(1)
    bad = build(rig, [("if find_key(after_boxes[box], key) then", "if false then"),
                      (ENTRIES_CHECK, ENTRIES_CHECK.replace("~= 0 then", "== 256 then")),
                      (FINAL_ENTRIES_CHECK, FINAL_ENTRIES_CHECK.replace("~= 0 then", "== 256 then"))], fio)
    done, why = _pair(_lua_withdraw(rig.lua)(bad, key))
    assert done is True and entries_byte(rig) == 7, "RED: reported withdrawn while the box still holds the mon"


def test_a_torn_party_write_is_refused_before_the_box_half_runs():
    """The appended slot is read back byte for byte BEFORE the box half. A bit flipped on the wire (HP low byte)
    refuses with the box copy untouched; a retry then refuses to reconcile onto the torn copy."""
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(party())
    options(rig)
    boxed(rig, mon)
    fio = flaky_io(rig)
    ex = build(rig, io=fio)
    fio.flaky.tear = PARTY_MONS + 3 * 48 + 35
    done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is None and "appended party slot differs" in why and "box copy is untouched" in why, (done, why)
    assert entries_byte(rig) == 7 and all(w["domain"] == "System Bus" for w in rig.writes())
    fio.flaky.tear = None
    done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is None and "reconcile refused" in why and entries_byte(rig) == 7
    # RED: the byte-for-byte read-back and the HP check removed -> the torn copy is accepted and the box erased
    rig = Rig(party())
    options(rig)
    boxed(rig, mon)
    fio = flaky_io(rig)
    fio.flaky.tear = PARTY_MONS + 3 * 48 + 35
    bad = build(rig, [("local function slot_is_want(raw, slot)\n",
                       "local function slot_is_want(raw, slot)\n                return true\n"
                       "            end\n            local function _unused(raw, slot)\n"),
                      ("if landed.hp ~= view.hp or landed.status ~= 0 then", "if false then")], fio)
    done, why = _pair(_lua_withdraw(rig.lua)(bad, key))
    assert done is True, why
    assert entries_byte(rig) == 0, "RED: the box copy was erased under a torn party copy"


def test_red_control_dropping_the_party_full_refusal():
    """RED: the full-party brake removed - the executor then tries a seventh record; the real one refuses first."""
    rig = Rig(party(6))
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    real_done, real_why = _pair(withdraw(rig, key_of(mon)))
    assert real_done is None and real_why.startswith("party full")
    assert rig.writes() == [] and rig.count() == 6
    bad = mutant(rig, 'if party.count >= c.PARTY_LENGTH then refuse("party full ("', 'if false then refuse("party full ("')
    done, why = _pair(_lua_withdraw(rig.lua)(bad, key_of(mon)))
    assert rig.count() <= 6, "no count past capacity may ever land"
    assert rig.writes() != [] or done is None, "the mutant must at least attempt the write the real one refused"
    assert not (done is True and rig.count() > 6)
    assert why is None or "party full" not in str(why)


def test_red_control_dropping_the_census_read_back():
    """RED: see test_a_silently_failed_entries_erase_is_refused_then_reconciled (the census + Entries read-backs
    removed there); here only the census check is removed and the Entries step check still brakes."""
    rig = Rig(party())
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    fio = flaky_io(rig)
    fio.flaky.swallow = box_flat(1)
    bad = build(rig, [("if find_key(after_boxes[box], key) then", "if false then")], fio)
    done, why = _pair(_lua_withdraw(rig.lua)(bad, key_of(mon)))
    assert done is None and "Entries byte not cleared" in why


def test_red_control_copying_the_stats_from_the_savemon():
    """RED: the record is taken from the savemon instead of rebuilt (stat tail zeroed). The byte-for-byte read-back
    of the appended slot refuses it before the box half, and the live option run proves the rebuild is the
    load-bearing part."""
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig, perfect_ivs=True)
    entry = boxed(rig, mon)
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is True, why
    assert record_bytes(rig, 3) == oracle(entry, apply_evs=False, natures_on=False, perfect_ivs=True)
    rig2 = Rig(mons)                      # the real withdraw above already moved the mon into the party
    options(rig2, perfect_ivs=True)
    boxed(rig2, mon)
    bad = mutant(rig2, '{domain = "System Bus", addr = at_rec, bytes = record},',
                 '{domain = "System Bus", addr = at_rec, bytes = '
                 '(function() local r = {0} for i = 1, 48 do r[i] = record[i] or 0 end '
                 'for i = 37, 48 do r[i] = 0 end return r end)()},')
    done, why = _pair(_lua_withdraw(rig2.lua)(bad, key_of(mon)))
    assert done is None and "appended party slot differs" in why, (done, why)
    assert record_bytes(rig2, 3) != oracle(entry, apply_evs=False, natures_on=False, perfect_ivs=True)
    assert entries_byte(rig2) == 7, "the box copy is untouched when the party half is wrong"


def test_red_control_reading_saved_options_instead_of_live():
    """RED 7: the option bytes are hard-coded neutral - the perfect-IVs run then disagrees with the oracle."""
    mons = party()
    mon = boxed_mon()
    rig = Rig(mons)
    options(rig, apply_evs=True, perfect_ivs=False)   # live: EVs ON
    entry = boxed(rig, mon)
    bad = mutant(rig, "return {apply_evs = (b & EV_OPTMASK) ~= 0, natures_on = ((a >> NATURES_OPT) & 1) == 1,",
                 "return {apply_evs = false, natures_on = ((a >> NATURES_OPT) & 1) == 1,")
    done, why = _pair(_lua_withdraw(rig.lua)(bad, key_of(mon)))
    assert done is True, why
    assert record_bytes(rig, 3) != oracle(entry, apply_evs=True, natures_on=False, perfect_ivs=False)


# ── Codex round 2: bank-2 silent erase, stale party count ─────────────────────────────────────────────────

BANKS_AT = box_flat(1) + 0x14


def test_bank2_silent_entries_failure_leaves_the_banks_byte_alone_and_reconciles():
    """A BANK-2 mon: the Banks bit selects the pokedb bank. If the Entries write silently fails and the Banks bit
    were cleared anyway, the still-non-zero pointer would be redirected into an unallocated bank-1 entry and the
    census would refuse everything. The Entries byte is therefore proved 0 BEFORE the Banks byte is touched."""
    mon = boxed_mon()
    key = key_of(mon)
    rig = Rig(party())
    options(rig)
    boxed(rig, mon, bank=2, entry=7)
    assert rig.img.mem["CartRAM"][BANKS_AT] & 1, "fixture: the boxed mon must be in bank 2"
    banks_before = rig.img.mem["CartRAM"][BANKS_AT]
    fio = flaky_io(rig)
    ex = build(rig, io=fio)
    fio.flaky.swallow = box_flat(1)
    done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
    assert done is None and why == ("withdraw read-back refused: Entries byte not cleared "
                                    "(box untouched beyond that byte)"), (done, why)
    assert rig.img.mem["CartRAM"][BANKS_AT] == banks_before, "the Banks byte must not be written"
    assert [w for w in rig.writes() if w["domain"] == "CartRAM"] == [], "only the swallowed Entries write was tried"
    keys, cwhy = rig.census_keys()
    assert keys is not None and key in keys[1], cwhy               # the census still completes
    assert rig.count() == 4
    fio.flaky.swallow = None
    done, note = _pair(_lua_withdraw(rig.lua)(ex, key))             # the retry reconciles
    assert done is True and note["reconciled"] is True, (done, note)
    assert entries_byte(rig) == 0 and not rig.img.mem["CartRAM"][BANKS_AT] & 1
    assert rig.count() == 4 and count_of_key(rig, key) == 1
    keys, cwhy = rig.census_keys()
    assert keys is not None and key not in keys[1], cwhy
    # RED: the Entries read-back removed -> the Banks bit is cleared over a still-live pointer and the census breaks
    rig = Rig(party())
    options(rig)
    boxed(rig, mon, bank=2, entry=7)
    fio = flaky_io(rig)
    fio.flaky.swallow = box_flat(1)
    bad = build(rig, [(ENTRIES_CHECK, ENTRIES_CHECK.replace("~= 0 then", "== 256 then"))], fio)
    _pair(_lua_withdraw(rig.lua)(bad, key))
    assert not rig.img.mem["CartRAM"][BANKS_AT] & 1, "RED: the Banks bit was cleared"
    assert entries_byte(rig) == 7
    keys, cwhy = rig.census_keys()
    assert keys is None, "RED: the redirected pointer must break the census"


def test_bank2_withdraw_happy_path_clears_entries_then_the_banks_bit():
    mon = boxed_mon()
    rig = Rig(party())
    options(rig)
    entry = boxed(rig, mon, bank=2, entry=7)
    flag = rig.img.mem["WRAM"][FLAG_FLAT[2]]
    done, why = _pair(withdraw(rig, key_of(mon)))
    assert done is True, why
    assert [w["addr"] for w in rig.writes() if w["domain"] == "CartRAM"] == [box_flat(1), BANKS_AT]
    assert entries_byte(rig) == 0 and not rig.img.mem["CartRAM"][BANKS_AT] & 1
    assert bytes(rig.img.mem["CartRAM"][entry_flat(2, 7):entry_flat(2, 7) + 49]) == entry
    assert rig.img.mem["WRAM"][FLAG_FLAT[2]] == flag


def test_a_party_count_that_moved_since_the_plan_refuses_the_append():
    """The append slot is planned from an early read of wPartyCount. If it moves before the emission the executor
    must refuse and write nothing (a stale slot index would overwrite a live record)."""
    mon = boxed_mon()
    key = key_of(mon)

    def run(edits, new_count):
        rig = Rig(party(4))                    # slot 3 holds a valid mon so a count of 4 still reads
        rig.mem[PARTY_COUNT] = 3
        options(rig)
        boxed(rig, mon)
        fio = flaky_io(rig)
        ex = build(rig, edits, fio)
        fio.flaky.hook_at = OPT2                                   # read after the plan, before the emission
        fio.flaky.hook = lambda: rig.mem.__setitem__(PARTY_COUNT, new_count)
        done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
        return rig, done, why

    for new_count in (4, 2):
        rig, done, why = run([], new_count)
        assert done is None and why == "party changed during withdraw", (new_count, done, why)
        assert rig.writes() == [] and entries_byte(rig) == 7
    # RED: the revalidation removed -> the stale slot is written (count 3 + 1 over a party that had 4)
    rig, done, why = run([(STALE_APPEND, "if false then refuse('x') end")], 4)
    assert rig.writes() != [], "RED: the stale-count append wrote"


def test_a_party_count_that_moved_since_the_plan_refuses_the_reconcile():
    """Reconciliation reads the party a second time; the matching slot must still be inside the CURRENT count."""
    mon = boxed_mon()
    key = key_of(mon)

    def run(edits):
        rig = Rig(party())
        options(rig)
        boxed(rig, mon)
        fio = flaky_io(rig)
        ex = build(rig, edits, fio)
        both_places(rig, ex, fio, key)
        rig.lua.eval(CLEAR)(rig.log["writes"])
        fio.flaky.hook_at = OPT2
        fio.flaky.hook = lambda: rig.mem.__setitem__(PARTY_COUNT, 3)   # the copy fell out of the party
        done, why = _pair(_lua_withdraw(rig.lua)(ex, key))
        return rig, done, why

    rig, done, why = run([])
    assert done is None and why == "party changed during withdraw", (done, why)
    assert rig.writes() == [] and entries_byte(rig) == 7
    # RED: the check removed -> the box copy is erased though the party copy is no longer inside the count
    rig, done, why = run([(STALE_RECONCILE, "")])
    assert entries_byte(rig) == 0, "RED: the box copy was removed under a stale party slot"


@pytest.mark.parametrize("classifier", [False, True], ids=["executor", "classifier"])
def test_codec_nil_result_preserves_second_return_reason(classifier):
    rig = Rig(party())
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    old = "local STATS, BASE, VARIANT, MOVE_PP = deps.stats, deps.base_stats, deps.variant_record, deps.move_pp"
    raw = build(rig, [(old, old.replace("deps.stats", "{party_from_savemon=function() return nil,'unsupported variant stats' end}"))])
    if classifier:
        ctx = rig.lua.eval("{write_id='codec-refusal',session='test',generation=1,valid=function() return true end}")
        result, plan = raw.attempt_box_write("party_mon", key_of(mon), ctx)
        assert result.outcome == "PROVED_NOT_COMPLETE" and plan.guard_refusal
        why = plan.reason
    else:
        done, why = _pair(_lua_withdraw(rig.lua)(raw, key_of(mon)))
        assert done is None
    assert why == "stat rebuild refused: unsupported variant stats"
    assert rig.writes() == [] and rig.count() == 3
