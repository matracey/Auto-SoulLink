"""Detail cards share the real status payload and never invent missing live values."""
import pytest

from server.adapters import get_adapter
from server.server import SLinkServer

KEY = "1DBA9C3D:1B4ABAE3"
OTHER = "17645363:1BB09F66"


@pytest.mark.parametrize("game", ["gen3_frlge", "gen1_rby", "gen1_purergb", "gen2_gsc", "gen4_hgsspt", "gen5_bw"])
def test_status_enrichment_uses_owning_adapter_and_marks_unreported_pp(tmp_path, game):
    srv = SLinkServer(data_dir=str(tmp_path))
    options = {"gen3_frlge": {"is_rr": True}, "gen2_gsc": {"title": "crystal"}}.get(game, {})
    adapter = get_adapter(game, **options)
    srv._player_adapters["a"] = adapter
    species = 476 if game == "gen3_frlge" else 25
    detail = {"species_id": species, "key": KEY, "hp": 12, "maxHP": 20, "moves": [33], "pp": [0]}
    srv.party_details["a"] = {KEY: detail}
    srv.pc_boxes["a"] = [{"key": OTHER, "species_id": species, "moves": [33]}]
    srv.battle_state["a"]["enemy_party"] = [{"species_id": species, "gender": "female", "moves": [33]}]
    payload = srv._build_status_dict()["players"]["a"]
    party = payload["party_details"][KEY]
    assert party["nature"] == adapter.calc_nature(KEY)
    assert party["type_names"] == list(dict.fromkeys(adapter.type_name(t) for t in adapter.species_types(species)))
    assert party["ability_slots"] == []
    assert payload["battle_state"]["enemy_party"][0]["gender"] == "female"
    assert party["move_details"][0]["current_pp"] == 0
    assert party["move_details"][0]["current_pp_known"] is True
    assert payload["pc_boxes"][0]["move_details"][0]["current_pp_known"] is False
    assert payload["battle_state"]["enemy_party"][0]["move_details"][0]["current_pp_known"] is False

def test_actual_abilities_survive_ingestion_on_every_surface(tmp_path, caplog):
    srv = SLinkServer(data_dir=str(tmp_path))
    srv._player_adapters["a"] = get_adapter("gen3_frlge", is_rr=True)
    record = {"key": KEY, "species_id": 476, "ability_id": 115,
              "ability_slots": [227, 115, 189], "ability_slot": 2, "ability_randomized": True}
    srv.party_details["a"] = srv._party_snapshot("a", [record])
    srv.pc_boxes["a"] = [dict(record)]
    srv.battle_state["a"]["enemy_party"] = [dict(record)]
    payload = srv._build_status_dict()["players"]["a"]
    for mon in (payload["party_details"][KEY], payload["pc_boxes"][0],
                payload["battle_state"]["enemy_party"][0]):
        assert mon["ability_name"] == "Magic Guard"
        assert [slot["name"] for slot in mon["ability_slots"]] == ["Illusion", "Magic Guard", "Forewarn"]
        assert [slot["active"] for slot in mon["ability_slots"]] == [False, True, False]
        assert mon["ability_randomized"] is True
    record["ability_slots"] = None
    record["ability_error"] = "ability reader ROM anchor mismatch"
    srv.party_details["a"] = srv._party_snapshot("a", [record])
    mon = srv._build_status_dict()["players"]["a"]["party_details"][KEY]
    assert mon["ability_slots"] == [] and mon["ability_error"]
    record["ability_slots"] = ["invalid", {}, None]
    srv.party_details["a"] = srv._party_snapshot("a", [record])
    assert srv._build_status_dict()["players"]["a"]["party_details"][KEY]["ability_slots"] == []
    assert "invalid reported ability slots" in caplog.text
