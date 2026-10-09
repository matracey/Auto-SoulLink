"""lua/gen3/client.lua (P4 C4-2b): the Gen 3 driver over lua/core, built by the PRODUCTION
lua/gen3/entry.lua and driven by tests/unit/gen3_world.py (fake GBA + fake server; every sent
line is schema-checked there).

First falsifiers:
  * an injected faint signal for a party mon yields exactly one schema-valid faint event with
    the right key;
  * no hello is sent before the checkpoint predicate holds;
  * a force_faint on the active battler holds where mechanism P does not apply (doubles), then
    lands on a simulated switch-out; in singles it commits P+H on every title (C4-ACTIVE-FAINT-P,
    G4-PH).
"""
from __future__ import annotations

import json
import re

import pytest

from tests.unit.gen3_world import ARTIFACTS, REPO, World, key_of, lua_to_py, mon_record

OT = 0x0000ABCD
A, B, C = 0x11111111, 0x22222222, 0x33333333
KA, KB, KC = key_of(A, OT), key_of(B, OT), key_of(C, OT)
FOE = mon_record(0x77777777, 0x1234, species=19, level=3)


def party(*pids, hp=20):
    return [mon_record(p, OT, species=4 + i, nickname=f"MON{i}", hp=hp) for i, p in enumerate(pids)]


def live(pack="gen3_frlg", title="firered", kind=None, pids=(A, B), frames=60):
    """A connected client past its first validation: hello sent, writes enabled."""
    w = World(pack, title, kind)
    w.set_party(party(*pids))
    w.step_to(frames)
    assert w.client.writes_enabled is True
    return w


def write_reasons(w):
    return [str(r.reason) for r in lua_to_py_list(w.parts.writes.log)]


def lua_to_py_list(t):
    return [t[i] for i in range(1, len(t) + 1)]


# ── falsifiers ────────────────────────────────────────────────────────────────────────────

def test_falsifier_a_faint_signal_yields_exactly_one_faint_with_the_right_key():
    w = live()
    w.set_party([mon_record(A, OT, species=4), mon_record(B, OT, species=5, hp=0)])
    w.fire("faint")
    w.step()
    (faint,) = w.events("faint")
    assert faint["key"] == KB and faint["area_id"] == "route_1"
    w.fire("faint")                                          # the same faint again: no repeat
    w.step()
    assert len(w.events("faint")) == 1


def test_falsifier_no_hello_out_of_battle_before_the_checkpoint_predicate_holds():
    w = World()
    w.set_party(party(A))
    w.break_checkpoint()
    w.step(200)
    assert w.events("hello") == [] and w.sent == []
    w.overworld_safe()
    w.step()
    assert len(w.events("hello")) == 1


def test_in_battle_hello_does_not_wait_for_the_checkpoint():
    """PLAN's rule is "checkpoint OR battle" (Gen 1 parity): a reconnect mid-battle hellos."""
    w = World()
    w.set_party(party(A))
    w.enter_battle([FOE], fire=False)                         # the in_battle clause fails the checkpoint
    w.step()
    (hello,) = w.events("hello")
    assert hello["in_battle"] is True


def test_falsifier_an_active_battler_force_faint_holds_then_lands_on_switch_out():
    """RR doubles: mechanism P is singles-only, so the active-battler hold stands (G4-PH made RR
    singles P+H; this falsifier moved to doubles to keep the hold-then-switch-out path covered)."""
    w = live("gen3_rr", "radical_red", pids=(A, B, C))
    w.battle_ok = True
    w.enter_battle([FOE, FOE], doubles=True, active=(0, 2))
    w.command(cmd="force_faint", key=KA)
    w.step(5)
    assert w.party_hp(0) == 20 and w.writes == []            # held: the active battler
    assert w.client.battle_pending_count(w.client) == 1
    w.set_active([1, 2])                                      # switched out
    w.step()
    assert w.party_hp(0) == 0 and w.client.battle_pending_count(w.client) == 0
    assert write_reasons(w) == ["battle_faint"]


# ── M1: hello and tick schema-valid on both packs ─────────────────────────────────────────

@pytest.mark.parametrize("pack,title,kind", ARTIFACTS)
def test_m1_hello_and_tick_are_schema_valid_on_every_artifact(pack, title, kind):
    w = World(pack, title, kind)
    w.set_party(party(A, B))
    w.set_balls(5)
    w.step_to(90)
    (hello,) = w.events("hello")
    assert hello["rom_type"] == {"firered": "firered", "leafgreen": "leafgreen",
                                 "radical_red": "firered_rr"}[title]
    assert hello["foundation"] == pack and hello["artifact_kind"] == kind
    assert [m["key"] for m in hello["party"]] == [KA, KB]
    assert all(len(m["blob_hex"]) == 200 for m in hello["party"])
    ticks = w.events("tick")
    assert len(ticks) == 3 and ticks[-1]["ball_count"] == 5 and ticks[-1]["has_pokeballs"] is True
    # empty lists go on the wire as arrays (conformance item 1, docs/protocol.md A19)
    assert '"enemy_party":[]' in [line for line in w.lines if '"tick"' in line][0].replace(" ", "")


def test_hello_carries_the_trainer_badges_and_seeded_boxes_on_frlg():
    w = World()
    w.set_party(party(A))
    w.set_badges(0b101)
    w.set_box(0, 3, mon_record(C, OT, species=7))
    w.step()
    (hello,) = w.events("hello")
    assert hello["ot_id"] == OT and hello["trainer_name"] == "RED" and hello["badges"] == 5
    assert [(e["box"], e["slot"], e["key"]) for e in hello["pc_boxes"]] == [(0, 3, KC)]


def test_rr_hello_carries_the_trainer_read():
    """RR-DURABLE: gen3_rr carries SB2_NAME_OFFSET now (the trade journal binds to the trainer)."""
    w = World("gen3_rr", "radical_red")
    w.set_party(party(A))
    w.step()
    (hello,) = w.events("hello")
    assert hello["ot_id"] and hello["trainer_name"] and hello["party"][0]["key"] == KA


def test_a_pre_game_save_sends_no_hello():
    w = World()
    w.set_trainer(0)
    w.step(100)
    assert w.events("hello") == []


# ── the reducer ─────────────────────────────────────────────────────────────────────────

def test_an_enemy_faint_reports_nothing():
    w = live()
    w.enter_battle([FOE])
    w.fire("faint")
    w.step()
    assert w.events("faint") == []


def test_a_caught_mon_is_one_capture_not_a_gift():
    w = live()
    w.set_balls(5)
    w.step(30)                                            # the tick latches has_pokeballs
    w.enter_battle([FOE])
    w.set_party(party(A, B, C))
    w.fire("capture_wild")
    w.fire("mon_given")
    w.step()
    (cap,) = w.events("capture")
    assert cap["key"] == KC and cap["area_id"] == "route_1" and "gift" not in cap
    w.leave_battle(outcome=7)
    w.step()
    assert w.events("no_catch") == []


def test_a_catch_the_client_could_not_attribute_never_dead_zones_the_area():
    w = live()
    w.set_balls(5)
    w.step(30)
    w.enter_battle([FOE])
    w.step(30)
    w.fire("capture_wild")                                     # caught, but no new key is readable
    w.step()
    w.leave_battle(outcome=4)
    w.step()
    assert w.events("capture") == [] and w.events("no_catch") == []


def test_a_gift_is_a_capture_with_gift_true():
    w = live()
    w.set_party(party(A, B, C))
    w.fire("mon_given")
    w.step()
    (cap,) = w.events("capture")
    assert cap["key"] == KC and cap["gift"] is True


FULL_STATS = {"level": 5, "maxHP": 20, "attack": 11, "defense": 12, "speed": 13, "spAtk": 14,
              "spDef": 15, "pp1": 35, "pp2": 30, "pp3": 0, "pp4": 0}


@pytest.mark.parametrize("pack,title", [("gen3_frlg", "firered"), ("gen3_rr", "radical_red")])
def test_party_capture_carries_full_stats_before_a_link_can_request_retrieval(pack, title):
    """The server may link two captures before box_mon sends its later stats_cache."""
    w = live(pack, title)
    w.set_balls(5)
    w.step(30)
    w.enter_battle([FOE])
    w.set_party(party(A, B, C))
    w.fire("capture_wild")
    w.fire("mon_given")
    w.step()
    (cap,) = w.events("capture")
    assert cap["key"] == KC and cap["stats"] == FULL_STATS


def test_a_catch_with_a_full_party_is_a_boxed_capture():
    w = live(pids=(A, B))
    w.enter_battle([mon_record(C, OT, species=19)])            # the wild mon, as caught
    w.set_box(1, 0, mon_record(C, OT, species=19))
    w.fire("capture_wild")
    w.fire("pc_move")
    w.step()
    (cap,) = w.events("capture")
    assert cap["key"] == KC and cap["in_box"] is True and cap["species_id"] == 19
    # MAJOR 4 (C4-2d): the complete stats the server caches and boxes.lua needs on withdraw
    assert cap["stats"] == FULL_STATS and cap["level"] == 5 and cap["maxHP"] == 20


def test_battle_end_recovers_a_missed_party_capture_signal():
    w = caught_world()
    w.enter_battle([FOE])
    w.step()
    w.set_party(party(A, B, C))
    w.step(5)
    assert w.events("capture") == []
    w.leave_battle(outcome=7)
    w.step()

    (cap,) = w.events("capture")
    assert cap["key"] == KC and cap["area_id"] == "route_1"
    assert len(acq(w, "fallback via=battle_end", "unknown=1")) == 1


def test_battle_end_recovers_a_missed_boxed_capture_signal():
    w = live(pids=(A, B))
    w.set_balls(5)
    w.step(30)
    w.logs.clear()
    w.enter_battle([mon_record(C, OT, species=19)])
    w.step()
    w.set_box(1, 0, mon_record(C, OT, species=19))
    w.step(5)
    w.leave_battle(outcome=7)
    w.step()

    (cap,) = w.events("capture")
    assert cap["key"] == KC and cap["in_box"] is True and cap["area_id"] == "route_1"
    assert len(acq(w, "fallback via=battle_end", "unknown=1")) == 1


@pytest.mark.parametrize("pack,title", [("gen3_frlg", "firered"), ("gen3_rr", "radical_red")])
def test_major4_party_to_box_carries_the_complete_stats_on_both_packs(pack, title):
    w = live(pack, title, pids=(A, B))
    w.set_party(party(A))
    w.set_box(0, 0, mon_record(B, OT, species=5))
    w.fire("pc_deposit")
    w.step()
    assert w.events("party_to_box")[0]["stats"] == FULL_STATS


def test_a_failed_wild_encounter_sends_one_no_catch():
    w = live()
    w.set_balls(3)
    w.step(30)
    w.enter_battle([FOE])
    w.step(30)                                                # a tick notes the foe
    w.leave_battle(outcome=4)
    w.step()
    (nc,) = w.events("no_catch")
    assert nc == {**nc, "area_id": "route_1", "species_id": 19, "level": 3}
    w.step(31)
    assert len(w.events("safe")) == 1


def test_frlg_a_failed_wild_battle_in_a_gift_area_sends_no_no_catch():
    """OMP cx-daf0f544 #4: oaks_lab (FR 4:3) is in the pack's gift_areas.ids, so the same failed
    battle that dead-zones route_1 above never dead-zones it."""
    w = live()
    assert "oaks_lab" in w.wc["gift_areas"]["ids"]
    w.set_location(4, 3)
    w.set_balls(3)
    w.step(30)
    w.enter_battle([FOE])
    w.step(30)
    w.leave_battle(outcome=4)
    w.step()
    assert w.events("no_catch") == []


def test_a_trainer_battle_sends_trainer_battle_start_once_and_no_no_catch():
    w = live()
    w.set_balls(3)
    w.enter_battle([FOE], trainer_id=42)
    w.step(61)
    assert [e["trainer_id"] for e in w.events("trainer_battle_start")] == [42]
    tick = [t for t in w.events("tick") if t["in_battle"]][-1]
    assert tick["is_trainer_battle"] is True and tick["trainer_id"] == 42
    assert tick["enemy_party"][0]["species_id"] == 19
    w.leave_battle()
    w.step()
    assert w.events("no_catch") == []


def test_whiteout_is_sent_once_per_whiteout():
    w = live()
    w.fire("whiteout")
    w.fire("whiteout")
    w.step()
    assert len(w.events("whiteout")) == 1
    w.fire("whiteout")
    w.step()
    assert len(w.events("whiteout")) == 2


def test_a_pc_deposit_and_withdraw_are_party_to_box_and_box_to_party():
    w = live(pids=(A, B))
    w.set_party(party(A))
    w.set_box(0, 0, mon_record(B, OT, species=5))
    w.fire("pc_deposit")
    w.step()
    (dep,) = w.events("party_to_box")
    assert dep["key"] == KB and dep["stats"] == FULL_STATS
    w.set_box(0, 0, None)
    w.set_party(party(A, B))
    w.fire("pc_withdraw")
    w.step()
    assert [e["key"] for e in w.events("box_to_party")] == [KB]


def test_a_mon_picked_up_in_the_pc_is_reported_only_when_placed():
    w = live(pids=(A, B))
    w.set_party(party(A))
    w.fire("pc_withdraw")                                      # carried in the cursor
    w.step()
    assert w.events("party_to_box") == []
    w.set_box(2, 5, mon_record(B, OT, species=5))
    w.fire("pc_box_place")
    w.step()
    assert [e["key"] for e in w.events("party_to_box")] == [KB]


def test_map_load_sends_area_enter():
    w = live()
    w.set_location(1, 0)
    w.fire("map_load")
    w.step()
    (area,) = w.events("area_enter")
    assert area["area_id"] == "viridian_forest"


def test_the_save_site_flushes_the_battery():
    w = live()
    w.fire("save")
    w.step()
    assert w.saveram_calls == 1


def test_a_borrowed_party_freezes_the_diff_and_the_tick_party():
    w = live(pids=(A, B))
    w.enter_battle([FOE])
    w.set_party([mon_record(0x99999999, 0x5555, hp=0)])        # a partner's party in RAM
    w.fire("faint")
    w.step(30)
    assert w.events("faint") == []
    assert "party" not in [t for t in w.events("tick") if t["in_battle"]][-1]
    w.set_party(party(A, B))
    w.step(30)
    assert "party" in w.events("tick")[-1]


@pytest.mark.parametrize("begin_kind", ["borrowed_party_begin", "borrowed_party_opponent_begin"])
def test_rr_prebattle_party_loan_holds_own_commands_and_borrowed_whiteout_until_restore(begin_kind):
    w = live("gen3_rr", "radical_red", "companion", pids=(A, B))
    w.fire(begin_kind)
    w.set_party([mon_record(C, 0x9999, species=7, hp=0)])
    w.fire("faint")
    w.fire("whiteout")
    w.command(cmd="force_faint", key=KB)
    w.step()
    assert w.client.driver.tick_fields()["party_hidden"] is True
    assert not any(w.events(event) for event in ("capture", "faint", "whiteout", "party_to_box", "box_to_party"))
    assert w.writes == []
    w.enter_battle([FOE])
    w.step()
    w.set_party(party(A, B))
    w.fire("borrowed_party_end")
    w.step()  # the restored record may precede battle_end; still withheld
    assert w.client.driver.tick_fields()["party_hidden"] is True and w.writes == []
    w.leave_battle()
    w.step(3)
    assert w.client.driver.tick_fields()["party_hidden"] is not True
    assert w.party_hp(1) == 0  # the old-key command lands once on the own record


def test_rr_borrow_cancel_before_battle_and_nested_begin_keep_own_preimage():
    w = live("gen3_rr", "radical_red", "companion", pids=(A, B))
    w.fire("borrowed_party_end")  # an end without a begin has no authority
    w.fire("borrowed_party_begin")
    w.set_party([mon_record(C, 0x9999, species=7)])
    w.fire("borrowed_party_begin")  # must not replace A/B with the loaner as its own base
    w.step()
    assert w.client.driver.tick_fields()["party_hidden"] is True
    w.set_party(party(A, B))
    w.fire("borrowed_party_end")
    w.step()
    assert w.client.driver.tick_fields()["party_hidden"] is not True
    assert not w.events("whiteout") and w.writes == []


def test_rr_borrow_restore_mismatch_stays_hidden_until_reset():
    w = live("gen3_rr", "radical_red", "companion", pids=(A, B))
    w.fire("borrowed_party_begin")
    w.set_party([mon_record(C, 0x9999, species=7)])
    w.fire("borrowed_party_end")
    w.set_party([mon_record(A, OT), mon_record(C, OT)])
    w.step(2)
    assert w.client.driver.tick_fields()["party_hidden"] is True
    assert any("borrowed party restore held" in line for line in w.logs)
    w.client.driver.on_reset()
    w.set_party(party(A, B))
    assert w.client.driver.tick_fields()["party_hidden"] is not True


