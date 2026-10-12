"""Card POL-RIVAL: the Polished Rival Team Swap (W-4) SOURCE-level writer, lua/gen2/polished_rival.lua, composed by
compose_polished (lua/gen2/entry.lua) and REFUSING by default.

Same rig as test_polished_explode_path.py (the REAL overlay ROM, a System Bus image laid out from
data/polished/polished_slink.sym, the composed frame-driven client), plus: an io whose PC register and write sink the
test controls (a write can be dropped to prove the read-back), and deps.rival_classes injected at build time.

Proved here (model only; no cartridge):
  * the write list: records, OTs, nicknames, then wOTPartyCount LAST, every byte read back; nothing at 01:D284
  * the permit is narrowed to exactly the ranges the count needs (R.ranges), on top of the whole-block bounds
  * every refusal writes ZERO bytes: wrong PC / bank, not a trainer battle, a paused/save/link context, count 0 or 7,
    unknown species, an egg, an incomplete command, a stale trainer / stale wCurOTMon, no HP on the mon sent out, a
    class outside the (owner-chosen) rival set, an EMPTY rival set (the default), an unarmed writer
  * a read-back mismatch is refused and the saved originals are restored (the count is never changed by a failed array)
  * through the composed client the swap is refused by default and from the frame-end poll (PC never at 0f:47DD there)
  * mutants of the real module, each shown RED against a named test (docstring of each)

The registered native callback is exercised below over the real ROM's mapped gate bytes (MODEL).
NOT proved here: live swap consumption, callback timing or the closing edge at 0f:480d. Configured
trainer classes in this rig are test inputs, not a policy ruling.
"""
from __future__ import annotations

import json
import random

import pytest

from server.adapters import polished_codec as pc
from tests.unit.test_polished_boxes import Image
from tests.unit.test_polished_client import _entry, _pair
from tests.unit.test_polished_explode_path import (
    FRAME_BANK,
    HARNESS_HOOKS,
    LUA,
    OVERRIDE,
    ROOT,
    SITES,
    BattleRig,
    addr,
    enter_battle,
    lupa,
    mutate,
    overlay,
)
from tests.unit.test_polished_write_path import SYM, live_mon, party, seal_save, sysbus

RECORD, NAME, NICK = 48, 11, 11
RIVAL_PC = SITES["rival_gate"][0]                      # 0x47DD
RIVAL_CLASS, RIVAL_ID = 0x1B, 3                         # a TEST input: which classes are rivals is an OWNER decision
SENTINEL, HERB = 0xEE, 0x5A
COUNT, MONS, OTS, NICKS, END, HERB_AT = (addr("wOTPartyCount"), addr("wOTPartyMons"), addr("wOTPartyMonOTs"),
                                         addr("wOTPartyMonNicknames"), addr("wOTPartyDataEnd"),
                                         addr("wMirrorHerbPendingBoosts"))
RIVAL = (LUA / "polished_rival.lua").read_text(encoding="utf-8")

CONTROL = """
return function(io, mem, log, st)
    local original = io.write_u8
    io.register = function() return st.pc end
    io.write_u8 = function(a, v, d)
        if d ~= nil and d ~= "System Bus" then return original(a, v, d) end
        log.writes[#log.writes + 1] = {addr = a, domain = "System Bus", value = v}
        if st.drop[a] then return end
        if st.late[a] and #log.writes > st.late_after then return end    -- lost only AFTER N writes: the rollback's own
        mem[a] = v
    end
end
"""


class RivalRig(BattleRig):
    """BattleRig with deps.rival_classes at build time, a settable PC register and a write sink that can drop a byte."""

    def __init__(self, mons, classes=(RIVAL_CLASS,), pc_=RIVAL_PC, overrides=None):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.mem = self.lua.table_from(sysbus(mons))
        self.img = seal_save(Image())
        deps, self.io, self.log = self.lua.execute(HARNESS_HOOKS.replace("ROOTDIR", json.dumps(ROOT)))(
            overlay()[1], self.mem, self.img)
        if classes is not None:        # None = deps.rival_classes ABSENT: the module default (RIVAL0/1/2)
            deps["rival_classes"] = self.lua.table_from(list(classes))
        if overrides:
            self.lua.globals().SLINK_OVERRIDES = self.lua.table_from(overrides)
            self.lua.execute(OVERRIDE)
        self.state = self.lua.table_from({"pc": pc_, "drop": self.lua.table_from({}),
                                          "late": self.lua.table_from({}), "late_after": 0})
        self.parts, why = _pair(_entry(self.lua).build(deps))
        assert why is None, why
        self.client = self.parts.client
        self.lua.globals().SLINK_PARTS = self.parts
        self.lua.execute(CONTROL)(self.io, self.mem, self.log, self.state)
        self.client.start(self.client)
        self.rival = self.parts.battle.rival

    def drop(self, address):
        self.state["drop"][address] = True

    def drop_late(self, address, after):
        self.state["late"][address] = True
        self.state["late_after"] = after

    def set_pc(self, value):
        self.state["pc"] = value

    def fill_enemy(self):
        """An OLD enemy party image: count 3, every record/OT/nick byte a sentinel, the Mirror Herb byte a marker."""
        self.mem[COUNT] = 3
        self.mem[HERB_AT] = HERB
        for a in range(MONS, END):
            self.mem[a] = SENTINEL

    def block(self):
        return {a: (self.mem[a] or 0) for a in [COUNT, HERB_AT, *range(MONS, END)]}


