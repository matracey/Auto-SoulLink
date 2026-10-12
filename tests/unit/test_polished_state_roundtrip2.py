"""Polished withdraw round trips, part 2: real composed Rig events -> real SoulLinkState -> unchanged client commands.

FIXTURE (SEEDED, not admission/capture/whiteout): linked pairs, B's already-withdrawn party and its native
box_to_party events are seeded. The two-pair rebuild has production fields started_at / queued_partner_keys;
A retains one unlinked live fixture mon, so this is not a natural whiteout. The size-lag case seeds A's
party_size=1 while its cartridge has three live mons. A's hello, every delivery tick and all acks come from
its real client. Both players' models are snapshotted after EACH event, before any reply is dispatched.
A sync_retrieve_done updates membership, not party_size: the next real party-bearing tick updates size.
Each scenario has an in-process MutantRig red control; no production source or shared rig is edited.
The Rig's minimal HUD lacks clear_rebuilding; a local helper supplies the REAL shared-HUD method, not a no-op.

Run: pytest tests/unit/test_polished_state_roundtrip2.py -v
"""
from __future__ import annotations

import copy
import functools
import json
import random
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.unit.polished_state_rig import (
    MutantRig,
    PolishedStateBridge,
    assert_sync_command,
    new_state,
    seed_pair,
    seed_rebuild,
)
from tests.unit.test_polished_state_roundtrip import AREA, mutated_overworld
from tests.unit.test_polished_withdraw_path import boxed, boxed_mon, options
from tests.unit.test_polished_write_path import (
    PARTY_COUNT,
    PARTY_END,
    ROOT,
    Rig,
    key_of,
    live_mon,
    party,
)

CLIENT = (Path(ROOT) / "lua/gen2/client.lua").read_text(encoding="utf-8")
STARTED_AT = "2026-10-06T00:00:00+00:00"


def cartridge_keys(rig):
    read = rig.parts.reads.read_party()
    result, why = read if isinstance(read, tuple) else (read, None)
    assert result is not None, why
    mons = list(result["mons"].values())
    assert all(not mon["is_egg"] and mon["hp"] > 0 for mon in mons), "fixture party must be live non-eggs"
    return sorted(mon["key"] for mon in mons)


def complete_rebuild_hud(rig):
    """Supply the missing presentation dependency without changing client/storage/server behaviour."""
    rig.lua.eval("""
        function(client, root)
            local shared = dofile(root .. "/lua/hud.lua")
            for i = 1, 100 do
                local name, value = debug.getupvalue(client.handle_command, i)
                if name == nil then break end
                if name == "hud" then
                    value.clear_rebuilding = shared.clear_rebuilding
                    return
                end
            end
            error("Rig client has no injected HUD upvalue")
        end
    """)(rig.client, ROOT)


class RoundTripBridge(PolishedStateBridge):
    """Small local extension: actual client ticks, ordered event pumping, and consumption of every A reply.

    No bare deliver() ticks. No batch cursor jump. Replies are delivered before the next pending command;
    resulting client events are fed immediately, so the first ack is recorded before the second withdraw.
    B has no client in this fixture: its seeded native withdrawal must return only noop.
    """

    def __init__(self, rig, state, *, tick_after_withdraw=True):
        super().__init__(rig, state)
        self.tick_after_withdraw = tick_after_withdraw
        self.delivered = []

    def record(self, player, msg, returned):
        snap = super().record(player, msg, returned)
        snap["message"] = copy.deepcopy(msg)
        snap["cartridge_keys"] = cartridge_keys(self.rig)
        snap["cartridge_count"] = self.rig.count()
        return snap

    def lua_value(self, value):
        """lupa's table_from is shallow: a nested list/dict would reach Lua as a Python object (IndexError on the
        client's own indexing). The state's reply dict itself stays untouched; only the value handed in is deep."""
        if isinstance(value, dict):
            return self.rig.lua.table_from({k: self.lua_value(v) for k, v in value.items()})
        if isinstance(value, (list, tuple)):
            return self.rig.lua.table_from([self.lua_value(v) for v in value])
        return value

    def send_unchanged(self, command):
        before = copy.deepcopy(command)
        self.rig.send_command({k: self.lua_value(v) for k, v in command.items()})
        assert command == before, "state reply dict changed on its way into the client"
        self.drain()

    def dispatch(self, returned, snapshot_index):
        for command in returned:
            if command["cmd"] == "noop":
                continue                            # a noop has no client operation
            self.delivered.append((snapshot_index, command))
            self.send_unchanged(command)
            if command["cmd"] == "party_mon" and self.tick_after_withdraw:
                self.tick()                         # observe the new count before the next withdraw

    def drain(self, player=None):
        assert player in (None, "a"), "the fixture has only an A cartridge"
        fresh = []
        while self.cursor < len(self.rig.sent(None)):
            assert len(self.history) < 300, "round-trip event pump did not settle"
            msg = self.rig.sent(None)[self.cursor]
            returned = self.feed("a", msg)           # feed() records both players before dispatch
            snapshot_index = self.history[-1]["i"]
            self.cursor += 1                        # advance this event, not the unread batch
            fresh.append(msg)
            self.dispatch(returned, snapshot_index)
        return fresh

    def tick(self):
        mark = self.mark()
        self.rig.client.send_tick(self.rig.client)   # real wire snapshot from the cartridge
        self.drain()
        ticks = [s for s in self.snapshots_since(mark) if s["player"] == "a" and s["event"] == "tick"]
        assert ticks, "the actual client emitted no tick"
        return ticks[0]

    def native_withdraw(self, key):
        returned = self.feed("b", {"event": "box_to_party", "key": key})
        assert all(c["cmd"] == "noop" for c in returned), "unexpected command for seeded B (no B cartridge)"