def test_open_kind_trade_done_reports_a_key_change_with_the_npc_trade_reason():
    w = live(pids=(A, B))
    w.fire("trade_begin")
    w.step()
    w.set_party([mon_record(A, OT), mon_record(C, 0x9999, species=122)])
    w.fire("trade_done")
    w.step()
    (kc,) = w.events("key_change")
    assert kc["old_key"] == KB and kc["new_key"] == key_of(C, 0x9999) and kc["reason"] == "npc_trade"


def test_rr_nature_change_pairs_actual_pid_preimage_before_same_frame_store():
    w = live("gen3_rr", "radical_red", "companion", pids=(A, B))
    w.regs["R4"] = w.party_base() + 100
    w.fire("nature_change_begin")
    w.set_party([mon_record(A, OT), mon_record(C, OT, species=5)])
    w.fire("nature_change")
    w.step()
    assert [(e["old_key"], e["new_key"], e["reason"]) for e in w.events("key_change")] == [
        (KB, key_of(C, OT), "nature_change")]
    assert all(not w.events(name) for name in ("capture", "faint", "party_to_box", "box_to_party"))


def test_rr_nature_change_waits_for_visible_journal_before_alias():
    from tests.unit.gen3_trade_journal_model import JournalModel

    model = JournalModel()
    w = World("gen3_rr", "radical_red", "companion", journal=model)
    w.set_party(party(A, B))
    w.step_to(60)
    journal = model.journal
    original_hidden = journal.hidden
    held = {"yes": True}
    journal.hidden = lambda self: held["yes"] or original_hidden(self)
    w.regs["R4"] = w.party_base() + 100
    w.fire("nature_change_begin")
    w.set_party([mon_record(A, OT), mon_record(C, OT, species=5)])
    w.fire("nature_change")
    w.step(2)
    assert w.events("key_change") == []
    held["yes"] = False
    w.step(3)
    assert [(e["old_key"], e["new_key"], e["reason"]) for e in w.events("key_change")] == [
        (KB, key_of(C, OT), "nature_change")]


def test_rr_two_hidden_nature_changes_keep_original_server_key():
    from tests.unit.gen3_trade_journal_model import JournalModel

    model = JournalModel()
    w = World("gen3_rr", "radical_red", "companion", journal=model)
    w.set_party(party(A, B))
    w.step_to(60)
    journal = model.journal
    original_hidden = journal.hidden
    held = {"yes": True}
    journal.hidden = lambda self: held["yes"] or original_hidden(self)
    w.regs["R4"] = w.party_base() + 100
    for new_pid in (C, C + 1):
        w.fire("nature_change_begin")
        w.set_party([mon_record(A, OT), mon_record(new_pid, OT, species=5)])
        w.fire("nature_change")
        w.step()
    assert w.events("key_change") == []
    held["yes"] = False
    w.step(3)
    assert [(e["old_key"], e["new_key"], e["reason"]) for e in w.events("key_change")] == [
        (KB, key_of(C + 1, OT), "nature_change")]


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("next_frame", [False, True])
def test_rr_nature_change_tracks_exact_party_slot_across_frame_boundary(slot, next_frame):
    mons = [mon_record(A + i, OT, species=5) for i in range(6)]
    w = World("gen3_rr", "radical_red", "companion")
    w.set_party(mons)
    w.step_to(60)
    w.regs["R4"] = w.party_base() + slot * 100
    w.fire("nature_change_begin")
    if next_frame:
        w.step()
    changed = list(mons)
    changed[slot] = mon_record(C + slot, OT, species=5)
    w.set_party(changed)
    w.fire("nature_change")
    w.step()
    assert [(m["old_key"], m["new_key"], m["reason"]) for m in w.events("key_change")] == [
        (key_of(A + slot, OT), key_of(C + slot, OT), "nature_change")]
    assert all(not w.events(kind) for kind in ("capture", "faint", "party_to_box", "box_to_party"))


@pytest.mark.parametrize("failure", ["no_begin", "pointer", "reset", "unchanged", "bad_flags", "egg"])
def test_rr_nature_change_refuses_unpaired_or_invalid_pid_store(failure):
    from server.adapters import gen3_codec as codec

    w = live("gen3_rr", "radical_red", "companion", pids=(A, B))
    ptr = w.party_base() + codec.PARTY_MON_SIZE
    w.regs["R4"] = ptr
    if failure != "no_begin":
        w.fire("nature_change_begin")
    if failure == "reset":
        w.client.driver.on_reset()
    if failure == "pointer":
        w.regs["R4"] = w.party_base()
    if failure != "unchanged":
        w.set_party([mon_record(A, OT), mon_record(C, OT, species=5, is_egg=1 if failure == "egg" else 0)])
    if failure == "bad_flags":
        w.poke_int(ptr + codec._OFF_FLAGS, 0, 1)
    w.fire("nature_change")
    w.step()
    assert w.events("key_change") == []


@pytest.mark.parametrize("same_frame", [True, False], ids=["captured-emerald-timing", "separate-frame-control"])
@pytest.mark.parametrize("startup", ["immediate", "quiet_party", "trainer_first"])
def test_emerald_npc_trade_reports_key_change_when_both_hooks_fire_in_one_frame(monkeypatch, same_frame, startup):
    from tests.unit import gen3_world as gw
    w = World("gen3_emerald", "emerald", "companion")
    bystander = mon_record(0x4D55444B, 0x20250925, species=283)
    outgoing = mon_record(0x52414C5C, 0x20250925, species=392)
    received = mon_record(0x84, 0x9746, species=298)
    if startup == "trainer_first":
        w.set_party([])
        w.break_checkpoint()
        w.step(3)
    w.set_party([bystander, outgoing])
    if startup != "immediate":
        # Quiet, readable startup precedes the first eligible hello on Emerald.
        # This must seed both known keys and the eventual trade/PC baseline.
        w.break_checkpoint()
        w.step(3)
        assert w.events("hello") == []
        w.overworld_safe()
    w.step_to(60)
    w.regs["R0"], w.regs["R1"] = 1, 0
    w.fire("trade_begin")
    if not same_frame:
        w.step()
    w.set_party([bystander, received])
    w.fire("trade_done")
    w.step(240)
    changes = w.events("key_change")
    assert len(changes) == 1
    assert (changes[0]["old_key"], changes[0]["new_key"], changes[0]["new_species"], changes[0]["reason"]) == (
        "52414C5C:20250925", "00000084:00009746", 298, "npc_trade")
    assert w.events("capture") == [] and w.writes == []


def test_npc_trade_without_a_readable_entry_preimage_does_not_guess_from_cached_party():
    w = live(pids=(A, B))
    w.poke_int(w.ram["PARTY_COUNT_ADDR"], 7, 1)  # invalid at entry, readable again at completion
    w.fire("trade_begin")
    w.set_party([mon_record(A, OT), mon_record(C, 0x9999, species=122)])
    w.fire("trade_done")
    w.step(3)
    assert w.events("key_change") == []
    assert any("NPC trade preimage unavailable" in line for line in w.logs)


def test_save_cleared_reset_discards_queued_npc_trade_preimage():
    w = live(pids=(A, B))
    w.fire("trade_begin")
    w.set_party([])
    w.set_trainer(0)
    w.client.validate(w.client)  # production save-cleared path, before signal drain
    w.set_trainer(OT)
    w.set_party([mon_record(A, OT), mon_record(C, 0x9999, species=122)])
    w.fire("trade_done")
    w.step(3)
    assert w.events("key_change") == []
    w.fire("trade_begin")
    w.set_party([mon_record(A, OT), mon_record(0x44444444, 0x8888, species=25)])
    w.fire("trade_done")
    w.step()
    assert [(m["old_key"], m["new_key"]) for m in w.events("key_change")] == [
        (key_of(C, 0x9999), "44444444:00008888")]


@pytest.mark.parametrize("size,slot", [(size, slot) for size in range(1, 7) for slot in range(size)])
def test_emerald_npc_trade_maps_each_slot_and_party_size(monkeypatch, size, slot):
    from tests.unit import gen3_world as gw
    w = World("gen3_emerald", "emerald", "companion")
    mons = [mon_record(A + i, OT, species=283) for i in range(size)]
    w.set_party(mons)
    w.step_to(60)
    w.regs["R0"], w.regs["R1"] = slot, 0
    w.fire("trade_begin")
    mons[slot] = mon_record(C, 0x9999, species=298)
    w.set_party(mons)
    w.fire("trade_done")
    w.step()
    assert [(m["old_key"], m["new_key"], m["reason"]) for m in w.events("key_change")] == [
        (key_of(A + slot, OT), key_of(C, 0x9999), "npc_trade")]


def test_cancelled_npc_trade_is_replaced_by_the_second_preimage():
    w = live(pids=(A, B))
    w.fire("trade_begin")
    w.step()  # no completion for this observation
    w.set_party([mon_record(A, OT), mon_record(0x44444444, OT)])
    w.fire("trade_begin")
    w.set_party([mon_record(A, OT), mon_record(C, 0x9999, species=122)])
    w.fire("trade_done")
    w.step()
    assert [(m["old_key"], m["new_key"]) for m in w.events("key_change")] == [
        ("44444444:0000ABCD", key_of(C, 0x9999))]


def test_npc_trade_preimage_survives_a_map_signal_between_swap_and_done():
    w = live(pids=(A, B))
    w.fire("trade_begin")
    w.step()
    w.set_party([mon_record(A, OT), mon_record(C, 0x9999, species=122)])
    w.fire("map_load")
    w.step()
    w.fire("trade_done")
    w.step()
    assert [(m["old_key"], m["new_key"]) for m in w.events("key_change")] == [(KB, key_of(C, 0x9999))]


def test_shared_client_forwards_existing_server_phone_content_without_consuming_the_command(monkeypatch):
    from tests.unit.test_state import make_state_with_link

    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "00000007ABCDEF01")
    calls, epochs = [], []

    def binder(lua):
        # MODEL command seam on the admitted companion harness. Native Emerald
        # capability/ABI/record gates are independently exercised in test_gen3_native.
        return lua.table(service=lambda *_: True, idle=lambda *_: True,
                         trade_capable=lambda *_: False,
                         bind_match_call_session=lambda _, epoch: epochs.append(epoch),
                         request_match_call=lambda _, tag, data: calls.append((tag, lua_to_py(data))))

    w = World("gen3_rr", "radical_red", "companion", native=binder)
    w.set_party(party(A, B))
    w.step_to(60)
    state = make_state_with_link("PARTNER:1", KA)
    state.trainer_names = {"a": "BOB", "b": "RED"}
    state.links[0].a.species, state.links[0].a.nickname = 392, "RALTS"
    state.links[0].b.species, state.links[0].b.nickname = 298, "DOTS"
    state.handle_event("a", {"event": "faint", "key": "PARTNER:1"})
    command = next(c for c in state.queued_commands["b"] if c.get("phone") == "fallen")
    w.command(**command)
    w.step(3)
    assert epochs == [7]
    assert calls == [("fallen", {"trainer_name": "BOB",
                                 "caller_mon": {"species_id": 392, "nickname": "RALTS"},
                                 "receiver_mon": {"species_id": 298, "nickname": "DOTS"}})]
    assert w.party_hp(0) == 0  # the original force_faint still runs


def test_clean_client_does_not_forward_phone_tags_to_an_injected_binder():
    calls = []
    w = World(kind="clean", native=lambda lua: lua.table(request_match_call=lambda *args: calls.append(args)))
    w.set_party(party(A, B))
    w.step_to(60)
    w.command(cmd="msgbox", text="same message", phone="first_link")
    w.step()
    assert calls == []


def test_phone_forwarding_error_never_drops_the_original_force_faint(monkeypatch):
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "00000007ABCDEF01")

    def binder(lua):
        def failed(*_):
            raise RuntimeError("synthetic phone path failure")
        return lua.table(service=lambda *_: True, idle=lambda *_: True,
                         trade_capable=lambda *_: False, request_match_call=failed)

    w = World("gen3_rr", "radical_red", "companion", native=binder)
    w.set_party(party(A, B))
    w.step_to(60)
    w.command(cmd="force_faint", key=KA, phone="fallen")
    w.step(3)
    assert w.party_hp(0) == 0
    assert any("match_call request failed" in line for line in w.logs)


# ── in-battle writes (owner ruling 2026-09-23) ────────────────────────────────────────────

def test_a_bench_faint_lands_immediately_and_its_faint_is_not_reported():
    w = live()
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))
    w.command(cmd="force_faint", key=KB)
    w.step()
    assert w.party_hp(1) == 0 and write_reasons(w) == ["battle_faint"]
    assert set(w.battle_checks) == {"battle_faint"}      # pre-check, arm, per-write re-check
    w.step(30)                                            # a tick sees hp 0 before the engine's faint
    w.fire("faint")
    w.step()
    assert w.events("faint") == []                            # commanded: suppressed
    assert any(h[0] == "show" and "KO'd" in h[1] for h in w.hud)


def test_a_refused_battle_faint_check_holds_and_writes_nothing():
    w = live()
    w.enter_battle([FOE], active=(0,))
    w.command(cmd="force_faint", key=KB)
    w.step(3)
    assert w.writes == [] and w.client.battle_pending_count(w.client) == 1
    held = lua_to_py_list(w.client.battle_pending)[0]
    assert "forbidden state" in str(held.why)                # the REAL battle clause set refused
    w.battle_ok = True
    w.step()
    assert w.party_hp(1) == 0 and w.client.battle_pending_count(w.client) == 0


def test_doubles_battler_two_is_active_and_held():
    w = live(pids=(A, B, C))
    w.battle_ok = True
    w.enter_battle([FOE, FOE], doubles=True, active=(0, 2))
    w.command(cmd="force_faint", key=KC)
    w.step(3)
    assert w.party_hp(2) == 20 and w.client.battle_pending_count(w.client) == 1


def test_a_held_battler_at_battle_end_lands_at_the_overworld_checkpoint():
    w = live()
    w.enter_battle([FOE], active=(0,))
    w.command(cmd="force_faint", key=KA)
    w.step(3)
    w.leave_battle()
    w.step()
    assert w.client.battle_pending_count(w.client) == 0
    w.step(2)
    assert w.party_hp(0) == 0 and write_reasons(w) == ["overworld"]


def test_frlg_force_explode_commits_but_keeps_hp_for_the_engine():
    w = live()
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))
    w.command(cmd="force_explode", key=KA)
    w.step(3)
    assert w.writes and w.client.battle_pending_count(w.client) == 1
    assert w.party_hp(0) == 20 and w._read(w.ram["CHOSEN_MOVE_ADDR"], 2) == 153


def _lift_rr_commit_hold(w):
    """Plan-shape scaffolding for the G5 controller-handoff design: the RR pack HOLDS battle_commit
    (REV-C5-RR-BW-FIX 2, the battle_commit_hold clause). safety.lua evaluates every clause and
    names every failure, so a refusal by the hold ALONE means the rest of the real clause set
    admitted: only that refusal is lifted, keeping the commit plan's coverage."""
    policy = w.parts.policy
    held_check, safety = policy.check, w.parts.safety

    def check(this, snap, reason, args=None):
        ok, why = held_check(this, snap, reason, args)
        if not ok and list(safety.last_clauses.values()) == ["battle_commit_hold"]:
            return True, "hold lifted (test scaffolding)"
        return ok, why
    policy.check = check


def _explode_world(lift_hold=True):
    w = live("gen3_rr", "radical_red")
    if lift_hold:
        _lift_rr_commit_hold(w)
    w.battle_ok = True
    w.poke_int(w.ram["BATTLE_STRUCT_PTR_ADDR"], 0x02020000, 4)
    w.enter_battle([FOE], active=(0,))
    base = w.ram["BATTLE_MONS_ADDR"]
    w.poke_int(base + 0x28, 20, 2)                              # battler 0 hp
    return w, base


def _policy_check(w, reason, args=None):
    policy = w.parts.policy
    snap = policy.snapshot(policy)
    ok, why = policy.check(policy, snap, reason, w.lua.table_from(args or {}, recursive=True))
    return bool(ok), str(why)


def test_rr_battle_commit_is_refused_unless_the_plan_hands_the_controller_off():
    """REV-C5-RR-BW-FIX 2: the commit writes comm[b] = 3 while CFRU's parked controller is still
    live, and an L press then runs RemoveBagItem (0x090AA114) -- a ball lost. The RR pack holds
    battle_commit by name (pack data, enforced by safety.lua: client.lua names no title); the
    battle state itself is admissible, so the hold is the ONLY failing clause for a commit that
    does not end in the hand-off (G4-PH / G5-EXPLODE-HANDOFF plans do, and are admitted)."""
    w, _base = _explode_world(lift_hold=False)
    assert _policy_check(w, "battle_faint")[0] is True        # the clause set itself admits
    ok, why = _policy_check(w, "battle_commit", {"battler": 0})
    assert ok is False and "battle_commit" in why and "0x090AA114" in why
    assert list(w.parts.safety.last_clauses.values()) == ["battle_commit_hold"]


