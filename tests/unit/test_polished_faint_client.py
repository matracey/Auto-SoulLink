"""F2b: fail-closed CLIENT settlement of a Polished active faint (lua/gen2/client.lua `settle`).

Real composed Polished Rig (the overlay ROM, the real facade/permit/F1 writer, the real client), driven through the
same hold callback the emulator uses. The opt-in interface is switched on with deps.polished_active_faint; every test
also names what would make it red: the in-process MUTANTS replace one guard in the client source and run the SAME
assertions, which must fail.

Proved here (MODEL, no emulator, no physical evidence): obligation ownership, exactly-one-operation per hold token,
Explosion-first with a witnessed-survival plain fallback, COMPLETE only after bound evidence, zero-write PENDING retention
(battle and overworld), PARTIAL quarantine without retry, identity-change invalidation, central suppression of echoes and
of dead_keys / lift re-introduction, and Polished flag-off isolation (a differential against the pre-F2 client.lua).

The post-copy positive is injected MODEL evidence. No producer is composed; the existing 0f:44c8 pre-copy site
cannot complete F1. NOT proved: a native post-copy witness (0f:44cd is a separate observer card), actual vanilla
Crystal/Gold/Silver parity, any server-side PARTIAL hold, native animation or save durability.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.unit import test_polished_explode_path as battle_rig, test_polished_plain_faint as pf
from tests.unit.test_polished_explode_path import EXPLOSION, SYM, at_hold, enter_battle, order
from tests.unit.test_polished_write_path import key_of

ROOT = Path(__file__).resolve().parents[2]
CLIENT = "lua/gen2/client.lua"
CLIENT_SRC = (ROOT / CLIENT).read_text(encoding="utf-8")
BASELINE = "2b94147ac"          # integration head the F2a card started from: client.lua before any F2 change
PARTY_MONS = SYM["wPartyMons"][1]
HP_AT = PARTY_MONS + 34

HUD_OLD = "local hud = {show=function() end, nuzlocke_start=function() end, sanitize=function(s) return s end}"
HUD_NEW = ("local hud = {show=function(t) log.hud = log.hud or {} log.hud[#log.hud + 1] = t end,"
           " nuzlocke_start=function() end, sanitize=function(s) return s end}")
DEPS_OLD = 'local deps = {root=ROOTDIR, title="polished", io=io, net=net, hud=hud, player="a", rom_size=#rom,'


def build(*, interface=True, trade_interface=True, initial_identity_unavailable=False, **kwargs):
    """pf.setup() (an F1-ready battle image) over a harness that records HUD text and opts in to the interface."""
    harness = battle_rig.HARNESS_HOOKS
    assert harness.count(HUD_OLD) == 1 and harness.count(DEPS_OLD) == 1
    harness = harness.replace(HUD_OLD, HUD_NEW)
    if interface:
        harness = harness.replace(DEPS_OLD, DEPS_OLD + " polished_active_faint=true, polished_faint_observer=false,")
    else:
        harness = harness.replace(DEPS_OLD, DEPS_OLD + " polished_active_faint=false, polished_faint_observer=false,")
    if not trade_interface:
        harness = harness.replace(DEPS_OLD, DEPS_OLD + " polished_trade_dev=false,")
    if initial_identity_unavailable:
        harness = harness.replace("function io.read_u8(a, d)",
                                  "function io.read_u8(a, d)\n"
                                  f"    if d ~= 'ROM' and a == {SYM['wPlayerID'][1]} and io.frame < 10 then return nil end\n")
    with patch.object(battle_rig, "HARNESS_HOOKS", harness):
        return pf.setup(**kwargs)


def mutate(*pairs):
    source = CLIENT_SRC
    for old, new in pairs:
        assert source.count(old) == 1, f"mutation anchor drifted: {old!r}"
        source = source.replace(old, new)
    return {CLIENT: source}


def FS(rig):
    return rig.client.faint_settle


def owner(rig, index=1):
    return FS(rig).owed[index]


def owed(rig):
    return len(FS(rig).owed)


def shown(rig):
    hud = rig.log["hud"]
    return [] if hud is None else list(hud.values())


def ko(rig):
    return [t for t in shown(rig) if "KO'd" in t]


def lines(rig, needle):
    return [t for t in rig.lines() if needle in t]


def event(rig, **fields):
    return rig.lua.table_from(fields, recursive=True)


def observe(rig, slot=0, hp=0, generation=None, link=0):
    ev = {"kind": "observation", "site_id": "battle_faint",
          "battle": {"slot": slot, "hp": hp, "max_hp": 300, "mode": 1, "link_mode": link}}
    if generation is not None:
        ev["batch_generation"] = generation
    rig.client.on_event(rig.client, event(rig, **ev))


def tick(rig):
    rig.frame(30)           # exactly one frame % TICK_INTERVAL == 0


def next_hold(rig):
    rig.io.frame += 1       # a LATER frame: the hold is another turn, not the same callback twice
    at_hold(rig)


def pending(rig):
    return len(rig.client.pending_battle_writes)


# ── 1. identity, ownership, awaiting, ordered witnesses ─────────────────────────────────────────────────────

def lifecycle(overrides=None):
    rig, mons = build(overrides=overrides)
    key = key_of(mons[0])
    order(rig, "force_faint", mons)
    assert owed(rig) == 1 and pending(rig) == 1 and owner(rig).state == "pending"
    at_hold(rig)
    start = len(rig.writes())
    assert start == 7 and rig.hp(0) == 0                                  # F1: six HP/status bytes + the order byte
    ob = owner(rig)
    # a successful writer call starts OBSERVATION; it is not a death
    assert ob.state == "awaiting" and ob.kind == "plain" and pending(rig) == 0 and len(rig.client.deferred) == 0
    assert ko(rig) == [] and not rig.client.dead_keys[key], "writer success was reported as a completed death"
    tick(rig)
    assert rig.sent("tick")[-1]["party"][0]["hp"] == 0                    # F1 wrote the party mirror itself
    assert ob.state == "awaiting", "a party HP 0 the writer itself produced completed the death"
    epoch = rig.client.epoch
    observe(rig, slot=1)                                                  # another slot
    observe(rig, hp=5)                                                    # not a faint
    observe(rig, generation=epoch - 1)                                    # a batch of another generation
    tick(rig)
    assert ob.obs_seq is None and ob.state == "awaiting", "an unbound observation was accepted"
    observe(rig)                                                          # pre-copy HP is still F1's own write
    tick(rig)
    assert ob.obs_seq is None and ob.state == "awaiting" and ko(rig) == []
    assert not rig.client.dead_keys[key]
    return rig


def test_active_faint_awaits_then_completes_only_on_bound_evidence():
    lifecycle()


def witness(rig):
    """Future observer contract, injected MODEL evidence, never claimed as a live native execution."""
    ob = owner(rig)
    attempt = ob.attempts[len(ob.attempts)]
    return event(rig, kind="observation", site_id="battle_faint", phase="after_party_copyback",
                 batch_generation=rig.client.epoch,
                 capture={"identity": ob.identity, "generation": ob.gen, "epoch": attempt.epoch,
                          "visit": attempt.visit, "key": ob.phys, "attempt_seq": attempt.seq,
                          "seq": FS(rig).next_seq()},
                 battle={"slot": attempt.slot, "hp": 0, "status": 0, "mode": 1, "link_mode": 0,
                         "fainted": True})


def completed_lifecycle(overrides=None):
    rig = lifecycle(overrides)
    ob, key = owner(rig), owner(rig).key
    start = len(rig.writes())
    rig.put("wPlayerSubStatus2", 4)                                       # injected native-FAINTED witness
    rig.client.on_event(rig.client, witness(rig))
    assert ob.obs_seq is not None and ob.state == "awaiting"
    tick(rig)                                                             # a LATER party-bearing tick
    assert ob.state == "done" and owed(rig) == 1 and owner(rig).quiet and owner(rig).state == "pending"
    assert len(ko(rig)) == 1 and rig.client.dead_keys[key]
    assert len(rig.client.deferred) == 1 and rig.client.deferred[1].quiet, "completed death keeps its quiet re-zero"
    observe(rig)
    tick(rig)
    assert len(ko(rig)) == 1 and len(rig.writes()) == start, "a duplicate witness repeated a write or the KO"
    assert rig.sent("faint") == [], "the command receipt must not be echoed as a natural faint"
    return rig


def test_injected_post_copy_native_witness_then_sent_tick_completes_once():
    completed_lifecycle()


@pytest.mark.parametrize("name,mutant", [
    ("tick-alone", mutate(("if a.kind == \"explode\" or (ob.obs_seq and seq > ob.obs_seq) then", "if true then"))),
    ("writer-return-is-a-ko", mutate(("ob.state, ob.kind, ob.scheduled = \"awaiting\", kind, false",
                                      "ob.state, ob.kind, ob.scheduled = \"awaiting\", kind, false\n            FS.complete(ob, mon)"))),
])
def test_red_control_witness_guards(name, mutant):
    with pytest.raises(AssertionError):
        lifecycle(mutant)


WITNESS_GUARDS = [
    ("phase", 'ev.phase == "after_party_copyback"'),
    ("identity", "captured.identity == a.identity and captured.identity == ob.identity"),
    ("generation", "captured.generation == ob.gen"),
    ("epoch", "captured.epoch == a.epoch"),
    ("visit", "captured.visit == a.visit"),
    ("key", "captured.key == ob.phys"),
    ("attempt_seq", "captured.attempt_seq == a.seq"),
    ("old_seq", "captured.seq > a.seq"),
    ("future_seq", "captured.seq <= FS.seq"),
    ("slot", "b.slot == a.slot"),
    ("hp", "b.hp == 0"),
    ("status", "b.status == 0"),
    ("link_mode", "b.link_mode == 0"),
    ("fainted", "b.fainted == true"),
    ("native_fainted", "native.fainted == true"),
    ("native_hp", "native.hp == 0"),
    ("native_status", "native.status == 0"),
    ("batch", "if ev.batch_generation ~= self.epoch then"),
]


@pytest.mark.parametrize("bad,guard", WITNESS_GUARDS, ids=[p[0] for p in WITNESS_GUARDS])
def test_post_copy_capture_binding_and_native_evidence_are_required(bad, guard):
    def check(overrides=None):
        rig, mons = build(overrides=overrides)
        order(rig, "force_faint", mons)
        at_hold(rig)
        ob = owner(rig)
        rig.put("wPlayerSubStatus2", 4)
        ev = witness(rig)
        if bad == "phase":
            ev.phase = "before_party_copyback"
        elif bad in ("identity", "key"):
            ev.capture[bad] = "another-save-or-mon"
        elif bad in ("generation", "epoch", "visit", "attempt_seq"):
            ev.capture[bad] += 1
        elif bad == "old_seq":
            ev.capture.seq = ob.attempts[1].seq
        elif bad == "future_seq":
            ev.capture.seq = FS(rig).seq + 1
        elif bad == "native_fainted":
            rig.put("wPlayerSubStatus2", 0)
        elif bad == "native_hp":
            rig.put("wBattleMonHP", 1, 1)
        elif bad == "native_status":
            rig.put("wBattleMonStatus", 8)
        elif bad == "batch":
            ev.batch_generation -= 1
        else:
            ev.battle[bad] = False if bad == "fainted" else 1
        before = FS(rig).seq
        rig.client.on_event(rig.client, ev)
        assert FS(rig).seq == before, "draining an observation minted fresh capture evidence"
        tick(rig)
        assert ob.state == "awaiting" and ob.obs_seq is None and ko(rig) == []
    check()
    replacement = "if false then" if bad == "batch" else "true"
    with pytest.raises(AssertionError):
        check(mutate((guard, replacement)))


def test_the_resolved_physical_key_not_the_command_key_reaches_f1():
    alias = "ALIAS:0000:001:00"

    def check(overrides=None):
        rig, mons = build(overrides=overrides)
        key = key_of(mons[0])
        rig.client.retired_alias[alias] = key                             # the server names this record by an old key
        rig.client.handle_command(rig.client, event(rig, cmd="force_faint", key=alias, nickname="MON0"))
        ob = owner(rig)
        assert ob.key == alias and ob.phys == key and ob.state == "pending"
        at_hold(rig)
        assert ob.state == "awaiting" and rig.hp(0) == 0, "F1 was not handed the fresh physical key"
        assert ob.key == alias and ob.phys == key

    check()
    with pytest.raises(AssertionError):
        check(mutate(("snap.key = mon_key(mon)", "snap.key = ob.key")))


# ── 2. exactly one operation per hold token; Explosion first; witnessed-survival fallback ─────────────────

def explosion_written(rig):
    return rig.get("wBattleMonMoves", 2) == EXPLOSION


def explode_flow(overrides=None):
    rig, mons = build(overrides=overrides)
    key = key_of(mons[0])
    order(rig, "force_explode", mons)
    at_hold(rig)
    ob = owner(rig)
    assert explosion_written(rig) and ob.state == "awaiting" and ob.kind == "explode"
    assert rig.hp(0) == 300 and rig.get("wBattleMonHP", 1) == 44, "the plain faint ran under the Explosion's token"
    assert len(rig.writes()) == 5 and ko(rig) == []
    at_hold(rig)                                                          # the same frame: not another turn
    assert len(rig.writes()) == 5 and ob.state == "awaiting"
    return rig, mons, key, ob


def test_explosion_first_one_operation_per_token_then_a_new_attempt_after_witnessed_survival():
    rig, mons, key, ob = explode_flow()
    next_hold(rig)                                                        # a later turn: same mon, alive, active
    assert ob.state == "awaiting" and ob.kind == "plain" and len(ob.attempts) == 2
    assert ob.attempts[1].kind == "explode" and ob.attempts[2].kind == "plain"
    assert ob.attempts[2].serial > ob.attempts[1].serial, "the fallback reused the Explosion's dispatch token"
    assert rig.hp(0) == 0 and ko(rig) == []


BOTH = ("        writes:disarm()\n        if ok then\n            ob.attempts[#ob.attempts + 1] = attempt",
        "        FS.dispatch(ob, \"plain\", function() writes:arm(\"battle_hold\") writes:faint_active_battler(slot, snap) end)\n"
        "        writes:disarm()\n        if ok then\n            ob.attempts[#ob.attempts + 1] = attempt")


def test_a_second_dispatch_under_one_token_is_refused_and_the_guard_is_load_bearing():
    rig, *_ = explode_flow(mutate(BOTH))                                  # the guard holds: still no plain faint
    assert rig.hp(0) == 300 and lines(rig, "dispatch token is spent")
    with pytest.raises(AssertionError):                                   # guard removed: both ran under one token
        explode_flow(mutate(BOTH, ("if FS.spent[ob.id] == FS.hold_serial then", "if false then")))


def test_explosion_completes_on_the_native_copy_back_tick_and_needs_it():
    def check(overrides=None):
        rig, mons, key, ob = explode_flow(overrides)
        tick(rig)
        assert ob.state == "awaiting" and ko(rig) == [], "completed with the mon still alive"
        for at in (HP_AT, HP_AT + 1):
            rig.mem[at] = 0                                              # the engine's copy-back landed
        tick(rig)
        assert ob.state == "done" and len(ko(rig)) == 1
    check()
    with pytest.raises(AssertionError):
        check(mutate(("if a.kind == \"explode\" or (ob.obs_seq and seq > ob.obs_seq) then", "if false then")))


def test_explosion_that_survived_the_battle_falls_back_to_one_overworld_faint():
    rig, mons, key, ob = explode_flow()
    rig.put("wBattleMode", 0)
    rig.put("hROMBank", 0x25)
    rig.frame(60)                                                         # tick: in_battle false, HP > 0 = witnessed survival
    assert ob.state == "done" and rig.hp(0) == 0 and rig.status(0) == 0
    assert [a.kind for a in ob.attempts.values()] == ["explode", "overworld"]
    assert len(ko(rig)) == 1


# ── 3. zero-write PENDING is retained (battle and overworld) ───────────────────────────────────────────────

def refusal_flow(overrides=None):
    rig, mons = build(overrides=overrides)
    rig.put("wPlayerSwitchTarget", 1)                                    # F1 refuses: a committed switch (zero writes)
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    assert rig.writes() == [] and ob.state == "pending" and pending(rig) == 1 and ko(rig) == [], "refusal consumed the owner"
    next_hold(rig)
    assert pending(rig) == 1 and owed(rig) == 1, "a refusal duplicated or dropped the reference"
    rig.put("wPlayerSwitchTarget", 0)
    next_hold(rig)
    assert ob.state == "awaiting" and rig.hp(0) == 0 and pending(rig) == 0
    return rig


def test_a_zero_write_refusal_stays_one_pending_obligation_and_retries_at_a_later_hold():
    refusal_flow()
    with pytest.raises(AssertionError):
        refusal_flow(mutate(("if settle.attempted_since(mark) == false then\n            ob.suppress = previous",
                             "if false then\n            ob.suppress = previous")))


def overworld_rig(**kwargs):
    rig, mons = build(**kwargs)
    rig.put("wBattleMode", 0)
    rig.put("hROMBank", 0x25)
    return rig, mons


def test_the_overworld_seam_retains_a_missing_or_ambiguous_target_then_retires_on_keyed_readback():
    rig, mons = overworld_rig()
    key = key_of(mons[1])
    rig.put("wPartyCount", 1)                                            # the mon is in a box: not in the party
    order(rig, "force_faint", mons, slot=1)
    rig.frame(5)
    ob = owner(rig)
    assert rig.writes() == [] and ob.state == "pending" and len(rig.client.deferred) == 1 and ko(rig) == []
    assert len(lines(rig, "retained at the checkpoint")) == 1
    rig.put("wPartyCount", 3)
    saved = [rig.mem[PARTY_MONS + 96 + i] for i in range(48)]
    for i in range(48):                                                  # an identical record in slot 2: ambiguous key
        rig.mem[PARTY_MONS + 96 + i] = rig.mem[PARTY_MONS + 48 + i]
    rig.frame(3)
    assert rig.writes() == [] and ob.state == "pending" and len(rig.client.deferred) == 1
    for i, byte in enumerate(saved):                                     # distinct again
        rig.mem[PARTY_MONS + 96 + i] = byte
    rig.frame(3)
    assert ob.state == "done" and rig.hp(1) == 0 and rig.hp(2) == 300 and len(ko(rig)) == 1
    assert rig.client.dead_keys[key]
    assert owed(rig) == 0, "an overworld completion left work behind"


def test_an_overworld_write_with_no_readback_is_partial_quarantine_never_a_retry():
    def check(overrides=None):
        rig, mons = overworld_rig(faults=True, overrides=overrides)
        rig.io.f1_at, rig.io.f1_fault = HP_AT + 0, "swallow"             # the HP write is lost: the readback disagrees
        order(rig, "force_faint", mons, slot=0)
        rig.frame(3)
        ob = owner(rig)
        assert ob.state == "partial" and ko(rig) == [] and len(lines(rig, "PARTIAL QUARANTINED")) == 1
        writes = len(rig.writes())
        rig.io.f1_fault = None
        rig.frame(120)
        assert len(rig.writes()) == writes and ob.state == "partial", "a quarantined attempt was retried"
    check()
    with pytest.raises(AssertionError):
        check(mutate(("            return FS.quarantine(ob, \"partial\", \"overworld\", err)\n        end\n"
                      "        ob.attempts[#ob.attempts + 1] = { kind = \"overworld\"",
                      "            return FS.requeue(ob, entry)\n        end\n"
                      "        ob.attempts[#ob.attempts + 1] = { kind = \"overworld\"")))


# ── 4. PARTIAL: quarantined, no retry anywhere ────────────────────────────────────────────────────────────

def partial_flow(overrides=None):
    rig, mons = build(faults=True, overrides=overrides)
    key = key_of(mons[0])
    rig.io.f1_at, rig.io.f1_fault = pf.addresses()[6], "swallow"          # the final order byte is lost after the HP bytes
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    assert ob.state == "partial" and pending(rig) == 0 and ko(rig) == []
    assert len(lines(rig, "PARTIAL QUARANTINED")) == 1 and "PARTIAL ACTIVE FAINT" in lines(rig, "PARTIAL QUARANTINED")[0]
    writes = len(rig.writes())
    rig.io.f1_fault = None
    for _ in range(3):                                                    # repeated holds, ticks, server re-sends
        next_hold(rig)
        order(rig, "force_faint", mons)
        order(rig, "force_explode", mons)
        rig.client.handle_command(rig.client, event(rig, cmd="dead_keys", keys=[key]))
        rig.frame(31)
    rig.client.handle_command(rig.client, event(rig, cmd="memorialize", key=key, nickname="MON0"))
    rig.put("wBattleMode", 0)                                             # battle ends, overworld checkpoint
    rig.put("hROMBank", 0x25)
    rig.frame(120)
    assert len(rig.writes()) == writes, "a PARTIAL attempt was written again"
    assert ob.state == "partial" and owed(rig) == 1 and ko(rig) == []
    assert rig.sent("memorialize_done") == [] and rig.sent("memorialize_failed") == [], "box op answered on an unsettled target"
    assert len(lines(rig, "PARTIAL QUARANTINED")) == 1
    return rig, mons, key


def test_partial_is_quarantined_with_one_log_line_and_never_retried():
    partial_flow()
    requeue = ("        ob.attempts[#ob.attempts + 1] = attempt\n        FS.quarantine(ob, \"partial\", kind, err)",
               "        ob.attempts[#ob.attempts + 1] = attempt\n        ob.scheduled = true\n        keep[#keep + 1] = w")
    with pytest.raises(AssertionError):                                   # fell through into the `keep` retry
        partial_flow(mutate(requeue))


def retrieval_held(overrides=None):
    rig, mons, key = partial_flow(overrides)
    before = len(rig.writes())
    rig.client.handle_command(rig.client, event(rig, cmd="party_mon", key=key))
    rig.frame(4)
    assert owner(rig).state == "partial" and len(rig.writes()) == before
    assert not rig.sent("sync_retrieve_done") and not rig.sent("sync_retrieve_failed")
    assert any(e.cmd == "party_mon" for e in rig.client.deferred.values())


def test_partial_retrieval_stays_held_without_a_definite_reply():
    retrieval_held()
    with pytest.raises(AssertionError):
        retrieval_held(mutate((' and cmd.cmd ~= "party_mon" then return false end', ' then return false end')))


def test_central_suppression_of_dead_keys_and_lift_while_an_obligation_is_open():
    def check(overrides=None):
        rig, mons, key = partial_flow()
        # heal: HP > 0 again; the server's dead_keys must not re-introduce a write for the quarantined owner
        for at in (HP_AT, HP_AT + 1):
            rig.mem[at] = 0 if at != HP_AT + 1 else 77
        rig.client.handle_command(rig.client, event(rig, cmd="dead_keys", keys=[key]))
        before = len(rig.writes())
        rig.frame(60)
        assert len(rig.writes()) == before and owed(rig) == 1
    check()
    # the re-introduction guards removed (revived_dead + quiet_entry): a quiet owner is created and the overworld writes
    mutant = mutate(("and not (settle and FS.find_owner(key, mon_key(mon))) then return key end", "then return key end"),
                    ("if FS.find_owner(key, mon and mon_key(mon)) then return nil end", ""))

    with pytest.raises(AssertionError):
        rig, mons, key = partial_flow(mutant)
        for at in (HP_AT, HP_AT + 1):
            rig.mem[at] = 0 if at != HP_AT + 1 else 77
        rig.client.handle_command(rig.client, event(rig, cmd="dead_keys", keys=[key]))
        before = len(rig.writes())
        rig.frame(60)
        assert len(rig.writes()) == before and owed(rig) == 1


# ── 5. identity ────────────────────────────────────────────────────────────────────────────────────────────

def change_identity(rig):
    rig.mem[SYM["wPlayerID"][1]] = 0x12                                   # another save's OT id
    rig.frame(3)


@pytest.mark.parametrize("seam", ["battle", "overworld"])
@pytest.mark.parametrize("identity", ["changed", "unavailable"])
def test_identity_is_checked_at_the_write_seam_before_frame_end(seam, identity):
    def check(overrides=None):
        rig, mons = build(overrides=overrides) if seam == "battle" else overworld_rig(overrides=overrides)
        order(rig, "force_faint", mons)
        ob = owner(rig)
        assert ob.identity == rig.client.hello_session.status(rig.client.hello_session).identity
        if identity == "changed":
            rig.mem[SYM["wPlayerID"][1]] = 0x12
        else:
            rig.parts.reads.read_player = rig.lua.eval("function() return nil end")
        if seam == "battle":
            at_hold(rig)
        else:
            rig.client.run_deferred(rig.client)
        assert rig.writes() == [], "an old/unverifiable identity reached the write permit"
        assert ob.state == "stale" and ko(rig) == []
    check()
    with pytest.raises(AssertionError):
        check(mutate(('    function FS.check_identity(ob)\n', '    function FS.check_identity(ob)\n        if true then return true end\n')))


@pytest.mark.parametrize("seam", ["battle", "overworld"])
def test_identity_is_rechecked_after_target_reads_before_permit(seam):
    rig, mons = build() if seam == "battle" else overworld_rig()
    order(rig, "force_faint", mons)
    # Change identity during target resolution, after begin_hold / the first overworld identity check.
    swap = rig.lua.eval("""function(read, mem, addr, nth)
        local calls = 0
        return function(...)
            local result = table.pack(read(...))
            calls = calls + 1
            if calls == nth then mem[addr] = 0x12 end
            return table.unpack(result, 1, result.n)
        end
    end""")
    rig.parts.reads.read_party = swap(rig.parts.reads.read_party, rig.mem, SYM["wPlayerID"][1],
                                     2 if seam == "battle" else 1)
    if seam == "battle":
        at_hold(rig)
    else:
        rig.client.run_deferred(rig.client)
    assert rig.writes() == [] and owner(rig).state == "stale"


def test_an_unaccepted_identity_cannot_authorize_or_block_the_new_hello():
    rig, mons = build()
    order(rig, "force_faint", mons)
    change_identity(rig)
    assert not rig.client.hello_session.status(rig.client.hello_session).ready
    order(rig, "force_faint", mons)                                      # stale server traffic before the new hello
    unaccepted = owner(rig, 2)
    at_hold(rig)
    assert rig.writes() == [] and unaccepted.state == "stale"
    rig.put("wBattleMode", 0)
    rig.put("hROMBank", 0x25)
    rig.frame(30)
    assert rig.client.hello_session.status(rig.client.hello_session).ready
    order(rig, "force_faint", mons)                                      # new-identity server order
    rig.frame(3)
    assert unaccepted.state == "stale" and rig.hp(0) == 0 and len(ko(rig)) == 1


def test_initial_unavailable_identity_recovers_only_after_accepted_hello():
    rig, mons = build(initial_identity_unavailable=True)
    assert rig.client.hello_session.status(rig.client.hello_session).ready
    assert not FS(rig).suspended
    order(rig, "force_faint", mons)
    at_hold(rig)
    assert owner(rig).state == "awaiting" and rig.hp(0) == 0
    assert owner(rig).identity == owner(rig).attempts[1].identity == FS(rig).last_identity


def test_synchronous_identity_quarantine_preserves_partial_write_evidence():
    rig, mons = build(faults=True)
    rig.io.f1_at, rig.io.f1_fault = pf.addresses()[6], "swallow"
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    assert ob.state == "partial"
    evidence = dict(ob.evidence.items())
    rig.io.f1_fault = None
    order(rig, "force_faint", mons, slot=1)
    rig.mem[SYM["wPlayerID"][1]] = 0x12
    before = len(rig.writes())
    at_hold(rig)
    assert ob.state == "stale" and ob.was == "partial" and len(rig.writes()) == before
    assert dict(ob.evidence.items()) == evidence


def identity_flow(overrides=None):
    rig, mons = build(overrides=overrides)
    key = key_of(mons[0])
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    old_epoch = rig.client.epoch
    observe(rig)                                                          # an old-identity witness already recorded/queued
    change_identity(rig)
    assert ob.state == "stale", "an attempted obligation survived the save change"
    assert FS(rig).gen == 1 and rig.client.epoch > old_epoch
    observe(rig, generation=old_epoch)                                    # a batch drained before, consumed after
    for at in (HP_AT, HP_AT + 1):
        rig.mem[at] = 0
    tick(rig)
    assert ob.state == "stale" and ko(rig) == [] and not rig.client.dead_keys[key]
    return rig


def test_identity_change_quarantines_old_work_and_old_witnesses_cannot_complete_it():
    identity_flow()
    with pytest.raises(AssertionError):
        identity_flow(mutate(("                if settle then FS.identity_changed() end\n", "")))
    with pytest.raises(AssertionError):
        identity_flow(mutate(("        FS.gen = FS.gen + 1\n        self.epoch = self.epoch + 1", "        self.epoch = self.epoch")))


def test_unattempted_old_identity_work_never_reaches_the_new_save_and_unavailable_suspends():
    rig, mons = build()
    order(rig, "force_faint", mons)
    assert pending(rig) == 1
    change_identity(rig)
    assert pending(rig) == 0 and len(rig.client.deferred) == 0 and owner(rig).state == "stale"
    at_hold(rig)
    assert rig.writes() == [], "old-identity work wrote to the new save"
    rig2, mons2 = build()
    rig2.client.hello_session.invalidate(rig2.client.hello_session, "identity_unavailable")
    assert FS(rig2).suspended is True
    order(rig2, "force_faint", mons2)
    at_hold(rig2)
    assert rig2.writes() == [] and owner(rig2).state == "pending", "attempted while the identity was unverifiable"
    rig2.frame(30)
    assert FS(rig2).suspended is False, "suspension never released after the hello was ready again"
    next_hold(rig2)
    assert owner(rig2).state == "awaiting"


def test_a_reset_boundary_quarantines_a_staged_attempt_and_hands_unattempted_work_over():
    rig, mons = build()
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    order(rig, "force_faint", mons, slot=1)
    rig.client.boundary(rig.client, "reset", "save_reset")
    assert ob.state == "lost" and len(rig.client.deferred) == 1 and owner(rig, 2).state == "pending"
    rig.frame(5)
    assert ob.state == "lost" and ko(rig) == []


# ── 6. echo suppression is scoped; safe is not evidence ───────────────────────────────────────────────────

def echo_flow(overrides=None):
    rig, mons = build(overrides=overrides)
    key, other = key_of(mons[0]), key_of(mons[1])
    order(rig, "force_faint", mons)
    at_hold(rig)
    def faint(k):
        rig.client.on_event(rig.client, event(rig, kind="faint", cause="poison", mon={"key": k, "is_egg": False}))

    faint(other)
    assert len(rig.client.faint_latches) == 1, "an unrelated natural faint was swallowed"
    faint(key)
    assert len(rig.client.faint_latches) == 1 and lines(rig, "faint echo of a commanded death dropped")
    faint(key)
    assert len(rig.client.faint_latches) == 2, "the suppression outlived its one echo"
    rig.client.on_event(rig.client, event(rig, kind="whiteout"))
    assert len(rig.sent("whiteout")) == 1, "natural whiteout behaviour changed"
    return rig


def test_the_commanded_echo_is_suppressed_by_identity_and_natural_events_are_not():
    echo_flow()
    with pytest.raises(AssertionError):
        echo_flow(mutate(("            if s and s.phys == key and s.gen == FS.gen and s.epoch == self.epoch then",
                          "            if s then")))


def test_a_box_only_safe_is_never_hp_evidence():
    rig, mons = build()
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    rig.put("wPlayerSubStatus2", 4)
    rig.client.on_event(rig.client, witness(rig))
    rig.put("wBattleMode", 0)
    rig.put("hROMBank", 0x25)
    rig.client.on_event(rig.client, event(rig, kind="observation", site_id="battle_end", refused_acquisitions=0))
    rig.frame(2)
    safes = rig.sent("safe")
    assert safes and "party" not in safes[-1]
    assert ob.state == "awaiting" and ko(rig) == [], "a safe event completed a death"
    tick(rig)
    assert ob.state == "done"


def test_a_held_tick_is_not_evidence():
    rig, mons = build()
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    rig.put("wPlayerSubStatus2", 4)
    rig.client.on_event(rig.client, witness(rig))
    rig.client.hello_session.invalidate(rig.client.hello_session, "identity_unavailable")   # not ready: the tick is held, not sent
    rig.frame(1)
    rig.client.send_tick(rig.client, "tick")
    assert ob.state == "awaiting"


def resumed_witness(consumed, overrides=None):
    rig, mons = build(overrides=overrides)
    order(rig, "force_faint", mons)
    at_hold(rig)
    ob = owner(rig)
    rig.put("wPlayerSubStatus2", 4)
    old = witness(rig)
    if consumed:
        rig.client.on_event(rig.client, old)
    rig.client.hello_session.invalidate(rig.client.hello_session, "identity_unavailable")
    rig.frame(1)
    if not consumed:
        rig.client.on_event(rig.client, old)
    # Invalidating hello while in battle also holds ticks. Restore normal hello readiness at the
    # overworld gate so the negative exercises a SENT tick, not merely an unavailable transport.
    rig.put("wBattleMode", 0)
    rig.put("hROMBank", 0x25)
    rig.frame(30)
    tick(rig)
    assert ob.obs_seq is None and ob.state == "awaiting" and ko(rig) == []
    rig.client.on_event(rig.client, witness(rig))
    tick(rig)
    assert ob.state == "done" and len(ko(rig)) == 1


@pytest.mark.parametrize("consumed", [False, True], ids=["captured-before-suspension", "consumed-before-suspension"])
def test_identity_resumption_requires_a_fresh_native_witness(consumed):
    resumed_witness(consumed)
    removed = (("ob.obs_seq = nil end", "ob.obs_seq = ob.obs_seq end") if consumed else
               ("and captured.seq > (FS.evidence_floor or 0)", "and true"))
    with pytest.raises(AssertionError):
        resumed_witness(consumed, mutate(removed))


# ── 7. vanilla isolation: the interface absent ────────────────────────────────────────────────────────────

def baseline_client():
    probe = subprocess.run(["git", "cat-file", "-e", f"{BASELINE}:{CLIENT}"], cwd=ROOT, capture_output=True)
    if probe.returncode != 0:
        pytest.skip(f"baseline commit {BASELINE} is absent from this clone")
    return subprocess.run(["git", "show", f"{BASELINE}:{CLIENT}"], cwd=ROOT, capture_output=True, check=True).stdout.decode()


def legacy_trace(overrides=None):
    # The pre-F2 client predates the shipped Polished trade pump (2e53f2958).
    # Disable both optional interfaces for this legacy-faint comparison; do not
    # normalize wire flags or discard logs from either trace.
    rig, mons = build(interface=False, trade_interface=False, overrides=overrides)
    assert rig.client.faint_settle is None, "the interface leaked into a composition without it"
    assert rig.parts.polished_trade is None and rig.parts.dev_polished_trade is None
    assert rig.client.trade_live(rig.client) is False
    snaps = []

    def snap(tag):
        snaps.append((tag, [(e.key, e.cmd, e.quiet) for e in rig.client.deferred.values()],
                      [(e.key, e.cmd, e.quiet) for e in rig.client.pending_battle_writes.values()],
                      sorted(rig.client.dead_keys.keys()), sorted(rig.client.commanded.keys()),
                      len(rig.client.faint_latches)))
    order(rig, "force_explode", mons)
    snap("explode queued")
    at_hold(rig)
    snap("explode held")
    next_hold(rig)
    order(rig, "force_faint", mons, slot=1)
    order(rig, "force_faint", mons, slot=2)
    at_hold(rig)
    snap("bench held")
    rig.client.on_event(rig.client, event(rig, kind="faint", cause="x", mon={"key": key_of(mons[0]), "is_egg": False}))
    snap("echo")
    rig.put("wBattleMode", 0)
    rig.put("hROMBank", 0x25)
    rig.frame(8)
    snap("overworld")
    rig.client.handle_command(rig.client, event(rig, cmd="dead_keys", keys=[key_of(mons[1])]))
    order(rig, "force_faint", mons, slot=1)
    rig.frame(4)
    rig.client.handle_command(rig.client, event(rig, cmd="force_faint", key="ABCDEF:1234:010:00"))
    rig.frame(4)
    snap("unknown key")
    rig.client.boundary(rig.client, "reset", "save_reset")
    rig.frame(3)
    snap("reset")
    enter_battle(rig, mons)
    rig.frame(2)
    next_hold(rig)
    snap("end")
    return ([json.loads(x) for x in rig.log["sent"].values()], rig.writes(), rig.lines(), shown(rig), snaps)


def test_vanilla_paths_are_byte_for_byte_the_pre_f2_client_with_the_interface_absent():
    now = legacy_trace()
    before = legacy_trace(overrides={CLIENT: baseline_client()})
    assert now[4] == before[4], "queue / dead_keys / commanded state diverged"
    assert now[1] == before[1], "write log diverged"
    assert now[0] == before[0], "emitted messages diverged"
    assert now[2] == before[2] and now[3] == before[3], "log / HUD diverged"
    assert now[1], "the scenario wrote nothing: it proves nothing"


def test_nil_settlement_does_not_disable_default_shipped_trade():
    rig, _ = build(interface=False)
    assert rig.client.faint_settle is None
    assert rig.parts.polished_trade is not None
    assert rig.client.trade_live(rig.client) is True


def test_red_control_a_branch_that_ignores_the_missing_interface_diverges():
    leaky = mutate(("    local settle = p.active_faint_settlement\n",
                    "    local settle = p.active_faint_settlement or {attempt_mark = function() end, "
                    "attempted_since = function() return false end}\n"))
    with pytest.raises(AssertionError):
        now = legacy_trace(leaky)
        before = legacy_trace(overrides={CLIENT: baseline_client()})
        assert now[4] == before[4]


def test_the_explicitly_disabled_composition_has_no_settlement_and_the_facade_still_refuses_unkeyed():
    rig, mons = build(interface=False)
    assert rig.client.faint_settle is None and rig.parts.battle.writes is not None
    order(rig, "force_faint", mons)
    at_hold(rig)
    assert rig.writes() == [] and len(rig.client.pending_battle_writes) == 1
    assert lines(rig, "not composed on Polished")


def test_completed_explosion_still_suppresses_its_delayed_native_faint():
    """Live replay: selfdestruct HP0 tick settles before the native faint about600 frames later."""
    rig, _, key, ob = explode_flow()
    for at in (HP_AT, HP_AT + 1):
        rig.mem[at] = 0  # engine selfdestruct copyback, before the native faint animation
    rig.put("wBattleMonHP", 0)
    rig.put("wBattleMonHP", 0, 1)
    tick(rig)
    assert ob.state == "done" and len(ko(rig)) == 1
    assert rig.sent("faint") == [] and len(rig.writes()) == 5
    rig.frame(600)
    rig.client.on_event(rig.client, event(rig, kind="faint", cause="battle",
                                        mon={"key": key, "is_egg": False}))
    tick(rig)
    assert rig.sent("faint") == [], "completed commanded Explosion echoed as a new natural faint"
    assert len(ko(rig)) == 1 and len(rig.writes()) == 5


def test_held_death_keeps_diagnostics_out_of_hud():
    rig, mons = build(interface=False)
    order(rig, "force_faint", mons)
    assert shown(rig) == []
    assert len(lines(rig, "held for the checkpoint")) == 1
    assert len(rig.client.pending_battle_writes) == 1 and rig.writes() == []


def test_refused_pending_death_logs_once_and_only_completion_has_ko_hud():
    rig, mons = overworld_rig()
    writes = rig.parts.battle.rival.writes
    original = writes.faint_party_slot
    writes.faint_party_slot = rig.lua.eval('function() error("fixture guard refusal") end')
    order(rig, "force_faint", mons)
    rig.frame(3)
    assert owner(rig).state == "pending" and rig.writes() == []
    assert shown(rig) == []
    assert len(lines(rig, "refused by the write gate (retained)")) == 1
    writes.faint_party_slot = original
    rig.frame(3)
    assert len(ko(rig)) == 1 and owed(rig) == 0