def py_party(count, hp=200, base=150):
    out = []
    for i in range(count):
        mon = live_mon(random.Random(900 + i), base + i, hp=hp, ot="RIVAL", nick=f"FOE{i}")
        out.append({"record": list(pc.encode_party_mon(mon)), "ot": list(pc.encode_text("RIVAL", 8) + bytes(3)),
                    "nick": list(pc.encode_text(f"FOE{i}", 11))})
    return out


def to_lua(lua, mons):
    return lua.table_from([lua.table_from({k: lua.table_from(v) for k, v in m.items()}) for m in mons])


def lcall(rig, fn, *args):
    return _pair(rig.lua.eval("function(f, ...) return pcall(f, ...) end")(fn, *args))


_SHARED = []


def ready(count_in_ram=0, *, classes=(RIVAL_CLASS,), cur=0, pc_=RIVAL_PC, overrides=None, mode=2, fresh=False):
    """A rig at the real gate state. A build costs ~10 s, so the unmutated rig is built once and RESET per call
    (classes, PC, dropped bytes, write log, battle bytes); mutants and client tests build their own (fresh=True)."""
    mons = party()
    if overrides or fresh:
        rig = RivalRig(mons, classes=classes, pc_=pc_, overrides=overrides)
        assert rig.arm_writes(), "the client never enabled its writes"
    else:
        if not _SHARED:
            _SHARED.append(RivalRig(mons))
            assert _SHARED[0].arm_writes(), "the client never enabled its writes"
        rig = _SHARED[0]
        rig.rival.writes.disarm(rig.rival.writes)
        rig.lua.eval("function(l) for k in pairs(l.writes) do l.writes[k] = nil end end")(rig.log)
        rig.state["drop"] = rig.lua.table_from({})
        rig.state["late"] = rig.lua.table_from({})
        rig.state["late_after"] = 0
        rig.set_pc(pc_)
        rig.rival.set_classes(rig.rival, rig.lua.table_from(list(classes)))
        for label in ("wLinkMode", "wGameLogicPaused"):
            rig.put(label, 0)
    enter_battle(rig, mons, mode=mode)
    rig.put("hBattleTurn", 1)  # native enemy send-in; unrelated tests keep a valid bound context
    rig.put("wOtherTrainerClass", RIVAL_CLASS)
    rig.put("wOtherTrainerID", RIVAL_ID)
    rig.put("wCurOTMon", cur)
    rig.put("wCurPartyMon", cur)
    rig.fill_enemy()
    return rig


def swap(rig, mons=None, *, cur=0, arm=True, link=0, **ctx):
    """arm + write_enemy_party through the composed facade, as client.rival_tick does; (ok, why); always disarmed."""
    mons = py_party(2) if mons is None else mons
    writes = rig.rival.writes
    ok, why = (lcall(rig, writes.arm, writes, "rival_swap") if arm else (True, None))
    if ok:
        table = rig.lua.table_from({"link_mode": link, "cur_ot_mon": cur,
                                    "trainer_id": RIVAL_CLASS * 256 + RIVAL_ID, **ctx})
        ok, why = lcall(rig, writes.write_enemy_party, writes, to_lua(rig.lua, mons), table)
    writes.disarm(writes)
    return ok, why


def refused(rig, why_part, mons=None, **kwargs):
    before = rig.block()
    ok, why = swap(rig, mons, **kwargs)
    assert ok is False and why_part in why, why
    assert rig.writes() == [] and rig.block() == before and rig.mem[HERB_AT] == HERB
    assert rig.rival.writes.armed is None


# ── the happy path: exact bytes, exact order ────────────────────────────────────────────────────────────────

def test_the_swap_writes_the_exact_bytes_in_order_with_the_count_last():
    rig = ready()
    mons = py_party(2)
    ok, why = swap(rig, mons)
    assert ok is True, why
    want_records = [b for m in mons for b in m["record"]]
    want_ots = [b for m in mons for b in m["ot"]]
    want_nicks = [b for m in mons for b in m["nick"]]
    assert [rig.mem[MONS + i] for i in range(2 * RECORD)] == want_records
    assert [rig.mem[OTS + i] for i in range(2 * NAME)] == want_ots
    assert [rig.mem[NICKS + i] for i in range(2 * NICK)] == want_nicks
    assert rig.mem[COUNT] == 2 and rig.mem[HERB_AT] == HERB
    # nothing past the narrowed ranges moved: mon 3.. of each array keeps the old image
    assert all(rig.mem[MONS + i] == SENTINEL for i in range(2 * RECORD, 6 * RECORD))
    assert all(rig.mem[OTS + i] == SENTINEL for i in range(2 * NAME, 6 * NAME))
    assert all(rig.mem[NICKS + i] == SENTINEL for i in range(2 * NICK, 6 * NICK))
    log = rig.writes()
    order = [w["addr"] for w in log]
    assert len(log) == 2 * (RECORD + NAME + NICK) + 1
    assert order[:2 * RECORD] == list(range(MONS, MONS + 2 * RECORD))                     # 1. records
    assert order[2 * RECORD:2 * RECORD + 2 * NAME] == list(range(OTS, OTS + 2 * NAME))    # 2. OTs
    assert order[2 * RECORD + 2 * NAME:-1] == list(range(NICKS, NICKS + 2 * NICK))        # 3. nicknames
    assert order[-1] == COUNT and log[-1]["value"] == 2, "wOTPartyCount must be written LAST"
    assert HERB_AT not in order and all(w["domain"] == "System Bus" for w in log)
    assert rig.rival.writes.armed is None