def test_frlg_battle_commit_policy_is_unchanged():
    w = live()
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))
    assert _policy_check(w, "battle_commit", {"battler": 0}) [0] is True


def test_rr_force_explode_commits_the_menu_skip_under_battle_commit():
    w, base = _explode_world()
    w.command(cmd="force_explode", key=KA)
    w.step()
    assert set(write_reasons(w)) == {"battle_commit"}
    assert w._read(base + 0x0C, 2) == 153 and w._read(base + 0x24, 1) == 5
    assert w._read(w.ram["CHOSEN_ACTION_ADDR"], 1) == 0
    assert w._read(w.ram["CHOSEN_MOVE_ADDR"], 2) == 153
    assert w._read(w.ram["BATTLE_COMM_ADDR"], 1) == 3
    assert w._read(0x02020000 + w.d["BATTLE_STRUCT_MOVE_TARGET_OFF"], 1) == 1
    held = lua_to_py_list(w.client.battle_pending)[0]
    assert str(held.why) == "explosion committed"


def test_rr_explosion_that_lands_settles_without_a_faint_report():
    w, base = _explode_world()
    w.command(cmd="force_explode", key=KA)
    w.step(30)                                                  # ticks see the battler alive meanwhile
    w.poke_int(base + 0x24, 4, 1)                               # PP dropped: executed
    w.poke_int(base + 0x28, 0, 2)                               # the user fainted
    w.set_party([mon_record(A, OT, hp=0), mon_record(B, OT, species=5)])
    w.fire("faint")
    w.step()
    assert w.client.battle_pending_count(w.client) == 0 and w.events("faint") == []


def _hp_writes(w):
    hp = {w.party_base() + slot * 100 + 0x56 + i for slot in range(6) for i in range(2)}
    hp |= {w.ram["BATTLE_MONS_ADDR"] + b * 0x58 + 0x28 + i for b in range(4) for i in range(2)}
    return [x for x in w.writes if x[0] in hp]


def test_the_full_commit_lands_through_the_real_writes_and_safety_with_the_guard_last():
    """C4-2e item 2: writes.lua re-checks the REAL battle_commit clause set per write call
    (the window is frame-bound); write_plan prevalidates its whole plan once. The final
    gBattleCommunication guard hands control over only after the preceding updates."""
    w, base = _explode_world()
    w.command(cmd="force_explode", key=KA)
    w.step()
    comm = w.ram["BATTLE_COMM_ADDR"]
    commit = [x for x in w.writes if x[1] is not None]
    addrs = [x[0] for x in commit]
    assert addrs[-5] == comm and addrs.count(comm) == 1           # the committing byte, then the hand-off
    assert addrs[-4:] == [P_SLOT + i for i in range(4)]           # G5-EXPLODE-HANDOFF: the tail, last
    assert len(commit) == 4 * 2 + 4 + 1 + 2 + 1 + 1 + 1 + 4       # moves, PP, action, move, pos, target, comm, hand-off
    assert sum(int(r.len) for r in lua_to_py_list(w.parts.writes.log)) == len(commit)


def test_explode_control_a_move_locked_battler_is_held_and_nothing_is_written():
    w, base = _explode_world()
    w.poke_int(w.ram["LOCKED_MOVES_ADDR"], 37, 2)              # Thrash in progress
    w.command(cmd="force_explode", key=KA)
    w.step(5)
    assert w.writes == [] and "move locked" in str(lua_to_py_list(w.client.battle_pending)[0].why)


@pytest.mark.parametrize("model", ["sleep", "flinch"])
def test_explode_control_sleep_or_flinch_rearms_the_commit_and_never_writes_hp(model):
    """The move never executes (PP stays 5) and the turn ends: the commit is re-armed under
    battle_commit, the entry stays held; no HP byte is ever written. Liveness NOT PHYSICAL."""
    w, base = _explode_world()
    w.command(cmd="force_explode", key=KA)
    w.step()
    for _ in range(3):                                         # three turns asleep / flinching
        _next_parked_menu(w)                                   # the next turn's input wait
        w.step()
        assert w._read(w.ram["BATTLE_COMM_ADDR"], 1) == 3
    assert _hp_writes(w) == [] and set(write_reasons(w)) == {"battle_commit"}
    assert w.client.battle_pending_count(w.client) == 1


def test_rr_explosion_that_the_battler_survives_degrades_to_a_held_faint():
    w, base = _explode_world()
    w.command(cmd="force_explode", key=KA)
    w.step()
    w.poke_int(base + 0x24, 4, 1)                               # executed
    w.poke_int(w.ram["BATTLE_COMM_ADDR"], 1, 1)                 # a new turn's input wait, hp > 0
    n = len(w.writes)
    w.step(3)
    held = lua_to_py_list(w.client.battle_pending)[0]
    assert "explosion failed" in str(held.why) and len(w.writes) == n
    assert _hp_writes(w) == []                                  # Damp: never an active-battler HP write


def test_rr_explosion_commit_is_rewritten_when_the_engine_resets_it():
    w, base = _explode_world()
    w.command(cmd="force_explode", key=KA)
    w.step()
    _next_parked_menu(w)                                        # turn start reset, PP untouched
    w.step()
    assert w._read(w.ram["BATTLE_COMM_ADDR"], 1) == 3


# ── mechanism P: the active battler faints in battle (C4-ACTIVE-FAINT-P, owner 2026-09-23) ──
# Scope doc docs/gen3/research/active_faint_in_battle_scope_2026-09-23.md §2d. Addresses are
# re-typed here from pokefirered.sym / pokeleafgreen.sym (equal in both) so the pack is checked,
# not trusted.
P_STATUS3, P_DISABLE, P_PERISH_OFF = 0x02023DFC, 0x02023E0C, 0x0F
P_CHOSEN_ACTION, P_COMM = 0x02023D7C, 0x02023E82
STATUS3_PERISH_SONG, B_ACTION_NOTHING_FAINTED = 0x20, 13
# G4-PH: the hand-off, gBattlerControllerFuncs[0] = PlayerBufferExecCompleted|1 (FR/LG .sym; RR
# LDR@0x090A9EFE), re-typed here so the pack is checked, not trusted.
P_SLOT, P_EXEC_COMPLETED = 0x03004FE0, 0x0802E33D


def _p_world(title="firered", pids=(A, B)):
    w = live(title=title, pids=pids)
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))                            # battler 0 hp 20 (World)
    return w


def _p_plan_bytes(w, status3_before, timer_before=0, handoff=True):
    """The P+H plan as (addr, byte) in write order: gStatuses3[0] (u32 LE), the perish timer
    (high nibble kept), the no-op action, gBattleCommunication[0], then the hand-off LAST."""
    s3 = (status3_before | STATUS3_PERISH_SONG).to_bytes(4, "little")
    tail = P_EXEC_COMPLETED.to_bytes(4, "little")
    return ([(P_STATUS3 + i, s3[i]) for i in range(4)]
            + [(P_DISABLE + P_PERISH_OFF, timer_before & 0xF0), (P_CHOSEN_ACTION, B_ACTION_NOTHING_FAINTED),
               (P_COMM, 3)]
            + ([(P_SLOT + i, tail[i]) for i in range(4)] if handoff else []))


def _next_parked_menu(w):
    """The next action menu: comm[0] = 1, exec bit 0 set, the input controller back in the slot."""
    w.battle_ok = True


def _p_commit(w):
    return [(a, v) for a, v, _f in w.writes]


def _engine_perish_ko(w, slot=0):
    """What BattleScript_PerishSongTakesLife leaves behind: datahpupdate zeroed gBattleMons AND
    the party (pret battle_script_commands.c:1861), then tryfaintmon fired the faint site."""
    w.poke_int(w.ram["BATTLE_MONS_ADDR"] + 0x28, 0, 2)
    w.poke_int(w.party_base() + slot * 100 + 0x56, 0, 2)
    w.fire("faint")


@pytest.mark.parametrize("title", ["firered", "leafgreen"])
def test_p_frlg_active_force_faint_commits_the_perish_plan_in_order_with_the_handoff_last(title):
    """F4 + plan shape (G4-PH): the P writes, comm, then the hand-off LAST, under battle_commit
    only; the hold carries no press hint (owner ruling 16: no A press on any title)."""
    w = _p_world(title)
    w.poke_int(P_STATUS3, 0x100, 4)                              # an unrelated status3 bit survives
    w.command(cmd="force_faint", key=KA)
    w.step()
    assert _p_commit(w) == _p_plan_bytes(w, 0x100)
    tail = [(P_SLOT + i, b) for i, b in enumerate(P_EXEC_COMPLETED.to_bytes(4, "little"))]
    assert _p_commit(w)[-5:] == [(P_COMM, 3)] + tail
    assert write_reasons(w) == ["battle_commit"] * 5
    (held,) = lua_to_py_list(w.client.battle_pending)
    assert str(held.why) == "active faint committed" and held.perish is True and held.handoff is True
    assert any("force_faint: Perish commit battler=0 handoff=1 " in line for line in w.logs)
    w.step(3)                                                    # held, no press hint, nothing more written
    assert str(held.why) == "active faint committed" and len(w.writes) == 11


@pytest.mark.parametrize("title", ["firered", "leafgreen"])
def test_frlg_explosion_is_separate_from_perish_and_retains_the_bench_rule(title):
    """Explosion coerces move 153, not Perish; a switch-out still lands the linked bench faint."""
    w = _p_world(title)
    w.command(cmd="force_explode", key=KA)
    w.step(30)
    assert set(write_reasons(w)) == {"battle_commit"}
    (held,) = lua_to_py_list(w.client.battle_pending)
    assert str(held.cmd) == "force_explode" and str(held.why) == "explosion committed"
    assert w._read(P_STATUS3, 4) & STATUS3_PERISH_SONG == 0 and w._read(P_COMM, 1) == 3
    assert w._read(w.ram["CHOSEN_ACTION_ADDR"], 1) == 0
    assert w._read(w.ram["CHOSEN_MOVE_ADDR"], 2) == 153
    w.set_active([1])                                            # the parent's landing: switch-out
    _next_parked_menu(w)                                         # engine has completed the prior hand-off
    w.step()
    assert w.party_hp(0) == 0 and write_reasons(w)[-1] == "battle_faint"
    assert w.client.battle_pending_count(w.client) == 0
    # control: the linked-faint path on the same title still uses P
    w = _p_world(title)
    w.command(cmd="force_faint", key=KA)
    w.step()
    assert _p_commit(w) == _p_plan_bytes(w, 0)


@pytest.mark.parametrize("title", ["firered", "leafgreen"])
def test_ph_frlg_a_pack_without_handoff_keeps_the_press_a_path(title):
    """G4-PH: the hand-off is data. With no battle.handoff (the policy proves no entry) the plan is
    the pre-G4-PH P plan, comm LAST, written entry by entry, and the hold keeps its press hint."""
    w = _p_world(title)
    w.parts.policy.handoff_entry = lambda *_a: None
    w.command(cmd="force_faint", key=KA)
    w.step()
    assert _p_commit(w) == _p_plan_bytes(w, 0, handoff=False)
    (held,) = lua_to_py_list(w.client.battle_pending)
    assert str(held.why) == "active faint committed (press A)" and not held.handoff
    assert any("Perish commit battler=0 handoff=0 " in line for line in w.logs)
    assert w._read(P_SLOT, 4) == 0x0802E439                      # the parked controller, untouched


@pytest.mark.parametrize("title", ["firered", "leafgreen", "radical_red"])
def test_ph_row_2_keeps_the_timer_high_nibble(title):
    w = _p_world(title) if title != "radical_red" else _rr_p_world()
    w.poke_int(P_DISABLE + P_PERISH_OFF, 0x35, 1)                # StartValue 3, timer 5
    w.command(cmd="force_faint", key=KA)
    w.step()
    assert _p_commit(w) == _p_plan_bytes(w, 0, timer_before=0x35)


def test_p_f4_control_comm_first_is_refused_mid_plan_by_the_real_sink():
    """F4 control: why comm must be last. gBattleCommunication[0] is both the battle_comm_0
    clause and the commit guard, and writes.lua re-checks per write call (the window is frame-bound), so the next
    write call is refused. (A property of writes.lua/safety.lua: this control is green on HEAD too.)"""
    w = _p_world()
    writes = w.parts.writes
    allow = w.lua.eval("function() return true end")
    writes.arm(writes, "battle_commit", allow, w.lua.table(battler=0))
    writes.write_bytes(writes, P_COMM, w.lua.table(3))
    with pytest.raises(Exception, match="forbidden|battle"):
        writes.write_bytes(writes, P_CHOSEN_ACTION, w.lua.table(B_ACTION_NOTHING_FAINTED))
    writes.disarm(writes)
    assert _p_commit(w) == [(P_COMM, 3)]


def test_p_f1_the_commit_forces_the_no_op_action_so_the_mon_cannot_act():
    """F1: without writes 3/4 the FIGHT the player picks runs (O3 fails) and Baton Pass would
    carry Perish to the replacement."""
    w = _p_world()
    w.command(cmd="force_faint", key=KA)
    w.step()
    assert w._read(P_CHOSEN_ACTION, 1) == B_ACTION_NOTHING_FAINTED and w._read(P_COMM, 1) == 3


def test_p_f2_the_faint_is_the_engines_perish_ko_never_an_hp_write():
    """F2: P writes the Perish flag + a zero timer and no HP word, party or gBattleMons."""
    w = _p_world()
    w.command(cmd="force_faint", key=KA)
    w.step(5)
    assert w._read(P_STATUS3, 4) & STATUS3_PERISH_SONG
    assert (P_DISABLE + P_PERISH_OFF, 0) in _p_commit(w)
    assert _hp_writes(w) == [] and w.party_hp(0) == 20


def test_p_f3_the_engines_faint_is_not_echoed_and_settles_the_entry():
    """F3: mark_commanded at commit time, so the engine's own faint reports nothing."""
    w = _p_world()
    w.command(cmd="force_faint", key=KA)
    w.step(30)                                                   # ticks observe the mon alive meanwhile
    _engine_perish_ko(w)
    w.step()
    assert w.events("faint") == []
    assert w.client.battle_pending_count(w.client) == 0
    assert any(h[0] == "show" and "fainted" in h[1] for h in w.hud)


def test_p_f5_a_locked_turn_writes_nothing_and_the_commit_lands_at_the_next_parked_menu():
    """F5: Fly/Dig/Outrage never park on HandleInputChooseAction, so the permit refuses."""
    w = _p_world()
    w.battle_ok = False                                          # locked: no action menu
    w.command(cmd="force_faint", key=KA)
    w.step(10)
    assert w.writes == [] and w.client.battle_pending_count(w.client) == 1
    w.battle_ok = True                                           # the lock ended: menu parked
    w.step()
    assert _p_commit(w) == _p_plan_bytes(w, 0)


def test_p_an_already_committed_battler_is_not_overwritten():
    w = _p_world()
    w.poke_int(P_COMM, 3, 1)                                     # the player already chose
    w.command(cmd="force_faint", key=KA)
    w.step(3)
    assert w.writes == [] and w.client.battle_pending_count(w.client) == 1


def test_p_the_commit_is_rewritten_when_the_engine_resets_it_with_the_mon_alive():
    w = _p_world()
    w.command(cmd="force_faint", key=KA)
    w.step()
    n = len(w.writes)
    w.step(3)
    assert len(w.writes) == n                                    # committed: nothing more
    _next_parked_menu(w)                                         # a new parked menu, hp > 0
    w.poke_int(P_STATUS3, 0, 4)
    w.step()
    assert _p_commit(w)[n:] == _p_plan_bytes(w, 0) and _hp_writes(w) == []


@pytest.mark.parametrize("slot", [0, 2])
def test_p_doubles_battlers_zero_and_two_stay_held(slot):
    w = live(pids=(A, B, C))
    w.battle_ok = True
    w.enter_battle([FOE, FOE], doubles=True, active=(0, 2))
    w.command(cmd="force_faint", key=(KA, KB, KC)[slot])
    w.step(3)
    assert w.writes == [] and w.client.battle_pending_count(w.client) == 1
    assert str(lua_to_py_list(w.client.battle_pending)[0].why) == "active battler"


# ── G4-PH: mechanism P+H on RR (rr_active_faint_parity_scope_2026-09-23.md §5.4 items 4, 5) ──
# The RR profile now proves the six P fields out of CFRU's Perish case and the pack proves the
# hand-off, so RR runs the same data-gated path as FR/LG: no title name in client.lua.

def _rr_p_world(pids=(A, B)):
    w = live("gen3_rr", "radical_red", pids=pids)                # the REAL pack: commit_hold present
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))
    return w


