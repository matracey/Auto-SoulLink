"""Durable rule transitions and directed ordinary PC intents, without a cartridge."""
from __future__ import annotations

import json

import pytest

from server.state import LinkEntry, LinkStatus, MonInfo, SoulLinkState

A, B = "11111111:0000ABCD", "22222222:0000BCDE"
ROM, STREAM = "ab" * 20, "12" * 16
STATS = {"level": 15, "maxHP": 55, "attack": 30, "defense": 20, "speed": 18,
         "spAtk": 22, "spDef": 19, "pp1": 20, "pp2": 15, "pp3": 0, "pp4": 0}


def linked(tmp_path):
    s = SoulLinkState(data_dir=str(tmp_path))
    e = LinkEntry(area_id="route_1", a=MonInfo(key=A), b=MonInfo(key=B), status=LinkStatus.ALIVE)
    s.links.append(e)
    s._index_entry(e)
    s.party_keys = {"a": {A}, "b": {B}}
    s.party_size = {"a": 1, "b": 1}
    s.pokeballs_obtained = {"a": True, "b": True}
    s._has_helld.update(("a", "b"))
    return s


def bind(s, pid="b", party=None, **extra):
    key = A if pid == "a" else B
    snapshot = [{**m, "maxHP": m.get("maxHP", 20)} for m in (party or [])]
    return s.handle_event(pid, {"event": "hello", "rom_type": "firered",
                               "report_delivery": 1, "rom_sha1": ROM,
                               "ot_id": int(key.split(":")[1], 16),
                               "has_pokeballs": True, "party": snapshot,
                               **extra})


def report(s, pid, number, event, **fields):
    return {"event": event, **fields, "report": {
        "stream": STREAM, "number": number, "binding": dict(s.report_bindings[pid])}}


def intent(cmds, name="box_mon"):
    return next(c for c in cmds if c["cmd"] == name)


def test_reports_commit_transition_and_receipt_in_one_write(tmp_path, monkeypatch):
    s = linked(tmp_path)
    bind(s, "a", [{"key": A, "hp": 20}])
    writes = []
    write = s._atomic_write_json
    monkeypatch.setattr(s, "_atomic_write_json", lambda path, data: (writes.append(data), write(path, data))[1])
    msg = report(s, "a", 1, "party_to_box", key=A, stats=STATS)
    cmds = s.handle_event("a", msg)
    assert any(c["cmd"] == "report_ack" for c in cmds)
    assert len(writes) == 1
    saved = json.loads((tmp_path / "links.json").read_text())
    assert saved["mon_stats"]["a"][A] == STATS
    assert saved["gameplay_delivery"]["receipts"]["a"][STREAM]["number"] == 1
    assert saved["queued_sync"]["b"][0]["key"] == B


def test_duplicate_receipt_survives_restart_and_does_not_repeat_capture(tmp_path):
    s = SoulLinkState(data_dir=str(tmp_path))
    bind(s, "a", [{"key": A, "hp": 20}])
    msg = report(s, "a", 1, "capture", key=A, area_id="route_2", species_id=4,
                 in_box=True, stats=STATS)
    assert any(c["cmd"] == "report_ack" for c in s.handle_event("a", msg))
    old = s.pending_captures["route_2"]["a"].key
    restored = SoulLinkState.load(data_dir=str(tmp_path))
    assert not restored.load_failed
    cmds = restored.handle_event("a", msg)
    assert [c["cmd"] for c in cmds] == ["report_ack"]
    assert restored.pending_captures["route_2"]["a"].key == old
    assert len(restored.pending_captures["route_2"]) == 1


def test_uncommitted_report_is_not_acknowledged_and_replays_cleanly(tmp_path, monkeypatch):
    s = linked(tmp_path)
    bind(s, "a", [{"key": A, "hp": 20}])
    write = s._atomic_write_json

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(s, "_atomic_write_json", fail)
    msg = report(s, "a", 1, "party_to_box", key=A, stats=STATS)
    assert s.handle_event("a", msg) == [{"cmd": "noop", "refused": "report_not_committed"}]
    assert A in s.party_keys["a"] and B in s.party_keys["b"]
    assert STREAM not in s.report_receipts["a"]
    assert not s.queued_commands["b"]
    monkeypatch.setattr(s, "_atomic_write_json", write)
    assert any(c["cmd"] == "report_ack" for c in s.handle_event("a", msg))


