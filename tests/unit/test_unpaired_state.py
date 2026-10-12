from server.state import AreaStatus, MonInfo, SoulLinkState


def test_unpaired_mons_persist_without_link_behavior(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state.unpaired_mons["route_25"] = {
        "b": MonInfo(key="B:SHITTO", nickname="Shitto", species=132, level=17),
    }
    state.area_states["route_25"] = AreaStatus.DEAD_ZONE
    state._save()

    restored = SoulLinkState.load(data_dir=str(tmp_path))

    assert restored.unpaired_mons["route_25"]["b"].key == "B:SHITTO"
    assert restored.entry_for("b", "B:SHITTO") is None
    restored.handle_event("b", {"event": "party_to_box", "key": "B:SHITTO"})
    assert restored.queued_commands == {"a": [], "b": []}