def test_ph_falsifier_rr_active_force_faint_commits_p_with_the_handoff_through_the_hold():
    """§5.4 item 5 (red on f6d503f4, where RR held "active battler"): the five-entry plan lands
    whole under the pack's commit_hold, because its hand-off tail replaces the hold clause."""
    w = _rr_p_world()
    assert "commit_hold" in w.wc["battle"] and w.wc["battle"]["handoff"]["value"] == P_EXEC_COMPLETED
    w.poke_int(P_STATUS3, 0x100, 4)
    w.command(cmd="force_faint", key=KA)
    w.step()
    assert _p_commit(w) == _p_plan_bytes(w, 0x100) and _hp_writes(w) == []
    assert write_reasons(w) == ["battle_commit"] * 5
    (held,) = lua_to_py_list(w.client.battle_pending)
    assert str(held.why) == "active faint committed" and held.handoff is True
    assert w._read(P_SLOT, 4) == P_EXEC_COMPLETED and w._read(P_COMM, 1) == 3


def test_ph_rr_the_engines_perish_ko_settles_the_entry_without_an_echo():
    w = _rr_p_world()
    w.command(cmd="force_faint", key=KA)
    w.step(30)
    _engine_perish_ko(w)
    w.step()
    assert w.events("faint") == [] and w.client.battle_pending_count(w.client) == 0


@pytest.mark.parametrize("slot", [0, 2])
def test_ph_rr_doubles_battlers_zero_and_two_stay_held(slot):
    w = live("gen3_rr", "radical_red", pids=(A, B, C))
    w.battle_ok = True
    w.enter_battle([FOE, FOE], doubles=True, active=(0, 2))
    w.command(cmd="force_faint", key=(KA, KB, KC)[slot])
    w.step(3)
    assert w.writes == [] and str(lua_to_py_list(w.client.battle_pending)[0].why) == "active battler"


def test_ph_rr_a_locked_turn_writes_nothing_and_commits_at_the_next_parked_menu():
    """MULTIPLETURNS / recharge: no parked menu, so the permit refuses and nothing is written."""
    w = _rr_p_world()
    w.battle_ok = False
    w.command(cmd="force_faint", key=KA)
    w.step(10)
    assert w.writes == [] and w.client.battle_pending_count(w.client) == 1
    w.battle_ok = True
    w.step()
    assert _p_commit(w) == _p_plan_bytes(w, 0)


def _explode_h_bytes(base, bs=0x02020000, with_moves=True):
    """RR's Explode+H plan as (addr, byte) in write order: commit_plan's rows exactly (Explosion x4
    + PP 5 x4 on the first commit, action 0, chosen move 153, the battle-struct slot/target when
    the pointer is set), comm = 3, then the hand-off LAST (owner ruling 19, 2026-09-24)."""
    out = []
    if with_moves:
        for i in range(4):
            out += [(base + 0x0C + 2 * i, 153), (base + 0x0C + 2 * i + 1, 0), (base + 0x24 + i, 5)]
    out += [(0x02023D7C, 0), (0x02023DC4, 153), (0x02023DC5, 0)]
    if bs:
        out += [(bs + 128, 0), (bs + 12, 1)]
    return out + [(P_COMM, 3)] + [(P_SLOT + i, b) for i, b in enumerate(P_EXEC_COMPLETED.to_bytes(4, "little"))]


def test_g5_rr_force_explode_commits_immediately_with_the_handoff_tail():
    """G5-EXPLODE-HANDOFF (owner ruling 19; red at 9e227101, where the hold refused it): on RR the
    menu skip ends in the same hand-off as P, so Explosion fires with no press and no L-throw."""
    w, base = _explode_world(lift_hold=False)                       # the REAL pack: commit_hold present
    w.command(cmd="force_explode", key=KA)
    w.step()
    assert [(a, v) for a, v, _f in w.writes] == _explode_h_bytes(base)
    assert set(write_reasons(w)) == {"battle_commit"} and len(write_reasons(w)) == 8 + 4 + 1 + 1
    assert w._read(P_SLOT, 4) == P_EXEC_COMPLETED
    (held,) = lua_to_py_list(w.client.battle_pending)
    assert str(held.why) == "explosion committed"
    assert any("force_explode: menu skip committed slot=0 battler=0 handoff=1" in line for line in w.logs)


def test_g5_rr_an_explode_recommit_after_an_engine_reset_also_hands_off():
    """Sleep/flinch: the move never ran, the next turn parks the menu again; the re-commit has no
    move/PP rows (commit_plan's rule) and still ends in the hand-off."""
    w, base = _explode_world(lift_hold=False)
    w.command(cmd="force_explode", key=KA)
    w.step()
    n = len(w.writes)
    _next_parked_menu(w)
    w.step()
    assert [(a, v) for a, v, _f in w.writes[n:]] == _explode_h_bytes(base, with_moves=False)


def test_g5_rr_explode_without_the_battle_struct_pointer_still_hands_off():
    w, base = _explode_world(lift_hold=False)
    w.poke_int(w.ram["BATTLE_STRUCT_PTR_ADDR"], 0, 4)
    w.command(cmd="force_explode", key=KA)
    w.step()
    assert [(a, v) for a, v, _f in w.writes] == _explode_h_bytes(base, bs=0)


@pytest.mark.parametrize("outcome", [1, 4, 7])
def test_p_the_battle_ending_before_the_turn_falls_back_to_the_overworld_write(outcome):
    """The foe fled (RAN), was caught, or KO'd its own last mon DURING an action (recoil,
    Self-Destruct): HandleAction_TryFinish -> checkteamslost sets WON, RunTurnActionsFunctions
    jumps to HandleEndTurn_BattleWon and BattleTurnPassed never runs, so Perish never fires
    (pret battle_main.c:4433-4440, 3706-3714). The entry takes the existing path: one last
    battle_write(ending) -> the overworld checkpoint."""
    w = _p_world()
    w.command(cmd="force_faint", key=KA)
    w.step(30)
    w.leave_battle(outcome=outcome)
    w.step(3)
    assert w.client.battle_pending_count(w.client) == 0
    assert w.party_hp(0) == 0 and write_reasons(w) == ["battle_commit"] * 5 + ["overworld"]
    assert w.events("faint") == []


def _bench_hp_addr(w, slot=1):
    return w.party_base() + slot * 100 + 0x56


@pytest.mark.parametrize("arrival", ["same_reply_frame", "held_then_permit_opens"])
def test_p_a_pending_bench_write_lands_before_the_perish_commit(arrival):
    """Review follow-up 2 item 1: the P commit sets gBattleCommunication[0] = 3, which fails every
    later battle_faint (battle_comm_0) until the next parked menu -- AFTER the Perish KO's party
    screen, where the still-alive bench mon could be sent in. An active entry flushed ahead of a
    bench entry must therefore wait for it: the bench HP write lands first, the commit next frame."""
    w = _p_world(pids=(A, B))
    if arrival == "held_then_permit_opens":
        w.battle_ok = False                                      # both arrive while refused
        w.command(cmd="force_faint", key=KA)
        w.step(2)
        w.command(cmd="force_faint", key=KB)
        w.step(2)
        assert w.writes == [] and w.client.battle_pending_count(w.client) == 2
        w.battle_ok = True
    else:
        w.command(cmd="force_faint", key=KA)                     # active first, bench second
        w.command(cmd="force_faint", key=KB)
    w.step(3)
    bench = _bench_hp_addr(w)
    assert w.party_hp(1) == 0
    addrs = [a for a, _v, _f in w.writes]
    assert addrs[:2] == [bench, bench + 1]                       # the bench write came first
    assert _p_commit(w)[2:] == _p_plan_bytes(w, 0)               # then exactly one P commit
    assert write_reasons(w) == ["battle_faint"] + ["battle_commit"] * 5
    assert w.client.battle_pending_count(w.client) == 1          # the active entry, committed
    _engine_perish_ko(w)
    w.step()
    assert w.events("faint") == [] and w.client.battle_pending_count(w.client) == 0


def test_p_a_bench_entry_arriving_after_the_commit_waits_for_the_next_parked_menu():
    """Documents the residual order: once comm[0] = 3 is written, a LATER bench arrival is refused
    (battle_comm_0) until the next parked menu; it is held, never deferred, and lands there."""
    w = _p_world(pids=(A, B))
    w.command(cmd="force_faint", key=KA)
    w.step()
    w.command(cmd="force_faint", key=KB)
    w.step(3)
    assert w.party_hp(1) == 20 and w.client.battle_pending_count(w.client) == 2
    _next_parked_menu(w)                                         # the next parked menu
    w.step()
    assert w.party_hp(1) == 0


def test_p_draw_edge_today_an_end_of_turn_foe_ko_plus_our_last_mons_perish_ko_is_a_whiteout():
    """Review follow-up 2 item 2, DOCUMENTED, NOT FIXED (owner call pending). If the foe's LAST mon
    faints to an END-OF-TURN effect (poison, burn, Leech Seed, weather, Curse, trap damage) on the
    turn our LAST usable mon is P-committed: BattleTurnPassed's HandleFaintedMonActions ->
    checkteamslost sets WON, HandleWishPerishSongOnTurnEnd is NOT gated on the outcome
    (pret battle_main.c:2958-2968) and fires, our KO's checkteamslost ORs LOST -> DREW (3), and
    sEndTurnFuncsTable[DREW] = HandleEndTurn_BattleLost: a whiteout in a battle the player won.
    Today the client does not intervene: the entry settles on the engine's KO, no faint echo,
    the engine's whiteout is reported, no further byte is written."""
    w = _p_world(pids=(A,))                                      # one usable mon
    w.command(cmd="force_faint", key=KA)
    w.step(30)
    n = len(w.writes)
    w.poke_int(w.ram["ENEMY_BASE"] + 0x56, 0, 2)                  # the foe's last mon: poison KO
    _engine_perish_ko(w)                                          # then our Perish KO, same turn end
    w.step()
    assert w.client.battle_pending_count(w.client) == 0
    w.poke_int(w.ram["BATTLE_OUTCOME_ADDR"], 3, 1)                # WON | LOST = DREW
    w.fire("whiteout")
    w.leave_battle(outcome=3)
    w.step(3)
    assert len(w.events("whiteout")) == 1 and w.events("faint") == []
    assert len(w.writes) == n and write_reasons(w) == ["battle_commit"] * 5


# ── command seams ───────────────────────────────────────────────────────────────────────

def test_apply_trade_on_frlg_writes_nothing_and_reports_unchanged():
    w = live()
    n = len(w.sent)
    w.command(cmd="apply_trade", slot=0, blob_hex="00" * 100, old_key=KA, token="t")
    w.step(3)
    assert w.writes == []
    assert [m["event"] for m in w.sent[n:] if m["event"] != "tick"] == ["trade_done", "menu_result"]


def test_major4_a_refused_apply_trade_under_a_hidden_hello_still_reaches_the_wire():
    """A refused apply_trade owes a NON-uncertain trade_done and, behind it, the
    menu_result cancel. The owed gate (client.lua drv.after_receive) refuses a
    non-uncertain trade_done while session.hello_visible is false, and hello_visible
    only refreshes when a NEW hello is BUILT -- core/session.lua builds one only while
    hello_sent is false, and the trade recovery block is the only thing that clears it.
    Unarmed, both reports sit in owed.list until a reconnect; the refusal branch must
    arm that recovery exactly as trade.lua's completed() does."""
    w = live()
    w.enter_battle([FOE], active=(0,))
    w.step()
    w.set_party([mon_record(0x99999999, 0x5555, species=7)])       # the partner's party in RAM
    w.fire("map_load")
    w.step()
    w.connected = False
    w.step()
    w.connected = True
    w.step()
    assert w.events("hello")[-1]["party_hidden"] is True             # hello_visible latched false
    w.command(cmd="apply_trade", slot=0, blob_hex="00" * 100, old_key=KA, token="t")
    w.step(3)
    assert w.events("menu_result") == [] and w.events("trade_done") == []
    w.set_party(party(A, B))                                        # our own party is back
    w.leave_battle()
    w.overworld_safe()
    w.step(60)
    assert [m["token"] for m in w.events("trade_done")] == ["t"]
    assert [m["token"] for m in w.events("menu_result")] == ["t"]
    assert w.events("trade_done")[0].get("uncertain") is not True    # server semantics unchanged


def test_a_held_owed_report_names_its_reason_once_and_not_once_per_frame():
    """owed:step runs on every after_receive and a held head blocks every later report
    (lua/owed_reports.lua:62). Without a line, a stalled trade_done is indistinguishable
    from a running emulator -- the reason must be named, once per hold."""
    from tests.unit.gen3_trade_journal_model import JournalModel

    store = JournalModel(run=None)                    # never bound: ready() is false for good
    w = World("gen3_frlg", "firered", "clean", journal=store, model_companion=True)
    w.set_party(party(A))
    w.step_to(60)
    w.command(cmd="apply_trade", slot=0, blob_hex="00" * 100, old_key=KA, token="t")
    w.step(120)
    assert w.events("trade_done") == [] and w.events("menu_result") == []
    assert [l for l in w.logs if "owed trade_done held" in l] == [
        "[SLink-gen3] owed trade_done held: trade run not bound"
    ]


@pytest.mark.parametrize("name,extra,event,field,value", [
    ("show_choices", {"options": ["a"], "text": "?"}, "menu_result", "choice", 127),
    ("show_menu", {"text": "?"}, "menu_result", "choice", 0),
    ("choose_mon", {}, "mon_chosen", "slot", 7),
])
def test_prompts_without_a_native_part_get_the_cancel_sentinels(name, extra, event, field, value):
    w = live("gen3_rr", "radical_red")
    w.command(cmd=name, token="tk", **extra)
    w.step()
    (reply,) = w.events(event)
    assert reply["token"] == "tk" and reply[field] == value


@pytest.mark.parametrize("kind,error", [("clean", "patch_required"), ("companion", "native absent")])
def test_rival_swap_without_the_companion_refuses_like_the_old_client(kind, error):
    # clean: no native part at all (the old client); companion: the native part is armed but the
    # fake ROM answers no mailbox, so the final refusal is native.lua's "native absent".
    w = live("gen3_rr", "radical_red", kind)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w))
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "not_in_battle"
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == error
    assert w.writes == []


def test_a_native_part_takes_the_menu_seam():
    seen = []
    w = World("gen3_rr", "radical_red", "companion",
              native=lambda L: L.table(show_menu=lambda _self, cmd: seen.append(str(cmd.token)) or True))
    w.set_party(party(A))
    w.step_to(60)
    w.command(cmd="show_menu", token="tk", text="?")
    w.step()
    assert seen == ["tk"] and w.events("menu_result") == []


# ── deferred checkpoint executors ─────────────────────────────────────────────────────────

class FakeBoxes:
    """A box mover with boxes.lua's contract (true | nil, reason). boxes.lua itself is the C4-3
    card's (tests/unit/test_gen3_boxes.py) and today needs rom.PP_UP_GET_MASK_ADDR, which no
    pack carries yet, so the executor wiring is proven against this double."""

    def __init__(self, results=None, effect=None):
        self.calls, self.results, self.effect = [], list(results or []), effect

    def table(self, L):
        def call(name):
            def fn(_self, key, *_rest):
                self.calls.append((name, str(key)))
                if self.effect:
                    self.effect(name, str(key))
                return self.results.pop(0) if self.results else (True, None)
            return fn
        return L.table(deposit=call("deposit"), withdraw=call("withdraw"),
                       memorialize=call("memorialize"), memorial_box=13)


def boxed_live(results=None, effect=None):
    fake = FakeBoxes(results, effect)
    w = World("gen3_frlg", "firered", boxes=fake.table)
    w.set_party(party(A, B))
    w.step_to(60)
    return w, fake


def test_a_deferred_box_mon_deposits_at_the_checkpoint_and_acks_stats_after():
    w, fake = boxed_live()
    w.command(cmd="box_mon", key=KB)
    w.step()
    assert fake.calls == [("deposit", KB)]
    (cache,) = w.events("stats_cache")
    assert cache["key"] == KB and cache["stats"]["level"] == 5 and cache["stats"]["spAtk"] == 14
    assert w.events("box_mon_failed") == []


def test_a_refused_deferred_deposit_is_box_mon_failed_without_stats_cache():
    w, _ = boxed_live([(None, "last party mon")])
    w.command(cmd="box_mon", key=KB)
    w.step()
    assert w.events("stats_cache") == []
    assert w.events("box_mon_failed")[0]["reason"] == "last party mon"


def test_memorialize_done_names_the_memorial_box():
    w, fake = boxed_live()
    w.command(cmd="memorialize", key=KB)
    w.step()
    (done,) = w.events("memorialize_done")
    assert done["key"] == KB and done["box"] == 13 and fake.calls == [("memorialize", KB)]


def test_our_own_deposit_is_not_reported_as_the_players():
    world = {}

    def move(name, key):                                       # the mover's effect on the save
        if name == "deposit":
            world["w"].set_party(party(A))
            world["w"].set_box(0, 0, mon_record(B, OT, species=5))
    w, _ = boxed_live(effect=move)
    world["w"] = w
    w.command(cmd="box_mon", key=KB)
    w.step()
    w.fire("pc_deposit")                                       # any later PC signal
    w.step()
    assert w.events("party_to_box") == []                      # rescan re-baselined after the move


