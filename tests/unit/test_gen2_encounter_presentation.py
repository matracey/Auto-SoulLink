"""Encounter presentation stays byte-identical through the shared Gen 2 policy extraction."""
import hashlib
import json
from pathlib import Path

import pytest

from server.adapters.gen2_gsc import Gen2GSCAdapter
from server.adapters.gen2_polished import Gen2PolishedAdapter

ROOT = Path(__file__).resolve().parents[2]
# Captured from public encounter_table outputs at c98120f61 before extraction, including
# dictionary order (methods affect the rendered order), forms and every generated map.
GOLDEN = {
    "crystal": "37bfa1634f970bd68e2d1550b6838eb678a13b51554736f05310714060184ccd",
    "gold": "0053999354060bca8ab28cb06a4d7b9b09b72a581e24a1a706bbba62cbed34d1",
    "silver": "4ad81a4f583555efa423e2e72d17343ab9d82b19f3eb096b5025701ac3835bd1",
    "polished_crystal": "6eaf17c2cc0eabf6058f3b3bc71a2409258b5b3f324ea3ee30710f8d15b6edc4",
}


@pytest.mark.parametrize("title", GOLDEN, ids=list(GOLDEN))
def test_every_displayed_encounter_row_is_byte_identical(title):
    adapter = Gen2PolishedAdapter() if title == "polished_crystal" else Gen2GSCAdapter(title)
    folder = "polished_crystal" if title == "polished_crystal" else "gen2_" + title
    areas = json.loads((ROOT / "data/games" / folder / "area_map.json").read_text())
    ids = sorted({row["area_id"] for row in areas.values()} | {"national_park_contest"})
    shown = {area: adapter.encounter_table(area) for area in ids}
    encoded = json.dumps(shown, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == GOLDEN[title]


def test_shared_policy_preserves_swarm_maps_and_inclusive_fish_weights():
    from server.adapters.gen2_encounter_presentation import present_encounters

    areas = {"257": {"area_id": "route", "map_group": 1, "map_number": 1,
                     "map_name": "North", "fishing_group": 1},
             "513": {"area_id": "route", "map_group": 2, "map_number": 1,
                     "map_name": "South", "fishing_group": 0}}
    slots = [{"species": 1, "level": 5}, {"species": 2, "level": 6}]
    wild = [{"map_group": g, "map_number": 1, "time": "day", "table": table,
             "rate": 20, "slots": slots} for g, table in ((1, "Normal"), (1, "SwarmTest"), (2, "Normal"))]
    fish = [dict(slots[0], threshold=127), dict(slots[1], threshold=255)]
    encounters = {"wild": {"grass_probabilities": [{"threshold": 80}, {"threshold": 100}],
                           "water_probabilities": [{"threshold": 100}], "grass": wild, "water": []},
                  "tree": {"sets": [], "headbutt_maps": [], "rock_smash_maps": []},
                  "fishing": {"groups": [{"group_id": 1, "bite_threshold": 128,
                                          "old": fish, "good": fish, "super": fish}]}}
    result = present_encounters(areas, encounters, lambda e: {"species_id": e["species"], "name": "Mon"})
    assert list(result["route"]) == ["Day (Normal) (North)", "Day (Swarm) (North)", "Day (South)",
                                     "Old Rod", "Good Rod", "Super Rod"]
    assert [r["rate"] for r in result["route"]["Day (Normal) (North)"]] == [80, 20]
    assert [r["rate"] for r in result["route"]["Old Rod"]] == [50.0, 50.0]
    assert result["route"]["Old Rod"][0]["bite_threshold"] == 128