@dataclass
class Scenario:
    rig: object
    state: object
    bridge: RoundTripBridge
    keys: tuple[str, ...]
    partners: tuple[str, ...]
    base_keys: tuple[str, ...]
    mark: int
    seeded_size: int
    rebuild: bool


def prepare(tmp_path, mons, *, make_rig=Rig, rebuild=False, lag_size=None, tick_after_withdraw=True):
    """SEEDED links/partner/rebuild only; hello, tick replies and A acknowledgements are observed."""
    count = 2 if rebuild else 1
    rig = make_rig(mons)
    options(rig)
    if rebuild:
        complete_rebuild_hud(rig)
    boxed_mons = [boxed_mon(rng_seed=909 + i, species=99 + i) for i in range(count)]
    for i, mon in enumerate(boxed_mons):
        boxed(rig, mon, slot=i + 1, bank=i + 1, entry=7 + i)
    keys = tuple(key_of(mon) for mon in boxed_mons)
    partners = tuple(key_of(live_mon(random.Random(555 + i), 30 + i, ot="KRIS", nick=f"PARTNER{i}"))
                     for i in range(count))
    base_keys = tuple(key_of(mon) for mon in mons)
    assert len({*keys, *partners, *base_keys}) == len(keys) + len(partners) + len(base_keys)
    state = new_state(tmp_path)
    bridge = RoundTripBridge(rig, state, tick_after_withdraw=tick_after_withdraw)
    assert rig.arm_writes(), "the composed client never enabled writes"
    bridge.drain()
    assert bridge.history[-1]["party_keys"]["a"] == sorted(base_keys)
    assert bridge.history[-1]["party_size"]["a"] == len(mons)
    for i, (key, partner) in enumerate(zip(keys, partners, strict=True)):
        seed_pair(state, key, partner, area=AREA if i == 0 else "route_30", a_party=False, b_party=True)
    state.party_size["b"] = len(partners)
    if rebuild:
        seed_rebuild(state, "a", keys)
        state.rebuild_pending["a"].update(started_at=STARTED_AT, queued_partner_keys=list(partners))
    else:
        assert state.rebuild_pending == {"a": None, "b": None}
    if lag_size is not None:
        state.party_size["a"] = lag_size             # SEEDED stale count, never a fabricated client tick
    return Scenario(rig, state, bridge, keys, partners, base_keys, bridge.mark(), state.party_size["a"], rebuild)


def request_and_deliver(sc):
    for partner in sc.partners:
        sc.bridge.native_withdraw(partner)
    sc.bridge.tick()                                # the real state's real tick reply supplies party_mon


