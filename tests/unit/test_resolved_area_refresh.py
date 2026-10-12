from types import SimpleNamespace

import pytest

from server.server import SLinkServer
from server.state import MonInfo


@pytest.mark.asyncio
async def test_manual_link_refreshes_areas_after_pending_cleanup(tmp_path):
    server = SLinkServer(data_dir=str(tmp_path))
    server.state.pending_captures["route_24"] = {
        "a": MonInfo(key="A:1", nickname="Alpha", species=1, level=5),
    }

    async def body():
        return {
            "a_key": "A:1", "b_key": "B:2",
            "area_id": "mt_moon", "force": True,
        }

    response = await server.handle_inject_link_api(SimpleNamespace(json=body))

    assert response.status == 200
    for pid in ("a", "b"):
        refreshes = [
            command["areas"]
            for command in server.state.queued_commands[pid]
            if command.get("cmd") == "resolved_areas"
        ]
        assert refreshes == [["mt_moon"]]