@pytest.mark.parametrize("change", ["run_id", "player", "ot_id", "rom_sha1"])
def test_report_cannot_cross_run_player_save_or_cartridge(tmp_path, change):
    s = linked(tmp_path)
    bind(s, "a", [{"key": A, "hp": 20}])
    msg = report(s, "a", 1, "party_to_box", key=A, stats=STATS)
    msg["report"]["binding"][change] = "wrong"
    assert s.handle_event("a", msg)[0]["refused"] == "report_binding"
    assert A in s.party_keys["a"]


def test_report_order_is_enforced(tmp_path):
    s = linked(tmp_path)
    bind(s, "a")
    assert s.handle_event("a", report(s, "a", 2, "area_enter", area_id="route_2"))[0]["refused"]
    assert "route_2" not in s.area_states


def test_sync_intent_primitives_retain_identity_until_owner_cancellation(tmp_path):
    s = linked(tmp_path)
    s.queued_commands["b"].append({"cmd": "box_mon", "key": B})
    s._prepare_sync_intents()
    first = dict(s.sync_versions["b"][B])
    s._prepare_sync_intents()
    assert s.sync_versions["b"][B] == first
    revision = s.suspend_sync("b", B, reason="census required")
    assert revision > first["revision"]
    assert not s.queued_commands["b"]
    restored = SoulLinkState.load(data_dir=str(tmp_path))
    assert restored.sync_versions["b"][B]["suspended"]
    restored.resume_sync("b", B)
    assert not restored.sync_versions["b"][B].get("suspended")


@pytest.mark.parametrize("command", ["box_mon", "party_mon"])
def test_legacy_delivery_retries_after_persistence_recovers(tmp_path, monkeypatch, command):
    s = linked(tmp_path)
    s.queued_commands["b"] = [{"cmd": command, "key": B}, {"cmd": "hud_show", "text": "Keep"}]
    s._save()
    write = s._atomic_write_json
    before = dict(s.sync_versions["b"][B])

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(s, "_atomic_write_json", fail)
    for _ in range(2):
        assert s.handle_event("b", {"event": "tick"}) == [{"cmd": "noop", "refused": "persistence"}]
        assert not s.sync_delivered["b"]
        assert not s.sync_inflight["b"]
        assert len(s.queued_commands["b"]) == 2
    monkeypatch.setattr(s, "_atomic_write_json", write)
    cmds = s.handle_event("b", {"event": "tick"})
    assert intent(cmds, command)["intent_id"] == before["intent_id"]
    assert any(c["cmd"] == "hud_show" for c in cmds)
    assert before["intent_id"] in s.sync_delivered["b"]
    assert (B, command) in s.sync_inflight["b"]
    assert s.handle_event("b", {"event": "tick"}) == [{"cmd": "noop"}]


def test_directed_deposit_survives_long_checkpoint_and_restart(tmp_path):
    s = linked(tmp_path)
    bind(s, "b", [{"key": B, "hp": 20}])
    s.handle_event("a", {"event": "party_to_box", "key": A, "stats": STATS})
    cmd = intent(s.handle_event("b", {"event": "tick", "party": [{"key": B, "hp": 20}]}))
    for _ in range(20):
        retry = intent(s.handle_event("b", {"event": "tick", "party": [{"key": B, "hp": 20}]}))
        assert retry["intent_id"] == cmd["intent_id"]
        assert not any(c["cmd"] == "party_mon" for c in s.queued_commands["a"])
    restored = SoulLinkState.load(data_dir=str(tmp_path))
    cmds = bind(restored, "b", [{"key": B, "hp": 20}])
    assert intent(cmds)["intent_id"] == cmd["intent_id"]
    assert restored.sync_versions["b"][B]["active"]