def test_the_swap_lands_for_one_to_six_mons_and_never_touches_the_herb_byte():
    for count in (1, 3, 6):
        rig = ready()
        ok, why = swap(rig, py_party(count))
        assert ok is True, (count, why)
        assert rig.mem[COUNT] == count and rig.mem[HERB_AT] == HERB
        assert HERB_AT not in [w["addr"] for w in rig.writes()]


def test_the_ranges_are_exactly_what_the_count_needs_and_never_the_herb_gap():
    rig = ready()
    for count in range(1, 7):
        got = [tuple(r.values()) for r in rig.rival.ranges(count).values()]
        assert got == [(MONS, count * RECORD), (OTS, count * NAME), (NICKS, count * NICK), (COUNT, 1)]
        assert all(not (a < addr("wOTPartyMons") and a + n > HERB_AT) for a, n in got)


# ── every refusal writes zero bytes ─────────────────────────────────────────────────────────────────────────

def test_a_wrong_pc_or_bank_refuses():
    refused(ready(pc_=RIVAL_PC + 3), "not at the SendInUserPkmn rival gate")      # mid-instruction
    refused(ready(pc_=0x416A), "not at the SendInUserPkmn rival gate")            # the explode hold, not this one
    refused(ready(pc_=0), "not at the SendInUserPkmn rival gate")                 # a frame end: where the client polls
    rig = ready()
    rig.put("hROMBank", 0x25)
    refused(rig, "hROMBank $25")


def test_a_wild_battle_and_a_paused_or_linked_context_refuse():
    refused(ready(mode=1), "not a trainer battle")
    rig = ready()
    rig.put("wBattleMode", 0)
    refused(rig, "not in a battle")
    rig = ready()
    rig.put("wGameLogicPaused", 1)
    refused(rig, "native save running")                                            # writes paused
    rig = ready()
    rig.put("wLinkMode", 1)
    refused(rig, "link cable active")
    refused(ready(), "linked or unknown battle context", link=1)


def test_an_empty_rival_set_refuses_and_a_foreign_class_refuses():
    refused(ready(classes=()), "no rival trainer classes configured")             # the DEFAULT: an owner decision
    refused(ready(classes=(0x1C,)), "is not a configured rival class")
    rig = ready(classes=())
    rig.rival.set_classes(rig.rival, rig.lua.table_from([RIVAL_CLASS]))          # the later-config path
    ok, why = swap(rig)
    assert ok is True, why


@pytest.mark.parametrize("count,part", [(0, "enemy party count"), (7, "enemy party count")])
def test_a_count_outside_one_to_six_refuses(count, part):
    refused(ready(), part, py_party(count) if count else [])


def test_a_party_the_engine_could_not_send_out_refuses():
    refused(ready(), "outside the new party", cur=2)                              # wCurOTMon beyond the NEW count
    rig = ready(cur=2)
    refused(rig, "outside the new party", py_party(2), cur=2)
    mons = py_party(2)
    mons[0]["record"][34] = mons[0]["record"][35] = 0                              # HP 0 on the mon sent out next
    refused(ready(), "has no HP", mons)


def test_unknown_species_and_an_egg_refuse():
    mons = py_party(2)
    mons[1]["record"][0] = 0                                                       # no species
    refused(ready(), "unknown species", mons)
    mons = py_party(2)
    mons[1]["record"][0], mons[1]["record"][21] = 0xFF, mons[1]["record"][21] | 0x20      # 9-bit species 511
    refused(ready(), "unknown species 511", mons)
    mons = py_party(2)
    mons[0]["record"][21] |= 0x40                                                  # IS_EGG
    refused(ready(), "an egg cannot battle", mons)
    mons = py_party(2)
    mons[0]["record"][31] = 101                                                    # level
    refused(ready(), "level out of range", mons)


@pytest.mark.parametrize("break_it,part", [
    (lambda m: m[0]["record"].pop(), "record must be 48 bytes"),
    (lambda m: m[1]["ot"].pop(), "ot must be 11 bytes"),
    (lambda m: m[1].pop("nick"), "nick must be 11 bytes"),
    (lambda m: m[0]["record"].__setitem__(5, 300), "byte out of range"),
])
def test_an_incomplete_command_refuses(break_it, part):
    mons = py_party(2)
    break_it(mons)
    refused(ready(), part, mons)


def test_a_stale_battle_refuses():
    refused(ready(), "stale battle", trainer_id=RIVAL_CLASS * 256 + RIVAL_ID + 1)   # another fight of the same class
    refused(ready(), "stale battle", trainer_id=0x1C00 + RIVAL_ID)
    rig = ready()
    ok, why = swap(rig, trainer_id=RIVAL_CLASS * 256 + RIVAL_ID)                   # the fight it was announced for lands
    assert ok is True, why
    rig = ready()
    rig.put("wCurOTMon", 1)                                                        # the byte moved on: ctx says 0
    refused(rig, "stale snapshot")
    rig = ready()
    rig.put("wCurPartyMon", 1)
    refused(rig, "stale snapshot")


def test_an_unarmed_writer_refuses():
    refused(ready(), "not armed", arm=False)


# ── the read-back ────────────────────────────────────────────────────────────────────────────────────────────

def test_a_dropped_record_byte_is_refused_and_the_old_party_is_restored():
    rig = ready()
    before = rig.block()
    rig.drop(MONS + 5)                                                             # the sink loses one record byte
    ok, why = swap(rig, py_party(2))
    assert ok is False and "read-back mismatch" in why and f"${MONS + 5:04X}" in why
    assert rig.mem[COUNT] == 3, "a failed array must never change wOTPartyCount"
    assert rig.block() == before, "the saved originals were not restored"
    assert rig.rival.writes.armed is None