def test_every_byte_written_went_through_the_armed_sink():
    w = live()
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))
    w.command(cmd="force_faint", key=KB)
    w.step()
    logged = sum(int(r.len) for r in lua_to_py_list(w.parts.writes.log))
    assert logged == len(w.writes) > 0


# ── C4-2d review findings ─────────────────────────────────────────────────────────────────

RR_PACK = json.loads((REPO / "data" / "games" / "gen3_rr" / "profile.json").read_text(encoding="utf-8"))
NATIVE = RR_PACK["native"]




def attach_real_native(w, present=True):
    """The REAL lua/gen3/native.lua instance Entry built for this RR companion World (the one
    the client, Safety's native_idle and the write policy share); present = its beacon is up."""
    assert w.parts.native is not None, "Entry builds native for the RR companion artifact"
    if present:
        w.poke_int(NATIVE["BASE"], NATIVE["SIG"], 4)
        w.poke_int(NATIVE["BASE"] + 4, NATIVE["ABI"], 2)
    return w.parts.native


def native_writes(w):
    lo, hi = NATIVE["BASE"], NATIVE["BASE"] + 0x200
    return [x for x in w.writes if lo <= x[0] < hi]


def rr_live():
    w = live("gen3_rr", "radical_red", "companion")
    w.battle_ok = True                                         # the fake predicate admits "native"
    return w


def test_blocker_control_an_eligible_session_posts_the_native_sound():
    w = rr_live()
    attach_real_native(w)
    w.command(cmd="play_sound", sound=25)
    w.step(3)
    assert native_writes(w), "positive control: the real native.lua must post while eligible"
    # published, not merely begun: the opcode is the post's last byte
    assert w._read(NATIVE["BASE"] + 6, 2) == NATIVE["OP_PLAY_SE"]


def test_blocker_a_paused_session_posts_nothing_through_the_real_native_part():
    w = rr_live()
    attach_real_native(w)
    w.command(cmd="play_sound", sound=25)
    w.step()                                                  # received: queued in native.lua
    w.client.writes_enabled, w.client.gate_revoked = False, True
    w.step(20)
    assert native_writes(w) == [] and w.writes == []
    w.client.writes_enabled, w.client.gate_revoked = True, False
    w.step(2)
    assert native_writes(w), "the queued job survives the pause and posts once eligible"


def test_blocker_a_paused_session_stages_no_rival_swap():
    w = rr_live()
    attach_real_native(w)
    ready_battle(w, 5)
    w.client.writes_enabled, w.client.gate_revoked = False, True
    session, bid = battle_identity(w)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
              session=session, battle_id=bid)
    w.step(5)
    assert w.writes == []
    (reply,) = w.events("rival_team_replaced")
    assert (reply["trainer_id"], reply["species_ids"], reply["error"]) == (
        5, [], "writes_paused"
    )
    w.client.writes_enabled, w.client.gate_revoked = True, False
    w.step(3)
    assert native_writes(w) == [], "nothing was queued while paused, so nothing stale posts later"
    assert len(w.events("rival_team_replaced")) == 1


def test_minor1_a_native_owned_prompt_is_answered_exactly_once():
    w = rr_live()
    attach_real_native(w, present=False)                       # native refuses: its callback answers
    w.command(cmd="show_menu", token="tk", text="?")
    w.step()
    assert [r["token"] for r in w.events("menu_result")] == ["tk"]


def test_major1_a_capture_before_hello_is_sent_after_it():
    w = World()
    w.set_party(party(A))
    w.break_checkpoint()                                       # no hello yet (out of battle)
    w.step()
    w.set_party(party(A, B))
    w.fire("mon_given")
    w.step(3)
    assert w.sent == []
    w.overworld_safe()
    w.step()
    assert w.names()[:2] == ["hello", "capture"]
    # R4 (C4-2e): exactly the acquired mon -- A was there before the first frame
    assert [c["key"] for c in w.events("capture")] == [KB]


def test_r4_a_party_loaded_without_a_signal_is_never_an_acquisition():
    """The save is live at the main menu with an empty party; CONTINUE brings A and B in with
    no engine acquisition signal; a later gift reports only the gift."""
    w = World()
    w.step(2)                                                  # live, empty party: the baseline
    w.set_party(party(A, B))                                   # CONTINUE, no signal
    w.step(3)
    w.set_party(party(A, B, C))
    w.fire("mon_given")
    w.step()
    assert [c["key"] for c in w.events("capture")] == [KC]


def test_r4_a_gift_straddling_a_frame_end_is_still_an_acquisition():
    """The party grows at a frame end whose return hook fires on the NEXT frame: the quiet
    frame must not absorb the new key before the signal settles it."""
    w = live(pids=(A,))
    w.set_party(party(A, B))
    w.step()                                                   # party written, hook not yet fired
    w.fire("mon_given")
    w.step()
    assert [c["key"] for c in w.events("capture")] == [KB]


def test_r1_a_mover_that_throws_on_byte_two_is_uncertain_with_the_production_counter():
    """The client's own write_count over the real writes.lua: byte 1 lands, byte 2 throws."""
    w, _ = boxed_live()
    L, landed = w.lua, []
    real = w.io.write_u8

    def sink(addr, value, *rest):
        landed.append(int(addr))
        if len(landed) == 2:
            raise RuntimeError("sink died on byte 2")
        return real(addr, value, *rest)
    w.io.write_u8 = sink
    w.client.deferred.exec.deposit = L.eval(
        "function(w) return function(key, hint) w:arm('overworld', function() return true end); "
        "w:write_bytes(0x02030000, {1, 2}); return true end end")(w.parts.writes)
    w.command(cmd="box_mon", key=KB)
    w.step(5)
    assert w._read(0x02030000, 1) == 1 and len(landed) == 2      # RAM changed; never retried
    (fail,) = w.events("box_mon_failed")
    assert fail["reason"].startswith("uncertain: partial write")


def _raise_alias(w, old_key, new_key):
    """What the reducer does when it sends key_change old->new: the alias over the record."""
    party = w.client.driver.read_party()
    w.client.identity.begin_alias(w.client.identity, old_key, new_key, party[1], party)


def test_r3_a_replayed_ack_lands_the_queued_faint_on_the_renamed_mon():
    """faint(A) queued, the cartridge's A became B and THIS client raised the A->B alias; the
    server's ACK is a replay (migrated:false). The faint lands on B through the retained alias."""
    w = live(pids=(A, C))
    w.break_checkpoint()
    w.command(cmd="force_faint", key=KA)
    w.step()
    w.set_party([mon_record(B, OT, species=4), mon_record(C, OT, species=5)])
    _raise_alias(w, KA, KB)
    w.command(cmd="key_change_ack", old_key=KA, new_key=KB, migrated=False)
    w.step()
    w.overworld_safe()
    w.step(2)
    assert w.party_hp(0) == 0


def test_r3_without_our_alias_a_replayed_ack_never_retargets_the_faint():
    """C4-2g: the same frames with NO alias (the B in slot 0 is unrelated to A): the faint stays
    on A and is dropped by name at the checkpoint; B keeps its HP."""
    w = live(pids=(A, C))
    w.break_checkpoint()
    w.command(cmd="force_faint", key=KA)
    w.step()
    w.set_party([mon_record(B, OT, species=4), mon_record(C, OT, species=5)])
    w.command(cmd="key_change_ack", old_key=KA, new_key=KB, migrated=False)
    w.step()
    w.overworld_safe()
    w.step(2)
    assert w.party_hp(0) == 20
    assert any("force_faint dropped at the checkpoint" in line and KA in line for line in w.logs)


def test_r4_codex1_a_capture_in_the_first_hello_frame_is_reported_after_the_hello():
    """C4-2g (Codex REV3): A present, checkpoint unsafe, a quiet frame baselines A; then B is
    added, mon_given fires and the checkpoint turns safe before the next step: that one frame
    sends the hello AND settles B. Exactly [hello, capture(B)]."""
    w = World()
    w.set_party(party(A))
    w.break_checkpoint()
    w.step()                                                   # the quiet baseline of A
    w.set_party(party(A, B))
    w.fire("mon_given")
    w.overworld_safe()
    w.step()
    assert w.names()[:2] == ["hello", "capture"]
    assert [c["key"] for c in w.events("capture")] == [KB]


def test_r4_codex2_a_reconnect_between_the_party_write_and_its_hook_still_captures():
    """C4-2g (Codex REV3): B's party write lands while disconnected; the connection returns
    and the hello is built on the frame whose mon_given hook fires. Exactly one capture(B)."""
    w = live(pids=(A,))
    w.connected = False
    w.step()
    w.set_party(party(A, B))                                    # written, hook not yet fired
    w.step()
    w.connected = True
    w.fire("mon_given")
    n = len(w.sent)
    w.step()
    assert [m["event"] for m in w.sent[n:]][:2] == ["hello", "capture"]
    assert [c["key"] for c in w.events("capture")] == [KB]


def test_rev5_r4_a_mon_added_one_frame_after_the_first_hello_is_still_captured():
    """Codex REV5: A -> the first hello step (its baseline) -> add B -> one quiet step ->
    mon_given -> step. The quiet interval started at hello's count, so B is not absorbed."""
    w = World()
    w.set_party(party(A))
    w.step()                                                   # the first hello baselines A
    assert w.names() == ["hello"]
    w.set_party(party(A, B))
    w.step()                                                   # quiet: B's hook is not in yet
    w.fire("mon_given")
    w.step()
    assert [c["key"] for c in w.events("capture")] == [KB]


def test_rev5_r4_a_count_change_inside_the_quiet_interval_restarts_it():
    w = live(pids=(A,))
    w.set_party(party(A, B))
    w.step()                                                   # a count change: interval starts
    w.set_party(party(A, B, C))
    w.step()                                                   # changed again before it was due
    w.fire("mon_given")
    w.step()
    assert [c["key"] for c in w.events("capture")] == [KB, KC]


def test_r4_control_the_first_hello_still_baselines_a_party_it_has_never_seen():
    """The first-ever baseline is still taken by hello when no quiet frame preceded it: a
    later acquisition reports only the new mon."""
    w = World()
    w.set_party(party(A, B))
    w.step()                                                   # hello on the very first frame
    w.set_party(party(A, B, C))
    w.fire("mon_given")
    w.step()
    assert [c["key"] for c in w.events("capture")] == [KC]


def test_major1_a_reconnect_hello_has_no_area_enter_before_it():
    w = live()
    w.connected = False
    w.step()
    w.set_location(1, 0)                                       # moved while offline
    w.connected = True
    n = len(w.sent)
    w.step()
    assert [m["event"] for m in w.sent[n:]][:1] == ["hello"]
    assert w.events("area_enter") == [] and w.sent[n]["area_id"] == "viridian_forest"


def test_major2_a_mid_battle_reconnect_with_a_borrowed_party_hellos_an_empty_party():
    w = live()
    w.enter_battle([FOE], active=(0,))
    w.step()
    w.set_party([mon_record(0x99999999, 0x5555, species=7)])
    w.fire("map_load")
    w.step()
    w.connected = False
    w.step()
    w.connected = True
    w.step()
    assert w.events("hello")[-1]["party"] == []
    assert w.events("hello")[-1]["party_hidden"] is True
    assert "pc_boxes" not in w.events("hello")[-1]
    assert "trade_outstanding" not in w.events("hello")[-1]  # borrowed-only, not a journal declaration


def test_major3_a_force_faint_during_a_borrowed_party_is_held_and_lands_after_restore():
    w = live()
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))
    w.step()
    w.set_party([mon_record(0x99999999, 0x5555, species=7)])  # the partner's party
    w.command(cmd="force_faint", key=KB)
    w.step(3)
    assert w.client.battle_pending_count(w.client) == 1 and w.writes == []
    w.set_party(party(A, B))                                   # restored
    w.step()
    assert w.party_hp(1) == 0 and w.client.battle_pending_count(w.client) == 0


def _checkpoint_sound(w):
    return json.loads((REPO / "data" / "games" / w.pack / "write_checkpoint.json")
                      .read_text(encoding="utf-8"))[w.title]["sound"]


TRACK0 = 0x03006300


def _m4a_world(title="firered", ident_ok=True, track=TRACK0):
    """An m4a SE1 player at the pack's own gMPlayInfo_SE1 (FR/LG sound block), its track in
    IWRAM, and SE 26's song header in ROM. The REAL writes.lua + safety.lua decide."""
    w = live("gen3_frlg", title)
    snd = _checkpoint_sound(w)
    player = snd["player_se1"]["address"]
    w.poke_int(player + snd["ident_off"], snd["ident_magic"] if ident_ok else 0, 4)
    w.poke_int(player + snd["tracks_off"], track, 4)
    hdr = w.profile["rom"]["SE_SONG_HEADERS"]["26"] - 0x08000000
    for i, b in enumerate([1, 0, 5, 0, 0, 0, 0, 0, 0x10, 0x20, 0x30, 0x08]):
        w.rom[hdr + i] = b
    return w, snd, player


def _sound_writes(w):
    return [r for r in lua_to_py_list(w.parts.writes.log) if str(r.reason) == "sound"]


@pytest.mark.parametrize("title", ["firered", "leafgreen"])
def test_sound_on_frlg_arms_sound_and_writes_exactly_the_pack_fields(title):
    """POSITIVE (C4-2f): through the real writes.lua ("sound" reason) and the real sound clause
    set, a play_sound lands every byte inside a field of the pack's sound block, logged."""
    w, snd, player = _m4a_world(title)
    w.command(cmd="play_sound", sound=26)
    w.step()
    logged = _sound_writes(w)
    assert logged and sum(int(r.len) for r in logged) == len(w.writes)   # every byte logged
    fields = [((player if f["on"] == "player" else TRACK0) + f["offset"], f["size"]) for f in snd["fields"]]
    for addr, _value, _frame in w.writes:
        assert any(base <= addr < base + size for base, size in fields), hex(addr)
    hdr = w.profile["rom"]["SE_SONG_HEADERS"]["26"]
    assert w._read(player + 0, 4) == hdr                                   # songHeader
    assert w._read(player + 4, 4) == 1                                     # status: 1 track
    assert w._read(TRACK0 + 0, 1) == 0xC0                                  # flags EXIST|START
    assert w._read(TRACK0 + 64, 4) == 0x08302010                           # cmdPtr from the header
    assert w._read(player + snd["ident_off"], 4) == snd["ident_magic"]     # never locked
    assert int(logged[-1].address) == player + 4                           # status published last


def test_sound_with_an_uninitialised_driver_writes_nothing():
    """NEGATIVE: the real sound_player_ready clause fails (ident is not the m4a magic)."""
    w, _snd, _player = _m4a_world(ident_ok=False)
    w.command(cmd="play_sound", sound=26)
    w.step()
    assert w.writes == []
    assert any("sound refused" in line and "not initialised" in line for line in w.logs)


def test_sound_with_a_track_outside_iwram_writes_nothing():
    """NEGATIVE: the real sound_addresses_in_iwram clause fails."""
    w, _snd, _player = _m4a_world(track=0x02000000)
    w.command(cmd="play_sound", sound=26)
    w.step()
    assert w.writes == [] and any("outside IWRAM" in line for line in w.logs)


def test_rr_sound_is_refused_by_name_when_no_player_can_be_resolved():
    w = live("gen3_rr", "radical_red")
    w.command(cmd="play_sound", sound=26)
    w.step()
    assert w.writes == []
    assert len([line for line in w.logs if "names no SE1 player" in line]) == 1


def test_sound_is_refused_while_the_session_is_ineligible():
    w, _snd, _player = _m4a_world()
    w.client.writes_enabled, w.client.gate_revoked = False, True
    w.command(cmd="play_sound", sound=26)
    w.step()
    assert w.writes == [] and "sound" not in w.battle_checks


# RR v1 transport fixtures remain for panel/sound/rival checks.
# Durable trade behavior and replacements for retired raw-copy success tests live in test_gen3_trade.py.

PARTNER = mon_record(0x55555555, 0x00009999, species=25, nickname="PIKA")
KP = key_of(0x55555555, 0x00009999)
MB = {"opcode": 6, "seq": 8, "status": 10, "ack": 12, "result": 48}
OK_, FAIL_ = 2, 3


def mb_op(w):
    return w._read(NATIVE["BASE"] + MB["opcode"], 2)


def mb_ack(w, status=OK_, result=0):
    """What the companion patch does when it finishes the posted op."""
    base = NATIVE["BASE"]
    w.poke_int(base + MB["ack"], w._read(base + MB["seq"], 2), 2)
    w.poke_int(base + MB["status"], status, 2)
    w.poke_int(base + MB["opcode"], 0, 2)
    w.poke_int(base + MB["result"], result, 1)


