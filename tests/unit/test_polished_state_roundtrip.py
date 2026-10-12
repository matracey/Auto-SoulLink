"""Polished box commands through BOTH halves: the composed client over the real overlay ROM (the Rig) AND the real
server SoulLinkState (tests/unit/polished_state_rig.py PolishedStateBridge).

test_polished_box_contract.py proves what the client SENDS for a duplicate `party_mon`. It cannot prove what the
server DOES with it, and that is where the damage would land (review cx-4beeb281: a refused duplicate reads as a
failed retrieval, drops the rebuild key and re-boxes the PARTNER). Here the client's own emitted events are fed to
the real state, a snapshot of both players' queued_commands / party_keys / party_size / sync_inflight /
rebuild_pending is taken after EACH event, and the assertions read those snapshots, so a later tick that repairs the
model cannot hide an intermediate damage.

FIXTURE (SEEDED: not an admission and not a natural capture). The Soul Link pair is written straight into the server
model: area `route_29` (a Polished area id), A's half is the mon planted in the box of the Rig's cartridge (absent from
state.party_keys['a']), B's half is a different valid Polished key that B "already withdrew natively" (present in
state.party_keys['b']). Everything else is observed: A's hello and ticks come from the real client, the party_mon
reaches the client only through the state's own tick reply (which arms sync_inflight), and the dict the state returned
goes UNCHANGED into rig.send_command.

Run: pytest tests/unit/test_polished_state_roundtrip.py -v
"""
from __future__ import annotations

import copy
import functools
import random
from dataclasses import dataclass

import pytest

from server.state import LinkStatus
from tests.unit.polished_state_rig import (
    MutantRig,
    PolishedStateBridge,
    assert_sync_command,
    new_state,
    seed_pair,
    seed_rebuild,
)
from tests.unit.test_polished_box_contract import OVERWORLD
from tests.unit.test_polished_withdraw_path import boxed, boxed_mon, options
from tests.unit.test_polished_write_path import HP, PARTY_MONS, RECORD, Rig, key_of, live_mon, party

lupa = pytest.importorskip("lupa")

AREA = "route_29"


@dataclass
class Scenario:
    rig: object
    state: object
    bridge: PolishedStateBridge
    key: str                  # A's linked mon: boxed on the cartridge
    partner: str              # B's linked mon: in B's party
    other: str                # a second key of A's seeded rebuild, so the rebuild is not finished by the first ack
    mark: int                 # history length just before B's native retrieval
    command: dict | None = None
    window: int = 0           # history length just before the duplicate
    count_before: int = 0
    writes_before: int = 0


def scenario(tmp_path, make_rig=Rig, cartridge_party=3, lag_one=False):
    """The real client + the real state, seeded pair, B retrieves natively, the state's party_mon reaches A.

    lag_one: the cartridge party is one mon ahead of the server's model (a mon A just caught): the state still
    believes A has room, so it hands out a party_mon the full cartridge party must refuse."""
    mons = party(cartridge_party)
    rig = make_rig(mons)
    options(rig)
    mon = boxed_mon()
    boxed(rig, mon)
    state = new_state(tmp_path)
    bridge = PolishedStateBridge(rig, state)
    assert rig.arm_writes(), "the client never enabled its writes"
    bridge.drain()                                            # A's hello + ticks, as the client emitted them
    assert state.party_size["a"] == cartridge_party and len(state.party_keys["a"]) == cartridge_party
    if lag_one:
        state.party_keys["a"].discard(key_of(mons[-1]))       # SEEDED lag: the server's party model is one mon behind
        state.party_size["a"] = cartridge_party - 1
    key = key_of(mon)
    partner = key_of(live_mon(random.Random(555), 30, ot="KRIS", nick="PARTNER"))
    other = key_of(live_mon(random.Random(321), 41, ot="KRIS", nick="OTHER"))
    assert len({key, partner, other}) == 3 and key not in state.party_keys["a"]
    seed_pair(state, key, partner, area=AREA, a_party=False, b_party=True)
    state.party_size["b"] = 1
    seed_rebuild(state, "a", [key, other])
    sc = Scenario(rig, state, bridge, key, partner, other, bridge.mark())
    bridge.feed("b", {"event": "box_to_party", "key": partner})            # B withdrew natively
    (cmd,) = [c for c in bridge.deliver("a") if c["cmd"] != "noop"]        # A's next tick returns it
    assert_sync_command(state, "a", cmd, "party_mon", key)
    sc.command = cmd
    return sc


