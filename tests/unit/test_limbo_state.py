import pytest

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
