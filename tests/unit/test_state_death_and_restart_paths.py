"""Death bookkeeping and restart durability holes found by the 2026-10-03 code sweep (OMP cx-c5babcca, cx-06955fa9).

Each test is the replay of one sequence the sweep named, checked at the source before the fix.
"""
from __future__ import annotations

import pytest

from server.state import LinkStatus, SoulLinkState
from tests.unit.test_state import has_cmd, make_state_with_link


def _fresh(tmp_path, monkeypatch, **kw) -> SoulLinkState:
    monkeypatch.setattr("server.state.LINKS_PATH", str(tmp_path / "links.json"))
    return make_state_with_link(**kw)


# ── death bookkeeping ───────────────────────────────────────────────────────────────────────

def test_a_whiteout_before_the_first_poke_ball_retires_nothing(tmp_path, monkeypatch):
    """Faints before the first Poke Ball are ignored (_handle_faint's gate); a whiteout from the same lost battle (the
    rival fight, with a gift-linked starter) must be ignored too, or it kills the partner's starter."""
    state = _fresh(tmp_path, monkeypatch)
    state.pokeballs_obtained = {"a": False, "b": False}
    state.handle_event("a", {"event": "whiteout"})
    assert state.links[0].status == LinkStatus.ALIVE
    assert not has_cmd(state.handle_event("b", {"event": "tick"}), "force_faint")


def test_a_faint_found_at_hello_drops_the_dead_mon_from_the_party(tmp_path, monkeypatch):
    """_propagate_faint discarded only the partner's half; the hello path relied on it, so a mon just declared dead
    still counted in the player's linked party (and refused the partner's withdrawals as 'party full')."""
    state = _fresh(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "hello", "rom_type": "firered", "ot_id": "1",
                             "party": [{"key": "A:1", "hp": 0, "maxHP": 30, "level": 12}]})
    assert state.links[0].status == LinkStatus.DEAD
    assert "A:1" not in state.party_keys["a"]


def test_a_failed_burial_drops_the_dead_mon_from_the_party(tmp_path, monkeypatch):
    """memorialize_failed must do the bookkeeping memorialize_done does."""
    state = _fresh(tmp_path, monkeypatch, status=LinkStatus.DEAD)
    state.pending_memorials["a"].add("A:1")
    state.handle_event("a", {"event": "memorialize_failed", "key": "A:1", "reason": "saved boxes not initialized"})
    assert "A:1" not in state.party_keys["a"]


# ── restart durability ──────────────────────────────────────────────────────────────────────

def test_a_partner_sync_waiting_for_an_offline_partner_survives_a_restart(tmp_path, monkeypatch):
    """A deposits a linked mon while B is offline; the box_mon for B waits in the queue. A server restart before B
    reconnects used to drop it, leaving the pair split with nothing able to notice."""
    state = _fresh(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "party_to_box", "key": "A:1"})
    assert has_cmd(state.queued_commands["b"], "box_mon", "B:2")
    reloaded = SoulLinkState.load()
    assert has_cmd(reloaded.queued_commands["b"], "box_mon", "B:2")


@pytest.mark.parametrize("completion", ["answer", "inventory"])
def test_a_delivered_partner_sync_replays_until_completion_survives_restart(tmp_path, monkeypatch, completion):
    state = _fresh(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "party_to_box", "key": "A:1"})
    delivered = next(c for c in state.handle_event("b", {"event": "tick"}) if c["cmd"] == "box_mon")
    reloaded = SoulLinkState.load()
    replay = next(c for c in reloaded.handle_event("b", {"event": "tick"}) if c["cmd"] == "box_mon")
    assert replay == delivered
    message = ({"event": "stats_cache", "key": "B:2",
                "stats": {"hp": 20, "maxHP": 20, "level": 5},
                "intent_id": replay["intent_id"], "intent_revision": replay["intent_revision"]}
               if completion == "answer" else
               {"event": "tick", "party": [], "pc_boxes": [{"key": "B:2"}], "pc_boxes_generation": 1})
    answer = reloaded.handle_event("b", message)
    assert not any(c.get("refused") for c in answer), answer
    assert not reloaded.sync_versions["b"]["B:2"]["active"]
    assert not has_cmd(SoulLinkState.load().queued_commands["b"], "box_mon")


def test_a_whiteout_rebuild_still_owes_the_offline_partner_after_a_restart(tmp_path, monkeypatch):
    """The rebuild queues party_mon for both halves; the hello re-arm only rebuilds the whited-out player's own half, so
    the partner's halves must survive a restart in the queue (sweep cx-06955fa9 finding 2)."""
    from server.state import AreaStatus, LinkEntry, MonInfo
    state = _fresh(tmp_path, monkeypatch)
    boxed = LinkEntry(area_id="route_2", a=MonInfo(key="A:boxed", level=6), b=MonInfo(key="B:boxed", level=6),
                      status=LinkStatus.ALIVE)
    state.links.append(boxed)
    state._index_entry(boxed)
    state.area_states["route_2"] = AreaStatus.LINKED
    state.handle_event("a", {"event": "whiteout"})
    assert has_cmd(state.queued_commands["b"], "party_mon", "B:boxed")
    reloaded = SoulLinkState.load()
    assert has_cmd(reloaded.queued_commands["b"], "party_mon", "B:boxed")


def test_a_restored_sync_for_a_key_that_migrated_or_died_is_dropped(tmp_path, monkeypatch):
    """Review cx-6911d575: a key that evolved or traded away while the server was down, or a pair that died, must not
    get its persisted box_mon/party_mon delivered to a cartridge that no longer holds it."""
    import json
    state = _fresh(tmp_path, monkeypatch)
    state.handle_event("a", {"event": "party_to_box", "key": "A:1"})
    doc = json.loads((tmp_path / "links.json").read_text())
    doc["key_migration_ledger"] = {"a": [], "b": [{"old_key": "B:2", "new_key": "B:9"}]}
    (tmp_path / "links.json").write_text(json.dumps(doc))
    assert not has_cmd(SoulLinkState.load().queued_commands["b"], "box_mon", "B:2")
    doc["key_migration_ledger"] = {"a": [], "b": []}
    doc["links"][0]["status"] = "dead"
    (tmp_path / "links.json").write_text(json.dumps(doc))
    assert not has_cmd(SoulLinkState.load().queued_commands["b"], "box_mon", "B:2")
