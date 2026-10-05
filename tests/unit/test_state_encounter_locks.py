import json

from server.server import SLinkServer
from server.state import AreaStatus, EncounterLock, MonInfo, SoulLinkState
from server.status_payload import empty_status_payload


def test_encounter_locks_round_trip(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state.encounter_locks = {
        "route_4": {
            "a": EncounterLock(species=5, level=12),
            "b": EncounterLock(species=19, level=10),
        }
    }
    state._save()

    loaded = SoulLinkState.load(data_dir=str(tmp_path))

    assert loaded.encounter_locks == state.encounter_locks


def test_old_save_defaults_to_no_encounter_locks(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state._save()
    path = tmp_path / "links.json"
    saved = json.loads(path.read_text())
    saved.pop("encounter_locks")
    path.write_text(json.dumps(saved))

    assert SoulLinkState.load(data_dir=str(tmp_path)).encounter_locks == {}


def test_malformed_encounter_locks_are_ignored(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state._save()
    path = tmp_path / "links.json"
    saved = json.loads(path.read_text())
    saved["encounter_locks"] = {
        "route_4": {
            "a": {"species": 5, "level": 12},
            "b": {"species": True, "level": -1},
            "c": {"species": 19, "level": 3},
        },
        "bad": [],
    }
    path.write_text(json.dumps(saved))

    loaded = SoulLinkState.load(data_dir=str(tmp_path))

    assert loaded.encounter_locks == {"route_4": {"a": EncounterLock(species=5, level=12)}}


def test_status_payload_describes_locked_species(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    server.state.encounter_locks = {"route_4": {"a": EncounterLock(species=5, level=12)}}

    lock = server._build_status_dict()["encounter_locks"]["route_4"]["a"]

    assert lock["species"] == 5
    assert lock["level"] == 12
    assert lock["species_name"] == "Charmeleon"
    assert "img" in lock["sprite_html"]


def test_empty_status_includes_encounter_locks():
    assert empty_status_payload()["encounter_locks"] == {}


def test_failed_first_encounter_locks_species_and_stays_open(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))

    commands = state.handle_event(
        "a",
        {"event": "no_catch", "area_id": "route_4", "species_id": 5, "level": 12},
    )

    assert state.encounter_locks == {"route_4": {"a": EncounterLock(species=5, level=12)}}
    assert state.area_states.get("route_4") != AreaStatus.DEAD_ZONE
    assert not state.links
    assert {"cmd": "unresolve_area", "area_id": "route_4"} in commands
    assert any(c.get("cmd") == "gui_prompt" and "Charmeleon" in c["text"] for c in commands)


def test_later_failures_keep_the_original_species_and_level(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state.handle_event(
        "a",
        {"event": "no_catch", "area_id": "route_4", "species_id": 5, "level": 12},
    )

    commands = state.handle_event(
        "a",
        {"event": "no_catch", "area_id": "route_4", "species_id": 19, "level": 20},
    )

    assert state.encounter_locks["route_4"]["a"] == EncounterLock(species=5, level=12)
    assert any(c.get("cmd") == "gui_prompt" and "not Rattata" in c["text"] for c in commands)


def test_failed_retry_preserves_partner_pending_capture(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))
    state.pending_captures["route_4"] = {"b": MonInfo(key="B:1", species=19, level=10)}

    state.handle_event(
        "a",
        {"event": "no_catch", "area_id": "route_4", "species_id": 5, "level": 12},
    )

    assert state.pending_captures["route_4"]["b"].key == "B:1"
    assert state.encounter_locks["route_4"]["a"].species == 5


def test_missing_species_keeps_area_open_without_guessing(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path))

    commands = state.handle_event("a", {"event": "no_catch", "area_id": "route_4"})

    assert state.encounter_locks == {}
    assert {"cmd": "unresolve_area", "area_id": "route_4"} in commands


def test_disabled_retry_rule_keeps_legacy_dead_zone(tmp_path):
    state = SoulLinkState(data_dir=str(tmp_path), retry_same_species=False)

    state.handle_event(
        "a",
        {"event": "no_catch", "area_id": "route_4", "species_id": 5, "level": 12},
    )

    assert state.area_states["route_4"].value == "dead_zone"
    assert state.encounter_locks == {}