def test_deposit_completion_caches_every_stat_and_settles_intent(tmp_path):
    s = linked(tmp_path)
    bind(s, "b", [{"key": B, "hp": 20}])
    s.handle_event("a", {"event": "party_to_box", "key": A})
    cmd = intent(s.handle_event("b", {"event": "tick"}))
    msg = report(s, "b", 1, "stats_cache", key=B, stats=STATS,
                 intent_id=cmd["intent_id"], intent_revision=cmd["intent_revision"])
    cmds = s.handle_event("b", msg)
    assert any(c["cmd"] == "report_ack" for c in cmds)
    assert not s.sync_versions["b"][B]["active"]
    assert s.mon_stats["b"][B] == STATS
    assert not any(c["cmd"] == "box_mon" for c in s.handle_event("b", {"event": "tick"}))


def test_stale_completion_cannot_settle_new_opposite_intent(tmp_path):
    s = linked(tmp_path)
    bind(s, "b", [{"key": B, "hp": 20}])
    s.handle_event("a", {"event": "party_to_box", "key": A})
    old = intent(s.handle_event("b", {"event": "tick"}))
    s.handle_event("a", {"event": "box_to_party", "key": A})
    new = intent(s.handle_event("b", {"event": "tick"}), "party_mon")
    assert old["intent_id"] != new["intent_id"]
    stale = report(s, "b", 1, "stats_cache", key=B, stats=STATS,
                   intent_id=old["intent_id"], intent_revision=old["intent_revision"])
    cmds = s.handle_event("b", stale)
    assert any(c["cmd"] == "report_ack" for c in cmds)
    assert s.sync_versions["b"][B]["intent_id"] == new["intent_id"]
    assert s.sync_versions["b"][B]["active"]
    assert B not in s.mon_stats["b"]


def test_qualified_census_confirms_applied_deposit_but_not_partial_snapshot(tmp_path):
    s = linked(tmp_path)
    bind(s, "b", [{"key": B, "hp": 20}])
    s.handle_event("a", {"event": "party_to_box", "key": A})
    cmd = intent(s.handle_event("b", {"event": "tick"}))
    bind(s, "b", [], pc_boxes=[{"key": B}])
    assert s.sync_versions["b"][B]["active"]
    replay = bind(s, "b", [], pc_boxes=[{"key": B}], pc_boxes_generation=1)
    assert intent(replay)["intent_id"] == cmd["intent_id"]
    assert s.sync_versions["b"][B]["active"]
    s.handle_event("b", {"event": "tick", "party": [], "pc_boxes": [{"key": B}],
                         "pc_boxes_generation": 1})
    assert not s.sync_versions["b"][B]["active"]
    assert not any(c.get("intent_id") == cmd["intent_id"] for c in s.queued_commands["b"])
    # A late, matching completion may still supply the stat block lost at delivery.
    s.handle_event("b", report(s, "b", 1, "stats_cache", key=B, stats=STATS,
                              intent_id=cmd["intent_id"], intent_revision=cmd["intent_revision"]))
    assert s.mon_stats["b"][B] == STATS