def model_is_consistent(sc):
    """Assert every post-event snapshot, including the short ack->next-tick size lag.

    The cartridge snapshot bounds each acknowledgement: an ack must never claim a still-boxed key. Queue/inflight
    bookkeeping follows actual state replies, not an assumed ordering of the two rebuild commands.
    """
    restored, queued, inflight = set(), set(), set()
    size = sc.seeded_size
    for snap in sc.bridge.snapshots_since(sc.mark):
        msg = snap["message"]
        label = f"#{snap['i']} {snap['player']}:{snap['event']}:{snap['key']}"
        if snap["player"] == "b" and snap["event"] == "box_to_party":
            queued.add(sc.keys[sc.partners.index(snap["key"])])
        if snap["player"] == "a":
            if msg.get("event") in ("hello", "tick", "safe") and "party" in msg:
                assert sorted(m["key"] for m in msg["party"]) == snap["cartridge_keys"], label
                size = len(msg["party"])
            assert snap["event"] != "sync_retrieve_failed", f"{label}: retrieval failed"
            if snap["event"] == "sync_retrieve_done":
                assert snap["key"] in snap["cartridge_keys"], f"{label}: acknowledged a still-boxed mon"
                assert snap["key"] in sc.keys, f"{label}: ack for an unrequested mon"
                restored.add(snap["key"])
                queued.discard(snap["key"])
                inflight.discard(snap["key"])
            delivered = [c for c in snap["returned"] if c["cmd"] == "party_mon"]
            for command in delivered:
                assert command["key"] in sc.keys, f"{label}: unexpected party_mon"
                inflight.add(command["key"])
        assert snap["party_keys"]["a"] == sorted(set(sc.base_keys) | restored), f"{label}: A membership"
        assert snap["party_keys"]["b"] == sorted(sc.partners), f"{label}: partner membership"
        assert snap["party_size"] == {"a": size, "b": len(sc.partners)}, f"{label}: party_size not last observed snapshot"
        assert sorted(c["key"] for c in snap["queued"]["a"] if c["cmd"] == "party_mon") == sorted(queued), label
        assert all(c["cmd"] == "party_mon" for c in snap["queued"]["a"]), f"{label}: unexpected A queue"
        assert snap["queued"]["b"] == [], f"{label}: partner command queued"
        assert [(key, cmd) for key, cmd, _ in snap["sync_inflight"]["a"]] == sorted((key, "party_mon") for key in inflight), label
        assert all(n > 0 for _, _, n in snap["sync_inflight"]["a"]), f"{label}: expired inflight retained"
        assert snap["sync_inflight"]["b"] == [], f"{label}: partner inflight changed"
        assert snap["rebuild"]["b"] is None, f"{label}: partner rebuild changed"
        rb = snap["rebuild"]["a"]
        if sc.rebuild and restored != set(sc.keys):
            assert rb is not None, f"{label}: rebuild ended before both acknowledgements"
            assert rb["queued_keys"] == list(sc.keys), f"{label}: completing one pair disturbed the other's key"
            assert rb["restored_keys"] == restored, f"{label}: rebuild confirmation drift"
            assert rb["queued_partner_keys"] == list(sc.partners), f"{label}: partner plan changed"
            assert rb["started_at"] == STARTED_AT, f"{label}: rebuild visit changed"
        else:
            assert rb is None, f"{label}: unexpected/non-completed rebuild"
    assert sc.bridge.commands_since(sc.mark, "b", "box_mon") == [], sc.bridge.trace(sc.mark)


def duplicate_is_safe(sc, mark, before_keys, before_size):
    for snap in sc.bridge.snapshots_since(mark):
        label = f"#{snap['i']} {snap['event']}"
        assert snap["party_keys"]["a"] == before_keys, f"{label}: duplicate changed A's party_keys"
        assert snap["party_keys"]["a"].count(sc.keys[0]) == 1, f"{label}: duplicate re-added an existing key"
        assert snap["party_size"]["a"] == before_size, f"{label}: duplicate changed party_size"
        assert snap["party_keys"]["b"] == list(sc.partners), f"{label}: duplicate discarded the partner"
        assert snap["sync_inflight"] == {"a": [], "b": []}, f"{label}: duplicate left inflight"
        assert snap["rebuild"] == {"a": None, "b": None}, f"{label}: unexpected rebuild"
        assert snap["queued"] == {"a": [], "b": []}, f"{label}: duplicate queued a command"
        assert all(c["cmd"] == "noop" for c in snap["returned"]), f"{label}: duplicate returned a command"
    assert sc.bridge.commands_since(mark, "b", "box_mon", sc.partners[0]) == [], sc.bridge.trace(mark)


def cartridge_bytes(rig):
    return tuple(rig.mem[a] or 0 for a in range(PARTY_COUNT, PARTY_END)), rig.img.snap()