@pytest.fixture(autouse=True)
def _battle_nonce(monkeypatch):
    """The battle request nonce is minted by the bootstrap (lua/gen3/run.lua) and consumed through
    $SLINK_GEN3_BATTLE_NONCE, the documented seam; the lupa harness builds Entry directly, so
    without this the client fails closed (no identity, no capability) and every rival test would
    refuse. A test that needs distinct sessions overrides the value itself."""
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "0000BEEF")


def viable_blobs(w, count=1):
    """`count` encoded party mons that pass §4.3's selectable-team rule (hp != 0, real species,
    neither an egg nor a bad egg) -- the blobs a rival swap is actually allowed to stage."""
    return [w.encode(mon_record(0x30000000 + i, OT, species=4 + i, hp=20)).hex().upper()
            for i in range(count)]


def ready_battle(w, trainer_id):
    """Enter a trainer battle and let the client note it: the trainer id is only recorded when
    the per-frame observer runs, which is the same 61-frame warm-up the event test uses."""
    w.set_balls(3)
    w.enter_battle([FOE], trainer_id=trainer_id)
    w.step(61)
    assert w.client.state.battle is not None
    assert w.client.state.battle["trainer_id"] == trainer_id


def battle_identity(w):
    """(session, battle_id) for the battle the client is in (card C5-10): both halves of the
    request identity it minted and announced, read from its own epoch record."""
    battle = w.client.state.battle
    assert battle is not None, "the client is not in a battle"
    return battle["session"], battle["battle_id"]


def trade_world(present=True):
    w = rr_live()
    attach_real_native(w, present)
    return w, w.encode(PARTNER).hex().upper()


def swap_in_partner(w):
    """The patch's scene / silent swap: slot 1 now holds the partner's mon."""
    w.set_party([mon_record(A, OT, species=4, nickname="MON0"), PARTNER])


def apply(w, blob, old_key=KB, slot=1, token="tr1"):
    w.command(cmd="apply_trade", slot=slot, blob_hex=blob, old_key=old_key, token=token)


def trade_writes_are_native_only(w):
    reasons = set(write_reasons(w))
    assert reasons <= {"native"}, reasons
    lo, hi = NATIVE["BASE"], NATIVE["BASE"] + 0x800
    assert all(lo <= a < hi for a, _v, _f in w.writes), "a trade byte landed outside the mailbox arena"


def test_rival_swap_posts_through_the_real_native_path_and_reports_the_readback_as_success():
    """G5-RR-RIVAL (was ..._reports_the_refresh_refusal): OP_RIVAL_SWAP posts through
    writes:arm("native") and the real native clauses, and a gEnemyParty readback that matches the
    staged team IS the success -- the patch consumes the op only inside W1, before the engine's
    selection, so no gBattleMons refresh exists to refuse."""
    w, _blob = trade_world()
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step(2)
    # C5-11a: the rival path posts its OWN opcode (28); OP_SET_ENEMY_PARTY (16) is the trade's.
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]
    w.poke(w.ram["ENEMY_BASE"], w.encode(PARTNER))              # the patch copied gEnemyParty
    w.poke_int(w.ram["ENEMY_COUNT_ADDR"], 1, 1)
    mb_ack(w)
    w.step()
    assert w.events("rival_team_replaced") == [], "success waits for the engine snapshot"
    engine_snapshot(w)
    w.step()
    (reply,) = w.events("rival_team_replaced")
    assert reply.get("error") is None and reply["species_ids"] == [25], reply
    trade_writes_are_native_only(w)


def test_rival_swap_whose_readback_does_not_match_still_reports_failure():
    """The success claim is the readback: an OK ack over a gEnemyParty that is NOT the staged team
    is enemy_readback_failed, never a swap."""
    w, _blob = trade_world()
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step(2)
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]
    w.poke_int(w.ram["ENEMY_COUNT_ADDR"], 1, 1)                 # count right, bytes still the FOE's
    mb_ack(w)
    w.step()
    (reply,) = w.events("rival_team_replaced")
    assert reply["error"] == "enemy_readback_failed" and reply["species_ids"] == [], reply


# ── G5-RR-RIVAL: announce before the battle, stage the swap, post it in W1 ──────────────────
RR_ROM = RR_PACK["titles"]["radical_red"]["rom"]
RR_RAM = RR_PACK["titles"]["radical_red"]["ram"]


def rival_script(w, trainer_id=331):
    """The trainerbattle script on the field: gTrainerBattleOpponent_A set, no battle yet."""
    w.poke_int(w.ram["TRAINER_OPPONENT_ADDR"], trainer_id, 2)
    w.step()


def rival_window(w, open_=True):
    """CB2_InitBattle -> SetUpBattleVars: battle_begin fires and gBattleMainFunc is the Dummy
    phase, while gBattleMons[0].maxHP is still 0 (in_battle reads false)."""
    w.poke_int(w.ram["BATTLE_TYPE_ADDR"], w.d["BATTLE_TYPE_TRAINER_MASK"], 4)   # StartTrainerBattle
    w.poke_int(RR_RAM["BATTLE_MAIN_FUNC_ADDR"], RR_ROM["BEGIN_BATTLE_INTRO_DUMMY_ADDR"] if open_ else 0x0801333D, 4)
    w.poke_int(RR_RAM["BATTLE_COMM_ADDR"], 0 if open_ else 16, 1)


def engine_snapshot(w, pid=0x55555555):
    """BattleIntroDrawTrainersOrMonsSprites: gBattleMons[1] holds the (swapped) lead's PID."""
    w.poke_int(w.ram["BATTLE_MONS_ADDR"] + 0x58 + 0x48, pid, 4)


def begin_trainer_battle(w):
    """BattleSetup_StartTrainerBattle sets the trainer type bit, then CB2_InitBattle fires."""
    w.poke_int(w.ram["BATTLE_TYPE_ADDR"], w.d["BATTLE_TYPE_TRAINER_MASK"], 4)
    w.fire("battle_begin")


def pre_announced(w, trainer_id=331):
    w.step(3)                                                   # the opponent's first read latches 0
    rival_script(w, trainer_id)
    (tbs,) = w.events("trainer_battle_start")
    return tbs


def test_rr_announces_a_trainer_battle_on_the_opponent_edge_before_the_battle():
    w, _blob = trade_world()
    tbs = pre_announced(w)
    assert tbs["trainer_id"] == 331 and tbs["session"] == "0000BEEF" and tbs["battle_id"] == 1
    assert w.client.state.battle is None                        # nothing began yet
    w.step(30)
    assert len(w.events("trainer_battle_start")) == 1           # an edge, not a level


def test_a_stale_opponent_after_a_battle_is_not_announced_again():
    w, _blob = trade_world()
    w.set_balls(3)
    w.step(3)
    w.enter_battle([FOE], trainer_id=331)                        # announced in battle (no edge seen)
    w.step(61)
    w.leave_battle()
    w.step(30)                                                   # opponent still reads 331
    assert [e["trainer_id"] for e in w.events("trainer_battle_start")] == [331]


@pytest.mark.parametrize("kind,announced", [("clean", 0), ("companion", 1)])
def test_frlg_keeps_the_in_battle_announcement(kind, announced):
    """Clean FR announces in battle only. The companion FR builds a native part, and client.lua's
    pre_announce hook keys on native.rival_window_open EXISTING (not on the pack), so a
    FR/LG/Emerald companion announces on the opponent edge too (found by the slice-2 migration)."""
    w = live(kind=kind)
    w.step(3)
    w.poke_int(w.ram["TRAINER_OPPONENT_ADDR"], 5, 2)
    w.step(10)
    assert len(w.events("trainer_battle_start")) == announced


def test_a_pre_announced_swap_is_staged_and_posts_in_the_w1_window():
    w, _blob = trade_world()
    tbs = pre_announced(w)
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=tbs["session"],
              battle_id=tbs["battle_id"])
    w.step(20)
    assert w.events("rival_team_replaced") == [] and mb_op(w) == 0, "staged, not posted, not refused"
    begin_trainer_battle(w)                                      # the battle this id named
    w.step()
    assert mb_op(w) == 0, "battle_begin alone is not the window"
    rival_window(w)
    w.step()
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]
    assert w._read(NATIVE["BASE"] + 17, 2) == 331
    w.poke(w.ram["ENEMY_BASE"], w.encode(PARTNER))
    w.poke_int(w.ram["ENEMY_COUNT_ADDR"], 1, 1)
    mb_ack(w)
    w.step()
    engine_snapshot(w)
    w.step()
    (reply,) = w.events("rival_team_replaced")
    assert reply.get("error") is None and reply["species_ids"] == [25], reply
    assert w.client.state.battle["battle_id"] == tbs["battle_id"]
    assert len(w.events("trainer_battle_start")) == 1, "the battle is never announced twice"
    trade_writes_are_native_only(w)


def test_a_swap_that_misses_the_window_is_refused_cleanly():
    """Late arrival (OMP F9: one outcome): the battle began, W1 passed, the command arrives in
    battle -> the hold is over, it posts, and the PATCH refuses REASON_WINDOW_CLOSED; the reply
    names it and nothing lands in gEnemyParty."""
    w, _blob = trade_world()
    tbs = pre_announced(w)
    begin_trainer_battle(w)
    rival_window(w, open_=False)
    w.poke_int(w.ram["BATTLE_MONS_ADDR"] + 0x28 + 4, 20, 2)     # gBattleMons[0].maxHP: in battle
    w.step(40)                                                  # past RIVAL_WINDOW with no window
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=tbs["session"],
              battle_id=tbs["battle_id"])
    enemy_before = w._read(w.ram["ENEMY_BASE"], 4)
    w.step(2)
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]
    mb_ack(w, status=3)
    w.poke_int(NATIVE["BASE"] + 14, 8, 2)                       # REASON_WINDOW_CLOSED
    w.step()
    (reply,) = w.events("rival_team_replaced")
    assert reply["error"] == "window_closed" and reply["reason"] == "window_closed", reply
    assert w._read(w.ram["ENEMY_BASE"], 4) == enemy_before


def test_omp_f5_a_refused_carry_never_announces_the_battle_twice():
    """The carry is refused (the opponent changed under it), yet the battle keeps its one
    announcement: exactly one trainer_battle_start and one rival_team_replaced."""
    w, _blob = trade_world()
    tbs = pre_announced(w)
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=tbs["session"],
              battle_id=tbs["battle_id"])
    w.step(2)
    w.set_balls(3)
    w.enter_battle([FOE], trainer_id=77, fire=False)            # a different trainer's battle
    w.fire("battle_begin")
    w.step(70)
    assert len(w.events("trainer_battle_start")) == 1
    assert len(w.events("rival_team_replaced")) == 1


def test_omp_f6_a_stale_doubles_bit_on_the_field_does_not_refuse_a_singles_swap():
    w, _blob = trade_world()
    w.poke_int(w.ram["BATTLE_TYPE_ADDR"], w.d["BATTLE_TYPE_DOUBLE_MASK"], 4)   # the last battle's
    tbs = pre_announced(w)
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=tbs["session"],
              battle_id=tbs["battle_id"])
    w.step(3)
    assert w.events("rival_team_replaced") == [], "staged, not refused slots_unviable"
    begin_trainer_battle(w)                                     # singles: the type bits are rewritten
    rival_window(w)
    w.step()
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]


def test_clean_rr_never_reads_the_opponent_off_battle_nor_announces_early():
    w = World("gen3_rr", "radical_red", "clean")
    w.set_party(party(A, B))
    w.step_to(60)
    reads = []
    real = w._read
    w._read = lambda addr, size: (reads.append(addr), real(addr, size))[1]
    w.poke_int(w.ram["TRAINER_OPPONENT_ADDR"], 331, 2)
    w.step(20)
    assert w.ram["TRAINER_OPPONENT_ADDR"] not in reads
    assert w.events("trainer_battle_start") == []


def test_a_reset_clears_the_opponent_latch():
    w, _blob = trade_world()
    w.step(3)
    rival_script(w, 331)
    assert w.client.state.opp_seen == 331
    w.client.driver.on_reset()
    assert w.client.state.opp_seen is None and w.client.state.pre_announced_id is None


def test_review_f1_a_defeated_trainer_edge_does_not_swallow_the_rivals():
    """Talking to a beaten trainer loads its id (a pre-announcement whose battle never begins);
    the rival's edge right after must still be announced and its swap must post in W1."""
    w, _blob = trade_world()
    beaten = pre_announced(w, trainer_id=102)
    rival_script(w, 331)
    ids = [(e["trainer_id"], e["battle_id"]) for e in w.events("trainer_battle_start")]
    assert ids == [(102, beaten["battle_id"]), (331, beaten["battle_id"])], ids
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=beaten["session"],
              battle_id=beaten["battle_id"])
    w.step(5)
    assert mb_op(w) == 0 and w.events("rival_team_replaced") == []
    begin_trainer_battle(w)
    rival_window(w)
    w.step()
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"] and w._read(NATIVE["BASE"] + 17, 2) == 331


def test_review_f1_a_job_staged_for_a_replaced_authority_is_refused():
    w, _blob = trade_world()
    beaten = pre_announced(w, trainer_id=102)
    w.command(cmd="replace_rival_team", trainer_id=102, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=beaten["session"],
              battle_id=beaten["battle_id"])
    w.step(2)
    rival_script(w, 331)                                         # replaces 102's authority
    w.step(2)
    (reply,) = w.events("rival_team_replaced")
    assert reply["trainer_id"] == 102 and mb_op(w) == 0
    assert reply["error"] in ("stale_battle_id", "not_in_battle"), reply


def test_review_f2_a_wild_battle_never_carries_a_pre_authority():
    w, _blob = trade_world()
    tbs = pre_announced(w)
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=tbs["session"],
              battle_id=tbs["battle_id"])
    w.step(2)
    w.poke_int(w.ram["BATTLE_TYPE_ADDR"], 0, 4)                  # a WILD battle begins
    w.fire("battle_begin")
    w.poke_int(RR_RAM["BATTLE_MAIN_FUNC_ADDR"], RR_ROM["BEGIN_BATTLE_INTRO_DUMMY_ADDR"], 4)
    w.poke_int(RR_RAM["BATTLE_COMM_ADDR"], 0, 1)
    w.step(2)
    assert mb_op(w) == 0, "nothing posts into a wild battle"
    battle = w.client.state.battle
    assert battle is not None and battle["trainer_sent"] is None and battle["trainer_id"] is None
    (reply,) = w.events("rival_team_replaced")
    assert reply["error"] == "stale_battle_id", reply


def test_review_f4_a_dropped_announcement_opens_no_authority():
    w, _blob = trade_world()
    w.step(3)
    w.connected = False
    rival_script(w, 331)
    w.step(2)
    w.connected = True
    w.step(3)
    assert w.events("trainer_battle_start") == []
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session="0000BEEF", battle_id=1)
    w.step(2)
    (reply,) = w.events("rival_team_replaced")
    assert reply["error"] == "not_in_battle" and mb_op(w) == 0, reply


def test_a_pre_announcement_whose_battle_never_begins_holds_nothing_after_a_battle_end():
    w, _blob = trade_world()
    tbs = pre_announced(w)
    w.command(cmd="replace_rival_team", trainer_id=331, n=1, source="auto",
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=tbs["session"],
              battle_id=tbs["battle_id"])
    w.step(2)
    w.fire("whiteout")                                          # any boundary closes the authority
    w.step(2)
    (reply,) = w.events("rival_team_replaced")
    assert reply["error"] in ("stale_battle_id", "not_in_battle") and mb_op(w) == 0, reply
























def _paused_gift(w):
    """The session pauses (native posting refused, zero bytes) and a gift C lands meanwhile."""
    w.client.writes_enabled, w.client.gate_revoked = False, True
    w.set_party([mon_record(A, OT, species=4, nickname="MON0"), mon_record(B, OT, species=5, nickname="MON1"),
                 mon_record(C, OT, species=6, nickname="MON2")])
    w.fire("mon_given")
    w.step()






def test_codex_c56c_1_control_the_same_paused_gift_without_a_trade_is_captured():
    w, _blob = trade_world()
    _paused_gift(w)
    assert [c["key"] for c in w.events("capture")] == [KC]


PARTNER2 = mon_record(0x66666666, 0x00008888, species=26, nickname="RAI")
KP2 = key_of(0x66666666, 0x00008888)








def test_trade_partner_declining_the_confirm_writes_no_party_byte_and_completes_nothing():
    w, _blob = trade_world()
    w.command(cmd="show_menu", token="cf", text="Trade your MON1 for PIKA?")
    w.step(2)
    assert mb_op(w) == NATIVE["OP_SHOW_MENU"]
    mb_ack(w, result=0)                                          # NO
    w.step()
    assert [(m["token"], m["choice"]) for m in w.events("menu_result")] == [("cf", 0)]
    assert w.events("trade_done") == [] and w.party_hp(1) == 20
    trade_writes_are_native_only(w)


def test_trade_on_rr_clean_has_no_trade_path_and_reports_unchanged():
    w = live("gen3_rr", "radical_red", "clean")
    assert w.parts.native is None
    apply(w, w.encode(PARTNER).hex().upper())
    w.step(5)
    assert w.writes == [] and w.events("trade_done")[-1]["new_key"] == KB