def run_command(sc):
    """Hand the state's returned dict to the client UNCHANGED and feed back what the client then emitted."""
    before = copy.deepcopy(sc.command)
    sc.rig.send_command(sc.command)
    assert sc.command == before, "the command dict was altered on its way to the client"
    sc.bridge.drain()


def duplicate_damage(sc, window, whole):
    """Every way the duplicate hurt the server model, read from the per-event snapshots. `window` = the snapshots from
    the duplicate on; `whole` = from B's native retrieval on (the partner must never be re-boxed at ANY point)."""
    bad = []
    for snap in sc.bridge.snapshots_since(window):
        rb = snap["rebuild"]["a"]
        if rb is None or sc.key not in rb["queued_keys"]:
            bad.append(f"#{snap['i']}: key dropped from the rebuild queued_keys")
        elif sc.key not in rb["restored_keys"]:
            bad.append(f"#{snap['i']}: key no longer marked restored")
        if snap["party_keys"]["a"].count(sc.key) != 1:
            bad.append(f"#{snap['i']}: key not in A's party_keys exactly once")
        if snap["sync_inflight"]["a"]:
            bad.append(f"#{snap['i']}: sync_inflight not clear {snap['sync_inflight']['a']}")
    for index, how in sc.bridge.commands_since(whole, "b", "box_mon", sc.partner):
        bad.append(f"#{index}: box_mon for B's mon {how}")
    for index, how in sc.bridge.commands_since(whole, "a", "box_mon", sc.key):
        bad.append(f"#{index}: box_mon for A's own mon {how}")
    return bad


# ── T1: the single round trip ────────────────────────────────────────────────────────────────────────────

def test_a_single_withdraw_round_trips_through_the_real_server(tmp_path):
    sc = scenario(tmp_path)
    delivered = sc.bridge.history[-1]
    assert sc.key in {k for k, c, _ in delivered["sync_inflight"]["a"] if c == "party_mon"}, "delivery armed no inflight"
    assert sc.key not in delivered["party_keys"]["a"]                      # not in the party until the client says so
    run_command(sc)
    assert sc.rig.count() == 4
    assert [m["key"] for m in sc.rig.sent("sync_retrieve_done")] == [sc.key]
    snap = sc.bridge.history[-1]
    assert (snap["event"], snap["key"]) == ("sync_retrieve_done", sc.key)
    assert sc.key in snap["party_keys"]["a"]
    assert snap["sync_inflight"]["a"] == []                                # the ack cleared the party_mon inflight
    assert sc.bridge.commands_since(sc.mark, "b", "box_mon") == [], sc.bridge.trace(sc.mark)
    rb = snap["rebuild"]["a"]
    assert rb is not None, "the rebuild finished on the first ack: the fixture cannot show the key surviving"
    assert rb["queued_keys"] == [sc.key, sc.other] and rb["restored_keys"] == {sc.key}
    assert snap["party_keys"]["b"] == [sc.partner]                         # B's half untouched
    # the ACK adds membership but must NOT bump the server's count: it stays the last tick-observed size (3)
    assert snap["party_size"]["a"] == delivered["party_size"]["a"] == 3, (snap["party_size"], delivered["party_size"])


# ── T2: the duplicate, and T3 its red control through the real client ────────────────────────────────────

def duplicate_scenario(tmp_path, make_rig=Rig):
    sc = scenario(tmp_path, make_rig)
    run_command(sc)
    assert sc.rig.count() == 4 and [m["key"] for m in sc.rig.sent("sync_retrieve_done")] == [sc.key]
    sc.window = sc.bridge.mark()
    sc.count_before, sc.writes_before = sc.rig.count(), len(sc.rig.writes())
    run_command(sc)                                                         # the SAME dict, again
    return sc