@pytest.mark.parametrize("idempotent", [True, False], ids=["reack", "no-reack"])
def test_no_rebuild_duplicate_reacks_without_adding_or_reboxing(tmp_path, idempotent):
    make_rig = Rig if idempotent else functools.partial(
        MutantRig, overrides={"lua/gen2/polished_overworld.lua": mutated_overworld()})
    sc = prepare(tmp_path, party(3), make_rig=make_rig)
    request_and_deliver(sc)
    model_is_consistent(sc)                         # the narrow mutant's FIRST withdraw still succeeds
    assert sc.rig.count() == 4
    assert [e["key"] for e in sc.rig.sent("sync_retrieve_done")] == list(sc.keys)
    commands = [cmd for _, cmd in sc.bridge.delivered if cmd["cmd"] == "party_mon"]
    (command,) = commands
    assert_sync_command(sc.state, "a", command, "party_mon", sc.keys[0])
    mark = sc.bridge.mark()
    before = sc.bridge.history[-1]
    writes, image = len(sc.rig.writes()), cartridge_bytes(sc.rig)
    sc.bridge.send_unchanged(command)                # SEEDED delayed duplicate of the SAME state-returned dict
    assert sc.rig.count() == 4 and len(sc.rig.writes()) == writes
    assert cartridge_bytes(sc.rig) == image, "a duplicate mutated the cartridge twice"
    after = sc.bridge.snapshots_since(mark)
    assert after, "the duplicate emitted no server-visible answer"
    if idempotent:
        assert sc.rig.sent("sync_retrieve_failed") == []
        assert [e["key"] for e in sc.rig.sent("sync_retrieve_done")] == [sc.keys[0], sc.keys[0]]
        assert after[0]["event"] == "sync_retrieve_done"
        duplicate_is_safe(sc, mark, before["party_keys"]["a"], before["party_size"]["a"])
    else:
        (failed,) = sc.rig.sent("sync_retrieve_failed")
        assert failed["key"] == sc.keys[0] and "key not boxed" in failed["reason"]
        nack = next(s for s in after if s["event"] == "sync_retrieve_failed")
        assert nack["rebuild"] == {"a": None, "b": None}  # NO rebuild key exists to drop or protect the partner
        assert sc.keys[0] not in nack["party_keys"]["a"]
        assert sc.partners[0] not in nack["party_keys"]["b"]
        (box,) = [c for c in nack["queued"]["b"] if c["cmd"] == "box_mon"]
        assert_sync_command(sc.state, "b", box, "box_mon", sc.partners[0])
        with pytest.raises(AssertionError, match="duplicate changed A's party_keys"):
            duplicate_is_safe(sc, mark, before["party_keys"]["a"], before["party_size"]["a"])


def premature_other_ack(keys):
    """RED: the composed client additionally ACKs the OTHER still-boxed rebuild key on each success."""
    old = ('                send("sync_retrieve_done", { key = cmd.key })\n'
           '                hud.show("↑ " .. name .. " unboxed", 100, 255, 160, 200)\n')
    assert CLIENT.count(old) == 1, "mutant success-ack anchor moved"
    extra = (f'                if cmd.key == {json.dumps(keys[0])} then\n'
             f'                    send("sync_retrieve_done", {{ key = {json.dumps(keys[1])} }})\n'
             f'                elseif cmd.key == {json.dumps(keys[1])} then\n'
             f'                    send("sync_retrieve_done", {{ key = {json.dumps(keys[0])} }})\n'
             '                end\n')
    return CLIENT.replace(old, old.splitlines(keepends=True)[0] + extra + old.splitlines(keepends=True)[1])