@pytest.mark.parametrize("command", ["box_mon", "party_mon"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("event", ["tick", "hello"])
def test_census_cannot_confirm_an_opposite_intent_before_delivery(tmp_path, command, restart, event):
    s = linked(tmp_path)
    previous = "party_mon" if command == "box_mon" else "box_mon"
    s.queued_commands["b"].append({"cmd": previous, "key": B})
    old = intent(s.handle_event("b", {"event": "tick"}), previous)
    s.queued_commands["b"].append({"cmd": command, "key": B})
    s._save()
    if restart:
        s = SoulLinkState.load(data_dir=str(tmp_path))
    census = {
        "event": event, "rom_type": "firered", "has_pokeballs": True,
        "party": [{"key": B, "hp": 20}] if command == "party_mon" else [],
        "pc_boxes": [{"key": B}] if command == "box_mon" else [],
        "pc_boxes_generation": 1,
    }
    new = intent(s.handle_event("b", census), command)
    assert new["intent_id"] != old["intent_id"]
    assert s.sync_versions["b"][B]["active"]
    s.handle_event("b", {**census, "event": "tick"})
    assert not s.sync_versions["b"][B]["active"]
    assert not any(c.get("intent_id") == new["intent_id"] for c in s.queued_commands["b"])


def test_death_supersedes_pc_intent_and_refuses_late_completion(tmp_path):
    s = linked(tmp_path)
    bind(s, "b", [{"key": B, "hp": 20}])
    s.handle_event("a", {"event": "party_to_box", "key": A})
    old = intent(s.handle_event("b", {"event": "tick"}))
    s._handle_faint("a", {"key": A})
    s._save()
    assert not s.sync_versions["b"][B]["active"]
    assert not any(c["cmd"] in ("box_mon", "party_mon") for c in s.queued_commands["b"])
    assert s.handle_event("b", {"event": "stats_cache", "key": B, "stats": STATS,
                              "intent_id": old["intent_id"], "intent_revision": old["intent_revision"]})[0]["refused"]


def test_owner_scoped_suspension_invalidates_completions_and_blocks_inverse_sync(tmp_path):
    s = linked(tmp_path)
    bind(s, "b", [{"key": B, "hp": 20}])
    s.handle_event("a", {"event": "party_to_box", "key": A})
    cmd = intent(s.handle_event("b", {"event": "tick"}))
    revision = s.suspend_sync("b", B, reason="operator census required")
    assert revision > cmd["intent_revision"]
    for _ in range(10):
        s.handle_event("b", {"event": "tick", "party": [{"key": B, "hp": 20}]})
    assert not any(c["cmd"] == "party_mon" for c in s.queued_commands["a"])
    assert s.sync_versions["b"][B]["suspended"]
    restored = SoulLinkState.load(data_dir=str(tmp_path))
    assert restored.sync_versions["b"][B]["suspended"]
    restored.resume_sync("b", B)
    assert not restored.sync_versions["b"][B].get("suspended")


def test_backlog_census_does_not_invent_inverse_pc_moves_before_report_replay(tmp_path):
    s = linked(tmp_path)
    bind(s, "a", [{"key": A, "hp": 20}], report_backlog=1)
    s.party_keys["a"].discard(A)
    s.party_keys["b"].discard(B)
    s.handle_event("a", {"event": "tick", "party": [{"key": A, "hp": 20}], "report_backlog": 1})
    assert not any(c["cmd"] == "party_mon" for c in s.queued_commands["b"])
    s.handle_event("a", {"event": "tick", "party": [{"key": A, "hp": 20}], "report_backlog": 0})
    assert any(c["cmd"] == "party_mon" for c in s.queued_commands["b"])


@pytest.mark.asyncio
@pytest.mark.parametrize("received", [False, True])
async def test_tcp_reconnect_replays_reports_before_or_after_persisted_receipt(tmp_path, received):
    from server.server import SLinkServer
    from tests.unit.companion_evidence import companion
    from tests.unit.test_mixed_foundations import _hello
    from tests.unit.test_server_run_reconnect import _send, _tcp

    srv = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    hello = _hello("a", {"rom_type": "firered", **companion("firered"), "report_delivery": 1,
                         "ot_id": 0xABCD, "rom_sha1": ROM})
    async with _tcp(srv) as (connect, _):
        old = await connect()
        reply = await _send(old, hello)
        assert any(c["cmd"] == "report_binding" for c in reply["commands"])
        msg = {"player": "a", **report(srv.state, "a", 1, "no_catch", area_id="route_40")}
        if received:
            reply = await _send(old, msg)
            assert any(c["cmd"] == "report_ack" for c in reply["commands"])
        old[1].close()
        await old[1].wait_closed()
    restored = SLinkServer(data_dir=str(tmp_path), retry_same_species=False)
    async with _tcp(restored) as (connect, _):
        fresh = await connect()
        await _send(fresh, hello)
        response = await _send(fresh, msg)
        assert any(c["cmd"] == "report_ack" for c in response["commands"])
        assert restored.state.report_receipts["a"][STREAM]["number"] == 1
        assert restored.state.area_states["route_40"].value == "dead_zone"
