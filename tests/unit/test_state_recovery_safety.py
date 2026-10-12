import pytest

from server.state import AreaStatus, LinkEntry, LinkStatus, MonInfo, SoulLinkState


def linked_state(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state.pokeballs_obtained = {"a": True, "b": True}
    for i in (1, 2):
        entry = LinkEntry(area_id=f"route_{i}", a=MonInfo(key=f"A:{i}"),
                          b=MonInfo(key=f"B:{i}"), status=LinkStatus.ALIVE)
        state.links.append(entry)
        state._index_entry(entry)
        state.party_keys["a"].add(f"A:{i}")
        state.party_keys["b"].add(f"B:{i}")
    return state


@pytest.mark.parametrize("player", ["a", "b"])
def test_pending_capture_death_survives_restart_and_cannot_link(tmp_path, player):
    state = linked_state(tmp_path)
    partner = "b" if player == "a" else "a"
    key = f"{player.upper()}:3"
    state.pending_captures["route_3"] = {player: MonInfo(key=key, species=25)}
    state.party_keys[player].add(key)
    commands = state.handle_event(player, {"event": "faint", "key": key})
    assert any(c.get("cmd") == "memorialize" and c["key"] == key for c in commands)
    assert state.area_states["route_3"] == AreaStatus.DEAD_ZONE
    assert "route_3" not in state.pending_captures
    loaded = SoulLinkState.load(data_dir=str(tmp_path))
    assert loaded.entry_for(player, key).status == LinkStatus.DEAD
    loaded.handle_event(partner, {"event": "capture", "key": f"{partner.upper()}:3",
                                  "area_id": "route_3", "species_id": 7})
    assert not any(e.area_id == "route_3" and e.status == LinkStatus.ALIVE for e in loaded.links)


@pytest.mark.parametrize("event", ["tick", "safe"])
@pytest.mark.parametrize("ignore_wild,trainer,dead", [
    (False, None, True), (True, None, False), (True, False, False), (True, True, False),
])
def test_snapshot_recovers_missed_faint_without_guessing_wild_policy(
        tmp_path, event, ignore_wild, trainer, dead):
    state = linked_state(tmp_path)
    state.ignore_wild_faints = ignore_wild
    state.handle_event("a", {"event": event, "party": [
        {"key": "A:1", "hp": 20, "maxHP": 20}, {"key": "A:2", "hp": 20, "maxHP": 20}]})
    message = {"event": event, "party": [{"key": "A:1", "hp": 0, "maxHP": 20},
                                        {"key": "A:2", "hp": 20, "maxHP": 20}]}
    if trainer is not None:
        message["is_trainer_battle"] = trainer
    state.handle_event("a", message)
    assert (state.links[0].status == LinkStatus.DEAD) is dead
    partner_commands = state.handle_event("b", {"event": "tick"})
    assert any(c.get("cmd") == "force_faint" and c.get("key") == "B:1"
               for c in partner_commands) is dead


@pytest.mark.parametrize("guard", ["pre_gate", "ambiguous", "settling", "invalid_hp"])
def test_uncertain_snapshot_does_not_initiate_death(tmp_path, guard):
    state = linked_state(tmp_path)
    message = {"event": "tick", "party": [{"key": "A:1", "hp": 0, "maxHP": 20}]}
    if guard == "pre_gate":
        state.pokeballs_obtained["a"] = False
    elif guard == "ambiguous":
        state.ambiguous_keys["a"]["A:1"] = {}
    elif guard == "settling":
        state._trade_settle_ticks["a"] = 2
    else:
        message["party"][0]["maxHP"] = 0
    state.handle_event("a", message)
    assert state.links[0].status == LinkStatus.ALIVE


@pytest.mark.parametrize("evidence", ["wild_snapshot", "wild_event", "restart"])
def test_exempt_zero_does_not_become_death_in_later_trainer_battle(tmp_path, evidence):
    state = linked_state(tmp_path)
    state.ignore_wild_faints = True
    state.handle_event("a", {"event": "tick", "party": [{"key": "A:1", "hp": 20, "maxHP": 20}]})
    if evidence == "wild_snapshot":
        state.handle_event("a", {"event": "tick", "is_trainer_battle": False,
                                 "party": [{"key": "A:1", "hp": 0, "maxHP": 20}]})
    elif evidence == "wild_event":
        state.handle_event("a", {"event": "faint", "key": "A:1", "_is_wild_battle": True})
    else:
        state._save()
        state = SoulLinkState.load(data_dir=str(tmp_path))
    state.handle_event("a", {"event": "tick", "is_trainer_battle": True,
                             "party": [{"key": "A:1", "hp": 0, "maxHP": 20}]})
    assert state.links[0].status == LinkStatus.ALIVE
    state.handle_event("a", {"event": "tick", "is_trainer_battle": True,
                             "party": [{"key": "A:1", "hp": 20, "maxHP": 20}]})
    state.handle_event("a", {"event": "tick", "is_trainer_battle": True,
                             "party": [{"key": "A:1", "hp": 0, "maxHP": 20}]})
    assert state.links[0].status == LinkStatus.ALIVE
    state.handle_event("a", {"event": "faint", "key": "A:1", "_is_wild_battle": False})
    assert state.links[0].status == LinkStatus.DEAD


def test_duplicate_snapshot_key_cannot_initiate_death(tmp_path):
    state = linked_state(tmp_path)
    state.handle_event("a", {"event": "tick", "party": [
        {"key": "A:1", "hp": 20, "maxHP": 20}, {"key": "A:1", "hp": 0, "maxHP": 20}]})
    assert state.links[0].status == LinkStatus.ALIVE


def test_full_partner_keeps_new_pair_quarantined(tmp_path):
    state = linked_state(tmp_path)
    state.party_size = {"a": 6, "b": 2}
    state.pending_captures["route_3"] = {"a": MonInfo(key="A:3", species=25)}
    commands = state.handle_event("b", {"event": "capture", "key": "B:3",
                                       "area_id": "route_3", "species_id": 7})
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "B:3" for c in commands)
    assert any(c.get("cmd") == "box_mon" and c.get("key") == "A:3"
               for c in state.queued_commands["a"])
    assert not any(c.get("cmd") == "party_mon" for c in commands)


def test_dead_zone_with_only_b_half_can_be_memorialized(tmp_path):
    state = linked_state(tmp_path)
    entry = LinkEntry(area_id="route_3", a=None, b=MonInfo(key="B:3"), status=LinkStatus.DEAD)
    state.links.append(entry)
    state._index_entry(entry)
    commands = state.handle_event("b", {"event": "box_to_party", "key": "B:3"})
    assert {"cmd": "memorialize", "key": "B:3"} in commands


def test_pending_npc_exchange_updates_clause_metadata(tmp_path):
    state = linked_state(tmp_path)
    old, new = "11111111:12345678", "22222222:12345678"
    state.pending_captures["route_3"] = {"a": MonInfo(key=old, species=25, nickname="Before")}
    state.handle_event("a", {"event": "key_change", "old_key": old, "new_key": new,
                            "new_species": 7, "new_nickname": "After", "reason": "npc_trade"})
    cap = state.pending_captures["route_3"]["a"]
    assert (cap.key, cap.species, cap.nickname) == (new, 7, "After")