@pytest.mark.parametrize("honest_ack", [True, False], ids=["two-acks", "early-other-ack"])
def test_two_pair_production_shaped_rebuild_keeps_the_other_key_until_its_own_ack(tmp_path, honest_ack):
    keys = tuple(key_of(boxed_mon(rng_seed=909 + i, species=99 + i)) for i in range(2))
    make_rig = Rig if honest_ack else functools.partial(
        MutantRig, overrides={"lua/gen2/client.lua": premature_other_ack(keys)})
    sc = prepare(tmp_path, party(1), make_rig=make_rig, rebuild=True)
    request_and_deliver(sc)
    commands = [(i, cmd) for i, cmd in sc.bridge.delivered if cmd["cmd"] == "party_mon"]
    assert len(commands) == 2 and {cmd["key"] for _, cmd in commands} == set(keys)
    assert all(sc.bridge.history[i]["event"] == "tick" for i, _ in commands), "withdraw bypassed a real tick reply"
    assert sc.rig.count() == 3 and set(cartridge_keys(sc.rig)) == {*sc.base_keys, *keys}
    assert sc.rig.sent("sync_retrieve_failed") == []
    if honest_ack:
        model_is_consistent(sc)
        done = [s for s in sc.bridge.snapshots_since(sc.mark) if s["event"] == "sync_retrieve_done"]
        assert len(done) == 2 and {s["key"] for s in done} == set(keys)
        first, last = done
        other = (set(keys) - {first["key"]}).pop()
        assert first["rebuild"]["a"]["queued_keys"] == list(keys)
        assert first["rebuild"]["a"]["restored_keys"] == {first["key"]}
        assert other not in first["party_keys"]["a"] and other not in first["cartridge_keys"]
        assert [(key, cmd) for key, cmd, _ in first["sync_inflight"]["a"]] == [(other, "party_mon")]
        first_tick = next(s for s in sc.bridge.history[first["i"] + 1:last["i"]] if s["event"] == "tick")
        assert first_tick["party_size"]["a"] == 2 and first_tick["rebuild"]["a"]["restored_keys"] == {first["key"]}
        assert last["rebuild"]["a"] is None and last["sync_inflight"]["a"] == []
        assert last["returned"] == [{"cmd": "rebuild_done"}]
        end = sc.bridge.history[-1]
        assert end["party_size"] == {"a": 3, "b": 2}
        assert end["rebuild"] == {"a": None, "b": None}
        assert end["queued"] == {"a": [], "b": []} and end["sync_inflight"] == {"a": [], "b": []}
    else:
        early = next(s for s in sc.bridge.snapshots_since(sc.mark)
                     if s["event"] == "sync_retrieve_done" and s["key"] not in s["cartridge_keys"])
        assert early["rebuild"]["a"] is None, "the deliberate extra ACK did not prematurely finish the rebuild"
        with pytest.raises(AssertionError, match="acknowledged a still-boxed mon"):
            model_is_consistent(sc)


def tick_without_party():
    """RED: remove ONLY the party field of the client's tick; hello and the executor are unchanged."""
    old = ('        local sent = send(event or "tick", {\n'
           '            party = party, has_pokeballs = self.has_pokeballs, ball_count = ball_count(),\n')
    new = ('        local sent = send(event or "tick", {\n'
           '            has_pokeballs = self.has_pokeballs, ball_count = ball_count(),\n')
    assert CLIENT.count(old) == 1, "mutant tick-payload anchor moved"
    return CLIENT.replace(old, new)


def tick_updates_size(sc, tick):
    assert tick["party_size"]["a"] == 4, "real client tick did not replace the seeded party_size with four"
    assert len(tick["message"]["party"]) == 4
    assert tick["party_keys"]["a"] == sorted((*sc.base_keys, *sc.keys))
    assert tick["party_size"]["a"] == tick["cartridge_count"]
    assert tick["queued"] == {"a": [], "b": []} and tick["sync_inflight"] == {"a": [], "b": []}
    assert tick["rebuild"] == {"a": None, "b": None}


@pytest.mark.parametrize("party_bearing", [True, False], ids=["four-live", "tick-no-party"])
def test_real_client_tick_counts_three_live_mons_plus_the_withdrawn_one(tmp_path, party_bearing):
    make_rig = Rig if party_bearing else functools.partial(
        MutantRig, overrides={"lua/gen2/client.lua": tick_without_party()})
    sc = prepare(tmp_path, party(3), make_rig=make_rig, lag_size=1, tick_after_withdraw=False)
    request_and_deliver(sc)
    assert sc.rig.count() == 4 and all(sc.rig.hp(i) > 0 for i in range(4))
    assert [e["key"] for e in sc.rig.sent("sync_retrieve_done")] == list(sc.keys)
    assert sc.rig.sent("sync_retrieve_failed") == []
    done = next(s for s in sc.bridge.snapshots_since(sc.mark) if s["event"] == "sync_retrieve_done")
    assert done["party_size"]["a"] == (3 if party_bearing else 1), "ACK must not invent a party count"
    tick = sc.bridge.tick()
    model_is_consistent(sc)                         # checks last-observed size even in the deliberate omission control
    if party_bearing:
        tick_updates_size(sc, tick)
        delivery = next(s for s in sc.bridge.snapshots_since(sc.mark) if s["event"] == "tick")
        assert len(delivery["message"]["party"]) == 3 and delivery["party_size"]["a"] == 3
        assert sc.keys[0] not in delivery["party_keys"]["a"]
    else:
        assert "party" not in tick["message"] and tick["party_size"]["a"] == sc.seeded_size == 1
        assert tick["cartridge_count"] == 4          # the same successful physical withdraw; only the wire field changed
        with pytest.raises(AssertionError, match="did not replace the seeded party_size"):
            tick_updates_size(sc, tick)