# ── card C5-10: the battle request identity ──────────────────────────────────────────────────

def test_c510_the_identity_is_minted_per_battle_and_the_counter_increments():
    """One session nonce per client process, a counter per battle: the second battle of the same
    client announces battle_id 2 and the SAME nonce (a reconnect or a new battle is not a
    restart)."""
    w = live("gen3_rr", "radical_red")
    w.set_balls(3)
    w.enter_battle([FOE], trainer_id=42)
    w.step(61)
    first = w.events("trainer_battle_start")[-1]
    assert first["battle_id"] == 1 and isinstance(first["session"], str) and first["session"]
    w.leave_battle()
    w.step()
    w.enter_battle([FOE], trainer_id=42)
    w.step(61)
    second = w.events("trainer_battle_start")[-1]
    assert second["battle_id"] == 2, second
    assert second["session"] == first["session"], "one nonce per client process"


def test_c510_a_missing_or_mismatched_identity_is_refused_and_writes_nothing():
    """The guard's vocabulary, for everything that can legitimately reach it: a wrong counter, a
    missing half, and a foreign session nonce are each refused with stale_battle_id, and none of
    them writes. (Malformed shapes never get this far — protocol_schema.py rejects those on the
    wire; see test_c510_malformed_identities_are_schema_violations.)"""
    from tests.unit.protocol_schema import validate_command
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    # only shapes that are schema-LEGAL can reach the guard: a half-identity is rejected on the
    # wire now (protocol_schema.PAIRED_FIELDS), and the malformed-value cases are pinned there.
    for bad in ({"session": session, "battle_id": bid + 1},
                {"session": "00000000", "battle_id": bid}):
        command = {"cmd": "replace_rival_team", "trainer_id": 5, "n": 1,
                   "blobs_hex": ["00" * 100], **bad}
        assert validate_command(command) == [], bad      # every case here is schema-legal
        before = list(w.writes)
        w.command(**command)
        w.step()
        assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id", bad
        assert w.writes == before, bad


def test_c510_malformed_identities_are_schema_violations():
    """Codex REV-5: a boolean, fractional or out-of-range counter, or a malformed nonce, fails
    validation in protocol_schema.py and is NEVER coerced."""
    from tests.unit.protocol_schema import validate_command, validate_event
    base = {"cmd": "replace_rival_team", "trainer_id": 5, "n": 1, "blobs_hex": ["00" * 100],
            "session": "ABCD1234", "battle_id": 1}
    assert validate_command(base) == []
    for bad in ({"battle_id": True}, {"battle_id": 1.5}, {"battle_id": 0},
                {"battle_id": -1}, {"battle_id": 2 ** 32}, {"battle_id": "1"},
                {"session": True}, {"session": ""}, {"session": "not-hex"},
                {"session": "A" * 17}):
        assert validate_command({**base, **bad}), bad
    # all-or-none pairing (Codex C5-10 review): a half-identity is a violation either way
    for half in ({"battle_id": 1}, {"session": "ABCD1234"}):
        command = {k: v for k, v in base.items() if k not in ("session", "battle_id")}
        assert validate_command({**command, **half}), half
    # present-but-null is a violation too (C5-11c minor): None is not "absent"
    for nulls in ({"session": None, "battle_id": 1}, {"session": "ABCD1234", "battle_id": None},
                  {"session": None, "battle_id": None}):
        assert validate_command({**base, **nulls}), nulls
    event = {"event": "trainer_battle_start", "player": "a", "seq": 1, "trainer_id": 42,
             "session": "ABCD1234", "battle_id": 2}
    assert validate_event(event) == []
    for bad in ({"battle_id": True}, {"battle_id": 0.5}, {"session": "zz"}, {"session": "A" * 20}):
        assert validate_event({**event, **bad}), bad


def test_c510_a_foreign_session_and_a_closed_epoch_are_both_refused():
    """Codex REV-5's two falsifiers: session A's counter 1 must not pass in a restarted session B
    (simulated by A's identity with another nonce, which is what B compares against), and a
    delayed command delivered after the battle ended must not swap anything either."""
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    before = list(w.writes)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
              session="DEADBEEF", battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"
    assert w.writes == before
    w.leave_battle()
    w.step()
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] in ("not_in_battle", "stale_battle_id")
    assert w.writes == before


def test_c510b_two_client_sessions_get_distinct_nonces_and_the_old_id_is_refused(monkeypatch):
    """Codex REV-5's exact falsifier, with two REAL client instances: session A's battle 1 and a
    restarted session B's battle 1 both carry counter 1, and A's command must not pass in B.
    The bootstrap's entropy is injected through $SLINK_GEN3_BATTLE_NONCE, the seam entry.lua
    hands to Client.new (card C5-10b)."""
    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "AAAA1111")
    a = live("gen3_rr", "radical_red")
    ready_battle(a, 42)
    start_a = a.events("trainer_battle_start")[-1]
    assert (start_a["session"], start_a["battle_id"]) == ("AAAA1111", 1)

    monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", "BBBB2222")
    b = live("gen3_rr", "radical_red")
    ready_battle(b, 42)
    start_b = b.events("trainer_battle_start")[-1]
    assert (start_b["session"], start_b["battle_id"]) == ("BBBB2222", 1)

    before = list(b.writes)
    b.command(cmd="replace_rival_team", trainer_id=42, n=1, blobs_hex=viable_blobs(b),
              session=start_a["session"], battle_id=start_a["battle_id"])
    b.step()
    assert b.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"
    assert b.writes == before, "the other session's command must write nothing"
    # ... while B's own identity is not itself a refusal (no mailbox answers here: native absent)
    b.command(cmd="replace_rival_team", trainer_id=42, n=1, blobs_hex=viable_blobs(b),
              session=start_b["session"], battle_id=start_b["battle_id"])
    b.step()
    assert b.events("rival_team_replaced")[-1]["error"] == "native absent"


def test_c510b_the_hello_declares_the_battle_identity_capability():
    """The capability the server gates the manual-inject refusal on: the new client declares it,
    and an older client simply omits the field."""
    w = live("gen3_rr", "radical_red")
    (hello,) = w.events("hello")
    assert hello["battle_identity"] is True


def test_c510b_a_missing_or_malformed_seed_fails_closed(monkeypatch):
    """Codex C5-10 review: there is NO deterministic fallback. A client that cannot get a seed
    mints no identity, declares no capability in hello, and refuses every rival command -- the
    swap simply does not happen, which is safer than a session that could collide."""
    for bad in ("zz-not-hex", "A" * 17, ""):
        monkeypatch.setenv("SLINK_GEN3_BATTLE_NONCE", bad)
        w = live("gen3_rr", "radical_red")
        hello = w.events("hello")[-1]
        assert "battle_identity" not in hello, bad
        ready_battle(w, 5)
        start = w.events("trainer_battle_start")[-1]
        assert "session" not in start and "battle_id" not in start, bad
        before = list(w.writes)
        w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
                  session="0000BEEF", battle_id=1)
        w.step()
        assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id", bad
        assert w.writes == before, bad


def test_c510_the_happy_path_passes_the_guard():
    """A valid identity is not itself a refusal: the reply is the final refusal
    (native absent: the companion's mailbox does not answer in the fake ROM), i.e. the command
    got past the guard and into the later checks."""
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "native absent"


# ── C5-11a: the rival opcode, the window refusal and the pre-filters ─────────────────────────

def test_c511a_the_rival_path_posts_opcode_28_with_the_trainer_argument():
    """The rival swap's own opcode (28), never the trade's 16, and the trainer rides in
    args[1..2] as a u16 little-endian."""
    w, _blob = trade_world()
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step(2)
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]
    assert NATIVE["OP_RIVAL_SWAP"] == 28, "the ABI number is fixed by handlers.c"
    assert w._read(NATIVE["BASE"] + 16, 1) == 1          # count
    assert w._read(NATIVE["BASE"] + 17, 2) == 5          # trainer_id, u16 LE


def test_c511a_the_patchs_window_refusal_reaches_the_reply_as_window_closed():
    """The patch's ST_FAIL + REASON_WINDOW_CLOSED (8) must surface as the documented shape:
    error refresh_failed with reason window_closed, and nothing written."""
    w, _blob = trade_world()
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    enemy_before = w._read(w.ram["ENEMY_BASE"], 4)        # the PID: the game state a swap touches
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step(2)
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]
    mb_ack(w, status=3)                                   # ST_FAIL
    w.poke_int(NATIVE["BASE"] + 14, 8, 2)                 # reason = REASON_WINDOW_CLOSED
    w.step()
    (reply,) = w.events("rival_team_replaced")
    # review F3: the patch's own reason is the error (was the misleading "refresh_failed")
    assert reply["error"] == "window_closed" and reply["reason"] == "window_closed", reply
    assert w._read(w.ram["ENEMY_BASE"], 4) == enemy_before, "a refused swap writes no game state"
    trade_writes_are_native_only(w)                       # only the mailbox was staged"


def test_c511a_a_new_client_on_an_old_patch_refuses_cleanly():
    """An unknown opcode on an older patch takes the dispatcher's default ack(ST_FAIL) (no
    reason), so the swap refuses as refresh_failed and nothing is written."""
    w, _blob = trade_world()
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    enemy_before = w._read(w.ram["ENEMY_BASE"], 4)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step(2)
    mb_ack(w, status=3)                                   # ST_FAIL, reason 0 (unset)
    w.step()
    (reply,) = w.events("rival_team_replaced")
    assert reply["error"] == "native_refused" and "reason" not in reply, reply
    assert w._read(w.ram["ENEMY_BASE"], 4) == enemy_before


def test_c511a_the_selectable_team_rule_refuses_no_viable_singles():
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    dead = [w.encode(mon_record(0x40000000, OT, species=4, hp=0, max_hp=20)).hex().upper()]
    before = list(w.writes)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=dead,
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "slots_unviable"
    assert w.writes == before


def test_c511a_the_selectable_team_rule_needs_two_distinct_mons_in_doubles():
    w = live("gen3_rr", "radical_red")
    w.set_balls(3)
    w.enter_battle([FOE], trainer_id=5, doubles=True)
    w.step(61)
    session, bid = battle_identity(w)
    one = viable_blobs(w, 1)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=one,
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "slots_unviable"
    w.command(cmd="replace_rival_team", trainer_id=5, n=2, blobs_hex=viable_blobs(w, 2),
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "native absent"


def test_c511a_the_selectable_team_rule_follows_the_engine_getter_on_bad_eggs():
    """MON_DATA_SPECIES_OR_EGG collapses an empty slot, an egg AND a bad egg to SPECIES_EGG
    (pret src/pokemon.c:3245-3249) -- so a bad egg is not selectable even though its species
    field looks real."""
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    bad_egg = dict(mon_record(0x50000000, OT, species=4, hp=20))
    bad_egg["is_bad_egg"] = 1
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(bad_egg).hex().upper()], session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "slots_unviable"


def test_c510_codex_repro_a_queued_rival_job_cannot_post_in_the_next_battle():
    """Codex's C5-10 repro: battle1 with session S/id1, a menu ACK still pending, a valid rival
    request queued BEHIND it. Battle1 ends, battle2 begins with the SAME trainer and id2, then
    the menu ACKs -- the old request must not post (it would write 107 bytes into battle2)."""
    w, _blob = trade_world()
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    assert bid == 1
    w.command(cmd="show_menu", token="tk", text="?")        # posts, stays pending (async UI)
    w.step(2)
    assert mb_op(w) == NATIVE["OP_SHOW_MENU"]
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step()
    w.leave_battle()                                        # battle1 ends
    w.step()
    ready_battle(w, 5)                                      # battle2, same trainer
    assert w.client.state.battle["battle_id"] == 2
    mb_ack(w)                                               # the menu finally completes
    w.step(2)
    assert mb_op(w) != NATIVE["OP_RIVAL_SWAP"], "the stale rival job must not post"
    assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"


def test_c510_a_client_with_no_epoch_refuses_by_name_instead_of_erroring():
    """MAJOR 1: with no epoch (e.g. attached mid-battle) the refusal must be named, never a Lua
    error from indexing a nil record."""
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    w.client.state.battle = None                            # the mid-battle attach case
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"


def test_c510_trainer_matching_has_no_shortcuts():
    """MAJOR 1: trainer 0, a mismatched trainer and an epoch whose trainer is unknown are each
    refused -- no shortcut accepts any of them."""
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    for trainer in (0, 7):
        w.command(cmd="replace_rival_team", trainer_id=trainer, n=1, blobs_hex=viable_blobs(w),
                  session=session, battle_id=bid)
        w.step()
        assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id", trainer
    w.client.state.battle["trainer_id"] = None
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w),
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"


# ── C5-11c: the rival authority, the encounter result, and the prefilter witnesses ──────────

def test_c511c_a_same_frame_end_plus_begin_lets_no_old_job_post():
    """Codex REV2-C5-10-11 BLOCKER 2: a battle-1 request queued behind a menu, then battle_end AND
    battle_begin for the SAME trainer captured in one frame, then the menu ACKs. The old job must
    not post -- the authority epoch is closed at capture, not by a live RAM comparison."""
    w, _blob = trade_world()
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    assert bid == 1
    w.command(cmd="show_menu", token="tk", text="?")          # posts, stays pending
    w.step(2)
    assert mb_op(w) == NATIVE["OP_SHOW_MENU"]
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step()
    before = list(w.writes)
    w.leave_battle()                                          # captured: closes the authority
    w.enter_battle([FOE], trainer_id=5)                       # same trainer, same frame
    mb_ack(w)                                                 # the menu completes
    w.step(2)
    assert mb_op(w) != NATIVE["OP_RIVAL_SWAP"], "a stale job must not post across the boundary"
    assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"
    assert w.writes == before, "and it must write nothing"


def test_c511c_the_encounter_result_survives_the_boundary_on_both_rr_kinds():
    """MAJOR 3 regression: the old boundary sweep cleared st.battle before battle_end drained, so
    finish_battle skipped the encounter result and the RR COMPANION sent no no_catch where RR
    clean did. Both kinds must send exactly one."""
    for kind in ("clean", "companion"):
        w = World("gen3_rr", "radical_red", kind)
        w.set_party(party(A, B))
        w.step_to(60)
        w.set_balls(3)
        w.step(30)
        w.enter_battle([FOE])                                  # a wild battle: no trainer id
        w.step(30)
        w.leave_battle(outcome=4)                              # ran away
        w.step()
        got = w.events("no_catch")
        assert len(got) == 1, f"{kind}: expected exactly one no_catch, got {len(got)}"


def test_c511c_doubles_is_read_from_the_battle_type_flags_not_the_battler_count():
    """MAJOR 6: gBattlersCount is not initialised yet in W1. With the DOUBLE flag set and a STALE
    count of 2, one eligible mon must still be refused."""
    w = live("gen3_rr", "radical_red")
    w.set_balls(3)
    w.enter_battle([FOE], trainer_id=5, doubles=True)
    w.poke_int(w.ram["BATTLERS_COUNT_ADDR"], 2, 1)             # stale: the flags say doubles
    w.step(61)
    session, bid = battle_identity(w)
    w.command(cmd="replace_rival_team", trainer_id=5, n=1, blobs_hex=viable_blobs(w, 1),
              session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "slots_unviable"


def test_c511c_the_secure_egg_bit_is_what_the_prefilter_reads():
    """MAJOR 6: getter semantics use the SECURE Misc.isEgg (pret pokemon.c:3182-3183), not the
    header flag. A mon with the secure bit set and the header flag clear is not selectable."""
    w = live("gen3_rr", "radical_red")
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    sneaky = dict(mon_record(0x60000000, OT, species=4, hp=20))
    sneaky["is_egg"] = 1                 # the secure substruct bit
    sneaky["is_egg_flag"] = 0            # the header bit stays clear
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(sneaky).hex().upper()], session=session, battle_id=bid)
    w.step()
    assert w.events("rival_team_replaced")[-1]["error"] == "slots_unviable"


# ── C5-11d: the rival authority must die with a reset and with the signal source ────────────

def _rival_job_behind_a_menu(w):
    """Codex REV3's common prefix: battle 1 announced (session S, id 1, trainer 5), a menu posted
    and left pending, a valid rival request for battle 1 queued BEHIND it."""
    ready_battle(w, 5)
    session, bid = battle_identity(w)
    assert bid == 1
    w.command(cmd="show_menu", token="tk", text="?")          # posts, stays pending
    w.step(2)
    assert mb_op(w) == NATIVE["OP_SHOW_MENU"]
    w.command(cmd="replace_rival_team", trainer_id=5, n=1,
              blobs_hex=[w.encode(PARTNER).hex().upper()], session=session, battle_id=bid)
    w.step()