def test_a_dropped_count_write_is_refused_and_the_arrays_are_restored():
    rig = ready()
    before = rig.block()
    rig.drop(COUNT)
    ok, why = swap(rig, py_party(2))
    assert ok is False and "read-back mismatch" in why and f"${COUNT:04X}" in why
    assert rig.block() == before, "the arrays were left half-replaced"


# ── through the composed client ──────────────────────────────────────────────────────────────────────────────

TID = RIVAL_CLASS * 256 + RIVAL_ID


def client_swap(rig):
    enter_battle(rig, party(), mode=2)
    rig.put("wOtherTrainerClass", RIVAL_CLASS)
    rig.put("wOtherTrainerID", RIVAL_ID)
    rig.put("wCurOTMon", 0xFF)
    rig.frame(3)                                                                   # announced
    blobs = ["".join(f"{b:02x}" for b in m["record"] + m["ot"] + m["nick"]) for m in py_party(2)]
    rig.client.handle_command(rig.client, rig.lua.table_from({"cmd": "replace_rival_team", "trainer_id": TID,
                                                              "blobs_hex": rig.lua.table_from(blobs)}))
    rig.frame(2)
    return [m["error"] for m in rig.sent("rival_team_replaced")]


def test_native_composition_parks_at_ff_and_keeps_the_frame_end_deadline():
    rig = ready(pc_=0, fresh=True)
    before = rig.block()
    assert client_swap(rig) == []
    assert rig.client.rival.pending is not None and rig.block() == before
    rig.client.frame += 241
    rig.client.rival_tick(rig.client)
    assert rig.sent("rival_team_replaced")[-1]["error"] == "late_reply"
    assert rig.writes() == []


def test_an_explicitly_empty_rival_set_still_refuses():
    """An explicit deps.rival_classes (even empty) overrides the default; only an ABSENT one takes RIVAL0/1/2."""
    rig = ready(classes=(), fresh=True)
    c = rig.parts.profile.constants
    assert (c.PARTYMON_STRUCT_LENGTH, c.MON_HP, c.MON_SPECIES) == (48, 34, 0)
    errors = client_swap(rig)
    assert len(errors) == 1 and "no rival trainer classes configured" in errors[0]


def _default_class_rig(trainer_class, overrides=None):
    """A fresh composition with deps.rival_classes ABSENT, fighting `trainer_class` (the real module unless `overrides`)."""
    rig = ready(classes=None, fresh=True, overrides=overrides)
    rig.put("wOtherTrainerClass", trainer_class)
    return rig


def test_the_default_rival_set_is_rival0_1_2_matching_the_pack_by_name():
    """Owner ruling 2026-10-08. R.DEFAULT_RIVAL_CLASSES is pinned in Lua (the profile has no trainer-class constants), so
    it is held equal BY NAME to the pack the server computes its ids from; LYRA1/LYRA2 ($1E/$1F) are refused."""
    import re
    from pathlib import Path

    pack = json.loads((Path(ROOT) / "data/games/polished_crystal/trainers.json").read_text(encoding="utf-8"))
    classes = pack["rival_classes"]
    assert [classes[n] for n in ("RIVAL0", "RIVAL1", "RIVAL2")] == [0x1B, 0x1C, 0x1D]
    assert [classes[n] for n in ("LYRA1", "LYRA2")] == [0x1E, 0x1F]
    declared = re.search(r"R\.DEFAULT_RIVAL_CLASSES = \{([^}]*)\}", RIVAL).group(1)
    assert sorted(int(x, 16) for x in declared.replace(" ", "").split(",")) == sorted(
        classes[n] for n in ("RIVAL0", "RIVAL1", "RIVAL2"))
    rig = _default_class_rig(classes["RIVAL0"])
    assert rig.rival.writes.rival_gate_enabled(rig.rival.writes) is True       # the native gate is enabled by default
    for name in ("RIVAL0", "RIVAL1", "RIVAL2"):
        rig = _default_class_rig(classes[name])
        ok, why = swap(rig, trainer_id=classes[name] * 256 + RIVAL_ID)
        assert ok is True, (name, why)
    for name in ("LYRA1", "LYRA2"):
        refused(_default_class_rig(classes[name]), "is not a configured rival class",
                trainer_id=classes[name] * 256 + RIVAL_ID)


def test_red_control_an_empty_default_fails_the_default_test():
    """Mutant: the pre-ruling default (none). The enabling assertions above must go red under it."""
    rig = _default_class_rig(0x1B, mutant(mutate(
        RIVAL, "self:set_classes(deps.rival_classes == nil and R.DEFAULT_RIVAL_CLASSES or deps.rival_classes)",
        "self:set_classes(deps.rival_classes)")))
    assert rig.rival.writes.rival_gate_enabled(rig.rival.writes) is False
    ok, why = swap(rig)
    assert ok is False and "no rival trainer classes configured" in why


def test_red_control_including_lyra_in_the_default_fails_the_exclusion():
    """Mutant: LYRA1/LYRA2 added to the default. The $1E/$1F refusal above must go red under it."""
    rig = _default_class_rig(0x1E, mutant(mutate(
        RIVAL, "R.DEFAULT_RIVAL_CLASSES = {0x1B, 0x1C, 0x1D}", "R.DEFAULT_RIVAL_CLASSES = {0x1B, 0x1C, 0x1D, 0x1E, 0x1F}")))
    ok, why = swap(rig, trainer_id=0x1E * 256 + RIVAL_ID)
    assert ok is True, why                     # the mutant accepts a LYRA fight: the real default refuses it


