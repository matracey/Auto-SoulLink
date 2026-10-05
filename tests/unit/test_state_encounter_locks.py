import json

from server.server import SLinkServer
from server.state import EncounterLock, SoulLinkState
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