def test_c511d_a_reset_revokes_the_rival_authority():
    """Codex REV3 MAJOR 3, the exact interleaving: announce battle 1, menu pending, rival request
    queued, driver.on_reset(), the same-trainer battle RAM left in place with NO fresh captured
    begin, then the menu ACKs. Before the fix opcode 28 posted with 109 new writes on the
    authority the reset should have closed."""
    w, _blob = trade_world()
    _rival_job_behind_a_menu(w)
    before = list(w.writes)
    w.client.driver.on_reset()
    mb_ack(w)
    w.step(2)
    assert mb_op(w) != NATIVE["OP_RIVAL_SWAP"], "a reset must revoke the rival authority"
    assert w.writes == before, f"{len(w.writes) - len(before)} bytes written after the reset"
    assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"


def _stop_by_failure_field(w):
    w.client.signals.failure = "test: signals failed"        # Codex REV3's repro, verbatim


def _stop_by_close(w):
    w.client.signals.close(w.client.signals)                 # the hooks are uninstalled


def _stop_by_a_fire_error(w):
    """The natural path: the next boundary fire itself fails (its ROM bytes differ at fire
    time), which sets failure BEFORE on_fire could close the authority (signals.lua:104-109)."""
    site = w.sites["battle_end"]
    w.rom[site["rom_offset"]] ^= 0xFF


def _stop_by_a_rejected_fire(w):
    """Boundary callbacks whose address is not the site's are counted and dropped
    (signals.lua:97-100): neither the end nor the next begin was delivered, so nothing closed."""
    for kind in ("battle_end", "battle_begin"):
        fn, addr = w.hooks[f"SLink-gen3-{kind}"]
        w.hooks[f"SLink-gen3-{kind}"] = (fn, addr + 2)


@pytest.mark.parametrize("stop", [_stop_by_failure_field, _stop_by_close, _stop_by_a_fire_error,
                                  _stop_by_a_rejected_fire],
                         ids=["failure", "close", "fire_error", "rejected_fire"])
def test_c511d_a_stopped_signal_source_revokes_the_rival_authority(stop):
    """Codex REV3 MAJOR 4: queue a rival request behind a menu, stop the signal source, end and
    start a same-trainer battle (the stopped source captures neither boundary), then ACK the menu.
    Before the fix opcode 28 posted with 109 writes on the retained id-1 authority. `failure` is
    Codex's exact step; the others are every other way delivery stops."""
    w, _blob = trade_world()
    _rival_job_behind_a_menu(w)
    before = list(w.writes)
    stop(w)
    w.leave_battle()
    w.step()
    w.enter_battle([FOE], trainer_id=5)                       # same trainer, battle RAM live again
    w.step()
    mb_ack(w)
    w.step(2)
    assert mb_op(w) != NATIVE["OP_RIVAL_SWAP"], "a stopped signal source must revoke the authority"
    assert w.writes == before, f"{len(w.writes) - len(before)} bytes written on a dead authority"
    assert w.events("rival_team_replaced")[-1]["error"] == "stale_battle_id"


def test_c511d_control_a_healthy_source_still_posts_the_same_queued_job():
    """The control for both falsifiers: the same prefix with nothing stopped and nothing reset
    posts opcode 28 when the menu ACKs, so the refusals above are the fix, not the harness."""
    w, _blob = trade_world()
    _rival_job_behind_a_menu(w)
    mb_ack(w)
    w.step(2)
    assert mb_op(w) == NATIVE["OP_RIVAL_SWAP"]


# ── C5-11d addendum: the LeafGreen sound stall (a plan applies whole or not at all) ──────────

def _se26_header(w, count, cmd_ptr):
    """SE 26's song header rewritten in ROM: byte 0 trackCount, byte 2 priority, 8..11 the
    track-0 script pointer (the layout m4a_plan reads)."""
    hdr = w.profile["rom"]["SE_SONG_HEADERS"]["26"] - 0x08000000
    for i, b in enumerate([count, 0, 5, 0, 0, 0, 0, 0, *cmd_ptr.to_bytes(4, "little")]):
        w.rom[hdr + i] = b


@pytest.mark.parametrize("count, cmd_ptr, named", [
    (40, 0x08302010, "trackCount 40"),              # the live LG failure: status overflowed LAST
    (0, 0x08302010, "trackCount 0"),
    (1, 0x02001000, "outside ROM"),
], ids=["track_count_40", "track_count_0", "cmd_ptr_in_ewram"])
def test_c511d_a_garbage_se_header_writes_nothing_and_is_refused_by_name(count, cmd_ptr, named):
    """Coordinator addendum (live LeafGreen run): LG's pack carried FR's SE header addresses, so
    m4a_plan read garbage; trackCount >= 32 made status = (1 << count) - 1 overflow u32, and
    writes.lua refused that LAST entry after 11 of 12 had landed -- a garbage songHeader and
    cmdPtr in gMPlayInfo_SE1, and the game stalled. An insane header must be refused by name with
    zero bytes written."""
    w, _snd, _player = _m4a_world("leafgreen")
    _se26_header(w, count, cmd_ptr)
    w.command(cmd="play_sound", sound=26)
    w.step()
    assert w.writes == [], f"{len(w.writes)} bytes written from a garbage header"
    assert any("sound refused" in line and named in line for line in w.logs), w.logs[-3:]


def test_c511d_a_plan_with_an_unwritable_late_entry_writes_nothing():
    """The generic guard, on a plan that is NOT sound: the explode commit's battle-struct entries
    come from a pointer read out of RAM. A garbage pointer makes those late entries unaddressable;
    before the fix the moves, PP, action and move (15 bytes) had already landed when writes.lua
    refused them. Now the whole plan is refused first."""
    w, _base = _explode_world()
    w.poke_int(w.ram["BATTLE_STRUCT_PTR_ADDR"], 0xFFFFFFFF, 4)
    w.command(cmd="force_explode", key=KA)
    w.step()
    assert w.writes == [], f"{len(w.writes)} bytes of a half-applied commit"
    held = lua_to_py_list(w.client.battle_pending)[0]
    assert "nothing written" in str(held.why)


def test_c511d_a_plan_stopped_mid_way_is_logged_as_partial():
    """What pre-validation cannot rule out -- the sink itself failing on byte two -- is reported
    loudly from writes.attempted instead of reading like a clean refusal."""
    w = live()
    w.battle_ok = True
    w.enter_battle([FOE], active=(0,))
    calls = []

    def dies_on_byte_two(addr, value, domain=None):
        calls.append(addr)
        if len(calls) == 2:
            raise RuntimeError("sink died")
        return w._write_u8(addr, value, domain)

    w.io.write_u8 = dies_on_byte_two
    w.command(cmd="force_faint", key=KB)
    w.step()
    assert any("PARTIAL battle_faint write: 2 byte(s) attempted, partial mutation possible" in line for line in w.logs), w.logs[-3:]




# ── static contract ───────────────────────────────────────────────────────────────────────

BIZHAWK = re.compile(r"\b(memory|mainmemory|event|gui|console|emu|client|joypad|comm|gameinfo|bizstring|forms)\s*\.")


def test_the_driver_names_no_bizhawk_global_and_no_title():
    code = "\n".join(line.split("--", 1)[0] for line in
                     (REPO / "lua" / "gen3" / "client.lua").read_text(encoding="utf-8").splitlines())
    assert not BIZHAWK.search(code), BIZHAWK.search(code)
    assert not re.search(r"firered|leafgreen|radical_red|gen3_frlg|gen3_rr", code)
    assert not re.search(r"\bmemory\.write|write_u8\(", code)
    _ = lua_to_py



def test_a_pc_withdraw_is_box_to_party_while_the_party_count_is_stale():
    """Live boxsync_gen3 run 1 (FR A): the withdraw sent no box_to_party. pret pokefirered: the
    storage system recounts gPlayerPartyCount only on box exit (pokemon_storage_system_tasks.c
    Task_OnBPressed/Task_OnCloseBoxPressed state 4). A deposit zeroes its slot under the old
    count; a withdraw lands at slot == count, past a count-bounded read."""
    w = live(pids=(A, B))
    base, size = w.party_base(), 100
    w.poke(base + size, bytes(size))                           # deposit: count stays 2
    w.set_box(0, 0, mon_record(B, OT, species=5))
    w.fire("pc_deposit")
    w.step()
    assert [e["key"] for e in w.events("party_to_box")] == [KB]
    w.set_party(party(A))                                      # box exit recounts: 1
    w.fire("map_load")
    w.step()
    w.set_box(0, 0, None)                                      # withdraw: count stays 1
    w.poke(base + size, w.encode(party(A, B)[1]))
    w.fire("pc_withdraw")
    w.step()
    assert [e["key"] for e in w.events("box_to_party")] == [KB]
    w.set_party(party(A, B))                                   # box exit recounts: 2
    w.fire("map_load")
    w.step()
    assert [e["key"] for e in w.events("box_to_party")] == [KB]   # no duplicate


@pytest.mark.parametrize("pack,title", [("gen3_frlg", "firered"), ("gen3_frlg", "leafgreen"),
                                        ("gen3_rr", "radical_red")])
def test_e3_frlg_rr_packs_carry_todays_client_values_explicitly(pack, title):
    """E3-CLIENT (a): the facts the client used to hard-code are explicit pack fields holding
    today's values -- committed state 3, identity wire->title SE ids, the server's gift areas."""
    from server.adapters.gen3_frlge import _GIFT_AREAS
    wc = json.loads((REPO / "data" / "games" / pack / "write_checkpoint.json")
                    .read_text(encoding="utf-8"))[title]
    headers = json.loads((REPO / "data" / "games" / pack / "profile.json")
                         .read_text(encoding="utf-8"))["titles"][title]["rom"]["SE_SONG_HEADERS"]
    assert wc["battle"]["commit_guard"]["value"] == 3
    assert wc["sound"]["se_ids"] == {k: int(k) for k in headers}
    assert set(wc["gift_areas"]["ids"]) == set(_GIFT_AREAS)


def test_every_write_checkpoint_title_carries_se_ids_and_gift_areas_ids():
    """OMP cx-daf0f544 #7: the client fails closed without either field, so every generated
    title (admitted or not) must ship both, as a JSON object and a list of non-empty strings."""
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    import gen_gen3_write_checkpoint as G
    seen = []
    for pack, titles in G.ALL_PACKS.items():
        wc = json.loads((REPO / "data" / "games" / pack / "write_checkpoint.json")
                        .read_text(encoding="utf-8"))
        assert set(wc) == set(titles), pack
        for title, t in wc.items():
            assert isinstance(t["sound"]["se_ids"], dict) and t["sound"]["se_ids"], title
            ids = t["gift_areas"]["ids"]
            assert isinstance(ids, list) and all(isinstance(i, str) and i for i in ids), title
            seen.append(title)
    assert sorted(seen) == ["emerald", "firered", "leafgreen", "radical_red"]


def test_gift_areas_has_no_default_for_an_unknown_pack():
    """OMP cx-daf0f544 #6: only the allowlisted packs get the Kanto list; any other is fatal."""
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    import gen_gen3_write_checkpoint as G
    assert G.gift_areas("gen3_rr", "radical_red")["ids"] == G.GIFT_AREAS_FRLG
    with pytest.raises(SystemExit, match="no gift_areas rule"):
        G.gift_areas("gen3_sapphire", "sapphire")


def test_every_pack_maps_every_sound_id_the_server_and_session_send():
    """OMP cx-6ecf4fc8 #8: the wire ids are a protocol constant (docs/protocol.md 8.2); a cue a
    title's se_ids lacks is refused on that title only. The server sends 22/25/26/95
    (server/state.py play_sound), lua/core/session.lua sends 26."""
    import re
    wire = {int(n) for n in re.findall(r'"play_sound",\s*"sound":\s*(\d+)', (REPO / "server/state.py").read_text(encoding="utf-8"))}
    wire |= {int(n) for n in re.findall(r"play_sound\((\d+)", (REPO / "lua/core/session.lua").read_text(encoding="utf-8"))}
    assert wire >= {22, 25, 26, 95}, wire
    for pack in ("gen3_frlg", "gen3_rr", "gen3_emerald"):
        wc = json.loads((REPO / "data/games" / pack / "write_checkpoint.json").read_text(encoding="utf-8"))
        for title, block in wc.items():
            if isinstance(block, dict) and "sound" in block:
                mapped = {int(k) for k in block["sound"]["se_ids"]}
                assert wire <= mapped, (pack, title, sorted(wire - mapped))


# ── ACQ diagnostics: log-only lines naming each acquisition's signal, its path and its outcome ───────────────────────────
def acq(w, *needles):
    return [line for line in w.logs if "[SLink-gen3] ACQ " in line and all(n in line for n in needles)]


def caught_world():
    w = live()
    w.set_balls(5)
    w.step(30)                                            # the tick latches has_pokeballs
    w.logs.clear()                                        # the connect-time baseline line is not what these tests count
    return w


def test_acq_a_party_capture_logs_its_signals_and_the_capture_exactly_once():
    w = caught_world()
    w.enter_battle([FOE])
    w.set_party(party(A, B, C))
    w.fire("capture_wild")
    w.fire("mon_given")
    w.step()
    w.step(40)                                            # more frames: nothing repeats
    (cap,) = w.events("capture")
    assert len(acq(w, "signal kind=capture_wild", "in_battle=true")) == 1 and len(acq(w, "signal kind=mon_given")) == 1
    (line,) = acq(w, "capture via=settle:party")
    assert f"key={KC} " in line and "species=6 " in line and "gift=false" in line and "sent=true" in line
    assert acq(w, "none") == [] and acq(w, "skipped") == [] and acq(w, "held") == []
    assert len(acq(w)) == 3


def test_acq_a_gift_and_a_box_capture_name_their_own_path():
    w = caught_world()
    w.set_party(party(A, B, C))
    w.fire("mon_given")
    w.step()
    (gift,) = acq(w, "capture via=settle:party")
    assert "gift=true" in gift and "sent=true" in gift
    w = live(pids=(A, B))
    w.logs.clear()
    w.enter_battle([mon_record(C, OT, species=19)])
    w.set_box(1, 0, mon_record(C, OT, species=19))
    w.fire("capture_wild")
    w.fire("pc_move")
    w.step()
    (boxed,) = acq(w, "capture via=settle:box")
    assert f"key={KC} " in boxed and "species=19" in boxed and "sent=true" in boxed


def test_acq_nothing_acquired_logs_nothing():
    w = caught_world()
    w.step(200)
    w.fire("faint")
    w.step(5)
    assert acq(w) == []


def test_acq_an_acquisition_signal_with_no_unknown_key_says_so_once():
    w = caught_world()
    w.fire("capture_wild")                                 # caught, but nothing new is in the party or boxes
    w.step(30)
    assert w.events("capture") == []
    (line,) = acq(w, "none via=settle")
    assert "caught=true" in line and "known_already=all" in line and "no capture sent" in line
    assert len(acq(w, "signal kind=capture_wild")) == 1


def test_acq_a_mon_the_quiet_observer_absorbs_is_logged_with_that_path():
    """The silent route: a mon appears with NO acquisition signal and observe_known marks it known (so it is never a capture)."""
    w = caught_world()
    w.set_party(party(A, B, C))
    w.step(120)
    assert w.events("capture") == []
    (line,) = acq(w, "known via=seed:observe")
    assert "new=1" in line and f"keys={KC}" in line
    w.step(120)
    assert len(acq(w, "known via=")) == 1                    # and only once


def test_acq_a_signal_dropped_while_a_trade_settles_is_logged_once():
    w = caught_world()
    w.client.state.trade_settle_until = 10 ** 9              # io.framecount() < trade_settle_until: "trading"
    w.set_party(party(A, B, C))
    w.fire("capture_wild")
    w.step(20)
    assert w.events("capture") == []
    (line,) = acq(w, "skipped")
    assert "reason=trading" in line and "dropped=true" in line and "caught=true" in line


def test_acq_the_connect_baseline_names_its_path_and_adds_no_behaviour():
    w = World()
    w.set_party(party(A, B))
    w.step_to(60)
    (line,) = acq(w, "known via=seed:hello")
    assert "new=2" in line and KA in line and KB in line
    assert len(w.events("hello")) == 1 and w.events("capture") == []


def test_acq_the_client_does_not_seed_known_from_the_server_or_a_partner():
    """Report, pinned: every st.known write in lua/gen3/client.lua is a local-RAM path (seed_known from the party and box cache, a
    capture/hatch/pc/identity settle, a link-trade completion). Nothing reads a server message, a partner event or the HUD notice
    (server/state.py 'Partner caught ...' is a hud_show text the client only displays)."""
    src = (REPO / "lua" / "gen3" / "client.lua").read_text(encoding="utf-8")
    writes = [line.strip() for line in src.splitlines() if re.search(r"st\.known\[[^\]]+\]\s*(,[^=]*)?=\s*true", line)]
    assert writes, "no st.known writes found"
    assert not re.search(r"st\.known\[[^\]]+\]\s*=\s*(msg|cmd|payload)", src)
    assert "Partner caught" not in src and "partner" not in "".join(w for w in writes)