def test_the_constants_are_derived_from_the_party_struct_not_invented():
    profile = json.loads((LUA.parent.parent / "data/games/polished_crystal/profile.json").read_text(encoding="utf-8"))
    party_struct = profile["titles"]["polished"]["structs"]["party"]
    sym_party = SYM["wPartyMons"]
    assert (party_struct["End"], party_struct["HP"], party_struct["Species"]) == (48, 34, 0)
    assert SYM["wPartyMonOTs"][1] - sym_party[1] == 6 * 48 and SYM["wOTPartyMonOTs"][1] - SYM["wOTPartyMons"][1] == 6 * 48
    assert FRAME_BANK == 0x0F


# ── MUTANTS: each brake removed from the REAL module, same rig, same image ──────────────────────────────────

def mutant(source):
    return {"lua/gen2/polished_rival.lua": source}


def swap_blocks(source):
    a = "pcall(function() gate:write_batch(p.arrays); verify(p.arrays) end)"
    b = "pcall(function() gate:write_batch(p.last); verify(p.last) end)"
    assert source.count(a) == 1 and source.count(b) == 1
    return source.replace(a, "@@").replace(b, a).replace("@@", b)


def lands_in_order(rig):
    ok, why = swap(rig, py_party(2))
    return ok is True and rig.writes()[-1]["addr"] == COUNT


def test_mutant_1_count_first_is_caught_by_the_write_order():
    assert lands_in_order(ready())                                                 # the composed control
    rig = ready(overrides=mutant(swap_blocks(RIVAL)))
    assert not lands_in_order(rig), "the count-last order is not load-bearing"


def test_mutant_2_a_count_write_that_reaches_d284_is_refused_by_the_permit():
    source = mutate(RIVAL, "last = {{domain = \"System Bus\", addr = list[4][1], bytes = {count}}}}",
                    "last = {{domain = \"System Bus\", addr = list[4][1], bytes = {count, 0}}}}")
    rig = ready(overrides=mutant(source))
    ok, why = swap(rig, py_party(2))
    assert ok is False and "write refused" in why, "a D284 write was not refused"
    assert rig.mem[HERB_AT] == HERB and rig.mem[COUNT] == 3                        # restored: the count never moved
    assert ready() and lands_in_order(ready())                                     # the control lands


def test_mutant_3_without_the_pc_gate_a_wrong_pc_lands():
    source = mutate(RIVAL, "assert(bank == site.bank and pc == site.pc, string.format(",
                    "assert(true or (bank == site.bank and pc == site.pc), string.format(")
    rig = ready(pc_=0, overrides=mutant(source))
    ok, why = swap(rig)
    assert ok is True and rig.mem[COUNT] == 2, "the PC gate is not load-bearing"
    rig = ready(pc_=0)
    assert swap(rig)[0] is False                                                   # the composed control refuses


def test_mutant_4_without_the_array_read_back_a_dropped_byte_goes_unnoticed():
    source = mutate(RIVAL, "gate:write_batch(p.arrays); verify(p.arrays) end)", "gate:write_batch(p.arrays) end)")
    rig = ready(overrides=mutant(source))
    rig.drop(MONS + 5)
    ok, why = swap(rig, py_party(2))
    assert ok is True and rig.mem[MONS + 5] == SENTINEL, "the array read-back is not load-bearing"


def test_mutant_4b_without_the_count_read_back_a_dropped_count_goes_unnoticed():
    source = mutate(RIVAL, "gate:write_batch(p.last); verify(p.last) end)", "gate:write_batch(p.last) end)")
    rig = ready(overrides=mutant(source))
    rig.drop(COUNT)
    ok, why = swap(rig, py_party(2))
    assert ok is True and rig.mem[COUNT] == 3, "the count read-back is not load-bearing"


WIDEN = ("return {{at.wOTPartyMons, count * NM}, {at.wOTPartyMonOTs, count * NAME},\n"
         "                {at.wOTPartyMonNicknames, count * NICK}, {at.wOTPartyCount, 1}}",
         "return {{at.wOTPartyMons, P * NM}, {at.wOTPartyMonOTs, P * NAME},\n"
         "                {at.wOTPartyMonNicknames, P * NICK}, {at.wOTPartyCount, 1}}")
DRIFT = ("{domain = \"System Bus\", addr = list[3][1], bytes = nicks}",
         "{domain = \"System Bus\", addr = list[3][1], bytes = (function() local t = {} for i = 1, #nicks do t[i] = "
         "nicks[i] end for i = 1, NICK do t[#t + 1] = 0 end return t end)()}")


def test_mutant_5_widened_permits_are_caught_by_the_exact_ranges():
    rig = ready(overrides=mutant(mutate(RIVAL, *WIDEN)))
    got = [tuple(r.values()) for r in rig.rival.ranges(2).values()]
    assert got != [(MONS, 2 * RECORD), (OTS, 2 * NAME), (NICKS, 2 * NICK), (COUNT, 1)], \
        "the narrowed ranges are not load-bearing"


def test_mutant_5b_a_drifted_write_is_stopped_only_by_the_narrowed_permit():
    drifted = mutate(RIVAL, *DRIFT)
    rig = ready(overrides=mutant(drifted))
    before = rig.block()
    ok, why = swap(rig, py_party(2))                                               # narrowed: the extra 11 bytes refuse
    assert ok is False and "outside the rival_swap window" in why and rig.writes() == [] and rig.block() == before
    rig = ready(overrides=mutant(mutate(drifted, *WIDEN)))                         # drifted AND widened
    ok, why = swap(rig, py_party(2))
    assert ok is True and rig.mem[NICKS + 2 * NICK] == 0, "the narrowed allow is not load-bearing"


