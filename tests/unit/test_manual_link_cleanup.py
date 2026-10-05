from types import SimpleNamespace

import pytest

from server.server import SLinkServer
from server.state import EncounterLock


@pytest.mark.asyncio
async def test_manual_link_clears_stale_encounter_lock(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    server.party_details["a"]["A:1"] = {
        "nickname": "Alpha", "species_id": 1, "level": 5,
    }
    server.party_details["b"]["B:2"] = {
        "nickname": "Beta", "species_id": 2, "level": 5,
    }
    server.state.encounter_locks["mt_moon"] = {
        "b": EncounterLock(species=388, level=14),
    }
    server.state.retry_areas["b"].add("mt_moon")

    async def body():
        return {
            "a_key": "A:1",
            "b_key": "B:2",
            "area_id": "mt_moon",
        }

    response = await server.handle_inject_link_api(SimpleNamespace(json=body))

    assert response.status == 200
    assert "mt_moon" not in server.state.encounter_locks
    assert "mt_moon" not in server.state.retry_areas["b"]