def test_a_duplicate_party_mon_after_a_completed_withdraw_never_reboxes_the_partner(tmp_path):
    sc = duplicate_scenario(tmp_path)
    assert sc.rig.sent("sync_retrieve_failed") == [], sc.rig.sent("sync_retrieve_failed")
    assert [m["key"] for m in sc.rig.sent("sync_retrieve_done")] == [sc.key, sc.key]     # re-acked
    assert sc.rig.count() == sc.count_before == 4                           # the cartridge party grew exactly once
    assert len(sc.rig.writes()) == sc.writes_before                         # no second mutation
    # the server side, from the per-event snapshots
    assert len(sc.bridge.snapshots_since(sc.window)) == 1, sc.bridge.trace(sc.window)
    assert duplicate_damage(sc, sc.window, sc.mark) == [], sc.bridge.trace(sc.mark)
    last = sc.bridge.history[-1]
    assert (last["event"], last["key"]) == ("sync_retrieve_done", sc.key)
    assert last["rebuild"]["a"]["queued_keys"] == [sc.key, sc.other]
    assert last["rebuild"]["a"]["restored_keys"] == {sc.key}
    assert last["party_keys"]["b"] == [sc.partner] and last["queued"] == {"a": [], "b": []}
    assert last["party_size"]["a"] == 3, last["party_size"]                  # a re-ack never moves the server's count


def mutated_overworld():
    """RED CONTROL: ONLY the idempotent branch is removed (its guard can no longer be true), so the executor falls
    through to its pre-fix `key not boxed` refusal. lua/gen2/polished_overworld.lua O.client_boxes stays composed."""
    old = "                if already and not already.is_egg then\n"
    assert OVERWORLD.count(old) == 1, "mutant anchor missing: the idempotent branch moved"
    return OVERWORLD.replace(old, "                if false and already and not already.is_egg then\n")


def test_red_control_without_the_idempotent_branch_the_real_client_makes_the_server_damage_the_partner(tmp_path):
    make_rig = functools.partial(MutantRig, overrides={"lua/gen2/polished_overworld.lua": mutated_overworld()})
    sc = duplicate_scenario(tmp_path, make_rig)
    # the mutation took effect through the REAL composed client, and it is not a Lua error
    (failed,) = sc.rig.sent("sync_retrieve_failed")
    assert failed["key"] == sc.key and "key not boxed" in failed["reason"], failed
    assert sc.rig.count() == 4                                              # the first withdraw worked (mutation is narrow)
    assert [m["key"] for m in sc.rig.sent("sync_retrieve_done")] == [sc.key]    # ... and the duplicate was NOT re-acked
    damage = duplicate_damage(sc, sc.window, sc.mark)
    assert damage, "the T2 assertions would not notice the regression"
    text = "\n".join(damage)
    assert "key dropped from the rebuild queued_keys" in text, text
    assert "box_mon for B's mon" in text, text
    # the damage, precisely, at the NACK snapshot
    nack = sc.bridge.history[-1]
    assert (nack["event"], nack["key"]) == ("sync_retrieve_failed", sc.key)
    assert sc.key not in nack["party_keys"]["a"]                            # the server forgot A's mon is in the party
    assert nack["rebuild"]["a"]["queued_keys"] == [sc.other]
    assert [c["key"] for c in nack["queued"]["b"] if c["cmd"] == "box_mon"] == [sc.partner]
    assert nack["party_keys"]["b"] == []                                    # and discarded B's half from B's party model


# ── T4: a duplicate for a mon that has since died ───────────────────────────────────────────────────────────

def kill_in_party(sc, slot=3):
    base = PARTY_MONS + slot * RECORD + HP
    sc.rig.mem[base] = 0
    sc.rig.mem[base + 1] = 0


def test_a_dead_pair_refuses_the_duplicate_by_name_and_the_server_neither_readds_nor_reboxes(tmp_path):
    """SEEDED post-death model: the server has processed the death (pair DEAD, both keys out of the party models, no
    rebuild running). The cartridge still has the HP 0 mon in the party and a delayed duplicate arrives."""
    sc = scenario(tmp_path)
    run_command(sc)
    entry = sc.state.entry_for("a", sc.key)
    entry.status = LinkStatus.DEAD
    sc.state.party_keys["a"].discard(sc.key)
    sc.state.party_keys["b"].discard(sc.partner)
    sc.state.rebuild_pending["a"] = None
    kill_in_party(sc)
    mark = sc.bridge.mark()
    done_before = len(sc.rig.sent("sync_retrieve_done"))
    run_command(sc)
    (failed,) = sc.rig.sent("sync_retrieve_failed")
    assert failed["key"] == sc.key and "dead in the party (hp 0)" in failed["reason"], failed
    assert len(sc.rig.sent("sync_retrieve_done")) == done_before            # never re-announced as retrieved
    sc.rig.frame(120)
    sc.bridge.drain()                                                       # later ticks show the dead mon in the party
    after = sc.bridge.snapshots_since(mark)
    assert [s["event"] for s in after][0] == "sync_retrieve_failed" and "tick" in {s["event"] for s in after}
    assert all(sc.key not in s["party_keys"]["a"] for s in after), sc.bridge.trace(mark)
    assert sc.bridge.commands_since(mark, "b", "box_mon") == [], sc.bridge.trace(mark)
    assert sc.bridge.commands_since(mark, "a", "party_mon", sc.key) == []