def test_mutant_6_a_vanilla_species_list_write_is_refused_by_the_bounds():
    source = mutate(RIVAL, "arrays = {{domain = \"System Bus\", addr = list[1][1], bytes = records},",
                    "arrays = {{domain = \"System Bus\", addr = at.wOTPartyCount + 1, bytes = {records[1], 0xFF}},\n"
                    "                          {domain = \"System Bus\", addr = list[1][1], bytes = records},")
    rig = ready(overrides=mutant(source))
    before = rig.block()
    ok, why = swap(rig, py_party(2))
    assert ok is False and "write refused" in why and rig.writes() == [] and rig.block() == before
    assert rig.mem[HERB_AT] == HERB


# ── review cx-c01c159e: raw species domain, a verified rollback, a leaked permit ─────────────────────────────

def test_a_variant_record_index_is_not_a_raw_species():
    """Raw species 292 (species byte $24 + the EXTSPECIES bit) is a VARIANT BaseData record index, not a species: the
    engine's raw species end at 291 ($123). The species check must use the species table, not the base-stats table that
    also carries the variant records."""
    mons = py_party(2)
    mons[0]["record"][0] = 0x24
    mons[0]["record"][21] |= 0x20
    refused(ready(), "unknown species 292", mons)
    ok = py_party(2)
    ok[0]["record"][0] = 0x24                                                      # species 36 (no ext bit): known
    ok[0]["record"][21] &= ~0x20 & 0xFF
    assert swap(ready(), ok)[0] is True


def test_a_failed_rollback_is_reported_as_a_torn_party_not_hidden_behind_the_first_error():
    rig = ready()
    before = rig.block()
    rig.drop(MONS + 5)                                                             # the forward read-back fails ...
    rig.drop_late(OTS, after=2 * RECORD + 2 * NAME + 2 * NICK)                     # ... and the rollback loses a byte it must restore
    ok, why = swap(rig, py_party(2))
    assert ok is False and "TORN ENEMY PARTY" in why and "rollback FAILED" in why and "read-back mismatch" in why, why
    assert rig.block() != before and rig.mem[COUNT] == 3                           # honest: the count was never touched
    assert rig.mem[HERB_AT] == HERB


def test_a_refused_write_leaves_the_permit_disarmed_without_the_helpers_disarm():
    rig = ready()
    writes = rig.rival.writes
    rig.drop(MONS + 5)
    assert lcall(rig, writes.arm, writes, "rival_swap")[0] is True
    table = rig.lua.table_from({"link_mode": 0, "cur_ot_mon": 0, "trainer_id": TID})
    ok, why = lcall(rig, writes.write_enemy_party, writes, to_lua(rig.lua, py_party(2)), table)
    assert ok is False and "read-back mismatch" in why
    assert writes.armed is None, "the writer left its permit armed after a refusal"
    writes.disarm(writes)


def test_mutant_5_an_ignored_rollback_result_hides_a_torn_party():
    source = mutate(RIVAL, "if restored then error(why, 0) end", "do error(why, 0) end")
    rig = ready(overrides=mutant(source))
    rig.drop(MONS + 5)
    rig.drop_late(OTS, after=2 * RECORD + 2 * NAME + 2 * NICK)
    ok, why = swap(rig, py_party(2))
    assert ok is False and "TORN ENEMY PARTY" not in why, "the rollback verification is not load-bearing"



@pytest.mark.parametrize("case,reason", [
    ("player", "not an enemy send-in"),
    ("wild", "not a trainer battle"),
    ("missing", "bound trainer identity required"),
    ("stale", "stale battle"),
], ids=["player-side", "wild-at-gate", "missing-id", "stale-id"])
def test_operation_predicate_refuses_without_any_write(case, reason):
    rig = ready()
    ctx = {}
    if case == "player":
        rig.put("hBattleTurn", 0)
    elif case == "wild":
        rig.put("wBattleMode", 1)
    elif case == "missing":
        ctx["trainer_id"] = None  # omitted by lupa, explicitly overriding the valid helper default
    else:
        ctx["trainer_id"] = TID + 1
    refused(rig, reason, **ctx)


def test_enemy_send_in_accepts_a_configured_nonfixture_rival_identity():
    rig = ready(classes=(0x1E,))
    rig.put("wOtherTrainerClass", 0x1E)
    rig.put("wOtherTrainerID", 7)
    ok, why = swap(rig, trainer_id=0x1E07)
    assert ok is True, why
    assert rig.mem[COUNT] == 2 and len(rig.writes()) == 141


def test_enemy_side_address_is_the_existing_generated_symbol():
    rig = ready()
    assert rig.parts.profile.hram.hBattleTurn == SYM["hBattleTurn"][1] == 0xFFD1


@pytest.mark.parametrize("guard", ["side", "identity"], ids=["drop-side", "optional-id"])
def test_operation_predicate_source_mutants_turn_refusal_red(guard):
    if guard == "side":
        anchor = 'assert(io.read_u8(profile.hram.hBattleTurn, "System Bus") == 1, "not an enemy send-in")'
        replacement = 'assert(true, "not an enemy send-in")'
        reason, ctx = "not an enemy send-in", {}
    else:
        anchor = ('assert(ctx.trainer_id ~= nil, "bound trainer identity required")\n'
                  '        assert(ctx.trainer_id == class * 256 + id, "stale battle: the trainer being fought differs")')
        replacement = 'assert(ctx.trainer_id == nil or ctx.trainer_id == class * 256 + id, "stale battle: the trainer being fought differs")'
        reason, ctx = "bound trainer identity required", {"trainer_id": None}
    assert RIVAL.count(anchor) == 1
    rig = ready(overrides=mutant(RIVAL.replace(anchor, replacement)))
    if guard == "side":
        rig.put("hBattleTurn", 0)
    with pytest.raises(AssertionError):
        refused(rig, reason, **ctx)
    assert rig.mem[COUNT] == 2 and len(rig.writes()) == 141  # wrong acceptance, not an unrelated error


