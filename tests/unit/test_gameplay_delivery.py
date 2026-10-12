"""Durable rule transitions and directed ordinary PC intents, without a cartridge."""
from __future__ import annotations

import json

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
