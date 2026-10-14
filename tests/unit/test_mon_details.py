"""Detail cards share the real status payload and never invent missing live values."""
import copy
from pathlib import Path

import jinja2
import pytest

from server.adapters import get_adapter
from server.board import board_context
from server.server import SLinkServer
from server.status_payload import empty_status_payload

ROOT = Path(__file__).resolve().parents[2]

KEY = "1DBA9C3D:1B4ABAE3"
OTHER = "17645363:1BB09F66"


def sample_status():
    status = empty_status_payload()
    status["players"]["a"].update(
        connected=True, rom_type="firered_rr", trainer_name="Martin",
        last_seen_label="just now", party_keys=[KEY],
        party_details={KEY: {
            "key": KEY, "nickname": "Gastrodon", "species_id": 476,
            "species_name": "Gastrodon", "level": 37, "hp": 140, "maxHP": 140,
            "gender": "female", "held_item_id": 209, "item_name": "Mystic Water",
            "nature": "Relaxed", "type_names": ["Water", "Ground"],
            "sprite_html": '<img class="mon-sprite" src="/static/sprites/rr/476.png" alt="Gastrodon">',
            "ability_id": 115, "ability_name": "Magic Guard", "ability_randomized": True,
            "ability_slots": [
                {"label": "Ability 1", "id": 227, "name": "Illusion", "active": False},
                {"label": "Ability 2", "id": 115, "name": "Magic Guard", "active": True},
                {"label": "Hidden Ability", "id": 189, "name": "Forewarn", "active": False}],
            "calc_stats": {"stats": {"hp": 140, "atk": 77, "def": 72, "spa": 84, "spd": 77, "spe": 40},
                           "ivs": {"hp": 31, "atk": 0}, "evs": {"hp": 0}},
            "move_details": [{"name": "Rain Dance", "type_name": "Water", "power": 0,
                              "accuracy": 0, "pp": 5, "current_pp": 0, "current_pp_known": True,
                              "split": 2}],
        }})
    status["players"]["a"]["capabilities"].update(
        abilities=True, stat_stage_labels=["ATK", "DEF", "SPD", "SATK", "SDEF", "ACC", "EVA"])
    status["links"] = [{
        "area_id": "route_3", "area_display": "Route 3", "status": "alive",
        "a_key": KEY, "a_species": 476, "a_species_name": "Gastrodon", "a_nickname": "Gastrodon",
        "a_level": 37, "a_sprite_html": '<img class="mon-sprite" src="/static/sprites/rr/476.png" alt="Gastrodon">',
        "b_key": OTHER, "b_species": 1370, "b_species_name": "Terapagos-Terastal",
        "b_nickname": "Gappy Gus", "b_level": 25, "b_sprite_html": "",
    }]
    return status

def card(status, pid="a", live=True):
    ctx = board_context(status, live=live)
    row = next(row for _, rows in ctx["board"]["sections"] for row in rows)
    return ctx["detail_card"](row[pid], pid, "pair-route_3", row)

def render(status, **kwargs):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "server/templates"), autoescape=True)
    return env.get_template("_mon_details.html").module.template(card(status, **kwargs))

def test_detail_card_keeps_zeroes_and_does_not_mutate_payload():
    status = sample_status()
    before = copy.deepcopy(status)
    detail = card(status)
    assert status == before
    assert detail["nature"] == "Relaxed"
    assert detail["area"] == "Route 3"
    assert detail["partner"]["nickname"] == "Gappy Gus"
    assert detail["stats"][0] == {"name": "HP", "value": 140, "iv": 31, "ev": 0}
    assert detail["stats"][1]["iv"] == 0
    assert detail["stats"][2]["iv"] is None
    assert detail["ability"] == "Magic Guard"
    markup = render(status)
    assert "0/5" in markup and ">0%</td>" not in markup
    assert "Illusion" in markup and "Hidden Ability" in markup and "Randomized" in markup

@pytest.mark.parametrize("mode", ["offline", "stale", "stopped", "box", "limbo", "withheld"])
def test_snapshot_provenance_and_missing_location(mode):
    status = sample_status()
    player = status["players"]["a"]
    if mode == "offline":
        player["connected"] = False
    elif mode == "stale":
        player["stale"] = True
    elif mode == "box":
        player["pc_boxes"] = [{**player["party_details"].pop(KEY), "box": 0}]
    elif mode == "limbo":
        status["limbo"]["a"][KEY] = {"revision": "1"}
    elif mode == "withheld":
        player["trade_recovery"] = {"hidden": True}
    detail = card(status, live=mode != "stopped")
    assert detail["freshness"] != "Latest reported details"
    if mode in ("limbo", "box"):
        assert detail["hp"] is None
    if mode == "limbo":
        assert detail["where"] is None
    if mode == "stopped":
        assert "stopped" in detail["freshness"]

def test_detail_card_uses_gen1_dvs_stat_exp_and_single_special():
    status = sample_status()
    player = status["players"]["a"]
    player["capabilities"]["stat_stage_labels"] = ["ATK", "DEF", "SPD", "SPC", "", "ACC", "EVA"]
    player["party_details"][KEY]["calc_stats"] = {
        "dvs": {"atk": 0, "spc": 3}, "stat_exp": {"spc": 65535}, "stats": {"spa": 40, "spd": 40}}
    detail = card(status)
    assert len(detail["stats"]) == 5
    assert detail["iv_label"] == "DV" and detail["ev_label"] == "Stat exp."
    special, = [stat for stat in detail["stats"] if stat["name"] == "Special"]
    assert special == {"name": "Special", "value": 40, "iv": 3, "ev": 65535}

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

def test_missing_abilities_are_not_replaced_by_species_defaults():
    status = sample_status()
    mon = status["players"]["a"]["party_details"][KEY]
    for field in ("ability_id", "ability_name", "ability_slots", "ability_randomized"):
        mon.pop(field)
    assert card(status)["ability"] == "Not reported"
    assert "Ability slots" not in render(status)