def test_rival_module_compiles_in_lua55():
    from lupa.lua55 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    assert lua.eval("function(s) return assert(load(s)) ~= nil end")(RIVAL)


@pytest.mark.parametrize("drift", [False, True], ids=["bound-id", "ram-drift-after-read"])
def test_native_caller_forwards_bound_id_and_refuses_ram_drift(drift):
    rig = ready(pc_=RIVAL_PC, fresh=True)
    captured = []
    rig.lua.globals().caller_capture = lambda tid: captured.append(tid)
    rig.lua.execute("""return function(writes,mem,id_addr,drift)
        local original,arm = writes.write_enemy_party,writes.arm
        writes.arm=function(self,...)
            local result=arm(self,...)
            if drift then mem[id_addr]=mem[id_addr]+1 end
            return result
        end
        writes.write_enemy_party=function(self,mons,ctx)
            caller_capture(ctx.trainer_id)
            return original(self,mons,ctx)
        end
    end""")(rig.rival.writes, rig.mem, addr("wOtherTrainerID"), drift)
    pending_native_swap(rig)
    before = rig.block()
    fire_native_rival(rig)
    rig.frame(1)
    errors = [m.get("error") for m in rig.sent("rival_team_replaced")]
    assert captured == [TID]
    if drift:
        assert "stale battle" in errors[0] and rig.block() == before and rig.writes() == []
    else:
        assert errors == [None] and rig.mem[COUNT] == 2 and len(rig.writes()) == 141


def test_drop_bound_trainer_id_caller_mutant_turns_spy_red():
    from pathlib import Path

    source = (Path(ROOT) / "lua/gen2/client.lua").read_text()
    anchor = "ctx.trainer_id = r.trainer_id"
    assert source.count(anchor) == 1
    rig = ready(
        pc_=RIVAL_PC,
        overrides={"lua/gen2/client.lua": source.replace(anchor, "ctx.trainer_id = nil")},
    )
    captured = []
    rig.lua.globals().caller_capture = lambda tid: captured.append(tid)
    rig.lua.execute("""return function(writes)
        local original=writes.write_enemy_party
        writes.write_enemy_party=function(self,mons,ctx)
            caller_capture(ctx.trainer_id)
            return original(self,mons,ctx)
        end
    end""")(rig.rival.writes)
    pending_native_swap(rig)
    before = rig.block()
    fire_native_rival(rig)
    rig.frame(1)
    errors = [m.get("error") for m in rig.sent("rival_team_replaced")]
    with pytest.raises(AssertionError):
        assert captured == [TID]
    assert captured == [None] and "bound trainer identity required" in errors[0]
    assert rig.block() == before and rig.writes() == []


RIVAL_HOOK = "SLink-gen2-polished:rival_swap_gate"


def pending_native_swap(rig):
    enter_battle(rig, party(), mode=2)
    rig.put("hBattleTurn", 1)
    rig.put("wOtherTrainerClass", RIVAL_CLASS)
    rig.put("wOtherTrainerID", RIVAL_ID)
    rig.put("wCurOTMon", 0xFF)
    rig.frame(3)
    blobs = ["".join(f"{b:02x}" for b in m["record"] + m["ot"] + m["nick"]) for m in py_party(2)]
    rig.client.handle_command(
        rig.client,
        rig.lua.table_from(
            {"cmd": "replace_rival_team", "trainer_id": TID, "blobs_hex": rig.lua.table_from(blobs)}
        ),
    )
    rig.frame(1)  # receipt is parked across the old FF frame-end opportunity
    assert rig.client.rival.pending is not None
    rig.put("wCurOTMon", 0)
    rig.put("wCurPartyMon", 0)
    # Map the pinned real ROM bytes into the rig's System Bus at the callback.
    rom = overlay()[1]
    for i in range(3):
        rig.mem[RIVAL_PC + i] = rom[FRAME_BANK * 0x4000 + RIVAL_PC - 0x4000 + i]
    return rig


def fire_native_rival(rig):
    hook = rig.log.hook_fn[RIVAL_HOOK]
    assert hook is not None, "composed rival hook missing"
    hook()


def test_native_rival_callback_lands_exact_image_once():
    rig = pending_native_swap(ready(fresh=True))
    fire_native_rival(rig)
    assert rig.mem[COUNT] == 2 and len(rig.writes()) == 141
    assert rig.sent("rival_team_replaced") == []  # callback never sends on the network
    for start, field in [(MONS, "record"), (OTS, "ot"), (NICKS, "nick")]:
        want = [b for m in py_party(2) for b in m[field]]
        assert [rig.mem[start + i] for i in range(len(want))] == want
    before = rig.writes()
    fire_native_rival(rig)  # same callback visit, before frame-end reply flushing
    assert rig.writes() == before
    rig.frame(1)
    replies = rig.sent("rival_team_replaced")
    assert len(replies) == 1 and replies[0].get("error") is None


