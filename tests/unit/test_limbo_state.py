from copy import deepcopy

import pytest

from server.recovery import RecoveryConflict
from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo, SoulLinkState

A, B, NEW = "11111111:0000ABCD", "22222222:0000BCDE", "33333333:0000ABCD"


def linked(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    entry = LinkEntry("route_1", MonInfo(A, species=25), MonInfo(B, species=7), LinkStatus.ALIVE)
    state.links.append(entry)
    state._index_entry(entry)
    state.area_states["route_1"] = AreaStatus.LINKED
    state.party_keys = {"a": {A}, "b": {B}}
    state.pokeballs_obtained = {"a": True, "b": True}
    state.recovery_clients = {"a", "b"}
    return state


def census(state, generation, records=(), player="a", **fields):
    return state.handle_event(player, {
        "event": "tick", "inventory_complete": True, "inventory_generation": generation,
        "party": list(records), "pc_boxes": [], "pc_boxes_generation": generation, **fields,
    })


def missing(state, records=()):
    census(state, 1, records)
    census(state, 2, records)
    return state.recovery.limbo["a"][A]["revision"]


def test_fresh_absence_persists_limbo_and_holds_only_affected_writes(tmp_path):
    state = linked(tmp_path)
    state.queued_commands["a"] = [{"cmd": "box_mon", "key": A}]
    first = census(state, 1)
    assert not state.recovery.limbo["a"]
    assert next(c for c in first if c["cmd"] == "recovery_control")["paused"] == [A]
    assert not any(c["cmd"] == "box_mon" for c in first)
    census(state, 2)
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert not loaded.load_failed
    assert A in loaded.recovery.limbo["a"]
    assert loaded.links[0].status == LinkStatus.ALIVE
    loaded.recovery_clients.add("b")
    loaded.queued_commands["b"] += [
        {"cmd": "force_faint", "key": B}, {"cmd": "force_faint", "key": "unrelated"}]
    commands = loaded.handle_event("b", {"event": "tick"})
    assert not any(c.get("key") == B and c["cmd"] == "force_faint" for c in commands)
    assert any(c.get("key") == "unrelated" and c["cmd"] == "force_faint" for c in commands)


@pytest.mark.parametrize("invalid", [
    {"inventory_complete": False}, {"in_battle": True}, {"party_hidden": True},
    {"pc_boxes": [None]}, {"inventory_generation": True},
])
def test_unqualified_census_never_confirms_loss(tmp_path, invalid):
    state = linked(tmp_path)
    census(state, 1)
    census(state, 2, **invalid)
    census(state, 3)
    assert not state.recovery.limbo["a"]


def test_reappearance_automatically_resumes_original_link(tmp_path):
    state = linked(tmp_path)
    missing(state)
    commands = census(state, 3, [{"key": A, "hp": 10, "maxHP": 20}])
    assert not state.recovery.limbo["a"]
    assert state.links[0].a.key == A
    assert next(c for c in commands if c["cmd"] == "recovery_control")["paused"] == []
    assert state.recovery.history[-1]["action"] == "reappeared"


def test_admin_rebind_preserves_pair_and_uses_current_stats(tmp_path):
    state = linked(tmp_path)
    revision = missing(state, [{"key": NEW, "hp": 30, "maxHP": 40, "species_id": 26}])
    state.mon_stats["a"][A] = {"level": 5, "maxHP": 12}
    state.mon_stats["a"][NEW] = {"level": 25, "maxHP": 40}
    state.recovery.resolve(state, "a", A, revision, "rebind", NEW)
    assert state.entry_for("a", NEW).a.species == 26
    assert state.entry_for("a", A) is None
    assert state.mon_stats["a"][NEW] == {"level": 25, "maxHP": 40}
    assert not state.recovery.paused(state)["b"]
    assert SoulLinkState.load(data_dir=str(tmp_path)).links[0].a.key == NEW


def test_detach_keeps_survivor_and_durable_cancellation(tmp_path):
    state = linked(tmp_path)
    revision = missing(state)
    state.recovery.resolve(state, "a", A, revision, "detach")
    assert not state.links
    assert state.unpaired_mons["route_1"]["b"].key == B
    assert state.area_states["route_1"] == AreaStatus.DEAD_ZONE
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    control = loaded.recovery.control(loaded, "b")
    assert control["paused"] == []
    assert control["cancelled"][B] > 0


def test_retirement_propagates_death_without_trying_to_bury_absent_mon(tmp_path):
    state = linked(tmp_path)
    revision = missing(state)
    state.recovery.resolve(state, "a", A, revision, "retire")
    assert state.links[0].status == LinkStatus.DEAD
    assert not state.recovery.limbo["a"]
    assert B in state.pending_memorials["b"]
    assert A not in state.pending_memorials["a"]
    assert any(c.get("key") == B and c["cmd"] == "force_faint" for c in state.queued_commands["b"])


@pytest.mark.parametrize("action", ["detach", "retire", "rebind"])
def test_failed_resolution_write_restores_all_state(tmp_path, monkeypatch, action):
    state = linked(tmp_path)
    revision = missing(state, [{"key": NEW, "hp": 30, "maxHP": 40, "species_id": 26}])
    original = (tmp_path / "links.json").read_bytes()
    incident = deepcopy(state.recovery.limbo["a"][A])

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(state, "_atomic_write_json", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        state.recovery.resolve(state, "a", A, revision, action, NEW)
    assert state.recovery.limbo["a"][A] == incident
    assert state.links[0].status == LinkStatus.ALIVE and state.links[0].a.key == A
    assert (tmp_path / "links.json").read_bytes() == original


def test_stale_resolution_and_dead_rebind_are_refused(tmp_path):
    state = linked(tmp_path)
    revision = missing(state, [{"key": NEW, "hp": 30, "maxHP": 40}])
    with pytest.raises(RecoveryConflict, match="changed"):
        state.recovery.resolve(state, "a", A, "old-revision", "detach")
    state.links[0].status = LinkStatus.DEAD
    with pytest.raises(RecoveryConflict, match="confirmed death"):
        state.recovery.resolve(state, "a", A, revision, "rebind", NEW)


def test_retiring_confirmed_death_keeps_partner_death_obligation(tmp_path):
    state = linked(tmp_path)
    revision = missing(state)
    state._propagate_faint("b", state.links[0])
    state.queued_commands["b"].append({"cmd": "force_faint", "key": B})
    state.recovery.resolve(state, "a", A, revision, "retire")
    assert any(c.get("key") == B and c["cmd"] == "force_faint" for c in state.queued_commands["b"])
    assert A not in state.pending_memorials["a"]


def test_detach_refuses_to_overwrite_another_survivor(tmp_path):
    state = linked(tmp_path)
    revision = missing(state)
    state.unpaired_mons["route_1"] = {"b": MonInfo(NEW, species=26)}
    with pytest.raises(RecoveryConflict, match="already occupies"):
        state.recovery.resolve(state, "a", A, revision, "detach")
    assert state.unpaired_mons["route_1"]["b"].key == NEW
    assert state.links[0].a.key == A


def test_regular_tick_does_not_reset_independent_census_streak(tmp_path):
    state = linked(tmp_path)
    census(state, 1)
    state.handle_event("a", {"event": "tick"})
    census(state, 2)
    assert A in state.recovery.limbo["a"]