def test_characterised_an_alive_pair_whose_death_the_server_has_not_processed_gets_its_partner_reboxed(tmp_path):
    """CHARACTERISATION (today's server, ALIVE pair, the HP 0 mon's death not yet reported): the refusal reads as a
    failed retrieval, so the partner IS re-boxed. The executor's own comment ("a dead pair is not ALIVE: re-boxes
    nobody") holds only once the death has been processed (test above). Pinned so a server change shows here."""
    sc = scenario(tmp_path)
    run_command(sc)
    kill_in_party(sc)
    mark = sc.bridge.mark()
    run_command(sc)
    (failed,) = sc.rig.sent("sync_retrieve_failed")
    assert "dead in the party (hp 0)" in failed["reason"]
    nack = sc.bridge.history[-1]
    assert (nack["event"], nack["key"]) == ("sync_retrieve_failed", sc.key)
    assert sc.key not in nack["party_keys"]["a"]                            # not re-added (it was discarded)
    assert nack["rebuild"]["a"]["queued_keys"] == [sc.other]                # the rebuild key is dropped
    assert [c["key"] for c in nack["queued"]["b"] if c["cmd"] == "box_mon"] == [sc.partner]   # partner re-boxed
    assert nack["party_keys"]["b"] == []
    assert sc.bridge.commands_since(mark, "b", "box_mon", sc.partner) == [(nack["i"], "queued")]


# ── T5: a full party ─────────────────────────────────────────────────────────────────────────────────────────

def test_characterised_a_full_party_refusal_discards_the_key_and_reboxes_the_partner(tmp_path):
    """CHARACTERISATION: the cartridge party is full (6) but the server's model lags by one mon, so it hands out a
    party_mon the client must refuse. The client retries once, then sends sync_retrieve_failed `party full`; the
    server drops the rebuild key and re-boxes the PARTNER (the Soul Link rule: both halves stay together)."""
    sc = scenario(tmp_path, cartridge_party=6, lag_one=True)
    mark = sc.bridge.mark()
    run_command(sc)
    retries = [line for line in sc.rig.log["lines"].values() if "party full, retry" in line]
    assert len(retries) == 1, retries
    (failed,) = sc.rig.sent("sync_retrieve_failed")
    assert failed["key"] == sc.key and failed["reason"] == "party full"
    assert sc.rig.sent("sync_retrieve_done") == [] and sc.rig.count() == 6
    nack = sc.bridge.history[-1]
    assert (nack["event"], nack["key"], nack["reason"]) == ("sync_retrieve_failed", sc.key, "party full")
    assert sc.key not in nack["party_keys"]["a"] and nack["sync_inflight"]["a"] == []
    assert nack["rebuild"]["a"]["queued_keys"] == [sc.other]                # the failed key is dropped from the plan
    assert [c["key"] for c in nack["queued"]["b"] if c["cmd"] == "box_mon"] == [sc.partner]
    assert nack["party_keys"]["b"] == []
    # and the state really hands the partner's box_mon to B on B's next tick (inflight armed)
    (box,) = [c for c in sc.bridge.deliver("b") if c["cmd"] == "box_mon"]
    assert_sync_command(sc.state, "b", box, "box_mon", sc.partner)
    assert [(k, c) for k, c, _ in sc.bridge.history[-1]["sync_inflight"]["b"]] == [(sc.partner, "box_mon")]
    assert sc.bridge.commands_since(mark, "b", "box_mon", sc.partner)