@pytest.mark.parametrize(
    "case,reason",
    [
        ("wild", "rival callback: not trainer"),
        ("player", "rival callback: not enemy"),
        ("stale", "rival callback: stale trainer"),
        ("unarmed", "rival callback: writes disabled"),
    ],
    ids=["wild", "player", "stale-id", "unarmed"],
)
def test_native_rival_callback_refuses_before_write(case, reason):
    rig = pending_native_swap(ready(fresh=True))
    if case == "wild":
        rig.put("wBattleMode", 1)
    elif case == "player":
        rig.put("hBattleTurn", 0)
    elif case == "stale":
        rig.put("wOtherTrainerID", RIVAL_ID + 1)
    else:
        rig.client.writes_enabled = False
    before = rig.block()
    fire_native_rival(rig)
    assert rig.block() == before and rig.writes() == []
    rig.frame(1)
    assert reason in rig.sent("rival_team_replaced")[0]["error"]


def test_second_callback_mutant_repeats_real_writes():
    source = (LUA / "client.lua").read_text()
    old = "if not r or r.gate_used then return nil end"
    assert source.count(old) == 1
    rig = pending_native_swap(
        ready(
            overrides={"lua/gen2/client.lua": source.replace(old, "if not r then return nil end")}
        )
    )
    fire_native_rival(rig)
    before = rig.writes()
    fire_native_rival(rig)
    with pytest.raises(AssertionError):
        assert rig.writes() == before
    assert len(before) == 141 and len(rig.writes()) == 282


def test_callback_side_mutant_reaches_writer_but_writer_still_refuses():
    old = 'assert(io.read_u8(profile.hram.hBattleTurn, "System Bus") == 1, "rival callback: not enemy")'
    assert RIVAL.count(old) == 1
    for changed in (False, True):
        rig = pending_native_swap(
            ready(
                overrides=mutant(RIVAL.replace(old, 'assert(true, "rival callback: not enemy")'))
                if changed
                else None,
                fresh=True,
            )
        )
        arms = []
        rig.lua.globals().capture_arm = lambda arms=arms: arms.append(True)
        rig.lua.execute("""return function(w)
            local arm=w.arm
            w.arm=function(self,...) capture_arm(); return arm(self,...) end
        end""")(rig.rival.writes)
        rig.put("hBattleTurn", 0)
        before = rig.block()
        fire_native_rival(rig)
        rig.frame(1)
        assert rig.writes() == [] and rig.block() == before
        if changed:
            with pytest.raises(AssertionError):
                assert (
                    arms == []
                )  # callback brake removed; independent plan brake still protects RAM
            assert arms == [True]
            assert "not an enemy send-in" in rig.sent("rival_team_replaced")[0]["error"]
        else:
            assert arms == []
            assert "rival callback: not enemy" in rig.sent("rival_team_replaced")[0]["error"]


def test_unarmed_polished_composition_registers_no_rival_hook():
    rig = ready(classes=(), fresh=True)
    assert RIVAL_HOOK not in list(rig.log.hooks.values())


@pytest.mark.parametrize("title", ["crystal", "gold", "silver"], ids=["crystal", "gold", "silver"])
def test_vanilla_composition_registers_no_polished_rival_hook(title):
    from pathlib import Path

    from tests.unit import test_gen2_client as vanilla

    root = Path(ROOT)
    profile = json.loads((root / f"data/games/gen2_{title}/profile.json").read_text())["titles"][
        title
    ]
    repo = "pokecrystal" if title == "crystal" else "pokegold"
    if not (root / f".cache/gen2-build/{repo}/{profile['artifact']}.gbc").exists():
        pytest.skip("vanilla ROM input absent")
    world = vanilla.World(title)
    assert all("rival_swap_gate" not in name for name in world.emu.callbacks)


def test_wrong_bank_does_not_consume_native_visit():
    rig = pending_native_swap(ready(fresh=True))
    rig.put("hROMBank", 0x25)
    fire_native_rival(rig)
    assert rig.writes() == [] and not rig.client.rival.gate_used
    rig.put("hROMBank", 0x0F)
    fire_native_rival(rig)
    assert len(rig.writes()) == 141


def test_first_gate_without_pending_closes_late_reply_window():
    rig = pending_native_swap(ready(fresh=True))
    cmd = rig.client.rival.pending.cmd
    rig.client.rival.pending = None
    fire_native_rival(rig)
    rig.client.handle_command(rig.client, cmd)
    assert rig.sent("rival_team_replaced")[-1]["error"] == "late_reply"
    assert rig.writes() == []


def test_changed_rival_modules_compile_in_lua55():
    from lupa.lua55 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    for name in ("client.lua", "signals.lua", "polished_rival.lua"):
        assert lua.eval("function(s) return assert(load(s)) ~= nil end")((LUA / name).read_text())


@pytest.mark.parametrize("throw", [False, True])
def test_rival_success_log_is_deferred_and_cannot_change_result(throw):
    rig = pending_native_swap(ready(fresh=True))
    logged = []
    rig.lua.execute("""return function(lines, capture, throwing)
        setmetatable(lines, {__newindex=function(t,k,v)
            if tostring(v):find("Polished rival team written",1,true) then
                capture(v)
                if throwing then error("logger unavailable") end
            end
            rawset(t,k,v)
        end})
    end""")(rig.log.lines, logged.append, throw)
    fire_native_rival(rig)
    assert len(rig.writes()) == 141
    assert logged == []
    assert rig.sent("rival_team_replaced") == []
    rig.frame(1)
    replies = rig.sent("rival_team_replaced")
    assert len(replies) == 1 and not replies[0].get("error")
    assert logged == ["[SLink-gen2] Polished rival team written: 2 mon(s)"]
